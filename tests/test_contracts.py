"""The shared contract: types, canonical JSON, receipts, SSE events and order, settings."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from legal_rag_router import NextAction, Router, RouteStatus
from legal_rag_verifier.engine import DEFAULT_REFUSAL
from legal_rag_verifier.judge import Chat, Judge
from legal_rag_verifier.premise import Premise

from chapterverse.config import ConfigError, Settings, read_secret
from chapterverse.contracts import (
    EVENT_TYPES,
    REFUSAL_KINDS,
    AllProvidersUnavailable,
    AnswerFactObject,
    ChatMessage,
    ChatProvider,
    ChatRequest,
    ChatResult,
    ClarifyEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    ExtractiveEvent,
    JudgeChat,
    JudgeReply,
    LabelledCoordinate,
    NoticeEvent,
    Policy,
    ProviderCall,
    ProviderUnavailable,
    ReceiptEvent,
    RefusalEvent,
    RenderedCitation,
    Rotation,
    RouteEvent,
    SentenceEvent,
    SentenceOut,
    SourcesEvent,
    TemporalStatus,
    TextStore,
    Timings,
    Window,
    canonical_json,
    encode_sse,
    fold_prefill,
    sha256_hex,
    to_jsonable,
    validate_order,
)

S124 = "section 124 of the Employment Rights Act 1996"
MARCHWOOD = "Marchwood Commercial Arbitration Act 1996"

# ============================================================================ fixture corpus


def test_router_binds_s124(fixture_router: Router) -> None:
    result = fixture_router.route(S124)
    assert result.status is RouteStatus.BOUNDED
    assert result.next_action is NextAction.RETRIEVE_BOUNDED
    assert [str(c) for c in result.coordinates] == ["uk/ukpga/1996/18/s124"]


def test_router_refuses_invented_act(fixture_router: Router) -> None:
    result = fixture_router.route(MARCHWOOD)
    assert result.status is RouteStatus.INSTRUMENT_NOT_FOUND
    assert result.next_action is NextAction.REFUSE
    assert result.coordinates == ()


def test_concept_index_loads(fixture_router: Router) -> None:
    found = fixture_router.discover("limit of compensatory award")
    assert "uk/ukpga/1996/18/s124" in [str(c.coordinate) for c in found.candidates]


def test_text_store_double_matches_protocol(
    fixture_text_store: TextStore, fixture_roles: dict[str, Any]
) -> None:
    assert isinstance(fixture_text_store, TextStore)
    rows = fixture_text_store.get("uk/ukpga/1996/18/s124")
    assert rows[0]["record_type"] == "instrument"
    assert rows[1]["coordinate"] == "uk/ukpga/1996/18/s124"
    premise = Premise.from_records(rows)
    assert any("£123,543" in p.text for p in premise.passages)
    with pytest.raises(KeyError):
        fixture_text_store.get("uk/ukpga/1996/18/s99")
    with pytest.raises(KeyError):
        fixture_text_store.get("uk/ukpga/1996/999")
    title_only = fixture_roles["title_only"]["instrument"]
    assert fixture_text_store.has_text(title_only) is False
    assert fixture_text_store.has_text("uk/ukpga/1996/18") is True
    assert fixture_text_store.get(title_only) == [fixture_text_store.instrument(title_only)]
    assert fixture_text_store.instrument("uk/ukpga/1996/999") is None


def _rows(root: Path, instrument: str) -> list[dict[str, Any]]:
    parts = instrument.split("/")
    path = root.joinpath(*parts[:3], "_".join(parts) + ".jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_fixture_roles_hold(fixture_data_root: Path, fixture_roles: dict[str, Any]) -> None:
    title_only = _rows(fixture_data_root, fixture_roles["title_only"]["instrument"])
    assert len(title_only) == 1
    assert title_only[0]["structure"] == "metadata_only"
    for role, flag in (("repealed", "repealed"), ("prospective", "prospective")):
        spec = fixture_roles[role]
        rows = {r["coordinate"]: r for r in _rows(fixture_data_root, spec["instrument"])}
        assert rows[spec["instrument"]]["repealed"] is False
        for coordinate in spec["provisions"]:
            assert rows[coordinate][flag] is True
        assert rows[spec["in_force_provision"]][flag] is False
    era = _rows(fixture_data_root, fixture_roles["era"]["instrument"])
    sections = {r["coordinate"].split("/")[4] for r in era[1:]}
    assert sections == set(fixture_roles["era"]["sections"])


@pytest.mark.data
def test_real_corpus_has_era(real_data_root: Path) -> None:
    assert (real_data_root / "uk" / "ukpga" / "1996" / "uk_ukpga_1996_18.jsonl").is_file()


# ============================================================================ canonical JSON


def test_canonical_json_is_sorted_compact_utf8() -> None:
    data = {"b": 1, "a": ("£", TemporalStatus.IN_FORCE), "c": {"z": None, "y": 1.5}}
    assert canonical_json(data) == '{"a":["£","IN_FORCE"],"b":1,"c":{"y":1.5,"z":null}}'.encode()


def test_canonical_json_handles_dataclasses() -> None:
    window = Window(
        passage_id="P1",
        coordinate="uk/ukpga/1996/18/s124/1ZA",
        heading="h",
        text="t",
        status=TemporalStatus.REPEALED,
        version_date=None,
        source_url="u",
        highlight=(0, 1),
    )
    assert json.loads(canonical_json(window))["status"] == "REPEALED"
    assert json.loads(canonical_json(window))["highlight"] == [0, 1]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), {1: "x"}, object()])
def test_canonical_json_rejects_values_without_a_form(bad: object) -> None:
    with pytest.raises(TypeError):
        canonical_json(bad)


def test_sha256_hex() -> None:
    assert sha256_hex("abc") == sha256_hex(b"abc")
    assert sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ============================================================================ receipts

_text = st.text(max_size=20)
_coord = st.from_regex(
    r"uk/ukpga/(19|20)[0-9]{2}/[1-9][0-9]?(/s[1-9][0-9]{0,2}[A-Z]?)?", fullmatch=True
)
_opt_int = st.none() | st.integers(min_value=0, max_value=10**6)
_ms = st.floats(min_value=0, max_value=1e6, allow_nan=False, allow_infinity=False)

_sentences = st.builds(
    SentenceOut,
    index=st.integers(min_value=0, max_value=50),
    text=_text,
    verdict=st.sampled_from(["supported", "refused"]),
    passage_ids=st.lists(st.from_regex(r"P[1-9]", fullmatch=True), max_size=3).map(tuple),
    reasons=st.lists(st.sampled_from(["GROUNDED", "UNGROUNDED_FIGURE"]), max_size=2).map(tuple),
    rollbacks=st.integers(min_value=0, max_value=8),
)
_calls = st.builds(
    ProviderCall,
    provider=st.sampled_from(["groq", "cloudflare", "modal"]),
    model_id=_text,
    quantisation=st.none() | _text,
    purpose=st.sampled_from(["generate", "judge"]),
    prompt_tokens=_opt_int,
    completion_tokens=_opt_int,
    latency_ms=_ms,
)
_spans = st.tuples(st.integers(0, 10**4), st.integers(0, 10**4)).map(lambda t: (min(t), max(t)))

afos = st.builds(
    AnswerFactObject,
    query_sha256=st.binary(max_size=8).map(sha256_hex),
    route_status=st.sampled_from([s.value for s in RouteStatus]),
    coordinates=st.lists(_coord, max_size=3).map(tuple),
    passages=st.dictionaries(st.from_regex(r"P[1-9]", fullmatch=True), _coord, max_size=4),
    version_dates=st.dictionaries(_coord, st.none() | st.dates().map(str), max_size=3),
    spans=st.dictionaries(_coord, _spans, max_size=3),
    sentences=st.lists(_sentences, max_size=4).map(tuple),
    corpus_id=_text,
    calls=st.lists(_calls, max_size=3).map(tuple),
    policy_hash=st.binary(max_size=8).map(sha256_hex),
    mode=st.sampled_from(["generated", "extractive", "refusal", "clarify"]),
)


def _shuffled(value: Any, rng: random.Random) -> Any:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {k: _shuffled(v, rng) for k, v in items}
    if isinstance(value, list):
        return [_shuffled(v, rng) for v in value]
    return value


@given(afos)
@settings(max_examples=200)
def test_afo_round_trip_is_identity(afo: AnswerFactObject) -> None:
    assert AnswerFactObject.from_dict(afo.to_dict()) == afo
    assert AnswerFactObject.from_dict(json.loads(canonical_json(afo))) == afo
    assert canonical_json(afo.to_dict()) == canonical_json(afo)


@given(afos, st.integers())
@settings(max_examples=200)
def test_answer_id_is_stable_under_key_order(afo: AnswerFactObject, seed: int) -> None:
    shuffled = _shuffled(afo.to_dict(), random.Random(seed))
    assert AnswerFactObject.from_dict(shuffled).answer_id == afo.answer_id
    assert sha256_hex(canonical_json(shuffled)) == afo.answer_id


def test_afo_rejects_another_schema() -> None:
    data = _example_afo().to_dict()
    data["schema"] = 2
    with pytest.raises(ValueError, match="schema"):
        AnswerFactObject.from_dict(data)


def test_afo_holds_no_raw_query() -> None:
    afo = _example_afo()
    assert b"compensatory" not in canonical_json(afo)
    assert afo.query_sha256 == sha256_hex("What is the limit of the compensatory award?")


# ============================================================================ policy


def test_policy_defaults_and_hash() -> None:
    policy = Policy(judge_preset="qwen27-ar4", prompt_sha256="0" * 64)
    assert policy.refusal_text == DEFAULT_REFUSAL
    assert (policy.max_rollbacks_per_sentence, policy.max_total_rollbacks) == (3, 8)
    assert policy.steering == "inject"
    assert policy.verifier_config_name == "STRICT_FIGURES"
    assert policy.policy_hash == sha256_hex(canonical_json(policy))
    assert len(policy.policy_hash) == 64
    other = Policy(judge_preset="qwen27-ar4", prompt_sha256="0" * 64, max_total_rollbacks=7)
    assert other.policy_hash != policy.policy_hash
    same = Policy(prompt_sha256="0" * 64, judge_preset="qwen27-ar4")
    assert same.policy_hash == policy.policy_hash


# ============================================================================ chat layer


def _request(**kwargs: Any) -> ChatRequest:
    base: dict[str, Any] = {
        "messages": (
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content="q"),
        ),
        "max_tokens": 64,
        "temperature": 0.7,
        "top_p": 0.8,
    }
    return ChatRequest(**{**base, **kwargs})


class FakeProvider:
    def __init__(self, name: str, *, fail: ProviderUnavailable | None = None) -> None:
        self.name = name
        self.model_id = "fake-model"
        self.quantisation: str | None = None
        self.supports_prefill = False
        self.fail = fail

    def complete(self, req: ChatRequest) -> ChatResult:
        if self.fail is not None:
            raise self.fail
        return ChatResult(
            text=req.messages[-1]["content"],
            finish_reason="stop",
            provider=self.name,
            model_id=self.model_id,
            quantisation=self.quantisation,
            prompt_tokens=None,
            completion_tokens=1,
            latency_ms=1.0,
        )


class FakeRotation:
    def __init__(self, providers: list[FakeProvider]) -> None:
        self.providers = providers

    def complete(self, req: ChatRequest) -> ChatResult:
        failures: list[ProviderUnavailable] = []
        for provider in self.providers:
            try:
                return provider.complete(req)
            except ProviderUnavailable as exc:
                failures.append(exc)
        raise AllProvidersUnavailable(failures)

    def available(self) -> list[str]:
        return [p.name for p in self.providers if p.fail is None]


class RotationJudgeChat:
    """The adapter shape agent B3/B4 will write: a Rotation behind the judge's chat callable."""

    def __init__(self, rotation: Rotation, reply: str) -> None:
        self.rotation = rotation
        self.model = "fake-model"
        self.reply = reply
        self.calls: list[ProviderCall] = []

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        req = _request(
            messages=tuple(ChatMessage(role="user", content=m["content"]) for m in messages),
            purpose="judge",
        )
        result = self.rotation.complete(req)
        self.calls.append(ProviderCall.from_result(result, "judge"))
        return JudgeReply(
            content=self.reply, ms=result.latency_ms, tokens=result.completion_tokens, finish="stop"
        )


def test_fakes_satisfy_protocols() -> None:
    provider = FakeProvider("groq")
    rotation = FakeRotation([provider])
    chat = RotationJudgeChat(rotation, "{}")
    assert isinstance(provider, ChatProvider)
    assert isinstance(rotation, Rotation)
    assert isinstance(chat, JudgeChat)
    as_verifier_chat: Chat = chat  # the verifier's own alias accepts the protocol (mypy)
    assert callable(as_verifier_chat)


def test_rotation_falls_through_and_reports_all_failures() -> None:
    quota = ProviderUnavailable("groq", "quota", retry_after_s=30.0)
    rate = ProviderUnavailable("cloudflare", "rate_limit", retry_after_s=5.0, detail="429")
    rotation = FakeRotation([FakeProvider("groq", fail=quota), FakeProvider("modal")])
    assert rotation.complete(_request()).provider == "modal"
    assert rotation.available() == ["modal"]
    dead = FakeRotation([FakeProvider("groq", fail=quota), FakeProvider("cf", fail=rate)])
    with pytest.raises(AllProvidersUnavailable) as caught:
        dead.complete(_request())
    assert caught.value.failures == (quota, rate)
    assert caught.value.retry_after_s == 5.0
    assert "cloudflare unavailable (rate_limit, retry after 5 s): 429" in str(caught.value)
    assert AllProvidersUnavailable([]).retry_after_s is None


def test_judge_accepts_judge_chat() -> None:
    chat = RotationJudgeChat(
        FakeRotation([FakeProvider("groq")]), '{"label":"supported","passage":1}'
    )
    judge = Judge(chat)
    assert judge.model_id == "fake-model"
    probs = judge.score(["The cap is £1."], "The cap is £1.")
    assert probs[0].entailment == 1.0
    assert judge.calls[0]["label"] == "supported"
    assert [c.purpose for c in chat.calls] == ["judge"]


def test_full_outage_escapes_the_judge() -> None:
    down = ProviderUnavailable("groq", "server")
    chat = RotationJudgeChat(FakeRotation([FakeProvider("groq", fail=down)]), "{}")
    with pytest.raises(AllProvidersUnavailable):
        Judge(chat).score(["p"], "h")


def test_fold_prefill() -> None:
    messages = (
        ChatMessage(role="system", content="s"),
        ChatMessage(role="user", content="q"),
        ChatMessage(role="assistant", content="a"),
    )
    folded = fold_prefill(messages, "The cap is")
    assert folded[0] == messages[0]
    assert folded[2] == messages[2]
    assert folded[1]["role"] == "user"
    assert folded[1]["content"].startswith("q\n\n")
    assert "The cap is" in folded[1]["content"]
    with pytest.raises(ValueError, match="user message"):
        fold_prefill((ChatMessage(role="system", content="s"),), "x")


# ============================================================================ SSE events


def _window(n: int = 1) -> Window:
    return Window(
        passage_id=f"P{n}",
        coordinate="uk/ukpga/1996/18/s124/1ZA",
        heading="Employment Rights Act 1996, section 124(1ZA)",
        text="(1ZA) The amount specified in this subsection is the lower of (a) £123,543,",
        status=TemporalStatus.IN_FORCE,
        version_date="2026-07-29",
        source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/124",
        highlight=(54, 76),
    )


def _example_afo() -> AnswerFactObject:
    return AnswerFactObject(
        query_sha256=sha256_hex("What is the limit of the compensatory award?"),
        route_status=RouteStatus.BOUNDED.value,
        coordinates=("uk/ukpga/1996/18/s124",),
        passages={"P1": "uk/ukpga/1996/18/s124/1ZA"},
        version_dates={"uk/ukpga/1996/18": "2026-07-29"},
        spans={"uk/ukpga/1996/18/s124/1ZA": (54, 76)},
        sentences=(
            SentenceOut(
                index=0,
                text="The limit is £123,543.",
                verdict="supported",
                passage_ids=("P1",),
                reasons=("GROUNDED",),
                rollbacks=0,
            ),
        ),
        corpus_id="fixture",
        calls=(),
        policy_hash=Policy(judge_preset="qwen27-ar4", prompt_sha256="0" * 64).policy_hash,
        mode="generated",
    )


def _every_event() -> list[Event]:
    afo = _example_afo()
    choice = LabelledCoordinate(coordinate="uk/ukpga/1996/18", label="Employment Rights Act 1996")
    return [
        RouteEvent(
            status="ROUTE_BOUNDED",
            next_action="RETRIEVE_BOUNDED",
            coordinates=("uk/ukpga/1996/18/s124",),
            corrections=(("employmnet", "employment"),),
            messages=(),
            index_snapshot="2026-09-28",
        ),
        ClarifyEvent(question="Which Act?", candidates=(choice,)),
        RefusalEvent(
            kind="TEXT_NOT_HELD",
            message="m",
            suggestions=(choice,),
            snapshot="2026-09-28",
            link="https://www.legislation.gov.uk/ukpga/1980/15",
        ),
        SourcesEvent(windows=(_window(),)),
        SentenceEvent(
            sentence=afo.sentences[0],
            citations=(
                RenderedCitation(
                    passage_id="P1",
                    coordinate="uk/ukpga/1996/18/s124/1ZA",
                    label="s.124(1ZA) Employment Rights Act 1996",
                    url="https://www.legislation.gov.uk/ukpga/1996/18/section/124",
                ),
            ),
        ),
        ExtractiveEvent(reason="providers_unavailable", windows=(_window(),)),
        NoticeEvent(code="provider_fallback", message="Answered by a fallback provider."),
        ReceiptEvent(answer_id=afo.answer_id, fact=afo),
        ErrorEvent(code="internal", message="Something went wrong.\nTry again."),
        DoneEvent(timings=Timings(first_source_ms=12.5, first_sentence_ms=None, total_ms=40.0)),
    ]


def test_every_event_type_is_covered() -> None:
    assert [e.type for e in _every_event()] == list(EVENT_TYPES)


@pytest.mark.parametrize("event", _every_event(), ids=lambda e: e.type)
def test_encode_sse(event: Event) -> None:
    raw = encode_sse(event)
    assert raw.endswith(b"\n\n")
    lines = raw[:-2].split(b"\n")
    assert lines[0] == b"event: " + event.type.encode()
    assert len(lines) == 2
    assert lines[1].startswith(b"data: ")
    payload = json.loads(lines[1][len(b"data: ") :])
    assert payload["type"] == event.type
    assert payload == to_jsonable(event)


def test_receipt_event_carries_the_fact_object() -> None:
    afo = _example_afo()
    payload = json.loads(
        encode_sse(ReceiptEvent(answer_id=afo.answer_id, fact=afo)).split(b"data: ")[1]
    )
    assert AnswerFactObject.from_dict(payload["fact"]).answer_id == payload["answer_id"]


def test_route_event_from_router(fixture_router: Router) -> None:
    event = RouteEvent.from_route_result(fixture_router.route(S124))
    assert event.status == "ROUTE_BOUNDED"
    assert event.next_action == "RETRIEVE_BOUNDED"
    assert event.coordinates == ("uk/ukpga/1996/18/s124",)
    assert event.index_snapshot == "2026-09-28"
    refused = RouteEvent.from_route_result(fixture_router.route(MARCHWOOD))
    assert refused.status in REFUSAL_KINDS
    assert refused.messages


def test_refusal_kinds() -> None:
    assert "ROUTE_BOUNDED" not in REFUSAL_KINDS
    assert "ROUTE_AMBIGUOUS" not in REFUSAL_KINDS
    assert "IN_FORCE" not in REFUSAL_KINDS
    assert {"TEXT_NOT_HELD", "HISTORY_NOT_HELD", "out_of_scope"} <= REFUSAL_KINDS
    assert "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND" in REFUSAL_KINDS


# ============================================================================ event order

LEGAL = [
    ["route", "clarify", "done"],
    ["route", "notice", "clarify", "notice", "done"],
    ["route", "refusal", "receipt", "done"],
    ["route", "sources", "receipt", "done"],
    ["route", "sources", "sentence", "receipt", "done"],
    ["route", "notice", "sources", "sentence", "notice", "sentence", "receipt", "notice", "done"],
    ["route", "sources", "extractive", "receipt", "done"],
    ["route", "sources", "notice", "extractive", "receipt", "done"],
    ["error", "done"],
    ["route", "error", "done"],
    ["route", "sources", "sentence", "error", "done"],
    ["route", "refusal", "receipt", "error", "done"],
]
ILLEGAL = [
    [],
    ["done"],
    ["route"],
    ["notice", "route", "clarify", "done"],
    ["route", "clarify", "receipt", "done"],
    ["route", "refusal", "done"],
    ["route", "sources", "done"],
    ["route", "sources", "sentence", "extractive", "receipt", "done"],
    ["route", "sources", "extractive", "sentence", "receipt", "done"],
    ["route", "sources", "extractive", "extractive", "receipt", "done"],
    ["route", "sentence", "receipt", "done"],
    ["route", "route", "clarify", "done"],
    ["route", "clarify", "done", "notice"],
    ["route", "error"],
    ["route", "error", "notice", "done"],
    ["route", "error", "error", "done"],
    ["route", "clarify", "done", "error", "done"],
    ["route", "bogus", "done"],
]


@pytest.mark.parametrize("types", LEGAL)
def test_legal_orders(types: list[str]) -> None:
    assert validate_order(types)


@pytest.mark.parametrize("types", ILLEGAL)
def test_illegal_orders(types: list[str]) -> None:
    assert not validate_order(types)


@given(st.lists(st.sampled_from([*EVENT_TYPES, "other"]), max_size=8))
def test_validate_order_never_raises(types: list[str]) -> None:
    result = validate_order(types)
    if result:
        assert types[-1] == "done"
        assert types.count("done") == 1
        assert types[0] in ("route", "error")


# ============================================================================ settings


def test_settings_defaults_from_root() -> None:
    s = Settings.from_env({"LEGAL_DATA_ROOT": "/data"})
    assert s.legal_data_root == Path("/data")
    assert s.router_index == Path("/data/index")
    assert s.concepts_index == Path("/data/concepts")
    assert s.store_dir == Path("artefacts/store")
    assert s.max_query_bytes == 4096
    assert s.cors_origins == ()
    assert (s.database_url, s.modal_url, s.cloudflare_account_id) == (None, None, None)
    assert s.groq_api_key_env == "GROQ_API_KEY"
    assert s.cloudflare_token_env == "CLOUDFLARE_AI_WORKER_TOKEN"
    assert s.modal_api_key_env == "MODAL_API_KEY"


def test_settings_overrides() -> None:
    s = Settings.from_env(
        {
            "ROUTER_INDEX": "/i",
            "CONCEPTS_INDEX": "/c",
            "STORE_DIR": "/s",
            "MAX_QUERY_BYTES": "100",
            "CORS_ORIGINS": "https://a.example, https://b.example,",
            "MODAL_URL": "https://modal.example",
            "CLOUDFLARE_ACCOUNT_ID": "acct",
        }
    )
    assert s.legal_data_root is None
    assert (s.router_index, s.concepts_index, s.store_dir) == (Path("/i"), Path("/c"), Path("/s"))
    assert s.max_query_bytes == 100
    assert s.cors_origins == ("https://a.example", "https://b.example")


@pytest.mark.parametrize(
    "env",
    [{}, {"ROUTER_INDEX": "/i"}, {"LEGAL_DATA_ROOT": "/d", "MAX_QUERY_BYTES": "x"},
     {"LEGAL_DATA_ROOT": "/d", "MAX_QUERY_BYTES": "0"}],
)  # fmt: skip
def test_settings_errors(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_settings_never_show_secrets() -> None:
    secret_url = "postgres://user:hunter2@db.example/cv"
    env = {"LEGAL_DATA_ROOT": "/d", "DATABASE_URL": secret_url, "GROQ_API_KEY": "gsk_secret"}
    s = Settings.from_env(env)
    assert s.database_url == secret_url
    for text in (repr(s), str(s), f"{s}"):
        assert "hunter2" not in text
        assert "gsk_secret" not in text
        assert "database_url='<set>'" in text


def test_read_secret() -> None:
    assert read_secret("K", {"K": "  abc \n"}) == "abc"
    assert read_secret("K", {"K": "  "}) is None
    assert read_secret("K", {}) is None
    with pytest.raises(ConfigError) as caught:
        read_secret("K", {"K": "a\x00b"})
    assert "a\x00b" not in str(caught.value)
