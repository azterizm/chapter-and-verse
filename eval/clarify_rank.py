"""Evaluate clarify candidate ranking on the everyday battery (S5.3, R2).

Runs each row of ``batteries/everyday/everyday.jsonl`` through
:meth:`chapterverse.pipeline.Pipeline.run` (built via ``eval.discovery_gate``)
and reads the ``clarify`` event's candidates, reporting per-row gold ranks,
hit@1, hit@3, hit@10, and MRR with dense off and (when ``--dense-dir`` is set) on.

Usage:
    uv run python -m eval.clarify_rank [--dense-dir DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from legal_rag_router import Router

from chapterverse.contracts import ClarifyEvent, Event, RouteEvent
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.store import open_store
from eval.discovery_gate import (
    ROUTER_REPO,
    RouterDiscoveryCode,
    _build_pipelines,
    _prepare_settings,
    add_prior_args,
    clarify_prior_report,
    load_eval_clarify_prior,
    load_router_discovery_code,
)

DEFAULT_BATTERY: Final = Path("batteries/everyday/everyday.jsonl")
TOP_K_3: Final = 3
TOP_K_10: Final = 10


@dataclass(frozen=True, slots=True)
class EverydayRow:
    id: str
    question: str
    gold: tuple[str, ...]
    split: str = "dev"
    note: str = ""


@dataclass(frozen=True, slots=True)
class EvalRowResult:
    row: EverydayRow
    rank: int | None
    mode: str
    route_status: str
    top_label: str | None
    top_coord: str | None
    candidates: tuple[str, ...]
    millis: float


def read_everyday(path: Path) -> list[EverydayRow]:
    rows: list[EverydayRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        rows.append(
            EverydayRow(
                id=str(raw["id"]),
                question=str(raw["question"]),
                gold=tuple(str(g) for g in raw["gold"]),
                split=str(raw.get("split", "dev")),
                note=str(raw.get("note", "")),
            )
        )
    return rows


def _eval_pipeline(
    rows: list[EverydayRow],
    pipeline: Pipeline,
    code: RouterDiscoveryCode,
) -> list[EvalRowResult]:
    results: list[EvalRowResult] = []
    for row in rows:
        events: list[Event] = []
        t0 = time.perf_counter()
        outcome = pipeline.run(AskRequest(query=row.question), events.append)
        millis = (time.perf_counter() - t0) * 1000.0

        clarify = next((e for e in events if isinstance(e, ClarifyEvent)), None)
        route_ev = next((e for e in events if isinstance(e, RouteEvent)), None)
        route_status = route_ev.status if route_ev is not None else "NO_ROUTE_EVENT"

        if clarify is not None and clarify.candidates:
            cand_coords = [str(c.coordinate) for c in clarify.candidates]
            top_label = clarify.candidates[0].label
            top_coord = cand_coords[0]
        else:
            cand_coords = []
            top_label = None
            top_coord = None

        rank = code.discovery.hit_rank(row.gold, cand_coords) if cand_coords else None
        results.append(
            EvalRowResult(
                row=row,
                rank=rank,
                mode=outcome.mode,
                route_status=route_status,
                top_label=top_label,
                top_coord=top_coord,
                candidates=tuple(cand_coords),
                millis=millis,
            )
        )
    return results


def _metrics(results: list[EvalRowResult]) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"rows": 0, "hit@1": 0.0, "hit@3": 0.0, "hit@10": 0.0, "mrr": 0.0}
    hit1 = sum(1 for r in results if r.rank == 1) / n
    hit3 = sum(1 for r in results if r.rank is not None and r.rank <= TOP_K_3) / n
    hit10 = sum(1 for r in results if r.rank is not None and r.rank <= TOP_K_10) / n
    mrr = sum(1.0 / r.rank for r in results if r.rank is not None) / n
    return {
        "rows": n,
        "hit@1": round(hit1, 3),
        "hit@3": round(hit3, 3),
        "hit@10": round(hit10, 3),
        "mrr": round(mrr, 3),
    }


def _format_row(r: EvalRowResult) -> dict[str, Any]:
    return {
        "id": r.row.id,
        "question": r.row.question,
        "gold": list(r.row.gold),
        "rank": r.rank,
        "mode": r.mode,
        "route_status": r.route_status,
        "top_coord": r.top_coord,
        "top_label": r.top_label,
        "candidates": list(r.candidates),
        "millis": round(r.millis, 2),
    }


def _print_stage(name: str, results: list[EvalRowResult], summary: dict[str, Any]) -> None:
    print(f"\n=== Everyday Clarify Rank: {name} ===")
    print(
        f"rows={summary['rows']}  hit@1={summary['hit@1']:.3f}  "
        f"hit@3={summary['hit@3']:.3f}  hit@10={summary['hit@10']:.3f}  "
        f"mrr={summary['mrr']:.3f}"
    )
    for r in results:
        rank_s = str(r.rank) if r.rank is not None else "MISS"
        print(f"  {r.row.id}  rank={rank_s:<4}  mode={r.mode:<7}  top={r.top_label}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_out = Path(os.environ.get("CV_SCRATCH", "scratch")) / "s5-clarify-rank" / "eval"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--router-repo", type=Path, default=ROUTER_REPO)
    parser.add_argument("--battery", type=Path, default=DEFAULT_BATTERY)
    parser.add_argument("--dense-dir", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=Path("artefacts/models"))
    add_prior_args(parser)
    parser.add_argument("--out", type=Path, default=default_out)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    repo: Path = args.router_repo.expanduser().resolve()
    out: Path = args.out.resolve()
    if out == repo or repo in out.parents:
        print("REFUSED: --out is inside the router repo")
        return 1

    code = load_router_discovery_code(repo)
    settings = _prepare_settings(repo)
    store = open_store(settings.store_dir)
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)
    if store.snapshot != router.index.snapshot:
        print(f"REFUSED: store {store.snapshot} and router {router.index.snapshot} differ")
        return 1

    prior = load_eval_clarify_prior(
        args.prior, no_prior=args.no_prior, router_snapshot=router.index.snapshot
    )
    print(
        f"Clarify prior: {'on' if prior is not None else 'off'} "
        f"({'--no-prior' if args.no_prior else args.prior})"
    )

    _, _, pipeline_nodense, pipeline_dense, _ = _build_pipelines(
        router,
        store,
        args.dense_dir,
        args.models_dir,
        prior=prior,
    )

    rows = read_everyday(args.battery)
    results_nodense = _eval_pipeline(rows, pipeline_nodense, code)
    metrics_nodense = _metrics(results_nodense)

    summary: dict[str, Any] = {
        "battery": str(args.battery),
        "rows": len(rows),
        "clarify_prior": clarify_prior_report(prior, prior_path=args.prior, no_prior=args.no_prior),
        "without_dense": metrics_nodense,
    }
    _print_stage("dense off (BM25)", results_nodense, metrics_nodense)

    results_dense: list[EvalRowResult] | None = None
    if pipeline_dense is not None:
        results_dense = _eval_pipeline(rows, pipeline_dense, code)
        metrics_dense = _metrics(results_dense)
        summary["dense"] = metrics_dense
        _print_stage("dense on (BM25 + dense RRF)", results_dense, metrics_dense)

    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    per_row: list[dict[str, Any]] = []
    for i, r_nd in enumerate(results_nodense):
        item: dict[str, Any] = {
            "id": r_nd.row.id,
            "question": r_nd.row.question,
            "gold": list(r_nd.row.gold),
            "without_dense": _format_row(r_nd),
        }
        if results_dense is not None:
            item["dense"] = _format_row(results_dense[i])
        per_row.append(item)

    with (out / "rows.jsonl").open("w", encoding="utf-8") as fh:
        for item in per_row:
            fh.write(json.dumps(item) + "\n")

    print(f"\nWrote {out / 'summary.json'} and {out / 'rows.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
