"""Slice 1 gate 1: every UK router battery row through the engine's own pipeline.

Each row goes through :meth:`chapterverse.pipeline.Pipeline.run`, the function ``/api/ask``
runs, over the real router index and the real text store, with a fake rotation (no LLM). The
routing outcome is read back from the stream only: status, bound coordinates and corrections
from the ``route`` event, the top candidate from ``clarify``. It is scored with the router's own
code, imported from the router checkout and never copied:

* ``batteries/schema.py`` (``BATTERIES``, ``read_battery``);
* ``eval/metrics.py`` (``Outcome``, ``domain_metrics``);
* ``eval/seal.py`` (``verify_battery_seal``, ``verify_results_seal``, ``version_change``);
* ``eval/run.py`` (``route_row`` and ``_row``, for the direct-router baseline).

Both repos have top-level ``eval`` and ``batteries`` packages, so the router's modules are
loaded by file path under private names. Bytecode writing is turned off first, and every output
goes under ``--out``: nothing is written inside the router repo.

The run refuses to start unless the battery seal verifies the way ``eval.seal verify`` does
(batteries, index files, aliases, harvest split, typo policy; the package version alone may
differ) and the sealed results file matches its seal. It then reports:

* the router metrics, through the pipeline and directly, beside the sealed run's;
* every row whose metric fields differ from the sealed row, for both paths;
* rows where the pipeline erred after routing: an ``error`` event (store miss, exception), an
  event order ``validate_order`` rejects, or a receipt whose ``answer_id`` does not match.

  LEGAL_DATA_ROOT=~/Code/legal-rag-router/data STORE_DIR=artefacts/store \\
      uv run python -m eval.run --out SCRATCH/battery
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from legal_rag_router import Router

from chapterverse.audit.policy import build_policy
from chapterverse.config import Settings
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ChatRequest,
    ChatResult,
    ClarifyEvent,
    Event,
    ProviderUnavailable,
    ReceiptEvent,
    RefusalEvent,
    RouteEvent,
    validate_order,
)
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.store import open_store

__all__ = [
    "METRIC_FIELDS",
    "EmptyRotation",
    "NoModelRotation",
    "RouterCode",
    "load_router_code",
    "main",
    "outcome_from_events",
    "pipeline_faults",
]

ROUTER_REPO: Final = Path(
    os.environ.get("LRR_ROOT", str(Path.home() / "Code" / "legal-rag-router"))
)
SEAL: Final = "seals/battery-2026-09-29-v2.json"
RESULTS_SEAL: Final = "seals/results-2026-09-29-v2.json"
RESULTS: Final = "results/uk-run-2026-09-29-v2.json"
METRIC_FIELDS: Final = ("status", "bound", "met", "wrong_instrument", "corrected")
"""Row fields compared with the sealed row; ``top_candidate`` too when the row asked."""
HEADLINE: Final = ("misroute_wrong_instrument", "bound_on_invented", "false_abstention")


# ---------------------------------------------------------------------------- router code


@dataclass(frozen=True, slots=True)
class RouterCode:
    """The router checkout's own battery, metrics, seal and run modules."""

    schema: ModuleType
    metrics: ModuleType
    seal: ModuleType
    run: ModuleType


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses and pydantic look their module up here
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _aliased(aliases: Mapping[str, ModuleType]) -> Iterator[None]:
    """Make ``from batteries.schema import ...`` and ``from eval.metrics import ...`` inside
    the router's ``eval/run.py`` resolve to the router's modules, then restore sys.modules."""
    saved = {name: sys.modules.get(name) for name in aliases}
    sys.modules.update(aliases)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def load_router_code(repo: Path) -> RouterCode:
    """Import the router's battery, metrics, seal and run modules without writing bytecode."""
    sys.dont_write_bytecode = True
    schema = _load("_lrr_batteries_schema", repo / "batteries" / "schema.py")
    metrics = _load("_lrr_eval_metrics", repo / "eval" / "metrics.py")
    seal = _load("_lrr_eval_seal", repo / "eval" / "seal.py")
    stub = ModuleType("batteries")
    stub.schema = schema  # type: ignore[attr-defined]
    with _aliased(
        {
            "batteries": stub,
            "batteries.schema": schema,
            "eval.metrics": metrics,
            "eval.seal": seal,
        }
    ):
        run = _load("_lrr_eval_run", repo / "eval" / "run.py")
    return RouterCode(schema=schema, metrics=metrics, seal=seal, run=run)


# ---------------------------------------------------------------------------- fake rotations


class EmptyRotation:
    """A ``Rotation`` whose model always answers with nothing: the generator, its prompt and
    the verifier run, and no sentence is produced. No network."""

    name: Final = "fake"
    calls: int

    def __init__(self) -> None:
        self.calls = 0

    def available(self) -> list[str]:
        return [self.name]

    def complete(self, req: ChatRequest) -> ChatResult:
        self.calls += 1
        return ChatResult("", "stop", self.name, "fake-model", None, 0, 0, 0.0)


class NoModelRotation:
    """A ``Rotation`` with no provider: every bound answer goes extractive. No network."""

    def available(self) -> list[str]:
        return []

    def complete(self, req: ChatRequest) -> ChatResult:
        raise AllProvidersUnavailable([ProviderUnavailable("fake", "config", None, "fake")])


# ---------------------------------------------------------------------------- scoring


def outcome_from_events(
    metrics: ModuleType, battery: str, row: Any, events: Sequence[Event]
) -> Any:
    """The router's ``Outcome`` for ``row``, read from the stream only (never the RouteResult).

    Status, bound coordinates and corrections come from ``route``; the top candidate from
    ``clarify`` (the router's ``candidates``, in its order). A stream without ``route`` scores
    as status ``NO_ROUTE_EVENT``, which no metric counts as met.
    """
    route = next((e for e in events if isinstance(e, RouteEvent)), None)
    clarify = next((e for e in events if isinstance(e, ClarifyEvent)), None)
    top = clarify.candidates[0].coordinate if clarify is not None and clarify.candidates else None
    return metrics.Outcome(
        battery=battery,
        row_id=row.id,
        source=row.source,
        expected_status=row.expected_status,
        expected=row.expected_coordinates,
        status=route.status if route is not None else "NO_ROUTE_EVENT",
        bound=tuple(route.coordinates) if route is not None else (),
        top_candidate=top,
        corrected=bool(route.corrections) if route is not None else False,
        split=row.split,
    )


def pipeline_faults(events: Sequence[Event], mode: str, error: str | None) -> list[str]:
    """What went wrong in the pipeline after routing, if anything (empty: nothing)."""
    types = [e.type for e in events]
    faults: list[str] = []
    if not types or types[0] != "route":
        faults.append("route_not_first")
    if not validate_order(types):
        faults.append("invalid_order")
    if "error" in types or mode == "error":
        faults.append(f"error:{error or 'unknown'}")
    faults.extend(
        "receipt_id_mismatch"
        for event in events
        if isinstance(event, ReceiptEvent) and event.answer_id != event.fact.answer_id
    )
    route = next((e for e in events if isinstance(e, RouteEvent)), None)
    if (
        route is not None
        and route.status == "ROUTE_BOUNDED"
        and "error" not in types
        and "sources" not in types
        and "refusal" not in types
    ):
        faults.append("bound_without_sources_or_refusal")
    return faults


def _app_outcome(events: Sequence[Event]) -> str:
    """The answer the user would see: ``sources``, ``clarify`` or the refusal kind."""
    for event in events:
        if isinstance(event, RefusalEvent):
            return f"refusal:{event.kind}"
        if event.type in ("sources", "clarify", "error"):
            return event.type
    return "none"


def _differs(now: Mapping[str, Any], sealed: Mapping[str, Any]) -> list[str]:
    fields = list(METRIC_FIELDS)
    if sealed.get("status") == "ROUTE_AMBIGUOUS" or now.get("status") == "ROUTE_AMBIGUOUS":
        fields.append("top_candidate")
    return [f for f in fields if now.get(f) != sealed.get(f)]


@dataclass(slots=True)
class _Tally:
    rows: int = 0
    seconds: float = 0.0
    faults: list[dict[str, Any]] = field(default_factory=list)
    modes: Counter[str] = field(default_factory=Counter)
    app: Counter[str] = field(default_factory=Counter)
    over_cap: list[str] = field(default_factory=list)


def _headline(metrics: Mapping[str, Any]) -> dict[str, Any]:
    domain = next(iter(metrics.values()))
    return {k: {"count": domain[k]["count"], "total": domain[k]["total"]} for k in HEADLINE}


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0912, PLR0915 - one linear report
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--router-repo", type=Path, default=ROUTER_REPO)
    parser.add_argument("--seal", default=SEAL, help="battery seal, relative to the router repo")
    parser.add_argument("--results", default=RESULTS, help="sealed results to compare with")
    parser.add_argument("--results-seal", default=RESULTS_SEAL)
    parser.add_argument("--rotation", choices=("empty", "none"), default="empty")
    parser.add_argument("--limit", type=int, default=0, help="rows per battery (0: all)")
    parser.add_argument("--out", type=Path, required=True, help="directory for the outputs")
    args = parser.parse_args(argv)
    repo: Path = args.router_repo.expanduser().resolve()
    out: Path = args.out.resolve()
    if out == repo or repo in out.parents:
        print("REFUSED: --out is inside the router repo")
        return 1

    code = load_router_code(repo)
    settings = Settings.from_env()
    index_dir = Path(settings.router_index)
    try:
        seal = code.seal.verify_battery_seal(repo / args.seal, repo, index_dir)
        code.seal.verify_results_seal(repo / args.results_seal, repo)
    except code.seal.SealError as exc:
        print(f"REFUSED: {exc}")
        return 1
    note = code.seal.version_change(seal)
    print(f"battery seal OK: {seal['seal_sha256']}" + (f"; {note}" if note else ""))
    print(f"results seal OK: {args.results_seal}")
    sealed = json.loads((repo / args.results).read_text(encoding="utf-8"))
    sealed_rows = {r["id"]: r for r in sealed["rows"]}

    store = open_store(settings.store_dir)
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)
    if store.snapshot != router.index.snapshot:
        print(f"REFUSED: store {store.snapshot} and router {router.index.snapshot} differ")
        return 1
    rotation = EmptyRotation() if args.rotation == "empty" else NoModelRotation()
    pipeline = Pipeline(
        router=router, store=store, rotation=rotation, policy=build_policy(JUDGE_PRESET_NAME)
    )

    battery_dir = repo / "batteries" / "uk"
    rows: dict[str, Any] = {}
    via_pipeline: list[Any] = []
    direct: list[Any] = []
    tally = _Tally()
    diffs: dict[str, list[dict[str, Any]]] = {
        "pipeline": [],
        "direct": [],
        "pipeline_vs_direct": [],
    }
    per_row: list[dict[str, Any]] = []
    for battery in code.schema.BATTERIES:
        battery_rows = code.schema.read_battery(battery_dir / f"{battery}.jsonl")
        if args.limit:
            battery_rows = battery_rows[: args.limit]
        for row in battery_rows:
            rows[row.id] = row
            if len(row.query.encode("utf-8")) > settings.max_query_bytes:
                tally.over_cap.append(row.id)
            events: list[Event] = []
            t0 = time.perf_counter()
            outcome = pipeline.run(
                AskRequest(query=row.query, context=tuple(row.context or ())), events.append
            )
            tally.seconds += time.perf_counter() - t0
            tally.rows += 1
            tally.modes[outcome.mode] += 1
            app = _app_outcome(events)
            tally.app[f"{row.expected_status} -> {app}"] += 1
            faults = pipeline_faults(events, outcome.mode, outcome.error)
            if faults:
                tally.faults.append({"id": row.id, "faults": faults, "events": outcome.events})
            scored = outcome_from_events(code.metrics, battery, row, events)
            baseline = code.run.route_row(router, battery, row)
            via_pipeline.append(scored)
            direct.append(baseline)
            now_p, now_d = code.run._row(scored), code.run._row(baseline)
            old = sealed_rows.get(row.id)
            for name, now, ref in (
                ("pipeline", now_p, old),
                ("direct", now_d, old),
                ("pipeline_vs_direct", now_p, now_d),
            ):
                if ref is None:
                    diffs[name].append({"id": row.id, "fields": ["missing_in_reference"]})
                    continue
                fields = _differs(now, ref)
                if fields:
                    diffs[name].append(
                        {
                            "id": row.id,
                            "fields": fields,
                            "now": {f: now.get(f) for f in fields},
                            "reference": {f: ref.get(f) for f in fields},
                        }
                    )
            per_row.append({**now_p, "app": app, "mode": outcome.mode, "events": outcome.events})

    def by_domain(outcomes: list[Any]) -> dict[str, Any]:
        domains: dict[str, list[Any]] = {}
        for o in outcomes:
            domains.setdefault(rows[o.row_id].domain, []).append(o)
        return {d: code.metrics.domain_metrics(v) for d, v in sorted(domains.items())}

    metrics_p, metrics_d = by_domain(via_pipeline), by_domain(direct)
    headline = {
        "pipeline": _headline(metrics_p),
        "direct": _headline(metrics_d),
        "sealed": _headline(sealed["metrics"]),
    }
    report = {
        "router_repo_commit_sealed": sealed["run"]["git_commit"],
        "battery_seal_sha256": seal["seal_sha256"],
        "version_note": note,
        "rotation": args.rotation,
        "rows": tally.rows,
        "pipeline_seconds": round(tally.seconds, 1),
        "headline": headline,
        "metrics_equal": {
            "pipeline_vs_sealed": metrics_p == sealed["metrics"],
            "direct_vs_sealed": metrics_d == sealed["metrics"],
            "pipeline_vs_direct": metrics_p == metrics_d,
        },
        "row_differences": {k: len(v) for k, v in diffs.items()},
        "pipeline_faults": len(tally.faults),
        "modes": dict(tally.modes),
        "app_outcomes": dict(sorted(tally.app.items())),
        "over_query_cap": tally.over_cap,
        "rotation_calls": getattr(rotation, "calls", 0),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (out / "differences.json").write_text(json.dumps(diffs, indent=1) + "\n", encoding="utf-8")
    (out / "faults.json").write_text(json.dumps(tally.faults, indent=1) + "\n", encoding="utf-8")
    (out / "metrics.json").write_text(
        json.dumps({"pipeline": metrics_p, "direct": metrics_d}, indent=1) + "\n",
        encoding="utf-8",
    )
    with (out / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for item in per_row:
            fh.write(json.dumps(item, sort_keys=True) + "\n")
    print(json.dumps(report, indent=1))
    for name, items in diffs.items():
        for item in items[:20]:
            print(f"diff[{name}] {json.dumps(item)}")
    for fault in tally.faults[:20]:
        print(f"fault {json.dumps(fault)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
