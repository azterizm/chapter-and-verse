"""S3.3a Dense retrieval model bake-off (dev split only).

Evaluates BM25F alone, each model dense-only within candidate/distractor pool,
and BM25F + dense RRF fusion against gold coordinates on dev split rows.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import numpy as np
from legal_rag_router import Router
from legal_rag_router.concepts import ConceptIndex, load_concepts

from chapterverse.dense.doctext import doc_text
from chapterverse.dense.embed import MODEL_NAMES, load_model
from chapterverse.store import open_store
from eval.discovery_gate import load_router_discovery_code

__all__ = ["main", "run_bakeoff"]

DISTRACTOR_SEED: Final = 20261122
DISTRACTOR_COUNT: Final = 30000
BM25_LIMIT: Final = 50
DENSE_FUSION_LIMIT: Final = 50
RRF_K: Final = 60.0
PREFIX_FIELDS_USED: Final = (
    "instrument: title, year, other_titles; provision: crossheading, number_label, title"
)
DEV_ROWS_COUNT: Final = 112


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_scratch = Path(
        os.environ.get(
            "CV_SCRATCH",
            "/private/tmp/claude-501/-Users-abdiel-Code-chapter-and-verse/e4ae6f53-e03d-42f3-8867-fa45990923d6/scratchpad",
        )
    )
    default_out = default_scratch / "s3-dense-bakeoff"
    default_cache = default_out / "cache"
    default_data_root = Path(
        os.environ.get(
            "LEGAL_DATA_ROOT",
            str(Path.home() / "Code" / "legal-rag-router" / "data"),
        )
    )
    default_store = Path(os.environ.get("STORE_DIR", "artefacts/store"))
    default_models = Path("artefacts/models")
    default_battery = (
        Path.home() / "Code" / "legal-rag-router" / "batteries" / "concept" / "uk.jsonl"
    )

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--store", type=Path, default=default_store, help="store directory")
    parser.add_argument("--data-root", type=Path, default=default_data_root, help="legal data root")
    parser.add_argument("--models-dir", type=Path, default=default_models, help="models directory")
    parser.add_argument(
        "--models",
        nargs="*",
        default=list(MODEL_NAMES),
        help="models to evaluate or models directory",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=default_cache,
        help="cache directory for pool embeddings",
    )
    parser.add_argument(
        "--battery", type=Path, default=default_battery, help="concept battery file"
    )
    parser.add_argument("--out", type=Path, default=default_out, help="output directory")
    parser.add_argument("--threads", type=int, default=4, help="intra-op threads for onnxruntime")
    parser.add_argument("--batch-size", type=int, default=64, help="batch size for embedding")
    parser.add_argument("--force-embed", action="store_true", help="force recomputing embeddings")
    return parser.parse_args(argv)


def _resolve_models_and_dir(args: argparse.Namespace) -> tuple[list[str], Path]:
    models_dir = args.models_dir
    models_list: list[str] = []
    if args.models:
        for m in args.models:
            for raw_part in str(m).split(","):
                part = raw_part.strip()
                if not part:
                    continue
                if Path(part).is_dir():
                    models_dir = Path(part)
                else:
                    models_list.append(part)
    if not models_list:
        models_list = list(MODEL_NAMES)
    return models_list, models_dir


def _build_coord_index(concepts: ConceptIndex) -> tuple[dict[str, int], dict[int, str]]:
    coord_to_id: dict[str, int] = {}
    id_to_coord: dict[int, str] = {}
    for doc_id_str, raw in concepts.doc_table.items():
        doc_id = int(doc_id_str)
        data = json.loads(raw)
        coord = str(data["c"])
        coord_to_id[coord] = doc_id
        id_to_coord[doc_id] = coord
    return coord_to_id, id_to_coord


def score_candidate_lists(
    code: Any,
    rows: list[Any],
    ranked_coords_by_row: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Score candidate lists against gold coordinates using the router discovery scorer."""
    results: list[Any] = []
    for r in rows:
        candidates = ranked_coords_by_row.get(r.id, [])
        top10 = candidates[:10]
        rank = code.discovery.hit_rank(r.gold, top10) if r.gold else None
        results.append(
            code.discovery.RowResult(
                row=r,
                rank=rank,
                confident=False,
                route_ok=True,
                phantom=0,
                millis=0.0,
                top=top10[0] if top10 else None,
            )
        )
    return dict(code.discovery._rates(results))


def _load_dev_rows(battery_path: Path, code: Any) -> list[Any]:
    all_rows = code.schema.read_concepts(battery_path)
    dev_rows = [r for r in all_rows if r.split == "dev"]
    for r in dev_rows:
        if r.split != "dev":
            raise ValueError(f"Row {r.id} has split {r.split}, expected dev!")
    if len(dev_rows) != DEV_ROWS_COUNT:
        raise ValueError(f"Expected {DEV_ROWS_COUNT} dev rows, got {len(dev_rows)}")
    return dev_rows


def _collect_bm25_and_pool(
    dev_rows: list[Any],
    router: Router,
    concepts: ConceptIndex,
    coord_to_id: dict[str, int],
) -> tuple[dict[str, list[str]], list[int]]:
    bm25_by_row: dict[str, list[str]] = {}
    pool_doc_ids_set: set[int] = set()

    for r in dev_rows:
        res = router.discover(r.query, limit=BM25_LIMIT)
        candidates = [str(c.coordinate) for c in res.candidates]
        bm25_by_row[r.id] = candidates
        for c in candidates:
            if c in coord_to_id:
                pool_doc_ids_set.add(coord_to_id[c])

    for r in dev_rows:
        for g in r.gold:
            if g in coord_to_id:
                pool_doc_ids_set.add(coord_to_id[g])

    rng = random.Random(DISTRACTOR_SEED)  # noqa: S311
    distractors = rng.sample(range(concepts.first_instrument_doc), DISTRACTOR_COUNT)
    pool_doc_ids_set.update(distractors)

    return bm25_by_row, sorted(pool_doc_ids_set)


def _get_or_compute_embeddings(
    model_name: str,
    embedder: Any,
    pool_texts: list[str],
    cache_file: Path,
    *,
    force_embed: bool,
) -> tuple[np.ndarray, float, float]:
    cached_valid = cache_file.is_file() and not force_embed
    if cached_valid:
        print(f"Loading cached embeddings from {cache_file}...")
        pool_embeddings: np.ndarray = np.load(cache_file)
        if pool_embeddings.shape != (len(pool_texts), embedder.dim):
            raise ValueError(f"cached embedding shape {pool_embeddings.shape} mismatch")
        return pool_embeddings, 0.0, 0.0

    print(f"Embedding {len(pool_texts)} pool documents with {model_name}...")
    t0 = time.perf_counter()
    pool_embeddings = embedder.embed_docs(pool_texts)
    embed_time_s = time.perf_counter() - t0
    throughput = len(pool_texts) / embed_time_s if embed_time_s > 0 else 0.0
    print(
        f"Embedding complete in {embed_time_s:.2f}s "
        f"({throughput:.1f} docs/s, threads={embedder.threads})"
    )
    np.save(cache_file, pool_embeddings)
    return pool_embeddings, embed_time_s, throughput


def _rank_dense_and_rrf(
    dev_rows: list[Any],
    embedder: Any,
    pool_embeddings: np.ndarray,
    pool_coords: list[str],
    bm25_by_row: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    dense_by_row: dict[str, list[str]] = {}
    rrf_by_row: dict[str, list[str]] = {}

    for r in dev_rows:
        q_vec = embedder.embed_query(r.query)
        cosines = pool_embeddings @ q_vec
        top_dense_indices = np.argsort(-cosines)[:DENSE_FUSION_LIMIT]
        dense_candidates = [pool_coords[idx] for idx in top_dense_indices]
        dense_by_row[r.id] = dense_candidates

        bm25_cands = bm25_by_row[r.id]
        bm25_ranks = {c: idx for idx, c in enumerate(bm25_cands, start=1)}
        dense_ranks = {c: idx for idx, c in enumerate(dense_candidates, start=1)}

        all_cands = set(bm25_ranks.keys()) | set(dense_ranks.keys())
        rrf_scores: dict[str, float] = {}
        for c in all_cands:
            score = 0.0
            if c in bm25_ranks:
                score += 1.0 / (RRF_K + bm25_ranks[c])
            if c in dense_ranks:
                score += 1.0 / (RRF_K + dense_ranks[c])
            rrf_scores[c] = score

        fused = sorted(all_cands, key=lambda c: (-rrf_scores[c], c))
        rrf_by_row[r.id] = fused

    return dense_by_row, rrf_by_row


def _evaluate_all_models(
    args: argparse.Namespace,
    code: Any,
    *,
    dev_rows: list[Any],
    pool_texts: list[str],
    pool_coords: list[str],
    bm25_by_row: dict[str, list[str]],
    cache_dir: Path,
    models_to_evaluate: list[str],
    models_dir: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    model_metrics: dict[str, dict[str, Any]] = {}
    model_dense_rates: dict[str, dict[str, Any]] = {}
    model_rrf_rates: dict[str, dict[str, Any]] = {}

    for model_name in models_to_evaluate:
        print(f"\n==================== Model: {model_name} ====================")
        embedder = load_model(
            model_name,
            models_dir,
            threads=args.threads,
            batch_size=args.batch_size,
        )

        cache_file = cache_dir / f"{model_name}.npy"
        is_cached = cache_file.is_file() and not args.force_embed
        pool_embeddings, embed_time_s, throughput = _get_or_compute_embeddings(
            model_name,
            embedder,
            pool_texts,
            cache_file,
            force_embed=args.force_embed,
        )

        total_tokens = sum(
            len(embedder.tokenizer.encode(f"{embedder.doc_prefix}{t}").ids)
            for t in pool_texts[:1000]
        )
        mean_tokens_per_doc = round(total_tokens / min(len(pool_texts), 1000), 1)

        tp_val: Any = (
            "cached (GPU build)"
            if is_cached
            else (round(throughput, 1) if throughput > 0 else "cached")
        )
        model_metrics[model_name] = {
            "throughput_docs_per_s": tp_val,
            "embed_time_s": round(embed_time_s, 2),
            "threads_used": embedder.threads,
            "mean_tokens_per_doc": mean_tokens_per_doc,
            "dimension": embedder.dim,
            "pooling": embedder.pooling,
            "pool_source": "cached (GPU build)" if is_cached else "CPU",
        }

        dense_by_row, rrf_by_row = _rank_dense_and_rrf(
            dev_rows, embedder, pool_embeddings, pool_coords, bm25_by_row
        )

        dense_rates = score_candidate_lists(code, dev_rows, dense_by_row)
        rrf_rates = score_candidate_lists(code, dev_rows, rrf_by_row)

        model_dense_rates[model_name] = dense_rates
        model_rrf_rates[model_name] = rrf_rates

        print(
            f"{model_name} dense-only: hit@1={dense_rates['hit@1']:.3f}, "
            f"hit@5={dense_rates['hit@5']:.3f}, hit@10={dense_rates['hit@10']:.3f}, "
            f"MRR={dense_rates['mrr']:.3f}"
        )
        print(
            f"{model_name} RRF:        hit@1={rrf_rates['hit@1']:.3f}, "
            f"hit@5={rrf_rates['hit@5']:.3f}, hit@10={rrf_rates['hit@10']:.3f}, "
            f"MRR={rrf_rates['mrr']:.3f}"
        )

    return model_metrics, model_dense_rates, model_rrf_rates


def _write_reports(out_dir: Path, report_data: dict[str, Any]) -> None:
    (out_dir / "report.json").write_text(json.dumps(report_data, indent=2) + "\n", encoding="utf-8")

    bm25 = report_data["bm25f_alone"]
    models = report_data["models"]
    pool_info = report_data["pool"]
    recommended_model = report_data["recommended_model"]

    md_lines = [
        "# Dense Retrieval Model Bake-off Report",
        "",
        "Dev split only (112 hand rows). Test split is sealed.",
        "",
        "Notice: Dense-only within a pool is an optimistic estimate. Model choice uses RRF on dev.",
        "",
        "## Retrieval Performance Table",
        "",
        "| Ranker | hit@1 | hit@5 | hit@10 | MRR |",
        "|---|---|---|---|---|",
        (
            f"| BM25F alone | {bm25['hit@1']:.3f} | {bm25['hit@5']:.3f} | "
            f"{bm25['hit@10']:.3f} | {bm25['mrr']:.3f} |"
        ),
    ]
    for name in models:
        d = models[name]["dense_only"]
        r = models[name]["rrf"]
        md_lines.append(
            f"| {name} (dense-only) | {d['hit@1']:.3f} | {d['hit@5']:.3f} | "
            f"{d['hit@10']:.3f} | {d['mrr']:.3f} |"
        )
        md_lines.append(
            f"| {name} (RRF) | {r['hit@1']:.3f} | {r['hit@5']:.3f} | "
            f"{r['hit@10']:.3f} | {r['mrr']:.3f} |"
        )

    md_lines.extend(
        [
            "",
            "## Throughput and Sizing",
            "",
            "| Model | Throughput (docs/s) | Mac CPU Threads | Mean Tokens/Doc | Dimension |",
            "|---|---|---|---|---|",
        ]
    )
    for name in models:
        m = models[name]["metrics"]
        tp_str = f"{m['throughput_docs_per_s']}"
        md_lines.append(
            f"| {name} | {tp_str} | {m['threads_used']} | {m['mean_tokens_per_doc']} | "
            f"{m['dimension']} |"
        )

    gpu_cached = [
        name
        for name, m in models.items()
        if m.get("metrics", {}).get("pool_source") == "cached (GPU build)"
        or "GPU build" in str(m.get("metrics", {}).get("throughput_docs_per_s", ""))
    ]

    md_lines.extend(
        [
            "",
            "## Configuration and Notes",
            "",
            f"- Prefix fields used: {PREFIX_FIELDS_USED}",
            (
                f"- Pool size: {pool_info['total_docs']} documents "
                f"({pool_info['distractors']} distractors sampled with seed "
                f"{pool_info['distractor_seed']})"
            ),
            f"- Recommended model by dev RRF MRR: {recommended_model}",
        ]
    )
    if gpu_cached or "gte-small" in models:
        cached_names = ", ".join(gpu_cached) if gpu_cached else "gte-small"
        md_lines.append(
            f"- Pool vectors for {cached_names} came from the GPU build; "
            "query vectors embedded on CPU."
        )
    md_lines.append("")

    (out_dir / "report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"\nWrote {out_dir / 'report.json'} and {out_dir / 'report.md'}")


def run_bakeoff(args: argparse.Namespace) -> dict[str, Any]:
    router_repo = Path.home() / "Code" / "legal-rag-router"
    code = load_router_discovery_code(router_repo)

    dev_rows = _load_dev_rows(args.battery.resolve(), code)
    print(f"Loaded {len(dev_rows)} dev split rows (test split sealed and excluded)")

    models_to_evaluate, models_dir = _resolve_models_and_dir(args)
    print(f"Models to evaluate: {models_to_evaluate} from {models_dir}")

    print(f"Opening store from {args.store}...")
    store = open_store(args.store)
    concepts_dir = args.data_root / "concepts"
    index_dir = args.data_root / "index"
    concepts = load_concepts(concepts_dir)
    router = Router.from_path(index_dir, concepts=concepts_dir)

    print("Building coordinate index from concepts doc table...")
    coord_to_id, id_to_coord = _build_coord_index(concepts)
    print(f"Indexed {len(coord_to_id)} coordinates.")

    print("Collecting BM25F candidates (limit 50) and building pool...")
    bm25_by_row, pool_doc_ids = _collect_bm25_and_pool(dev_rows, router, concepts, coord_to_id)
    print(
        f"Pool built: {len(pool_doc_ids)} unique documents "
        f"({DISTRACTOR_COUNT} distractors, seed {DISTRACTOR_SEED})"
    )

    print("Extracting document texts for pool...")
    t_text0 = time.perf_counter()
    pool_texts = [doc_text(doc_id, store, concepts) for doc_id in pool_doc_ids]
    text_elapsed = time.perf_counter() - t_text0
    print(
        f"Extracted {len(pool_texts)} document texts in {text_elapsed:.2f}s "
        f"({len(pool_texts) / text_elapsed:.1f} docs/s)"
    )

    out_dir = args.out.resolve()
    cache_dir = args.cache.resolve() if getattr(args, "cache", None) else (out_dir / "cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "pool_doc_ids.json").write_text(json.dumps(pool_doc_ids), encoding="utf-8")

    bm25_rates = score_candidate_lists(code, dev_rows, bm25_by_row)
    print(
        f"\nBM25F alone: hit@1={bm25_rates['hit@1']:.3f}, hit@5={bm25_rates['hit@5']:.3f}, "
        f"hit@10={bm25_rates['hit@10']:.3f}, MRR={bm25_rates['mrr']:.3f}"
    )

    pool_coords = [id_to_coord[doc_id] for doc_id in pool_doc_ids]

    model_metrics, model_dense_rates, model_rrf_rates = _evaluate_all_models(
        args,
        code,
        dev_rows=dev_rows,
        pool_texts=pool_texts,
        pool_coords=pool_coords,
        bm25_by_row=bm25_by_row,
        cache_dir=cache_dir,
        models_to_evaluate=models_to_evaluate,
        models_dir=models_dir,
    )

    def _model_sort_key(name: str) -> tuple[float, float, float]:
        rrf = model_rrf_rates[name]
        tp_raw = model_metrics[name]["throughput_docs_per_s"]
        tp = float(tp_raw) if isinstance(tp_raw, int | float) else 0.0
        return (float(rrf["mrr"]), float(rrf["hit@10"]), tp)

    recommended_model = max(models_to_evaluate, key=_model_sort_key)
    print(f"\nRecommended model by dev RRF MRR: {recommended_model}")

    gpu_cached = [
        name
        for name in models_to_evaluate
        if model_metrics[name].get("pool_source") == "cached (GPU build)"
        or "GPU build" in str(model_metrics[name].get("throughput_docs_per_s", ""))
    ]
    note_str = "Dense-only within a pool is an optimistic estimate. Model choice uses RRF on dev."
    if gpu_cached or "gte-small" in models_to_evaluate:
        cached_names = ", ".join(gpu_cached) if gpu_cached else "gte-small"
        note_str += f" Pool vectors for {cached_names} came from the GPU build."

    report_data = {
        "split": "dev",
        "rows": len(dev_rows),
        "pool": {
            "total_docs": len(pool_doc_ids),
            "distractors": DISTRACTOR_COUNT,
            "distractor_seed": DISTRACTOR_SEED,
            "candidate_and_gold_docs": len(pool_doc_ids) - DISTRACTOR_COUNT,
        },
        "prefix_fields_used": PREFIX_FIELDS_USED,
        "bm25f_alone": {
            "hit@1": bm25_rates["hit@1"],
            "hit@5": bm25_rates["hit@5"],
            "hit@10": bm25_rates["hit@10"],
            "mrr": bm25_rates["mrr"],
        },
        "models": {
            name: {
                "metrics": model_metrics[name],
                "dense_only": {
                    "hit@1": model_dense_rates[name]["hit@1"],
                    "hit@5": model_dense_rates[name]["hit@5"],
                    "hit@10": model_dense_rates[name]["hit@10"],
                    "mrr": model_dense_rates[name]["mrr"],
                },
                "rrf": {
                    "hit@1": model_rrf_rates[name]["hit@1"],
                    "hit@5": model_rrf_rates[name]["hit@5"],
                    "hit@10": model_rrf_rates[name]["hit@10"],
                    "mrr": model_rrf_rates[name]["mrr"],
                },
            }
            for name in models_to_evaluate
        },
        "recommended_model": recommended_model,
        "note": note_str,
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
    }

    _write_reports(out_dir, report_data)

    return report_data


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_bakeoff(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
