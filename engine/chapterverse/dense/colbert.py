"""ColBERT late-interaction neural reranker (Slice 3, requirements R1 and R2).

Provides ColbertScorer with ONNX CPU inference, multi-vector token encoding,
skiplist punctuation masking, MaxSim late-interaction scoring, and subsection windowing.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
from tokenizers import Tokenizer

from chapterverse.dense.embed import ModelLoadError

__all__ = [
    "ColbertMatch",
    "ColbertScorer",
    "compute_maxsim",
    "split_section_windows",
]

_DEFAULT_QUERY_MAXLEN: Final = 32
_DEFAULT_DOC_MAXLEN: Final = 300
_DEFAULT_DIM: Final = 96
_QUERY_MARKER_ID: Final = 1  # [unused0]
_DOC_MARKER_ID: Final = 2  # [unused1]
_SEP_TOKEN_ID: Final = 102  # [SEP]
_DEFAULT_BATCH_SIZE: Final = 64


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_colbert_manifest(model_dir: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise ModelLoadError(f"manifest not found at {manifest_path}")

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ModelLoadError(f"cannot read manifest {manifest_path}: {exc}") from exc

    model_key = model_dir.name
    if model_key not in manifest_data:
        if "answerai-colbert-small-v1" in manifest_data:
            model_key = "answerai-colbert-small-v1"
        elif len(manifest_data) == 1:
            model_key = next(iter(manifest_data))
        else:
            raise ModelLoadError(f"model {model_dir.name!r} not found in {manifest_path}")

    entry = manifest_data[model_key]
    if not isinstance(entry, dict):
        raise ModelLoadError(f"manifest entry for {model_key} is not a dict")
    files = entry.get("files", {})
    for rel_path_str, file_info in files.items():
        file_path = model_dir / rel_path_str
        if not file_path.is_file():
            raise ModelLoadError(f"missing model file: {file_path}")

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

    return cast(dict[str, Any], entry)


@dataclass(frozen=True, slots=True)
class ColbertMatch:
    """ColBERT match result for a candidate section."""

    score: float
    best_window: int
    span_char_start: int
    span_char_end: int

    @property
    def span(self) -> tuple[int, int]:
        """Convenience tuple of (span_char_start, span_char_end)."""
        return (self.span_char_start, self.span_char_end)


def compute_maxsim(
    query_vecs: np.ndarray,
    doc_vecs: np.ndarray,
    doc_offsets: Sequence[tuple[int, int]] | None = None,
) -> tuple[float, tuple[int, int]]:
    """Compute ColBERT MaxSim score and winning token character span.

    MaxSim rule:
    Score is sum over query tokens of max cosine similarity to document tokens.

    Matched span rule:
    1. For each query token, identify the document token with non-empty offsets (end > start)
       achieving maximum cosine similarity (ties broken by lowest document token index).
    2. Count the number of query tokens won by each valid document token.
    3. Identify the document tokens achieving the highest count (most query tokens won).
    4. Span is (min(start), max(end)) across those top winning document tokens.
       If no valid document token won, span is (0, 0).
    """
    if len(query_vecs) == 0 or len(doc_vecs) == 0:
        return 0.0, (0, 0)

    sim = np.matmul(query_vecs, doc_vecs.T)
    max_sim = sim.max(axis=1)
    score = float(max_sim.sum())

    if doc_offsets is None or len(doc_offsets) == 0:
        return score, (0, 0)

    winning_doc_indices = sim.argmax(axis=1)
    counts = Counter(winning_doc_indices)

    valid_counts = {
        idx: c
        for idx, c in counts.items()
        if idx < len(doc_offsets) and doc_offsets[idx][1] > doc_offsets[idx][0]
    }

    if not valid_counts:
        return score, (0, 0)

    max_c = max(valid_counts.values())
    top_winners = [idx for idx, c in valid_counts.items() if c == max_c]
    span_start = min(doc_offsets[idx][0] for idx in top_winners)
    span_end = max(doc_offsets[idx][1] for idx in top_winners)
    return score, (span_start, span_end)


def split_section_windows(text: str, heading: str = "") -> list[str]:
    """Split section text into subsection windows at top-level subsection markers.

    Splits at markers like (1), (2), (1A), (1ZA), never inside an (a)/(b) paragraph
    or cross-reference. Prepend the section heading to every window.
    A section without subsections returns a single window.
    """
    cleaned_text = text.strip()
    if not cleaned_text:
        h = heading.strip()
        return [h] if h else []

    pattern = re.compile(r"(?:^|(?<=[.;:\n])\s+)(?P<marker>\((?P<num>\d+[A-Za-z]*)\))(?=\s+|$)")

    valid_starts: list[int] = []
    for m in pattern.finditer(cleaned_text):
        start = m.start("marker")
        prefix = cleaned_text[:start].rstrip().lower()
        if re.search(r"\b(?:sections?|sub-sections?|subsections?|s\.|ss\.|and|or|to)\s*$", prefix):
            continue
        valid_starts.append(start)

    if not valid_starts:
        windows = [cleaned_text]
    else:
        windows = []
        for i in range(len(valid_starts)):
            st = valid_starts[i]
            en = valid_starts[i + 1] if i + 1 < len(valid_starts) else len(cleaned_text)
            win_text = cleaned_text[st:en].strip()
            if win_text:
                windows.append(win_text)

    h = heading.strip()
    result: list[str] = []
    for w in windows:
        if h and not w.startswith(h):
            result.append(f"{h} {w}".strip())
        else:
            result.append(w.strip())
    return [r for r in result if r]


class ColbertScorer:
    """ONNX CPU ColbertScorer for candidate reranking."""

    def __init__(
        self,
        session: ort.InferenceSession,
        tokenizer: Tokenizer,
        skiplist_ids: frozenset[int],
        *,
        query_maxlen: int = _DEFAULT_QUERY_MAXLEN,
        doc_maxlen: int = _DEFAULT_DOC_MAXLEN,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        dim: int = _DEFAULT_DIM,
        threads: int = 4,
    ) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.skiplist_ids = skiplist_ids
        self.query_maxlen = query_maxlen
        self.doc_maxlen = doc_maxlen
        self.batch_size = max(1, batch_size)
        self.dim = dim
        self.threads = threads

        self.last_latency_ms: float = 0.0
        self.last_windows_count: int = 0
        self.last_tokens_count: int = 0
        self.last_matches: list[ColbertMatch] | None = None

    @classmethod
    def load(
        cls,
        model_dir: Path | str,
        manifest: Path | str | dict[str, Any] | None = None,
        *,
        threads: int = 4,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> ColbertScorer:
        """Load ColbertScorer with SHA-256 verification, fail closed on mismatch."""
        root = Path(model_dir)
        if manifest is None:
            manifest_path = root.parent / "models-manifest.json"
            if not manifest_path.is_file():
                manifest_path = root / "models-manifest.json"
        elif isinstance(manifest, (str, Path)):
            manifest_path = Path(manifest)
            if manifest_path.is_dir():
                manifest_path = manifest_path / "models-manifest.json"
        else:
            manifest_path = root.parent / "models-manifest.json"

        _verify_colbert_manifest(root, manifest_path)

        onnx_model_path = root / "model.onnx"
        if not onnx_model_path.is_file():
            raise ModelLoadError(f"model.onnx not found in {root}")

        tokenizer_path = root / "tokenizer.json"
        if not tokenizer_path.is_file():
            raise ModelLoadError(f"tokenizer.json not found in {root}")

        tokenizer = Tokenizer.from_file(str(tokenizer_path))

        cfg_path = root / "onnx_config.json"
        skiplist_words: list[str] = []
        query_maxlen = _DEFAULT_QUERY_MAXLEN
        doc_maxlen = _DEFAULT_DOC_MAXLEN
        dim = _DEFAULT_DIM
        if cfg_path.is_file():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                skiplist_words = cfg.get("skiplist_words", [])
                query_maxlen = cfg.get("query_length", _DEFAULT_QUERY_MAXLEN)
                doc_maxlen = cfg.get("document_length", _DEFAULT_DOC_MAXLEN)
                dim = cfg.get("embedding_dim", _DEFAULT_DIM)
            except Exception as exc:
                raise ModelLoadError(f"cannot read {cfg_path}: {exc}") from exc

        skiplist_ids = set()
        for word in skiplist_words:
            tid = tokenizer.token_to_id(word)
            if tid is not None:
                skiplist_ids.add(tid)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        session = ort.InferenceSession(
            str(onnx_model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )

        return cls(
            session=session,
            tokenizer=tokenizer,
            skiplist_ids=frozenset(skiplist_ids),
            query_maxlen=query_maxlen,
            doc_maxlen=doc_maxlen,
            batch_size=batch_size,
            dim=dim,
            threads=threads,
        )

    def encode_query(self, text: str) -> np.ndarray:
        """Encode a single query string into L2-normalised token vectors.

        Shape: (seq_len, dim).
        """
        enc = self.tokenizer.encode(text)
        ids = list(enc.ids)
        ids.insert(1, _QUERY_MARKER_ID)
        if len(ids) > self.query_maxlen:
            ids = [*ids[: self.query_maxlen - 1], _SEP_TOKEN_ID]

        att = [1] * len(ids)
        t_ids = [0] * len(ids)

        feed = {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.array([att], dtype=np.int64),
            "token_type_ids": np.array([t_ids], dtype=np.int64),
        }
        raw = self.session.run(None, feed)[0]
        vecs = raw[0]
        norms = np.linalg.norm(vecs, ord=2, axis=-1, keepdims=True)
        normed = vecs / np.clip(norms, a_min=1e-12, a_max=None)
        return cast(np.ndarray, normed.astype(np.float32))

    def encode_docs(self, texts: Sequence[str]) -> list[tuple[np.ndarray, list[tuple[int, int]]]]:
        """Encode multiple document texts in length-sorted batches.

        Punctuation tokens in skiplist_words are excluded from document token vectors.
        Returns list of (doc_vectors, doc_offsets) in original document order.
        """
        n = len(texts)
        if n == 0:
            return []

        doc_data: list[tuple[list[int], list[tuple[int, int]]]] = []
        for text in texts:
            enc = self.tokenizer.encode(text)
            ids = list(enc.ids)
            offsets = list(enc.offsets)
            ids.insert(1, _DOC_MARKER_ID)
            offsets.insert(1, (0, 0))
            if len(ids) > self.doc_maxlen:
                ids = [*ids[: self.doc_maxlen - 1], _SEP_TOKEN_ID]
                offsets = [*offsets[: self.doc_maxlen - 1], (0, 0)]
            doc_data.append((ids, offsets))

        indices = sorted(range(n), key=lambda i: len(doc_data[i][0]))
        sorted_data = [doc_data[i] for i in indices]

        results: list[tuple[np.ndarray, list[tuple[int, int]]]] = []
        batch_size = self.batch_size

        for start_idx in range(0, n, batch_size):
            batch_slice = sorted_data[start_idx : start_idx + batch_size]
            max_len = max(len(ids) for ids, _ in batch_slice)
            bsize = len(batch_slice)

            input_ids = np.zeros((bsize, max_len), dtype=np.int64)
            attention_mask = np.zeros((bsize, max_len), dtype=np.int64)
            token_type_ids = np.zeros((bsize, max_len), dtype=np.int64)

            for b_idx, (ids, _) in enumerate(batch_slice):
                input_ids[b_idx, : len(ids)] = ids
                attention_mask[b_idx, : len(ids)] = 1

            feed = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            }
            raw = self.session.run(None, feed)[0]
            norms = np.linalg.norm(raw, ord=2, axis=-1, keepdims=True)
            normed = raw / np.clip(norms, a_min=1e-12, a_max=None)

            for b_idx, (ids, offsets) in enumerate(batch_slice):
                doc_len = len(ids)
                doc_vecs_all = normed[b_idx, :doc_len]
                kept_vecs: list[np.ndarray] = []
                kept_offsets: list[tuple[int, int]] = []
                for t_idx in range(doc_len):
                    tid = ids[t_idx]
                    if tid not in self.skiplist_ids:
                        kept_vecs.append(doc_vecs_all[t_idx])
                        kept_offsets.append(offsets[t_idx])

                if not kept_vecs:
                    results.append((np.zeros((0, self.dim), dtype=np.float32), []))
                else:
                    arr = np.array(kept_vecs, dtype=np.float32)
                    results.append((arr, kept_offsets))

        restore = [0] * n
        for sorted_pos, orig_pos in enumerate(indices):
            restore[orig_pos] = sorted_pos

        return [results[restore[i]] for i in range(n)]

    def score(self, query: str, docs: Sequence[Sequence[str] | str]) -> list[ColbertMatch]:
        """Score candidate documents against query using ColBERT MaxSim over subsection windows.

        Each element of docs is either a sequence of window strings or a single section string.
        Returns list of ColbertMatch(score, best_window, span_char_start, span_char_end).
        """
        t0 = time.perf_counter()
        if not docs:
            self.last_latency_ms = 0.0
            self.last_windows_count = 0
            self.last_tokens_count = 0
            self.last_matches = []
            return []

        doc_windows_list: list[list[str]] = []
        flattened_windows: list[str] = []
        doc_window_slices: list[tuple[int, int]] = []

        for item in docs:
            windows = split_section_windows(item) if isinstance(item, str) else list(item)
            if not windows:
                windows = [""]
            doc_windows_list.append(windows)
            start = len(flattened_windows)
            flattened_windows.extend(windows)
            end = len(flattened_windows)
            doc_window_slices.append((start, end))

        q_vecs = self.encode_query(query)
        encoded_windows = self.encode_docs(flattened_windows)

        total_tokens = sum(len(vecs) for vecs, _ in encoded_windows)
        self.last_windows_count = len(flattened_windows)
        self.last_tokens_count = total_tokens

        matches: list[ColbertMatch] = []
        for start, end in doc_window_slices:
            best_score = float("-inf")
            best_win_idx = 0
            best_span = (0, 0)
            for win_idx, flat_idx in enumerate(range(start, end)):
                w_vecs, w_offsets = encoded_windows[flat_idx]
                score, span = compute_maxsim(q_vecs, w_vecs, w_offsets)
                if score > best_score:
                    best_score = score
                    best_win_idx = win_idx
                    best_span = span

            matches.append(
                ColbertMatch(
                    score=best_score,
                    best_window=best_win_idx,
                    span_char_start=best_span[0],
                    span_char_end=best_span[1],
                )
            )

        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0
        self.last_matches = matches
        return matches
