"""The FastAPI app, ``chapterverse.app:app`` (plan section 5; docs/ARCHITECTURE.md section 11).

Endpoints:

* ``POST /api/ask``: body ``{"query": str, "as_at"?: "YYYY-MM-DD", "context"?: [coordinate]}``
  (other fields the web client sends, such as ``session_id``, are ignored). The answer streams
  as ``text/event-stream``, each event the exact bytes of ``contracts.encode_sse``.
* ``GET /api/health``: ``{"status", "corpus_id", "snapshot"}``.

Edge rules, all before routing, as HTTP statuses with a JSON body ``{"error", "message"}`` (the
web client reads the status of a response that is not an event stream):

1. 403 ``access_required`` when ``REQUIRE_ACCESS_HEADERS`` is on and the request lacks the
   Cloudflare Access headers (``Cf-Access-Jwt-Assertion``, or the service token's
   ``CF-Access-Client-Id`` and ``CF-Access-Client-Secret``). A presence check, as a second line
   behind Access itself; ``/api/health`` is exempt (it holds nothing private, and the container
   health check calls it).
2. 429 ``rate_limited`` with ``Retry-After`` from :class:`~chapterverse.ratelimit.TokenBucket`,
   keyed by the Worker's ``X-CV-Client-IP`` when the request passed the Access-header check
   (``REQUIRE_ACCESS_HEADERS=1``), else by the socket peer.
3. 413 ``body_too_large`` / ``query_too_long``: the body cap, then ``MAX_QUERY_BYTES`` on the
   query's UTF-8 bytes. 400 ``bad_request`` for a body that is not the shape above.

Turnstile is checked by the Worker, which mints the session the request carries.

Startup (lifespan): open the text store (every file's SHA-256 checked), the router index and
the concept index once; refuse to start when the store and router snapshots differ; build the
rotation from the environment (``GEN_PROVIDERS``, ``JUDGE_PROVIDERS``) over the quota ledger
(Postgres when ``DATABASE_URL`` is set); load the Modal KV path when Modal is configured; schedule
the provider pin check and the database retention purge as immediate first runs on the maintenance
daemon thread (off the readiness path), then every ``PIN_CHECK_INTERVAL_S`` (a day) and hourly.
Until the first check finishes, the providers keep their configured initial state: every provider is
pinned (in rotation); native prefill is enabled for Cloudflare and disabled (folded into the user
turn) for Groq and Modal. An inconclusive or failed check leaves the pins as they were, equal to the
state between daily checks.

The pin check is :func:`chapterverse.providers.pinning.check_provider` and ``apply_checks`` (the
two halves of ``run_daily_check``) for every provider except Modal: Modal serves our own pinned
revision (``serve.py`` refuses to start on any other), and any request to a cold Modal endpoint
starts a billed container, so checking it daily would cost about a cold start a day for nothing.
Checks run concurrently across a small thread pool (one thread per provider), and ``apply_checks``
is called once with all results.

Each answer runs in a worker thread from a bounded pool (``ASK_WORKERS``, default 4); events
cross to the event loop with ``loop.call_soon_threadsafe``. When the client disconnects the
response sets a flag; the worker's next ``emit`` raises ``StreamClosed`` and it stops at that
sentence boundary.

Logging: one line per answer with ``query_sha256``, the route status, the mode, counts and the
timings. The query itself is never logged.

Extra environment variables (``config.Settings`` holds the shared ones):

==========================  ========================================================  =======
``REQUIRE_ACCESS_HEADERS``  ``1`` makes the Access headers mandatory (production)       off
``RATE_LIMIT_PER_MINUTE``   Token refill per client per minute                          10
``RATE_LIMIT_BURST``        Bucket size per client                                      10
``ASK_WORKERS``             Answers computed at once                                    4
``PIN_CHECK_INTERVAL_S``    Seconds between pin checks                                  86400
``MODAL_MASK_PROCESSOR``    Path of ``mask_processor.txt`` (deploy/README.md 3.5)       unset
``MODAL_TOKENIZER_JSON``    Path of the pinned revision's ``tokenizer.json``;            unset
                            unset: downloaded once from Hugging Face at the revision
``LOG_LEVEL``               Python log level for ``chapterverse``                       INFO
==========================  ========================================================  =======
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, NoReturn

import anyio
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from legal_rag_router import Router
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send
from symspellpy import SymSpell  # type: ignore[import-untyped]
from tokenizers import Tokenizer

from chapterverse.audit.modal_path import BearerPost, ModalSGLangBackend
from chapterverse.audit.policy import build_policy
from chapterverse.bound_rank import InstrumentRanges
from chapterverse.config import ConfigError, Settings
from chapterverse.contracts import (
    AllProvidersUnavailable,
    Event,
    ProviderUnavailable,
    TextStore,
    UnavailableKind,
    encode_sse,
)
from chapterverse.dense.colbert import ColbertScorer
from chapterverse.dense.embed import load_model
from chapterverse.dense.retriever import DenseRetriever
from chapterverse.dense.store import BinaryInt8Store
from chapterverse.discovery import ClarifyPrior, load_clarify_prior
from chapterverse.nlp import get_nlp
from chapterverse.normalise import load_dictionary
from chapterverse.persistence import ReceiptStore, open_persistence
from chapterverse.pipeline import (
    MAX_CONTEXT_COORDINATES,
    AskRequest,
    ModalKV,
    Pipeline,
    StreamClosed,
)
from chapterverse.providers.ledger import CallUsage
from chapterverse.providers.openai_compat import OpenAICompatProvider
from chapterverse.providers.pinning import PinCheck, apply_checks, check_provider, is_conclusive
from chapterverse.providers.presets import JUDGE_PRESET, JUDGE_PRESET_NAME, MODAL_MODEL_ID
from chapterverse.providers.rotation import ProviderRotation, RotationConfig
from chapterverse.ratelimit import (
    DEFAULT_DAILY_ASK_CAP,
    DEFAULT_GLOBAL_DAILY_ANSWERS,
    DEFAULT_HOSTING_SHARE,
    DailyBudgetStore,
    DailyCapStore,
    TokenBucket,
    client_key_hash,
)
from chapterverse.store import open_store
from chapterverse.store.manifest import sha256_file
from chapterverse.symspell_build import build_symspell_in_memory

__all__ = [
    "MeteredPost",
    "Runtime",
    "app",
    "build_runtime",
    "check_snapshots",
    "create_app",
    "load_mask_processor",
    "load_modal_kv",
    "load_tokenizer",
    "parse_ask",
    "pin_check",
]

log = logging.getLogger("chapterverse.app")

MIN_BODY_BYTES: Final = 12_288
"""The Worker's body cap (deploy/README.md section 5); the engine allows at least this."""
DAY_S: Final = 86_400.0
PURGE_INTERVAL_S: Final = 3_600.0
ACCESS_JWT: Final = "cf-access-jwt-assertion"
ACCESS_ID: Final = "cf-access-client-id"
ACCESS_SECRET: Final = "cf-access-client-secret"  # noqa: S105 - a header name
CLIENT_IP_HEADER: Final = "x-cv-client-ip"
"""Set by the Worker from ``CF-Connecting-IP``; any client-sent copy is dropped there."""
NET_CLASS_HEADER: Final = "x-cv-net-class"
"""Network classification ('hosting', etc.); trusted only with valid Access headers."""
MIN_CAP_SALT_LEN: Final = 32
"""Minimum character length for ``CAP_SALT`` when daily cap is active (requirements R7)."""
SSE_HEADERS: Final = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
HF_RESOLVE: Final = "https://huggingface.co/{model}/resolve/{revision}/{name}"


# ---------------------------------------------------------------------------- environment


def _env(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _env_number(environ: Mapping[str, str], name: str, default: float) -> float:
    text = _env(environ, name)
    if text is None:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ConfigError(f"{name} is not a number") from None
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _env_nonnegative_number(environ: Mapping[str, str], name: str, default: float) -> float:
    text = _env(environ, name)
    if text is None:
        return default
    try:
        value = float(text)
    except ValueError:
        raise ConfigError(f"{name} is not a number") from None
    if not math.isfinite(value) or value < 0:
        raise ConfigError(f"{name} must be non-negative")
    return value


def _env_flag(environ: Mapping[str, str], name: str) -> bool:
    text = (_env(environ, name) or "0").lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be 1 or 0")


def _cors_origins(environ: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(o.strip() for o in environ.get("CORS_ORIGINS", "").split(",") if o.strip())


# ---------------------------------------------------------------------------- runtime


@dataclass(kw_only=True)
class Runtime:
    """Everything shared by all requests, built once at startup."""

    settings: Settings
    pipeline: Pipeline
    store: TextStore
    limiter: TokenBucket
    receipts: ReceiptStore | None = None
    daily_caps: DailyCapStore | None = None
    daily_ask_cap: int = DEFAULT_DAILY_ASK_CAP
    cap_salt: str = ""
    daily_budget: DailyBudgetStore | None = None
    global_daily_answers: int = DEFAULT_GLOBAL_DAILY_ANSWERS
    hosting_share: float = DEFAULT_HOSTING_SHARE
    require_access: bool = False
    workers: int = 4
    dictionary: SymSpell | None = None
    pin_check: Callable[[], object] | None = None
    pin_interval_s: float = DAY_S
    purge: Callable[[], object] | None = None
    close: Callable[[], None] | None = None
    executor: ThreadPoolExecutor = field(init=False)

    def __post_init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="ask")
        if self.daily_caps is not None and self.pipeline.daily_caps is None:
            self.pipeline.daily_caps = self.daily_caps
            self.pipeline.daily_ask_cap = self.daily_ask_cap
        if self.daily_budget is not None and self.pipeline.daily_budget is None:
            self.pipeline.daily_budget = self.daily_budget
            self.pipeline.global_daily_answers = self.global_daily_answers
            self.pipeline.hosting_share = self.hosting_share

    @property
    def max_body_bytes(self) -> int:
        return max(MIN_BODY_BYTES, 3 * self.settings.max_query_bytes + 1024)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self.close is not None:
            self.close()


def check_snapshots(store: TextStore, router: Router) -> None:
    """Refuse to run a store and a router built from different snapshots.

    Raises:
        RuntimeError: the snapshots differ.
    """
    if store.snapshot != router.index.snapshot:
        raise RuntimeError(
            f"text store snapshot {store.snapshot} differs from router index snapshot "
            f"{router.index.snapshot}; rebuild one of them"
        )


def pin_check(rotation: ProviderRotation) -> dict[str, PinCheck]:
    """Check every provider's pinned model except Modal's (module docstring) and apply it.

    Runs provider checks concurrently across a small thread pool (one thread per provider).
    """
    providers_to_check: list[tuple[str, OpenAICompatProvider]] = []
    for name, provider in rotation.providers.items():
        if not isinstance(provider, OpenAICompatProvider):
            continue
        if provider.adapter.health_url() is not None:
            log.info("pin check %s skipped: it serves its own pinned revision", name)
            continue
        providers_to_check.append((name, provider))

    checks: dict[str, PinCheck] = {}
    if providers_to_check:
        with ThreadPoolExecutor(
            max_workers=len(providers_to_check), thread_name_prefix="pin-check"
        ) as pool:
            futures = {
                name: pool.submit(check_provider, provider) for name, provider in providers_to_check
            }
            for name, fut in futures.items():
                try:
                    check = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("pin check %s failed: %s", name, type(exc).__name__)
                    continue
                checks[name] = check
                log.info(
                    "pin check %s: listed=%s thinking_off=%s prefill=%s conclusive=%s",
                    name,
                    check.model_listed,
                    check.thinking_off_ok,
                    check.prefill_ok,
                    is_conclusive(check),
                )
    apply_checks(rotation, checks.values())
    return checks


# ---------------------------------------------------------------------------- Modal KV path


class QwenChatTokenizer:
    """The Hugging Face tokenizer duck type ``ModalSGLangBackend`` needs, over ``tokenizers``.

    ``tokenizers`` loads ``tokenizer.json`` but has no chat template, so the Qwen3 template is
    written here for the only case the KV path uses: system and user turns, the generation
    prompt, thinking off (``<|im_start|>assistant\\n<think>\\n\\n</think>\\n\\n``, what the
    model's own template renders for ``enable_thinking=False``).
    """

    chat_template: Final = "qwen3-chatml (thinking off)"
    _ROLES: Final = frozenset({"system", "user", "assistant"})

    def __init__(self, tokenizer: Tokenizer) -> None:
        self.tokenizer = tokenizer
        eos = [tokenizer.token_to_id(t) for t in ("<|im_end|>", "<|endoftext|>")]
        if any(t is None for t in eos) or tokenizer.token_to_id("<|im_start|>") is None:
            raise ValueError("tokenizer.json lacks the Qwen chat tokens")
        self.eos: tuple[int, ...] = tuple(int(t) for t in eos if t is not None)

    def __repr__(self) -> str:
        return f"QwenChatTokenizer(vocab={self.tokenizer.get_vocab_size()})"

    def __call__(self, text: str, add_special_tokens: bool = False) -> dict[str, list[int]]:  # noqa: FBT001, FBT002 - the transformers signature
        return {
            "input_ids": list(
                self.tokenizer.encode(text, add_special_tokens=add_special_tokens).ids
            )
        }

    def decode(self, ids: Sequence[int], skip_special_tokens: bool = True) -> str:  # noqa: FBT001, FBT002
        return str(self.tokenizer.decode(list(ids), skip_special_tokens=skip_special_tokens))

    def render(self, messages: Sequence[Mapping[str, str]]) -> str:
        parts: list[str] = []
        for message in messages:
            role = message["role"]
            if role not in self._ROLES:
                raise ValueError(f"unknown chat role {role!r}")
            parts.append(f"<|im_start|>{role}\n{message['content']}<|im_end|>\n")
        parts.append("<|im_start|>assistant\n<think>\n\n</think>\n\n")
        return "".join(parts)

    def apply_chat_template(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        add_generation_prompt: bool = True,
        return_dict: bool = True,
        enable_thinking: bool = False,
    ) -> dict[str, list[int]]:
        if not (add_generation_prompt and return_dict) or enable_thinking:
            raise ValueError("only the thinking-off generation prompt is supported")
        return self(self.render(messages))


def load_mask_processor(environ: Mapping[str, str]) -> str:
    """The serialised SGLang logit mask from ``MODAL_MASK_PROCESSOR`` (the file
    ``serve.py::get_mask_processor --out`` writes once; deploy/README.md section 3 step 5).

    Raises:
        FileNotFoundError: the variable is unset or the file is missing.
        ValueError: the file is empty.
    """
    path = _env(environ, "MODAL_MASK_PROCESSOR")
    if path is None:
        raise FileNotFoundError("MODAL_MASK_PROCESSOR is not set")
    text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("the mask processor file is empty")
    return text


def load_tokenizer(
    environ: Mapping[str, str], *, client: httpx.Client | None = None
) -> QwenChatTokenizer:
    """The pinned revision's tokenizer: ``MODAL_TOKENIZER_JSON``, else ``tokenizer.json``
    downloaded once from Hugging Face at ``JUDGE_PRESET.revision`` (held in memory only).

    Raises:
        OSError, httpx.HTTPError, ValueError: it could not be read or is not a Qwen tokenizer.
    """
    path = _env(environ, "MODAL_TOKENIZER_JSON")
    if path is not None:
        return QwenChatTokenizer(Tokenizer.from_file(str(Path(path).expanduser())))
    url = HF_RESOLVE.format(
        model=JUDGE_PRESET.model, revision=JUDGE_PRESET.revision, name="tokenizer.json"
    )
    own = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        response = http.get(url)
        response.raise_for_status()
        return QwenChatTokenizer(Tokenizer.from_str(response.text))
    finally:
        if own:
            http.close()


class MeteredPost:
    """The KV path's ``/generate`` POST, accounted exactly like the rotation's chat calls.

    Before each call: the provider must be pinned, its circuit breaker must allow the call, and
    the quota ledger must reserve it (Modal: dollars a month against the hard cap). Any refusal
    raises :class:`~chapterverse.contracts.AllProvidersUnavailable`, so Modal is unavailable for
    that request (extractive before the first sentence, a notice after). After each call the
    usage (prompt ids, output ids, wall time) is recorded in the ledger and the outcome in the
    breaker, with the rotation's rules: a 429 or quota trips it for the stated wait, anything
    else counts as a failure. Thread-safe (the ledger and breaker are).
    """

    def __init__(
        self,
        inner: Callable[[dict[str, Any]], dict[str, Any]],
        rotation: ProviderRotation,
        *,
        provider: str = "modal",
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.inner = inner
        self.rotation = rotation
        self.provider = provider
        self._clock = clock

    def __repr__(self) -> str:
        return f"MeteredPost(provider={self.provider!r})"

    def _refuse(self, kind: UnavailableKind, detail: str, retry: float | None = None) -> NoReturn:
        raise AllProvidersUnavailable([ProviderUnavailable(self.provider, kind, retry, detail)])

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        name = self.provider
        rotation = self.rotation
        if not rotation.is_pinned(name):
            self._refuse("model_changed", "unpinned")
        breaker = rotation.breaker(name)
        if not breaker.allow():
            self._refuse("server", "circuit open", breaker.retry_after_s())
        est = len(body.get("input_ids") or ())
        params = body.get("sampling_params") or {}
        max_out = int(params.get("max_new_tokens") or 0)
        ledger = rotation.ledger
        if ledger is not None and not ledger.reserve(name, est, max_output_tokens=max_out):
            breaker.cancel()
            self._refuse("quota", "local quota ledger refused")
        t0 = self._clock()
        try:
            out = self.inner(body)
        except AllProvidersUnavailable as exc:
            failure = exc.failures[0] if exc.failures else ProviderUnavailable(name, "server")
            self._record(est, max_out, t0, None)
            if failure.kind in ("quota", "rate_limit"):
                if failure.retry_after_s:
                    breaker.trip(failure.retry_after_s)
                else:
                    breaker.cancel()
            else:
                breaker.record_failure()
            raise
        except Exception as exc:  # noqa: BLE001 - a client bug must not escape as a judge retry
            self._record(est, max_out, t0, None)
            breaker.record_failure()
            self._refuse("server", f"unexpected {type(exc).__name__}")
        self._record(est, max_out, t0, len(out.get("output_ids") or ()))
        breaker.record_success()
        return out

    def _record(self, est: int, max_out: int, t0: float, completion: int | None) -> None:
        ledger = self.rotation.ledger
        if ledger is None:
            return
        ledger.record(
            CallUsage(
                provider=self.provider,
                est_tokens=est,
                max_output_tokens=max_out,
                prompt_tokens=est if completion is not None else None,
                completion_tokens=completion,
                latency_s=self._clock() - t0,
            )
        )


def load_modal_kv(
    rotation: ProviderRotation,
    settings: Settings,
    environ: Mapping[str, str],
    *,
    tokenizer_client: httpx.Client | None = None,
) -> ModalKV | None:
    """The Modal KV path, or None (logged why) when Modal is absent or either part fails;
    Modal then serves chat and the judge only."""
    provider = rotation.providers.get("modal")
    if not isinstance(provider, OpenAICompatProvider) or settings.modal_url is None:
        return None
    try:
        processor = load_mask_processor(environ)
    except (OSError, ValueError) as exc:
        log.warning("Modal KV path off: mask processor not loaded (%s)", exc)
        return None
    try:
        tokenizer = load_tokenizer(environ, client=tokenizer_client)
    except (OSError, ValueError, httpx.HTTPError, Exception) as exc:  # noqa: BLE001 - tokenizers raises its own types
        log.warning("Modal KV path off: tokenizer not loaded (%s)", type(exc).__name__)
        return None
    url = settings.modal_url.rstrip("/").removesuffix("/v1")
    key_env = settings.modal_api_key_env

    metered = MeteredPost(BearerPost(url, api_key_env=key_env), rotation, provider=provider.name)

    def make_backend() -> ModalSGLangBackend:
        return ModalSGLangBackend(
            url,
            tokenizer,
            model_id=MODAL_MODEL_ID,
            eos=tokenizer.eos,
            api_key_env=key_env,
            processor=processor,
            post=metered,
        )

    def probe() -> bool:
        try:
            return provider.probe()
        except ProviderUnavailable:
            return False

    log.info("Modal KV path on")
    return ModalKV(make_backend=make_backend, probe=probe, provider=provider.name)


def _verify_symspell_file(dict_file: Path, store: TextStore) -> bool:
    manifest_path = dict_file.parent / "symspell-manifest.json"
    if not manifest_path.is_file():
        log.warning("symspell-manifest.json missing, falling back to store")
        return False
    try:
        manifest_data = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError) as exc:
        log.warning(
            "symspell-manifest.json invalid JSON (%s), falling back to store",
            type(exc).__name__,
        )
        return False
    if not isinstance(manifest_data, dict) or manifest_data.get("corpus_id") != store.corpus_id:
        cid = manifest_data.get("corpus_id") if isinstance(manifest_data, dict) else None
        log.warning(
            "symspell manifest corpus_id mismatch (%s vs %s), falling back to store",
            cid,
            store.corpus_id,
        )
        return False
    files_entry = manifest_data.get("files")
    dict_meta = (
        files_entry.get(dict_file.name, files_entry.get("dictionary.txt"))
        if isinstance(files_entry, dict)
        else None
    )
    expected_sha = dict_meta.get("sha256") if isinstance(dict_meta, dict) else None
    if not expected_sha or sha256_file(dict_file) != expected_sha:
        log.warning("symspell dictionary sha256 mismatch or missing, falling back to store")
        return False
    return True


def _load_slice2_dictionary(
    env: Mapping[str, str],
    store: TextStore,
    preloaded: tuple[threading.Thread, list[Any], float] | None = None,
) -> SymSpell:
    symspell_path = _env(env, "SYMSPELL_DICT")
    t0 = time.perf_counter() if preloaded is None else preloaded[2]
    if symspell_path is not None:
        dict_file = Path(symspell_path)
        if dict_file.is_file():
            if _verify_symspell_file(dict_file, store):
                if preloaded is not None:
                    preloaded[0].join()
                    if preloaded[1] and isinstance(preloaded[1][0], SymSpell):
                        sym = preloaded[1][0]
                    else:
                        sym = load_dictionary(dict_file)
                else:
                    sym = load_dictionary(dict_file)
                elapsed = time.perf_counter() - t0
                log.info("dictionary from file: %d terms in %.3fs", sym.word_count, elapsed)
                return sym
        else:
            log.warning("SYMSPELL_DICT path does not exist, falling back to store")

    sym = build_symspell_in_memory(store)
    elapsed = time.perf_counter() - t0
    log.info("dictionary built from store: %d terms in %.3fs", sym.word_count, elapsed)
    return sym


def _preload_symspell(
    env: Mapping[str, str],
) -> tuple[threading.Thread, list[Any], float] | None:
    symspell_path = _env(env, "SYMSPELL_DICT")
    if symspell_path is None:
        return None
    dict_file = Path(symspell_path)
    if not dict_file.is_file():
        return None
    dict_res: list[Any] = []
    t_preload = time.perf_counter()

    def _preload() -> None:
        try:
            dict_res.append(load_dictionary(dict_file))
        except Exception as exc:  # noqa: BLE001
            dict_res.append(exc)

    th = threading.Thread(target=_preload, name="symspell-preload", daemon=True)
    th.start()
    return (th, dict_res, t_preload)


def _preload_dense(
    env: Mapping[str, str],
    corpus_id: str,
) -> tuple[threading.Thread, list[Any]] | None:
    dense_dir_val = _env(env, "DENSE_DIR")
    dense_model_dir_val = _env(env, "DENSE_MODEL_DIR")
    if dense_dir_val is None or dense_model_dir_val is None:
        return None
    res: list[Any] = []

    def _preload() -> None:
        t_dense = time.perf_counter()
        try:
            dense_store = BinaryInt8Store.open(Path(dense_dir_val), corpus_id=corpus_id)
            dense_embedder = load_model("bge-small-en-v1.5", dense_model_dir_val)
            elapsed_dense = time.perf_counter() - t_dense
            res.append((dense_store, dense_embedder, elapsed_dense))
        except Exception as exc:  # noqa: BLE001
            res.append(exc)

    th = threading.Thread(target=_preload, name="dense-preload", daemon=True)
    th.start()
    return (th, res)


def _init_dense_retriever(
    env: Mapping[str, str],
    store: TextStore,
    router: Router,
    preloaded: tuple[threading.Thread, list[Any]] | None = None,
) -> DenseRetriever | None:
    dense_dir_val = _env(env, "DENSE_DIR")
    dense_model_dir_val = _env(env, "DENSE_MODEL_DIR")
    if dense_dir_val is None or dense_model_dir_val is None:
        log.info("dense retrieval off: DENSE_DIR or DENSE_MODEL_DIR unset")
        return None

    if preloaded is not None:
        th, res_list = preloaded
        t_join = time.perf_counter()
        th.join()
        join_wait_s = time.perf_counter() - t_join
        if not res_list:
            log.error("dense load failed: no result produced")
            return None
        res = res_list[0]
        if isinstance(res, Exception):
            log.error("dense load failed: %s: %s", type(res).__name__, res)
            return None
        dense_store, dense_embedder, elapsed_dense = res
    else:
        t_dense = time.perf_counter()
        try:
            dense_store = BinaryInt8Store.open(Path(dense_dir_val), corpus_id=store.corpus_id)
            dense_embedder = load_model("bge-small-en-v1.5", dense_model_dir_val)
        except Exception as exc:  # noqa: BLE001
            log.error("dense load failed: %s: %s", type(exc).__name__, exc)  # noqa: TRY400
            return None
        elapsed_dense = time.perf_counter() - t_dense
        join_wait_s = 0.0

    concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
    retriever = DenseRetriever(dense_embedder, dense_store, concepts)
    log.info(
        "dense retrieval on: %d vectors loaded in %.3fs (join wait %.3fs)",
        dense_store.count,
        elapsed_dense,
        join_wait_s,
    )
    return retriever


def _load_clarify_prior(env: Mapping[str, str]) -> ClarifyPrior | None:
    raw = _env(env, "CLARIFY_PRIOR_PATH")
    path = Path(raw) if raw is not None else Path("artefacts/clarify_prior.json")
    prior = load_clarify_prior(path)
    if prior is not None:
        log.info(
            "clarify prior loaded: %d sections, %d instruments",
            len(prior.sections),
            len(prior.instruments),
        )
    else:
        log.info("clarify prior off: %s absent or invalid", path)
    return prior


def _init_colbert_scorer(
    env: Mapping[str, str],
) -> ColbertScorer | None:
    colbert_dir_val = _env(env, "COLBERT_DIR")
    if colbert_dir_val is not None:
        t_colbert = time.perf_counter()
        model_dir = Path(colbert_dir_val)
        manifest_path = model_dir.parent / "models-manifest.json"
        try:
            scorer = ColbertScorer.load(model_dir, manifest_path)
        except Exception as exc:  # noqa: BLE001
            log.error("ColBERT load failed: %s: %s", type(exc).__name__, exc)  # noqa: TRY400
            return None
        elapsed_colbert = time.perf_counter() - t_colbert
        log.info("ColBERT reranking on: loaded in %.3fs", elapsed_colbert)
        return scorer
    log.info("ColBERT reranking off: COLBERT_DIR unset")
    return None


def build_runtime(environ: Mapping[str, str] | None = None) -> Runtime:
    """Build everything from the environment (module docstring, "Startup").

    Raises:
        ConfigError: a setting is missing or malformed.
        RuntimeError: the store and router snapshots differ.
        chapterverse.store.StoreLoadError, legal_rag_router.IndexLoadError: damaged data.
    """
    env = os.environ if environ is None else environ
    settings = Settings.from_env(env)
    dict_preload = _preload_symspell(env)
    t0 = time.perf_counter()
    store = open_store(settings.store_dir)
    dense_preload = (
        None if _env_flag(env, "NO_DENSE_OVERLAP") else _preload_dense(env, store.corpus_id)
    )
    router = Router.from_path(settings.router_index, concepts=settings.concepts_index)
    check_snapshots(store, router)
    concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
    bound_ranker: InstrumentRanges | None = None
    if concepts is not None:
        bound_ranker = InstrumentRanges()
        bound_ranker.start_background_build(concepts)
    log.info(
        "data open in %.1fs: corpus %s, snapshot %s",
        time.perf_counter() - t0,
        store.corpus_id[:12],
        store.snapshot,
    )
    persistence = open_persistence(settings)
    config = RotationConfig.from_env(env, settings=settings)
    for name, why in config.skipped:
        log.info("provider %s out of rotation: %s", name, why)
    rotation = ProviderRotation.from_config(config, ledger=persistence.ledger)
    log.info("rotation: %r", rotation)
    modal_kv = load_modal_kv(rotation, settings, env)
    daily_ask_cap_raw = _env(env, "DAILY_ASK_CAP")
    daily_ask_cap = (
        int(_env_nonnegative_number(env, "DAILY_ASK_CAP", DEFAULT_DAILY_ASK_CAP))
        if daily_ask_cap_raw is not None
        else DEFAULT_DAILY_ASK_CAP
    )
    cap_salt = _env(env, "CAP_SALT") or ""
    if daily_ask_cap > 0 and len(cap_salt) < MIN_CAP_SALT_LEN:
        raise ConfigError("CAP_SALT must be at least 32 characters when the daily cap is active")
    global_daily_answers = int(
        _env_number(env, "GLOBAL_DAILY_ANSWERS", DEFAULT_GLOBAL_DAILY_ANSWERS)
    )
    hosting_share = _env_nonnegative_number(env, "HOSTING_SHARE", DEFAULT_HOSTING_SHARE)
    if hosting_share > 1.0:
        raise ConfigError("HOSTING_SHARE must be between 0 and 1")

    t_slice2 = time.perf_counter()
    sym_dict = _load_slice2_dictionary(env, store, preloaded=dict_preload)
    get_nlp()
    slice2_time_s = time.perf_counter() - t_slice2
    log.info("Slice 2 startup: loaded SymSpell and spaCy in %.3fs", slice2_time_s)

    dense_retriever = _init_dense_retriever(env, store, router, preloaded=dense_preload)
    colbert_scorer = _init_colbert_scorer(env)
    clarify_prior = _load_clarify_prior(env)

    pipeline = Pipeline(
        router=router,
        store=store,
        rotation=rotation,
        policy=build_policy(JUDGE_PRESET_NAME),
        modal_kv=modal_kv,
        daily_caps=persistence.daily_caps,
        daily_ask_cap=daily_ask_cap,
        daily_budget=persistence.daily_budget,
        global_daily_answers=global_daily_answers,
        hosting_share=hosting_share,
        dictionary=sym_dict,
        dense=dense_retriever,
        colbert=colbert_scorer,
        bound_ranker=bound_ranker,
        clarify_prior=clarify_prior,
    )
    limiter = TokenBucket(
        per_minute=_env_number(env, "RATE_LIMIT_PER_MINUTE", 10.0),
        burst=int(_env_number(env, "RATE_LIMIT_BURST", 10.0)),
    )
    receipts = persistence.receipts

    def close() -> None:
        persistence.close()
        for provider in rotation.providers.values():
            if isinstance(provider, OpenAICompatProvider):
                provider.close()
        ledger = rotation.ledger
        snapshot = getattr(getattr(ledger, "inner", ledger), "snapshot", None)
        if callable(snapshot):
            log.info("quota ledger at shutdown: %s", json.dumps(snapshot(), sort_keys=True))

    return Runtime(
        settings=settings,
        pipeline=pipeline,
        store=store,
        limiter=limiter,
        receipts=receipts,
        daily_caps=persistence.daily_caps,
        daily_ask_cap=daily_ask_cap,
        cap_salt=cap_salt,
        daily_budget=persistence.daily_budget,
        global_daily_answers=global_daily_answers,
        hosting_share=hosting_share,
        require_access=_env_flag(env, "REQUIRE_ACCESS_HEADERS"),
        workers=int(_env_number(env, "ASK_WORKERS", 4.0)),
        dictionary=sym_dict,
        pin_check=lambda: pin_check(rotation),
        pin_interval_s=_env_number(env, "PIN_CHECK_INTERVAL_S", DAY_S),
        purge=receipts.purge if receipts is not None else None,
        close=close,
    )


class _Maintenance:
    """A daemon thread running periodic tasks: the pin check and the retention purge."""

    def __init__(self, tasks: Sequence[tuple[str, float, Callable[[], object]]]) -> None:
        self._tasks = list(tasks)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="maintenance", daemon=True)

    def start(self) -> None:
        if self._tasks:
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        if not self._tasks:
            return
        due = {name: 0.0 for name, _, _ in self._tasks}
        while not self._stop.is_set():
            now = time.monotonic()
            for name, interval, task in self._tasks:
                if self._stop.is_set():
                    break
                if now >= due[name]:
                    due[name] = now + interval
                    try:
                        task()
                    except Exception as exc:  # noqa: BLE001 - a failed task waits for its next turn
                        log.warning("maintenance task %s failed: %s", name, type(exc).__name__)
            if self._stop.is_set():
                break
            delay = min(due.values()) - time.monotonic()
            self._stop.wait(max(0.001, delay))


# ---------------------------------------------------------------------------- edge


class _Reject(Exception):  # noqa: N818 - control flow
    def __init__(
        self, status: int, code: str, message: str, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.headers = dict(headers or {})

    def response(self) -> JSONResponse:
        return JSONResponse(
            {"error": self.code, "message": self.message},
            status_code=self.status,
            headers={"Cache-Control": "no-store", **self.headers},
        )


def _has_access(headers: Mapping[str, str]) -> bool:
    return bool(headers.get(ACCESS_JWT)) or (
        bool(headers.get(ACCESS_ID)) and bool(headers.get(ACCESS_SECRET))
    )


def _client_key(request: Request, *, access_checked: bool) -> str:
    """The rate-limit key: the Worker's ``X-CV-Client-IP`` when the request passed the
    Access-header check (``REQUIRE_ACCESS_HEADERS=1``), else the socket peer. Without that check
    the header could come from anyone, so it is never trusted."""
    if access_checked:
        forwarded = request.headers.get(CLIENT_IP_HEADER, "").strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client is not None else "unknown"


def _net_class(request: Request, *, access_checked: bool) -> str:
    """The network class: the Worker's ``X-CV-Net-Class`` when the request passed the
    Access-header check (``REQUIRE_ACCESS_HEADERS=1``). Without that check the header could
    come from anyone, so it is never trusted. Missing class counts as 'other'."""
    if access_checked:
        forwarded = request.headers.get(NET_CLASS_HEADER, "").strip().lower()
        if forwarded:
            return forwarded
    return "other"


async def _read_body(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise _Reject(413, "body_too_large", "The request is too large.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise _Reject(413, "body_too_large", "The request is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def parse_ask(
    body: bytes,
    *,
    max_query_bytes: int,
    started: float | None = None,
    client_key: str | None = None,
    net_class: str = "other",
) -> AskRequest:
    """The question in a ``POST /api/ask`` body.

    Raises:
        _Reject: 400 for a malformed body, 413 for a query over ``max_query_bytes``.
    """
    bad = _Reject(400, "bad_request", "The request must be JSON with a text field 'query'.")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise bad from None
    if not isinstance(data, dict) or not isinstance(data.get("query"), str):
        raise bad
    query: str = data["query"]
    try:
        size = len(query.encode("utf-8"))
    except UnicodeEncodeError:
        raise bad from None
    if size > max_query_bytes:
        raise _Reject(
            413, "query_too_long", f"The question is over {max_query_bytes} bytes. Shorten it."
        )
    as_at: date | None = None
    raw_date = data.get("as_at")
    if raw_date not in (None, ""):
        if not isinstance(raw_date, str):
            raise bad
        try:
            as_at = date.fromisoformat(raw_date)
        except ValueError:
            raise _Reject(400, "bad_request", "as_at must be a date, YYYY-MM-DD.") from None
    raw_context = data.get("context") or []
    if (
        not isinstance(raw_context, list)
        or len(raw_context) > MAX_CONTEXT_COORDINATES
        or not all(isinstance(c, str) and 0 < len(c) <= 200 for c in raw_context)  # noqa: PLR2004
    ):
        raise _Reject(400, "bad_request", "context must be a short list of coordinates.")
    return AskRequest(
        query=query,
        as_at=as_at,
        context=tuple(raw_context),
        started=time.perf_counter() if started is None else started,
        client_key=client_key,
        net_class=net_class,
    )


class EventStreamResponse(StreamingResponse):
    """An SSE response that always watches for disconnect and calls ``on_close`` when it ends,
    however it ends (finished, client gone, or cancelled)."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        on_close: Callable[[], None],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as group:

                async def stream() -> None:
                    with contextlib.suppress(OSError):
                        await self.stream_response(send)
                    group.cancel_scope.cancel()

                async def listen() -> None:
                    await self.listen_for_disconnect(receive)
                    group.cancel_scope.cancel()

                group.start_soon(stream)
                group.start_soon(listen)
        finally:
            self._on_close()


def _answer_stream(runtime: Runtime, ask: AskRequest) -> EventStreamResponse:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    closed = threading.Event()

    def push(item: bytes | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def emit(event: Event) -> None:
        if closed.is_set():
            raise StreamClosed
        try:
            push(encode_sse(event))
        except RuntimeError:  # the loop is closed
            raise StreamClosed from None

    def work() -> None:
        try:
            outcome = runtime.pipeline.run(ask, emit, closed.is_set)
            log.info("ask %s", json.dumps(outcome.summary(), sort_keys=True))
            if outcome.fact is not None and runtime.receipts is not None:
                runtime.receipts.save(outcome.fact)
        except Exception as exc:  # noqa: BLE001 - never let a worker die silently
            log.error("answer worker failed: %s", type(exc).__name__)  # noqa: TRY400
        finally:
            with contextlib.suppress(RuntimeError):
                push(None)

    runtime.executor.submit(work)

    async def body() -> AsyncIterator[bytes]:
        while (chunk := await queue.get()) is not None:
            yield chunk

    return EventStreamResponse(body(), on_close=closed.set, headers=SSE_HEADERS)


# ---------------------------------------------------------------------------- the app


def _configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    package = logging.getLogger("chapterverse")
    package.setLevel(getattr(logging, level, logging.INFO))
    if not logging.getLogger().handlers and not package.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        package.addHandler(handler)


def _lifespan(
    factory: Callable[[], Runtime],
) -> Callable[[FastAPI], contextlib.AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        _configure_logging()
        rt = await anyio.to_thread.run_sync(factory)
        tasks: list[tuple[str, float, Callable[[], object]]] = []
        if rt.pin_check is not None:
            tasks.append(("pin_check", rt.pin_interval_s, rt.pin_check))
        if rt.purge is not None:
            tasks.append(("purge", PURGE_INTERVAL_S, rt.purge))
        maintenance = _Maintenance(tasks)
        maintenance.start()
        application.state.runtime = rt
        try:
            yield
        finally:
            maintenance.stop()
            rt.shutdown()

    return lifespan


async def _admit(rt: Runtime, request: Request, started: float) -> AskRequest:
    """The edge rules, in order: Access, rate limit, body and query size, shape.

    Raises:
        _Reject: the request is refused (403, 429, 413 or 400).
    """
    if rt.require_access and not _has_access(request.headers):
        raise _Reject(403, "access_required", "This origin is reached through the site.")
    raw_ip = _client_key(request, access_checked=rt.require_access)
    net_class = _net_class(request, access_checked=rt.require_access)
    decision = rt.limiter.take(raw_ip)
    if not decision.allowed:
        raise _Reject(
            429,
            "rate_limited",
            "Too many questions from this address. Wait a minute and try again.",
            {"Retry-After": decision.retry_after_header},
        )
    body = await _read_body(request, rt.max_body_bytes)
    client_key = client_key_hash(raw_ip, rt.cap_salt)
    return parse_ask(
        body,
        max_query_bytes=rt.settings.max_query_bytes,
        started=started,
        client_key=client_key,
        net_class=net_class,
    )


def create_app(
    runtime: Runtime | Callable[[], Runtime] | None = None,
    *,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    """The app. ``runtime`` (or a factory) replaces :func:`build_runtime` (tests)."""
    if isinstance(runtime, Runtime):
        built = runtime

        def factory() -> Runtime:
            return built

    elif runtime is None:
        factory = build_runtime
    else:
        factory = runtime

    application = FastAPI(
        title="Chapter & Verse",
        lifespan=_lifespan(factory),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    origins = tuple(cors_origins) if cors_origins is not None else _cors_origins(os.environ)
    if isinstance(runtime, Runtime) and cors_origins is None:
        origins = runtime.settings.cors_origins
    if origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Last-Event-ID", "X-Session-Token", "X-Turnstile-Token"],
            expose_headers=["Retry-After", "X-Session-Token"],
            allow_credentials=False,
            max_age=600,
        )

    def current(request: Request) -> Runtime:
        rt: Runtime = request.app.state.runtime
        return rt

    @application.api_route("/api/health", methods=["GET", "HEAD"])
    async def health(request: Request) -> JSONResponse:
        rt = current(request)
        return JSONResponse(
            {"status": "ok", "corpus_id": rt.store.corpus_id, "snapshot": rt.store.snapshot},
            headers={"Cache-Control": "no-store"},
        )

    @application.post("/api/ask")
    async def ask(request: Request) -> Response:
        started = time.perf_counter()
        rt = current(request)
        try:
            question = await _admit(rt, request, started)
        except _Reject as reject:
            log.info("ask refused at the edge: %s", reject.code)
            return reject.response()
        return _answer_stream(rt, question)

    return application


def _module_app() -> FastAPI:
    return create_app()


app: Final[FastAPI] = _module_app()
"""``uvicorn chapterverse.app:app``: built from the environment at startup."""
