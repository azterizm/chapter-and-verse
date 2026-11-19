"""The shared contract every engine module codes against (docs/ARCHITECTURE.md).

Plain frozen dataclasses, TypedDicts and Protocols, stdlib-typed. Nothing here does I/O.

Rules that hold across the engine:

* Text reaches generation only through a coordinate the router bound (router contract).
* The model cites passage ids (``P1`` ...) only. Citation strings are rendered by the system
  from those ids (:class:`RenderedCitation`); any other citation string fails the sentence.
* The receipt (:class:`AnswerFactObject`) holds the hash of the query, never the query.
* The chat layer is synchronous. Pipelines run it in a worker thread (threading model).
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal, Protocol, TypeAlias, TypedDict, runtime_checkable

from legal_rag_router import RouteResult, RouteStatus
from legal_rag_verifier.engine import DEFAULT_REFUSAL

__all__ = [
    "AFO_SCHEMA_VERSION",
    "EVENT_TYPES",
    "PREFILL_USER_TEMPLATE",
    "PROVIDER_NAMES",
    "REFUSAL_KINDS",
    "AllProvidersUnavailable",
    "AnswerFactObject",
    "AnswerMode",
    "ChatMessage",
    "ChatMode",
    "ChatProvider",
    "ChatRequest",
    "ChatResult",
    "ChatRole",
    "ClarifyEvent",
    "Coordinate",
    "DoneEvent",
    "ErrorEvent",
    "Event",
    "EventType",
    "ExtractiveEvent",
    "ExtractiveReason",
    "FinishReason",
    "JudgeChat",
    "JudgeReply",
    "LabelledCoordinate",
    "NoticeEvent",
    "Policy",
    "ProviderCall",
    "ProviderUnavailable",
    "Purpose",
    "ReceiptEvent",
    "RefusalEvent",
    "RenderedCitation",
    "Rotation",
    "RouteEvent",
    "SentenceEvent",
    "SentenceOut",
    "SourcesEvent",
    "TemporalStatus",
    "TextStore",
    "Timings",
    "UnavailableKind",
    "Verdict",
    "Window",
    "canonical_json",
    "encode_sse",
    "fold_prefill",
    "sha256_hex",
    "to_jsonable",
    "validate_order",
]

# ============================================================================ coordinates

Coordinate: TypeAlias = str
"""A router coordinate as a string: ``uk/ukpga/1996/18/s124/1ZA``.

``legal_rag_router.Coordinate`` objects convert with ``str()``. Instrument coordinates have
four parts for calendar-numbered instruments (``uk/ukpga/1996/18``) and five for regnal-year
Acts (``uk/ukpga/Vict/24-25/100``); use ``legal_rag_verifier.corpus.instrument_coordinate``.
"""


class TemporalStatus(StrEnum):
    """The status of a bound provision's text (plan §5 stage 6, legal-rag-temporal PLAN §3)."""

    IN_FORCE = "IN_FORCE"
    """Current text applies."""
    NOT_YET_IN_FORCE = "NOT_YET_IN_FORCE"
    """Prospective: a provision record with ``prospective: true``."""
    REPEALED = "REPEALED"
    """A provision record with ``repealed: true`` (or an instrument record with it)."""
    TEXT_NOT_HELD = "TEXT_NOT_HELD"
    """Title-only instrument: ``structure == "metadata_only"``, no provision rows."""
    HISTORY_NOT_HELD = "HISTORY_NOT_HELD"
    """The question's ``as_at`` date is before the instrument record's ``version_date``."""


# ============================================================================ text store


@runtime_checkable
class TextStore(Protocol):
    """Read access to provision text (agent B1). One instance is shared by all requests.

    Implementations must be safe to call from several worker threads at once.
    """

    @property
    def corpus_id(self) -> str:
        """Identity of this store build (e.g. the sha256 of its manifest). Goes in receipts."""
        ...

    @property
    def snapshot(self) -> str:
        """ISO date of the source data the store was built from (matches the router index)."""
        ...

    def get(self, coordinate: Coordinate) -> list[dict[str, Any]]:
        """The instrument record, then the provision at ``coordinate`` and all its descendants.

        Rows are in the exact shape of ``legal_rag_verifier.corpus.load_provision`` (the router's
        ``data/uk`` JSONL rows, file order), so ``Premise.from_records`` takes the list unchanged.
        For an instrument coordinate the list is the instrument record plus every provision; for
        a title-only instrument that is the instrument record alone.

        Raises:
            KeyError: the coordinate is not in the store.
        """
        ...

    def instrument(self, coordinate: Coordinate) -> dict[str, Any] | None:
        """The instrument record for any coordinate within it, or None if the store lacks it."""
        ...

    def has_text(self, instrument_coordinate: Coordinate) -> bool:
        """False for a title-only instrument (``structure == "metadata_only"``).

        Raises:
            KeyError: the instrument is not in the store.
        """
        ...


# ============================================================================ windows


@dataclass(frozen=True, slots=True)
class Window:
    """One numbered passage shown to the model, the auditor and the user.

    ``passage_id`` is ``"P1"``, ``"P2"`` ... in prompt order. ``text`` is the window text as
    built by ``Premise.from_records`` (one subsection: stem, children, tail). ``highlight`` is a
    ``(start, end)`` character span within ``text`` (end exclusive), or None.
    """

    passage_id: str
    coordinate: Coordinate
    heading: str
    text: str
    status: TemporalStatus
    version_date: str | None
    source_url: str
    highlight: tuple[int, int] | None = None


# ============================================================================ chat layer

ChatRole: TypeAlias = Literal["system", "user", "assistant"]
ChatMode: TypeAlias = Literal["short", "synthesis"]
Purpose: TypeAlias = Literal["generate", "judge"]
FinishReason: TypeAlias = Literal["stop", "length", "content_filter", "other"]
UnavailableKind: TypeAlias = Literal[
    "quota", "rate_limit", "server", "timeout", "model_changed", "config"
]

PROVIDER_NAMES: Final = ("groq", "cloudflare", "modal")
"""Provider names in rotation order (plan §5 stage 7). Tests may use other names."""


class ChatMessage(TypedDict):
    role: ChatRole
    content: str


PREFILL_USER_TEMPLATE: Final = (
    "{content}\n\nContinue this answer from exactly where it stops. Do not repeat it.\n"
    "<<<\n{prefill}\n>>>"
)
"""How :func:`fold_prefill` writes a prefill into the last user turn."""


def fold_prefill(messages: Sequence[ChatMessage], prefill: str) -> tuple[ChatMessage, ...]:
    """Move an assistant prefill into the last user turn, for providers without prefill support.

    Raises:
        ValueError: there is no user message.
    """
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i]["role"] == "user":
            content = PREFILL_USER_TEMPLATE.format(content=out[i]["content"], prefill=prefill)
            out[i] = ChatMessage(role="user", content=content)
            return tuple(out)
    raise ValueError("fold_prefill needs a user message")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """One chat completion request. Not hashable (``messages`` holds dicts).

    ``prefill`` is text the assistant turn already holds: the model continues it, and
    :attr:`ChatResult.text` holds the continuation only, never the prefill. A provider whose
    ``supports_prefill`` is False sends ``fold_prefill(messages, prefill)`` instead. Sampling
    values are passed through as given; ``mode`` picks the provider's model settings
    (``"synthesis"`` enables thinking), ``purpose`` picks generation or judge settings and is
    recorded in :class:`ProviderCall`.
    """

    messages: tuple[ChatMessage, ...]
    max_tokens: int
    temperature: float
    top_p: float
    stop: tuple[str, ...] = ()
    mode: ChatMode = "short"
    prefill: str | None = None
    seed: int | None = None
    purpose: Purpose = "generate"


@dataclass(frozen=True, slots=True)
class ChatResult:
    """The reply to one :class:`ChatRequest` from one provider.

    ``quantisation`` is None when the provider does not state it. Token counts are None when
    the provider does not report them. ``latency_ms`` is wall time of the successful call.
    """

    text: str
    finish_reason: FinishReason
    provider: str
    model_id: str
    quantisation: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float


@runtime_checkable
class ChatProvider(Protocol):
    """One OpenAI-compatible chat endpoint (agent B3). Synchronous; thread-safe.

    API keys are read from the environment at call time and never stored on the instance or
    shown in its repr (the rule of ``legal_rag_verifier.judge.OpenAIChat``).
    """

    @property
    def name(self) -> str: ...
    @property
    def model_id(self) -> str: ...
    @property
    def quantisation(self) -> str | None: ...
    @property
    def supports_prefill(self) -> bool: ...

    def complete(self, req: ChatRequest) -> ChatResult:
        """One completion.

        Raises:
            ProviderUnavailable: the provider cannot serve the request now.
        """
        ...


class ProviderUnavailable(Exception):  # noqa: N818 - the name is the contract
    """A provider cannot serve a request: out of quota, rate limited, failing, or unpinned.

    Subclasses ``Exception`` directly: ``legal_rag_verifier.judge.Judge`` swallows ValueError,
    KeyError, OSError, TimeoutError and TypeError as failed attempts. ``detail`` is free text
    for logs and must never hold a secret.
    """

    def __init__(
        self,
        provider: str,
        kind: UnavailableKind,
        retry_after_s: float | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(provider, kind, retry_after_s, detail)
        self.provider = provider
        self.kind: UnavailableKind = kind
        self.retry_after_s = retry_after_s
        self.detail = detail

    def __str__(self) -> str:
        after = "" if self.retry_after_s is None else f", retry after {self.retry_after_s:g} s"
        detail = f": {self.detail}" if self.detail else ""
        return f"{self.provider} unavailable ({self.kind}{after}){detail}"


class AllProvidersUnavailable(Exception):  # noqa: N818 - the name is the contract
    """Every provider in the rotation failed or was skipped for one request.

    The pipeline answers in extractive mode when this is raised before the first sentence.
    It propagates out of ``Judge`` and ``JudgedVerifier`` unchanged (it is not one of the
    exception types the judge retries).
    """

    def __init__(self, failures: Sequence[ProviderUnavailable]) -> None:
        super().__init__(tuple(failures))
        self.failures: tuple[ProviderUnavailable, ...] = tuple(failures)

    @property
    def retry_after_s(self) -> float | None:
        """The soonest any provider said it would be back, or None."""
        waits = [f.retry_after_s for f in self.failures if f.retry_after_s is not None]
        return min(waits) if waits else None

    def __str__(self) -> str:
        if not self.failures:
            return "no provider configured"
        return "all providers unavailable: " + "; ".join(str(f) for f in self.failures)


@runtime_checkable
class Rotation(Protocol):
    """The ordered providers with circuit breakers, quota ledger and pinning (agent B3)."""

    def complete(self, req: ChatRequest) -> ChatResult:
        """The first provider able to serve ``req``, in order.

        Raises:
            AllProvidersUnavailable: none could.
        """
        ...

    def available(self) -> list[str]:
        """Names of the providers currently in rotation (circuit closed, quota left, pinned)."""
        ...


# ============================================================================ judge bridge


class JudgeReply(TypedDict):
    """What ``legal_rag_verifier.judge.Judge`` reads from one chat call (``OpenAIChat``'s keys)."""

    content: str
    ms: float
    tokens: int | None
    finish: str | None


@runtime_checkable
class JudgeChat(Protocol):
    """The ``chat`` callable ``legal_rag_verifier.judge.Judge(chat)`` expects.

    ``Judge`` calls ``chat(messages)`` with ``[{"role": ..., "content": ...}, ...]`` and reads
    ``content``, ``ms`` and ``tokens`` from the reply. It reads ``chat.model`` (via getattr) as
    its ``model_id``. A failure the judge should retry raises ValueError or OSError; a full
    outage raises :class:`AllProvidersUnavailable`, which the judge does not catch.
    """

    @property
    def model(self) -> str: ...

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply: ...


# ============================================================================ policy


@dataclass(frozen=True, slots=True, kw_only=True)
class Policy:
    """The generation and audit policy one answer ran under. Its hash goes in the receipt.

    ``judge_preset`` names the judge configuration (agent B3's presets). ``prompt_sha256`` is
    the sha256 of the generation prompt template (agent B4). ``verifier_config_name`` names a
    ``VerifierConfig`` of the pinned verifier (``STRICT_FIGURES``).
    """

    judge_preset: str
    prompt_sha256: str
    max_rollbacks_per_sentence: int = 3
    max_total_rollbacks: int = 8
    refusal_text: str = DEFAULT_REFUSAL
    steering: Literal["inject", "ban", "allow"] = "inject"
    verifier_config_name: str = "STRICT_FIGURES"

    @property
    def policy_hash(self) -> str:
        """sha256 hex of :func:`canonical_json` of the policy's fields."""
        return sha256_hex(canonical_json(self))


# ============================================================================ answers

Verdict: TypeAlias = Literal["supported", "refused"]
AnswerMode: TypeAlias = Literal["generated", "extractive", "refusal", "clarify"]


@dataclass(frozen=True, slots=True)
class SentenceOut:
    """One audited sentence as streamed. ``index`` is 0-based in the answer.

    ``text`` never contains a citation string: citations are rendered from ``passage_ids``.
    A refused sentence carries ``Policy.refusal_text`` and empty ``passage_ids``. ``reasons`` are
    the verifier's reason codes (``GROUNDED``, ``UNGROUNDED_FIGURE`` ...). ``rollbacks`` counts
    discarded drafts, as the verifier's engine counts them.
    """

    index: int
    text: str
    verdict: Verdict
    passage_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    rollbacks: int


@dataclass(frozen=True, slots=True)
class RenderedCitation:
    """A citation the system rendered for a passage id (agent B2 renders).

    ``label`` reads like ``s.124(1ZA)(a) Employment Rights Act 1996``; ``url`` is the
    legislation.gov.uk URL of that provision.
    """

    passage_id: str
    coordinate: Coordinate
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """One successful chat call made for an answer, generation or judge."""

    provider: str
    model_id: str
    quantisation: str | None
    purpose: Purpose
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float

    @classmethod
    def from_result(cls, result: ChatResult, purpose: Purpose) -> ProviderCall:
        return cls(
            provider=result.provider,
            model_id=result.model_id,
            quantisation=result.quantisation,
            purpose=purpose,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
        )


AFO_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerFactObject:
    """The research receipt for one answer (determinism note §2.2). Its hash is the answer id.

    ``query_sha256`` is :func:`sha256_hex` of the query's UTF-8 bytes as received; the query
    itself is never stored. ``route_status`` is the router's ``RouteStatus`` value.
    ``coordinates`` are the bound coordinates. ``passages`` maps each passage id to its window
    coordinate, so a sentence's ``passage_ids`` resolve without the stream. ``version_dates``
    maps each instrument coordinate to its record's ``version_date``. ``spans`` maps a window
    coordinate to the highlighted character span within that window's text.
    """

    query_sha256: str
    route_status: str
    coordinates: tuple[Coordinate, ...]
    passages: Mapping[str, Coordinate]
    version_dates: Mapping[Coordinate, str | None]
    spans: Mapping[Coordinate, tuple[int, int]]
    sentences: tuple[SentenceOut, ...]
    corpus_id: str
    calls: tuple[ProviderCall, ...]
    policy_hash: str
    mode: AnswerMode
    schema: int = AFO_SCHEMA_VERSION

    @property
    def answer_id(self) -> str:
        """sha256 hex of :func:`canonical_json` of this object."""
        return sha256_hex(canonical_json(self))

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict: lists for tuples, plain dicts, ``from_dict`` inverts it."""
        out = to_jsonable(self)
        assert isinstance(out, dict)  # noqa: S101 - a dataclass always maps to a dict
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AnswerFactObject:
        """Inverse of :meth:`to_dict`. Key order does not matter.

        Raises:
            KeyError, TypeError, ValueError: ``data`` is not a receipt of this schema.
        """
        schema = int(data.get("schema", AFO_SCHEMA_VERSION))
        if schema != AFO_SCHEMA_VERSION:
            raise ValueError(f"receipt schema {schema} is not {AFO_SCHEMA_VERSION}")
        return cls(
            query_sha256=str(data["query_sha256"]),
            route_status=str(data["route_status"]),
            coordinates=tuple(str(c) for c in data["coordinates"]),
            passages={str(k): str(v) for k, v in data["passages"].items()},
            version_dates={
                str(k): None if v is None else str(v) for k, v in data["version_dates"].items()
            },
            spans={str(k): _span(v) for k, v in data["spans"].items()},
            sentences=tuple(
                SentenceOut(
                    index=int(s["index"]),
                    text=str(s["text"]),
                    verdict=_verdict(s["verdict"]),
                    passage_ids=tuple(str(p) for p in s["passage_ids"]),
                    reasons=tuple(str(r) for r in s["reasons"]),
                    rollbacks=int(s["rollbacks"]),
                )
                for s in data["sentences"]
            ),
            corpus_id=str(data["corpus_id"]),
            calls=tuple(
                ProviderCall(
                    provider=str(c["provider"]),
                    model_id=str(c["model_id"]),
                    quantisation=None if c["quantisation"] is None else str(c["quantisation"]),
                    purpose=_purpose(c["purpose"]),
                    prompt_tokens=_opt_int(c["prompt_tokens"]),
                    completion_tokens=_opt_int(c["completion_tokens"]),
                    latency_ms=float(c["latency_ms"]),
                )
                for c in data["calls"]
            ),
            policy_hash=str(data["policy_hash"]),
            mode=_mode(data["mode"]),
            schema=schema,
        )


def _span(value: Any) -> tuple[int, int]:
    start, end = value
    return (int(start), int(end))


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _verdict(value: Any) -> Verdict:
    if value == "supported":
        return "supported"
    if value == "refused":
        return "refused"
    raise ValueError(f"unknown verdict {value!r}")


def _purpose(value: Any) -> Purpose:
    if value == "generate":
        return "generate"
    if value == "judge":
        return "judge"
    raise ValueError(f"unknown purpose {value!r}")


def _mode(value: Any) -> AnswerMode:
    for mode in ("generated", "extractive", "refusal", "clarify"):
        if value == mode:
            return mode
    raise ValueError(f"unknown answer mode {value!r}")


# ============================================================================ canonical JSON


def to_jsonable(obj: Any) -> Any:  # noqa: PLR0911 - one return per JSON kind
    """Convert dataclasses, enums, tuples and mappings to plain JSON types, recursively.

    Dataclasses become dicts of their fields (``init=False`` fields included); enums become
    their values; tuples, lists and frozensets become lists (frozensets sorted); mapping keys
    must be strings.

    Raises:
        TypeError: a value has no JSON form, or a mapping key is not a string.
    """
    if obj is None or isinstance(obj, bool | int | str):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise TypeError("NaN and infinity have no canonical JSON form")
        return obj
    if isinstance(obj, enum.Enum):
        return to_jsonable(obj.value)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError(f"mapping key {key!r} is not a string")
            out[key] = to_jsonable(value)
        return out
    if isinstance(obj, tuple | list):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, frozenset | set):
        return sorted(to_jsonable(v) for v in obj)
    raise TypeError(f"{type(obj).__name__} has no canonical JSON form")


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8 characters kept."""
    return json.dumps(
        to_jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    """sha256 hex digest; a str is hashed as its UTF-8 bytes."""
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# ============================================================================ SSE events

EventType: TypeAlias = Literal[
    "route",
    "clarify",
    "refusal",
    "sources",
    "sentence",
    "extractive",
    "notice",
    "receipt",
    "error",
    "done",
]
EVENT_TYPES: Final[tuple[EventType, ...]] = (
    "route",
    "clarify",
    "refusal",
    "sources",
    "sentence",
    "extractive",
    "notice",
    "receipt",
    "error",
    "done",
)

REFUSAL_KINDS: Final[frozenset[str]] = frozenset(
    {s.value for s in RouteStatus if s not in (RouteStatus.BOUNDED, RouteStatus.AMBIGUOUS)}
    | {s.value for s in TemporalStatus if s is not TemporalStatus.IN_FORCE}
    | {"out_of_scope"}
)
"""Allowed :attr:`RefusalEvent.kind` values: a non-answering router status, a temporal status
other than ``IN_FORCE``, or ``out_of_scope``."""

ExtractiveReason: TypeAlias = Literal["providers_unavailable", "provider_warming"]


@dataclass(frozen=True, slots=True)
class LabelledCoordinate:
    """A coordinate offered to the user with a readable label (clarify chips, suggestions)."""

    coordinate: Coordinate
    label: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RouteEvent:
    """Always first. The router's decision, minus anything that could hold the raw query."""

    status: str
    next_action: str
    coordinates: tuple[Coordinate, ...]
    corrections: tuple[tuple[str, str], ...]
    messages: tuple[str, ...]
    index_snapshot: str
    type: Literal["route"] = field(default="route", init=False)

    @classmethod
    def from_route_result(cls, result: RouteResult) -> RouteEvent:
        return cls(
            status=result.status.value,
            next_action=result.next_action.value,
            coordinates=tuple(str(c) for c in result.coordinates),
            corrections=tuple((a, b) for a, b in result.corrections),
            messages=tuple(result.messages),
            index_snapshot=result.index_snapshot,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ClarifyEvent:
    """The router found several candidates (``ROUTE_AMBIGUOUS``): ask, never pick silently."""

    question: str
    candidates: tuple[LabelledCoordinate, ...]
    type: Literal["clarify"] = field(default="clarify", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class RefusalEvent:
    """A templated refusal (agent B2), no LLM involved. ``kind`` is one of REFUSAL_KINDS.

    ``link`` is the legislation.gov.uk URL given for ``TEXT_NOT_HELD`` (the instrument page)
    and ``HISTORY_NOT_HELD`` (the dated URL); None otherwise.
    """

    kind: str
    message: str
    suggestions: tuple[LabelledCoordinate, ...]
    snapshot: str
    link: str | None = None
    type: Literal["refusal"] = field(default="refusal", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourcesEvent:
    """The windows the answer is built from, before any sentence."""

    windows: tuple[Window, ...]
    type: Literal["sources"] = field(default="sources", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class SentenceEvent:
    """One audited sentence and the citations rendered for its passage ids, in that order."""

    sentence: SentenceOut
    citations: tuple[RenderedCitation, ...]
    type: Literal["sentence"] = field(default="sentence", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractiveEvent:
    """No generation: the provision text with the relevant span highlighted, and why."""

    reason: ExtractiveReason
    windows: tuple[Window, ...]
    type: Literal["extractive"] = field(default="extractive", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class NoticeEvent:
    """Information for the user that does not change the answer.

    Codes in use: ``provider_fallback`` (a later provider served the request),
    ``provider_warming`` (Modal is cold-starting), ``corrections_applied`` (spelling fixed).
    """

    code: str
    message: str
    type: Literal["notice"] = field(default="notice", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReceiptEvent:
    """The Answer Fact Object and its hash. ``answer_id == fact.answer_id``."""

    answer_id: str
    fact: AnswerFactObject
    type: Literal["receipt"] = field(default="receipt", init=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorEvent:
    """Replaces the rest of the stream; ``done`` follows. ``message`` is safe to show."""

    code: str
    message: str
    type: Literal["error"] = field(default="error", init=False)


@dataclass(frozen=True, slots=True)
class Timings:
    """Milliseconds from request start. None when the stream had no source or sentence."""

    first_source_ms: float | None
    first_sentence_ms: float | None
    total_ms: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DoneEvent:
    """Always last."""

    timings: Timings
    type: Literal["done"] = field(default="done", init=False)


Event: TypeAlias = (
    RouteEvent
    | ClarifyEvent
    | RefusalEvent
    | SourcesEvent
    | SentenceEvent
    | ExtractiveEvent
    | NoticeEvent
    | ReceiptEvent
    | ErrorEvent
    | DoneEvent
)


def encode_sse(event: Event) -> bytes:
    """``event: <type>\\ndata: <canonical json of the event, type included>\\n\\n``.

    The JSON is one line: :func:`canonical_json` escapes every newline inside strings.
    """
    return b"event: " + event.type.encode("ascii") + b"\ndata: " + canonical_json(event) + b"\n\n"


# Legal event orders as a state machine (docs/ARCHITECTURE.md, "SSE event order").
_TRANSITIONS: Final[dict[str, dict[str, str]]] = {
    "start": {"route": "routed"},
    "routed": {"notice": "routed", "clarify": "clarified", "refusal": "refused",
               "sources": "sourced"},
    "clarified": {"notice": "clarified", "done": "end"},
    "refused": {"notice": "refused", "receipt": "receipted"},
    "sourced": {"notice": "sourced", "sentence": "generating", "extractive": "extracted",
                "receipt": "receipted"},
    "generating": {"notice": "generating", "sentence": "generating", "receipt": "receipted"},
    "extracted": {"notice": "extracted", "receipt": "receipted"},
    "receipted": {"notice": "receipted", "done": "end"},
    "errored": {"done": "end"},
    "end": {},
}  # fmt: skip


def validate_order(types: Sequence[str]) -> bool:
    """True when ``types`` is a complete, legal event sequence.

    ``route`` first; then ``clarify, done`` or ``refusal, receipt, done`` or
    ``sources, (sentence* | extractive), receipt, done``. ``notice`` may appear anywhere after
    ``route`` and before ``done``. ``error`` may replace the rest of any legal prefix (including
    the empty one) and must be followed by ``done`` alone.
    """
    state = "start"
    for kind in types:
        if kind == "error" and state not in ("errored", "end"):
            state = "errored"
            continue
        nxt = _TRANSITIONS[state].get(kind)
        if nxt is None:
            return False
        state = nxt
    return state == "end"
