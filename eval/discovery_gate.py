"""Slice 2 gate 1: held-out concept battery through the engine's own pipeline.

Runs every held-out (test split) row of ``batteries/concept/uk.jsonl`` through
:meth:`chapterverse.pipeline.Pipeline.run`, the entry point ``/api/ask`` uses.
Discovered candidates are read from the ``clarify`` event.
Scored against gold coordinates using the router's own ``eval/discovery.py``.
Direct ``Router.discover`` on raw queries is scored side by side with the sealed figures.
Checks the concept battery seal and reports the impact of normalisation.

Usage:
    uv run python -m eval.discovery_gate [--out OUT_DIR]
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import statistics
import sys
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from legal_rag_router import Coordinate, Router

from chapterverse.audit.policy import build_policy
from chapterverse.config import Settings
from chapterverse.contracts import (
    ClarifyEvent,
    Event,
    RouteEvent,
)
from chapterverse.dense.embed import load_model
from chapterverse.dense.retriever import DenseRetriever
from chapterverse.dense.store import BinaryInt8Store
from chapterverse.nlp import analyse
from chapterverse.normalise import find_protected_spans, normalise
from chapterverse.pii import mask_pii
from chapterverse.pipeline import AskRequest, Pipeline
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.store import open_store
from eval.run import EmptyRotation

__all__ = [
    "load_router_discovery_code",
    "main",
]

ROUTER_REPO: Final = Path(
    os.environ.get("LRR_ROOT", str(Path.home() / "Code" / "legal-rag-router"))
)
SEAL: Final = "seals/concept-2026-09-29.json"
BATTERY: Final = "batteries/concept/uk.jsonl"
SEALED_TEST: Final = "reports/discovery-uk-test.json"
TOP_K_RANK: Final = 10
GATE_HIT10_THRESHOLD: Final = 0.88
GATE_MRR_THRESHOLD: Final = 0.63


@dataclass(frozen=True, slots=True)
class RouterDiscoveryCode:
    schema: ModuleType
    seal: ModuleType
    discovery: ModuleType


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def _aliased(aliases: Mapping[str, ModuleType]) -> Iterator[None]:
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


def load_router_discovery_code(repo: Path) -> RouterDiscoveryCode:
    sys.dont_write_bytecode = True
    schema = _load("_lrr_batteries_schema", repo / "batteries" / "schema.py")
    seal = _load("_lrr_eval_seal", repo / "eval" / "seal.py")
    stub_b = ModuleType("batteries")
    stub_b.schema = schema  # type: ignore[attr-defined]
    stub_e = ModuleType("eval")
    stub_e.seal = seal  # type: ignore[attr-defined]
    with _aliased(
        {
            "batteries": stub_b,
            "batteries.schema": schema,
            "eval": stub_e,
            "eval.seal": seal,
        }
    ):
        discovery = _load("_lrr_eval_discovery", repo / "eval" / "discovery.py")
    return RouterDiscoveryCode(schema=schema, seal=seal, discovery=discovery)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_out = Path(os.environ.get("CV_SCRATCH", "scratch")) / "s2-gates" / "discovery"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--router-repo", type=Path, default=ROUTER_REPO)
    parser.add_argument("--seal", default=SEAL, help="battery seal, relative to router repo")
    parser.add_argument("--battery", default=BATTERY, help="concept battery file")
    parser.add_argument(
        "--sealed-test", default=None, help="sealed test results (required if split is test)"
    )
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument(
        "--dense-dir", type=Path, default=None, help="Path to dense index directory"
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("artefacts/models"),
        help="Path to models directory (default: artefacts/models)",
    )
    parser.add_argument("--out", type=Path, default=default_out, help="output directory")
    return parser.parse_args(argv)


def _verify_seal(
    code: RouterDiscoveryCode, repo: Path, seal_rel: str, index_dir: Path
) -> tuple[dict[str, Any], str] | None:
    seal_path = repo / seal_rel
    seal_raw = json.loads(seal_path.read_text(encoding="utf-8"))
    if code.seal.sha256_of(code.seal.canonical(seal_raw["contents"])) != seal_raw["seal_sha256"]:
        print(f"REFUSED: {seal_rel} contents do not match seal_sha256")
        return None

    battery_file_key = "batteries/concept/uk.jsonl"
    expected_battery_hash = seal_raw["contents"]["batteries"].get(battery_file_key)
    now_battery_hash = code.seal._file_hashes([repo / battery_file_key], repo).get(battery_file_key)
    if expected_battery_hash != now_battery_hash:
        print(
            f"REFUSED: {battery_file_key} hash mismatch: "
            f"expected {expected_battery_hash}, got {now_battery_hash}"
        )
        return None

    try:
        seal_info = code.seal.verify_battery_seal(seal_path, repo, index_dir)
        note = code.seal.version_change(seal_info)
    except code.seal.SealError as exc:
        v2_seal_path = repo / "seals" / "battery-2026-09-29-v2.json"
        if v2_seal_path.is_file():
            code.seal.verify_battery_seal(v2_seal_path, repo, index_dir)
            note = (
                f"battery {battery_file_key} matches seal {seal_raw['seal_sha256']}; "
                "index verified against v2 seal"
            )
            seal_info = seal_raw
        else:
            print(f"REFUSED: concept seal failed: {exc}")
            return None
    return seal_info, note


def _check_normalise_changed(query: str, router_index: Any, dict_obj: Any) -> bool:
    try:
        cit_spans = find_protected_spans(query)
        analysis = analyse(query, protected_spans=cit_spans)
        masked = mask_pii(query, analysis, router_index=router_index, protected_spans=cit_spans)
        protect = list(cit_spans) + list(masked.name_spans)
        norm = normalise(query, dictionary=dict_obj, protect=protect)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False
    else:
        return norm.text != query


def _evaluate_row(
    row: Any,
    pipeline: Pipeline,
    router: Router,
    code: RouterDiscoveryCode,
    *,
    changed: bool,
) -> tuple[Any, Any, dict[str, Any]]:
    events: list[Event] = []
    t0 = time.perf_counter()
    outcome = pipeline.run(AskRequest(query=row.query), events.append)
    elapsed_p = (time.perf_counter() - t0) * 1000

    clarify = next((e for e in events if isinstance(e, ClarifyEvent)), None)
    route_ev = next((e for e in events if isinstance(e, RouteEvent)), None)
    route_status_p = route_ev.status if route_ev is not None else "NO_ROUTE_EVENT"

    if clarify is not None and clarify.candidates:
        candidates_p = [str(c.coordinate) for c in clarify.candidates]
        top_p = clarify.candidates[0].label
        confident_p = clarify.question.startswith("Did you mean")
    else:
        candidates_p = []
        top_p = None
        confident_p = False

    phantom_p = sum(
        1
        for c in candidates_p
        if c not in router.index.coordinates
        and not router.index.instrument(Coordinate.parse(c).instrument_id)
    )
    rank_p = code.discovery.hit_rank(row.gold, candidates_p) if row.gold else None

    res_p = code.discovery.RowResult(
        row=row,
        rank=rank_p,
        confident=confident_p,
        route_ok=(route_status_p == row.route_status),
        phantom=phantom_p,
        millis=elapsed_p,
        top=top_p,
    )

    t0_d = time.perf_counter()
    res_raw = router.discover(row.query)
    elapsed_d = (time.perf_counter() - t0_d) * 1000

    candidates_d = [str(c.coordinate) for c in res_raw.candidates]
    top_d = res_raw.candidates[0].label if res_raw.candidates else None
    phantom_d = sum(
        1
        for c in candidates_d
        if c not in router.index.coordinates
        and not router.index.instrument(Coordinate.parse(c).instrument_id)
    )
    rank_d = code.discovery.hit_rank(row.gold, candidates_d) if row.gold else None

    res_d = code.discovery.RowResult(
        row=row,
        rank=rank_d,
        confident=res_raw.confident,
        route_ok=(router.route(row.query).status.value == row.route_status),
        phantom=phantom_d,
        millis=elapsed_d,
        top=top_d,
    )

    per_row_item = {
        "id": row.id,
        "source": row.source,
        "area": row.area,
        "query": row.query,
        "normalise_changed": changed,
        "pipeline": {
            "rank": rank_p,
            "confident": confident_p,
            "route_status": route_status_p,
            "route_ok": res_p.route_ok,
            "mode": outcome.mode,
            "top": top_p,
        },
        "direct": {
            "rank": rank_d,
            "confident": res_raw.confident,
            "route_ok": res_d.route_ok,
            "top": top_d,
        },
    }
    return res_p, res_d, per_row_item


def _compute_summary(
    split: str,
    rows: list[Any],
    *,
    norm_changed_ids: list[str],
    seal_info: dict[str, Any],
    report_p: dict[str, Any],
    report_d: dict[str, Any],
    sealed_ref: Any,
    results_p: list[Any],
    results_d: list[Any],
) -> tuple[dict[str, Any], bool]:
    changed_rows_with_gold = [r for r in rows if r.id in norm_changed_ids and r.gold]
    n_changed_gold = len(changed_rows_with_gold)
    if n_changed_gold > 0:
        hit10_p_changed = (
            sum(
                1
                for r in results_p
                if r.row.id in norm_changed_ids and r.rank and r.rank <= TOP_K_RANK
            )
            / n_changed_gold
        )
        hit10_d_changed = (
            sum(
                1
                for r in results_d
                if r.row.id in norm_changed_ids and r.rank and r.rank <= TOP_K_RANK
            )
            / n_changed_gold
        )
        norm_delta_hit10 = round(hit10_p_changed - hit10_d_changed, 3)
    else:
        hit10_p_changed = 0.0
        hit10_d_changed = 0.0
        norm_delta_hit10 = 0.0

    hand_p = report_p.get("by_source", {}).get("hand", {})
    hand_hit10_p = hand_p.get("hit@10", 0.0)
    hand_mrr_p = hand_p.get("mrr", 0.0)

    pass_hit10 = hand_hit10_p >= GATE_HIT10_THRESHOLD
    pass_mrr = hand_mrr_p >= GATE_MRR_THRESHOLD
    overall_pass = pass_hit10 and pass_mrr

    summary = {
        "split": split,
        "rows": len(rows),
        "seal_sha256": seal_info["seal_sha256"],
        "gate_pass": overall_pass,
        "gate_criteria": {
            "hand_hit@10 >= 0.88": pass_hit10,
            "hand_mrr >= 0.63": pass_mrr,
            "hand_hit@10_measured": hand_hit10_p,
            "hand_mrr_measured": hand_mrr_p,
        },
        "normalise": {
            "queries_changed": len(norm_changed_ids),
            "queries_changed_with_gold": n_changed_gold,
            "hit@10_pipeline_on_changed": round(hit10_p_changed, 3),
            "hit@10_direct_on_changed": round(hit10_d_changed, 3),
            "hit@10_delta_on_changed": norm_delta_hit10,
        },
        "side_by_side": {
            "pipeline": {
                "all": report_p["all"],
                "by_source": report_p["by_source"],
            },
            "direct": {
                "all": report_d["all"],
                "by_source": report_d["by_source"],
            },
            "sealed": sealed_ref,
        },
        "safety": {
            "pipeline": report_p["safety"],
            "direct": report_d["safety"],
        },
        "latency_ms": {
            "pipeline_p50": report_p["latency_ms"]["p50"],
            "direct_p50": report_d["latency_ms"]["p50"],
        },
    }
    return summary, overall_pass


def _compute_dense_latencies(
    dense_embed_ms_list: list[float],
    dense_search_ms_list: list[float],
) -> dict[str, float]:
    embed_sorted = sorted(dense_embed_ms_list)
    search_sorted = sorted(dense_search_ms_list)
    n_times = len(embed_sorted)
    p50_embed = round(statistics.median(embed_sorted), 2) if n_times else 0.0
    p95_embed = round(embed_sorted[int(0.95 * n_times)], 2) if n_times else 0.0
    p50_search = round(statistics.median(search_sorted), 2) if n_times else 0.0
    p95_search = round(search_sorted[int(0.95 * n_times)], 2) if n_times else 0.0
    return {
        "embed_p50": p50_embed,
        "embed_p95": p95_embed,
        "search_p50": p50_search,
        "search_p95": p95_search,
    }


def _run_gate_eval(
    rows: list[Any],
    pipeline_nodense: Pipeline,
    pipeline_dense: Pipeline | None,
    dense_retriever: DenseRetriever | None,
    *,
    router: Router,
    code: RouterDiscoveryCode,
) -> tuple[list[Any], list[Any], list[Any], list[dict[str, Any]], list[str], dict[str, float]]:
    results_p: list[Any] = []
    results_p_dense: list[Any] = []
    results_d: list[Any] = []
    dense_embed_ms_list: list[float] = []
    dense_search_ms_list: list[float] = []
    norm_changed_ids: list[str] = []
    per_row: list[dict[str, Any]] = []
    dict_obj = pipeline_nodense._get_dictionary()

    for row in rows:
        changed = _check_normalise_changed(row.query, router.index, dict_obj)
        if changed:
            norm_changed_ids.append(row.id)

        res_p, res_d, per_row_item = _evaluate_row(
            row, pipeline_nodense, router, code, changed=changed
        )
        results_p.append(res_p)
        results_d.append(res_d)

        if pipeline_dense is not None and dense_retriever is not None:
            res_pd, _, per_row_dense = _evaluate_row(
                row, pipeline_dense, router, code, changed=changed
            )
            results_p_dense.append(res_pd)
            dense_embed_ms_list.append(dense_retriever.last_embed_ms)
            dense_search_ms_list.append(dense_retriever.last_search_ms)
            per_row_item["pipeline_dense"] = per_row_dense["pipeline"]

        per_row.append(per_row_item)

    latencies = (
        _compute_dense_latencies(dense_embed_ms_list, dense_search_ms_list)
        if pipeline_dense is not None
        else {}
    )
    return results_p, results_p_dense, results_d, per_row, norm_changed_ids, latencies


def _build_pipelines(
    router: Router,
    store: Any,
    dense_dir: Path | None,
    models_dir: Path,
) -> tuple[DenseRetriever | None, Pipeline, Pipeline | None]:
    dense_retriever: DenseRetriever | None = None
    if dense_dir is not None:
        dense_store = BinaryInt8Store.open(dense_dir, corpus_id=store.corpus_id)
        dense_embedder = load_model("bge-small-en-v1.5", models_dir)
        concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
        dense_retriever = DenseRetriever(dense_embedder, dense_store, concepts)

    pipeline_nodense = Pipeline(
        router=router,
        store=store,
        rotation=EmptyRotation(),
        policy=build_policy(JUDGE_PRESET_NAME),
        dense=None,
    )
    pipeline_dense = (
        Pipeline(
            router=router,
            store=store,
            rotation=EmptyRotation(),
            policy=build_policy(JUDGE_PRESET_NAME),
            dense=dense_retriever,
        )
        if dense_retriever is not None
        else None
    )
    return dense_retriever, pipeline_nodense, pipeline_dense


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.split == "test" and not args.sealed_test:
        print("REFUSED: --split test requires --sealed-test")
        return 1

    repo: Path = args.router_repo.expanduser().resolve()
    out: Path = args.out.resolve()
    if out == repo or repo in out.parents:
        print("REFUSED: --out is inside the router repo")
        return 1

    code = load_router_discovery_code(repo)
    env_dict = dict(os.environ)
    if "LEGAL_DATA_ROOT" not in env_dict and (repo / "data").is_dir():
        env_dict["LEGAL_DATA_ROOT"] = str(repo / "data")
    settings = Settings.from_env(env_dict)
    index_dir = Path(settings.router_index)

    seal_result = _verify_seal(code, repo, args.seal, index_dir)
    if seal_result is None:
        return 1
    seal_info, note = seal_result
    print(f"concept seal OK: {seal_info['seal_sha256']}" + (f"; {note}" if note else ""))

    sealed_ref = None
    if args.sealed_test:
        sealed_path = repo / args.sealed_test
        sealed_ref = (
            json.loads(sealed_path.read_text(encoding="utf-8")) if sealed_path.is_file() else None
        )

    store = open_store(settings.store_dir)
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)
    if store.snapshot != router.index.snapshot:
        print(f"REFUSED: store {store.snapshot} and router {router.index.snapshot} differ")
        return 1

    dense_retriever, pipeline_nodense, pipeline_dense = _build_pipelines(
        router, store, args.dense_dir, args.models_dir
    )

    rows = [r for r in code.schema.read_concepts(repo / args.battery) if r.split == args.split]
    print(f"Loaded {len(rows)} rows for split={args.split}")

    (
        results_p,
        results_p_dense,
        results_d,
        per_row,
        norm_changed_ids,
        latencies,
    ) = _run_gate_eval(
        rows,
        pipeline_nodense,
        pipeline_dense,
        dense_retriever,
        router=router,
        code=code,
    )

    report_nodense = code.discovery.report(results_p)
    report_d = code.discovery.report(results_d)

    active_report_p = report_nodense
    active_results_p = results_p
    if pipeline_dense is not None:
        report_dense = code.discovery.report(results_p_dense)
        active_report_p = report_dense
        active_results_p = results_p_dense

    summary, overall_pass = _compute_summary(
        args.split,
        rows,
        norm_changed_ids=norm_changed_ids,
        seal_info=seal_info,
        report_p=active_report_p,
        report_d=report_d,
        sealed_ref=sealed_ref,
        results_p=active_results_p,
        results_d=results_d,
    )

    summary["without_dense"] = {
        "all": report_nodense["all"],
        "by_source": report_nodense["by_source"],
    }

    if pipeline_dense is not None:
        summary["dense"] = {
            "all": report_dense["all"],
            "by_source": report_dense["by_source"],
            "latency_ms": latencies,
        }

    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (out / "rows.jsonl").open("w", encoding="utf-8") as f:
        for item in per_row:
            f.write(json.dumps(item) + "\n")

    print(json.dumps(summary, indent=2))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
