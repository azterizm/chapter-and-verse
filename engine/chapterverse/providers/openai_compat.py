"""One synchronous OpenAI-compatible chat client, with a small adapter per provider.

:class:`OpenAICompatProvider` implements ``ChatProvider`` over an ``httpx.Client`` that follows
no redirect. Every outcome is a normalised ``ChatResult`` or a ``ProviderUnavailable``
(ARCHITECTURE §4.4); no other exception leaves :meth:`OpenAICompatProvider.complete`.

Adapters (decision 0001):

* :class:`GroqAdapter`: strips ``top_k``, ``min_p``, ``chat_template_kwargs``; thinking by
  ``reasoning_effort``; prefill with a final assistant message, the continuation returned with
  its leading space trimmed and re-joined by :func:`rejoin_continuation` (decision 0001; the
  live smoke of 2026-11-19 found Groq closes the turn instead, so native prefill is off by
  default and only a passed canary turns it on); ``x-ratelimit-*`` headers go to the ledger.
* :class:`CloudflareAdapter`: always sends ``chat_template_kwargs.enable_thinking`` (thinking is
  on by default); prefill with the undocumented ``continue_final_message`` and
  ``add_generation_prompt`` (the daily canary checks them); ``usage.neurons`` goes to the ledger.
* :class:`ModalAdapter`: the measured ``qwen27-ar4`` preset on a self-hosted SGLang server;
  prefill unverified, so folded into the user turn; a short ``/health`` probe before a call when
  the server may be cold (measured cold start 406 s), raising :class:`ProviderWarming`.

Error mapping: 429 is ``rate_limit`` (per-minute) or ``quota`` (daily), told apart by the body,
the headers and ``Retry-After``; 5xx ``server``; timeouts ``timeout``; 401/403 ``config``;
404 or an unknown model ``model_changed``. ``detail`` is scrubbed of keys and account ids.

API keys are read through :func:`chapterverse.config.read_secret` at call time and never stored,
logged or shown in a repr. The Cloudflare account id is in the URL only, never in a repr or detail.
"""

from __future__ import annotations

import json
import re
import threading
import time
import types
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, Final

import httpx
from legal_rag_verifier.judge import CLOSE_THINK

from chapterverse.config import ConfigError, read_secret
from chapterverse.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResult,
    FinishReason,
    ProviderUnavailable,
    Purpose,
    UnavailableKind,
    fold_prefill,
)
from chapterverse.providers.ledger import (
    CallUsage,
    QuotaLedger,
    RateHeaders,
    estimate_tokens,
    parse_rate_headers,
    parse_retry_after,
    seconds_to_utc_midnight,
)
from chapterverse.providers.presets import (
    JUDGE_PRESET,
    MODAL_MODEL_ID,
    REJECTED_PARAMS,
    extra_sampling,
    thinking_enabled,
)

__all__ = [
    "CLOUDFLARE_MODEL",
    "DEFAULT_SHORT_READ_TIMEOUTS",
    "DEFAULT_SHORT_READ_TIMEOUT_S",
    "GEMINI_BASE_URL",
    "GEMINI_MODEL",
    "GROQ_BASE_URL",
    "GROQ_MODEL",
    "VERCEL_BASE_URL",
    "VERCEL_MODEL",
    "Adapter",
    "CloudflareAdapter",
    "GeminiAdapter",
    "GroqAdapter",
    "ModalAdapter",
    "OpenAICompatProvider",
    "ProviderWarming",
    "VercelAdapter",
    "make_cloudflare",
    "make_gemini",
    "make_groq",
    "make_modal",
    "make_vercel",
    "normalise_finish",
    "rejoin_continuation",
]

GROQ_BASE_URL: Final = "https://api.groq.com/openai/v1"
GROQ_MODEL: Final = "qwen/qwen3.8-27b"
CLOUDFLARE_API: Final = "https://api.cloudflare.com/client/v4/accounts"
CLOUDFLARE_MODEL: Final = "@cf/qwen/qwen3.8-27b"
VERCEL_BASE_URL: Final = "https://ai-gateway.vercel.sh/v1"
VERCEL_MODEL: Final = "alibaba/qwen3.7-flash"
GEMINI_BASE_URL: Final = "https://generativelanguage.googleapis.com/v1beta/openai"
GEMINI_MODEL: Final = "gemini-flash-lite-latest"

DEFAULT_SHORT_READ_TIMEOUT_S: Final = 12.0
DEFAULT_SHORT_READ_TIMEOUTS: Final[Mapping[Purpose, float]] = types.MappingProxyType(
    {"generate": DEFAULT_SHORT_READ_TIMEOUT_S, "judge": DEFAULT_SHORT_READ_TIMEOUT_S}
)


def _normalize_short_timeouts(
    timeouts: Mapping[Purpose, float] | float | None,
) -> Mapping[Purpose, float] | None:
    if timeouts is None:
        return None
    if isinstance(timeouts, (int, float)):
        val = float(timeouts)
        return {"generate": val, "judge": val}
    return dict(timeouts)


_MINUTE_S: Final = 60.0
ErrorOutcome = tuple[UnavailableKind, float | None, float | None]
"""(kind, retry_after_s, ledger block_s)."""


class ProviderWarming(ProviderUnavailable):
    """Modal is cold-starting (kind ``timeout``). The rotation does not count it as a failure."""

    def __init__(self, provider: str, detail: str = "") -> None:
        super().__init__(provider, "timeout", None, detail or "warming: cold start in progress")


# ---------------------------------------------------------------------------- text helpers

_NO_SPACE_CHARS: Final = frozenset(".,;:!?%\u2026\u2030'\u2019\u201d\u00bb)]}")


def rejoin_continuation(prefill: str, continuation: str) -> str:
    """Groq's continuation, re-joined to ``prefill`` the way the model wrote it.

    Groq trims the leading space of a continuation. One space goes back unless the prefill
    already ends in whitespace, or the continuation is empty, starts with whitespace, or starts
    with closing or trailing punctuation (``.,;:!?%`` and ellipsis, apostrophes, closing quotes and
    brackets, and any Unicode closing or final punctuation).
    Opening brackets, opening quotes, dashes and symbols such as ``£`` get the space.
    """
    if not continuation:
        return ""
    if not prefill or prefill[-1].isspace():
        return continuation
    first = continuation[0]
    if first.isspace() or first in _NO_SPACE_CHARS:
        return continuation
    if unicodedata.category(first) in {"Pe", "Pf"}:
        return continuation
    return " " + continuation


def normalise_finish(raw: object) -> FinishReason:
    """``stop``, ``length``, ``content_filter`` or ``other``, from any provider's value."""
    value = str(raw or "").lower()
    if value in {"stop", "eos", "end_turn", "stop_sequence"}:
        return "stop"
    if value in {"length", "max_tokens", "model_length"}:
        return "length"
    if value in {"content_filter", "safety"}:
        return "content_filter"
    return "other"


_HEX_ID = re.compile(r"\b[0-9a-fA-F]{32}\b")
_KEYLIKE = re.compile(
    r"(gsk_[A-Za-z0-9]+|vck_[A-Za-z0-9_-]+|AIza[A-Za-z0-9_-]{16,}|Bearer\s+\S+|sk-[A-Za-z0-9_-]{8,})"
)


def _scrub(text: str, secrets: Sequence[str | None]) -> str:
    """Remove secrets and anything shaped like a key or a Cloudflare account id; cap length."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "<redacted>")
    out = _HEX_ID.sub("<redacted>", out)
    out = _KEYLIKE.sub("<redacted>", out)
    out = " ".join(out.split())
    return out[:200]


def _error_text(response: httpx.Response) -> str:
    """The provider's error message (OpenAI, Groq or Cloudflare shapes), else the raw body."""
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return response.text[:300]
    parts: list[str] = []
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            parts += [str(err.get(k)) for k in ("code", "type", "message") if err.get(k)]
        elif err:
            parts.append(str(err))
        errors = body.get("errors")
        if isinstance(errors, list):
            for item in errors[:3]:
                if isinstance(item, dict):
                    parts += [str(item.get(k)) for k in ("code", "message") if item.get(k)]
        for key in ("detail", "message"):
            if body.get(key):
                parts.append(str(body[key]))
    return " ".join(parts) if parts else response.text[:300]


_MODEL_GONE = re.compile(
    r"model_not_found|model_decommissioned|model_not_available|"
    r"(no such|unknown|invalid) model|"
    r"model\b.{0,80}\b(does not exist|not found|no such|decommissioned|deprecated|not available)",
    re.IGNORECASE,
)


def _model_matches(expected: str, got: str) -> bool:
    def norm(s: str) -> str:
        s = s.strip().lower()
        return s.removeprefix("@cf/")

    a, b = norm(expected), norm(got)
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


# ---------------------------------------------------------------------------- adapters


class Adapter:
    """What differs between providers. Subclasses override the hooks they need."""

    name: str = "openai"
    request_model: str = ""
    model_id: str = ""
    quantisation: str | None = None
    key_env: str = ""
    prefill_capable: bool = False
    """Native prefill can be sent (a final assistant message the server may continue)."""
    native_prefill: bool = False
    """Native prefill is on before any canary has passed."""
    timeout: httpx.Timeout = httpx.Timeout(60.0, connect=5.0)
    short_read_timeouts: Mapping[Purpose, float] | None = None
    health_path: str | None = None

    def timeout_for(self, req: ChatRequest) -> httpx.Timeout:
        """The HTTP timeout for one request: short calls get short read timeouts."""
        if req.mode == "short" and self.short_read_timeouts is not None:
            read = self.short_read_timeouts.get(req.purpose)
            if read is not None:
                return httpx.Timeout(
                    connect=self.timeout.connect,
                    read=read,
                    write=self.timeout.write,
                    pool=self.timeout.pool,
                )
        return self.timeout

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model={self.request_model!r})"

    @property
    def rejected(self) -> frozenset[str]:
        return REJECTED_PARAMS.get(self.name, frozenset())

    def base_url(self) -> str:
        raise NotImplementedError

    def chat_url(self) -> str:
        return self.base_url() + "/chat/completions"

    def models_url(self) -> tuple[str, dict[str, str]]:
        return self.base_url() + "/models", {}

    def parse_models(self, body: Any) -> list[str]:
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise TypeError("models list has no data array")
        return [str(m["id"]) for m in data if isinstance(m, dict) and "id" in m]

    def health_url(self) -> str | None:
        return None

    def secrets(self) -> tuple[str, ...]:
        """Non-key values to scrub from details (the Cloudflare account id)."""
        return ()

    def thinking_fields(self, *, thinking: bool, prefill: bool) -> dict[str, Any]:
        """The thinking switch. ``prefill``: a native prefill is being continued."""
        del prefill
        return {"chat_template_kwargs": {"enable_thinking": thinking}}

    def prefill_fields(self) -> dict[str, Any]:
        return {}

    def prepare_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        return [dict(m) for m in messages]

    def join(self, prefill: str, text: str) -> str:
        del prefill
        return text

    def rate_headers(self, headers: Mapping[str, str]) -> RateHeaders | None:
        del headers
        return None

    def neurons(self, body: Any, headers: Mapping[str, str]) -> float | None:
        del body, headers
        return None

    def classify_429(self, headers: Mapping[str, str], text: str, now: float) -> ErrorOutcome:
        del text, now
        retry = parse_retry_after(headers.get("retry-after"))
        return "rate_limit", retry if retry is not None else 30.0, None


class GroqAdapter(Adapter):
    name = "groq"
    request_model = GROQ_MODEL
    model_id = GROQ_MODEL
    key_env = "GROQ_API_KEY"
    prefill_capable = True
    # Off until the prefill canary passes: live 2026-11-19 Groq continued 1 of 4 prefills and
    # each prompt cost +20 tokens over the plain one (a closed turn); decision 0001 amendment.
    native_prefill = False
    timeout = httpx.Timeout(60.0, connect=5.0)
    short_read_timeouts: Mapping[Purpose, float] | None = DEFAULT_SHORT_READ_TIMEOUTS

    def __init__(
        self,
        *,
        key_env: str = "GROQ_API_KEY",
        base_url: str = GROQ_BASE_URL,
        short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    ) -> None:
        self.key_env = key_env
        self._base = base_url.rstrip("/")
        self.short_read_timeouts = (
            DEFAULT_SHORT_READ_TIMEOUTS
            if short_read_timeouts is DEFAULT_SHORT_READ_TIMEOUTS
            else _normalize_short_timeouts(short_read_timeouts)
        )

    def base_url(self) -> str:
        return self._base

    def thinking_fields(self, *, thinking: bool, prefill: bool) -> dict[str, Any]:
        del prefill
        if thinking:
            return {"reasoning_effort": "medium", "reasoning_format": "parsed"}
        return {"reasoning_effort": "none"}

    def join(self, prefill: str, text: str) -> str:
        return rejoin_continuation(prefill, text)

    def rate_headers(self, headers: Mapping[str, str]) -> RateHeaders | None:
        return parse_rate_headers(headers)

    def classify_429(self, headers: Mapping[str, str], text: str, now: float) -> ErrorOutcome:
        del now
        rh = parse_rate_headers(headers) or RateHeaders()
        lowered = text.lower()
        daily = any(s in lowered for s in ("per day", "(rpd)", "(tpd)"))
        minute = any(s in lowered for s in ("per minute", "(rpm)", "(tpm)"))
        if not daily and not minute:
            # Groq's x-ratelimit-*-requests is the daily request limit (live 2026-11-18).
            daily = rh.remaining_requests == 0 or (
                rh.retry_after_s is not None and rh.retry_after_s > _MINUTE_S
            )
        if daily:
            wait = rh.retry_after_s or rh.reset_requests_s or 3600.0
            return "quota", wait, wait
        wait = rh.retry_after_s or rh.reset_tokens_s or 60.0
        return "rate_limit", wait, None


class CloudflareAdapter(Adapter):
    name = "cloudflare"
    request_model = CLOUDFLARE_MODEL
    model_id = CLOUDFLARE_MODEL
    key_env = "CLOUDFLARE_AI_WORKER_TOKEN"
    prefill_capable = True
    native_prefill = True
    timeout = httpx.Timeout(120.0, connect=5.0)
    short_read_timeouts: Mapping[Purpose, float] | None = DEFAULT_SHORT_READ_TIMEOUTS

    def __init__(
        self,
        account_id: str,
        *,
        key_env: str = "CLOUDFLARE_AI_WORKER_TOKEN",
        api: str = "",
        short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    ) -> None:
        if not account_id.strip():
            raise ConfigError("CLOUDFLARE_ACCOUNT_ID is not set")
        self._account = account_id.strip()
        self.key_env = key_env
        self._api = (api or CLOUDFLARE_API).rstrip("/")
        self.short_read_timeouts = (
            DEFAULT_SHORT_READ_TIMEOUTS
            if short_read_timeouts is DEFAULT_SHORT_READ_TIMEOUTS
            else _normalize_short_timeouts(short_read_timeouts)
        )

    def __repr__(self) -> str:
        return "CloudflareAdapter(account=<redacted>)"

    def _account_base(self) -> str:
        return f"{self._api}/{self._account}/ai"

    def base_url(self) -> str:
        return self._account_base() + "/v1"

    def models_url(self) -> tuple[str, dict[str, str]]:
        return self._account_base() + "/models/search", {"search": "qwen3.8-27b"}

    def parse_models(self, body: Any) -> list[str]:
        result = body.get("result") if isinstance(body, dict) else None
        if not isinstance(result, list):
            raise TypeError("models search has no result array")
        return [str(m["name"]) for m in result if isinstance(m, dict) and "name" in m]

    def secrets(self) -> tuple[str, ...]:
        return (self._account,)

    def prefill_fields(self) -> dict[str, Any]:
        return {"continue_final_message": True, "add_generation_prompt": False}

    def neurons(self, body: Any, headers: Mapping[str, str]) -> float | None:
        usage = body.get("usage") if isinstance(body, dict) else None
        value: object = usage.get("neurons") if isinstance(usage, dict) else None
        if value is None:
            value = headers.get("cf-ai-neurons")
        try:
            return None if value is None or isinstance(value, bool) else float(str(value))
        except ValueError:
            return None

    def classify_429(self, headers: Mapping[str, str], text: str, now: float) -> ErrorOutcome:
        lowered = text.lower()
        if any(s in lowered for s in ("daily", "per day", "neuron", "allocation")):
            wait = seconds_to_utc_midnight(now)
            return "quota", wait, wait
        retry = parse_retry_after(headers.get("retry-after"))
        return "rate_limit", retry if retry is not None else 60.0, None


class VercelAdapter(Adapter):
    name = "vercel"
    request_model = VERCEL_MODEL
    model_id = VERCEL_MODEL
    key_env = "AI_GATEWAY_API_KEY"
    prefill_capable = False
    native_prefill = False
    timeout = httpx.Timeout(60.0, connect=5.0)
    short_read_timeouts: Mapping[Purpose, float] | None = DEFAULT_SHORT_READ_TIMEOUTS

    def __init__(
        self,
        *,
        model: str = VERCEL_MODEL,
        key_env: str = "AI_GATEWAY_API_KEY",
        base_url: str = VERCEL_BASE_URL,
        short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    ) -> None:
        clean = model.strip()
        if not clean:
            raise ConfigError("VERCEL_MODEL is empty")
        self.request_model = clean
        self.model_id = clean
        self.key_env = key_env
        self._base = base_url.rstrip("/")
        self.short_read_timeouts = (
            DEFAULT_SHORT_READ_TIMEOUTS
            if short_read_timeouts is DEFAULT_SHORT_READ_TIMEOUTS
            else _normalize_short_timeouts(short_read_timeouts)
        )

    def base_url(self) -> str:
        return self._base

    def thinking_fields(self, *, thinking: bool, prefill: bool) -> dict[str, Any]:
        del prefill
        if thinking:
            return {"reasoning_effort": "medium"}
        return {"reasoning_effort": "none"}

    def classify_429(self, headers: Mapping[str, str], text: str, now: float) -> ErrorOutcome:
        del now
        lowered = text.lower()
        if any(
            s in lowered
            for s in ("insufficient", "quota", "credit", "billing", "balance", "budget", "monthly")
        ):
            return "quota", 3600.0, 3600.0
        retry = parse_retry_after(headers.get("retry-after"))
        return "rate_limit", retry if retry is not None else 30.0, None


class GeminiAdapter(Adapter):
    name = "gemini"
    request_model = GEMINI_MODEL
    model_id = GEMINI_MODEL
    key_env = "GEMINI_API_KEY"
    prefill_capable = False
    native_prefill = False
    timeout = httpx.Timeout(60.0, connect=5.0)
    short_read_timeouts: Mapping[Purpose, float] | None = DEFAULT_SHORT_READ_TIMEOUTS

    def __init__(
        self,
        *,
        model: str = GEMINI_MODEL,
        key_env: str = "GEMINI_API_KEY",
        base_url: str = GEMINI_BASE_URL,
        short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    ) -> None:
        clean = model.strip().removeprefix("models/")
        if not clean:
            raise ConfigError("GEMINI_MODEL is empty")
        self.request_model = clean
        self.model_id = clean
        self.key_env = key_env
        self._base = base_url.rstrip("/")
        self.short_read_timeouts = (
            DEFAULT_SHORT_READ_TIMEOUTS
            if short_read_timeouts is DEFAULT_SHORT_READ_TIMEOUTS
            else _normalize_short_timeouts(short_read_timeouts)
        )

    def base_url(self) -> str:
        return self._base

    def parse_models(self, body: Any) -> list[str]:
        return [m.removeprefix("models/") for m in super().parse_models(body)]

    def thinking_fields(self, *, thinking: bool, prefill: bool) -> dict[str, Any]:
        del prefill
        if thinking:
            return {"reasoning_effort": "medium"}
        effort = "none" if "2.5" in self.request_model else "minimal"
        return {"reasoning_effort": effort}

    def classify_429(self, headers: Mapping[str, str], text: str, now: float) -> ErrorOutcome:
        lowered = text.lower()
        minute = any(s in lowered for s in ("per minute", "perminute", "rpm", "tpm"))
        daily = any(
            s in lowered
            for s in ("per day", "perday", "daily", "rpd", "tpd", "billing", "credit", "monthly")
        )
        if daily and not minute:
            wait = seconds_to_utc_midnight(now)
            return "quota", wait, wait
        retry = parse_retry_after(headers.get("retry-after"))
        return "rate_limit", retry if retry is not None else 60.0, None


class ModalAdapter(Adapter):
    name = "modal"
    request_model = JUDGE_PRESET.model
    model_id = MODAL_MODEL_ID
    quantisation = JUDGE_PRESET.quantisation
    key_env = "MODAL_API_KEY"
    native_prefill = False
    timeout = httpx.Timeout(120.0, connect=10.0)
    health_path = "/health"

    def __init__(self, url: str, *, key_env: str = "MODAL_API_KEY") -> None:
        if not url.startswith(("https://", "http://")):
            raise ConfigError("MODAL_URL is not an http(s) URL")
        self._url = url.rstrip("/").removesuffix("/v1")
        self.key_env = key_env

    def __repr__(self) -> str:
        return f"ModalAdapter(url={self._url!r})"

    def base_url(self) -> str:
        return self._url + "/v1"

    def health_url(self) -> str | None:
        return self._url + "/health"

    def prepare_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        # As legal_rag_verifier.judge.OpenAIChat sends them (the measured request body).
        return [
            {**m, "reasoning_content": ""} if m["role"] == "assistant" else dict(m)
            for m in messages
        ]


# ---------------------------------------------------------------------------- the client

_RETRY_STATUS: Final = frozenset({500, 502, 504})


class _Attempt(Exception):  # noqa: N818 - internal control flow
    def __init__(self, error: ProviderUnavailable, *, retryable: bool) -> None:
        super().__init__(error)
        self.error = error
        self.retryable = retryable


class OpenAICompatProvider:
    """A :class:`~chapterverse.contracts.ChatProvider` for one adapter. Thread-safe.

    ``ledger`` (optional) is reserved before each request and recorded after it. ``retries``
    re-sends after a 500, 502, 504 or a dropped connection, with exponential backoff from
    ``backoff_s``; a 429, a timeout or a warming server is never retried here (the rotation moves
    on). ``warm_window_s``: after a success, calls skip the health probe for this long (Modal's
    scaledown window). ``environ`` replaces ``os.environ`` for key lookups (tests).
    """

    def __init__(
        self,
        adapter: Adapter,
        *,
        ledger: QuotaLedger | None = None,
        client: httpx.Client | None = None,
        retries: int = 1,
        backoff_s: float = 0.5,
        warm_window_s: float = 120.0,
        probe_timeout_s: float = 3.0,
        short_read_timeouts: Mapping[Purpose, float] | float | None = None,
        environ: Mapping[str, str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self._client = client or httpx.Client(follow_redirects=False, timeout=adapter.timeout)
        self._owns_client = client is None
        self.retries = max(0, retries)
        self.backoff_s = backoff_s
        self.warm_window_s = warm_window_s
        self.probe_timeout_s = probe_timeout_s
        self._short_read_timeouts = _normalize_short_timeouts(short_read_timeouts)
        self._environ = environ
        self._sleep = sleep
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._prefill_enabled = adapter.native_prefill
        self._last_ok: float | None = None
        self._warming_since: float | None = None

    def __repr__(self) -> str:
        return f"OpenAICompatProvider({self.adapter!r})"

    # ------------------------------------------------------------------ ChatProvider

    @property
    def name(self) -> str:
        return self.adapter.name

    @property
    def model_id(self) -> str:
        return self.adapter.model_id

    @property
    def quantisation(self) -> str | None:
        return self.adapter.quantisation

    @property
    def supports_prefill(self) -> bool:
        with self._lock:
            return self._prefill_enabled

    def set_prefill(self, *, enabled: bool) -> None:
        """Turn native prefill on (a passed canary) or off (failed: fold), where possible."""
        with self._lock:
            self._prefill_enabled = enabled and self.adapter.prefill_capable

    @property
    def warming(self) -> bool:
        """True while the server is known to be cold-starting (Modal)."""
        with self._lock:
            return self._warming_since is not None

    @property
    def warming_for_s(self) -> float | None:
        with self._lock:
            since = self._warming_since
        return None if since is None else self._clock() - since

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def timeout_for(self, req: ChatRequest) -> httpx.Timeout:
        """The HTTP timeout for one request: short calls get short read timeouts."""
        if self._short_read_timeouts is not None:
            if req.mode == "short":
                read = self._short_read_timeouts.get(req.purpose)
                if read is not None:
                    return httpx.Timeout(
                        connect=self.adapter.timeout.connect,
                        read=read,
                        write=self.adapter.timeout.write,
                        pool=self.adapter.timeout.pool,
                    )
            return self.adapter.timeout
        return self.adapter.timeout_for(req)

    def complete(self, req: ChatRequest) -> ChatResult:
        result, _ = self.complete_raw(req)
        return result

    def complete_raw(
        self, req: ChatRequest, *, native_prefill: bool | None = None
    ) -> tuple[ChatResult, dict[str, Any]]:
        """:meth:`complete`, plus the reply's message object (for the canaries).

        ``native_prefill`` overrides :attr:`supports_prefill` for this call only (the daily
        canary tests native prefill while it is off); ignored when the adapter cannot send it.

        Raises:
            ProviderUnavailable: every failure, including a missing key.
        """
        key = self._key()
        if self.adapter.health_url() is not None and not self._recently_ok():
            self._probe_or_raise(key)
        body, prefill = self.build_body(req, native_prefill=native_prefill)
        est = estimate_tokens(req.messages, req.prefill)
        timeout = self.timeout_for(req)
        attempt = 0
        while True:
            try:
                return self._attempt(body, key, est, req.max_tokens, prefill, timeout=timeout)
            except _Attempt as failed:
                if not failed.retryable or attempt >= self.retries:
                    raise failed.error from None
                self._sleep(self.backoff_s * (2**attempt))
                attempt += 1

    # ------------------------------------------------------------------ requests

    def build_body(
        self, req: ChatRequest, *, native_prefill: bool | None = None
    ) -> tuple[dict[str, Any], str | None]:
        """The JSON body for ``req`` and the prefill sent natively (None when folded or absent).

        Raises:
            ProviderUnavailable: ``config``, a prefill with no user message to fold it into.
        """
        a = self.adapter
        messages: list[ChatMessage] = list(req.messages)
        native: str | None = None
        extra: dict[str, Any] = {}
        native_ok = self.supports_prefill if native_prefill is None else native_prefill
        if req.prefill:
            if native_ok and a.prefill_capable and not req.prefill.endswith("\n"):
                messages.append(ChatMessage(role="assistant", content=req.prefill))
                native = req.prefill
                extra = a.prefill_fields()
            else:
                try:
                    messages = list(fold_prefill(messages, req.prefill))
                except ValueError:
                    raise ProviderUnavailable(
                        a.name, "config", detail="prefill needs a user message"
                    ) from None
        body: dict[str, Any] = {"model": a.request_model, "messages": a.prepare_messages(messages)}
        body.update(extra_sampling(a.name, req.mode, req.purpose))
        body["temperature"] = req.temperature
        body["top_p"] = req.top_p
        body["max_tokens"] = req.max_tokens
        if req.seed is not None:
            body["seed"] = req.seed
        if req.stop:
            body["stop"] = list(req.stop)
        body.update(
            a.thinking_fields(
                thinking=thinking_enabled(req.mode, req.purpose), prefill=native is not None
            )
        )
        body.update(extra)
        for field in a.rejected:
            body.pop(field, None)
        return body, native

    def _key(self) -> str:
        try:
            key = read_secret(self.adapter.key_env, self._environ)
        except ConfigError as exc:
            raise ProviderUnavailable(self.name, "config", detail=str(exc)) from None
        if key is None:
            raise ProviderUnavailable(
                self.name, "config", detail=f"{self.adapter.key_env} is not set"
            )
        return key

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _scrub(self, text: str, key: str) -> str:
        return _scrub(text, (key, *self.adapter.secrets()))

    def _recently_ok(self) -> bool:
        with self._lock:
            last = self._last_ok
        return last is not None and self._clock() - last <= self.warm_window_s

    def _mark_ok(self) -> None:
        with self._lock:
            self._last_ok = self._clock()
            self._warming_since = None

    def _mark_warming(self) -> None:
        with self._lock:
            if self._warming_since is None:
                self._warming_since = self._clock()

    def _record(self, usage: CallUsage) -> None:
        if self.ledger is not None:
            self.ledger.record(usage)

    def _reserve(self, est: int, max_out: int) -> None:
        if self.ledger is not None and not self.ledger.reserve(
            self.name, est, max_output_tokens=max_out
        ):
            raise ProviderUnavailable(self.name, "quota", detail="local quota ledger refused")

    def _warming_error(self, why: str) -> ProviderWarming:
        self._mark_warming()
        return ProviderWarming(self.name, f"warming: {why} (measured cold start 406 s)")

    def probe(self) -> bool:
        """GET the health URL with a short timeout: True when the server answers 200.

        A cold Modal server starts on this request. Never raises for transport errors.

        Raises:
            ProviderUnavailable: ``config`` on a missing key or HTTP 401/403; ``quota`` when the
                ledger refuses (a probe can start a billed container).
        """
        return self._probe(self._key())

    def _probe(self, key: str) -> bool:
        url = self.adapter.health_url()
        if url is None:
            return True
        self._reserve(0, 0)
        t0 = time.perf_counter()
        sent = True
        status = 0
        try:
            response = self._client.get(
                url, headers=self._headers(key), timeout=self.probe_timeout_s
            )
            status = response.status_code
        except httpx.ConnectError:
            sent = False
        except httpx.HTTPError:
            status = 0
        latency = time.perf_counter() - t0
        self._record(CallUsage(provider=self.name, est_tokens=0, sent=sent, latency_s=latency))
        if status in {401, 403}:
            raise ProviderUnavailable(self.name, "config", detail=f"health HTTP {status}")
        if status in {200, 404}:  # 404: no health route; the call itself decides
            self._mark_ok()
            return True
        self._mark_warming()
        return False

    def _probe_or_raise(self, key: str) -> None:
        if not self._probe(key):
            raise self._warming_error("health probe did not answer")

    def list_models(self) -> list[str]:
        """The provider's model ids (Cloudflare: the model search for this model).

        Raises:
            ProviderUnavailable: the list could not be read.
        """
        key = self._key()
        url, params = self.adapter.models_url()
        try:
            response = self._client.get(url, params=params, headers=self._headers(key))
        except httpx.TimeoutException:
            raise ProviderUnavailable(self.name, "timeout", detail="models list") from None
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                self.name, "server", detail=f"models list: {type(exc).__name__}"
            ) from None
        if response.status_code != 200:  # noqa: PLR2004
            kind, retry, _ = self._map_status(response, key)
            detail = self._scrub(f"models list HTTP {response.status_code}", key)
            raise ProviderUnavailable(self.name, kind, retry, detail)
        try:
            return self.adapter.parse_models(response.json())
        except (ValueError, KeyError, TypeError):
            raise ProviderUnavailable(self.name, "server", detail="malformed models list") from None

    def _attempt(
        self,
        body: dict[str, Any],
        key: str,
        est: int,
        max_out: int,
        prefill: str | None,
        *,
        timeout: httpx.Timeout | None = None,
    ) -> tuple[ChatResult, dict[str, Any]]:
        a = self.adapter
        self._reserve(est, max_out)
        usage = CallUsage(provider=self.name, est_tokens=est, max_output_tokens=max_out)
        t0 = time.perf_counter()
        try:
            response = self._client.post(
                a.chat_url(), json=body, headers=self._headers(key), timeout=timeout
            )
        except httpx.TimeoutException as exc:
            latency = time.perf_counter() - t0
            connect = isinstance(exc, httpx.ConnectTimeout | httpx.PoolTimeout)
            self._record(_with(usage, sent=not connect, latency_s=latency))
            if a.health_url() is not None and connect:
                raise _Attempt(self._warming_error("connect timeout"), retryable=False) from None
            name = type(exc).__name__
            raise _Attempt(
                ProviderUnavailable(self.name, "timeout", detail=name), retryable=False
            ) from None
        except httpx.ConnectError:
            self._record(_with(usage, sent=False))
            if a.health_url() is not None:
                raise _Attempt(self._warming_error("connect error"), retryable=False) from None
            raise _Attempt(
                ProviderUnavailable(self.name, "server", detail="ConnectError"), retryable=True
            ) from None
        except httpx.HTTPError as exc:
            self._record(_with(usage, latency_s=time.perf_counter() - t0))
            raise _Attempt(
                ProviderUnavailable(self.name, "server", detail=type(exc).__name__),
                retryable=isinstance(exc, httpx.TransportError),
            ) from None
        latency = time.perf_counter() - t0
        headers = response.headers
        rate = a.rate_headers(headers)
        if response.status_code != 200:  # noqa: PLR2004
            kind, retry, block = self._map_status(response, key)
            self._record(_with(usage, latency_s=latency, headers=rate, block_s=block))
            if kind == "timeout" and a.health_url() is not None and response.status_code != 408:  # noqa: PLR2004
                raise _Attempt(self._warming_error(f"HTTP {response.status_code}"), retryable=False)
            detail = self._scrub(f"HTTP {response.status_code}: {_error_text(response)}", key)
            raise _Attempt(
                ProviderUnavailable(self.name, kind, retry, detail),
                retryable=response.status_code in _RETRY_STATUS,
            )
        try:
            out = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            out = None
        prompt, completion = _usage_tokens(out)
        self._record(
            _with(
                usage,
                latency_s=latency,
                headers=rate,
                prompt_tokens=prompt,
                completion_tokens=completion,
                neurons=a.neurons(out, headers),
            )
        )
        try:
            result, message = self._parse(out, latency * 1000, prefill, prompt, completion)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _Attempt(
                ProviderUnavailable(self.name, "server", detail=self._scrub(str(exc), key)),
                retryable=False,
            ) from None
        self._mark_ok()
        return result, message

    def _map_status(  # noqa: PLR0911 - one return per status class
        self, response: httpx.Response, key: str
    ) -> ErrorOutcome:
        status = response.status_code
        a = self.adapter
        if status == 429:  # noqa: PLR2004
            return a.classify_429(response.headers, _error_text(response), self._wall())
        if 300 <= status < 400:  # noqa: PLR2004
            # Modal answers a long-held web request with a 303; elsewhere a redirect is a
            # wrong URL. Never followed (it would resend the key).
            return ("timeout", None, None) if a.health_url() is not None else ("config", None, None)
        if status == 402:  # noqa: PLR2004
            return "quota", 3600.0, 3600.0
        if status in {401, 403}:
            return "config", None, None
        if status == 404:  # noqa: PLR2004
            return "model_changed", None, None
        if status == 408:  # noqa: PLR2004
            return "timeout", None, None
        if status == 503 and a.health_url() is not None:  # noqa: PLR2004
            return "timeout", parse_retry_after(response.headers.get("retry-after")), None
        if status >= 500:  # noqa: PLR2004
            return "server", parse_retry_after(response.headers.get("retry-after")), None
        if _MODEL_GONE.search(self._scrub(_error_text(response), key)):
            return "model_changed", None, None
        return "config", None, None

    def _parse(
        self,
        out: Any,
        latency_ms: float,
        prefill: str | None,
        prompt: int | None,
        completion: int | None,
    ) -> tuple[ChatResult, dict[str, Any]]:
        a = self.adapter
        if not isinstance(out, dict):
            raise TypeError("malformed chat completion: the body is not an object")
        got_model = out.get("model")
        if (
            isinstance(got_model, str)
            and got_model
            and not _model_matches(a.request_model, got_model)
        ):
            raise ProviderUnavailable(
                self.name, "model_changed", detail=f"reply model {got_model[:80]!r}"
            )
        choices = out["choices"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("malformed chat completion: no choices")
        choice = choices[0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise TypeError("malformed chat completion: no message")
        message: dict[str, Any] = choice["message"]
        content = message.get("content")
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if not all(t is None or isinstance(t, str) for t in (content, reasoning)):
            raise ValueError("malformed chat completion: the content is not text")
        text = content or ""
        if CLOSE_THINK.search(text):
            text = CLOSE_THINK.split(text)[-1].lstrip()
        elif not text.strip() and reasoning and CLOSE_THINK.search(reasoning):
            text = CLOSE_THINK.split(reasoning)[-1].lstrip()
        if prefill is not None:
            text = a.join(prefill, text)
        result = ChatResult(
            text=text,
            finish_reason=normalise_finish(choice.get("finish_reason")),
            provider=self.name,
            model_id=a.model_id,
            quantisation=a.quantisation,
            prompt_tokens=prompt,
            completion_tokens=completion,
            latency_ms=latency_ms,
        )
        return result, message


def _with(usage: CallUsage, **changes: Any) -> CallUsage:
    return replace(usage, **changes)


def _usage_tokens(out: Any) -> tuple[int | None, int | None]:
    usage = out.get("usage") if isinstance(out, dict) else None
    if not isinstance(usage, dict):
        return None, None

    def count(name: str) -> int | None:
        value = usage.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    return count("prompt_tokens"), count("completion_tokens")


# ---------------------------------------------------------------------------- factories


def make_groq(
    *,
    key_env: str = "GROQ_API_KEY",
    ledger: QuotaLedger | None = None,
    short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    **kwargs: Any,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        GroqAdapter(key_env=key_env, short_read_timeouts=short_read_timeouts),
        ledger=ledger,
        **kwargs,
    )


def make_cloudflare(
    account_id: str,
    *,
    key_env: str = "CLOUDFLARE_AI_WORKER_TOKEN",
    ledger: QuotaLedger | None = None,
    short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    **kwargs: Any,
) -> OpenAICompatProvider:
    """Raises: ConfigError: ``account_id`` is empty."""
    return OpenAICompatProvider(
        CloudflareAdapter(account_id, key_env=key_env, short_read_timeouts=short_read_timeouts),
        ledger=ledger,
        **kwargs,
    )


def make_vercel(
    *,
    model: str = VERCEL_MODEL,
    key_env: str = "AI_GATEWAY_API_KEY",
    ledger: QuotaLedger | None = None,
    short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    **kwargs: Any,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        VercelAdapter(model=model, key_env=key_env, short_read_timeouts=short_read_timeouts),
        ledger=ledger,
        **kwargs,
    )


def make_gemini(
    *,
    model: str = GEMINI_MODEL,
    key_env: str = "GEMINI_API_KEY",
    ledger: QuotaLedger | None = None,
    short_read_timeouts: Mapping[Purpose, float] | float | None = DEFAULT_SHORT_READ_TIMEOUTS,
    **kwargs: Any,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        GeminiAdapter(model=model, key_env=key_env, short_read_timeouts=short_read_timeouts),
        ledger=ledger,
        **kwargs,
    )


def make_modal(
    url: str, *, key_env: str = "MODAL_API_KEY", ledger: QuotaLedger | None = None, **kwargs: Any
) -> OpenAICompatProvider:
    """Raises: ConfigError: ``url`` is not an http(s) URL."""
    return OpenAICompatProvider(ModalAdapter(url, key_env=key_env), ledger=ledger, **kwargs)
