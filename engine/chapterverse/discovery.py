"""Concept discovery and negative evidence (Slice 2, specs/s2-discovery/requirements.md).

Turns a citation-less query (which routes ``ROUTE_UNRESOLVED``) into either candidate
suggestions (:class:`Suggest`) or a negative-evidence refusal (:class:`NoEvidence`),
or refuses out-of-scope queries (case law, foreign law, personal advice) before discovery.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, TypeAlias

from legal_rag_router import Coordinate, Router
from legal_rag_router.concepts import terms
from legal_rag_router.normalise import split_title_year, title_key, title_words
from legal_rag_router.result import ParsedCitation

from chapterverse.citations import instrument_name, provision_label
from chapterverse.contracts import (
    ClarifyEvent,
    LabelledCoordinate,
    RefusalEvent,
)
from chapterverse.templates import (
    out_of_scope_refusal,
    plain_text,
    private_document_refusal,
)

__all__ = [
    "PRIVATE_DOC_PHRASES",
    "PRIVATE_DOC_TYPES",
    "DiscoveryOutcome",
    "NoEvidence",
    "Suggest",
    "check_out_of_scope",
    "check_private_document",
    "discover_step",
    "format_candidate_label",
    "is_instrument_title",
    "is_named_private_document",
    "remove_bare_section",
]

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


@dataclass(frozen=True, slots=True)
class Suggest:
    """Discovered candidate provisions offered to the user to confirm."""

    candidates: tuple[LabelledCoordinate, ...]
    confident: bool
    question: str
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
    """Check whether a query is out of scope (case law, foreign law, personal advice).

    Returns a ``RefusalEvent`` of kind ``out_of_scope`` if matched, else None.
    """
    if _CASE_LAW_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="case_law")
    if _NON_UK_CS_RE.search(query) or _NON_UK_CI_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="non_uk_law")
    if _PERSONAL_ADVICE_RE.search(query):
        return out_of_scope_refusal(snapshot, reason="personal_advice")
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


def discover_step(
    router: Router,
    query: object,
    *,
    limit: int = 10,
) -> DiscoveryOutcome:
    """Discover candidate provisions from the concept index for a citation-less query.

    Returns one of:
    - ``RefusalEvent`` of kind ``out_of_scope`` if the query cues case law, non-UK law, or
      personal advice.
    - ``NoEvidence(receipt)`` with a negative-evidence ``RefusalEvent`` if no provision matched.
    - ``Suggest(candidates, confident, question)`` with up to ``limit`` candidate provisions.
    """
    snapshot = ""
    if hasattr(router, "index") and router.index is not None:
        snapshot = getattr(router.index, "snapshot", "") or ""

    if isinstance(query, str):
        out_of_scope = check_out_of_scope(query, snapshot)
        if out_of_scope is not None:
            return out_of_scope

    discovered = router.discover(query, limit=limit)
    if discovered.index_snapshot:
        snapshot = discovered.index_snapshot

    if not discovered.candidates or discovered.reason in ("no_match", "no_terms"):
        n = 0
        concepts = getattr(router, "_concepts", None) or getattr(router, "concepts", None)
        if concepts is not None and hasattr(concepts, "first_instrument_doc"):
            n = concepts.first_instrument_doc
        elif concepts is not None and hasattr(concepts, "doc_count"):
            n = concepts.doc_count

        norm_terms = terms(query) if isinstance(query, str) else []
        query_terms = " ".join(dict.fromkeys(norm_terms))
        msg = (
            f"Searched {n} provisions in the statute book as of {snapshot} for: "
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

    candidates = tuple(
        LabelledCoordinate(
            coordinate=str(c.coordinate),
            label=format_candidate_label(c.coordinate, c.heading, router),
        )
        for c in discovered.candidates[:limit]
    )

    if discovered.confident:
        question = "Did you mean one of these provisions?"
    else:
        question = "These provisions may be related. Choose one, or name the Act and section."

    return Suggest(
        candidates=candidates,
        confident=discovered.confident,
        question=question,
    )
