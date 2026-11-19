"""The provider rotation: an order per purpose, circuit breakers, the quota ledger and pinning.

:class:`ProviderRotation` implements :class:`~chapterverse.contracts.Rotation`. For each request
it walks the order for ``req.purpose`` and skips a provider that is unpinned, whose quota
ledger refuses, or whose circuit is open; on :class:`~chapterverse.contracts.ProviderUnavailable`
it tries the next one. When none serves, it raises
:class:`~chapterverse.contracts.AllProvidersUnavailable` carrying every failure and skip.

Generation defaults to ``groq, cloudflare, modal``. The judge defaults to ``modal`` alone: the
judge stays on the measured Modal preset until Groq and Cloudflare each pass the held-out
battery (decision 0001, item 5). :meth:`RotationConfig.from_env` reads ``GEN_PROVIDERS`` and
``JUDGE_PROVIDERS`` (comma lists) and drops providers whose settings are missing.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from chapterverse.config import ConfigError, Settings, read_secret
from chapterverse.contracts import (
    PROVIDER_NAMES,
    AllProvidersUnavailable,
    ChatProvider,
    ChatRequest,
    ChatResult,
    ProviderUnavailable,
    Purpose,
    UnavailableKind,
)
from chapterverse.providers.breaker import CircuitBreaker
from chapterverse.providers.ledger import InMemoryLedger, QuotaLedger
from chapterverse.providers.openai_compat import (
    ProviderWarming,
    make_cloudflare,
    make_groq,
    make_modal,
)

__all__ = [
    "DEFAULT_GENERATE",
    "DEFAULT_JUDGE",
    "ProviderRotation",
    "RotationConfig",
]

DEFAULT_GENERATE: Final = ("groq", "cloudflare", "modal")
DEFAULT_JUDGE: Final = ("modal",)
_MAX_TRIP_S: Final = 86400.0


def _parse_order(text: str | None, var: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if text is None or not text.strip():
        return default
    names = tuple(n.strip().lower() for n in text.split(",") if n.strip())
    for name in names:
        if name not in PROVIDER_NAMES:
            raise ConfigError(f"{var}: unknown provider {name!r}")
    if len(set(names)) != len(names):
        raise ConfigError(f"{var}: a provider is listed twice")
    return names


@dataclass(frozen=True, slots=True, kw_only=True)
class RotationConfig:
    """Which providers serve which purpose, in order, with the settings to build them.

    Holds variable names for keys, never keys. ``cloudflare_account_id`` is not shown in the
    repr. ``skipped`` lists ``(provider, reason)`` for providers dropped for missing settings.
    """

    generate: tuple[str, ...] = DEFAULT_GENERATE
    judge: tuple[str, ...] = DEFAULT_JUDGE
    cloudflare_account_id: str | None = field(default=None, repr=False)
    modal_url: str | None = None
    groq_key_env: str = "GROQ_API_KEY"
    cloudflare_token_env: str = "CLOUDFLARE_AI_WORKER_TOKEN"  # noqa: S105 - a variable name
    modal_key_env: str = "MODAL_API_KEY"
    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def providers(self) -> tuple[str, ...]:
        """Every provider used, generation order first."""
        return self.generate + tuple(n for n in self.judge if n not in self.generate)

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, *, settings: Settings | None = None
    ) -> RotationConfig:
        """Read ``GEN_PROVIDERS`` and ``JUDGE_PROVIDERS`` and drop unconfigured providers.

        Groq needs its key; Cloudflare its token and ``CLOUDFLARE_ACCOUNT_ID``; Modal
        ``MODAL_URL`` and its key. ``settings`` (when given) supplies the account id, the URL
        and the key variable names; otherwise they are read from ``environ``.

        Raises:
            ConfigError: an order names an unknown provider or one twice.
        """
        env = os.environ if environ is None else environ
        generate = _parse_order(env.get("GEN_PROVIDERS"), "GEN_PROVIDERS", DEFAULT_GENERATE)
        judge = _parse_order(env.get("JUDGE_PROVIDERS"), "JUDGE_PROVIDERS", DEFAULT_JUDGE)
        if settings is not None:
            account = settings.cloudflare_account_id
            modal_url = settings.modal_url
            keys = (settings.groq_api_key_env, settings.cloudflare_token_env)
            modal_key = settings.modal_api_key_env
        else:
            account = env.get("CLOUDFLARE_ACCOUNT_ID", "").strip() or None
            modal_url = env.get("MODAL_URL", "").strip() or None
            keys = ("GROQ_API_KEY", "CLOUDFLARE_AI_WORKER_TOKEN")
            modal_key = "MODAL_API_KEY"
        groq_key, cf_key = keys

        def has(var: str) -> str | None:
            try:
                return None if read_secret(var, env) else f"{var} is not set"
            except ConfigError as exc:
                return str(exc)

        reasons: dict[str, str] = {}
        if (why := has(groq_key)) is not None:
            reasons["groq"] = why
        if account is None:
            reasons["cloudflare"] = "CLOUDFLARE_ACCOUNT_ID is not set"
        elif (why := has(cf_key)) is not None:
            reasons["cloudflare"] = why
        if modal_url is None:
            reasons["modal"] = "MODAL_URL is not set"
        elif (why := has(modal_key)) is not None:
            reasons["modal"] = why
        listed = generate + tuple(n for n in judge if n not in generate)
        return cls(
            generate=tuple(n for n in generate if n not in reasons),
            judge=tuple(n for n in judge if n not in reasons),
            cloudflare_account_id=account,
            modal_url=modal_url,
            groq_key_env=groq_key,
            cloudflare_token_env=cf_key,
            modal_key_env=modal_key,
            skipped=tuple((n, reasons[n]) for n in listed if n in reasons),
        )


class ProviderRotation:
    """:class:`~chapterverse.contracts.Rotation` over named providers. Thread-safe.

    ``generate`` and ``judge`` are the orders per purpose; by default generation uses every
    provider in the order given and the judge uses ``modal`` alone (when present). Each provider
    gets a :class:`CircuitBreaker` (``failure_threshold``, ``cooldown_s``, ``clock``).
    """

    def __init__(
        self,
        providers: Sequence[ChatProvider],
        *,
        generate: Sequence[str] | None = None,
        judge: Sequence[str] | None = None,
        ledger: QuotaLedger | None = None,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._providers: dict[str, ChatProvider] = {}
        for provider in providers:
            if provider.name in self._providers:
                raise ValueError(f"provider {provider.name!r} given twice")
            self._providers[provider.name] = provider
        names = tuple(self._providers)
        self._orders: dict[Purpose, tuple[str, ...]] = {
            "generate": tuple(generate) if generate is not None else names,
            "judge": tuple(judge)
            if judge is not None
            else tuple(n for n in DEFAULT_JUDGE if n in self._providers),
        }
        for purpose, order in self._orders.items():
            unknown = [n for n in order if n not in self._providers]
            if unknown:
                raise ValueError(f"{purpose} order names unknown providers {unknown}")
        self.ledger = ledger
        self._breakers = {
            n: CircuitBreaker(
                n, failure_threshold=failure_threshold, cooldown_s=cooldown_s, clock=clock
            )
            for n in self._providers
        }
        self._lock = threading.Lock()
        self._pinned: dict[str, bool] = dict.fromkeys(self._providers, True)
        self._last_kind: dict[str, UnavailableKind] = {}

    def __repr__(self) -> str:
        return (
            f"ProviderRotation(generate={list(self._orders['generate'])}, "
            f"judge={list(self._orders['judge'])})"
        )

    @classmethod
    def from_config(
        cls,
        config: RotationConfig,
        *,
        ledger: QuotaLedger | None = None,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        **provider_kwargs: Any,
    ) -> ProviderRotation:
        """Build the providers ``config`` names, sharing one ledger (default in memory)."""
        shared = ledger if ledger is not None else InMemoryLedger()
        built: list[ChatProvider] = []
        for name in config.providers:
            if name == "groq":
                built.append(
                    make_groq(key_env=config.groq_key_env, ledger=shared, **provider_kwargs)
                )
            elif name == "cloudflare" and config.cloudflare_account_id:
                built.append(
                    make_cloudflare(
                        config.cloudflare_account_id,
                        key_env=config.cloudflare_token_env,
                        ledger=shared,
                        **provider_kwargs,
                    )
                )
            elif name == "modal" and config.modal_url:
                built.append(
                    make_modal(
                        config.modal_url,
                        key_env=config.modal_key_env,
                        ledger=shared,
                        **provider_kwargs,
                    )
                )
        present = {p.name for p in built}
        return cls(
            built,
            generate=[n for n in config.generate if n in present],
            judge=[n for n in config.judge if n in present],
            ledger=shared,
            failure_threshold=failure_threshold,
            cooldown_s=cooldown_s,
        )

    # ------------------------------------------------------------------ state

    @property
    def providers(self) -> Mapping[str, ChatProvider]:
        return dict(self._providers)

    def order(self, purpose: Purpose) -> tuple[str, ...]:
        return self._orders[purpose]

    def breaker(self, name: str) -> CircuitBreaker:
        return self._breakers[name]

    def is_pinned(self, name: str) -> bool:
        with self._lock:
            return self._pinned[name]

    def set_pinned(self, name: str, *, pinned: bool) -> None:
        """Put a provider back in rotation, or take it out (a failed pin check)."""
        with self._lock:
            if name not in self._pinned:
                raise KeyError(name)
            self._pinned[name] = pinned

    def warming(self) -> list[str]:
        """Providers known to be cold-starting (the pipeline shows ``provider_warming``)."""
        return [n for n, p in self._providers.items() if getattr(p, "warming", False) is True]

    def available(self, purpose: Purpose | None = None) -> list[str]:
        """Names in rotation now: pinned, circuit not open, quota left, not warming.

        In ``purpose``'s order, or (None) generation order then judge-only providers.
        """
        if purpose is None:
            gen = self._orders["generate"]
            names = gen + tuple(n for n in self._orders["judge"] if n not in gen)
        else:
            names = self._orders[purpose]
        out = []
        for name in names:
            if not self.is_pinned(name) or self._breakers[name].state == "open":
                continue
            if self.ledger is not None and not self.ledger.available(name):
                continue
            if getattr(self._providers[name], "warming", False) is True:
                continue
            out.append(name)
        return out

    # ------------------------------------------------------------------ calls

    def complete(self, req: ChatRequest) -> ChatResult:
        failures: list[ProviderUnavailable] = []
        for name in self._orders[req.purpose]:
            if not self.is_pinned(name):
                failures.append(ProviderUnavailable(name, "model_changed", detail="unpinned"))
                continue
            if self.ledger is not None and not self.ledger.available(name):
                failures.append(
                    ProviderUnavailable(name, "quota", detail="local quota ledger refused")
                )
                continue
            breaker = self._breakers[name]
            if not breaker.allow():
                with self._lock:
                    kind = self._last_kind.get(name, "server")
                failures.append(
                    ProviderUnavailable(name, kind, breaker.retry_after_s(), "circuit open")
                )
                continue
            try:
                result = self._providers[name].complete(req)
            except ProviderWarming as exc:
                breaker.cancel()
                failures.append(exc)
                continue
            except ProviderUnavailable as exc:
                self._on_failure(name, exc)
                failures.append(exc)
                continue
            except Exception as exc:  # noqa: BLE001 - a provider bug must not escape as a judge retry
                unexpected = ProviderUnavailable(
                    name, "server", detail=f"unexpected {type(exc).__name__}"
                )
                self._on_failure(name, unexpected)
                failures.append(unexpected)
                continue
            breaker.record_success()
            return result
        raise AllProvidersUnavailable(failures)

    def _on_failure(self, name: str, exc: ProviderUnavailable) -> None:
        breaker = self._breakers[name]
        with self._lock:
            self._last_kind[name] = exc.kind
        if exc.kind in {"quota", "rate_limit"}:
            if exc.retry_after_s is not None and exc.retry_after_s > 0:
                breaker.trip(min(exc.retry_after_s, _MAX_TRIP_S))
            else:
                breaker.cancel()  # the ledger refused locally: nothing was sent
            return
        if exc.kind == "model_changed":
            self.set_pinned(name, pinned=False)
        breaker.record_failure()
