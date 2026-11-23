"""Unit tests for engine/chapterverse/dense/embed.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from chapterverse.dense.embed import (
    MODEL_NAMES,
    ModelLoadError,
    OnnxEmbedder,
    load_model,
    verify_model_files,
)

MODELS_DIR = Path("artefacts/models")


@pytest.mark.parametrize("model_name", MODEL_NAMES)
def test_embedder_model(model_name: str) -> None:
    model_dir = MODELS_DIR / model_name
    if not (model_dir / "tokenizer.json").is_file():
        pytest.skip(f"Model files absent for {model_name}")

    embedder = load_model(model_name, MODELS_DIR, threads=2)
    assert isinstance(embedder, OnnxEmbedder)
    assert embedder.dim == 384
    assert "CPUExecutionProvider" in embedder.active_providers
    assert embedder.providers == ("CPUExecutionProvider",)

    # 1. Determinism: same text gives the same vector twice
    doc = "An employee has the right not to be unfairly dismissed by his employer."
    v1 = embedder.embed_docs([doc])
    v2 = embedder.embed_docs([doc])
    np.testing.assert_allclose(v1, v2, atol=1e-5)

    # 2. Norms are 1 (L2 normalised)
    norm = np.linalg.norm(v1[0])
    np.testing.assert_allclose(norm, 1.0, atol=1e-5)

    q_vec = embedder.embed_query("unfair dismissal rights")
    q_norm = np.linalg.norm(q_vec)
    np.testing.assert_allclose(q_norm, 1.0, atol=1e-5)

    # 3. Known-similar pair scores higher than an unrelated pair
    unrelated = "A completely unrelated sentence discussing astrophysics and quantum mechanics."
    batch_vecs = embedder.embed_docs([doc, unrelated])
    assert batch_vecs.shape == (2, 384)

    score_similar = float(batch_vecs[0] @ q_vec)
    score_unrelated = float(batch_vecs[1] @ q_vec)
    assert score_similar > score_unrelated, f"{model_name}: {score_similar} <= {score_unrelated}"


def test_embed_batch_sorted_restores_order() -> None:
    model_name = "bge-small-en-v1.5"
    if not (MODELS_DIR / model_name / "tokenizer.json").is_file():
        pytest.skip(f"Model files absent for {model_name}")

    embedder = load_model(model_name, MODELS_DIR, threads=2, batch_size=2)
    texts = [
        "Short text.",
        "A much longer text containing various words to test length sorting in batching.",
        "Medium length sentence here.",
        "Another very short.",
    ]
    batch_vecs = embedder.embed_docs(texts)
    assert batch_vecs.shape == (len(texts), 384)

    # Individual embeddings should match batched embeddings
    for i, t in enumerate(texts):
        single_vec = embedder.embed_docs([t])[0]
        np.testing.assert_allclose(batch_vecs[i], single_vec, atol=1e-5)


def test_embed_empty() -> None:
    model_name = "bge-small-en-v1.5"
    if not (MODELS_DIR / model_name / "tokenizer.json").is_file():
        pytest.skip(f"Model files absent for {model_name}")

    embedder = load_model(model_name, MODELS_DIR, threads=2)
    empty = embedder.embed_docs([])
    assert empty.shape == (0, 384)


def test_verify_manifest_fail_closed(tmp_path: Path) -> None:
    # Missing manifest
    with pytest.raises(ModelLoadError, match="manifest not found"):
        verify_model_files("bge-small-en-v1.5", tmp_path)

    # Bad model name
    (tmp_path / "models-manifest.json").write_text('{"other": {}}', encoding="utf-8")
    with pytest.raises(ModelLoadError, match="not in"):
        verify_model_files("bge-small-en-v1.5", tmp_path)

    # Missing file
    (tmp_path / "models-manifest.json").write_text(
        '{"bge-small-en-v1.5": {"files": {"model.onnx": {"size": 10, "sha256": "abc"}}}}',
        encoding="utf-8",
    )
    with pytest.raises(ModelLoadError, match="missing model file"):
        verify_model_files("bge-small-en-v1.5", tmp_path)

    # Size mismatch
    bge_dir = tmp_path / "bge-small-en-v1.5"
    bge_dir.mkdir()
    (bge_dir / "model.onnx").write_bytes(b"hello")
    with pytest.raises(ModelLoadError, match="size mismatch"):
        verify_model_files("bge-small-en-v1.5", tmp_path)

    # Hash mismatch
    (tmp_path / "models-manifest.json").write_text(
        '{"bge-small-en-v1.5": {"files": {"model.onnx": {"size": 5, "sha256": "wrong_hash"}}}}',
        encoding="utf-8",
    )
    with pytest.raises(ModelLoadError, match="sha256 mismatch"):
        verify_model_files("bge-small-en-v1.5", tmp_path)


def test_embed_standalone_import() -> None:
    spec = importlib.util.spec_from_file_location(
        "embed_standalone", "engine/chapterverse/dense/embed.py"
    )
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "OnnxEmbedder")
    assert hasattr(mod, "load_model")
