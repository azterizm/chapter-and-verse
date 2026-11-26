"""Runtime settings, read once from the environment by :meth:`Settings.from_env`.

API keys are never held here. Settings carry the *names* of the variables that hold them, and
:func:`read_secret` reads a value at call time, the rule of ``legal_rag_verifier.judge.OpenAIChat``.
``DATABASE_URL`` can hold a password, so the repr shows only whether it is set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "DEFAULT_GROQ_REQUESTS_PER_DAY",
    "DEFAULT_GROQ_REQUESTS_PER_MINUTE",
    "DEFAULT_GROQ_TOKENS_PER_DAY",
    "DEFAULT_GROQ_TOKENS_PER_MINUTE",
    "DEFAULT_MAX_PROMPT_WINDOWS",
    "DEFAULT_MAX_QUERY_BYTES",
    "DEFAULT_PREMISE_BUDGET_TOKENS",
    "DEFAULT_STORE_DIR",
    "ConfigError",
    "Settings",
    "read_secret",
]

DEFAULT_MAX_QUERY_BYTES: Final = 4096
DEFAULT_STORE_DIR: Final = Path("artefacts/store")
DEFAULT_GROQ_TOKENS_PER_MINUTE: Final = 8_000
DEFAULT_GROQ_TOKENS_PER_DAY: Final = 200_000
DEFAULT_GROQ_REQUESTS_PER_MINUTE: Final = 30
DEFAULT_GROQ_REQUESTS_PER_DAY: Final = 1_000
DEFAULT_PREMISE_BUDGET_TOKENS: Final = 1_500
DEFAULT_MAX_PROMPT_WINDOWS: Final = 8


class ConfigError(ValueError):
    """The environment does not describe a usable configuration. Never names a secret value."""


def read_secret(env_var: str, environ: Mapping[str, str] | None = None) -> str | None:
    """The value of ``env_var`` now, stripped; None when unset or empty.

    Raises:
        ConfigError: the value holds a character that cannot go in an HTTP header. The message
            names the variable, never the value.
    """
    env = os.environ if environ is None else environ
    value = env.get(env_var, "").strip()
    if not value:
        return None
    if not (value.isprintable() and all(c <= "\xff" for c in value)):
        raise ConfigError(f"{env_var} holds an invalid header value")
    return value


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    text = _optional(env, name)
    if text is None:
        return default
    try:
        value = int(text)
    except ValueError:
        raise ConfigError(f"{name} is not an integer") from None
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    """Engine settings. Build with :meth:`from_env`; construct directly in tests."""

    legal_data_root: Path | None
    """``LEGAL_DATA_ROOT``: the data root (``uk/``, ``index/``, ``concepts/``). Optional when
    both index paths are given."""
    router_index: Path
    """``ROUTER_INDEX``, default ``<LEGAL_DATA_ROOT>/index``."""
    concepts_index: Path
    """``CONCEPTS_INDEX``, default ``<LEGAL_DATA_ROOT>/concepts``."""
    store_dir: Path = DEFAULT_STORE_DIR
    """``STORE_DIR``: the text store built by ``build/`` (agent B1)."""
    database_url: str | None = None
    """``DATABASE_URL``: Postgres for the quota ledger and the research trail. May hold a
    password: never logged, never in the repr."""
    modal_url: str | None = None
    """``MODAL_URL``: the Modal SGLang endpoint; Modal is out of rotation when unset."""
    cloudflare_account_id: str | None = None
    """``CLOUDFLARE_ACCOUNT_ID``: Cloudflare is out of rotation when unset."""
    max_query_bytes: int = DEFAULT_MAX_QUERY_BYTES
    """``MAX_QUERY_BYTES``: the UTF-8 size cap on a query (plan §5 stage 1)."""
    cors_origins: tuple[str, ...] = ()
    """``CORS_ORIGINS``: comma-separated origins; none when unset."""
    groq_tokens_per_minute: int = DEFAULT_GROQ_TOKENS_PER_MINUTE
    """``GROQ_TOKENS_PER_MINUTE``: Groq TPM limit enforced by the quota ledger."""
    groq_tokens_per_day: int = DEFAULT_GROQ_TOKENS_PER_DAY
    """``GROQ_TOKENS_PER_DAY``: Groq TPD limit enforced by the quota ledger."""
    groq_requests_per_minute: int = DEFAULT_GROQ_REQUESTS_PER_MINUTE
    """``GROQ_REQUESTS_PER_MINUTE``: Groq RPM limit enforced by the quota ledger."""
    groq_requests_per_day: int = DEFAULT_GROQ_REQUESTS_PER_DAY
    """``GROQ_REQUESTS_PER_DAY``: Groq RPD limit enforced by the quota ledger."""
    premise_budget_tokens: int = DEFAULT_PREMISE_BUDGET_TOKENS
    """``PREMISE_BUDGET_TOKENS``: estimated token budget for rendered prompt passages."""
    max_prompt_windows: int = DEFAULT_MAX_PROMPT_WINDOWS
    """``MAX_PROMPT_WINDOWS``: maximum number of passage windows sent to generation."""
    vercel_model: str | None = None
    """``VERCEL_MODEL``: optional model id override for Vercel AI Gateway."""
    gemini_model: str | None = None
    """``GEMINI_MODEL``: optional model id override for Google AI Studio."""
    # The names of the variables holding the keys, not keys.
    groq_api_key_env: str = "GROQ_API_KEY"
    cloudflare_token_env: str = "CLOUDFLARE_AI_WORKER_TOKEN"  # noqa: S105 - a variable name
    vercel_key_env: str = "AI_GATEWAY_API_KEY"
    gemini_key_env: str = "GEMINI_API_KEY"
    modal_api_key_env: str = "MODAL_API_KEY"

    def __repr__(self) -> str:
        shown = {
            "legal_data_root": self.legal_data_root,
            "router_index": self.router_index,
            "concepts_index": self.concepts_index,
            "store_dir": self.store_dir,
            "database_url": "<set>" if self.database_url else None,
            "modal_url": self.modal_url,
            "cloudflare_account_id": self.cloudflare_account_id,
            "max_query_bytes": self.max_query_bytes,
            "cors_origins": self.cors_origins,
            "groq_tokens_per_minute": self.groq_tokens_per_minute,
            "groq_tokens_per_day": self.groq_tokens_per_day,
            "groq_requests_per_minute": self.groq_requests_per_minute,
            "groq_requests_per_day": self.groq_requests_per_day,
            "premise_budget_tokens": self.premise_budget_tokens,
            "max_prompt_windows": self.max_prompt_windows,
            "vercel_model": self.vercel_model,
            "gemini_model": self.gemini_model,
            "groq_api_key_env": self.groq_api_key_env,
            "cloudflare_token_env": self.cloudflare_token_env,
            "vercel_key_env": self.vercel_key_env,
            "gemini_key_env": self.gemini_key_env,
            "modal_api_key_env": self.modal_api_key_env,
        }
        return "Settings(" + ", ".join(f"{k}={v!r}" for k, v in shown.items()) + ")"

    __str__ = __repr__

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Read the settings from ``environ`` (default ``os.environ``).

        Raises:
            ConfigError: an index path cannot be worked out, or a number does not parse.
        """
        env = os.environ if environ is None else environ
        root_text = _optional(env, "LEGAL_DATA_ROOT")
        root = Path(root_text).expanduser() if root_text else None

        def index_path(name: str, default: str) -> Path:
            value = _optional(env, name)
            if value:
                return Path(value).expanduser()
            if root is None:
                raise ConfigError(f"set {name} or LEGAL_DATA_ROOT")
            return root / default

        max_bytes = _positive_int(env, "MAX_QUERY_BYTES", DEFAULT_MAX_QUERY_BYTES)
        store_text = _optional(env, "STORE_DIR")
        origins = tuple(o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip())
        return cls(
            legal_data_root=root,
            router_index=index_path("ROUTER_INDEX", "index"),
            concepts_index=index_path("CONCEPTS_INDEX", "concepts"),
            store_dir=Path(store_text).expanduser() if store_text else DEFAULT_STORE_DIR,
            database_url=_optional(env, "DATABASE_URL"),
            modal_url=_optional(env, "MODAL_URL"),
            cloudflare_account_id=_optional(env, "CLOUDFLARE_ACCOUNT_ID"),
            max_query_bytes=max_bytes,
            cors_origins=origins,
            groq_tokens_per_minute=_positive_int(
                env, "GROQ_TOKENS_PER_MINUTE", DEFAULT_GROQ_TOKENS_PER_MINUTE
            ),
            groq_tokens_per_day=_positive_int(
                env, "GROQ_TOKENS_PER_DAY", DEFAULT_GROQ_TOKENS_PER_DAY
            ),
            groq_requests_per_minute=_positive_int(
                env, "GROQ_REQUESTS_PER_MINUTE", DEFAULT_GROQ_REQUESTS_PER_MINUTE
            ),
            groq_requests_per_day=_positive_int(
                env, "GROQ_REQUESTS_PER_DAY", DEFAULT_GROQ_REQUESTS_PER_DAY
            ),
            premise_budget_tokens=_positive_int(
                env, "PREMISE_BUDGET_TOKENS", DEFAULT_PREMISE_BUDGET_TOKENS
            ),
            max_prompt_windows=_positive_int(env, "MAX_PROMPT_WINDOWS", DEFAULT_MAX_PROMPT_WINDOWS),
            vercel_model=_optional(env, "VERCEL_MODEL"),
            gemini_model=_optional(env, "GEMINI_MODEL"),
        )
