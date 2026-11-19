"""The daily model-pin check (plan §5 stage 7; decision 0001, items 7 and 8). Functions only.

Per provider: the ``/models`` list must contain the pinned id (Cloudflare: its model search),
then one prefill canary and one thinking-off canary, both the French test of the decision:
system "Reply in one sentence.", user "Name the capital of France.", and for the prefill an
assistant message "La capitale de la France est", which a fresh English turn cannot continue.

A provider is *pinned* when its model is listed and the thinking-off canary passes; an unpinned
provider leaves the rotation (item 8). The prefill canary only switches native prefill: a pass
turns it on, a failure folds the verified prefix into the user turn and does not unpin (item 7,
amended 2026-11-19, :func:`apply_checks`). Groq starts folded, Cloudflare native. The prefill
canary passes only when the reply starts with "Paris" *and* the prompt cost no more than
:data:`PREFILL_MAX_EXTRA_PROMPT_TOKENS` over the plain canary's (a continued, not closed,
turn). If the model is not listed the canaries are not sent. Each check costs one ``GET`` and
two completions of at most 32 output tokens.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from chapterverse.contracts import ChatMessage, ProviderUnavailable
from chapterverse.providers.openai_compat import OpenAICompatProvider
from chapterverse.providers.presets import generation_request
from chapterverse.providers.rotation import ProviderRotation

__all__ = [
    "CANARY_MESSAGES",
    "CANARY_PREFILL",
    "PREFILL_MAX_EXTRA_PROMPT_TOKENS",
    "PinCheck",
    "apply_checks",
    "check_provider",
    "prefill_continued",
    "prefill_ok",
    "run_daily_check",
    "thinking_was_off",
]

CANARY_MESSAGES: Final = (
    ChatMessage(role="system", content="Reply in one sentence."),
    ChatMessage(role="user", content="Name the capital of France."),
)
CANARY_PREFILL: Final = "La capitale de la France est"
CANARY_MAX_TOKENS: Final = 32
THINKING_OFF_MAX_TOKENS: Final = 24
"""Live thinking-off replies took 8 tokens; thinking on took 44 (decision 0001)."""


@dataclass(frozen=True, slots=True)
class PinCheck:
    """One provider's daily check. None means not run (an earlier step failed)."""

    provider: str
    model_id: str
    checked_at: str
    model_listed: bool | None
    prefill_ok: bool | None
    thinking_off_ok: bool | None
    notes: tuple[str, ...] = ()

    @property
    def pinned(self) -> bool:
        return self.model_listed is True and self.thinking_off_ok is True


def prefill_continued(text: str) -> bool:
    """The reply continues "La capitale de la France est" (it starts with "Paris"), rather than
    opening a new turn ("The capital of France is Paris.")."""
    stripped = text.strip().lower()
    return stripped.startswith("paris") and "capital" not in stripped


PREFILL_MAX_EXTRA_PROMPT_TOKENS: Final = 12
"""How many more prompt tokens the prefill canary may cost than the plain one.

A continued assistant turn adds the prefix and drops the generation prompt; a closed turn adds
the prefix, the turn end and a new generation prompt. Live 2026-11-19, same messages: Cloudflare
with ``continue_final_message`` +6 (34 vs 28, continued " Paris."); Groq +20 (48 vs 28) in each
of 4 calls, which replied with a fresh answer 3 times. The check does not depend on sampling, so
a terse fresh "Paris" cannot pass it.
"""


def prefill_ok(
    text: str, plain_prompt_tokens: int | None, prefill_prompt_tokens: int | None
) -> bool:
    """:func:`prefill_continued`, and the prompt-token cost of a continued turn (when reported)."""
    if not prefill_continued(text):
        return False
    if plain_prompt_tokens is None or prefill_prompt_tokens is None:
        return True
    return prefill_prompt_tokens - plain_prompt_tokens <= PREFILL_MAX_EXTRA_PROMPT_TOKENS


def thinking_was_off(text: str, message: dict[str, object], completion_tokens: int | None) -> bool:
    """No reasoning in the reply, a short answer naming Paris."""
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return False
    if "<think" in text or "</think" in text:
        return False
    if completion_tokens is not None and completion_tokens > THINKING_OFF_MAX_TOKENS:
        return False
    return "paris" in text.lower()


def _now_iso(wall_clock: Callable[[], float]) -> str:
    return datetime.fromtimestamp(wall_clock(), UTC).isoformat(timespec="seconds")


def check_provider(
    provider: OpenAICompatProvider, *, wall_clock: Callable[[], float] = time.time
) -> PinCheck:
    """Run the model list check and both canaries on one provider. Never raises."""
    notes: list[str] = []
    checked_at = _now_iso(wall_clock)
    try:
        ids = provider.list_models()
    except ProviderUnavailable as exc:
        notes.append(f"models list: {exc}")
        return _check(provider, checked_at, listed=None, prefill=None, thinking=None, notes=notes)
    listed = provider.adapter.request_model in ids
    if not listed:
        notes.append(f"{provider.adapter.request_model!r} not in the models list")
        return _check(provider, checked_at, listed=False, prefill=None, thinking=None, notes=notes)

    thinking_ok: bool | None = None
    plain_prompt: int | None = None
    try:
        req = generation_request(CANARY_MESSAGES, mode="short", max_tokens=CANARY_MAX_TOKENS)
        result, message = provider.complete_raw(req)
        thinking_ok = thinking_was_off(result.text, message, result.completion_tokens)
        plain_prompt = result.prompt_tokens
        notes.append(f"thinking-off canary: {result.completion_tokens} tokens")
    except ProviderUnavailable as exc:
        notes.append(f"thinking-off canary: {exc}")

    continued: bool | None = None
    try:
        req = generation_request(
            CANARY_MESSAGES, mode="short", max_tokens=CANARY_MAX_TOKENS, prefill=CANARY_PREFILL
        )
        result, _ = provider.complete_raw(req, native_prefill=True)
        if provider.adapter.prefill_capable:
            continued = prefill_ok(result.text, plain_prompt, result.prompt_tokens)
        else:  # folded into the user turn: the prompt is longer by design
            continued = prefill_continued(result.text)
        notes.append(
            f"prefill canary: {result.text[:40]!r}, prompt {result.prompt_tokens} "
            f"vs {plain_prompt} tokens"
        )
    except ProviderUnavailable as exc:
        notes.append(f"prefill canary: {exc}")

    return _check(
        provider, checked_at, listed=True, prefill=continued, thinking=thinking_ok, notes=notes
    )


def _check(
    provider: OpenAICompatProvider,
    checked_at: str,
    *,
    listed: bool | None,
    prefill: bool | None,
    thinking: bool | None,
    notes: list[str],
) -> PinCheck:
    return PinCheck(
        provider=provider.name,
        model_id=provider.model_id,
        checked_at=checked_at,
        model_listed=listed,
        prefill_ok=prefill,
        thinking_off_ok=thinking,
        notes=tuple(notes),
    )


def apply_checks(rotation: ProviderRotation, checks: Iterable[PinCheck]) -> None:
    """Pin or unpin each checked provider (model list and thinking-off canary); turn native
    prefill on after a passed prefill canary and off (the user-turn fold) after a failed one."""
    providers = rotation.providers
    for check in checks:
        if check.provider not in providers:
            continue
        rotation.set_pinned(check.provider, pinned=check.pinned)
        provider = providers[check.provider]
        if isinstance(provider, OpenAICompatProvider) and check.prefill_ok is not None:
            provider.set_prefill(enabled=check.prefill_ok)


def run_daily_check(
    rotation: ProviderRotation, *, wall_clock: Callable[[], float] = time.time
) -> dict[str, PinCheck]:
    """Check every provider in ``rotation`` and apply the results. No scheduler: call daily."""
    checks = {
        name: check_provider(p, wall_clock=wall_clock)
        for name, p in rotation.providers.items()
        if isinstance(p, OpenAICompatProvider)
    }
    apply_checks(rotation, checks.values())
    return checks
