"""Deterministic wording and events: refusals, clarify, notices, banners."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest
from legal_rag_router import (
    Candidate,
    Coordinate,
    NextAction,
    ParsedCitation,
    Router,
    RouteResult,
    RouteStatus,
)

from chapterverse.contracts import (
    REFUSAL_KINDS,
    ClarifyEvent,
    LabelledCoordinate,
    NoticeEvent,
    RefusalEvent,
    TemporalStatus,
    encode_sse,
)
from chapterverse.status import StatusDecision, decide_status
from chapterverse.templates import (
    CLARIFY_DEFAULT,
    DAILY_CAP_MESSAGE,
    DISCLAIMER_NOTICE,
    EXTRACTIVE_BANNERS,
    GLOBAL_BUDGET_MESSAGE,
    INCOMPLETE_MESSAGE,
    NOTICE_CODES,
    OUT_OF_SCOPE_REASONS,
    PRIVACY_NOTICE,
    REFUSAL_MESSAGES,
    clarify_event,
    corrections_notice,
    daily_cap_notice,
    extractive_banner,
    format_date,
    global_budget_notice,
    out_of_scope_refusal,
    plain_text,
    provider_fallback_notice,
    provider_warming_notice,
    providers_unavailable_notice,
    quote_truncated_notice,
    refusal,
    route_refusal,
    status_refusal,
)

SNAPSHOT = "2026-09-28"
# Em and en dashes, horizontal bar, arrows, curly quotes, Markdown bold.
_FORBIDDEN = re.compile(
    "[\u2013\u2014\u2015\u2190-\u21ff\u27f5-\u27ff\u2018\u2019\u201c\u201d]|\\*\\*"
)


def _plain(text: str) -> bool:
    return _FORBIDDEN.search(text) is None


def _all_wording() -> list[str]:
    out = [DISCLAIMER_NOTICE, PRIVACY_NOTICE, CLARIFY_DEFAULT]
    out += list(REFUSAL_MESSAGES.values()) + list(EXTRACTIVE_BANNERS.values())
    out += [
        provider_fallback_notice().message,
        provider_warming_notice().message,
        daily_cap_notice().message,
        global_budget_notice().message,
        providers_unavailable_notice().message,
        quote_truncated_notice(8, 12).message,
    ]
    out += [out_of_scope_refusal(SNAPSHOT, r).message for r in OUT_OF_SCOPE_REASONS]
    return out


def test_wording_is_plain() -> None:
    for text in _all_wording():
        assert _plain(text), text
        assert text.isascii() or "&" in text, text
        assert text == text.strip()


def test_standing_notices() -> None:
    assert "information about legislation, not legal advice" in DISCLAIMER_NOTICE
    assert "personal data" in PRIVACY_NOTICE
    assert "client" in PRIVACY_NOTICE


# ---------------------------------------------------------------------------- refusals


def test_every_refusal_kind_has_a_message() -> None:
    assert set(REFUSAL_MESSAGES) == set(REFUSAL_KINDS)
    for kind in sorted(REFUSAL_KINDS):
        event = refusal(kind, snapshot=SNAPSHOT, subject="s.1 Example Act 2000")
        assert isinstance(event, RefusalEvent)
        assert event.kind == kind
        assert event.snapshot == SNAPSHOT
        assert "{" not in event.message
        assert _plain(event.message)
        assert encode_sse(event).startswith(b"event: refusal\n")
    with pytest.raises(ValueError, match="not a refusal kind"):
        refusal("ROUTE_BOUNDED", snapshot=SNAPSHOT)


def test_invented_act_quotes_the_router(fixture_router: Router) -> None:
    result = fixture_router.route("Marchwood Commercial Arbitration Act 1996")
    event = route_refusal(result)
    assert event.kind == RouteStatus.INSTRUMENT_NOT_FOUND.value
    assert event.snapshot == result.index_snapshot == SNAPSHOT
    assert event.link is None
    assert event.message.startswith(REFUSAL_MESSAGES[event.kind])
    quoted = plain_text(result.messages[0])
    assert quoted in event.message
    assert '"Marchwood Commercial Arbitration Act 1996"' in event.message
    assert _plain(event.message)


def test_missing_provision_and_foreign_law(fixture_router: Router) -> None:
    missing = route_refusal(fixture_router.route("ERA 1996 s.99"))
    assert missing.kind == RouteStatus.PROVISION_NOT_FOUND.value
    assert "Employment Rights Act 1996 has no s.99" in missing.message
    foreign = route_refusal(fixture_router.route("Code civil article 1240"))
    assert foreign.kind == RouteStatus.OUT_OF_COVERAGE.value
    assert '"Code civil" is another legal system' in foreign.message


def test_unresolved_asks_for_the_act_and_section(fixture_router: Router) -> None:
    event = route_refusal(fixture_router.route("unfair dismissal compensation cap"))
    assert event.kind == RouteStatus.UNRESOLVED.value
    assert "Name the Act or SI and the section" in event.message
    assert event.suggestions == ()


def test_router_suggestions_are_carried() -> None:
    result = RouteResult(
        status=RouteStatus.INSTRUMENT_NOT_FOUND,
        next_action=NextAction.REFUSE,
        suggestions=(
            Candidate(Coordinate.parse("uk/ukpga/1996/23"), "Arbitration Act 1996"),
            Candidate(Coordinate.parse("uk/ukpga/1996/18"), "Employment Rights Act 1996"),
        ),
        index_snapshot=SNAPSHOT,
        messages=("No instrument \u201cX Act 1996\u201d exists \u2014 see below.",),
    )
    event = route_refusal(result)
    assert event.suggestions == (
        LabelledCoordinate("uk/ukpga/1996/23", "Arbitration Act 1996"),
        LabelledCoordinate("uk/ukpga/1996/18", "Employment Rights Act 1996"),
    )
    assert event.message.endswith('No instrument "X Act 1996" exists, see below.')


def test_bound_and_ambiguous_are_not_refusals(fixture_router: Router) -> None:
    for query in ("s.124 ERA 1996", "Emplyment Rihgts Act 1996"):
        with pytest.raises(ValueError, match="not a refusal"):
            route_refusal(fixture_router.route(query))


# ---------------------------------------------------------------------------- temporal


def test_text_not_held(fixture_text_store: Any) -> None:
    decision = decide_status(fixture_text_store.get("uk/ukpga/1980/15"))
    event = status_refusal(
        decision,
        subject="National Health Service (Invalid Direction) Act 1980",
        snapshot=SNAPSHOT,
    )
    assert event.kind == TemporalStatus.TEXT_NOT_HELD.value
    assert event.link == "https://www.legislation.gov.uk/ukpga/1980/15"
    assert event.message == (
        "Only the title of National Health Service (Invalid Direction) Act 1980 is held here, "
        "not its text. Read it on legislation.gov.uk."
    )


def test_history_not_held(fixture_text_store: Any) -> None:
    decision = decide_status(
        fixture_text_store.get("uk/ukpga/1996/18/s124"), as_at=date(2020, 4, 6)
    )
    event = status_refusal(decision, subject="s.124 Employment Rights Act 1996", snapshot=SNAPSHOT)
    assert event.kind == TemporalStatus.HISTORY_NOT_HELD.value
    assert event.link == "https://www.legislation.gov.uk/ukpga/1996/18/section/124/2020-04-06"
    assert event.message == (
        "Only the version of s.124 Employment Rights Act 1996 as at 29 July 2026 is held here. "
        "The law on 6 April 2020 cannot be stated. Read that version on legislation.gov.uk."
    )
    unknown = StatusDecision(
        "uk/uksi/2011/3006",
        TemporalStatus.HISTORY_NOT_HELD,
        "version_date_unknown",
        "https://www.legislation.gov.uk/uksi/2011/3006/2020-01-01",
        None,
        date(2020, 1, 1),
    )
    message = status_refusal(unknown, subject="SI 2011/3006", snapshot=SNAPSHOT).message
    assert message.startswith("The date of the version of SI 2011/3006 held here is not known.")


def test_repealed_and_prospective(fixture_text_store: Any) -> None:
    repealed = status_refusal(
        decide_status(fixture_text_store.get("uk/ukpga/2008/7/s4")),
        subject="s.4 European Union (Amendment) Act 2008",
        snapshot=SNAPSHOT,
    )
    assert repealed.kind == TemporalStatus.REPEALED.value
    assert repealed.link is None
    assert repealed.message == (
        "s.4 European Union (Amendment) Act 2008 has been repealed. No answer is given from it."
    )
    prospective = status_refusal(
        decide_status(fixture_text_store.get("uk/ukpga/2018/11/s1")),
        subject="s.1 Secure Tenancies (Victims of Domestic Abuse) Act 2018",
        snapshot=SNAPSHOT,
    )
    assert prospective.kind == TemporalStatus.NOT_YET_IN_FORCE.value
    assert prospective.link is None
    assert "is not yet in force" in prospective.message
    with pytest.raises(ValueError, match="IN_FORCE"):
        status_refusal(
            decide_status(fixture_text_store.get("uk/ukpga/2018/11/s2")),
            subject="s.2",
            snapshot=SNAPSHOT,
        )


def test_out_of_scope() -> None:
    general = out_of_scope_refusal(SNAPSHOT)
    assert general.kind == "out_of_scope"
    assert "case law" in general.message
    assert "other countries" in general.message
    assert "personal situation" in general.message
    assert out_of_scope_refusal(SNAPSHOT, "case_law").message.startswith("Case law is not covered.")
    assert out_of_scope_refusal(SNAPSHOT, "non_uk_law").message.startswith("Only UK legislation")
    assert out_of_scope_refusal(SNAPSHOT, "personal_advice").message.startswith("Advice on")


# ---------------------------------------------------------------------------- clarify, notices


def test_clarify_from_the_router(fixture_router: Router) -> None:
    event = clarify_event(fixture_router.route("Emplyment Rihgts Act 1996"))
    assert isinstance(event, ClarifyEvent)
    assert event.question == "Did you mean the Employment Rights Act 1996?"
    assert event.candidates == (
        LabelledCoordinate("uk/ukpga/1996/18", "Employment Rights Act 1996"),
    )
    with pytest.raises(ValueError, match="not ambiguous"):
        clarify_event(fixture_router.route("s.124 ERA 1996"))
    bare = RouteResult(status=RouteStatus.AMBIGUOUS, next_action=NextAction.ASK_USER)
    assert clarify_event(bare).question == CLARIFY_DEFAULT


def test_clarify_keeps_provision_when_present(fixture_router: Router) -> None:
    # R1.1, R1.2: when ambiguous query names a provision, each candidate carries the
    # coordinate of that provision inside candidate instrument when it exists;
    # otherwise instrument coordinate. Label names the provision.
    result = RouteResult(
        status=RouteStatus.AMBIGUOUS,
        next_action=NextAction.ASK_USER,
        candidates=(
            Candidate(Coordinate.parse("uk/ukpga/1996/18"), "Employment Rights Act 1996"),
            Candidate(
                Coordinate.parse("uk/ukpga/1980/15"),
                "National Health Service (Invalid Direction) Act 1980",
            ),
        ),
        citations=(
            ParsedCitation(
                span=(0, 33),
                jurisdiction_hint="uk",
                instrument_type="act",
                title_as_cited="Employment Rights Act",
                year=None,
                number=None,
                provision="s.124",
                resolution="resolved",
                live_checkable=False,
            ),
        ),
        index_snapshot=SNAPSHOT,
    )
    event = clarify_event(result, router=fixture_router)
    assert event.candidates == (
        LabelledCoordinate("uk/ukpga/1996/18/s124", "s.124 Employment Rights Act 1996"),
        LabelledCoordinate(
            "uk/ukpga/1980/15", "National Health Service (Invalid Direction) Act 1980"
        ),
    )


def test_notices() -> None:
    assert provider_fallback_notice().code == "provider_fallback"
    assert provider_warming_notice().code == "provider_warming"
    incomplete = providers_unavailable_notice()
    assert incomplete.code == "providers_unavailable"
    assert (
        incomplete.message
        == INCOMPLETE_MESSAGE
        == (
            "The model became unavailable while writing. Every sentence shown was checked; "
            "there may have been more to say."
        )
    )
    event = corrections_notice([("rihgts", "rights"), ("emplyment", "employment")])
    assert isinstance(event, NoticeEvent)
    assert event.code == "corrections_applied"
    assert event.message == (
        'Spelling corrected: "rihgts" read as "rights"; "emplyment" read as "employment".'
    )
    assert corrections_notice([]) is None
    assert corrections_notice([("a", "a")]) is None
    daily = daily_cap_notice()
    assert daily.code == "daily_cap"
    assert (
        daily.message
        == DAILY_CAP_MESSAGE
        == (
            "Daily limit reached for generated answers. The provision text is shown instead. "
            "It resets at 00:00 UTC."
        )
    )
    budget = global_budget_notice()
    assert budget.code == "global_budget"
    assert (
        budget.message
        == GLOBAL_BUDGET_MESSAGE
        == (
            "Today's generated answers are used up. The provision text is shown instead. "
            "It resets at 00:00 UTC."
        )
    )
    truncated = quote_truncated_notice(8, 12)
    assert truncated.code == "quote_truncated"
    assert "8 of 12" in truncated.message
    assert "legislation.gov.uk" in truncated.message
    codes = {
        provider_fallback_notice().code,
        provider_warming_notice().code,
        incomplete.code,
        event.code,
        daily.code,
        budget.code,
        truncated.code,
    }
    assert codes == set(NOTICE_CODES)


def test_extractive_banners() -> None:
    assert set(EXTRACTIVE_BANNERS) == {
        "providers_unavailable",
        "provider_warming",
        "quote_requested",
    }
    assert "no model is available" in extractive_banner("providers_unavailable")
    assert "starting up" in extractive_banner("provider_warming")
    assert extractive_banner("quote_requested") == "Statutory text, quoted from legislation.gov.uk."


def test_out_of_scope_new_reasons() -> None:
    assert "system_search" in OUT_OF_SCOPE_REASONS
    assert "named_person" in OUT_OF_SCOPE_REASONS

    ref_sys = out_of_scope_refusal(SNAPSHOT, "system_search")
    assert ref_sys.kind == "out_of_scope"
    assert (
        ref_sys.message
        == "Only UK legislation is searched; connected systems, stores and repositories are not. "
        + REFUSAL_MESSAGES["out_of_scope"]
    )
    assert _plain(ref_sys.message)

    ref_person = out_of_scope_refusal(SNAPSHOT, "named_person")
    assert ref_person.kind == "out_of_scope"
    assert (
        ref_person.message
        == "Information about individual people is not given. " + REFUSAL_MESSAGES["out_of_scope"]
    )
    assert _plain(ref_person.message)


def test_helpers() -> None:
    assert format_date("2026-07-29") == "29 July 2026"
    assert format_date(date(2020, 4, 6)) == "6 April 2020"
    assert format_date(None) == "an unknown date"
    assert format_date("soon") == "soon"
    assert plain_text("a \u2014 b\u2013c \u2192 d \u2018e\u2019") == "a, b-c to d 'e'"
