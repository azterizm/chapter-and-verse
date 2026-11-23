"""Tests for dense store and index build (requirement R7).

Tests:
- quantisation round trip
- exact search equals brute-force float ranking on a small random set when rerank >= N
- Hamming tie order
- manifest tamper and corpus mismatch fail closed
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from chapterverse.contracts import canonical_json
from chapterverse.dense.store import BinaryInt8Store, DenseLoadError
from chapterverse.store.manifest import sha256_file


def _make_dummy_store(
    tmp_path: Path,
    vectors: np.ndarray,
    corpus_id: str = "test-corpus-id-1234",
    snapshot: str = "2026-09-28",
    first_instrument_doc: int | None = None,
) -> Path:
    count, dim = vectors.shape
    max_abs = np.max(np.abs(vectors), axis=0).astype(np.float32)
    scales = max_abs / 127.0
    scales[scales == 0] = 1.0
    scales_f32 = scales.astype(np.float32)

    q = np.clip(np.round(vectors / scales), -127, 127).astype(np.int8)
    bits = vectors > 0
    codes = np.packbits(bits, axis=-1, bitorder="little").astype(np.uint8)

    tmp_path.mkdir(parents=True, exist_ok=True)
    vecs_file = tmp_path / "vectors.i8"
    codes_file = tmp_path / "codes.bin"
    scales_file = tmp_path / "scales.f32"
    manifest_file = tmp_path / "dense-manifest.json"

    vecs_file.write_bytes(q.tobytes())
    codes_file.write_bytes(codes.tobytes())
    scales_file.write_bytes(scales_f32.tobytes())

    files_info = {
        "codes.bin": {"sha256": sha256_file(codes_file), "size": codes_file.stat().st_size},
        "scales.f32": {"sha256": sha256_file(scales_file), "size": scales_file.stat().st_size},
        "vectors.i8": {"sha256": sha256_file(vecs_file), "size": vecs_file.stat().st_size},
    }

    manifest = {
        "format_version": 1,
        "kind": "chapterverse-dense-index",
        "model": "bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "dim": dim,
        "count": count,
        "corpus_id": corpus_id,
        "snapshot": snapshot,
        "first_instrument_doc": count if first_instrument_doc is None else first_instrument_doc,
        "quantisation": "int8_scalar_per_dim+binary_sign",
        "files": files_info,
    }
    manifest_file.write_bytes(canonical_json(manifest))
    return tmp_path


def test_quantisation_round_trip() -> None:
    np.random.seed(42)
    dim = 384
    n = 200
    vecs = np.random.randn(n, dim).astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=-1, keepdims=True)

    max_abs = np.max(np.abs(vecs), axis=0).astype(np.float32)
    scales = max_abs / 127.0
    scales[scales == 0] = 1.0

    int8_vecs = np.clip(np.round(vecs / scales), -127, 127).astype(np.int8)
    dequant = int8_vecs.astype(np.float32) * scales

    # Maximum absolute error per dimension is at most scale / 2 + float rounding
    max_err = np.max(np.abs(vecs - dequant))
    max_allowed = np.max(scales) * 0.6
    assert max_err <= max_allowed

    # Mean absolute error is small
    mae = np.mean(np.abs(vecs - dequant))
    assert mae < 0.005

    # Sign bits packing and unpacking
    bits = vecs > 0
    packed = np.packbits(bits, axis=-1, bitorder="little")
    unpacked = np.unpackbits(packed, axis=-1, bitorder="little")
    assert np.array_equal(bits, unpacked.astype(bool))


def test_exact_search_equals_brute_force(tmp_path: Path) -> None:
    np.random.seed(123)
    n = 150
    dim = 384
    vecs = np.random.randn(n, dim).astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=-1, keepdims=True)

    store_dir = _make_dummy_store(tmp_path / "exact_store", vecs, corpus_id="corpus-xyz")
    store = BinaryInt8Store.open(store_dir, corpus_id="corpus-xyz")
    assert store.count == n

    # Scales and int8 vecs for brute force
    max_abs = np.max(np.abs(vecs), axis=0).astype(np.float32)
    scales = max_abs / 127.0
    int8_vecs = np.clip(np.round(vecs / scales), -127, 127).astype(np.int8)
    dequant_all = int8_vecs.astype(np.float32) * scales

    for _ in range(5):
        query = np.random.randn(dim).astype(np.float32)
        query = query / np.linalg.norm(query)

        # Brute-force float dot product on all dequantised vectors
        bf_scores = dequant_all.dot(query)
        # Expected ranking with ties broken by doc_id
        bf_order = np.lexsort((np.arange(n), -bf_scores))[:10]
        expected_hits = [(int(i), float(bf_scores[i])) for i in bf_order]

        # Search with rerank >= N
        hits = store.search(query, k=10, rerank=n, provisions_only=False)
        assert len(hits) == 10
        for (hit_id, hit_score), (exp_id, exp_score) in zip(hits, expected_hits, strict=True):
            assert hit_id == exp_id
            assert abs(hit_score - exp_score) < 1e-5


def test_hamming_tie_order(tmp_path: Path) -> None:
    dim = 384
    # Create 4 vectors with identical sign bits (all positive) but different magnitudes
    vecs = np.ones((4, dim), dtype=np.float32)
    vecs[0] *= 1.0
    vecs[1] *= 2.0
    vecs[2] *= 1.0
    vecs[3] *= 0.5

    store_dir = _make_dummy_store(tmp_path / "hamming_store", vecs, corpus_id="corpus-tie")
    store = BinaryInt8Store.open(store_dir, corpus_id="corpus-tie")

    query = np.ones(dim, dtype=np.float32)
    # Query has all positive bits, so Hamming distance to all 4 vecs is 0.
    # Ties in Hamming distance must be broken by doc_id (0, 1, 2, 3)
    # Rescoring: vecs[1] will have highest score. vecs[0] and vecs[2] have equal scores!
    # Among vecs[0] and vecs[2], tie must be broken by doc_id 0 before 2.
    hits = store.search(query, k=4, rerank=4, provisions_only=False)
    doc_order = [h[0] for h in hits]
    assert doc_order[0] == 1  # highest score
    assert doc_order[1] == 0  # equal score with 2, but doc_id 0 < 2
    assert doc_order[2] == 2
    assert doc_order[3] == 3


def test_manifest_tamper_and_corpus_mismatch(tmp_path: Path) -> None:
    vecs = np.random.randn(20, 384).astype(np.float32)
    store_dir = _make_dummy_store(tmp_path / "tamper_store", vecs, corpus_id="corpus-valid")

    # 1. Successful open
    store = BinaryInt8Store.open(store_dir, corpus_id="corpus-valid")
    assert store.count == 20

    # 2. Corpus ID mismatch fails closed
    with pytest.raises(DenseLoadError, match="corpus_id mismatch"):
        BinaryInt8Store.open(store_dir, corpus_id="wrong-corpus-id")

    # 3. Missing manifest fails closed
    with pytest.raises(DenseLoadError, match="manifest not found"):
        BinaryInt8Store.open(tmp_path / "non_existent_dir", corpus_id="corpus-valid")

    # 4. Tampered vectors file (modify byte) fails closed
    vecs_file = store_dir / "vectors.i8"
    original_bytes = vecs_file.read_bytes()
    tampered_bytes = bytearray(original_bytes)
    tampered_bytes[0] = (tampered_bytes[0] + 1) % 256
    vecs_file.write_bytes(tampered_bytes)
    with pytest.raises(DenseLoadError, match=r"sha256 mismatch for vectors\.i8"):
        BinaryInt8Store.open(store_dir, corpus_id="corpus-valid")
    vecs_file.write_bytes(original_bytes)

    # 5. Tampered size fails closed
    scales_file = store_dir / "scales.f32"
    orig_scales = scales_file.read_bytes()
    scales_file.write_bytes(orig_scales + b"\x00\x00\x00\x00")
    with pytest.raises(DenseLoadError, match=r"size mismatch for scales\.f32"):
        BinaryInt8Store.open(store_dir, corpus_id="corpus-valid")
    scales_file.write_bytes(orig_scales)
