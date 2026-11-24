"""The per-request pipeline (agent C1): every route, the premise cap, failures and the receipt.

The router and text store are the fixture ones (tests/conftest.py); the rotation and judge are
:class:`test_pipeline_fakes.FakeRotation`. Every stream is checked with ``validate_order``.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from legal_rag_router import Router
from legal_rag_router.coordinate import Coordinate
from legal_rag_router.result import Candidate, NextAction, ParsedCitation, RouteResult, RouteStatus

from chapterverse.audit.policy import build_policy
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ClarifyEvent,
    Event,
    ExtractiveEvent,
    NoticeEvent,
    ProviderCall,
    ReceiptEvent,
    RefusalEvent,
    SentenceEvent,
    SentenceOut,
    SourcesEvent,
    TemporalStatus,
    TextStore,
    Window,
    validate_order,
)
from chapterverse.pipeline import (
    AskRequest,
    ModalKV,
    Outcome,
    Pipeline,
    PipelineConfig,
    StreamClosed,
    hint_as_at,
    merge_calls,
    select_windows,
)
from chapterverse.providers import ProviderRotation, ProviderWarming, make_cloudflare, make_groq
from chapterverse.providers.presets import JUDGE_PRESET_NAME
from chapterverse.ratelimit import DailyBudgetCounts, InMemoryDailyBudget, InMemoryDailyCap
from chapterverse.templates import clarify_event

if TYPE_CHECKING:  # mypy sees tests/ as the package "tests"; pytest puts tests/ on sys.path
    from tests.test_pipeline_fakes import (
        AMBIGUOUS_QUERY,
        CITED,
        GOOD1,
        GOOD2,
        MARCHWOOD_QUERY,
        S124_QUERY,
        TITLE_ONLY_QUERY,
        CharBackend,
        FakeRotation,
        unavailable,
    )
else:
    from test_pipeline_fakes import (
        AMBIGUOUS_QUERY,
        CITED,
        GOOD1,
        GOOD2,
        MARCHWOOD_QUERY,
        S124_QUERY,
        TITLE_ONLY_QUERY,
        CharBackend,
        FakeRotation,
        unavailable,
    )

POLICY = build_policy(JUDGE_PRESET_NAME)
VERSION_DATE = "2026-07-29"


class CountingStore:
    """A ``TextStore`` proxy that counts body reads."""

    def __init__(self, inner: TextStore) -> None:
        self.inner = inner
        self.gets: list[str] = []
        self.heads: list[str] = []

    @property
    def corpus_id(self) -> str:
        return self.inner.corpus_id

    @property
    def snapshot(self) -> str:
        return self.inner.snapshot

    def get(self, coordinate: str) -> list[dict[str, Any]]:
        self.gets.append(coordinate)
        return self.inner.get(coordinate)

    def instrument(self, coordinate: str) -> dict[str, Any] | None:
        self.heads.append(coordinate)
        return self.inner.instrument(coordinate)

    def has_text(self, instrument_coordinate: str) -> bool:
        return self.inner.has_text(instrument_coordinate)


@pytest.fixture
def store(fixture_text_store: TextStore) -> CountingStore:
    return CountingStore(fixture_text_store)


def make(router: Router, store: TextStore, rotation: Any, **kwargs: Any) -> Pipeline:
    return Pipeline(router=router, store=store, rotation=rotation, policy=POLICY, **kwargs)


def run(pipeline: Pipeline, query: str, **kwargs: Any) -> tuple[list[Event], Outcome]:
    events: list[Event] = []
    outcome = pipeline.run(AskRequest(query=query, **kwargs), events.append)
    types = [e.type for e in events]
    assert validate_order(types), types
    return events, outcome


def of(events: list[Event], kind: type[Any]) -> list[Any]:
    return [e for e in events if isinstance(e, kind)]


def receipt(events: list[Event]) -> ReceiptEvent:
    (found,) = of(events, ReceiptEvent)
    assert isinstance(found, ReceiptEvent)
    assert found.answer_id == found.fact.answer_id
    return found


# ---------------------------------------------------------------------- the Slice 1 cases


def test_s124_answered_with_verified_cited_sentences(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")])
    events, outcome = run(make(fixture_router, store, rotation), S124_QUERY)
    assert [e.type for e in events] == ["route", "sources", "sentence", "sentence", "receipt",
                                        "done"]  # fmt: skip
    sentences = of(events, SentenceEvent)
    assert [s.sentence.verdict for s in sentences] == ["supported", "supported"]
    assert sentences[0].sentence.passage_ids == ("P2",)
    (cite,) = sentences[0].citations
    assert cite.label == "s.124(1ZA) Employment Rights Act 1996"
    assert cite.url == "https://www.legislation.gov.uk/ukpga/1996/18/section/124/1ZA"
    assert "[P2]" not in sentences[0].sentence.text
    fact = receipt(events).fact
    assert fact.mode == "generated"
    assert fact.coordinates == ("uk/ukpga/1996/18/s124",)
    assert fact.passages["P2"] == "uk/ukpga/1996/18/s124/1ZA"
    assert fact.version_dates == {"uk/ukpga/1996/18": VERSION_DATE}
    assert fact.corpus_id == "fixture"
    assert fact.policy_hash == POLICY.policy_hash
    # One generation call and two judge calls, judge records from RotationJudgeChat.
    assert [(c.provider, c.purpose) for c in fact.calls] == [
        ("groq", "generate"),
        ("groq", "judge"),
        ("groq", "judge"),
    ]
    assert all(c.provider != "unknown" for c in fact.calls)
    judge = [c for c in fact.calls if c.purpose == "judge"]
    assert {c.model_id for c in judge} == {"judge-model"}
    assert outcome.first_source_ms is not None
    assert outcome.first_sentence_ms is not None
    assert outcome.first_source_ms <= outcome.first_sentence_ms
    assert store.gets == ["uk/ukpga/1996/18/s124"]


def test_invented_act_refused(fixture_router: Router, store: CountingStore) -> None:
    rotation = FakeRotation()
    events, _ = run(make(fixture_router, store, rotation), MARCHWOOD_QUERY)
    assert [e.type for e in events] == ["route", "refusal", "receipt", "done"]
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"
    assert "Marchwood" in refusal.message
    assert refusal.snapshot == "2026-09-28"
    assert receipt(events).fact.mode == "refusal"
    assert not rotation.requests
    assert not store.gets


def test_title_only_act_is_text_not_held_with_its_link(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation()
    events, _ = run(make(fixture_router, store, rotation), TITLE_ONLY_QUERY)
    assert [e.type for e in events] == ["route", "refusal", "receipt", "done"]
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == "TEXT_NOT_HELD"
    assert refusal.link == "https://www.legislation.gov.uk/ukpga/1980/15"
    fact = receipt(events).fact
    assert fact.coordinates == ("uk/ukpga/1980/15",)
    assert "uk/ukpga/1980/15" in fact.version_dates
    assert store.heads == ["uk/ukpga/1980/15"]
    assert not store.gets  # refused from the instrument record alone
    assert not rotation.requests


def test_ambiguous_citation_asks(fixture_router: Router, store: CountingStore) -> None:
    events, outcome = run(make(fixture_router, store, FakeRotation()), AMBIGUOUS_QUERY)
    assert [e.type for e in events] == ["route", "clarify", "done"]
    clarify = events[1]
    assert clarify.type == "clarify"
    assert clarify.question == "Which Act or SI do you mean?"
    assert clarify.candidates == ()
    assert outcome.mode == "clarify"
    assert outcome.fact is None


def test_ambiguous_query_keeps_provision_on_candidates(
    fixture_router: Router, store: CountingStore
) -> None:
    # clarify_event keeps provision coordinate on candidate instruments
    res = RouteResult(
        status=RouteStatus.AMBIGUOUS,
        next_action=NextAction.ASK_USER,
        reason="ambiguous_instrument",
        candidates=(
            Candidate(
                coordinate=Coordinate.parse("uk/ukpga/1996/18"),
                label="Employment Rights Act 1996",
            ),
        ),
        citations=(
            ParsedCitation(
                span=(0, 11),
                jurisdiction_hint=None,
                instrument_type=None,
                title_as_cited=None,
                year=None,
                number=None,
                provision="section 124",
                resolution="unlinked",
                live_checkable=False,
            ),
        ),
        clarification="Which instrument?",
    )
    clarify = clarify_event(res, router=fixture_router, store=store)
    assert clarify.type == "clarify"
    assert [c.coordinate for c in clarify.candidates] == ["uk/ukpga/1996/18/s124"]
    assert clarify.candidates[0].label == "s.124 Employment Rights Act 1996"


def test_unresolved_refuses(fixture_router: Router, store: CountingStore) -> None:
    events, outcome = run(
        make(fixture_router, store, FakeRotation()), "teleportation spacecraft asteroid"
    )
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == "ROUTE_UNRESOLVED"
    assert outcome.mode == "refusal"


def test_unresolved_concept_clarifies(fixture_router: Router, store: CountingStore) -> None:
    events, outcome = run(
        make(fixture_router, store, FakeRotation()), "what about unfair dismissal"
    )
    (clarify,) = of(events, ClarifyEvent)
    assert clarify.type == "clarify"
    assert outcome.mode == "clarify"


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("s.4 European Union (Amendment) Act 2008", "REPEALED"),
        ("s.1 Secure Tenancies (Victims of Domestic Abuse) Act 2018", "NOT_YET_IN_FORCE"),
    ],
)
def test_temporal_status_refuses(
    fixture_router: Router, store: CountingStore, query: str, kind: str
) -> None:
    rotation = FakeRotation()
    events, _ = run(make(fixture_router, store, rotation), query)
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == kind
    assert refusal.link is None
    assert not rotation.requests


def test_history_not_held_from_as_at_and_from_the_question(
    fixture_router: Router, store: CountingStore
) -> None:
    pipeline = make(fixture_router, store, FakeRotation())
    events, _ = run(pipeline, S124_QUERY, as_at=date(2010, 1, 1))
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == "HISTORY_NOT_HELD"
    assert refusal.link is not None
    assert refusal.link.endswith("/2010-01-01")
    events, _ = run(pipeline, "s.124 ERA 1996 as at 1 January 2010")
    (refusal,) = of(events, RefusalEvent)
    assert refusal.kind == "HISTORY_NOT_HELD"
    assert not store.gets  # decided from the instrument record


def test_hint_as_at() -> None:
    assert hint_as_at("as at 1 January 2010") == date(2010, 1, 1)
    assert hint_as_at("in force in March 2010") == date(2010, 3, 1)
    assert hint_as_at("as of 31st Dec. 2019") == date(2019, 12, 31)
    assert hint_as_at("as at 2010") == date(2010, 1, 1)
    assert hint_as_at("as at 30 February 2010") == date(2010, 2, 1)
    assert hint_as_at("as originally enacted") is None
    assert hint_as_at(None) is None


def test_dateless_temporal_question_gets_a_notice(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1, "stop")])
    events, _ = run(make(fixture_router, store, rotation), "s.124 ERA 1996 as originally enacted")
    notices = [e.code for e in of(events, NoticeEvent)]
    assert notices == ["current_text_only"]


def test_partly_refused_binding_answers_from_the_rest(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1, "stop")])
    events, _ = run(
        make(fixture_router, store, rotation),
        "s.124 ERA 1996 and s.4 European Union (Amendment) Act 2008",
    )
    (notice,) = of(events, NoticeEvent)
    assert notice.code == "status_excluded"
    assert "repealed" in notice.message
    (sources,) = of(events, SourcesEvent)
    assert all(w.coordinate.startswith("uk/ukpga/1996/18/s124") for w in sources.windows)
    fact = receipt(events).fact
    assert set(fact.version_dates) == {"uk/ukpga/1996/18", "uk/ukpga/2008/7"}


def test_excluded_coordinates_are_never_read_into_the_premise(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD2.replace("[P3]", "[P2]"), "stop")])
    events, _ = run(make(fixture_router, store, rotation), "ERA 1996 s.124 except s.124(1ZA)")
    (sources,) = of(events, SourcesEvent)
    coordinates = [w.coordinate for w in sources.windows]
    assert "uk/ukpga/1996/18/s124/1ZA" not in coordinates
    assert "uk/ukpga/1996/18/s124/1" in coordinates
    prompt = rotation.generate_requests[0].messages[0]["content"]
    assert "s124/1ZA" not in prompt


def test_nested_bindings_are_read_once(fixture_router: Router, store: CountingStore) -> None:
    rotation = FakeRotation([(GOOD1, "stop")])
    pipeline = make(fixture_router, store, rotation)
    result = fixture_router.route(S124_QUERY)
    nested = dataclasses.replace(
        result,
        coordinates=(
            *result.coordinates,
            result.coordinates[0].__class__.parse("uk/ukpga/1996/18/s124/1ZA"),
        ),
    )
    pipeline.router = _FixedRouter(nested)  # type: ignore[assignment]
    events, _ = run(pipeline, S124_QUERY)
    assert store.gets == ["uk/ukpga/1996/18/s124"]
    (sources,) = of(events, SourcesEvent)
    coordinates = [w.coordinate for w in sources.windows]
    assert len(coordinates) == len(set(coordinates))


class _FixedRouter:
    def __init__(self, result: Any) -> None:
        self.result = result

    def route(self, query: str, *, context: Any = None) -> Any:
        del query, context
        return self.result


# ---------------------------------------------------------------------- providers


def test_no_provider_before_first_sentence_gives_extractive(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([unavailable("groq", "cloudflare")])
    events, outcome = run(make(fixture_router, store, rotation), S124_QUERY)
    assert [e.type for e in events] == ["route", "sources", "extractive", "receipt", "done"]
    (extractive,) = of(events, ExtractiveEvent)
    assert extractive.reason == "providers_unavailable"
    assert any(w.highlight is not None for w in extractive.windows)
    fact = receipt(events).fact
    assert fact.mode == "extractive"
    assert fact.spans
    assert fact.sentences == ()
    assert outcome.failures == ["groq:rate_limit", "cloudflare:rate_limit"]


def test_nothing_available_skips_generation(fixture_router: Router, store: CountingStore) -> None:
    rotation = FakeRotation(names=())
    events, _ = run(make(fixture_router, store, rotation), S124_QUERY)
    assert [e.type for e in events] == ["route", "sources", "extractive", "receipt", "done"]
    assert not rotation.requests


def test_judge_outage_before_first_sentence_gives_extractive(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1, "stop")], judge_error=unavailable("modal"))
    events, _ = run(make(fixture_router, store, rotation), S124_QUERY)
    assert "extractive" in [e.type for e in events]
    fact = receipt(events).fact
    assert [c.purpose for c in fact.calls] == ["generate"]  # the successful call is recorded


def test_warming_gives_extractive_with_a_notice(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([AllProvidersUnavailable([ProviderWarming("modal")])])
    events, _ = run(make(fixture_router, store, rotation), S124_QUERY)
    assert [e.type for e in events] == [
        "route", "sources", "notice", "extractive", "receipt", "done"
    ]  # fmt: skip
    (notice,) = of(events, NoticeEvent)
    assert notice.code == "provider_warming"
    (extractive,) = of(events, ExtractiveEvent)
    assert extractive.reason == "provider_warming"


def test_outage_after_a_sentence_closes_the_answer(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1 + " The", "length"), unavailable("groq")])
    events, _ = run(make(fixture_router, store, rotation), S124_QUERY)
    assert [e.type for e in events] == [
        "route", "sources", "sentence", "notice", "receipt", "done"
    ]  # fmt: skip
    (notice,) = of(events, NoticeEvent)
    assert notice.code == "providers_unavailable"
    assert notice.message == (
        "The model became unavailable while writing. Every sentence shown was checked; "
        "there may have been more to say."
    )
    assert receipt(events).fact.mode == "generated"


def test_written_citation_is_rolled_back_and_retried(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(CITED, "stop"), (GOOD1, "stop")])
    events, _ = run(make(fixture_router, store, rotation), S124_QUERY)
    (sentence,) = of(events, SentenceEvent)
    assert sentence.sentence.verdict == "supported"
    assert sentence.sentence.rollbacks == 1
    assert len(rotation.generate_requests) == 2


def test_fallback_provider_gets_a_notice(fixture_router: Router, store: CountingStore) -> None:
    rotation = FakeRotation([(GOOD1, "stop")], provider="cloudflare")
    pipeline = make(fixture_router, store, rotation, primary_provider="groq")
    events, _ = run(pipeline, S124_QUERY)
    assert [e.type for e in events] == [
        "route", "sources", "notice", "sentence", "receipt", "done"
    ]  # fmt: skip
    assert of(events, NoticeEvent)[0].code == "provider_fallback"


GROQ = "https://api.groq.com/openai/v1"
ACCOUNT = "0123456789abcdef0123456789abcdef"
CF = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai"
ENV = {"GROQ_API_KEY": "gsk_test", "CLOUDFLARE_AI_WORKER_TOKEN": "cf_test"}


@respx.mock
def test_every_provider_429_gives_extractive(fixture_router: Router, store: CountingStore) -> None:
    """The Slice 1 gate through the real clients and rotation: HTTP 429 everywhere."""
    groq = respx.post(f"{GROQ}/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"}, json={"error": "slow"})
    )
    cf = respx.post(f"{CF}/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"errors": [{"message": "rate limited"}]})
    )
    rotation = ProviderRotation(
        [make_groq(environ=ENV), make_cloudflare(ACCOUNT, environ=ENV)],
        generate=["groq", "cloudflare"],
        judge=["groq", "cloudflare"],
    )
    pipeline = make(fixture_router, store, rotation)
    for _ in range(3):
        events, outcome = run(pipeline, S124_QUERY)
        assert [e.type for e in events] == ["route", "sources", "extractive", "receipt", "done"]
        assert outcome.mode == "extractive"
        assert outcome.error is None
    assert groq.call_count == 1  # then the circuit is open for the Retry-After
    assert cf.call_count == 1


# ---------------------------------------------------------------------- Modal KV path


def test_modal_kv_path_generates_with_rollback_backend(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation(names=("modal",), provider="modal")
    backend = CharBackend({"": f"{GOOD1} {GOOD2}"})
    kv = ModalKV(make_backend=lambda: backend, probe=lambda: True)
    events, _ = run(make(fixture_router, store, rotation, modal_kv=kv), S124_QUERY)
    sentences = of(events, SentenceEvent)
    assert [s.sentence.verdict for s in sentences] == ["supported", "supported"]
    fact = receipt(events).fact
    purposes = [(c.provider, c.purpose) for c in fact.calls]
    assert ("modal", "generate") in purposes
    assert ("modal", "judge") in purposes
    assert not rotation.generate_requests  # generation went to the backend, not the chat API


def test_modal_kv_path_cold_gives_warming_extractive(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation(names=("modal",), provider="modal")
    kv = ModalKV(make_backend=lambda: CharBackend({}), probe=lambda: False)
    events, _ = run(make(fixture_router, store, rotation, modal_kv=kv), S124_QUERY)
    assert [e.type for e in events] == [
        "route", "sources", "notice", "extractive", "receipt", "done"
    ]  # fmt: skip
    assert of(events, ExtractiveEvent)[0].reason == "provider_warming"


def test_modal_kv_path_not_used_while_groq_serves(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1, "stop")], names=("groq", "modal"))
    kv = ModalKV(make_backend=lambda: CharBackend({}), probe=lambda: True)
    events, _ = run(make(fixture_router, store, rotation, modal_kv=kv), S124_QUERY)
    assert len(rotation.generate_requests) == 1
    assert of(events, SentenceEvent)


# ---------------------------------------------------------------------- disconnect, errors


def test_disconnect_stops_the_worker_at_a_sentence_boundary(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1 + " The", "length"), (GOOD2, "stop")])
    pipeline = make(fixture_router, store, rotation)
    seen: list[str] = []

    def emit(event: Event) -> None:
        if event.type == "sentence":
            raise StreamClosed
        seen.append(event.type)

    outcome = pipeline.run(AskRequest(query=S124_QUERY), emit)
    assert outcome.mode == "closed"
    assert outcome.fact is None
    assert seen == ["route", "sources"]
    assert len(rotation.generate_requests) == 1


def test_cancel_flag_is_polled_between_sentences(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([(GOOD1 + " The", "length"), (GOOD2, "stop")])
    pipeline = make(fixture_router, store, rotation)
    events: list[Event] = []
    outcome = pipeline.run(
        AskRequest(query=S124_QUERY),
        events.append,
        lambda: any(e.type == "sentence" for e in events),
    )
    assert outcome.mode == "closed"
    assert [e.type for e in events] == ["route", "sources", "sentence"]
    assert len(rotation.generate_requests) == 1  # the next block was never requested


class BrokenStore(CountingStore):
    def get(self, coordinate: str) -> list[dict[str, Any]]:
        raise RuntimeError(f"disk on fire near {coordinate}")


def test_internal_failure_ends_the_stream_legally_without_the_query(
    fixture_router: Router, fixture_text_store: TextStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pipeline = make(fixture_router, BrokenStore(fixture_text_store), FakeRotation())
    events, outcome = run(pipeline, S124_QUERY)
    assert [e.type for e in events] == ["route", "error", "done"]
    assert outcome.error is not None
    assert outcome.error.startswith("RuntimeError at test_pipeline.py")
    for record in caplog.records:
        assert "compensatory award" not in record.getMessage()
        assert record.exc_info is None


class MissingStore(CountingStore):
    def instrument(self, coordinate: str) -> dict[str, Any] | None:
        del coordinate
        return None


def test_store_without_the_bound_instrument_is_an_error(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    events, outcome = run(
        make(fixture_router, MissingStore(fixture_text_store), FakeRotation()), S124_QUERY
    )
    assert [e.type for e in events] == ["route", "error", "done"]
    assert outcome.error is not None
    assert outcome.error.startswith("LookupError")


# ---------------------------------------------------------------------- premise cap


def _window(i: int, text: str, status: TemporalStatus = TemporalStatus.IN_FORCE) -> Window:
    return Window(
        passage_id=f"P{i}",
        coordinate=f"uk/ukpga/1996/18/s{i}",
        heading=f"Employment Rights Act 1996, section {i}",
        text=text,
        status=status,
        version_date=None,
        source_url=f"https://www.legislation.gov.uk/ukpga/1996/18/section/{i}",
    )


FILLER = "The employer shall keep records of the matters set out in this section. " * 20


def test_select_windows_keeps_a_small_premise_whole() -> None:
    windows = [_window(i, f"Text {i} about pay.") for i in (3, 1, 2)]
    selection = select_windows(windows, "pay")
    assert selection.strategy == "all"
    assert [w.passage_id for w in selection.windows] == ["P1", "P2", "P3"]
    assert [w.coordinate for w in selection.windows] == [w.coordinate for w in windows]
    assert all(w.highlight is None for w in selection.windows)


def test_select_windows_picks_by_overlap_within_the_budget() -> None:
    windows = [_window(i, FILLER) for i in range(1, 30)]
    windows[20] = _window(21, FILLER + " The compensatory award cannot exceed the limit. " + FILLER)
    windows[5] = _window(6, FILLER + " A compensatory award is made. " + FILLER)
    windows[7] = _window(8, FILLER, TemporalStatus.NOT_YET_IN_FORCE)
    selection = select_windows(windows, "compensatory award limit", budget_tokens=600)
    assert selection.strategy == "lexical"
    assert selection.total == 29
    coordinates = [w.coordinate for w in selection.windows]
    assert coordinates[:2] == ["uk/ukpga/1996/18/s6", "uk/ukpga/1996/18/s21"]
    assert selection.estimated_tokens <= 600
    assert all(w.highlight is not None for w in selection.windows)
    assert [w.passage_id for w in selection.windows] == [
        f"P{i}" for i in range(1, len(selection.windows) + 1)
    ]


def test_select_windows_without_overlap_takes_the_leading_windows() -> None:
    windows = [_window(i, FILLER) for i in range(1, 30)]
    selection = select_windows(windows, "zebra crossings", budget_tokens=900)
    assert selection.strategy == "leading"
    assert selection.windows[0].coordinate == "uk/ukpga/1996/18/s1"
    assert len(selection.windows) <= 8
    assert selection.estimated_tokens <= 900


def test_select_windows_cuts_a_single_huge_window() -> None:
    windows = [_window(1, FILLER * 10), _window(2, FILLER * 10)]
    selection = select_windows(windows, "zebra", budget_tokens=500)
    (only,) = selection.windows
    assert only.highlight is not None
    assert only.highlight[0] == 0
    assert selection.estimated_tokens <= 500
    with pytest.raises(ValueError, match="positive"):
        select_windows(windows, "x", budget_tokens=0)
    assert select_windows([], "x").strategy == "none"


def test_whole_act_binding_is_capped_by_lexical_overlap(
    fixture_router: Router, store: CountingStore
) -> None:
    rotation = FakeRotation([("The limit is set out in the passages [P1].", "stop")])
    events, outcome = run(
        make(fixture_router, store, rotation),
        "What does the Employment Rights Act 1996 say about the compensatory award limit?",
    )
    (sources,) = of(events, SourcesEvent)
    assert 1 <= len(sources.windows) <= 8
    assert outcome.selection == "lexical"
    assert outcome.windows_total > len(sources.windows)
    assert all(w.highlight is not None for w in sources.windows)
    prompt = rotation.generate_requests[0].messages[0]["content"]
    assert len(prompt) < 1500 * 4 + 2000
    assert receipt(events).fact.spans


def test_config_budget_is_used(fixture_router: Router, store: CountingStore) -> None:
    rotation = FakeRotation([(GOOD1, "stop")])
    pipeline = make(fixture_router, store, rotation, config=PipelineConfig(max_prompt_windows=2))
    events, _ = run(pipeline, S124_QUERY)
    (sources,) = of(events, SourcesEvent)
    assert len(sources.windows) == 2


def _call(provider: str, purpose: Any) -> ProviderCall:
    return ProviderCall(provider, "m", None, purpose, 1, 1, 1.0)


def test_merge_calls_replaces_placeholders_in_order() -> None:
    gen = [_call("groq", "generate"), _call("unknown", "judge"), _call("groq", "generate"),
           _call("unknown", "judge")]  # fmt: skip
    judge = [_call("cloudflare", "judge"), _call("groq", "judge")]
    merged = merge_calls(gen, judge)
    assert [(c.provider, c.purpose) for c in merged] == [
        ("groq", "generate"), ("cloudflare", "judge"), ("groq", "generate"), ("groq", "judge")
    ]  # fmt: skip
    mismatch = merge_calls(gen, judge[:1])
    assert [c.provider for c in mismatch] == ["groq", "groq", "cloudflare"]


def test_pipeline_global_budget_under_and_over(
    fixture_router: Router, store: CountingStore
) -> None:
    budget = InMemoryDailyBudget()
    pipeline = make(
        fixture_router,
        store,
        FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 2),
        daily_budget=budget,
        global_daily_answers=1,
    )
    today = date(2026, 11, 20)
    pipeline._clock = lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    # 1. Under cap: generated answer
    _events_1, out_1 = run(pipeline, S124_QUERY)
    assert out_1.mode == "generated"
    assert budget.get(today) == DailyBudgetCounts(1, 0)

    # 2. Over cap: extractive with global_budget notice
    events_2, out_2 = run(pipeline, S124_QUERY)
    assert out_2.mode == "extractive"
    notices = [e for e in events_2 if isinstance(e, NoticeEvent)]
    assert any(n.code == "global_budget" for n in notices)
    assert budget.get(today) == DailyBudgetCounts(1, 0)


def test_pipeline_global_budget_hosting_share(fixture_router: Router, store: CountingStore) -> None:
    budget = InMemoryDailyBudget()
    pipeline = make(
        fixture_router,
        store,
        FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 10),
        daily_budget=budget,
        global_daily_answers=10,
        hosting_share=0.2,  # max 2 hosting answers
    )
    today = date(2026, 11, 20)
    pipeline._clock = lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    # Hosting answers 1 and 2 succeed
    _, out_h1 = run(pipeline, S124_QUERY, net_class="hosting")
    assert out_h1.mode == "generated"
    _, out_h2 = run(pipeline, S124_QUERY, net_class="hosting")
    assert out_h2.mode == "generated"
    assert budget.get(today) == DailyBudgetCounts(2, 2)

    # 3rd hosting answer is over hosting share -> extractive with global_budget notice
    events_h3, out_h3 = run(pipeline, S124_QUERY, net_class="hosting")
    assert out_h3.mode == "extractive"
    assert any(isinstance(e, NoticeEvent) and e.code == "global_budget" for e in events_h3)
    assert budget.get(today) == DailyBudgetCounts(2, 2)

    # Other network class is allowed (total 2 < 10)
    _, out_o1 = run(pipeline, S124_QUERY, net_class="other")
    assert out_o1.mode == "generated"
    assert budget.get(today) == DailyBudgetCounts(3, 2)


def test_pipeline_daily_cap_checked_first(fixture_router: Router, store: CountingStore) -> None:
    caps = InMemoryDailyCap()
    budget = InMemoryDailyBudget()
    pipeline = make(
        fixture_router,
        store,
        FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 5),
        daily_caps=caps,
        daily_ask_cap=1,
        daily_budget=budget,
        global_daily_answers=1,
    )
    pipeline._clock = lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

    # 1. 1st query from k1 -> generated
    _, out_1 = run(pipeline, S124_QUERY, client_key="k1")
    assert out_1.mode == "generated"

    # 2. 2nd query from k1 -> both daily cap and global budget would trigger,
    # but daily_cap is checked first so daily_cap notice is emitted
    events_2, out_2 = run(pipeline, S124_QUERY, client_key="k1")
    assert out_2.mode == "extractive"
    notices = [e for e in events_2 if isinstance(e, NoticeEvent)]
    assert any(n.code == "daily_cap" for n in notices)
    assert not any(n.code == "global_budget" for n in notices)


def test_refused_sentence_has_no_citation_chips(
    fixture_router: Router, store: CountingStore
) -> None:
    pipeline = make(fixture_router, store, FakeRotation([]))
    sentence = SentenceOut(
        index=0,
        text="I can't confirm this from the law I found, so I won't guess.",
        verdict="refused",
        passage_ids=("P1", "P2"),
        reasons=(),
        rollbacks=0,
    )
    assert pipeline._cite(sentence, {}, None, {}) == ()  # type: ignore[arg-type]

    narration = "The provided passages do not state the general limitation period [P1]."
    pipeline_run = make(fixture_router, store, FakeRotation([(narration, "stop")]))
    events, _ = run(pipeline_run, S124_QUERY)
    sentence_events = of(events, SentenceEvent)
    assert len(sentence_events) == 1
    assert sentence_events[0].sentence.verdict == "refused"
    assert (
        sentence_events[0].sentence.text
        == "I can't confirm this from the law I found, so I won't guess."
    )
    assert sentence_events[0].citations == ()
