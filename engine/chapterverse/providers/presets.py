"""Request settings per provider, mode and purpose (decision 0001, items 2 to 6).

The judge's sampling is the measured ``qwen27-ar4`` preset, imported from
``legal_rag_verifier.judge.PRESETS`` so it can never drift from the measured copy. Generation uses
Qwen's model-card sampling: the non-thinking card in short mode, the thinking card in synthesis.

Only one combination is measured: the judge on Modal, which runs the preset unchanged on its own
SGLang server. Everything else is unmeasured (another quantisation, or a provider that rejects
``top_k`` and ``min_p``), and :attr:`RequestSettings.measured` says so.

A :class:`~chapterverse.contracts.ChatRequest` carries ``temperature``, ``top_p``, ``max_tokens``,
``seed`` and ``stop``; the provider client adds :func:`extra_sampling` (``top_k``, ``min_p``,
``presence_penalty``) and the thinking switch, then strips what the provider rejects.
"""

from __future__ import annotations

import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from legal_rag_verifier.judge import PRESETS, Preset

from chapterverse.contracts import ChatMessage, ChatMode, ChatRequest, ChatRole, Purpose

__all__ = [
    "EXTRA_SAMPLING_KEYS",
    "JUDGE_PRESET",
    "JUDGE_PRESET_NAME",
    "MEASURED",
    "MODAL_MODEL_ID",
    "REJECTED_PARAMS",
    "SHORT_CARD",
    "SYNTHESIS_CARD",
    "RequestSettings",
    "extra_sampling",
    "generation_request",
    "judge_request",
    "request_settings",
    "thinking_enabled",
]

JUDGE_PRESET_NAME: Final = "qwen27-ar4"
JUDGE_PRESET: Final[Preset] = PRESETS[JUDGE_PRESET_NAME]
MODAL_MODEL_ID: Final = f"{JUDGE_PRESET.model}@{JUDGE_PRESET.revision}"
"""The Modal model id as receipts record it (the form ``Judge.from_preset`` uses)."""

SHORT_CARD: Final[Mapping[str, float | int]] = types.MappingProxyType(
    {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
)
"""Qwen3.8's non-thinking model card (decision item 6)."""
SYNTHESIS_CARD: Final[Mapping[str, float | int]] = types.MappingProxyType(
    {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}
)
"""Qwen3.8's thinking model card (decision item 6)."""

EXTRA_SAMPLING_KEYS: Final = ("top_k", "min_p", "presence_penalty")
"""Sampling fields a ``ChatRequest`` does not carry; the client adds them from the preset."""

REJECTED_PARAMS: Final[Mapping[str, frozenset[str]]] = types.MappingProxyType(
    {
        # Live 2026-11-18: HTTP 400 for each (decision 0001, Groq table).
        "groq": frozenset({"top_k", "min_p", "chat_template_kwargs"}),
        "cloudflare": frozenset(),
        "modal": frozenset(),
    }
)

MEASURED: Final[frozenset[tuple[str, Purpose]]] = frozenset({("modal", "judge")})
"""(provider, purpose) pairs whose settings match a measured configuration."""


def thinking_enabled(mode: ChatMode, purpose: Purpose) -> bool:
    """Thinking is on for synthesis generation only; the judge always runs thinking off."""
    return purpose == "generate" and mode == "synthesis"


def _judge_sampling() -> dict[str, float | int]:
    return dict(JUDGE_PRESET.sampling)


def extra_sampling(provider: str, mode: ChatMode, purpose: Purpose) -> dict[str, float | int]:
    """``top_k``, ``min_p`` and ``presence_penalty`` for this call, minus rejected fields."""
    if purpose == "judge":
        source: Mapping[str, float | int] = JUDGE_PRESET.sampling
    else:
        source = SYNTHESIS_CARD if mode == "synthesis" else SHORT_CARD
    rejected = REJECTED_PARAMS.get(provider, frozenset())
    return {k: source[k] for k in EXTRA_SAMPLING_KEYS if k in source and k not in rejected}


@dataclass(frozen=True, slots=True)
class RequestSettings:
    """The sampling and thinking settings one provider gets for one mode and purpose.

    ``sampling`` is the full set sent (the judge's includes ``max_tokens`` and ``seed``);
    ``dropped`` lists preset fields removed because the provider rejects them. ``measured`` is
    True only where the settings, server and quantisation match the measured preset.
    """

    provider: str
    mode: ChatMode
    purpose: Purpose
    sampling: Mapping[str, float | int]
    thinking: bool
    dropped: tuple[str, ...]
    measured: bool
    note: str


def request_settings(provider: str, mode: ChatMode, purpose: Purpose) -> RequestSettings:
    """The settings matrix of decision items 2 to 6, one cell."""
    if purpose == "judge":
        full: dict[str, float | int] = _judge_sampling()
    else:
        full = dict(SYNTHESIS_CARD if mode == "synthesis" else SHORT_CARD)
    rejected = REJECTED_PARAMS.get(provider, frozenset())
    dropped = tuple(k for k in full if k in rejected)
    sampling = types.MappingProxyType({k: v for k, v in full.items() if k not in rejected})
    measured = (provider, purpose) in MEASURED
    if measured:
        note = f"measured: {JUDGE_PRESET_NAME} unchanged ({JUDGE_PRESET.quantisation})"
    elif purpose == "judge":
        note = f"unmeasured: {JUDGE_PRESET_NAME} sampling on an undisclosed quantisation" + (
            f", without {', '.join(dropped)}" if dropped else ""
        )
    else:
        card = "thinking" if mode == "synthesis" else "non-thinking"
        note = f"unmeasured: Qwen {card} card" + (
            f", without {', '.join(dropped)}" if dropped else ""
        )
    return RequestSettings(
        provider=provider,
        mode=mode,
        purpose=purpose,
        sampling=sampling,
        thinking=thinking_enabled(mode, purpose),
        dropped=dropped,
        measured=measured,
        note=note,
    )


def _role(value: str) -> ChatRole:
    if value == "system":
        return "system"
    if value == "user":
        return "user"
    if value == "assistant":
        return "assistant"
    raise ValueError(f"unknown chat role {value!r}")


def _messages(messages: Sequence[Mapping[str, object]]) -> tuple[ChatMessage, ...]:
    return tuple(
        ChatMessage(role=_role(str(m["role"])), content=str(m["content"])) for m in messages
    )


def judge_request(messages: Sequence[Mapping[str, object]]) -> ChatRequest:
    """A ``purpose="judge"`` request carrying the measured preset's sampling.

    Raises:
        ValueError: a message has a role other than system, user or assistant.
        KeyError: a message lacks ``role`` or ``content``.
    """
    sampling = JUDGE_PRESET.sampling
    seed = sampling.get("seed")
    return ChatRequest(
        messages=_messages(messages),
        max_tokens=int(sampling["max_tokens"]),
        temperature=float(sampling["temperature"]),
        top_p=float(sampling["top_p"]),
        mode="short",
        seed=None if seed is None else int(seed),
        purpose="judge",
    )


def generation_request(
    messages: Sequence[Mapping[str, object]],
    *,
    mode: ChatMode,
    max_tokens: int,
    prefill: str | None = None,
    stop: Sequence[str] = (),
    seed: int | None = None,
) -> ChatRequest:
    """A ``purpose="generate"`` request with the model card's temperature and top_p for ``mode``.

    Raises:
        ValueError: a message has an unknown role.
    """
    card = SYNTHESIS_CARD if mode == "synthesis" else SHORT_CARD
    return ChatRequest(
        messages=_messages(messages),
        max_tokens=max_tokens,
        temperature=float(card["temperature"]),
        top_p=float(card["top_p"]),
        stop=tuple(stop),
        mode=mode,
        prefill=prefill,
        seed=seed,
        purpose="generate",
    )


def _settings_matrix() -> dict[tuple[str, ChatMode, Purpose], RequestSettings]:
    """Every cell, for tests and docs."""
    out: dict[tuple[str, ChatMode, Purpose], RequestSettings] = {}
    modes: tuple[ChatMode, ...] = ("short", "synthesis")
    purposes: tuple[Purpose, ...] = ("generate", "judge")
    for provider in REJECTED_PARAMS:
        for mode in modes:
            for purpose in purposes:
                out[(provider, mode, purpose)] = request_settings(provider, mode, purpose)
    return out


def describe() -> list[dict[str, Any]]:
    """The settings matrix as plain dicts (for the smoke script and docs)."""
    return [
        {
            "provider": s.provider,
            "mode": s.mode,
            "purpose": s.purpose,
            "sampling": dict(s.sampling),
            "thinking": s.thinking,
            "dropped": list(s.dropped),
            "measured": s.measured,
            "note": s.note,
        }
        for s in _settings_matrix().values()
    ]
