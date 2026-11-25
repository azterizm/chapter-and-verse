"""Concept discovery and negative evidence (Slice 2, specs/s2-discovery/requirements.md).

Turns a citation-less query (which routes ``ROUTE_UNRESOLVED``) into either candidate
suggestions (:class:`Suggest`) or a negative-evidence refusal (:class:`NoEvidence`),
or refuses out-of-scope queries (case law, foreign law, personal advice) before discovery.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, TypeAlias

from legal_rag_router import Coordinate, Router
from legal_rag_router.concepts import PRIOR_FLAGS, terms
from legal_rag_router.normalise import split_title_year, title_key, title_words
from legal_rag_router.result import ParsedCitation
from legal_rag_verifier.corpus import instrument_coordinate
from legal_rag_verifier.premise import Premise

from chapterverse.citations import instrument_name, provision_label
from chapterverse.contracts import (
    ClarifyEvent,
    LabelledCoordinate,
    RefusalEvent,
    TextStore,
)
from chapterverse.dense.colbert import ColbertScorer, split_section_windows
from chapterverse.dense.doctext import _DEFAULTS
from chapterverse.dense.retriever import DenseRetriever
from chapterverse.templates import (
    out_of_scope_refusal,
    plain_text,
    private_document_refusal,
)

__all__ = [
    "PRIVATE_DOC_PHRASES",
    "PRIVATE_DOC_TYPES",
    "ClarifyPrior",
    "DiscoveryOutcome",
    "NoEvidence",
    "Suggest",
    "check_out_of_scope",
    "check_private_document",
    "discover_step",
    "format_candidate_label",
    "is_instrument_title",
    "is_named_private_document",
    "load_clarify_prior",
    "remove_bare_section",
]

log = logging.getLogger(__name__)

_NI_SERIES: Final = frozenset({"nisi", "nisr", "nisro", "apni", "nia", "mnia"})
_SCOTLAND_SERIES: Final = frozenset({"ssi", "asp", "aosp"})
_YEAR_PART_INDEX: Final = 2
_CALENDAR_INST_PARTS: Final = 4
_REGNAL_INST_PARTS: Final = 5
_PRIOR_RRF_K: Final = 20
_PRIOR_DENSE_WEIGHT: Final = 1.05
_PRIOR_SCHED_RRF_PENALTY: Final = 0.90
_PRIOR_SCHED_BM25_PENALTY: Final = 0.95
_PRIOR_SEC_RRF_WEIGHT: Final = 0.20
_PRIOR_SEC_BM25_WEIGHT: Final = 0.30
_PRIOR_INST_RRF_WEIGHT: Final = 0.05
_RRF_FLAG_PENALTIES: Final[tuple[float, ...]] = (0.3, 0.4, 0.4, 0.5, 0.3, 1.0)

PRIVATE_DOC_TYPES: Final[tuple[str, ...]] = (
    "Terms and Conditions",
    "Articles of Association",
    "Code of Conduct",
    "Trust Deed",
    "Agreement",
    "Lease",
    "Contract",
    "Policy",
    "Handbook",
    "Deed",
    "Licence",
    "License",
    "Terms",
    "Conditions",
    "Constitution",
    "Memorandum",
    "Bylaws",
    "Rules",
    "Manual",
    "Plan",
    "Schedule",
    "Letter",
    "Notice",
    "Will",
    "Statement",
)

PRIVATE_DOC_PHRASES: Final[tuple[str, ...]] = (
    "my lease",
    "my contract",
    "our policy",
    "the agreement",
    "the attached",
    "uploaded",
    "this document",
)

_DOC_TYPE_WORDS: Final[tuple[str, ...]] = (
    r"Terms\s+and\s+Conditions",
    r"Articles\s+of\s+Association",
    r"Code\s+of\s+Conduct",
    r"Trust\s+Deed",
    r"Agreement",
    r"Lease",
    r"Contract",
    r"Policy",
    r"Handbook",
    r"Deed",
    r"Licence",
    r"License",
    r"Terms",
    r"Conditions",
    r"Constitution",
    r"Memorandum",
    r"Bylaws",
    r"Rules",
    r"Manual",
    r"Plan",
    r"Schedule",
    r"Letter",
    r"Notice",
    r"Will",
    r"Statement",
)

_DOC_TYPES_PATTERN: Final = "|".join(_DOC_TYPE_WORDS)

_NON_ENTITY_WORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "my",
        "our",
        "your",
        "their",
        "his",
        "her",
        "its",
        "any",
        "each",
        "every",
        "some",
        "all",
        "both",
        "neither",
        "either",
        "under",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "about",
        "from",
        "to",
        "can",
        "could",
        "may",
        "might",
        "must",
        "shall",
        "should",
        "will",
        "would",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "where",
        "when",
        "why",
        "how",
        "employment",
        "tenancy",
        "settlement",
        "standard",
    }
)

# Matches a capitalized name followed by one of the document types (case-insensitive for type,
# e.g. AcmeCloud contract, Mayfair Lease Agreement), optional additional document types,
# and optional year.
_NAMED_PRIVATE_DOC_RE: Final = re.compile(
    rf"\b(?P<name>[A-Z][a-zA-Z0-9'.-]*(?:\s+(?:&|and|of|for|the|[A-Z][a-zA-Z0-9'.-]*))*)\s+"
    rf"(?P<type>(?i:{_DOC_TYPES_PATTERN}))"
    rf"(?:\s+(?i:{_DOC_TYPES_PATTERN}))*"
    rf"(?:\s+(?:1[2-9]\d\d|20\d\d))?\b"
)

_CONTENT_REQUEST_PATTERNS: Final[tuple[str, ...]] = (
    # "what does (my|our|the|this) (contract|lease|policy|agreement|handbook|... ) say"
    # and "what does section 4 of my lease say"
    r"\bwhat\s+(?:does|do)\s+(?:(?:clause|section|paragraph|rule|part|schedule)\s+[0-9a-zA-Z.]+\s+of\s+)?(?:my|our|the|this)\s+(?:contract|lease|policy|agreement|handbook|document|warranty|terms)\s+say(?:s)?\b",
    # "what does this document say/mean"
    r"\bwhat\s+does\s+this\s+document\s+(?:say|mean)\b",
    # "what is our policy on leave?"
    r"\bwhat\s+is\s+(?:our|my)\s+(?:policy|contract|agreement|handbook|terms)\s+on\b",
    # "under (clause|section|paragraph) N of (my|our|the) ..."
    r"\b(?:under|in)\s+(?:clause|section|paragraph|rule|part|schedule)\s+[0-9a-zA-Z.]+\s+of\s+(?:my|our|the|this)\b",
    # "(clause|section|paragraph) N of (my|our|the) ..."
    r"\b(?:clause|section|paragraph)\s+[0-9a-zA-Z.]+\s+of\s+(?:my|our|the|this)\s+(?:contract|lease|policy|agreement|handbook|document|warranty|terms)\b",
    # "according to (my|our|the) ..."
    r"\baccording\s+to\s+(?:my|our|this|the\s+(?:attached|uploaded|(?:contract|lease|policy|agreement|handbook|document|warranty|terms|records|repositories)))\b",
    # "as stated in my contract"
    r"\bas\s+stated\s+in\s+(?:my|our|the|this)\s+(?:contract|lease|policy|agreement|handbook|document|terms)\b",
    # "summarise/summarize (the|this) (uploaded|attached) ..."
    r"\bsummari[sz]e\s+(?:the|this)\s+(?:uploaded|attached)\b",
    # "uploaded"
    r"\buploaded\b",
    # "attached document" / "the attached" / "in the attached file"
    r"\battached\s+document\b|\b(?:the|this|in\s+the)\s+attached\b",
    # "in our (policy|contract|agreement|handbook|records|repositories|vector stores)"
    r"\bin\s+(?:our|my)\s+(?:policy|contract|agreement|handbook|records|repositories|vector\s*stores?)\b",
    # "check my contract", "check the agreement"
    r"\bcheck\s+(?:my|our|the|this)\s+(?:contract|agreement|lease|policy|handbook)\b",
    # "under my lease", "under this document", "under the agreement"
    r"\bunder\s+(?:my\s+lease|this\s+document|the\s+agreement)\b",
)

_CONTENT_REQUEST_RE: Final = re.compile(
    "|".join(f"(?:{p})" for p in _CONTENT_REQUEST_PATTERNS),
    re.IGNORECASE,
)

_BARE_SECTION_RE: Final = re.compile(
    r"\b(?:under\s+|in\s+)?(?:sections?|sec\.?|s\.?|subsections?|sub-s\.?|ss\.?|articles?|art\.?|paragraphs?|para\.?|rules?|r\.?)\s*\d+[a-z]*(?:\s*\([a-z0-9]+\))*(?:\s+(?:of|under)\b)?",
    re.IGNORECASE,
)

_CASE_LAW_RE: Final = re.compile(
    r"\b(?:case[\s-]law|judg(?:e)?ments?|precedents?)\b",
    re.IGNORECASE,
)
_NON_UK_CI_RE: Final = re.compile(
    r"\b(?:americans?|california|france|french)\b"
    r"|\bus\s+(?:law|legislation|statute|code|jurisdiction|court)\b"
    r"|\b(?:in|under)\s+the\s+us\b",
    re.IGNORECASE,
)
_NON_UK_CS_RE: Final = re.compile(r"\b(?:US|USA|U\.S\.|U\.S\.A\.)\b")
_PERSONAL_ADVICE_RE: Final = re.compile(
    r"\b(?:should\s+i|my\s+lawyer)\b",
    re.IGNORECASE,
)
_SYSTEM_SEARCH_RE: Final = re.compile(
    r"\bconnected\s+repositor(?:y|ies)\b"
    r"|\bvector\s+stores?\b"
    r"|\bknowledge\s+bases?\b"
    r"|\bcanary\b"
    r"|\bsearch\s+all(?:\s+connected)?\s+(?:repositories|systems|stores|documents|files|databases)\b"
    r"|\b(?:api|secret)\s+keys?\b"
    r"|\bsystem\s+prompts?\b",
    re.IGNORECASE,
)
_CANDIDATE_CV_RE: Final = re.compile(r"\bcandidate\s+cv\b", re.IGNORECASE)
_PERSON_VERB_RE: Final = re.compile(
    r"\b(?:summari[sz]e|describe|tell\s+me\s+about|what\s+(?:is|are))\b",
    re.IGNORECASE,
)
_PERSON_TOPIC_RE: Final = re.compile(
    r"\b(?:backgrounds?|experiences?|cvs?|curriculum\s+vitae|careers?|biograph(?:y|ies)|r[eé]sum[eé]s?|qualifications?)\b",
    re.IGNORECASE,
)
_PERSON_TITLE_RE: Final = re.compile(r"\b(?:Miss|Prof|Mrs|Mr|Dr|Ms|Mx)(?:\.|\b)\s+[A-Z][a-zA-Z]*\b")


def _is_named_person_query(query: str) -> bool:
    if _CANDIDATE_CV_RE.search(query):
        return True
    if not _PERSON_VERB_RE.search(query):
        return False
    if not _PERSON_TOPIC_RE.search(query):
        return False
    return bool(_PERSON_TITLE_RE.search(query))


@dataclass(frozen=True, slots=True)
class Suggest:
    """Discovered candidate provisions offered to the user to confirm."""

    candidates: tuple[LabelledCoordinate, ...]
    confident: bool
    question: str
    spans: tuple[tuple[int, int], ...] | None = None
    event: ClarifyEvent = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "event",
            ClarifyEvent(question=self.question, candidates=self.candidates),
        )

    @property
    def clarify_event(self) -> ClarifyEvent:
        """Alias for :attr:`event`."""
        return self.event


@dataclass(frozen=True, slots=True)
class NoEvidence:
    """No matching provision found; carries a negative-evidence refusal receipt."""

    receipt: RefusalEvent

    @property
    def event(self) -> RefusalEvent:
        """Alias for :attr:`receipt`."""
        return self.receipt


DiscoveryOutcome: TypeAlias = Suggest | NoEvidence | RefusalEvent


def check_out_of_scope(query: str, snapshot: str = "") -> RefusalEvent | None:
    """Check whether a query is out of scope (case law, foreign law, personal advice,
    system search, or named persons).

    Returns a ``RefusalEvent`` of kind ``out_of_scope`` if matched, else None.
    """
    if _CASE_LAW_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="case_law")
    if _NON_UK_CS_RE.search(query) or _NON_UK_CI_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="non_uk_law")
    if _PERSONAL_ADVICE_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="personal_advice")
    if _SYSTEM_SEARCH_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="system_search")
    if _is_named_person_query(query):
        return out_of_scope_refusal(snapshot, reason="named_person")
    return None


def is_instrument_title(title_text: str, router: Any) -> bool:
    """Check if title_text matches a known legislation title in the router index."""
    if router is None:
        return False
    idx = getattr(router, "index", getattr(router, "_index", router))
    tables = getattr(idx, "tables", {})
    titles_tbl = tables.get("titles")
    cov_titles_tbl = tables.get("coverage_titles")
    if not titles_tbl and not cov_titles_tbl:
        return False

    base, year = split_title_year(title_text)
    words = title_words(base)
    if not words:
        return False
    keys = (title_key(words, year), title_key(words))
    for k in keys:
        if (titles_tbl and k in titles_tbl) or (cov_titles_tbl and k in cov_titles_tbl):
            return True
    return False


def is_named_private_document(query: str, router: Any = None) -> bool:
    """Check if query names a non-legislation (private) document or asks for its content (R1, R4).

    Detects:
    - a named private document with a capitalised name (for example "the Mayfair Lease Agreement",
      "the AcmeCloud contract", "the ZenithHost SaaS Agreement"), unless known to the router index;
    - a content request about one:
      - "what does (my|our|the|this) <type> say"
      - "under (clause|section|paragraph) N of (my|our|the) ..."
      - "according to (my|our|the) ..."
      - "summarise/summarize (the|this) (uploaded|attached) ..."
      - "uploaded"
      - "attached document"
      - "in our (policy|contract|agreement|handbook|records|repositories|vector stores)"
    Bare mentions ("change my contract", "end my tenancy agreement", "my employment contract",
    "a settlement agreement") are NOT refused.
    """
    if not query:
        return False

    for m in _NAMED_PRIVATE_DOC_RE.finditer(query):
        name = m.group("name")
        name_words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", name)]
        if name_words and all(w in _NON_ENTITY_WORDS for w in name_words):
            continue
        matched_title = m.group(0).strip()
        if not is_instrument_title(matched_title, router):
            return True

    return bool(_CONTENT_REQUEST_RE.search(query))


def check_private_document(
    query: str, router: Any = None, snapshot: str = ""
) -> RefusalEvent | None:
    """Check if query names a private document (requirements R1).

    Returns an out_of_scope RefusalEvent if matched, else None.
    """
    if is_named_private_document(query, router):
        return private_document_refusal(snapshot)
    return None


def remove_bare_section(query: str, citations: Sequence[ParsedCitation] = ()) -> str:
    """Remove bare section / provision reference from question (requirements R2)."""
    text = query
    spans: list[tuple[int, int]] = []
    for c in citations:
        span = getattr(c, "span", None)
        resolution = getattr(c, "resolution", "")
        provision = getattr(c, "provision", None)
        if span and provision and resolution == "unlinked":
            spans.append(span)

    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        if 0 <= start <= end <= len(text):
            text = text[:start] + " " + text[end:]

    text = _BARE_SECTION_RE.sub(" ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"^[,\s;:]+", "", text)
    text = re.sub(r"[,\s;:]+$", "", text)
    return " ".join(text.split())


def format_candidate_label(
    coord: Coordinate | str,
    heading: str | None,
    router: Router | None = None,
) -> str:
    """Format a candidate coordinate and heading into a readable citation label.

    E.g. ``s.98 Employment Rights Act 1996: Fairness``.
    """
    coord_str = str(coord)
    head = provision_label(coord_str)
    inst_id: str | None = None
    if isinstance(coord, Coordinate):
        inst_id = coord.instrument_id
    else:
        with contextlib.suppress(ValueError):
            inst_id = Coordinate.parse(coord_str).instrument_id

    title = ""
    if router is not None and inst_id and hasattr(router, "index") and router.index is not None:
        with contextlib.suppress(ValueError, KeyError, RuntimeError):
            info = router.index.info(inst_id)
            if getattr(info, "title", None):
                title = info.title

    inst = instrument_name(coord_str, {"title": title} if title else None)
    head_and_inst = f"{head} {inst}" if head else inst
    if heading and heading.strip():
        return f"{head_and_inst}: {plain_text(heading.strip())}"
    return head_and_inst


def _build_no_evidence(snapshot: str, router: Router, query: object) -> NoEvidence:
    n = 0
    concepts = getattr(router, "_concepts", None) or getattr(router, "concepts", None)
    if concepts is not None:
        doc_n = getattr(concepts, "first_instrument_doc", None)
        if isinstance(doc_n, int):
            n = doc_n
        else:
            doc_c = getattr(concepts, "doc_count", None)
            if isinstance(doc_c, int):
                n = doc_c

    norm_terms = terms(query) if isinstance(query, str) else []
    query_terms = " ".join(dict.fromkeys(norm_terms))
    msg = (
        f"Searched {n:,} provisions in the statute book as of {snapshot} for: "
        f"{query_terms}. No provision matched."
    )
    receipt = RefusalEvent(
        kind="ROUTE_UNRESOLVED",
        message=msg,
        suggestions=(),
        snapshot=snapshot,
        link=None,
    )
    return NoEvidence(receipt=receipt)


@dataclass(frozen=True, slots=True)
class ClarifyPrior:
    """Optional section and instrument priors for clarify candidate ordering."""

    sections: Mapping[str, int] = field(default_factory=dict)
    instruments: Mapping[str, int] = field(default_factory=dict)
    snapshot: str = ""

    def section_count(self, coordinate: str) -> int:
        return self.sections.get(_section_coordinate(coordinate), 0)

    def instrument_count(self, coordinate: str) -> int:
        return self.instruments.get(instrument_coordinate(coordinate), 0)


def load_clarify_prior(
    path: Path | str | None,
    *,
    router_snapshot: str,
) -> ClarifyPrior | None:
    """Load an optional clarify prior JSON artefact, returning None if absent, invalid, or stale."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("clarify prior load failed (%s), using default order", type(exc).__name__)
        return None
    if not isinstance(raw, dict) or raw.get("format_version", 1) != 1:
        log.warning("clarify prior format invalid, using default order")
        return None
    snapshot = str(raw.get("snapshot") or "").strip()
    if not snapshot or snapshot != router_snapshot:
        log.warning(
            "clarify prior snapshot %r does not match router snapshot %r, using default order",
            snapshot,
            router_snapshot,
        )
        return None
    raw_sections = raw.get("sections")
    raw_instruments = raw.get("instruments")
    sections = (
        {str(k): int(v) for k, v in raw_sections.items()}
        if isinstance(raw_sections, Mapping)
        else {}
    )
    instruments = (
        {str(k): int(v) for k, v in raw_instruments.items()}
        if isinstance(raw_instruments, Mapping)
        else {}
    )
    return ClarifyPrior(
        sections=sections,
        instruments=instruments,
        snapshot=snapshot,
    )


def _provision_start(parts: Sequence[str]) -> int:
    return (
        _CALENDAR_INST_PARTS
        if (len(parts) > _YEAR_PART_INDEX and parts[_YEAR_PART_INDEX].isdigit())
        else _REGNAL_INST_PARTS
    )


def _section_coordinate(coord: str) -> str:
    parts = coord.split("/")
    base = _provision_start(parts)
    if len(parts) <= base:
        return coord
    if parts[base].startswith("sch") and len(parts) > base + 1:
        return "/".join(parts[: base + 2])
    return "/".join(parts[: base + 1])


def _is_schedule_coordinate(coord: str) -> bool:
    parts = coord.split("/")
    prov_start = _provision_start(parts)
    return len(parts) > prov_start and parts[prov_start].startswith("sch")


def _instrument_flags(coord_str: str, router: Router) -> int:
    idx = getattr(router, "index", None)
    if idx is None or not hasattr(idx, "instrument"):
        return 0
    inst_id: str | None = None
    with contextlib.suppress(ValueError):
        inst_id = Coordinate.parse(coord_str).instrument_id
    if not inst_id:
        return 0
    flags = 0
    with contextlib.suppress(ValueError, KeyError, TypeError, RuntimeError):
        info = idx.instrument(inst_id)
        if info is None:
            return 0
        repealed = getattr(info, "repealed", None)
        series = getattr(info, "series", None)
        title = getattr(info, "title", None)
        if not (isinstance(repealed, bool) and isinstance(series, str) and isinstance(title, str)):
            return 0
        title_cf = title.casefold()
        if repealed:
            flags |= 1 << PRIOR_FLAGS.index("repealed")
        if series in _NI_SERIES or "northern ireland" in title_cf:
            flags |= 1 << PRIOR_FLAGS.index("northern_ireland")
        if series in _SCOTLAND_SERIES or "(scotland)" in title_cf:
            flags |= 1 << PRIOR_FLAGS.index("scotland")
        if "amendment" in title_cf:
            flags |= 1 << PRIOR_FLAGS.index("amending")
        if "commencement" in title_cf:
            flags |= 1 << PRIOR_FLAGS.index("commencement")
    return flags


def _source_flags(
    coord_str: str,
    doc_id: int | None,
    router: Router,
) -> int:
    concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
    if doc_id is not None and concepts is not None and hasattr(concepts, "prior"):
        with contextlib.suppress(ValueError, KeyError, IndexError, TypeError, RuntimeError):
            flags, _ = concepts.prior(doc_id)
            if isinstance(flags, int):
                return flags
    return _instrument_flags(coord_str, router)


def _flag_multiplier(flags: int, query: object) -> float:
    if not flags:
        return 1.0
    q_lower = query.lower() if isinstance(query, str) else ""
    mult = 1.0
    ni_idx = PRIOR_FLAGS.index("northern_ireland")
    sc_idx = PRIOR_FLAGS.index("scotland")
    for i, penalty in enumerate(_RRF_FLAG_PENALTIES):
        if penalty == 1.0 or not (flags >> i & 1):
            continue
        if i == ni_idx and "northern ireland" in q_lower:
            continue
        if i == sc_idx and ("scotland" in q_lower or "scottish" in q_lower):
            continue
        mult *= penalty
    return mult


def _rrf_fuse_candidates(
    discovered_candidates: Sequence[Any],
    dense_hits: Sequence[Any],
    *,
    pool: int,
    rrf_k: int,
    limit: int,
    router: Router,
    query: object = "",
    prior: ClarifyPrior | None = None,
) -> tuple[LabelledCoordinate, ...]:
    bm25_rank: dict[str, int] = {}
    heading_map: dict[str, str | None] = {}
    original_coord: dict[str, Any] = {}

    for rank, c in enumerate(discovered_candidates[:pool], start=1):
        coord_str = str(c.coordinate)
        bm25_rank[coord_str] = rank
        heading_map[coord_str] = c.heading
        original_coord[coord_str] = c.coordinate

    dense_rank: dict[str, int] = {}
    dense_doc_id: dict[str, int] = {}
    for h in dense_hits[:pool]:
        coord_str = str(h.coordinate)
        dense_rank[coord_str] = h.rank
        if isinstance(getattr(h, "doc_id", None), int):
            dense_doc_id[coord_str] = h.doc_id
        if coord_str not in heading_map:
            heading_map[coord_str] = h.heading
            original_coord[coord_str] = h.coordinate

    dense_weight = _PRIOR_DENSE_WEIGHT if prior is not None else 1.0
    all_coords = set(bm25_rank.keys()) | set(dense_rank.keys())
    scored: list[tuple[float, float, str]] = []
    for coord_str in all_coords:
        b_rank = bm25_rank.get(coord_str)
        d_rank = dense_rank.get(coord_str)
        score = 0.0
        if b_rank is not None:
            score += 1.0 / (rrf_k + b_rank)
        if d_rank is not None:
            score += dense_weight / (rrf_k + d_rank)
        if prior is not None:
            flags = _source_flags(coord_str, dense_doc_id.get(coord_str), router)
            score *= _flag_multiplier(flags, query)
            if _is_schedule_coordinate(coord_str):
                score *= _PRIOR_SCHED_RRF_PENALTY
            sec_c = prior.section_count(coord_str)
            if sec_c:
                score *= 1.0 + _PRIOR_SEC_RRF_WEIGHT * math.log1p(sec_c) / 8.0
            inst_c = prior.instrument_count(coord_str)
            if inst_c:
                score *= 1.0 + _PRIOR_INST_RRF_WEIGHT * math.log1p(inst_c) / 5.0
        b_tie = float(b_rank) if b_rank is not None else float("inf")
        scored.append((score, b_tie, coord_str))

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    return tuple(
        LabelledCoordinate(
            coordinate=coord_str,
            label=format_candidate_label(
                original_coord[coord_str], heading_map.get(coord_str), router
            ),
        )
        for _, _, coord_str in scored[:limit]
    )


def _get_candidate_windows(
    coord_str: str,
    heading: str | None,
    store: TextStore | None,
    router: Router,
) -> list[str]:
    active_store = store if store is not None else _DEFAULTS.get("store")

    if active_store is not None:
        try:
            rows = active_store.get(coord_str)
            prov_row = rows[1] if len(rows) > 1 else {}
            p_heading = heading or str(prov_row.get("title") or "")
            premise = Premise.from_records(rows)
            if premise.passages:
                return [
                    f"{p_heading} {p.text}".strip()
                    if p_heading and not p.text.startswith(p_heading)
                    else p.text.strip()
                    for p in premise.passages
                    if p.text
                ]
            body = " ".join(
                str(t)
                for r in rows[1:]
                for t in (r.get("text"), r.get("text_after"))
                if t and not str(t).startswith(". . .")
            )
            return split_section_windows(body, heading=p_heading)
        except (KeyError, IndexError, ValueError, TypeError, OSError, RuntimeError):
            return [heading or coord_str]

    h = heading or coord_str
    return [h]


def _get_initial_candidates(
    discovered_candidates: Sequence[Any],
    query: object,
    limit: int,
    dense: DenseRetriever | None,
    *,
    pool: int,
    rrf_k: int,
    router: Router,
    prior: ClarifyPrior | None = None,
) -> tuple[LabelledCoordinate, ...]:
    if dense is None:
        if prior is None:
            return tuple(
                LabelledCoordinate(
                    coordinate=str(c.coordinate),
                    label=format_candidate_label(c.coordinate, c.heading, router),
                )
                for c in discovered_candidates[:limit]
            )
        bm25_scored: list[tuple[float, int, Any]] = []
        for rank, c in enumerate(discovered_candidates[:pool], start=1):
            coord_str = str(c.coordinate)
            raw_score = getattr(c, "score", None)
            score = float(raw_score) if isinstance(raw_score, int | float) else 1.0 / rank
            if _is_schedule_coordinate(coord_str):
                score *= _PRIOR_SCHED_BM25_PENALTY
            sec_c = prior.section_count(coord_str)
            if sec_c:
                score *= 1.0 + _PRIOR_SEC_BM25_WEIGHT * math.log1p(sec_c) / 8.0
            bm25_scored.append((score, rank, c))
        bm25_scored.sort(key=lambda item: (-item[0], item[1], str(item[2].coordinate)))
        return tuple(
            LabelledCoordinate(
                coordinate=str(c.coordinate),
                label=format_candidate_label(c.coordinate, c.heading, router),
            )
            for _, _, c in bm25_scored[:limit]
        )
    dense_hits = dense.search(str(query), k=pool) if isinstance(query, str) else []
    return _rrf_fuse_candidates(
        discovered_candidates,
        dense_hits,
        pool=pool,
        rrf_k=rrf_k,
        limit=limit,
        router=router,
        query=query,
        prior=prior,
    )


def _bm25_discover_limit(
    *,
    limit: int,
    pool: int,
    rerank_n: int,
    dense: DenseRetriever | None,
    colbert: ColbertScorer | None,
    prior: ClarifyPrior | None,
) -> int:
    if dense is not None or prior is not None:
        return max(pool, rerank_n) if colbert is not None else pool
    return rerank_n if colbert is not None else limit


def discover_step(
    router: Router,
    query: object,
    *,
    limit: int = 10,
    dense: DenseRetriever | None = None,
    pool: int = 50,
    rrf_k: int | None = None,
    colbert: ColbertScorer | None = None,
    rerank_n: int = 24,
    store: TextStore | None = None,
    prior: ClarifyPrior | None = None,
) -> DiscoveryOutcome:
    """Discover candidate provisions from the concept index for a citation-less query.

    Returns one of:
    - ``RefusalEvent`` of kind ``out_of_scope`` if the query cues case law, non-UK law, or
      personal advice.
    - ``NoEvidence(receipt)`` with a negative-evidence ``RefusalEvent`` if no provision matched.
    - ``Suggest(candidates, confident, question, spans)`` with up to ``limit`` candidate provisions.
    """
    snapshot = ""
    if hasattr(router, "index") and router.index is not None:
        snapshot = getattr(router.index, "snapshot", "") or ""

    if isinstance(query, str):
        out_of_scope = check_out_of_scope(query, snapshot)
        if out_of_scope is not None:
            return out_of_scope

    active_rrf_k = (_PRIOR_RRF_K if prior is not None else 60) if rrf_k is None else rrf_k
    bm25_limit = _bm25_discover_limit(
        limit=limit,
        pool=pool,
        rerank_n=rerank_n,
        dense=dense,
        colbert=colbert,
        prior=prior,
    )
    discovered = router.discover(query, limit=bm25_limit)
    if discovered.index_snapshot:
        snapshot = discovered.index_snapshot

    if not discovered.candidates or discovered.reason in ("no_match", "no_terms"):
        return _build_no_evidence(snapshot, router, query)

    candidates: tuple[LabelledCoordinate, ...]
    spans: tuple[tuple[int, int], ...] | None = None

    if colbert is None:
        candidates = _get_initial_candidates(
            discovered.candidates,
            query,
            limit,
            dense,
            pool=pool,
            rrf_k=active_rrf_k,
            router=router,
            prior=prior,
        )
    else:
        initial_cands = _get_initial_candidates(
            discovered.candidates,
            query,
            rerank_n,
            dense,
            pool=pool,
            rrf_k=active_rrf_k,
            router=router,
            prior=prior,
        )
        if not initial_cands:
            candidates = ()
            spans = ()
        else:
            cands_windows = [
                _get_candidate_windows(str(c.coordinate), c.label, store, router)
                for c in initial_cands
            ]
            matches = colbert.score(str(query), cands_windows)
            scored = [
                (matches[i].score, i + 1, c, matches[i].span) for i, c in enumerate(initial_cands)
            ]
            scored.sort(key=lambda item: (-item[0], item[1]))
            top_scored = scored[:limit]
            candidates = tuple(item[2] for item in top_scored)
            spans = tuple(item[3] for item in top_scored)

    if discovered.confident:
        question = "Did you mean one of these provisions?"
    else:
        question = "These provisions may be related. Choose one, or name the Act and section."

    return Suggest(
        candidates=candidates,
        confident=discovered.confident,
        question=question,
        spans=spans,
    )
