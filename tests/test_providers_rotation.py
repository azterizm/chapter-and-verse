"""Rotation order and fallthrough, its configuration, the judge bridge, pinning and presets."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest
import respx
from legal_rag_verifier.judge import PRESETS, Chat, Judge

from chapterverse.config import ConfigError, Settings
from chapterverse.contracts import (
    AllProvidersUnavailable,
    ChatMessage,
    ChatRequest,
    ChatResult,
    JudgeChat,
    ProviderUnavailable,
    Rotation,
)
from chapterverse.providers import RotationJudgeChat
from chapterverse.providers.ledger import InMemoryLedger
from chapterverse.providers.openai_compat import ProviderWarming, make_cloudflare, make_groq
from chapterverse.providers.pinning import (
    CANARY_PREFILL,
    PinCheck,
    apply_checks,
    check_provider,
    prefill_continued,
    prefill_ok,
    run_daily_check,
    thinking_was_off,
)
from chapterverse.providers.presets import (
    JUDGE_PRESET,
    MEASURED,
    REJECTED_PARAMS,
    SHORT_CARD,
    SYNTHESIS_CARD,
    describe,
    generation_request,
    judge_request,
    request_settings,
)
from chapterverse.providers.rotation import ProviderRotation, RotationConfig

ACCOUNT = "0123456789abcdef0123456789abcdef"
ENV = {"GROQ_API_KEY": "gsk_TESTSECRET", "CLOUDFLARE_AI_WORKER_TOKEN": "cf-TESTSECRET"}
GROQ = "https://api.groq.com/openai/v1"
CF = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai"


class Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def prefill_on(provider: Any) -> bool:
    """Read through a call so mypy does not narrow the property across assertions."""
    return bool(provider.supports_prefill)


class Fake:
    """A ChatProvider that replies or raises from a script."""

    def __init__(self, name: str, *outcomes: str | ProviderUnavailable | Exception) -> None:
        self.name = name
        self.model_id = f"{name}-model"
        self.quantisation: str | None = None
        self.supports_prefill = True
        self.warming = False
        self.outcomes = list(outcomes) or ["ok"]
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, req: ChatRequest) -> ChatResult:
        with self._lock:
            self.calls += 1
            outcome = self.outcomes[0] if len(self.outcomes) == 1 else self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ChatResult(
            text=outcome,
            finish_reason="stop",
            provider=self.name,
            model_id=self.model_id,
            quantisation=None,
            prompt_tokens=1,
            completion_tokens=2,
            latency_ms=3.0,
        )


def req(purpose: Any = "generate") -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage(role="user", content="q"),),
        max_tokens=8,
        temperature=0.7,
        top_p=0.8,
        purpose=purpose,
    )


# ---------------------------------------------------------------------------- order


def test_default_orders_and_protocol() -> None:
    rot = ProviderRotation([Fake("groq"), Fake("cloudflare"), Fake("modal")])
    assert isinstance(rot, Rotation)
    assert rot.order("generate") == ("groq", "cloudflare", "modal")
    assert rot.order("judge") == ("modal",)
    assert rot.complete(req()).provider == "groq"
    assert rot.complete(req("judge")).provider == "modal"
    assert rot.available() == ["groq", "cloudflare", "modal"]
    assert rot.available("judge") == ["modal"]
    assert "TESTSECRET" not in repr(rot)


def test_fallthrough_collects_every_failure() -> None:
    quota = ProviderUnavailable("groq", "quota", retry_after_s=30.0)
    server = ProviderUnavailable("cloudflare", "server", detail="HTTP 500")
    rot = ProviderRotation([Fake("groq", quota), Fake("cloudflare", server), Fake("modal")])
    assert rot.complete(req()).provider == "modal"
    rot2 = ProviderRotation([Fake("groq", quota), Fake("cloudflare", server)], judge=["cloudflare"])
    with pytest.raises(AllProvidersUnavailable) as caught:
        rot2.complete(req())
    first = caught.value.failures
    assert first[0] is quota
    assert first[1] is server
    with pytest.raises(AllProvidersUnavailable) as caught:
        rot2.complete(req("judge"))
    assert [f.provider for f in caught.value.failures] == ["cloudflare"]


def test_no_judge_provider_is_all_unavailable() -> None:
    rot = ProviderRotation([Fake("groq")])
    with pytest.raises(AllProvidersUnavailable) as caught:
        rot.complete(req("judge"))
    assert caught.value.failures == ()


def test_rate_limit_trips_breaker_for_retry_after() -> None:
    clock = Clock()
    groq = Fake("groq", ProviderUnavailable("groq", "rate_limit", retry_after_s=20.0), "ok")
    rot = ProviderRotation([groq, Fake("cloudflare")], clock=clock)
    assert rot.complete(req()).provider == "cloudflare"
    assert rot.breaker("groq").state == "open"
    assert rot.available() == ["cloudflare"]
    assert rot.complete(req()).provider == "cloudflare"
    assert groq.calls == 1  # skipped while open
    clock.now = 20.0
    assert rot.complete(req()).provider == "groq"  # half-open probe succeeds
    assert rot.breaker("groq").state == "closed"


def test_circuit_open_failure_reports_last_kind_and_wait() -> None:
    clock = Clock()
    groq = Fake("groq", ProviderUnavailable("groq", "rate_limit", retry_after_s=20.0))
    rot = ProviderRotation([groq], clock=clock)
    with pytest.raises(AllProvidersUnavailable):
        rot.complete(req())
    clock.now = 5.0
    with pytest.raises(AllProvidersUnavailable) as caught:
        rot.complete(req())
    failure = caught.value.failures[0]
    assert (failure.kind, failure.detail) == ("rate_limit", "circuit open")
    assert failure.retry_after_s == pytest.approx(15.0)
    assert caught.value.retry_after_s == pytest.approx(15.0)


def test_server_failures_open_after_threshold() -> None:
    clock = Clock()
    groq = Fake("groq", ProviderUnavailable("groq", "server"))
    rot = ProviderRotation([groq, Fake("modal")], failure_threshold=2, cooldown_s=10, clock=clock)
    for _ in range(4):
        assert rot.complete(req()).provider == "modal"
    assert groq.calls == 2
    assert rot.breaker("groq").state == "open"


def test_local_quota_refusal_does_not_trip() -> None:
    groq = Fake("groq", ProviderUnavailable("groq", "quota", detail="local quota ledger refused"))
    rot = ProviderRotation([groq, Fake("modal")])
    for _ in range(5):
        rot.complete(req())
    assert rot.breaker("groq").state == "closed"


def test_ledger_refusal_skips_provider() -> None:
    from chapterverse.providers.ledger import CallUsage  # noqa: PLC0415

    ledger = InMemoryLedger(clock=Clock(1e6))
    ledger.record(CallUsage(provider="groq", est_tokens=0, block_s=60.0))
    groq = Fake("groq")
    rot = ProviderRotation([groq, Fake("modal")], ledger=ledger)
    assert rot.complete(req()).provider == "modal"
    assert groq.calls == 0
    assert rot.available() == ["modal"]


def test_model_changed_unpins_until_pin_check() -> None:
    groq = Fake("groq", ProviderUnavailable("groq", "model_changed"), "ok")
    rot = ProviderRotation([groq, Fake("modal")])
    assert rot.complete(req()).provider == "modal"
    assert not rot.is_pinned("groq")
    assert rot.complete(req()).provider == "modal"
    assert groq.calls == 1
    assert "groq" not in rot.available()
    rot.set_pinned("groq", pinned=True)
    assert rot.complete(req()).provider == "groq"
    with pytest.raises(KeyError):
        rot.set_pinned("nope", pinned=True)


def test_warming_is_skipped_without_tripping() -> None:
    modal = Fake("modal", ProviderWarming("modal"))
    rot = ProviderRotation([modal], judge=["modal"], failure_threshold=1)
    for _ in range(3):
        with pytest.raises(AllProvidersUnavailable) as caught:
            rot.complete(req("judge"))
        assert caught.value.failures[0].kind == "timeout"
    assert modal.calls == 3
    assert rot.breaker("modal").state == "closed"
    modal.warming = True
    assert rot.warming() == ["modal"]
    assert rot.available() == []


def test_unexpected_exception_becomes_server_failure() -> None:
    rot = ProviderRotation([Fake("groq", RuntimeError("bug")), Fake("modal")])
    assert rot.complete(req()).provider == "modal"
    with pytest.raises(AllProvidersUnavailable) as caught:
        ProviderRotation([Fake("groq", KeyError("x"))]).complete(req())
    assert caught.value.failures[0].kind == "server"
    assert "KeyError" in caught.value.failures[0].detail


def test_bad_orders_rejected() -> None:
    with pytest.raises(ValueError, match="unknown providers"):
        ProviderRotation([Fake("groq")], generate=["modal"])
    with pytest.raises(ValueError, match="twice"):
        ProviderRotation([Fake("groq"), Fake("groq")])


def test_rotation_thread_safety_smoke() -> None:
    script: list[str | ProviderUnavailable | Exception] = [
        ProviderUnavailable("groq", "server"),
        "ok",
    ] * 200
    flaky = Fake("groq", *script, "ok")
    rot = ProviderRotation([flaky, Fake("modal")], failure_threshold=1000)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(50):
            provider = rot.complete(req()).provider
            with lock:
                results.append(provider)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 400
    assert set(results) <= {"groq", "modal"}


# ---------------------------------------------------------------------------- config


def test_config_defaults_and_skips() -> None:
    cfg = RotationConfig.from_env({"GROQ_API_KEY": "k"})
    assert cfg.generate == ("groq",)
    assert cfg.judge == ()
    assert dict(cfg.skipped) == {
        "cloudflare": "CLOUDFLARE_ACCOUNT_ID is not set",
        "modal": "MODAL_URL is not set",
    }
    full = {
        **ENV,
        "CLOUDFLARE_ACCOUNT_ID": ACCOUNT,
        "MODAL_URL": "https://x.modal.run",
        "MODAL_API_KEY": "m",
    }
    cfg = RotationConfig.from_env(full)
    assert cfg.generate == ("groq", "cloudflare", "modal")
    assert cfg.judge == ("modal",)
    assert cfg.skipped == ()
    assert ACCOUNT not in repr(cfg)
    no_key = RotationConfig.from_env({**full, "MODAL_API_KEY": " "})
    assert dict(no_key.skipped) == {"modal": "MODAL_API_KEY is not set"}
    cf_no_token = RotationConfig.from_env({**full, "CLOUDFLARE_AI_WORKER_TOKEN": ""})
    assert "cloudflare" not in cf_no_token.generate


def test_config_orders_from_env() -> None:
    env = {
        **ENV,
        "CLOUDFLARE_ACCOUNT_ID": ACCOUNT,
        "GEN_PROVIDERS": "cloudflare, groq",
        "JUDGE_PROVIDERS": "groq,cloudflare",
    }
    cfg = RotationConfig.from_env(env)
    assert cfg.generate == ("cloudflare", "groq")
    assert cfg.judge == ("groq", "cloudflare")
    assert cfg.providers == ("cloudflare", "groq")
    with pytest.raises(ConfigError, match="unknown provider"):
        RotationConfig.from_env({"GEN_PROVIDERS": "openai"})
    with pytest.raises(ConfigError, match="twice"):
        RotationConfig.from_env({"JUDGE_PROVIDERS": "modal,modal"})


def test_config_from_settings() -> None:
    from pathlib import Path  # noqa: PLC0415

    settings = Settings(
        legal_data_root=None,
        router_index=Path("i"),
        concepts_index=Path("c"),
        cloudflare_account_id=ACCOUNT,
        modal_url=None,
    )
    cfg = RotationConfig.from_env(ENV, settings=settings)
    assert cfg.generate == ("groq", "cloudflare")
    assert cfg.cloudflare_account_id == ACCOUNT


@respx.mock
def test_from_config_end_to_end_falls_through_to_cloudflare() -> None:
    env = {**ENV, "CLOUDFLARE_ACCOUNT_ID": ACCOUNT}
    cfg = RotationConfig.from_env(env)
    rot = ProviderRotation.from_config(cfg, environ=env, sleep=lambda _s: None)
    respx.post(f"{GROQ}/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached on requests per minute (RPM)"}},
            headers={"retry-after": "9"},
        )
    )
    respx.post(f"{CF}/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Paris."}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "neurons": 1.0},
            },
        )
    )
    result = rot.complete(
        generation_request([{"role": "user", "content": "q"}], mode="short", max_tokens=16)
    )
    assert result.provider == "cloudflare"
    assert rot.breaker("groq").state == "open"
    assert isinstance(rot.ledger, InMemoryLedger)
    snap = rot.ledger.snapshot()
    assert snap["groq"]["requests_day"] == 1
    assert snap["cloudflare"]["neurons_day"] == 1.0


# ---------------------------------------------------------------------------- judge bridge


def test_judge_chat_satisfies_protocols_and_records_calls() -> None:
    reply = '{"label":"supported","passage":1}'
    rot = ProviderRotation([Fake("modal", reply)])
    chat = RotationJudgeChat(rot)
    assert isinstance(chat, JudgeChat)
    as_chat: Chat = chat
    assert callable(as_chat)
    assert chat.model == f"{PRESETS['qwen27-ar4'].model}@{PRESETS['qwen27-ar4'].revision}"
    judge = Judge(chat)
    assert judge.model_id == chat.model
    probs = judge.score(["The cap is £1."], "The cap is £1.")
    assert probs[0].entailment == 1.0
    assert judge.calls[0]["label"] == "supported"
    assert [(c.provider, c.purpose) for c in chat.calls] == [("modal", "judge")]
    out = chat([{"role": "user", "content": "x"}])
    assert out == {"content": reply, "ms": 3.0, "tokens": 2, "finish": "stop"}


def test_judge_sends_judge_purpose_with_preset_sampling() -> None:
    seen: list[ChatRequest] = []

    class Spy(Fake):
        def complete(self, req: ChatRequest) -> ChatResult:
            seen.append(req)
            return super().complete(req)

    chat = RotationJudgeChat(ProviderRotation([Spy("modal", "{}")]))
    chat([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    sent = seen[0]
    assert sent.purpose == "judge"
    assert sent.mode == "short"
    sampling = PRESETS["qwen27-ar4"].sampling
    assert (sent.temperature, sent.top_p, sent.max_tokens, sent.seed) == (
        sampling["temperature"],
        sampling["top_p"],
        sampling["max_tokens"],
        sampling["seed"],
    )


def test_real_judge_reraises_all_providers_unavailable() -> None:
    down = ProviderRotation(
        [Fake("modal", ProviderUnavailable("modal", "server", detail="HTTP 500"))]
    )
    chat = RotationJudgeChat(down)
    judge = Judge(chat)
    with pytest.raises(AllProvidersUnavailable) as caught:
        judge.score(["The cap is £1."], "The cap is £1.")
    assert caught.value.failures[0].provider == "modal"
    assert judge.calls == []  # not scored "not stated"
    assert chat.calls == []


def test_judge_chat_rejects_unknown_role() -> None:
    chat = RotationJudgeChat(ProviderRotation([Fake("modal")]))
    with pytest.raises(ValueError, match="role"):
        chat([{"role": "tool", "content": "x"}])


# ---------------------------------------------------------------------------- pinning


def _completion(content: str, prompt_tokens: int = 20, **message: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content, **message}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 3},
        },
    )


def _is_prefill(request: httpx.Request) -> bool:
    import json  # noqa: PLC0415

    return CANARY_PREFILL in json.loads(request.content)["messages"][-1]["content"]


@respx.mock
def test_pin_check_passes_and_applies() -> None:
    respx.get(f"{GROQ}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen/qwen3.8-27b"}]})
    )
    respx.post(f"{GROQ}/chat/completions").mock(
        side_effect=lambda r: _completion("Paris." if _is_prefill(r) else "Paris is the capital.")
    )
    groq = make_groq(environ=ENV)
    assert not prefill_on(groq)
    check = check_provider(groq, wall_clock=lambda: 0.0)
    assert check.model_listed
    assert check.thinking_off_ok
    assert check.prefill_ok
    assert check.pinned
    assert check.checked_at == "1970-01-01T00:00:00+00:00"
    rot = ProviderRotation([groq])
    apply_checks(rot, [check])
    assert prefill_on(groq)  # a passed canary turns Groq's native prefill on
    assert rot.is_pinned("groq")


@respx.mock
def test_closed_turn_signature_fails_prefill_canary() -> None:
    """The live Groq result of 2026-11-19: a terse "Paris" but a closed-turn prompt (48 vs 28)."""
    respx.get(f"{GROQ}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen/qwen3.8-27b"}]})
    )
    respx.post(f"{GROQ}/chat/completions").mock(
        side_effect=lambda r: (
            _completion("Paris", 48)
            if _is_prefill(r)
            else _completion("The capital of France is Paris.", 28)
        )
    )
    groq = make_groq(environ=ENV)
    groq.set_prefill(enabled=True)  # as after an earlier passed canary
    rot = ProviderRotation([groq])
    checks = run_daily_check(rot)
    assert checks["groq"].prefill_ok is False
    assert checks["groq"].pinned  # still in rotation, with the prefix folded into the user turn
    assert not prefill_on(groq)
    assert "prompt 48 vs 28" in checks["groq"].notes[-1]


@respx.mock
def test_pin_check_model_missing_unpins_without_canaries() -> None:
    respx.get(f"{GROQ}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen/qwen3-32b"}]})
    )
    chat = respx.post(f"{GROQ}/chat/completions").mock(return_value=_completion("Paris."))
    groq = make_groq(environ=ENV)
    rot = ProviderRotation([groq])
    checks = run_daily_check(rot)
    assert checks["groq"].model_listed is False
    assert not checks["groq"].pinned
    assert not chat.called
    assert not rot.is_pinned("groq")


@respx.mock
def test_failed_prefill_canary_folds_but_keeps_provider() -> None:
    respx.get(f"{CF}/models/search").mock(
        return_value=httpx.Response(200, json={"result": [{"name": "@cf/qwen/qwen3.8-27b"}]})
    )
    respx.post(f"{CF}/v1/chat/completions").mock(
        side_effect=lambda r: _completion(
            "The capital of France is Paris." if _is_prefill(r) else "Paris."
        )
    )
    cf = make_cloudflare(ACCOUNT, environ=ENV)
    rot = ProviderRotation([cf])
    checks = run_daily_check(rot)
    assert checks["cloudflare"].prefill_ok is False
    assert checks["cloudflare"].pinned
    assert rot.is_pinned("cloudflare")
    assert not prefill_on(cf)
    # the next day's canary re-tests native prefill even though it is off
    respx.post(f"{CF}/v1/chat/completions").mock(
        side_effect=lambda r: _completion(" Paris." if _is_prefill(r) else "Paris.")
    )
    run_daily_check(rot)
    assert prefill_on(cf)


@respx.mock
def test_thinking_canary_failure_unpins() -> None:
    respx.get(f"{GROQ}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen/qwen3.8-27b"}]})
    )
    respx.post(f"{GROQ}/chat/completions").mock(
        return_value=_completion("Paris.", reasoning="Let me think about France...")
    )
    rot = ProviderRotation([make_groq(environ=ENV)])
    checks = run_daily_check(rot)
    assert checks["groq"].thinking_off_ok is False
    assert not rot.is_pinned("groq")


@respx.mock
def test_pin_check_models_error_is_unpinned() -> None:
    respx.get(f"{GROQ}/models").mock(return_value=httpx.Response(503))
    check = check_provider(make_groq(environ=ENV))
    assert check.model_listed is None
    assert not check.pinned
    assert "models list" in check.notes[0]


def test_canary_predicates_and_apply_ignores_unknown() -> None:
    assert prefill_ok(" Paris.", 28, 34)  # Cloudflare, live
    assert not prefill_ok("Paris", 28, 48)  # Groq, live: a closed turn
    assert prefill_ok("Paris", None, 48)
    assert not prefill_ok("The capital of France is Paris.", 28, 34)
    assert prefill_continued(" Paris.")
    assert prefill_continued("Paris")
    assert not prefill_continued("The capital of France is Paris.")
    assert thinking_was_off("Paris.", {}, 8)
    assert not thinking_was_off("Paris.", {}, 44)
    assert not thinking_was_off("<think>x</think>Paris.", {}, 8)
    assert not thinking_was_off("Paris.", {"reasoning_content": "hmm"}, 8)
    rot = ProviderRotation([Fake("groq")])
    apply_checks(rot, [PinCheck("other", "m", "t", True, True, True)])
    apply_checks(rot, [PinCheck("groq", "m", "t", False, None, None)])
    assert not rot.is_pinned("groq")


# ---------------------------------------------------------------------------- presets


def test_judge_sampling_is_the_imported_preset() -> None:
    modal = request_settings("modal", "short", "judge")
    assert dict(modal.sampling) == dict(PRESETS["qwen27-ar4"].sampling)
    assert modal.measured
    assert not modal.thinking
    groq = request_settings("groq", "short", "judge")
    assert set(groq.dropped) == {"top_k", "min_p"}
    assert "top_k" not in groq.sampling
    assert not groq.measured
    assert not request_settings("cloudflare", "short", "judge").measured
    assert JUDGE_PRESET is PRESETS["qwen27-ar4"]
    assert {("modal", "judge")} == MEASURED


def test_generation_cards() -> None:
    assert dict(SHORT_CARD) == {
        "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5
    }  # fmt: skip
    assert dict(SYNTHESIS_CARD) == {
        "temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0
    }  # fmt: skip
    synth = request_settings("cloudflare", "synthesis", "generate")
    assert synth.thinking
    assert dict(synth.sampling) == dict(SYNTHESIS_CARD)
    assert not request_settings("modal", "short", "generate").measured
    assert REJECTED_PARAMS["groq"] == {"top_k", "min_p", "chat_template_kwargs"}
    rows = describe()
    assert len(rows) == 12
    assert sum(r["measured"] for r in rows) == 2  # modal judge, both modes
    req_ = judge_request([{"role": "user", "content": "x"}])
    assert (req_.purpose, req_.seed, req_.max_tokens) == ("judge", 0, 1024)
    gen = generation_request([{"role": "user", "content": "x"}], mode="synthesis", max_tokens=9)
    assert (gen.temperature, gen.top_p, gen.mode) == (1.0, 0.95, "synthesis")
