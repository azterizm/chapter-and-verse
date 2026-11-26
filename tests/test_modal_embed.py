"""Unit tests for deploy/modal/embed.py and R4 requirements."""

from __future__ import annotations

import io
import json
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from legal_rag_router.concepts import load_concepts

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "deploy" / "modal") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "deploy" / "modal"))

from deploy.modal.embed import (  # noqa: E402
    Embedder,
    iter_shards,
    run_dense_embed,
)
from eval.dense_bakeoff import (  # noqa: E402
    _parse_args,
    _resolve_models_and_dir,
    _write_reports,
)
from tests.conftest import JsonlTextStore  # noqa: E402


def test_iter_shards(
    fixture_data_root: Path,
    fixture_text_store: JsonlTextStore,
) -> None:
    concepts = load_concepts(fixture_data_root / "concepts")
    doc_ids = list(range(10))
    shard_size = 4

    shards = list(iter_shards(doc_ids, fixture_text_store, concepts, shard_size))
    # 10 items with shard_size 4 => [4, 4, 2]
    assert len(shards) == 3
    assert len(shards[0]) == 4
    assert len(shards[1]) == 4
    assert len(shards[2]) == 2
    # Verify non-empty strings
    for s in shards:
        for t in s:
            assert isinstance(t, str)
            assert len(t) > 0


def test_run_dense_embed_roundtrip_fake_map(
    tmp_path: Path,
    fixture_data_root: Path,
    fixture_text_store: JsonlTextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setenv("CV_MODAL_LEDGER", str(ledger_path))

    concepts = load_concepts(fixture_data_root / "concepts")
    total_docs = min(15, concepts.doc_count)
    doc_ids = list(range(total_docs))
    doc_ids_file = tmp_path / "pool_doc_ids.json"
    doc_ids_file.write_text(json.dumps(doc_ids), encoding="utf-8")

    out_dir = tmp_path / "out"
    model_name = "gte-small"
    dim = 384

    # Fake embedding map that returns deterministically ordered vectors
    # Each row vector has vec[0] = row_idx for order verification
    row_counter = 0

    def fake_map(shard_iter: Iterator[list[str]]) -> Iterator[bytes]:
        nonlocal row_counter
        for shard in shard_iter:
            vecs = np.zeros((len(shard), dim), dtype=np.float32)
            for i in range(len(shard)):
                vecs[i, 0] = float(row_counter)
                row_counter += 1
            buf = io.BytesIO()
            np.save(buf, vecs)
            yield buf.getvalue()

    result = run_dense_embed(
        model=model_name,
        out=out_dir,
        doc_ids=str(doc_ids_file),
        shard=4,
        max_usd=2.0,
        store=fixture_text_store,
        concepts=concepts,
        models_dir="artefacts/models",
        map_fn=fake_map,
    )

    assert result["count"] == total_docs
    assert result["dim"] == dim

    npy_path = out_dir / f"{model_name}.npy"
    assert npy_path.is_file()

    loaded = np.load(npy_path)
    assert loaded.shape == (total_docs, dim)
    assert loaded.dtype == np.float32

    # Check order preservation: loaded[i, 0] must equal i
    for i in range(total_docs):
        assert loaded[i, 0] == float(i)

    # Check doc_ids.json
    doc_ids_out = out_dir / f"{model_name}.doc_ids.json"
    assert doc_ids_out.is_file()
    saved_doc_ids = json.loads(doc_ids_out.read_text(encoding="utf-8"))
    assert saved_doc_ids == doc_ids

    # Check meta.json
    meta_path = out_dir / f"{model_name}.meta.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["model"] == model_name
    assert meta["count"] == total_docs
    assert meta["dim"] == dim
    assert "CUDAExecutionProvider" in meta["providers"]
    assert "revision" in meta
    assert "files_sha256" in meta
    assert isinstance(meta["seconds"], float)
    assert isinstance(meta["estimated_usd"], float)

    # Check ledger recorded
    assert ledger_path.is_file()
    entries = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(entries) >= 1
    assert entries[-1]["run"] == f"dense-embed {model_name}"
    assert entries[-1]["gpu"] == "L4"


def test_run_dense_embed_without_doc_ids(
    tmp_path: Path,
    fixture_data_root: Path,
    fixture_text_store: JsonlTextStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_path = tmp_path / "ledger.json"
    monkeypatch.setenv("CV_MODAL_LEDGER", str(ledger_path))

    concepts = load_concepts(fixture_data_root / "concepts")
    limit = 5
    out_dir = tmp_path / "out_all"
    model_name = "bge-small-en-v1.5"

    def fake_map(shard_iter: Iterator[list[str]]) -> Iterator[bytes]:
        for shard in shard_iter:
            vecs = np.zeros((len(shard), 384), dtype=np.float32)
            buf = io.BytesIO()
            np.save(buf, vecs)
            yield buf.getvalue()

    result = run_dense_embed(
        model=model_name,
        out=out_dir,
        doc_ids="",
        limit=limit,
        shard=2,
        max_usd=2.0,
        store=fixture_text_store,
        concepts=concepts,
        models_dir="artefacts/models",
        map_fn=fake_map,
    )

    assert result["count"] == limit
    npy_path = out_dir / f"{model_name}.npy"
    assert npy_path.is_file()
    loaded = np.load(npy_path)
    assert loaded.shape == (limit, 384)

    # doc_ids.json should not be written when doc_ids is empty
    doc_ids_out = out_dir / f"{model_name}.doc_ids.json"
    assert not doc_ids_out.exists()


def test_spend_guard_refusal(
    tmp_path: Path,
    fixture_data_root: Path,
    fixture_text_store: JsonlTextStore,
) -> None:
    concepts = load_concepts(fixture_data_root / "concepts")
    with pytest.raises(RuntimeError, match="exceeds max_usd"):
        run_dense_embed(
            model="gte-small",
            out=tmp_path / "out",
            limit=1000,
            max_usd=0.00000001,
            store=fixture_text_store,
            concepts=concepts,
        )


def test_embedder_raises_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "chapterverse.dense.embed.load_model",
        lambda *_a, **_kw: SimpleNamespace(active_providers=["CPUExecutionProvider"]),
    )
    embedder = Embedder()
    # On Mac / CPU environment, CUDAExecutionProvider will not be in active_providers
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with pytest.raises(RuntimeError, match="CUDAExecutionProvider not active"):
            embedder.embed("gte-small", "doc", ["Some test text"])


def test_bakeoff_args_parsing() -> None:
    # 1. Defaults
    args1 = _parse_args([])
    models1, _dir1 = _resolve_models_and_dir(args1)
    assert models1 == ["bge-small-en-v1.5", "e5-small-v2", "gte-small"]
    assert str(args1.cache).endswith("s3-dense-bakeoff/cache")

    # 2. Custom --models and --cache
    args2 = _parse_args(["--cache", "/custom/cache", "--models", "gte-small", "e5-small-v2"])
    models2, _dir2 = _resolve_models_and_dir(args2)
    assert models2 == ["gte-small", "e5-small-v2"]
    assert str(args2.cache) == "/custom/cache"

    # 3. Comma-separated --models
    args3 = _parse_args(["--models", "gte-small,bge-small-en-v1.5"])
    models3, _ = _resolve_models_and_dir(args3)
    assert models3 == ["gte-small", "bge-small-en-v1.5"]


def test_bakeoff_write_reports_notes_gpu_cache(tmp_path: Path) -> None:
    report_data = {
        "split": "dev",
        "rows": 112,
        "pool": {
            "total_docs": 31000,
            "distractors": 30000,
            "distractor_seed": 20261122,
            "candidate_and_gold_docs": 1000,
        },
        "prefix_fields_used": "test fields",
        "bm25f_alone": {"hit@1": 0.5, "hit@5": 0.7, "hit@10": 0.8, "mrr": 0.6},
        "models": {
            "gte-small": {
                "metrics": {
                    "throughput_docs_per_s": "cached (GPU build)",
                    "embed_time_s": 0.0,
                    "threads_used": 4,
                    "mean_tokens_per_doc": 100.0,
                    "dimension": 384,
                    "pooling": "mean",
                    "pool_source": "cached (GPU build)",
                },
                "dense_only": {"hit@1": 0.4, "hit@5": 0.6, "hit@10": 0.7, "mrr": 0.5},
                "rrf": {"hit@1": 0.6, "hit@5": 0.8, "hit@10": 0.9, "mrr": 0.7},
            }
        },
        "recommended_model": "gte-small",
        "note": "Pool vectors for gte-small came from the GPU build.",
        "environment": {"platform": "test", "machine": "test", "cpu_count": 4},
    }

    _write_reports(tmp_path, report_data)

    md_text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Pool vectors for gte-small came from the GPU build" in md_text
    assert "query vectors embedded on CPU" in md_text

    json_text = (tmp_path / "report.json").read_text(encoding="utf-8")
    saved_json = json.loads(json_text)
    assert "gte-small" in saved_json["models"]
    assert "Pool vectors for gte-small came from the GPU build" in saved_json["note"]
