"""Concept discovery and negative evidence (Slice 2, specs/s2-discovery/requirements.md).

Turns a citation-less query (which routes ``ROUTE_UNRESOLVED``) into either candidate
suggestions (:class:`Suggest`) or a negative-evidence refusal (:class:`NoEvidence`),
or refuses out-of-scope queries (case law, foreign law, personal advice) before discovery.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Final, TypeAlias

from legal_rag_router import Coordinate, Router
from legal_rag_router.concepts import terms

from chapterverse.citations import instrument_name, provision_label
from chapterverse.contracts import (
    ClarifyEvent,
    LabelledCoordinate,
    RefusalEvent,
)
from chapterverse.templates import (
    out_of_scope_refusal,
    plain_text,
)

__all__ = [
    "DiscoveryOutcome",
    "NoEvidence",
    "Suggest",
    "check_out_of_scope",
    "discover_step",
    "format_candidate_label",
]

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
    limit: int = 8,
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
