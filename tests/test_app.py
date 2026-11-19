"""The FastAPI app end to end (agent C1): SSE bytes, edge rules, logging, startup wiring.

Requests go through Starlette's ``TestClient`` (or straight through ASGI for the disconnect
case) to the fixture router and store with :class:`test_pipeline_fakes.FakeRotation`.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from legal_rag_router import Router
from starlette.types import Message
from tokenizers import Tokenizer, models, pre_tokenizers

from chapterverse.app import (
    MeteredPost,
    QwenChatTokenizer,
    Runtime,
    _Maintenance,
    build_runtime,
    check_snapshots,
    create_app,
    load_mask_processor,
    load_modal_kv,
    load_tokenizer,
    parse_ask,
    pin_check,
)
from chapterverse.audit.modal_path import ModalSGLangBackend
from chapterverse.audit.policy import build_policy
from chapterverse.config import Settings
from chapterverse.contracts import (
    AllProvidersUnavailable,
    AnswerFactObject,
    ProviderUnavailable,
    TextStore,
    canonical_json,
    sha256_hex,
)
from chapterverse.persistence import INSERT_RECEIPT_SQL, Database, ReceiptStore
from chapterverse.pipeline import AskRequest, ModalKV, Pipeline
from chapterverse.providers import ProviderRotation, make_cloudflare, make_groq, make_modal
from chapterverse.providers.ledger import InMemoryLedger, ModalLimits
from chapterverse.providers.presets import JUDGE_PRESET, JUDGE_PRESET_NAME
from chapterverse.ratelimit import (
    DEFAULT_DAILY_ASK_CAP,
    DailyCapStore,
    InMemoryDailyCap,
    TokenBucket,
    client_key_hash,
)

if TYPE_CHECKING:  # mypy sees tests/ as the package "tests"; pytest puts tests/ on sys.path
    from tests.test_pipeline_fakes import (
        AMBIGUOUS_QUERY,
        GOOD1,
        GOOD2,
        MARCHWOOD_QUERY,
        S124_QUERY,
        TITLE_ONLY_QUERY,
        FakeRotation,
        fact_bytes,
        parse_sse,
    )
else:
    from test_pipeline_fakes import (
        AMBIGUOUS_QUERY,
        GOOD1,
        GOOD2,
        MARCHWOOD_QUERY,
        S124_QUERY,
        TITLE_ONLY_QUERY,
        FakeRotation,
        fact_bytes,
        parse_sse,
    )

REPO = Path(__file__).resolve().parents[1]
POLICY = build_policy(JUDGE_PRESET_NAME)
GROQ = "https://api.groq.com/openai/v1"
ACCOUNT = "0123456789abcdef0123456789abcdef"
CF = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai"
ENV = {"GROQ_API_KEY": "gsk_test", "CLOUDFLARE_AI_WORKER_TOKEN": "cf_test"}
ACCESS = {"CF-Access-Client-Id": "id.access", "CF-Access-Client-Secret": "secret"}


class SpyRouter:
    """Counts ``route`` calls; delegates to the fixture router."""

    def __init__(self, inner: Router) -> None:
        self.inner = inner
        self.calls = 0

    @property
    def index(self) -> Any:
        return self.inner.index

    def route(self, query: str, *, context: Any = None) -> Any:
        self.calls += 1
        return self.inner.route(query, context=context)


class FakeConnection:
    """A psycopg connection stand-in that records statements."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    def execute(self, query: Any, params: Any = ()) -> Any:
        self.log.append((str(query), params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []

    def close(self) -> None:
        pass


def settings(root: Path, **kwargs: Any) -> Settings:
    return Settings(
        legal_data_root=root,
        router_index=root / "index",
        concepts_index=root / "concepts",
        **kwargs,
    )


def runtime(
    root: Path,
    router: Any,
    store: TextStore,
    rotation: Any,
    *,
    limiter: TokenBucket | None = None,
    receipts: ReceiptStore | None = None,
    daily_caps: DailyCapStore | None = None,
    daily_ask_cap: int = DEFAULT_DAILY_ASK_CAP,
    cap_salt: str = "",
    require_access: bool = False,
    **kwargs: Any,
) -> Runtime:
    caps = daily_caps if daily_caps is not None else InMemoryDailyCap()
    pipeline = Pipeline(
        router=router,
        store=store,
        rotation=rotation,
        policy=POLICY,
        daily_caps=caps,
        daily_ask_cap=daily_ask_cap,
    )
    return Runtime(
        settings=settings(root, **kwargs),
        pipeline=pipeline,
        store=store,
        limiter=limiter if limiter is not None else TokenBucket(per_minute=600, burst=100),
        receipts=receipts,
        daily_caps=caps,
        daily_ask_cap=daily_ask_cap,
        cap_salt=cap_salt,
        require_access=require_access,
    )


@pytest.fixture
def spy(fixture_router: Router) -> SpyRouter:
    return SpyRouter(fixture_router)


def ask(client: TestClient, query: str, **extra: Any) -> Any:
    """POST a question; the response type is the test client's (httpx or httpx2)."""
    headers = extra.pop("headers", {})
    return client.post("/api/ask", json={"query": query, **extra}, headers=headers)


def events(response: Any) -> list[tuple[str, dict[str, Any], str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    parsed = parse_sse(response.content)
    from chapterverse.contracts import validate_order  # noqa: PLC0415

    assert validate_order([t for t, _, _ in parsed]), [t for t, _, _ in parsed]
    return parsed


# ---------------------------------------------------------------------- the Slice 1 cases


def test_s124_streams_verified_cited_sentences_and_a_hashable_receipt(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")])
    rt = runtime(fixture_data_root, spy, fixture_text_store, rotation)
    with TestClient(create_app(rt)) as client:
        parsed = events(ask(client, S124_QUERY))
    types = [t for t, _, _ in parsed]
    assert types == ["route", "sources", "sentence", "sentence", "receipt", "done"]
    sentences = [d for t, d, _ in parsed if t == "sentence"]
    assert all(s["sentence"]["verdict"] == "supported" for s in sentences)
    assert sentences[0]["citations"][0]["label"] == "s.124(1ZA) Employment Rights Act 1996"
    (_, receipt, raw) = next(p for p in parsed if p[0] == "receipt")
    data = fact_bytes(raw)
    assert sha256_hex(data) == receipt["answer_id"]
    fact = AnswerFactObject.from_dict(json.loads(data))
    assert fact.answer_id == receipt["answer_id"]
    assert canonical_json(fact) == data
    assert fact.query_sha256 == sha256_hex(S124_QUERY)
    done = parsed[-1][1]
    assert done["timings"]["first_source_ms"] is not None
    assert done["timings"]["first_sentence_ms"] >= done["timings"]["first_source_ms"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (MARCHWOOD_QUERY, ["route", "refusal", "receipt", "done"]),
        (TITLE_ONLY_QUERY, ["route", "refusal", "receipt", "done"]),
        (AMBIGUOUS_QUERY, ["route", "clarify", "done"]),
    ],
)
def test_refusals_and_clarify_end_to_end(
    fixture_data_root: Path,
    spy: SpyRouter,
    fixture_text_store: TextStore,
    query: str,
    expected: list[str],
) -> None:
    rt = runtime(fixture_data_root, spy, fixture_text_store, FakeRotation())
    with TestClient(create_app(rt)) as client:
        parsed = events(ask(client, query))
    assert [t for t, _, _ in parsed] == expected
    if query == TITLE_ONLY_QUERY:
        refusal = parsed[1][1]
        assert refusal["kind"] == "TEXT_NOT_HELD"
        assert refusal["link"] == "https://www.legislation.gov.uk/ukpga/1980/15"
    if query == MARCHWOOD_QUERY:
        assert parsed[1][1]["kind"] == "EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND"


@respx.mock
def test_every_provider_429_gives_extractive_with_no_failed_response(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    respx.post(f"{GROQ}/chat/completions").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"}, json={"error": "x"})
    )
    respx.post(f"{CF}/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={"errors": [{"message": "rate limited"}]})
    )
    rotation = ProviderRotation(
        [make_groq(environ=ENV), make_cloudflare(ACCOUNT, environ=ENV)],
        generate=["groq", "cloudflare"],
        judge=["groq", "cloudflare"],
    )
    rt = runtime(fixture_data_root, spy, fixture_text_store, rotation)
    with TestClient(create_app(rt)) as client:
        responses = [ask(client, S124_QUERY) for _ in range(5)]
    failed = [r for r in responses if r.status_code != 200]
    assert failed == []
    for response in responses:
        parsed = events(response)
        assert [t for t, _, _ in parsed] == ["route", "sources", "extractive", "receipt", "done"]
        assert parsed[2][1]["reason"] == "providers_unavailable"
        assert "error" not in [t for t, _, _ in parsed]


# ---------------------------------------------------------------------- edge rules


def test_oversized_query_is_413_before_routing(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    rt = runtime(fixture_data_root, spy, fixture_text_store, FakeRotation(), max_query_bytes=64)
    with TestClient(create_app(rt)) as client:
        too_long = ask(client, "é" * 33)  # 66 UTF-8 bytes
        assert too_long.status_code == 413
        assert too_long.json()["error"] == "query_too_long"
        huge = client.post(
            "/api/ask",
            content=b'{"query": "' + b"a" * 20_000 + b'"}',
            headers={"content-type": "application/json"},
        )
        assert huge.status_code == 413
        assert huge.json()["error"] == "body_too_large"
        exact = ask(client, "a" * 64)
        assert exact.status_code == 200
    assert spy.calls == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b'["query"]',
        b'{"query": 5}',
        b'{"query": "x", "as_at": "yesterday"}',
        b'{"query": "x", "context": "uk/ukpga/1996/18"}',
        b'{"query": "\\ud800"}',
    ],
)
def test_malformed_body_is_400(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore, body: bytes
) -> None:
    rt = runtime(fixture_data_root, spy, fixture_text_store, FakeRotation())
    with TestClient(create_app(rt)) as client:
        response = client.post(
            "/api/ask", content=body, headers={"content-type": "application/json"}
        )
    assert response.status_code == 400
    assert response.json()["error"] == "bad_request"
    assert spy.calls == 0


def test_parse_ask_reads_the_optional_fields() -> None:
    body = json.dumps(
        {"query": "q", "as_at": "2010-01-01", "context": ["uk/ukpga/1996/18"], "session_id": "s"}
    ).encode()
    request = parse_ask(body, max_query_bytes=10)
    assert request.as_at is not None
    assert request.as_at.isoformat() == "2010-01-01"
    assert request.context == ("uk/ukpga/1996/18",)
    assert parse_ask(b'{"query": "q", "as_at": null}', max_query_bytes=10).as_at is None


def test_rate_limit_is_429_before_routing(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        FakeRotation(),
        limiter=TokenBucket(per_minute=1, burst=2),
    )
    with TestClient(create_app(rt)) as client:
        first = ask(client, MARCHWOOD_QUERY)
        second = ask(client, MARCHWOOD_QUERY)
        third = ask(client, MARCHWOOD_QUERY)
    assert (first.status_code, second.status_code) == (200, 200)
    assert third.status_code == 429
    assert third.json()["error"] == "rate_limited"
    assert int(third.headers["retry-after"]) >= 1
    assert spy.calls == 2


def test_access_headers_required_when_set(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    rt = runtime(fixture_data_root, spy, fixture_text_store, FakeRotation(), require_access=True)
    with TestClient(create_app(rt)) as client:
        bare = ask(client, MARCHWOOD_QUERY)
        assert bare.status_code == 403
        assert bare.json()["error"] == "access_required"
        partial = ask(client, MARCHWOOD_QUERY, headers={"CF-Access-Client-Id": "x"})
        assert partial.status_code == 403
        assert ask(client, MARCHWOOD_QUERY, headers=ACCESS).status_code == 200
        jwt = ask(client, MARCHWOOD_QUERY, headers={"Cf-Access-Jwt-Assertion": "e30.e30.x"})
        assert jwt.status_code == 200
        assert client.get("/api/health").status_code == 200
    assert spy.calls == 2


@pytest.mark.parametrize("require_access", [True, False])
def test_rate_limit_keys_on_the_worker_client_ip_only_after_the_access_check(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore, require_access: bool
) -> None:
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        FakeRotation(),
        limiter=TokenBucket(per_minute=1, burst=1),
        require_access=require_access,
    )
    with TestClient(create_app(rt)) as client:
        a = {**ACCESS, "X-CV-Client-IP": "203.0.113.1"}
        b = {**ACCESS, "X-CV-Client-IP": "203.0.113.2"}
        assert ask(client, MARCHWOOD_QUERY, headers=a).status_code == 200
        second = ask(client, MARCHWOOD_QUERY, headers=b).status_code
        if require_access:
            # Access checked: each Worker-reported client has its own bucket.
            assert second == 200
            assert ask(client, MARCHWOOD_QUERY, headers=a).status_code == 429
            cf = {**ACCESS, "CF-Connecting-IP": "198.51.100.9", "X-CV-Client-IP": "203.0.113.1"}
            assert ask(client, MARCHWOOD_QUERY, headers=cf).status_code == 429
        else:
            # No Access check: the header could be forged, so the socket peer is the key.
            assert second == 429


def test_health(fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore) -> None:
    rt = runtime(fixture_data_root, spy, fixture_text_store, FakeRotation())
    with TestClient(create_app(rt)) as client:
        response = client.get("/api/health")
    assert response.json() == {"status": "ok", "corpus_id": "fixture", "snapshot": "2026-09-28"}


def test_cors_allows_only_the_configured_origins(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        FakeRotation(),
        cors_origins=("https://chapterandverse.uk",),
    )
    with TestClient(create_app(rt)) as client:
        ok = client.options(
            "/api/ask",
            headers={
                "Origin": "https://chapterandverse.uk",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        other = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "https://chapterandverse.uk"
    assert "access-control-allow-origin" not in other.headers


def test_no_raw_query_in_logs(
    fixture_data_root: Path,
    spy: SpyRouter,
    fixture_text_store: TextStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")])
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        rotation,
        limiter=TokenBucket(per_minute=1, burst=3),
        max_query_bytes=200,
    )
    with TestClient(create_app(rt)) as client:
        events(ask(client, S124_QUERY))
        events(ask(client, MARCHWOOD_QUERY))
        assert ask(client, S124_QUERY + " x" * 200).status_code == 413
        assert ask(client, S124_QUERY).status_code == 429
    messages = [r.getMessage() for r in caplog.records]
    assert any(sha256_hex(S124_QUERY) in m for m in messages)
    for text in (S124_QUERY, "compensatory award", "Marchwood", MARCHWOOD_QUERY):
        assert not any(text in m for m in messages), text
    summary = next(m for m in messages if m.startswith("ask {"))
    assert json.loads(summary.removeprefix("ask "))["route_status"] == "ROUTE_BOUNDED"


def test_receipts_are_stored_without_the_query(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    log: list[tuple[str, Any]] = []
    db = Database("postgres://unused", connect=lambda _: FakeConnection(log), background=False)
    rotation = FakeRotation([(GOOD1, "stop")])
    rt = runtime(fixture_data_root, spy, fixture_text_store, rotation, receipts=ReceiptStore(db))
    with TestClient(create_app(rt)) as client:
        parsed = events(ask(client, S124_QUERY))
        events(ask(client, AMBIGUOUS_QUERY))  # clarify: no receipt
    answer_id = next(d for t, d, _ in parsed if t == "receipt")["answer_id"]
    inserts = [p for sql, p in log if sql == INSERT_RECEIPT_SQL]
    assert [p[0] for p in inserts] == [answer_id]
    stored = json.loads(inserts[0][1])
    assert S124_QUERY not in inserts[0][1]
    assert "query" not in stored
    assert stored["query_sha256"] == sha256_hex(S124_QUERY)
    assert sha256_hex(inserts[0][1]) == answer_id


# ---------------------------------------------------------------------- disconnect


def test_disconnect_stops_the_worker_and_stores_nothing(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    log: list[tuple[str, Any]] = []
    db = Database("postgres://unused", connect=lambda _: FakeConnection(log), background=False)
    rotation = FakeRotation([(GOOD1 + " The", "length"), (GOOD2, "stop")])
    rotation.gate = threading.Event()
    rt = runtime(fixture_data_root, spy, fixture_text_store, rotation, receipts=ReceiptStore(db))
    app = create_app(rt)
    app.state.runtime = rt
    body = json.dumps({"query": S124_QUERY}).encode()
    sent: list[Message] = []

    async def scenario() -> None:
        incoming = [{"type": "http.request", "body": body, "more_body": False}]
        gone = anyio.Event()

        async def receive() -> Message:
            if incoming:
                return incoming.pop(0)
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            sent.append(message)
            if b"event: sentence" in message.get("body", b""):
                gone.set()  # the reader closes the tab after the first sentence

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/ask",
            "raw_path": b"/api/ask",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 5000),
            "server": ("testserver", 80),
            "state": {},
        }
        with anyio.fail_after(10):
            await app(scope, receive, send)

    anyio.run(scenario)
    rotation.gate.set()  # let the held second block finish; the worker must then stop
    rt.executor.shutdown(wait=True)
    streamed = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert b"event: sentence" in streamed
    assert b"event: receipt" not in streamed
    assert not [sql for sql, _ in log if sql == INSERT_RECEIPT_SQL]
    assert len(rotation.generate_requests) <= 2
    assert len(rotation.requests) <= 4


# ---------------------------------------------------------------------- startup


class OtherSnapshot:
    corpus_id = "x"
    snapshot = "2026-01-01"


def test_snapshot_mismatch_refuses_to_start(fixture_router: Router) -> None:
    with pytest.raises(RuntimeError, match="differs from router index snapshot"):
        check_snapshots(OtherSnapshot(), fixture_router)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def built_store(tmp_path_factory: pytest.TempPathFactory, fixture_data_root: Path) -> Path:
    out = tmp_path_factory.mktemp("store") / "store"
    cmd = [
        sys.executable,
        str(REPO / "build" / "textstore.py"),
        "build",
        "--data",
        str(fixture_data_root),
        "--out",
        str(out),
        "--workers",
        "1",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO)  # noqa: S603
    return out


def test_build_runtime_from_the_environment(
    fixture_data_root: Path, built_store: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    env = {
        "LEGAL_DATA_ROOT": str(fixture_data_root),
        "STORE_DIR": str(built_store),
        "GEN_PROVIDERS": "groq,cloudflare",
        "JUDGE_PROVIDERS": "groq,cloudflare",
        "RATE_LIMIT_PER_MINUTE": "30",
    }
    with TestClient(create_app(lambda: build_runtime(env))) as client:
        health = client.get("/api/health").json()
        parsed = events(ask(client, S124_QUERY))
    assert health["status"] == "ok"
    assert health["snapshot"] == "2026-09-28"
    assert len(health["corpus_id"]) == 64
    # No keys in this environment: both providers are out, so the answer is extractive.
    assert [t for t, _, _ in parsed] == ["route", "sources", "extractive", "receipt", "done"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("provider groq out of rotation: GROQ_API_KEY is not set" in m for m in messages)


# ---------------------------------------------------------------------- pin check


def _models(ids: list[str]) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": i} for i in ids]})


def _completion(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "qwen/qwen3.8-27b",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 28, "completion_tokens": 8},
        },
    )


@respx.mock
def test_pin_check_skips_modal_and_keeps_a_provider_on_an_inconclusive_check() -> None:
    modal_route = respx.route(host="modal.example").mock(return_value=httpx.Response(500))
    respx.get(f"{GROQ}/models").mock(return_value=httpx.Response(429))
    modal_env = {**ENV, "MODAL_API_KEY": "mk"}
    rotation = ProviderRotation(
        [make_groq(environ=ENV), make_modal("https://modal.example", environ=modal_env)],
        generate=["groq", "modal"],
        judge=["modal"],
    )
    checks = pin_check(rotation)
    assert set(checks) == {"groq"}
    assert checks["groq"].model_listed is None
    assert rotation.is_pinned("groq")  # a 429 on the models list decides nothing
    assert rotation.is_pinned("modal")
    assert not modal_route.called


@respx.mock
def test_pin_check_unpins_a_missing_model() -> None:
    respx.get(f"{GROQ}/models").mock(return_value=_models(["qwen/qwen3-32b"]))
    rotation = ProviderRotation([make_groq(environ=ENV)])
    pin_check(rotation)
    assert not rotation.is_pinned("groq")
    respx.get(f"{GROQ}/models").mock(return_value=_models(["qwen/qwen3.8-27b"]))
    respx.post(f"{GROQ}/chat/completions").mock(return_value=_completion("Paris."))
    pin_check(rotation)
    assert rotation.is_pinned("groq")


def test_maintenance_runs_tasks_until_stopped() -> None:
    ran = threading.Event()
    failed: list[int] = []

    def boom() -> None:
        failed.append(1)
        raise RuntimeError

    maintenance = _Maintenance([("tick", 0.01, ran.set), ("boom", 0.01, boom)])
    maintenance.start()
    assert ran.wait(5)
    maintenance.stop()
    assert failed


# ---------------------------------------------------------------------- Modal KV loading


def tiny_tokenizer() -> Tokenizer:
    words = ["system", "user", "assistant", "hello", "there", "<think>", "</think>"]
    vocab = {"[UNK]": 0, "<|im_start|>": 1, "<|im_end|>": 2, "<|endoftext|>": 3}
    vocab |= {w: i for i, w in enumerate(words, start=4)}
    tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))  # noqa: S106
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.add_special_tokens(["<|im_start|>", "<|im_end|>", "<|endoftext|>"])
    return tokenizer


def test_qwen_chat_tokenizer_renders_the_thinking_off_prompt() -> None:
    chat = QwenChatTokenizer(tiny_tokenizer())
    assert chat.eos == (2, 3)
    messages = [{"role": "system", "content": "hello"}, {"role": "user", "content": "there"}]
    assert chat.render(messages) == (
        "<|im_start|>system\nhello<|im_end|>\n<|im_start|>user\nthere<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    ids = chat.apply_chat_template(messages, enable_thinking=False)["input_ids"]
    assert ids[0] == 1
    assert ids.count(2) == 2
    assert chat("hello there")["input_ids"] == [7, 8]
    assert chat.decode([7, 2, 8]) == "hello there"
    with pytest.raises(ValueError, match="thinking-off"):
        chat.apply_chat_template(messages, enable_thinking=True)
    with pytest.raises(ValueError, match="role"):
        chat.render([{"role": "tool", "content": "x"}])
    bare = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))  # noqa: S106
    with pytest.raises(ValueError, match="Qwen chat tokens"):
        QwenChatTokenizer(bare)


def test_mask_processor_and_tokenizer_files(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_mask_processor({})
    empty = tmp_path / "empty.txt"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_mask_processor({"MODAL_MASK_PROCESSOR": str(empty)})
    mask = tmp_path / "mask_processor.txt"
    mask.write_text('{"callable": "x"}\n', encoding="utf-8")
    assert load_mask_processor({"MODAL_MASK_PROCESSOR": str(mask)}) == '{"callable": "x"}'
    tok = tmp_path / "tokenizer.json"
    tok.write_text(tiny_tokenizer().to_str(), encoding="utf-8")
    assert load_tokenizer({"MODAL_TOKENIZER_JSON": str(tok)}).eos == (2, 3)


@respx.mock
def test_tokenizer_downloads_at_the_pinned_revision() -> None:
    url = (
        f"https://huggingface.co/{JUDGE_PRESET.model}/resolve/{JUDGE_PRESET.revision}/"
        "tokenizer.json"
    )
    respx.get(url).mock(return_value=httpx.Response(200, text=tiny_tokenizer().to_str()))
    with httpx.Client() as client:
        assert load_tokenizer({}, client=client).eos == (2, 3)


def test_load_modal_kv(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    modal_env = {"MODAL_API_KEY": "mk"}
    rotation = ProviderRotation(
        [make_modal("https://modal.example", environ=modal_env)],
        generate=["modal"],
        judge=["modal"],
    )
    root = tmp_path
    s = settings(root, modal_url="https://modal.example/v1")
    assert load_modal_kv(rotation, s, {}) is None
    assert any("mask processor not loaded" in r.getMessage() for r in caplog.records)
    mask = tmp_path / "mask.txt"
    mask.write_text("serialised", encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    env = {"MODAL_MASK_PROCESSOR": str(mask), "MODAL_TOKENIZER_JSON": str(bad)}
    assert load_modal_kv(rotation, s, env) is None
    assert any("tokenizer not loaded" in r.getMessage() for r in caplog.records)
    tok = tmp_path / "tokenizer.json"
    tok.write_text(tiny_tokenizer().to_str(), encoding="utf-8")
    env["MODAL_TOKENIZER_JSON"] = str(tok)
    kv = load_modal_kv(rotation, s, env)
    assert kv is not None
    backend = kv.make_backend()
    assert isinstance(backend, ModalSGLangBackend)
    assert backend.url == "https://modal.example"
    assert backend.processor() == "serialised"
    assert backend.eos == frozenset({2, 3})
    assert load_modal_kv(ProviderRotation([make_groq(environ=ENV)]), s, env) is None


@respx.mock
def test_modal_probe_failure_reads_as_cold(tmp_path: Path) -> None:
    respx.get("https://modal.example/health").mock(return_value=httpx.Response(401))
    rotation = ProviderRotation(
        [make_modal("https://modal.example", environ={"MODAL_API_KEY": "k"})]
    )
    mask = tmp_path / "mask.txt"
    mask.write_text("serialised", encoding="utf-8")
    tok = tmp_path / "tokenizer.json"
    tok.write_text(tiny_tokenizer().to_str(), encoding="utf-8")
    env = {"MODAL_MASK_PROCESSOR": str(mask), "MODAL_TOKENIZER_JSON": str(tok)}
    kv = load_modal_kv(rotation, settings(tmp_path, modal_url="https://modal.example"), env)
    assert kv is not None
    assert kv.probe() is False


# ---------------------------------------------------------------------- Modal KV metering


class FakePost:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(body)
        if self.error is not None:
            raise self.error
        return {"output_ids": [5, 6, 7], "meta_info": {"finish_reason": {"type": "length"}}}


BODY = {"input_ids": [1, 2, 3, 4], "sampling_params": {"max_new_tokens": 3}}


def modal_rotation(ledger: Any = None, **kwargs: Any) -> ProviderRotation:
    return ProviderRotation(
        [make_modal("https://modal.example", environ={"MODAL_API_KEY": "k"})],
        generate=["modal"],
        judge=["modal"],
        ledger=ledger,
        **kwargs,
    )


def test_metered_post_reserves_and_records_against_the_modal_ledger() -> None:
    ledger = InMemoryLedger()
    post = FakePost()
    metered = MeteredPost(post, modal_rotation(ledger))
    assert metered(dict(BODY))["output_ids"] == [5, 6, 7]
    snapshot = ledger.snapshot()["modal"]
    assert snapshot["usd_month"] > 0  # a cold start and its tail, as the ledger models it
    assert snapshot["pending_usd"] == 0
    assert len(post.bodies) == 1


def test_metered_post_refused_reservation_means_modal_unavailable() -> None:
    ledger = InMemoryLedger(modal=ModalLimits(usd_per_month=0.01))
    post = FakePost()
    rotation = modal_rotation(ledger)
    with pytest.raises(AllProvidersUnavailable) as caught:
        MeteredPost(post, rotation)(dict(BODY))
    assert caught.value.failures[0].kind == "quota"
    assert post.bodies == []
    assert rotation.breaker("modal").state == "closed"


def test_metered_post_respects_and_feeds_the_breaker() -> None:
    rotation = modal_rotation(InMemoryLedger(), failure_threshold=2)
    failing = FakePost(AllProvidersUnavailable([ProviderUnavailable("modal", "server")]))
    metered = MeteredPost(failing, rotation)
    for _ in range(2):
        with pytest.raises(AllProvidersUnavailable):
            metered(dict(BODY))
    assert rotation.breaker("modal").state == "open"
    with pytest.raises(AllProvidersUnavailable) as caught:
        metered(dict(BODY))
    assert "circuit open" in caught.value.failures[0].detail
    assert len(failing.bodies) == 2  # the open circuit sent nothing
    limited = modal_rotation(InMemoryLedger())
    with pytest.raises(AllProvidersUnavailable):
        MeteredPost(
            FakePost(AllProvidersUnavailable([ProviderUnavailable("modal", "rate_limit", 30.0)])),
            limited,
        )(dict(BODY))
    assert limited.breaker("modal").state == "open"
    unpinned = modal_rotation(InMemoryLedger())
    unpinned.set_pinned("modal", pinned=False)
    with pytest.raises(AllProvidersUnavailable) as caught:
        MeteredPost(FakePost(), unpinned)(dict(BODY))
    assert caught.value.failures[0].kind == "model_changed"
    broken = modal_rotation(InMemoryLedger())
    with pytest.raises(AllProvidersUnavailable) as caught:
        MeteredPost(FakePost(RuntimeError("bug")), broken)(dict(BODY))
    assert caught.value.failures[0].kind == "server"


def test_kv_path_over_the_cap_answers_extractive(
    fixture_router: Router, fixture_text_store: TextStore
) -> None:
    rotation = FakeRotation(names=("modal",), provider="modal")
    capped = modal_rotation(InMemoryLedger(modal=ModalLimits(usd_per_month=0.01)))
    post = FakePost()

    def backend() -> ModalSGLangBackend:
        return ModalSGLangBackend(
            "https://modal.example",
            QwenChatTokenizer(tiny_tokenizer()),
            model_id="m",
            eos=(2, 3),
            post=MeteredPost(post, capped),
        )

    pipeline = Pipeline(
        router=fixture_router,
        store=fixture_text_store,
        rotation=rotation,
        policy=POLICY,
        modal_kv=ModalKV(make_backend=backend, probe=lambda: True),
    )
    seen: list[Any] = []
    outcome = pipeline.run(AskRequest(query=S124_QUERY), seen.append)
    assert [e.type for e in seen] == ["route", "sources", "extractive", "receipt", "done"]
    assert outcome.failures == ["modal:quota"]
    assert post.bodies == []


def test_load_modal_kv_meters_its_backend(tmp_path: Path) -> None:
    rotation = modal_rotation(InMemoryLedger())
    mask = tmp_path / "mask.txt"
    mask.write_text("serialised", encoding="utf-8")
    tok = tmp_path / "tokenizer.json"
    tok.write_text(tiny_tokenizer().to_str(), encoding="utf-8")
    env = {"MODAL_MASK_PROCESSOR": str(mask), "MODAL_TOKENIZER_JSON": str(tok)}
    kv = load_modal_kv(rotation, settings(tmp_path, modal_url="https://modal.example"), env)
    assert kv is not None
    post = kv.make_backend()._post
    assert isinstance(post, MeteredPost)
    assert post.rotation is rotation


# ---------------------------------------------------------------------------- daily cap


def test_daily_cap_41st_answer_is_extractive_with_notice(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    """R5: app-level test that the 41st generated answer is extractive with the notice."""
    cap_store = InMemoryDailyCap()
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 40)
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        rotation,
        limiter=TokenBucket(per_minute=6000, burst=1000),
        daily_caps=cap_store,
        daily_ask_cap=40,
        cap_salt="salt-secret",
    )
    with TestClient(create_app(rt)) as client:
        # Answers 1 through 40 are generated
        for i in range(40):
            res = ask(client, S124_QUERY)
            assert res.status_code == 200, f"Request {i + 1} failed"
            events = parse_sse(res.content)
            assert any(k == "sentence" for k, _, _ in events), f"Request {i + 1} not generated"
            receipt = next(d for k, d, _ in events if k == "receipt")
            assert receipt["fact"]["mode"] == "generated"

        # Today's count is now 40 (at cap)
        today = datetime.now(UTC).date()
        key = client_key_hash("testclient", "salt-secret")
        assert cap_store.get(key, today) == 40

        # The 41st generated answer is extractive with the notice (over cap)
        res_41 = ask(client, S124_QUERY)
        assert res_41.status_code == 200
        events_41 = parse_sse(res_41.content)
        event_types = [k for k, _, _ in events_41]
        assert event_types == ["route", "sources", "notice", "extractive", "receipt", "done"]

        notice = next(d for k, d, _ in events_41 if k == "notice")
        assert notice["code"] == "daily_cap"
        assert notice["message"] == (
            "Daily limit reached for generated answers. The provision text is shown instead. "
            "It resets at 00:00 UTC."
        )

        extractive = next(d for k, d, _ in events_41 if k == "extractive")
        assert extractive["type"] == "extractive"

        receipt_41 = next(d for k, d, _ in events_41 if k == "receipt")
        assert receipt_41["fact"]["mode"] == "extractive"

        # Count did NOT increment for the extractive answer over cap
        assert cap_store.get(key, today) == 40


def test_daily_cap_refusals_and_clarifications_not_counted(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    """R1, R5: refusals and clarifications do not count against the daily cap."""
    cap_store = InMemoryDailyCap()
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 2)
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        rotation,
        limiter=TokenBucket(per_minute=6000, burst=1000),
        daily_caps=cap_store,
        daily_ask_cap=2,
        cap_salt="salt-secret",
    )
    with TestClient(create_app(rt)) as client:
        today = datetime.now(UTC).date()
        key = client_key_hash("testclient", "salt-secret")

        # 1. Refusal (unknown statute)
        res_refusal = ask(client, MARCHWOOD_QUERY)
        assert res_refusal.status_code == 200
        events_refusal = parse_sse(res_refusal.content)
        assert events_refusal[1][0] == "refusal"
        assert cap_store.get(key, today) == 0

        # 2. Clarification (ambiguous)
        res_clarify = ask(client, AMBIGUOUS_QUERY)
        assert res_clarify.status_code == 200
        events_clarify = parse_sse(res_clarify.content)
        assert events_clarify[1][0] == "clarify"
        assert cap_store.get(key, today) == 0

        # 3. Two generated answers (cap is 2)
        res_1 = ask(client, S124_QUERY)
        assert res_1.status_code == 200
        assert cap_store.get(key, today) == 1

        res_2 = ask(client, S124_QUERY)
        assert res_2.status_code == 200
        assert cap_store.get(key, today) == 2

        # 4. Third question is over cap -> extractive with daily_cap notice
        res_3 = ask(client, S124_QUERY)
        assert res_3.status_code == 200
        events_3 = parse_sse(res_3.content)
        assert any(k == "notice" and d["code"] == "daily_cap" for k, d, _ in events_3)
        assert any(k == "extractive" for k, _, _ in events_3)
        assert cap_store.get(key, today) == 2

        # 5. Even when over cap, a refusal question still returns the refusal, never an error
        res_over_refusal = ask(client, MARCHWOOD_QUERY)
        assert res_over_refusal.status_code == 200
        events_over_refusal = parse_sse(res_over_refusal.content)
        assert events_over_refusal[1][0] == "refusal"
        assert cap_store.get(key, today) == 2


def test_daily_cap_salt_and_client_key_isolation(
    fixture_data_root: Path, spy: SpyRouter, fixture_text_store: TextStore
) -> None:
    """R2: salt from env, raw IP is never stored, different clients have separate caps."""
    cap_store = InMemoryDailyCap()
    rotation = FakeRotation([(f"{GOOD1} {GOOD2}", "stop")] * 2)
    rt = runtime(
        fixture_data_root,
        spy,
        fixture_text_store,
        rotation,
        limiter=TokenBucket(per_minute=6000, burst=1000),
        daily_caps=cap_store,
        daily_ask_cap=1,
        cap_salt="custom-secret-salt",
        require_access=True,
    )
    with TestClient(create_app(rt)) as client:
        today = datetime.now(UTC).date()
        ip_a = "203.0.113.10"
        ip_b = "203.0.113.20"
        headers_a = {**ACCESS, "X-CV-Client-IP": ip_a}
        headers_b = {**ACCESS, "X-CV-Client-IP": ip_b}

        key_a = client_key_hash(ip_a, "custom-secret-salt")
        key_b = client_key_hash(ip_b, "custom-secret-salt")

        # Client A asks 1st question -> generated
        res_a1 = ask(client, S124_QUERY, headers=headers_a)
        assert res_a1.status_code == 200
        assert cap_store.get(key_a, today) == 1
        assert cap_store.get(key_b, today) == 0

        # Client A asks 2nd question -> over cap (cap=1)
        res_a2 = ask(client, S124_QUERY, headers=headers_a)
        assert res_a2.status_code == 200
        events_a2 = parse_sse(res_a2.content)
        assert any(k == "notice" and d["code"] == "daily_cap" for k, d, _ in events_a2)

        # Client B asks 1st question -> generated (independent client cap)
        res_b1 = ask(client, S124_QUERY, headers=headers_b)
        assert res_b1.status_code == 200
        events_b1 = parse_sse(res_b1.content)
        assert any(k == "sentence" for k, _, _ in events_b1)
        assert cap_store.get(key_b, today) == 1
