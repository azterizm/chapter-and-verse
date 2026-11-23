"""Deterministic user-facing wording and the events that carry it. No LLM is involved.

Covers every refusal kind (:data:`~chapterverse.contracts.REFUSAL_KINDS`), the clarifying
question, the notices, the extractive banners, and two standing notices. Wording rules: cold,
plain, short, facts only; ASCII punctuation only (no em or en dashes, arrows or curly quotes).
Text quoted from the router (its ``messages``, candidate labels) passes through
:func:`plain_text`, which applies the same punctuation rule. Provision text is never altered.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Final, Literal

from legal_rag_router import Candidate, Coordinate, Router, RouteResult, RouteStatus

from chapterverse.citations import provision_label
from chapterverse.contracts import (
    REFUSAL_KINDS,
    ClarifyEvent,
    ExtractiveReason,
    LabelledCoordinate,
    NoticeEvent,
    RefusalEvent,
    TemporalStatus,
    TextStore,
)
from chapterverse.status import StatusDecision

__all__ = [
    "CLARIFY_DEFAULT",
    "CLARIFY_RELATED_PROVISIONS",
    "CLARIFY_WHICH_ACT",
    "DAILY_CAP_MESSAGE",
    "DISCLAIMER_NOTICE",
    "EXTRACTIVE_BANNERS",
    "GLOBAL_BUDGET_MESSAGE",
    "INCOMPLETE_MESSAGE",
    "NOTICE_CODES",
    "OUT_OF_SCOPE_REASONS",
    "PRIVACY_NOTICE",
    "PRIVATE_DOCUMENTS_MESSAGE",
    "REFUSAL_MESSAGES",
    "OutOfScopeReason",
    "clarify_event",
    "corrections_notice",
    "daily_cap_notice",
    "extractive_banner",
    "format_date",
    "global_budget_notice",
    "out_of_scope_refusal",
    "plain_text",
    "private_document_refusal",
    "provider_fallback_notice",
    "provider_warming_notice",
    "providers_unavailable_notice",
    "refusal",
    "route_refusal",
    "status_refusal",
]

DISCLAIMER_NOTICE: Final = "Chapter & Verse gives information about legislation, not legal advice."
"""Shown with every answer (Groq Services Agreement section 4.3; decision 0001 item 10)."""

PRIVACY_NOTICE: Final = "Do not enter client details or any other personal data."
"""Shown by the question box."""

CLARIFY_DEFAULT: Final = "Which of these do you mean?"
CLARIFY_RELATED_PROVISIONS: Final = "Which Act or SI do you mean? These provisions may be related."
CLARIFY_WHICH_ACT: Final = "Which Act or SI do you mean?"

PRIVATE_DOCUMENTS_MESSAGE: Final = (
    "Chapter & Verse covers UK legislation only. It cannot read private documents such as "
    "leases, contracts or policies. Ask about the law that applies, or name an Act or SI "
    "and a section."
)

OutOfScopeReason = Literal[
    "case_law",
    "named_person",
    "non_uk_law",
    "personal_advice",
    "private_document",
    "system_search",
]
OUT_OF_SCOPE_REASONS: Final[tuple[OutOfScopeReason, ...]] = (
    "case_law",
    "named_person",
    "non_uk_law",
    "personal_advice",
    "private_document",
    "system_search",
)

_OUT_OF_SCOPE_LEADS: Final[dict[OutOfScopeReason, str]] = {
    "case_law": "Case law is not covered.",
    "named_person": "Information about individual people is not given.",
    "non_uk_law": "Only UK legislation is covered.",
    "personal_advice": "Advice on a personal situation is not given.",
    "system_search": (
        "Only UK legislation is searched; connected systems, stores and repositories are not."
    ),
}

REFUSAL_MESSAGES: Final[Mapping[str, str]] = {
    RouteStatus.INSTRUMENT_NOT_FOUND.value: (
        "The cited legislation was not found. No answer is given."
    ),
    RouteStatus.PROVISION_NOT_FOUND.value: (
        "The cited provision was not found in that legislation. No answer is given."
    ),
    RouteStatus.OUT_OF_COVERAGE.value: (
        "The cited legislation is outside the legislation held here. No answer is given."
    ),
    RouteStatus.UNRESOLVED.value: (
        "No Act or SI was identified in the question. Name the Act or SI and the section, "
        "for example s.124 Employment Rights Act 1996."
    ),
    TemporalStatus.NOT_YET_IN_FORCE.value: (
        "{subject} is not yet in force. No answer is given from its text."
    ),
    TemporalStatus.REPEALED.value: "{subject} has been repealed. No answer is given from it.",
    TemporalStatus.TEXT_NOT_HELD.value: (
        "Only the title of {subject} is held here, not its text. Read it on legislation.gov.uk."
    ),
    TemporalStatus.HISTORY_NOT_HELD.value: (
        "Only the version of {subject} as at {version_date} is held here. "
        "The law on {as_at} cannot be stated. Read that version on legislation.gov.uk."
    ),
    "out_of_scope": (
        "This service covers UK legislation only. It does not cover case law, the law of other "
        "countries, or advice on a personal situation."
    ),
}
"""The base message for every refusal kind. Temporal ones take ``{subject}`` (a citation
label); ``HISTORY_NOT_HELD`` also takes ``{version_date}`` and ``{as_at}``."""

_HISTORY_DATE_UNKNOWN: Final = (
    "The date of the version of {subject} held here is not known. "
    "The law on {as_at} cannot be stated. Read that version on legislation.gov.uk."
)
_PARTLY: Final[Mapping[str, str]] = {
    "provision_repealed": "{subject} has been repealed. No answer is given from it.",
    "text_repealed": "All the text of {subject} has been repealed. No answer is given from it.",
    "instrument_repealed": "{subject} has been repealed. No answer is given from it.",
    "text_prospective": (
        "None of the text of {subject} is in force yet. No answer is given from it."
    ),
}

EXTRACTIVE_BANNERS: Final[Mapping[ExtractiveReason, str]] = {
    "providers_unavailable": (
        "No answer could be written because no model is available. The provision text is "
        "shown, with the part matching the question highlighted."
    ),
    "provider_warming": (
        "The model is starting up. The provision text is shown, with the part matching the "
        "question highlighted. Ask again in a few minutes for a written answer."
    ),
}

DAILY_CAP_MESSAGE: Final = (
    "Daily limit reached for generated answers. The provision text is shown instead. "
    "It resets at 00:00 UTC."
)

GLOBAL_BUDGET_MESSAGE: Final = (
    "Today's generated answers are used up. The provision text is shown instead. "
    "It resets at 00:00 UTC."
)

INCOMPLETE_MESSAGE: Final = (
    "The model became unavailable while writing. Every sentence shown was checked; "
    "there may have been more to say."
)

NOTICE_CODES: Final = (
    "provider_fallback",
    "provider_warming",
    "corrections_applied",
    "daily_cap",
    "global_budget",
    "providers_unavailable",
)

_MONTHS: Final = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)  # fmt: skip

# Built from code points so the source itself stays plain ASCII.
_PUNCTUATION: Final = str.maketrans(
    dict.fromkeys(map(chr, (0x2018, 0x2019, 0x201A, 0x201B, 0x2032)), "'")
    | dict.fromkeys(map(chr, (0x201C, 0x201D, 0x201E, 0x201F, 0x2033)), '"')
    | dict.fromkeys(map(chr, (0x2010, 0x2011, 0x2012, 0x2212)), "-")
    | {chr(0x2026): "...", chr(0x00A0): " "}
)
_DASHES: Final = "".join(map(chr, (0x2013, 0x2014, 0x2015)))
_SPACED_DASH: Final = re.compile(rf"\s*[{_DASHES}]\s+|\s+[{_DASHES}]\s*")
_DASH: Final = re.compile(rf"[{_DASHES}]")
_ARROW: Final = re.compile(rf"\s*[{chr(0x2190)}-{chr(0x21FF)}{chr(0x27F5)}-{chr(0x27FF)}]\s*")


def plain_text(text: str) -> str:
    """Apply the wording rule to quoted text: straight quotes, no dashes or arrows.

    A spaced em or en dash becomes ``, ``; an unspaced one (a range such as 124 to 126) a hyphen;
    an arrow ``to``.
    """
    out = _SPACED_DASH.sub(", ", text.translate(_PUNCTUATION))
    out = _ARROW.sub(" to ", _DASH.sub("-", out))
    return " ".join(out.split())


def format_date(value: date | str | None) -> str:
    """``2026-07-29`` reads ``29 July 2026``; an unparsable string is returned as given."""
    if value is None:
        return "an unknown date"
    if isinstance(value, str):
        try:
            day = date.fromisoformat(value[:10])
        except ValueError:
            return value
    else:
        day = value
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _labelled(candidates: Iterable[Candidate]) -> tuple[LabelledCoordinate, ...]:
    return tuple(
        LabelledCoordinate(coordinate=str(c.coordinate), label=plain_text(c.label))
        for c in candidates
    )


def refusal(
    kind: str,
    *,
    snapshot: str,
    subject: str = "This provision",
    extra: Sequence[str] = (),
    suggestions: Sequence[LabelledCoordinate] = (),
    link: str | None = None,
    version_date: str | None = None,
    as_at: date | None = None,
) -> RefusalEvent:
    """The refusal for any kind in ``REFUSAL_KINDS``: the base message, then ``extra`` lines.

    Raises:
        ValueError: ``kind`` is not a refusal kind.
    """
    if kind not in REFUSAL_KINDS:
        raise ValueError(f"{kind!r} is not a refusal kind")
    message = REFUSAL_MESSAGES[kind].format(
        subject=subject, version_date=format_date(version_date), as_at=format_date(as_at)
    )
    lines = [message, *(plain_text(x) for x in extra if x.strip())]
    return RefusalEvent(
        kind=kind,
        message=" ".join(lines),
        suggestions=tuple(suggestions),
        snapshot=snapshot,
        link=link,
    )


def route_refusal(result: RouteResult) -> RefusalEvent:
    """The refusal for a router outcome that does not answer.

    Quotes the router's ``messages`` after the base message, and carries its ``suggestions``
    and ``index_snapshot``. ``ROUTE_UNRESOLVED`` asks for the Act or SI and the section
    (Slice 1 has no discovery).

    Raises:
        ValueError: the result is bound or ambiguous (not a refusal).
    """
    if result.status in (RouteStatus.BOUNDED, RouteStatus.AMBIGUOUS):
        raise ValueError(f"{result.status.value} is not a refusal")
    return refusal(
        result.status.value,
        snapshot=result.index_snapshot,
        extra=result.messages,
        suggestions=_labelled(result.suggestions),
    )


def status_refusal(decision: StatusDecision, *, subject: str, snapshot: str) -> RefusalEvent:
    """The refusal for a temporal status other than ``IN_FORCE``.

    ``subject`` is the citation label of the bound coordinate (``s.4 European Union
    (Amendment) Act 2008``). ``TEXT_NOT_HELD`` and ``HISTORY_NOT_HELD`` carry the decision's
    link; the others carry none.

    Raises:
        ValueError: the decision is ``IN_FORCE``.
    """
    status = decision.status
    if status is TemporalStatus.IN_FORCE:
        raise ValueError("IN_FORCE is not a refusal")
    linked = status in (TemporalStatus.TEXT_NOT_HELD, TemporalStatus.HISTORY_NOT_HELD)
    if decision.reason == "version_date_unknown":
        template = _HISTORY_DATE_UNKNOWN
    else:
        template = _PARTLY.get(decision.reason, REFUSAL_MESSAGES[status.value])
    message = template.format(
        subject=plain_text(subject),
        version_date=format_date(decision.version_date),
        as_at=format_date(decision.as_at),
    )
    return RefusalEvent(
        kind=status.value,
        message=message,
        suggestions=(),
        snapshot=snapshot,
        link=decision.link if linked else None,
    )


def private_document_refusal(snapshot: str) -> RefusalEvent:
    """The ``out_of_scope`` refusal for private documents (leases, contracts, policies)."""
    return RefusalEvent(
        kind="out_of_scope",
        message=PRIVATE_DOCUMENTS_MESSAGE,
        suggestions=(),
        snapshot=snapshot,
        link=None,
    )


def out_of_scope_refusal(snapshot: str, reason: OutOfScopeReason | None = None) -> RefusalEvent:
    """The ``out_of_scope`` refusal: case law, non-UK law, personal advice, private documents,
    system search, or named persons.
    """
    if reason == "private_document":
        return private_document_refusal(snapshot)
    base = REFUSAL_MESSAGES["out_of_scope"]
    message = f"{_OUT_OF_SCOPE_LEADS[reason]} {base}" if reason is not None else base
    return RefusalEvent(kind="out_of_scope", message=message, suggestions=(), snapshot=snapshot)


def _format_candidate_label(base_label: str, coord: str) -> str:
    head = provision_label(coord)
    base = plain_text(base_label)
    if not head:
        return base
    if base.startswith(head):
        return base
    if base.endswith(f" {head}"):
        base = base[: -len(head) - 1].strip()
    elif head.startswith("s.") and base.lower().endswith(f" section {head[2:]}"):
        base = base[: -len(f" section {head[2:]}")].strip()
    return f"{head} {base}"


def _resolve_candidate_coordinate(
    candidate_coord: str,
    provision: str | None,
    router: Router | None,
    store: TextStore | None,
) -> str:
    parsed = Coordinate.parse(candidate_coord)
    if parsed.provision:
        return candidate_coord
    if not provision:
        return candidate_coord

    if router is not None:
        try:
            res = router.route(f"{candidate_coord} {provision}")
        except (KeyError, ValueError):
            pass
        else:
            if res.status is RouteStatus.BOUNDED and res.coordinates:
                return str(res.coordinates[0])

    if store is not None:
        m = re.match(r"^(?:s\.?|section\s*)(\d+[a-z]*)$", provision.strip(), re.IGNORECASE)
        if m:
            prov_coord = f"{candidate_coord}/s{m.group(1)}"
            try:
                store.get(prov_coord)
            except KeyError:
                pass
            else:
                return prov_coord

    return candidate_coord


def clarify_event(
    result: RouteResult,
    *,
    router: Router | None = None,
    store: TextStore | None = None,
) -> ClarifyEvent:
    """The clarifying question for ``ROUTE_AMBIGUOUS``: the router's own question and
    candidates; never a silent pick.

    When the query names a provision, each candidate carries the coordinate of that provision
    inside the candidate instrument when that provision exists in that instrument (requirements R1).

    Raises:
        ValueError: the result is not ambiguous.
    """
    if result.status is not RouteStatus.AMBIGUOUS:
        raise ValueError(f"{result.status.value} is not ambiguous")
    question = plain_text(result.clarification or "") or CLARIFY_DEFAULT

    provision: str | None = None
    if hasattr(result, "citation") and getattr(result.citation, "provision", None):
        provision = result.citation.provision
    elif result.citations:
        for cit in result.citations:
            if cit.provision:
                provision = cit.provision
                break

    candidates: list[LabelledCoordinate] = []
    for c in result.candidates:
        coord = _resolve_candidate_coordinate(str(c.coordinate), provision, router, store)
        label = _format_candidate_label(c.label, coord)
        candidates.append(LabelledCoordinate(coordinate=coord, label=label))

    return ClarifyEvent(question=question, candidates=tuple(candidates))


def providers_unavailable_notice() -> NoticeEvent:
    """The model became unavailable after at least one sentence was written."""
    return NoticeEvent(code="providers_unavailable", message=INCOMPLETE_MESSAGE)


def provider_fallback_notice() -> NoticeEvent:
    """A provider later in the rotation served the request."""
    return NoticeEvent(
        code="provider_fallback",
        message="The first model provider was unavailable. A backup provider wrote this answer.",
    )


def provider_warming_notice() -> NoticeEvent:
    """Modal is cold-starting."""
    return NoticeEvent(
        code="provider_warming",
        message="The backup model is starting up. Ask again in a few minutes for a written answer.",
    )


def daily_cap_notice() -> NoticeEvent:
    """The client reached the daily cap for generated answers."""
    return NoticeEvent(code="daily_cap", message=DAILY_CAP_MESSAGE)


def global_budget_notice() -> NoticeEvent:
    """The daily limit for generated answers across all users was reached."""
    return NoticeEvent(code="global_budget", message=GLOBAL_BUDGET_MESSAGE)


def corrections_notice(corrections: Sequence[tuple[str, str]]) -> NoticeEvent | None:
    """The spelling corrections applied to the question, or None when there were none.

    ``[("rihgts", "rights")]`` reads ``Spelling corrected: "rihgts" read as "rights".``
    """
    pairs = [(plain_text(a), plain_text(b)) for a, b in corrections if a != b]
    if not pairs:
        return None
    body = "; ".join(f'"{a}" read as "{b}"' for a, b in pairs)
    return NoticeEvent(code="corrections_applied", message=f"Spelling corrected: {body}.")


def extractive_banner(reason: ExtractiveReason) -> str:
    """The banner shown above an extractive answer."""
    return EXTRACTIVE_BANNERS[reason]
