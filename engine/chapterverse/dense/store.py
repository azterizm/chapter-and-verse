"""Dense vector store and exact int8+binary retrieval (requirement R2).

Provides VectorStore protocol and BinaryInt8Store with fail-closed manifest
and corpus_id verification, memory-mapped storage, and two-stage search:
Hamming distance over sign bits then exact float32 int8 rescale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from chapterverse.store.manifest import sha256_file

__all__ = [
    "BinaryInt8Store",
    "DenseLoadError",
    "VectorStore",
]

MANIFEST_NAME = "dense-manifest.json"
REQUIRED_FILES = ("codes.bin", "scales.f32", "vectors.i8")


class DenseLoadError(RuntimeError):
    """Raised when a dense index is corrupt, missing files, or fails verification."""


@runtime_checkable
class VectorStore(Protocol):
    """Protocol for dense vector stores."""

    @property
    def count(self) -> int:
        """Total number of vectors in the store."""
        ...

    def search(
        self,
        query: np.ndarray,
        k: int,
    ) -> list[tuple[int, float]]:
        """Search top-k doc_ids with their float similarity scores."""
        ...


class BinaryInt8Store:
    """Exact vector store with int8 scalar quantisation and binary sign codes.

    Searches in two stages:
    1. Hamming distance on 48-byte sign codes (XOR of uint64 views and bitwise_count),
       keeping the best `rerank` candidates (ties broken by doc_id).
    2. Rescore those candidates by `(int8_row * scale) . query` in float32,
       returning top `k` by score (ties broken by doc_id).
    """

    def __init__(
        self,
        *,
        vectors: np.ndarray,
        codes: np.ndarray,
        scales: np.ndarray,
        manifest: dict[str, Any],
        path: Path,
    ) -> None:
        self._path = path
        self._manifest = manifest
        self._vectors = vectors
        self._codes = codes
        self._codes_u64 = codes.view(np.uint64)
        self._scales = scales.astype(np.float32)

        self.corpus_id: str = str(manifest.get("corpus_id", ""))
        self.snapshot: str = str(manifest.get("snapshot", ""))
        self.dim: int = int(manifest.get("dim", 384))
        self.count: int = int(manifest.get("count", len(vectors)))
        self.first_instrument_doc: int = int(manifest.get("first_instrument_doc", self.count))

    @classmethod
    def open(
        cls,
        dir_path: Path | str,
        *,
        corpus_id: str,
    ) -> BinaryInt8Store:
        """Open a dense index directory, verify manifest and hashes, fail-closed."""
        root = Path(dir_path)
        manifest_file = root / MANIFEST_NAME
        if not manifest_file.is_file():
            raise DenseLoadError(f"manifest not found at {manifest_file}")

        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DenseLoadError(f"cannot read manifest {manifest_file}: {exc}") from exc

        # Fail closed on corpus_id mismatch
        actual_corpus_id = manifest.get("corpus_id")
        if actual_corpus_id != corpus_id:
            msg = (
                f"corpus_id mismatch in {manifest_file}: "
                f"expected {corpus_id}, got {actual_corpus_id}"
            )
            raise DenseLoadError(msg)

        files_meta = manifest.get("files", {})
        for req_name in REQUIRED_FILES:
            if req_name not in files_meta:
                raise DenseLoadError(f"manifest missing file entry for {req_name}")

        for filename, info in files_meta.items():
            file_path = root / filename
            if not file_path.is_file():
                raise DenseLoadError(f"missing index file: {file_path}")

            expected_size = info.get("size")
            actual_size = file_path.stat().st_size
            if expected_size is not None and actual_size != expected_size:
                raise DenseLoadError(
                    f"size mismatch for {filename}: expected {expected_size}, got {actual_size}"
                )

            expected_sha = info.get("sha256")
            if expected_sha is not None:
                actual_sha = sha256_file(file_path)
                if actual_sha != expected_sha:
                    raise DenseLoadError(
                        f"sha256 mismatch for {filename}: expected {expected_sha}, got {actual_sha}"
                    )

        count = int(manifest.get("count", 0))
        dim = int(manifest.get("dim", 384))
        code_bytes = dim // 8

        # Memory map files
        vecs_path = root / "vectors.i8"
        codes_path = root / "codes.bin"
        scales_path = root / "scales.f32"

        vectors_mmap = np.memmap(vecs_path, dtype=np.int8, mode="r", shape=(count, dim))
        codes_mmap = np.memmap(codes_path, dtype=np.uint8, mode="r", shape=(count, code_bytes))
        scales_data = np.fromfile(scales_path, dtype=np.float32)
        if len(scales_data) != dim:
            raise DenseLoadError(
                f"scales dimension mismatch: expected {dim}, got {len(scales_data)}"
            )

        return cls(
            vectors=vectors_mmap,
            codes=codes_mmap,
            scales=scales_data,
            manifest=manifest,
            path=root,
        )

    def search(
        self,
        query: np.ndarray,
        k: int = 10,
        *,
        rerank: int = 1000,
        provisions_only: bool = True,
    ) -> list[tuple[int, float]]:
        """Exact search: Hamming filter on sign bits, then float32 int8 dot product rescore."""
        if k <= 0 or self.count == 0:
            return []

        q_float = query.astype(np.float32)
        n_search = (
            min(self.first_instrument_doc, self.count)
            if provisions_only and self.first_instrument_doc > 0
            else self.count
        )
        if n_search == 0:
            return []

        # 1. Hamming distance on packed sign codes
        q_bits = q_float > 0
        q_packed = np.packbits(q_bits, bitorder="little").astype(np.uint8)
        q_u64 = q_packed.view(np.uint64)

        codes_u64 = self._codes_u64[:n_search]
        xor = codes_u64 ^ q_u64
        dists = np.bitwise_count(xor).sum(axis=-1).astype(np.int32)

        # Top `rerank` candidates, ties broken by doc_id
        m = min(rerank, n_search)
        compound = dists.astype(np.int64) * n_search + np.arange(n_search, dtype=np.int64)
        if m < n_search:
            part = np.argpartition(compound, m)[:m]
            cands = part[np.argsort(compound[part])]
        else:
            cands = np.argsort(compound)

        # 2. Rescore top candidates with float32 dequantisation
        cand_vecs = self._vectors[cands].astype(np.float32)
        cand_floats = cand_vecs * self._scales
        scores = cand_floats.dot(q_float)

        # Return top k by score, ties broken by doc_id
        k_res = min(k, len(cands))
        order = np.lexsort((cands, -scores))[:k_res]
        return [(int(cands[i]), float(scores[i])) for i in order]
