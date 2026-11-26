"""Chat providers: Groq, Cloudflare Workers AI, Modal SGLang, and their rotation (agent B3).

* :mod:`.openai_compat`: one ``httpx`` client and a small adapter per provider.
* :mod:`.presets`: request settings per provider, mode and purpose (decision 0001).
* :mod:`.ledger`: the quota ledger Protocol and an in-memory implementation.
* :mod:`.breaker`: a circuit breaker per provider.
* :mod:`.rotation`: :class:`ProviderRotation` and :class:`RotationConfig`.
* :mod:`.pinning`: the daily model-pin check and canaries.
* :mod:`.judge_chat`: :class:`RotationJudgeChat`, the verifier judge's chat callable.
"""

from chapterverse.providers.breaker import BreakerState, CircuitBreaker
from chapterverse.providers.judge_chat import RotationJudgeChat
from chapterverse.providers.ledger import (
    TOKENS_PER_CHAR,
    CallUsage,
    CloudflareLimits,
    GroqLimits,
    InMemoryLedger,
    ModalLimits,
    QuotaLedger,
    RateHeaders,
    estimate_tokens,
)
from chapterverse.providers.openai_compat import (
    CloudflareAdapter,
    GeminiAdapter,
    GroqAdapter,
    ModalAdapter,
    OpenAICompatProvider,
    ProviderWarming,
    VercelAdapter,
    make_cloudflare,
    make_gemini,
    make_groq,
    make_modal,
    make_vercel,
    rejoin_continuation,
)
from chapterverse.providers.pinning import PinCheck, apply_checks, check_provider, run_daily_check
from chapterverse.providers.presets import (
    JUDGE_PRESET_NAME,
    RequestSettings,
    generation_request,
    judge_request,
    request_settings,
)
from chapterverse.providers.rotation import ProviderRotation, RotationConfig

__all__ = [
    "JUDGE_PRESET_NAME",
    "TOKENS_PER_CHAR",
    "BreakerState",
    "CallUsage",
    "CircuitBreaker",
    "CloudflareAdapter",
    "CloudflareLimits",
    "GeminiAdapter",
    "GroqAdapter",
    "GroqLimits",
    "InMemoryLedger",
    "ModalAdapter",
    "ModalLimits",
    "OpenAICompatProvider",
    "PinCheck",
    "ProviderRotation",
    "ProviderWarming",
    "QuotaLedger",
    "RateHeaders",
    "RequestSettings",
    "RotationConfig",
    "RotationJudgeChat",
    "VercelAdapter",
    "apply_checks",
    "check_provider",
    "estimate_tokens",
    "generation_request",
    "judge_request",
    "make_cloudflare",
    "make_gemini",
    "make_groq",
    "make_modal",
    "make_vercel",
    "rejoin_continuation",
    "request_settings",
    "run_daily_check",
]
