"""ONNX CPU embedder and model loader (requirement R2).

Provides OnnxEmbedder with batching, length-sorted execution, L2 normalization,
and load_model helper with SHA-256 manifest verification (fail-closed).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, Literal, cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from tokenizers import Tokenizer

__all__ = [
    "MODEL_NAMES",
    "ModelLoadError",
    "OnnxEmbedder",
    "load_model",
    "verify_model_files",
]

MODEL_NAMES: Final = ("bge-small-en-v1.5", "e5-small-v2", "gte-small")
MANIFEST_NAME: Final = "models-manifest.json"
_MIN_TENSOR_DIMS: Final = 3
_DEFAULT_EMBED_DIM: Final = 384


class ModelLoadError(RuntimeError):
    """Raised when model files are missing, corrupted, or fail manifest verification."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_files(name: str, models_dir: Path | str) -> dict[str, Any]:
    """Verify each file's size and sha256 against models-manifest.json.

    Raises:
        ModelLoadError: if manifest or any file is missing, size or sha256 mismatch.
    """
    root = Path(models_dir)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ModelLoadError(f"manifest not found at {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelLoadError(f"cannot read manifest {manifest_path}: {exc}") from exc

    if name not in manifest:
        raise ModelLoadError(f"model {name!r} not in {manifest_path}")

    model_entry = manifest[name]
    files = model_entry.get("files", {})
    model_dir = root / name

    for rel_path_str, file_info in files.items():
        file_path = model_dir / rel_path_str
        if not file_path.is_file():
            raise ModelLoadError(f"missing model file {file_path}")

        expected_size = file_info.get("size")
        actual_size = file_path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            msg = f"size mismatch for {file_path}: expected {expected_size}, got {actual_size}"
            raise ModelLoadError(msg)

        expected_sha = file_info.get("sha256")
        if expected_sha is not None:
            actual_sha = _sha256(file_path)
            if actual_sha != expected_sha:
                msg = f"sha256 mismatch for {file_path}: expected {expected_sha}, got {actual_sha}"
                raise ModelLoadError(msg)

    return dict(model_entry)


class OnnxEmbedder:
    """CPU ONNX embedder for dense retrieval.

    Handles tokenization with padding/truncation, sorted-by-length batching,
    CLS or mean pooling, and L2 normalization.
    """

    def __init__(
        self,
        model_dir: Path | str,
        *,
        pooling: Literal["cls", "mean"],
        query_prefix: str = "",
        doc_prefix: str = "",
        max_tokens: int = 512,
        threads: int | None = None,
        batch_size: int = 64,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        pad_to_multiple_of: int | None = None,
    ) -> None:
        root = Path(model_dir)
        tokenizer_path = root / "tokenizer.json"
        if not tokenizer_path.is_file():
            raise ModelLoadError(f"missing tokenizer.json in {root}")

        self.model_dir = root
        self.pooling: Literal["cls", "mean"] = pooling
        self.query_prefix = query_prefix
        self.doc_prefix = doc_prefix
        self.max_tokens = max_tokens
        self.batch_size = max(1, batch_size)
        self.providers = tuple(providers)

        cpu_count = os.cpu_count() or 4
        self.threads = threads if threads is not None else min(cpu_count, 4)

        # Initialize tokenizer
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=max_tokens)
        pad_id = tokenizer.token_to_id("[PAD]")
        pad_id = 0 if pad_id is None else pad_id
        # pad_to_multiple_of bounds the number of distinct input shapes (GPU builds); the
        # attention mask keeps the pooled vectors the same up to float rounding.
        tokenizer.enable_padding(
            length=None,
            pad_id=pad_id,
            pad_token="[PAD]",  # noqa: S106
            pad_to_multiple_of=pad_to_multiple_of,
        )
        self.tokenizer = tokenizer

        # Locate ONNX model file
        onnx_path = root / "onnx" / "model.onnx"
        if not onnx_path.is_file():
            onnx_path = root / "model.onnx"
        if not onnx_path.is_file():
            raise ModelLoadError(f"no model.onnx found in {root} or {root / 'onnx'}")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self.threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        self.session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=list(providers)
        )

        out_shape = self.session.get_outputs()[0].shape
        self.dim: int = (
            int(out_shape[-1]) if len(out_shape) >= _MIN_TENSOR_DIMS else _DEFAULT_EMBED_DIM
        )

    @property
    def active_providers(self) -> list[str]:
        """Return the active execution providers for the ONNX session."""
        return list(self.session.get_providers())

    def _pool_and_norm(
        self, last_hidden_state: np.ndarray, attention_mask: np.ndarray
    ) -> np.ndarray:
        if self.pooling == "cls":
            vecs = last_hidden_state[:, 0, :]
        else:
            mask = np.expand_dims(attention_mask, axis=-1).astype(np.float32)
            sum_vecs = np.sum(last_hidden_state * mask, axis=1)
            sum_mask = np.clip(np.sum(mask, axis=1), a_min=1e-9, a_max=None)
            vecs = sum_vecs / sum_mask

        norms = np.linalg.norm(vecs, ord=2, axis=-1, keepdims=True)
        normed = vecs / np.clip(norms, a_min=1e-12, a_max=None)
        return cast(np.ndarray, normed.astype(np.float32))

    def embed_docs(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a sequence of documents, L2 normalised. Shape: (n, dim)."""
        n = len(texts)
        if n == 0:
            return np.zeros((0, self.dim), dtype=np.float32)

        prefixed = [f"{self.doc_prefix}{t}" for t in texts]

        # Sort by character length for efficient batched padding
        indices = sorted(range(n), key=lambda i: len(prefixed[i]))
        sorted_texts = [prefixed[i] for i in indices]

        batches_out: list[np.ndarray] = []
        batch_size = self.batch_size

        for start_idx in range(0, n, batch_size):
            batch_slice = sorted_texts[start_idx : start_idx + batch_size]
            encs = self.tokenizer.encode_batch(batch_slice)
            input_ids = np.array([e.ids for e in encs], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            token_type_ids = np.array([e.type_ids for e in encs], dtype=np.int64)

            feed = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            }
            hidden = self.session.run(["last_hidden_state"], feed)[0]
            pooled = self._pool_and_norm(hidden, attention_mask)
            batches_out.append(pooled)

        stacked = np.concatenate(batches_out, axis=0) if len(batches_out) > 1 else batches_out[0]

        # Restore original order
        restore = [0] * n
        for sorted_pos, orig_pos in enumerate(indices):
            restore[orig_pos] = sorted_pos

        return stacked[restore]

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query, L2 normalised. Shape: (dim,)."""
        prefixed = f"{self.query_prefix}{text}"
        enc = self.tokenizer.encode(prefixed)
        input_ids = np.array([enc.ids], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask], dtype=np.int64)
        token_type_ids = np.array([enc.type_ids], dtype=np.int64)

        feed = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        hidden = self.session.run(["last_hidden_state"], feed)[0]
        pooled = self._pool_and_norm(hidden, attention_mask)
        return cast(np.ndarray, pooled[0])


def load_model(
    name: str,
    models_dir: Path | str,
    *,
    threads: int | None = None,
    max_tokens: int = 512,
    batch_size: int = 64,
    providers: Sequence[str] = ("CPUExecutionProvider",),
    pad_to_multiple_of: int | None = None,
) -> OnnxEmbedder:
    """Verify SHA-256 hashes against models-manifest.json and load an OnnxEmbedder.

    Raises:
        ModelLoadError: if verification fails.
    """
    root = Path(models_dir)
    verify_model_files(name, root)

    model_dir = root / name
    pooling_cfg_path = model_dir / "1_Pooling" / "config.json"
    pooling: Literal["cls", "mean"] = "mean"
    if pooling_cfg_path.is_file():
        try:
            cfg = json.loads(pooling_cfg_path.read_text(encoding="utf-8"))
            if cfg.get("pooling_mode_cls_token"):
                pooling = "cls"
            elif cfg.get("pooling_mode_mean_tokens"):
                pooling = "mean"
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    # Model specific prefixes and poolings per requirements
    if name == "bge-small-en-v1.5":
        pooling = "cls"
        query_prefix = "Represent this sentence for searching relevant passages: "
        doc_prefix = ""
    elif name == "e5-small-v2":
        pooling = "mean"
        query_prefix = "query: "
        doc_prefix = "passage: "
    elif name == "gte-small":
        pooling = "mean"
        query_prefix = ""
        doc_prefix = ""
    else:
        query_prefix = ""
        doc_prefix = ""

    return OnnxEmbedder(
        model_dir,
        pooling=pooling,
        query_prefix=query_prefix,
        doc_prefix=doc_prefix,
        max_tokens=max_tokens,
        threads=threads,
        batch_size=batch_size,
        providers=providers,
        pad_to_multiple_of=pad_to_multiple_of,
    )
