"""Bound retrieval ranking and range citations (S4.4, requirements R1, R2, R4).

Provides:
- Instrument provision doc ranges map built in background (R1).
- Restricted BM25F and dense section ranking inside a named instrument (R2).
- Range citation sibling expansion (R4).
"""

from __future__ import annotations

import logging
import math
import re
import sys
import threading
import time
from bisect import bisect_left
from collections.abc import Mapping
from typing import Final

from legal_rag_router.concepts import PRIOR_FLAGS, ConceptIndex, terms, unpack_tf
from legal_rag_router.discovery import DEFAULT_DISCOVERY, DiscoveryPolicy
from legal_rag_verifier.corpus import instrument_coordinate

from chapterverse.contracts import Coordinate, TextStore
from chapterverse.dense.retriever import DenseRetriever

__all__ = [
    "InstrumentRanges",
    "build_instrument_ranges",
    "expand_siblings",
    "parse_sibling_range",
    "rank_sections",
]

log = logging.getLogger(__name__)

DEFAULT_RRF_K: Final[int] = 60
MAX_SIBLINGS: Final[int] = 26
MIN_PARTS_FOR_SIBLINGS: Final[int] = 5
INSTRUMENT_COORD_PARTS: Final[int] = 4


def _build_ranges_fast(
    data: memoryview | bytes, offsets: list[int], n: int
) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    prev_inst_b: bytes | None = None
    start_doc = 0
    for doc_id in range(n):
        off = offsets[doc_id]
        line = bytes(data[off : off + 80])
        idx = line.find(b'"c": "')
        if idx == -1:
            continue
        start_c = idx + 6
        end_quote = line.find(b'"', start_c)
        coord_bytes = line[start_c:end_quote]
        parts = coord_bytes.split(b"/")
        if len(parts) >= INSTRUMENT_COORD_PARTS:
            inst_b = b"/".join(parts[:4]) if parts[2].isdigit() else b"/".join(parts[:5])
        else:
            inst_b = coord_bytes
        if inst_b != prev_inst_b:
            if prev_inst_b is not None:
                ranges[prev_inst_b.decode("ascii")] = (start_doc, doc_id)
            prev_inst_b = inst_b
            start_doc = doc_id
    if prev_inst_b is not None:
        ranges[prev_inst_b.decode("ascii")] = (start_doc, n)
    return ranges


def _build_ranges_generic(concepts: ConceptIndex, n: int) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    prev_inst: str | None = None
    start_doc = 0
    for doc_id in range(n):
        doc = concepts.doc(doc_id)
        inst = instrument_coordinate(doc.coordinate)
        if inst != prev_inst:
            if prev_inst is not None:
                ranges[prev_inst] = (start_doc, doc_id)
            prev_inst = inst
            start_doc = doc_id
    if prev_inst is not None:
        ranges[prev_inst] = (start_doc, n)
    return ranges


def build_instrument_ranges(concepts: ConceptIndex) -> dict[str, tuple[int, int]]:
    """Scan concept index documents 0..first_instrument_doc-1 to build
    ``instrument coordinate -> (first_doc, end_doc)`` (end exclusive).
    """
    n = concepts.first_instrument_doc
    if n == 0:
        return {}

    doc_table = getattr(concepts, "doc_table", None)
    data = getattr(doc_table, "_data", None)
    offsets = getattr(doc_table, "_offsets", None)
    if data is not None and offsets is not None and len(offsets) >= n:
        try:
            return _build_ranges_fast(data, offsets, n)
        except Exception:
            log.debug("fast instrument range scan failed, falling back to doc(i)", exc_info=True)

    return _build_ranges_generic(concepts, n)


class InstrumentRanges:
    """Thread-safe holder for the instrument provision doc ranges map (requirement R1).

    Built once on a background thread started after router load; until ready (or on failure),
    retrieval falls back to standard window selection, logged once.
    """

    def __init__(self) -> None:
        self._ranges: dict[str, tuple[int, int]] | None = None
        self._ready: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self._build_time_s: float = 0.0

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._ranges is not None

    def get(self, instrument: str) -> tuple[int, int] | None:
        if not self.ready or self._ranges is None:
            return None
        return self._ranges.get(instrument)

    def __len__(self) -> int:
        return len(self._ranges) if self._ranges is not None else 0

    def memory_bytes(self) -> int:
        if self._ranges is None:
            return 0
        total = sys.getsizeof(self._ranges)
        for k, v in self._ranges.items():
            total += sys.getsizeof(k) + sys.getsizeof(v)
        return total

    def start_background_build(self, concepts: ConceptIndex) -> threading.Thread:
        """Start building ranges on a daemon thread."""

        def _worker() -> None:
            t0 = time.perf_counter()
            try:
                self._ranges = build_instrument_ranges(concepts)
                self._build_time_s = time.perf_counter() - t0
                size_mb = self.memory_bytes() / (1024 * 1024)
                log.info(
                    "instrument ranges built in %.2fs: %d instruments, %.2f MB",
                    self._build_time_s,
                    len(self._ranges),
                    size_mb,
                )
            except Exception as exc:
                self._error = exc
                log.exception("failed to build instrument ranges")
            finally:
                self._ready.set()

        thread = threading.Thread(target=_worker, name="instrument-ranges-build", daemon=True)
        self._thread = thread
        thread.start()
        return thread

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the background build to complete (for testing or synchronization)."""
        return self._ready.wait(timeout=timeout)


def _query_term_weights(
    concepts: ConceptIndex, words: list[str], policy: DiscoveryPolicy
) -> dict[str, tuple[float, int]]:
    out: dict[str, tuple[float, int]] = {}
    for i, word in enumerate(words):
        out[word] = (1.0, i)
    for i, word in enumerate(words):
        for syn in concepts.thesaurus.get(word, ()):
            out.setdefault(syn, (policy.synonym_weight, i))
    return out


def _score_bm25f(
    query: str,
    doc_range: tuple[int, int],
    concepts: ConceptIndex,
    policy: DiscoveryPolicy,
) -> dict[int, float]:
    typed = list(dict.fromkeys(terms(query)))
    query_terms = _query_term_weights(concepts, typed, policy) if typed else {}
    if not query_terms:
        return {}

    start_doc, end_doc = doc_range
    n = concepts.doc_count
    cutoff = max(1.0, policy.max_df_share * n)
    dfs = {t: concepts.document_frequency(t) for t in query_terms}
    rarest = sorted(dfs[t] for t in typed if dfs[t])
    if rarest and rarest[0] > cutoff:
        cutoff = float(rarest[min(1, len(rarest) - 1)])

    weights, bs, avg = policy.field_weights, policy.field_b, concepts.average_lengths
    k1 = policy.k1
    c0 = [1.0 - b for b in bs]
    c1 = [b / a if a else 0.0 for b, a in zip(bs, avg, strict=True)]

    bm25_scores: dict[int, float] = {}
    for term, (qweight, _origin) in query_terms.items():
        df = dfs[term]
        if df == 0 or df > cutoff:
            continue
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        gain = qweight * idf
        docs, tfs = concepts.postings(term)
        i_start = bisect_left(docs, start_doc)
        i_end = bisect_left(docs, end_doc)
        for doc, packed in zip(docs[i_start:i_end], tfs[i_start:i_end], strict=True):
            f_lengths = concepts.field_lengths(doc)
            unpacked = unpack_tf(packed)
            tf = 0.0
            for f in range(len(weights)):
                count = unpacked[f]
                if count:
                    tf += count * weights[f] / (c0[f] + c1[f] * f_lengths[f])
            bm25_scores[doc] = bm25_scores.get(doc, 0.0) + gain * tf / (k1 + tf)

    return bm25_scores


def _score_dense(
    query: str,
    doc_range: tuple[int, int],
    dense: DenseRetriever | None,
) -> dict[int, int]:
    if dense is None:
        return {}
    start_doc, end_doc = doc_range
    try:
        q_vec = dense.embedder.embed_query(query)
        scores = dense.store.score_rows(q_vec, start_doc, end_doc)
        doc_scores = [(start_doc + i, float(scores[i])) for i in range(len(scores))]
        doc_scores.sort(key=lambda item: (-item[1], item[0]))
        return {doc_id: rank for rank, (doc_id, _) in enumerate(doc_scores, start=1)}
    except Exception as exc:  # noqa: BLE001
        log.warning("dense scoring failed in rank_sections: %s", exc)
        return {}


def _rrf_fuse(
    bm25_ranked: dict[int, int],
    dense_ranked: dict[int, int],
    rrf_k: int,
) -> list[tuple[float, int]]:
    all_doc_ids = set(bm25_ranked.keys()) | set(dense_ranked.keys())
    fused: list[tuple[float, int]] = []
    for doc_id in all_doc_ids:
        score = 0.0
        if doc_id in bm25_ranked:
            score += 1.0 / (rrf_k + bm25_ranked[doc_id])
        if doc_id in dense_ranked:
            score += 1.0 / (rrf_k + dense_ranked[doc_id])
        fused.append((score, doc_id))
    fused.sort(key=lambda item: (-item[0], item[1]))
    return fused


# Regional lift and prior penalties mirror legal_rag_router.discovery._candidates and _prior.
def _effective_prior_penalties(query: str, policy: DiscoveryPolicy) -> list[float]:
    penalties = list(policy.prior_penalties)
    query_terms = set(terms(query))
    if {"northern", "ireland"} <= query_terms and "northern_ireland" in PRIOR_FLAGS:
        penalties[PRIOR_FLAGS.index("northern_ireland")] = 1.0
    if {"scotland", "scottish"} & query_terms and "scotland" in PRIOR_FLAGS:
        penalties[PRIOR_FLAGS.index("scotland")] = 1.0
    return penalties


def _prior_factor(
    concepts: ConceptIndex,
    doc_id: int,
    penalties: list[float],
    policy: DiscoveryPolicy,
) -> float:
    """Mirrors legal_rag_router.discovery._prior."""
    if not hasattr(concepts, "prior"):
        return 1.0
    try:
        flags, degree = concepts.prior(doc_id)
    except Exception:  # noqa: BLE001
        return 1.0
    factor = 1.0 + policy.in_degree_weight * degree / 255
    for i, penalty in enumerate(penalties):
        if flags >> i & 1:
            factor *= penalty
    return factor


def rank_sections(
    query: str,
    instrument: str,
    *,
    concepts: ConceptIndex,
    ranges: InstrumentRanges | Mapping[str, tuple[int, int]] | None = None,
    dense: DenseRetriever | None = None,
    policy: DiscoveryPolicy = DEFAULT_DISCOVERY,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[tuple[Coordinate, float]]:
    """Restricted ranking of provisions inside a named instrument (requirements R2, R7).

    Computes BM25F using parameters from ``DEFAULT_DISCOVERY`` over the instrument's
    contiguous provision document range. When ``dense`` is available, computes exact
    float32 dequantised dot products over those rows and fuses with RRF. Applies the same
    prior factor discovery applies (with regional lifts) before final ranking.
    Deterministic ties broken by doc id.
    """
    doc_range: tuple[int, int] | None = None
    if isinstance(ranges, (InstrumentRanges, Mapping)):
        doc_range = ranges.get(instrument)
    if doc_range is None or doc_range[0] >= doc_range[1]:
        return []

    bm25_scores = _score_bm25f(query, doc_range, concepts, policy)
    dense_ranked = _score_dense(query, doc_range, dense)
    penalties = _effective_prior_penalties(query, policy)

    if dense_ranked:
        sorted_bm25 = sorted(
            (d for d in bm25_scores if bm25_scores[d] > 0),
            key=lambda d: (-bm25_scores[d], d),
        )
        bm25_ranked = {doc_id: rank for rank, doc_id in enumerate(sorted_bm25, start=1)}
        fused = _rrf_fuse(bm25_ranked, dense_ranked, rrf_k)
        adjusted = [
            (score * _prior_factor(concepts, doc_id, penalties, policy), doc_id)
            for score, doc_id in fused
        ]
        adjusted.sort(key=lambda item: (-item[0], item[1]))
        return [(concepts.doc(doc_id).coordinate, score) for score, doc_id in adjusted]

    adjusted_bm25 = [
        (bm25_scores[d] * _prior_factor(concepts, d, penalties, policy), d)
        for d in bm25_scores
        if bm25_scores[d] > 0
    ]
    adjusted_bm25.sort(key=lambda item: (-item[0], item[1]))
    return [(concepts.doc(doc_id).coordinate, score) for score, doc_id in adjusted_bm25]


def parse_sibling_range(query: str, start_label: str) -> list[str] | None:
    """Extract list or range of sibling labels beginning with start_label.

    Supports: "(a) to (d)", "(a)-(d)", "(a) - (d)", "(a) and (b)",
    "(a), (b) and (c)", "(1) to (3)".
    """
    esc = re.escape(start_label)
    range_pat = re.compile(
        rf"\({esc}\)\s*(?:to|-)\s*\((?P<end>[a-zA-Z0-9]+)\)",
        re.IGNORECASE,
    )
    m_range = range_pat.search(query)
    if m_range:
        return [start_label, m_range.group("end")]

    list_pat = re.compile(
        rf"\({esc}\)(?:\s*,\s*\([a-zA-Z0-9]+\))*\s*(?:,\s*)?and\s*\((?P<end>[a-zA-Z0-9]+)\)",
        re.IGNORECASE,
    )
    m_list = list_pat.search(query)
    if m_list:
        return re.findall(r"\(([a-zA-Z0-9]+)\)", m_list.group(0))

    return None


def _find_matching_siblings(
    rows: list[dict[str, object]],
    parent: str,
    labels_to_match: list[str],
) -> list[str] | None:
    prefix = parent + "/"
    children: list[tuple[str, str]] = []
    for row in rows:
        c = str(row.get("coordinate", ""))
        if c.startswith(prefix):
            rel = c[len(prefix) :]
            if "/" not in rel:
                children.append((rel, c))

    if not children:
        return None

    child_labels = [c[0].lower() for c in children]
    child_coords = [c[1] for c in children]

    for lbl in labels_to_match:
        if lbl.lower() not in child_labels:
            return None

    first_idx = child_labels.index(labels_to_match[0].lower())
    last_idx = child_labels.index(labels_to_match[-1].lower())

    if last_idx < first_idx:
        return None

    result = child_coords[first_idx : last_idx + 1]
    if len(result) > MAX_SIBLINGS:
        return None

    return result


def expand_siblings(query: str, coordinate: Coordinate, store: TextStore) -> list[Coordinate]:
    """Pure function expanding sibling paragraphs or subsections named in query (requirement R4).

    Reads parent's children from the store in store order and returns siblings from first
    to last named label inclusive. Any unclear case (labels not siblings, not found, end
    before start, more than 26) returns ``[coordinate]`` unchanged.
    """
    coord_str = str(coordinate)
    parts = coord_str.split("/")
    if len(parts) < MIN_PARTS_FOR_SIBLINGS:
        return [coord_str]

    parent = coord_str.rsplit("/", 1)[0]
    start_label = parts[-1]

    labels_to_match = parse_sibling_range(query, start_label)
    if not labels_to_match:
        return [coord_str]

    try:
        rows = store.get(parent)
    except (KeyError, ValueError, OSError):
        return [coord_str]

    matched = _find_matching_siblings(rows, parent, labels_to_match)
    if matched is None:
        return [coord_str]

    return matched
