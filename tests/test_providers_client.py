"""The OpenAI-compatible client and its three adapters, against respx-mocked HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from legal_rag_verifier.judge import PRESETS, OpenAIChat

from chapterverse.contracts import ChatMessage, ChatProvider, ChatRequest, ProviderUnavailable
from chapterverse.providers.ledger import InMemoryLedger
from chapterverse.providers.openai_compat import (
    GEMINI_MODEL,
    VERCEL_MODEL,
    OpenAICompatProvider,
    ProviderWarming,
    make_cloudflare,
    make_gemini,
    make_groq,
    make_modal,
    make_vercel,
    normalise_finish,
    rejoin_continuation,
)
from chapterverse.providers.presets import generation_request, judge_request

GROQ_KEY = "gsk_TESTSECRETabc123"
CF_KEY = "cf-token-TESTSECRET"
MODAL_KEY = "modal-key-TESTSECRET"
ACCOUNT = "0123456789abcdef0123456789abcdef"
ENV = {
    "GROQ_API_KEY": GROQ_KEY,
    "CLOUDFLARE_AI_WORKER_TOKEN": CF_KEY,
    "MODAL_API_KEY": MODAL_KEY,
}
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
CF_BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai"
CF_URL = f"{CF_BASE}/v1/chat/completions"
MODAL = "https://example--sglang-serve.modal.run"
MODAL_URL = f"{MODAL}/v1/chat/completions"
MODAL_HEALTH = f"{MODAL}/health"
MODAL_MODEL = PRESETS["qwen27-ar4"].model

MESSAGES = (
    ChatMessage(role="system", content="Reply in one sentence."),
    ChatMessage(role="user", content="Name the capital of France."),
)


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def reply(
    content: str | None = "Paris.",
    *,
    model: str | None = None,
    finish: str = "stop",
    usage: dict[str, Any] | None = None,
    message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    msg.update(message or {})
    body: dict[str, Any] = {
        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        "usage": usage if usage is not None else {"prompt_tokens": 20, "completion_tokens": 3},
    }
    if model is not None:
        body["model"] = model
    return body


def prefill_on(provider: Any) -> bool:
    """Read through a call so mypy does not narrow the property across assertions."""
    return bool(provider.supports_prefill)


def is_warming(provider: OpenAICompatProvider) -> bool:
    """Read through a call so mypy does not narrow the property across assertions."""
    return provider.warming


def sent(route: respx.Route) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(route.calls.last.request.content)
    return body


def groq(**kwargs: Any) -> OpenAICompatProvider:
    kwargs.setdefault("environ", ENV)
    kwargs.setdefault("sleep", lambda _s: None)
    return make_groq(**kwargs)


def cloudflare(**kwargs: Any) -> OpenAICompatProvider:
    kwargs.setdefault("environ", ENV)
    kwargs.setdefault("sleep", lambda _s: None)
    return make_cloudflare(ACCOUNT, **kwargs)


def modal(**kwargs: Any) -> OpenAICompatProvider:
    kwargs.setdefault("environ", ENV)
    kwargs.setdefault("sleep", lambda _s: None)
    return make_modal(MODAL, **kwargs)


def short(prefill: str | None = None, max_tokens: int = 32) -> ChatRequest:
    return generation_request(MESSAGES, mode="short", max_tokens=max_tokens, prefill=prefill)


# ---------------------------------------------------------------------------- protocol, repr


def test_providers_satisfy_protocol_and_hide_secrets() -> None:
    for provider in (groq(), cloudflare(), modal()):
        assert isinstance(provider, ChatProvider)
        text = repr(provider) + repr(provider.adapter)
        for secret in (GROQ_KEY, CF_KEY, MODAL_KEY, ACCOUNT):
            assert secret not in text
    assert groq().model_id == "qwen/qwen3.8-27b"
    assert cloudflare().model_id == "@cf/qwen/qwen3.8-27b"
    assert modal().model_id == f"{MODAL_MODEL}@{PRESETS['qwen27-ar4'].revision}"
    assert modal().quantisation == PRESETS["qwen27-ar4"].quantisation
    assert groq().quantisation is None
    assert not prefill_on(groq())  # off until the prefill canary passes (amendment)
    assert prefill_on(cloudflare())
    assert not prefill_on(modal())


@respx.mock
def test_missing_key_is_config_and_sends_nothing() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply()))
    provider = make_groq(environ={})
    with pytest.raises(ProviderUnavailable) as caught:
        provider.complete(short())
    assert caught.value.kind == "config"
    assert "GROQ_API_KEY is not set" in caught.value.detail
    assert not route.called


def test_bad_key_value_is_config_without_the_value() -> None:
    provider = make_groq(environ={"GROQ_API_KEY": "bad\nkey"})
    with pytest.raises(ProviderUnavailable) as caught:
        provider.complete(short())
    assert caught.value.kind == "config"
    assert "bad" not in str(caught.value)


# ---------------------------------------------------------------------------- Groq


@respx.mock
def test_groq_body_strips_rejected_and_sets_thinking_off() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply()))
    result = groq().complete(short())
    body = sent(route)
    assert body["model"] == "qwen/qwen3.8-27b"
    for field in ("top_k", "min_p", "chat_template_kwargs"):
        assert field not in body
    assert body["reasoning_effort"] == "none"
    assert "reasoning_format" not in body
    assert body["temperature"] == 0.7
    assert body["top_p"] == 0.8
    assert body["presence_penalty"] == 1.5
    assert body["max_tokens"] == 32
    assert route.calls.last.request.headers["authorization"] == f"Bearer {GROQ_KEY}"
    assert result.text == "Paris."
    assert result.provider == "groq"
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (20, 3)
    assert result.latency_ms >= 0


@respx.mock
def test_groq_judge_and_synthesis_settings() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply("{}")))
    groq().complete(judge_request([dict(m) for m in MESSAGES]))
    body = sent(route)
    assert body["reasoning_effort"] == "none"
    assert body["seed"] == 0
    assert body["max_tokens"] == 1024
    assert "top_k" not in body
    assert "min_p" not in body
    assert body["presence_penalty"] == 1.5

    synth = generation_request(MESSAGES, mode="synthesis", max_tokens=64, stop=("\n\n",))
    groq().complete(synth)
    body = sent(route)
    assert body["reasoning_effort"] == "medium"
    assert body["reasoning_format"] == "parsed"
    assert body["temperature"] == 1.0
    assert body["top_p"] == 0.95
    assert body["presence_penalty"] == 0.0
    assert body["stop"] == ["\n\n"]


@respx.mock
def test_groq_folds_prefill_by_default() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply("Paris.")))
    result = groq().complete(short(prefill="La capitale de la France est"))
    body = sent(route)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "La capitale de la France est" in body["messages"][-1]["content"]
    assert result.text == "Paris."  # folded: the text as returned, no re-join


@respx.mock
def test_groq_native_prefill_once_enabled_is_a_final_assistant_message_and_rejoins() -> None:
    route = respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply("Paris.")))
    provider = groq()
    provider.set_prefill(enabled=True)
    result = provider.complete(short(prefill="La capitale de la France est"))
    body = sent(route)
    assert body["messages"][-1] == {"role": "assistant", "content": "La capitale de la France est"}
    assert "continue_final_message" not in body
    assert body["reasoning_effort"] == "none"
    assert "top_k" not in body
    assert result.text == " Paris."


@pytest.mark.parametrize(
    ("prefill", "continuation", "expected"),
    [
        ("The capital is", "Paris.", " Paris."),
        ("The cap is £1.", "It applies.", " It applies."),
        ("The capital is Paris", ", in France.", ", in France."),
        ("The capital is Paris", ".", "."),
        ("The capital is Paris", "; it is large.", "; it is large."),
        ("It said", "\nNext line.", "\nNext line."),
        ("It said", " already spaced", " already spaced"),
        ("It said", "", ""),
        ("Ends with space ", "Paris", "Paris"),
        ("Ends with newline\n", "Paris", "Paris"),
        ("See section 124", "(1ZA) of the Act.", " (1ZA) of the Act."),
        ("The Act says", '"employer" means', ' "employer" means'),
        ("The rate is 50", "% of pay.", "% of pay."),
        ("France", "'s capital", "'s capital"),
        ("The list", ") ends", ") ends"),
        ("The quote ends", "” here", "” here"),
        ("The fee is", "£10.", " £10."),
        ("", "Paris", "Paris"),
    ],
)
def test_groq_rejoin_edge_cases(prefill: str, continuation: str, expected: str) -> None:
    assert rejoin_continuation(prefill, continuation) == expected


@respx.mock
def test_groq_rate_headers_reach_the_ledger() -> None:
    clock = Clock()
    ledger = InMemoryLedger(clock=clock)
    headers = {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "1m26.4s",
        "x-ratelimit-limit-tokens": "6000",
        "x-ratelimit-remaining-tokens": "5900",
        "x-ratelimit-reset-tokens": "1s",
    }
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply(), headers=headers))
    provider = groq(ledger=ledger)
    provider.complete(short())
    snap = ledger.snapshot()["groq"]
    assert snap["tokens_per_minute"] == 6000
    assert snap["requests_day"] == 1
    assert snap["pending_requests"] == 0
    assert not ledger.available("groq")  # the header said no requests remain
    with pytest.raises(ProviderUnavailable) as caught:
        provider.complete(short())
    assert caught.value.kind == "quota"
    assert caught.value.retry_after_s is None
    clock.now += 87
    assert ledger.available("groq")


def _groq_429(body: dict[str, Any], headers: dict[str, str]) -> ProviderUnavailable:
    with respx.mock:
        respx.post(GROQ_URL).mock(return_value=httpx.Response(429, json=body, headers=headers))
        with pytest.raises(ProviderUnavailable) as caught:
            groq().complete(short())
    return caught.value


def test_groq_429_per_minute_is_rate_limit() -> None:
    err = _groq_429(
        {"error": {"message": "Rate limit reached on tokens per minute (TPM): Limit 8000"}},
        {"retry-after": "7", "x-ratelimit-remaining-requests": "900"},
    )
    assert err.kind == "rate_limit"
    assert err.retry_after_s == 7.0


def test_groq_429_per_day_is_quota() -> None:
    err = _groq_429(
        {"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000"}},
        {"retry-after": "30"},
    )
    assert err.kind == "quota"
    assert err.retry_after_s == 30.0


def test_groq_429_told_apart_by_headers() -> None:
    by_remaining = _groq_429(
        {"error": {"message": "rate limited"}},
        {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m0s"},
    )
    assert by_remaining.kind == "quota"
    assert by_remaining.retry_after_s == 120.0
    by_wait = _groq_429({"error": {"message": "rate limited"}}, {"retry-after": "3600"})
    assert by_wait.kind == "quota"
    short_wait = _groq_429({"error": {"message": "rate limited"}}, {"retry-after": "2"})
    assert short_wait.kind == "rate_limit"
    assert short_wait.retry_after_s == 2.0
    no_headers = _groq_429({}, {})
    assert no_headers.kind == "rate_limit"
    assert no_headers.retry_after_s == 60.0


@pytest.mark.parametrize(
    ("status", "body", "kind"),
    [
        (401, {"error": {"message": "Invalid API Key"}}, "config"),
        (403, {"error": {"message": "Forbidden"}}, "config"),
        (404, {"error": {"message": "Not found"}}, "model_changed"),
        (
            400,
            {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}},
            "model_changed",
        ),
        (400, {"error": {"code": "model_decommissioned", "message": "gone"}}, "model_changed"),
        (400, {"error": {"message": "property 'min_p' is unsupported"}}, "config"),
        (422, {"detail": "bad request"}, "config"),
        (408, {}, "timeout"),
        (500, {}, "server"),
        (502, {}, "server"),
        (503, {"error": {"message": "over capacity"}}, "server"),
        (504, {}, "server"),
        (302, {}, "config"),
    ],
)
def test_groq_status_mapping(status: int, body: dict[str, Any], kind: str) -> None:
    with respx.mock:
        headers = {"location": "https://elsewhere.example/steal"} if status == 302 else {}
        route = respx.post(GROQ_URL).mock(
            return_value=httpx.Response(status, json=body, headers=headers)
        )
        other = respx.route(host="elsewhere.example").mock(return_value=httpx.Response(200))
        with pytest.raises(ProviderUnavailable) as caught:
            groq().complete(short())
        assert not other.called  # no redirect is followed
    assert caught.value.kind == kind
    assert caught.value.provider == "groq"
    # 500, 502 and 504 are re-sent once; nothing else is
    assert route.call_count == (2 if status in {500, 502, 504} else 1)


@respx.mock
def test_server_error_then_success_is_retried() -> None:
    sleeps: list[float] = []
    route = respx.post(GROQ_URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json=reply())]
    )
    result = groq(sleep=sleeps.append, backoff_s=0.25).complete(short())
    assert result.text == "Paris."
    assert route.call_count == 2
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    ("exc", "kind", "calls"),
    [
        (httpx.ReadTimeout, "timeout", 1),
        (httpx.ConnectTimeout, "timeout", 1),
        (httpx.WriteTimeout, "timeout", 1),
        (httpx.ConnectError, "server", 2),
        (httpx.RemoteProtocolError, "server", 2),
    ],
)
@respx.mock
def test_groq_transport_errors(exc: type[Exception], kind: str, calls: int) -> None:
    route = respx.post(GROQ_URL).mock(side_effect=exc)
    with pytest.raises(ProviderUnavailable) as caught:
        groq().complete(short())
    assert caught.value.kind == kind
    assert route.call_count == calls


@respx.mock
def test_detail_never_holds_a_secret() -> None:
    echo = f"Invalid API Key {GROQ_KEY} for account {ACCOUNT}; Bearer {GROQ_KEY}"
    respx.post(GROQ_URL).mock(return_value=httpx.Response(401, json={"error": {"message": echo}}))
    with pytest.raises(ProviderUnavailable) as caught:
        groq().complete(short())
    text = f"{caught.value} {caught.value.detail} {caught.value!r}"
    assert GROQ_KEY not in text
    assert ACCOUNT not in text
    assert "HTTP 401" in caught.value.detail

    respx.post(CF_URL).mock(return_value=httpx.Response(400, text=f"bad {ACCOUNT} {CF_KEY}"))
    with pytest.raises(ProviderUnavailable) as caught:
        cloudflare().complete(short())
    text = f"{caught.value} {caught.value!r}"
    assert ACCOUNT not in text
    assert CF_KEY not in text


@respx.mock
def test_reply_from_another_model_is_model_changed() -> None:
    respx.post(GROQ_URL).mock(
        return_value=httpx.Response(200, json=reply(model="meta-llama/llama-4"))
    )
    with pytest.raises(ProviderUnavailable) as caught:
        groq().complete(short())
    assert caught.value.kind == "model_changed"
    respx.post(GROQ_URL).mock(
        return_value=httpx.Response(200, json=reply(model="qwen/qwen3.8-27b"))
    )
    assert groq().complete(short()).text == "Paris."


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": []},
        {"choices": [{"message": "text"}]},
        {"choices": [{"message": {"content": 5}}]},
    ],
)
@respx.mock
def test_malformed_body_is_server(body: Any) -> None:
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(ProviderUnavailable) as caught:
        groq().complete(short())
    assert caught.value.kind == "server"


@respx.mock
def test_non_json_body_is_server() -> None:
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderUnavailable) as caught:
        groq().complete(short())
    assert caught.value.kind == "server"


@respx.mock
def test_think_tags_and_reasoning_fallback() -> None:
    route = respx.post(GROQ_URL)
    route.mock(return_value=httpx.Response(200, json=reply("<think>hmm</think>\n\nParis.")))
    assert groq().complete(short()).text == "Paris."
    route.mock(
        return_value=httpx.Response(
            200, json=reply("", message={"reasoning_content": "x</think>Paris."})
        )
    )
    assert groq().complete(short()).text == "Paris."
    route.mock(return_value=httpx.Response(200, json=reply(None, finish="length")))
    result = groq().complete(short())
    assert (result.text, result.finish_reason) == ("", "length")


def test_finish_normalisation() -> None:
    assert normalise_finish("stop") == "stop"
    assert normalise_finish("eos") == "stop"
    assert normalise_finish("length") == "length"
    assert normalise_finish("content_filter") == "content_filter"
    assert normalise_finish("tool_calls") == "other"
    assert normalise_finish(None) == "other"


@respx.mock
def test_usage_counts_may_be_missing() -> None:
    respx.post(GROQ_URL).mock(return_value=httpx.Response(200, json=reply(usage={})))
    result = groq().complete(short())
    assert result.prompt_tokens is None
    assert result.completion_tokens is None


# ---------------------------------------------------------------------------- Cloudflare


@respx.mock
def test_cloudflare_body_thinking_and_full_sampling() -> None:
    route = respx.post(CF_URL).mock(return_value=httpx.Response(200, json=reply()))
    cloudflare().complete(short())
    body = sent(route)
    assert body["model"] == "@cf/qwen/qwen3.8-27b"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert (body["top_k"], body["min_p"], body["presence_penalty"]) == (20, 0.0, 1.5)
    assert "reasoning_effort" not in body
    assert route.calls.last.request.headers["authorization"] == f"Bearer {CF_KEY}"

    cloudflare().complete(judge_request([dict(m) for m in MESSAGES]))
    body = sent(route)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["seed"] == 0

    cloudflare().complete(generation_request(MESSAGES, mode="synthesis", max_tokens=64))
    assert sent(route)["chat_template_kwargs"] == {"enable_thinking": True}


@respx.mock
def test_cloudflare_prefill_fields_and_text_as_returned() -> None:
    route = respx.post(CF_URL).mock(return_value=httpx.Response(200, json=reply(" Paris.")))
    result = cloudflare().complete(short(prefill="La capitale de la France est"))
    body = sent(route)
    assert body["continue_final_message"] is True
    assert body["add_generation_prompt"] is False
    assert body["messages"][-1]["role"] == "assistant"
    assert result.text == " Paris."


@respx.mock
def test_cloudflare_folds_newline_terminated_steering_prefill() -> None:
    route = respx.post(CF_URL).mock(return_value=httpx.Response(200, json=reply("Continued.")))
    steering_prefill = "\n\n(Source: [P1] text)\n\n"
    result = cloudflare().complete(short(prefill=steering_prefill))
    body = sent(route)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "continue_final_message" not in body
    assert "add_generation_prompt" not in body
    assert steering_prefill in body["messages"][-1]["content"]
    assert result.text == "Continued."


@respx.mock
def test_cloudflare_neurons_reach_the_ledger() -> None:
    ledger = InMemoryLedger(clock=Clock())
    route = respx.post(CF_URL)
    route.mock(
        return_value=httpx.Response(
            200, json=reply(usage={"prompt_tokens": 28, "completion_tokens": 8, "neurons": 3.47})
        )
    )
    provider = cloudflare(ledger=ledger)
    provider.complete(short())
    assert ledger.snapshot()["cloudflare"]["neurons_day"] == pytest.approx(3.47)
    # header fallback
    route.mock(
        return_value=httpx.Response(
            200, json=reply(usage={"prompt_tokens": 1}), headers={"cf-ai-neurons": "2.5"}
        )
    )
    provider.complete(short())
    assert ledger.snapshot()["cloudflare"]["neurons_day"] == pytest.approx(5.97)
    assert ledger.snapshot()["cloudflare"]["pending_neurons"] == pytest.approx(0.0)


@respx.mock
def test_cloudflare_429_daily_is_quota_until_utc_midnight() -> None:
    noon = 1_763_467_200.0  # 2025-11-18 12:00:00 UTC
    respx.post(CF_URL).mock(
        return_value=httpx.Response(
            429,
            json={
                "success": False,
                "errors": [{"code": 3036, "message": "daily free allocation exceeded"}],
            },
        )
    )
    ledger = InMemoryLedger(clock=Clock(noon))
    with pytest.raises(ProviderUnavailable) as caught:
        cloudflare(wall_clock=lambda: noon, ledger=ledger).complete(short())
    assert caught.value.kind == "quota"
    assert caught.value.retry_after_s == pytest.approx(12 * 3600)
    assert not ledger.available("cloudflare")

    respx.post(CF_URL).mock(return_value=httpx.Response(429, headers={"retry-after": "5"}))
    with pytest.raises(ProviderUnavailable) as caught:
        cloudflare().complete(short())
    assert caught.value.kind == "rate_limit"
    assert caught.value.retry_after_s == 5.0


@respx.mock
def test_models_lists() -> None:
    respx.get("https://api.groq.com/openai/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "qwen/qwen3.8-27b"}, {"id": "llama"}]}
        )
    )
    assert groq().list_models() == ["qwen/qwen3.8-27b", "llama"]
    search = respx.get(f"{CF_BASE}/models/search").mock(
        return_value=httpx.Response(200, json={"result": [{"name": "@cf/qwen/qwen3.8-27b"}]})
    )
    assert cloudflare().list_models() == ["@cf/qwen/qwen3.8-27b"]
    assert search.calls.last.request.url.params["search"] == "qwen3.8-27b"
    respx.get("https://api.groq.com/openai/v1/models").mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderUnavailable) as caught:
        groq().list_models()
    assert caught.value.kind == "config"


# ---------------------------------------------------------------------------- Modal


@respx.mock
def test_modal_judge_body_is_the_measured_request() -> None:
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(MODAL_URL).mock(
        return_value=httpx.Response(200, json=reply('{"label":"supported"}', model=MODAL_MODEL))
    )
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "bad"},
        {"role": "user", "content": "retry"},
    ]
    result = modal().complete(judge_request(messages))
    preset = PRESETS["qwen27-ar4"]
    reference = OpenAIChat(
        MODAL,
        preset.model,
        sampling=dict(preset.sampling),
        template_kwargs=dict(preset.template_kwargs),
    )
    # The body OpenAIChat would send, rebuilt from its documented rule.
    expected = {
        "model": reference.model,
        "messages": [
            {**m, "reasoning_content": ""} if m["role"] == "assistant" else m for m in messages
        ],
        **reference.sampling,
        "chat_template_kwargs": reference.template_kwargs,
    }
    assert sent(route) == expected
    assert route.calls.last.request.headers["authorization"] == f"Bearer {MODAL_KEY}"
    assert result.text == '{"label":"supported"}'
    assert result.model_id.endswith("@" + preset.revision)


@respx.mock
def test_modal_folds_prefill_and_uses_thinking_switch() -> None:
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
    route = respx.post(MODAL_URL).mock(return_value=httpx.Response(200, json=reply("Paris.")))
    provider = modal()
    result = provider.complete(short(prefill="La capitale de la France est"))
    body = sent(route)
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "La capitale de la France est" in body["messages"][-1]["content"]
    assert "continue_final_message" not in body
    assert result.text == "Paris."
    provider.complete(generation_request(MESSAGES, mode="synthesis", max_tokens=64))
    body = sent(route)
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["top_k"] == 20


def test_fold_without_user_message_is_config() -> None:
    req = ChatRequest(
        messages=(ChatMessage(role="system", content="s"),),
        max_tokens=8,
        temperature=0.7,
        top_p=0.8,
        prefill="x",
    )
    with respx.mock:
        respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
        with pytest.raises(ProviderUnavailable) as caught:
            modal().complete(req)
    assert caught.value.kind == "config"


@pytest.mark.parametrize(
    "health",
    [httpx.ConnectError, httpx.ReadTimeout, httpx.Response(503), httpx.Response(303)],
)
@respx.mock
def test_modal_cold_health_probe_raises_warming(health: Any) -> None:
    if isinstance(health, httpx.Response):
        respx.get(MODAL_HEALTH).mock(return_value=health)
    else:
        respx.get(MODAL_HEALTH).mock(side_effect=health)
    chat = respx.post(MODAL_URL).mock(return_value=httpx.Response(200, json=reply()))
    provider = modal()
    with pytest.raises(ProviderWarming) as caught:
        provider.complete(short())
    assert caught.value.kind == "timeout"
    assert isinstance(caught.value, ProviderUnavailable)
    assert provider.warming
    assert provider.warming_for_s is not None
    assert not chat.called


@pytest.mark.parametrize(
    "outcome", [httpx.ConnectError, httpx.ConnectTimeout, httpx.Response(503), httpx.Response(303)]
)
@respx.mock
def test_modal_chat_warmup_errors_are_timeout(outcome: Any) -> None:
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
    if isinstance(outcome, httpx.Response):
        route = respx.post(MODAL_URL).mock(return_value=outcome)
    else:
        route = respx.post(MODAL_URL).mock(side_effect=outcome)
    provider = modal()
    with pytest.raises(ProviderWarming):
        provider.complete(short())
    assert provider.warming
    assert route.call_count == 1


@respx.mock
def test_modal_warm_window_skips_probe_and_success_clears_warming() -> None:
    clock = Clock()
    health = respx.get(MODAL_HEALTH).mock(
        side_effect=[httpx.ConnectError("down"), httpx.Response(200), httpx.Response(200)]
    )
    chat = respx.post(MODAL_URL).mock(return_value=httpx.Response(200, json=reply()))
    provider = modal(clock=clock, warm_window_s=120.0)
    with pytest.raises(ProviderWarming):
        provider.complete(short())
    assert is_warming(provider)
    clock.now += 400
    provider.complete(short())
    assert not is_warming(provider)
    clock.now += 60
    provider.complete(short())  # within the window: no probe
    assert health.call_count == 2
    clock.now += 121
    provider.complete(short())
    assert health.call_count == 3
    assert chat.call_count == 3


@respx.mock
def test_modal_probe_auth_failure_is_config() -> None:
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(401))
    with pytest.raises(ProviderUnavailable) as caught:
        modal().complete(short())
    assert caught.value.kind == "config"
    assert not isinstance(caught.value, ProviderWarming)


@respx.mock
def test_modal_read_timeout_after_probe_is_plain_timeout() -> None:
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
    respx.post(MODAL_URL).mock(side_effect=httpx.ReadTimeout)
    provider = modal()
    with pytest.raises(ProviderUnavailable) as caught:
        provider.complete(short())
    assert caught.value.kind == "timeout"
    assert not isinstance(caught.value, ProviderWarming)


@respx.mock
def test_modal_ledger_refusal_blocks_even_the_probe() -> None:
    from chapterverse.providers.ledger import ModalLimits  # noqa: PLC0415

    health = respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(200))
    ledger = InMemoryLedger(modal=ModalLimits(usd_per_month=0.01), clock=Clock())
    with pytest.raises(ProviderUnavailable) as caught:
        modal(ledger=ledger).complete(short())
    assert caught.value.kind == "quota"
    assert not health.called


def test_set_prefill_cannot_enable_what_the_adapter_lacks() -> None:
    provider = modal()
    provider.set_prefill(enabled=True)
    assert not prefill_on(provider)
    g = groq()
    assert not prefill_on(g)
    g.set_prefill(enabled=True)
    assert prefill_on(g)
    g.set_prefill(enabled=False)
    assert not prefill_on(g)
    c = cloudflare()
    c.set_prefill(enabled=False)
    assert not prefill_on(c)


def test_short_mode_gets_short_timeout_and_synthesis_keeps_long_timeout() -> None:
    g = groq()
    cf = cloudflare()
    m = modal()

    short_gen = ChatRequest(
        MESSAGES, max_tokens=32, temperature=0.7, top_p=0.8, mode="short", purpose="generate"
    )
    short_judge = ChatRequest(
        MESSAGES, max_tokens=32, temperature=0.7, top_p=0.8, mode="short", purpose="judge"
    )
    synth_gen = ChatRequest(
        MESSAGES, max_tokens=32, temperature=1.0, top_p=0.95, mode="synthesis", purpose="generate"
    )

    # Groq: default 12s for short mode, 60s for synthesis
    assert g.timeout_for(short_gen).read == 12.0
    assert g.timeout_for(short_judge).read == 12.0
    assert g.timeout_for(synth_gen).read == 60.0

    # Cloudflare: default 12s for short mode, 120s for synthesis
    assert cf.timeout_for(short_gen).read == 12.0
    assert cf.timeout_for(short_judge).read == 12.0
    assert cf.timeout_for(synth_gen).read == 120.0

    # Modal keeps its own timeout (120s) in both modes
    assert m.timeout_for(short_gen).read == 120.0
    assert m.timeout_for(short_judge).read == 120.0
    assert m.timeout_for(synth_gen).read == 120.0


def test_per_purpose_short_read_timeouts() -> None:
    g = make_groq(short_read_timeouts={"generate": 14.0, "judge": 9.0}, environ=ENV)
    short_gen = ChatRequest(
        MESSAGES, max_tokens=32, temperature=0.7, top_p=0.8, mode="short", purpose="generate"
    )
    short_judge = ChatRequest(
        MESSAGES, max_tokens=32, temperature=0.7, top_p=0.8, mode="short", purpose="judge"
    )
    assert g.timeout_for(short_gen).read == 14.0
    assert g.timeout_for(short_judge).read == 9.0

    cf = make_cloudflare(
        ACCOUNT, short_read_timeouts={"generate": 15.0, "judge": 11.0}, environ=ENV
    )
    assert cf.timeout_for(short_gen).read == 15.0
    assert cf.timeout_for(short_judge).read == 11.0


@respx.mock
def test_short_call_times_out_and_raises_provider_unavailable() -> None:
    respx.post(GROQ_URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    provider = groq()
    req = ChatRequest(
        MESSAGES, max_tokens=32, temperature=0.7, top_p=0.8, mode="short", purpose="generate"
    )
    with pytest.raises(ProviderUnavailable) as caught:
        provider.complete(req)
    assert caught.value.kind == "timeout"
    assert caught.value.provider == "groq"


# ---------------------------------------------------------------------------- Vercel & Gemini


VERCEL_KEY = "vck_TESTSECRETvercel123"
GEMINI_KEY = "AIzaSyTESTSECRETgemini12345"
VERCEL_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
VG_ENV = {"AI_GATEWAY_API_KEY": VERCEL_KEY, "GEMINI_API_KEY": GEMINI_KEY}


def test_vercel_and_gemini_satisfy_protocol_and_hide_secrets() -> None:
    v = make_vercel(environ=VG_ENV, sleep=lambda _s: None)
    g = make_gemini(environ=VG_ENV, sleep=lambda _s: None)
    for provider in (v, g):
        assert isinstance(provider, ChatProvider)
        text = repr(provider) + repr(provider.adapter)
        for secret in (VERCEL_KEY, GEMINI_KEY):
            assert secret not in text
        assert not prefill_on(provider)
        provider.set_prefill(enabled=True)
        assert not prefill_on(provider)
    assert v.model_id == VERCEL_MODEL
    assert g.model_id == GEMINI_MODEL
    short_gen = short()
    assert v.timeout_for(short_gen).read == 12.0
    assert g.timeout_for(short_gen).read == 12.0


@respx.mock
def test_vercel_body_folds_prefill_and_maps_402_429() -> None:
    route = respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model=VERCEL_MODEL))
    )
    provider = make_vercel(environ=VG_ENV, sleep=lambda _s: None)
    result = provider.complete(short(prefill="La capitale de la France est"))
    body = sent(route)
    assert body["model"] == VERCEL_MODEL
    for field in ("top_k", "min_p", "chat_template_kwargs", "continue_final_message"):
        assert field not in body
    assert body["reasoning_effort"] == "none"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert "La capitale de la France est" in body["messages"][-1]["content"]
    assert result.text == "Paris."

    respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(402, json={"error": {"message": f"bad {VERCEL_KEY}"}})
    )
    with pytest.raises(ProviderUnavailable) as caught_402:
        provider.complete(short())
    assert caught_402.value.kind == "quota"
    assert caught_402.value.retry_after_s == 3600.0
    assert VERCEL_KEY not in caught_402.value.detail

    respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "Insufficient monthly credits"}}, headers={}
        )
    )
    with pytest.raises(ProviderUnavailable) as caught_credit:
        provider.complete(short())
    assert caught_credit.value.kind == "quota"

    respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "Rate limit exceeded"}}, headers={"retry-after": "7"}
        )
    )
    with pytest.raises(ProviderUnavailable) as caught_rate:
        provider.complete(short())
    assert caught_rate.value.kind == "rate_limit"
    assert caught_rate.value.retry_after_s == 7.0


@respx.mock
def test_gemini_strips_models_prefix_and_maps_429() -> None:
    respx.get("https://generativelanguage.googleapis.com/v1beta/openai/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "models/gemini-flash-lite-latest"},
                    {"id": "models/gemini-2.5-flash-lite"},
                ]
            },
        )
    )
    route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            200, json=reply("Paris.", model="models/gemini-flash-lite-latest")
        )
    )
    provider = make_gemini(
        model="models/gemini-flash-lite-latest", environ=VG_ENV, sleep=lambda _s: None
    )
    assert provider.model_id == "gemini-flash-lite-latest"
    assert "gemini-flash-lite-latest" in provider.list_models()
    result = provider.complete(
        generation_request(MESSAGES, mode="short", max_tokens=32, seed=0, stop=("\n\n",))
    )
    body = sent(route)
    assert body["model"] == "gemini-flash-lite-latest"
    for field in ("top_k", "min_p", "presence_penalty", "seed", "chat_template_kwargs"):
        assert field not in body
    assert body["stop"] == ["\n\n"]
    assert body["reasoning_effort"] == "minimal"
    assert result.text == "Paris."

    # 2.5 models still send reasoning_effort="none"
    legacy_route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model="models/gemini-2.5-flash-lite"))
    )
    make_gemini(model="gemini-2.5-flash-lite", environ=VG_ENV, sleep=lambda _s: None).complete(
        short()
    )
    assert sent(legacy_route)["reasoning_effort"] == "none"

    # Google 429 with RESOURCE_EXHAUSTED for per-minute burst stays rate_limit
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Rate limit per minute RPM",
                }
            },
            headers={"retry-after": "12"},
        )
    )
    with pytest.raises(ProviderUnavailable) as caught_rpm:
        provider.complete(short())
    assert caught_rpm.value.kind == "rate_limit"
    assert caught_rpm.value.retry_after_s == 12.0

    # Daily quota exhaustion triggers quota until UTC midnight
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            429, json={"error": {"message": f"Requests per day RPD exceeded {GEMINI_KEY}"}}
        )
    )
    with pytest.raises(ProviderUnavailable) as caught_rpd:
        provider.complete(short())
    assert caught_rpd.value.kind == "quota"
    assert GEMINI_KEY not in caught_rpd.value.detail


@respx.mock
def test_thinking_off_effort_overrides_for_vercel_and_gemini() -> None:
    v_low_route = respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model=VERCEL_MODEL))
    )
    make_vercel(environ=VG_ENV, sleep=lambda _s: None, thinking_off_effort="low").complete(short())
    assert sent(v_low_route)["reasoning_effort"] == "low"

    v_omit_route = respx.post(VERCEL_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model=VERCEL_MODEL))
    )
    make_vercel(environ=VG_ENV, sleep=lambda _s: None, thinking_off_effort="omit").complete(short())
    assert "reasoning_effort" not in sent(v_omit_route)

    g_low_route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model=GEMINI_MODEL))
    )
    make_gemini(environ=VG_ENV, sleep=lambda _s: None, thinking_off_effort="low").complete(short())
    assert sent(g_low_route)["reasoning_effort"] == "low"

    g_omit_route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(200, json=reply("Paris.", model=GEMINI_MODEL))
    )
    make_gemini(environ=VG_ENV, sleep=lambda _s: None, thinking_off_effort="off").complete(short())
    assert "reasoning_effort" not in sent(g_omit_route)


@respx.mock
def test_modal_warm_poller_recovers_without_double_billing() -> None:
    import time as _time  # noqa: PLC0415

    clock = Clock()
    ledger = InMemoryLedger()
    health = respx.get(MODAL_HEALTH).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200),
        ]
    )
    provider = make_modal(
        MODAL,
        ledger=ledger,
        environ={"MODAL_API_KEY": "modal_test"},
        clock=clock,
        warm_poll_interval_s=0.01,
        warm_max_wait_s=10.0,
        auto_warm_poll=True,
    )
    try:
        with pytest.raises(ProviderWarming):
            provider.complete(short())
        assert provider.warming
        initial_usd = ledger.snapshot()["modal"]["usd_session"]
        assert initial_usd > 0.0

        deadline = _time.monotonic() + 2.0
        while provider.warming and _time.monotonic() < deadline:
            _time.sleep(0.01)

        assert not provider.warming
        assert health.call_count == 3
        # Background polls must not add extra ledger entries or cold-start charges.
        assert ledger.snapshot()["modal"]["usd_session"] == initial_usd
    finally:
        provider.close()


@respx.mock
def test_modal_warm_poller_clears_on_max_wait() -> None:
    import time as _time  # noqa: PLC0415

    clock = Clock()
    respx.get(MODAL_HEALTH).mock(return_value=httpx.Response(503))
    provider = make_modal(
        MODAL,
        environ={"MODAL_API_KEY": "modal_test"},
        clock=clock,
        warm_poll_interval_s=0.01,
        warm_max_wait_s=0.5,
        auto_warm_poll=True,
    )
    try:
        with pytest.raises(ProviderWarming):
            provider.complete(short())
        assert provider.warming
        clock.now += 1.0

        deadline = _time.monotonic() + 2.0
        while provider.warming and _time.monotonic() < deadline:
            _time.sleep(0.01)

        assert not provider.warming
    finally:
        provider.close()
