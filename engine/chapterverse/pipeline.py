"""The per-request read path (plan section 5): route, read, status, premise, generate and audit,
cite, receipt, as one ordered event stream (docs/ARCHITECTURE.md sections 3, 5 and 10).

:meth:`Pipeline.run` is synchronous and runs in one worker thread per request (ARCHITECTURE
section 6). It hands each event to ``emit`` as soon as it is decided; the app turns them into
SSE. Every stream it produces satisfies ``contracts.validate_order``.

Stages:

1. **Route.** ``Router.route(query, context=...)``. ``route`` first, then a
   ``corrections_applied`` notice when the router corrected spelling.
2. **Non-bound outcomes.** ``ROUTE_AMBIGUOUS`` asks (``clarify``, ``done``). Abstentions,
   out of coverage and unresolved refuse from templates (Slice 1 has no discovery).
3. **Read** (``ROUTE_BOUNDED``). ``partition_filter`` validates the result first. The scope is
   the bound coordinates and their descendants, minus the excluded ones and theirs; a bound
   coordinate inside another bound one adds nothing and is not read again. For each coordinate
   the instrument record alone is read first (``TextStore.instrument``): a title-only, repealed
   or out-of-date instrument is refused without reading its body. Only then is the subtree read.
4. **Status.** :func:`~chapterverse.status.decide_status` per bound coordinate; a status other
   than ``IN_FORCE`` refuses (``TEXT_NOT_HELD`` with its link). When several coordinates are
   bound and only some refuse, the answer goes ahead from the others and each refused one gets
   a ``status_excluded`` notice.
5. **Premise and windows.** ``Premise.from_records`` and ``build_windows`` per instrument, then
   :func:`select_windows` caps what the model sees (below). ``sources`` carries the selected
   windows. Only ``IN_FORCE`` windows go to the model and the auditor, so prospective or
   repealed text is never stated as law in force.
6. **Generate and audit.** ``ChatContinuationGenerator`` on Groq and Cloudflare, or
   ``ModalPathGenerator`` (true KV rollback) when Modal is the provider that would serve and its
   tokenizer and mask processor loaded. Each request gets its own ``RotationJudgeChat``, judge
   and verifier. Each decided sentence is emitted with its system-rendered citations.
7. **Failures.** ``AllProvidersUnavailable`` before any sentence gives ``extractive``
   (``providers_unavailable``, or ``provider_warming`` with a notice while Modal cold-starts);
   after a sentence it gives a ``providers_unavailable`` notice and the answer closes.
8. **Receipt.** The Answer Fact Object, then ``done`` with the timings.

Premise cap. Every block request resends the passages and every judge call sends candidate
windows, and Groq's free tier allows 8,000 tokens a minute. The rendered passages are capped at
:data:`PREMISE_BUDGET_TOKENS` (1,500 estimated tokens, about 6,000 characters) and
:data:`MAX_PROMPT_WINDOWS` (8) windows. With the instructions (about 250 tokens), the question,
a prefix of at most 512 answer tokens and 200 output tokens, a block request stays near 2,500
tokens, so a typical answer (two blocks and three or four judge calls) fits one minute's quota.
A subtree within the cap is sent whole (ERA 1996 s.124: six windows, about 3,000 characters).
A larger one, such as a whole Act, gets the windows with the best lexical overlap with the
question (the extractive span rule, ``chapterverse.extractive``), each trimmed to its span plus
300 characters of context, best first, until the budget is spent; they are then shown in
document order. Extractive mode is not used for this: its reasons mean "no model", and a model
is available. When no window shares a word with the question, the leading windows are used.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Literal

from legal_rag_router import RouteContext, Router, RouteResult, RouteStatus, partition_filter
from legal_rag_router.coordinate import Coordinate as RouterCoordinate
from legal_rag_verifier.citations import find_citations, is_relative
from legal_rag_verifier.corpus import instrument_coordinate
from legal_rag_verifier.premise import Passage, Premise
from symspellpy import SymSpell  # type: ignore[import-untyped]

from chapterverse.audit.continuation import ChatContinuationGenerator, GenerationSettings
from chapterverse.audit.modal_path import ModalPathGenerator
from chapterverse.audit.prompt import (
    CHARS_PER_TOKEN,
    CONTEXT_MARGIN_CHARS,
    estimate_tokens,
    render_passage,
    trim_window_text,
)
from chapterverse.bound_rank import InstrumentRanges, expand_siblings, rank_sections
from chapterverse.citations import instrument_name, provision_label, render_citation
from chapterverse.contracts import (
    AllProvidersUnavailable,
    AnswerFactObject,
    AnswerMode,
    ChatMode,
    ChatRequest,
    ChatResult,
    ClarifyEvent,
    Coordinate,
    DoneEvent,
    ErrorEvent,
    Event,
    ExtractiveEvent,
    ExtractiveReason,
    NoticeEvent,
    Policy,
    ProviderCall,
    ReceiptEvent,
    RefusalEvent,
    RenderedCitation,
    Rotation,
    RouteEvent,
    SentenceEvent,
    SentenceOut,
    SourcesEvent,
    TemporalStatus,
    TextStore,
    Timings,
    Window,
    sha256_hex,
)
from chapterverse.dense.colbert import ColbertScorer
from chapterverse.dense.retriever import DenseRetriever
from chapterverse.discovery import (
    ClarifyPrior,
    NoEvidence,
    Suggest,
    check_out_of_scope,
    discover_step,
    is_named_private_document,
    remove_bare_section,
)
from chapterverse.extractive import (
    MAX_EXTRACTIVE_WINDOWS,
    extractive_answer,
    highlight_span,
    query_terms,
)
from chapterverse.intent import is_quote_request
from chapterverse.nlp import analyse
from chapterverse.normalise import Normalised, find_protected_spans, load_dictionary, normalise
from chapterverse.pii import Masked, mask_pii
from chapterverse.pit_limits import (
    PitLimitRow,
    PitLimits,
    build_pit_window,
    parse_exact_date,
    render_pit_citation,
    render_pit_sentence,
)
from chapterverse.providers.judge_chat import RotationJudgeChat
from chapterverse.providers.openai_compat import ProviderWarming
from chapterverse.providers.rotation import ProviderRotation
from chapterverse.ratelimit import (
    DEFAULT_DAILY_ASK_CAP,
    DEFAULT_GLOBAL_DAILY_ANSWERS,
    DEFAULT_HOSTING_SHARE,
    DailyBudgetStore,
    DailyCapStore,
)
from chapterverse.sections import section_coordinate
from chapterverse.status import StatusDecision, decide_status, instrument_status
from chapterverse.symspell_build import build_symspell_in_memory
from chapterverse.templates import (
    CLARIFY_RELATED_PROVISIONS,
    CLARIFY_WHICH_ACT,
    clarify_event,
    corrections_notice,
    daily_cap_notice,
    global_budget_notice,
    private_document_refusal,
    provider_fallback_notice,
    provider_warming_notice,
    providers_unavailable_notice,
    quote_truncated_notice,
    route_refusal,
    status_refusal,
)
from chapterverse.windows import build_windows

__all__ = [
    "MAX_CONTEXT_COORDINATES",
    "MAX_PROMPT_WINDOWS",
    "PREMISE_BUDGET_TOKENS",
    "TOP_SECTION_WINDOWS",
    "AskRequest",
    "ModalKV",
    "Outcome",
    "Pipeline",
    "PipelineConfig",
    "Selection",
    "StreamClosed",
    "hint_as_at",
    "merge_calls",
    "select_windows",
]

log = logging.getLogger(__name__)

PREMISE_BUDGET_TOKENS: Final = 1500
"""Estimated tokens of rendered passages sent to the model (module docstring)."""
MAX_PROMPT_WINDOWS: Final = 8
"""At most this many windows go to the model (the extractive cap too)."""
TOP_SECTION_WINDOWS: Final = 3
"""Maximum windows allocated to the top-ranked section in round 1 (requirement R8)."""
ROUND2_BREADTH_SECTIONS: Final = 6
"""Maximum ranked sections allocated one window each in round 2 before adding depth."""
UNTRIMMED_MIN_CHARS: Final = 2 * CONTEXT_MARGIN_CHARS
"""Windows longer than this stay untrimmed when the full text fits inside the budget."""
UNTRIMMED_MAX_CHARS: Final = 3200
"""Upper bound on untrimmed long windows so massive schedules remain highlight-trimmed."""
MAX_CONTEXT_COORDINATES: Final = 10
"""Previous-turn coordinates accepted as routing context."""
_REL_SUBSECTION: Final = re.compile(
    r"\b(?:subsection|paragraph|sub-paragraph)\s*\(([0-9A-Za-z]+)\)",
    re.IGNORECASE,
)

INCOMPLETE_NOTICE: Final = providers_unavailable_notice()
NO_TEXT_NOTICE: Final = NoticeEvent(
    code="no_text",
    message="The cited provision holds no text in force to answer from.",
)
CURRENT_TEXT_NOTICE: Final = NoticeEvent(
    code="current_text_only",
    message=(
        "The question asks about an earlier version. Only the current text is held, and the "
        "answer is from it."
    ),
)
ERROR_MESSAGE: Final = "The answer failed because of a fault in the service. Try again."


class StreamClosed(Exception):  # noqa: N818 - a signal, not an error
    """Raised by ``emit`` when the client has gone; the worker stops at once."""


# ---------------------------------------------------------------------------- inputs


@dataclass(frozen=True, slots=True, kw_only=True)
class AskRequest:
    """One question. ``query`` is never logged or stored: only its sha256 is."""

    query: str
    as_at: date | None = None
    context: tuple[Coordinate, ...] = ()
    started: float = field(default_factory=time.perf_counter)
    """``time.perf_counter()`` when the request arrived; timings count from here."""
    client_key: str | None = None
    """``sha256(salt + client_ip)`` for the daily cap (requirements R2); never the raw IP."""
    net_class: str = "other"
    """The network class (``X-CV-Net-Class`` header); defaults to 'other'."""

    @property
    def query_sha256(self) -> str:
        return sha256_hex(self.query.encode("utf-8"))


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineConfig:
    premise_budget_tokens: int = PREMISE_BUDGET_TOKENS
    max_prompt_windows: int = MAX_PROMPT_WINDOWS
    mode: ChatMode = "short"
    generation: GenerationSettings = field(default_factory=GenerationSettings)


@dataclass(frozen=True, slots=True, kw_only=True)
class ModalKV:
    """What the Modal KV path needs, loaded once at startup (``app.load_modal_kv``).

    ``make_backend`` builds a fresh ``ModalSGLangBackend`` for one answer; ``probe`` answers
    whether the server is up now (a cold server starts on it and the answer goes extractive).
    """

    make_backend: Callable[[], Any]
    probe: Callable[[], bool]
    provider: str = "modal"


# ---------------------------------------------------------------------------- outputs


@dataclass(slots=True)
class Outcome:
    """What one request did, for the app's log line (never the query)."""

    query_sha256: str
    route_status: str = ""
    mode: AnswerMode | Literal["error", "closed"] = "error"
    events: list[str] = field(default_factory=list)
    fact: AnswerFactObject | None = None
    first_source_ms: float | None = None
    first_sentence_ms: float | None = None
    first_supported_ms: float | None = None
    total_ms: float = 0.0
    windows_total: int = 0
    windows_sent: int = 0
    selection: str = ""
    failures: list[str] = field(default_factory=list)
    error: str | None = None
    stop_reason: str | None = None

    @property
    def sentences(self) -> tuple[SentenceOut, ...]:
        return self.fact.sentences if self.fact is not None else ()

    def summary(self) -> dict[str, Any]:
        """Loggable fields: hashes, counts, timings, provider names. No text."""
        sentences = self.sentences
        calls = self.fact.calls if self.fact is not None else ()
        return {
            "query_sha256": self.query_sha256,
            "route_status": self.route_status,
            "mode": self.mode,
            "events": len(self.events),
            "answer_id": self.fact.answer_id if self.fact is not None else None,
            "sentences": len(sentences),
            "refused": sum(s.verdict == "refused" for s in sentences),
            "rollbacks": sum(s.rollbacks for s in sentences),
            "windows": f"{self.windows_sent}/{self.windows_total}",
            "selection": self.selection,
            "calls": dict(Counter(f"{c.provider}:{c.purpose}" for c in calls)),
            "failures": dict(Counter(self.failures)),
            "first_source_ms": _round(self.first_source_ms),
            "first_sentence_ms": _round(self.first_sentence_ms),
            "first_supported_ms": _round(self.first_supported_ms),
            "total_ms": _round(self.total_ms),
            "error": self.error,
            "stop_reason": self.stop_reason,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


# ---------------------------------------------------------------------------- window cap


@dataclass(frozen=True, slots=True)
class Selection:
    """The windows sent to the model, renumbered ``P1..Pk`` in document order."""

    windows: tuple[Window, ...]
    strategy: Literal["all", "lexical", "leading", "none", "ranked"]
    total: int
    estimated_tokens: int


def _cost(window: Window) -> int:
    return estimate_tokens(render_passage(window)) + 1


def _renumber(windows: Iterable[Window]) -> tuple[Window, ...]:
    return tuple(dataclasses.replace(w, passage_id=f"P{i}") for i, w in enumerate(windows, start=1))


def _term_overlap(text: str, terms: frozenset[str]) -> int:
    lowered = text.lower()
    return sum(1 for t in terms if t in lowered or (len(t) >= 5 and t[:5] in lowered))  # noqa: PLR2004


def _intra_instrument_xrefs(
    windows: Sequence[Window],
    ordered_secs: list[Coordinate],
    terms: frozenset[str],
    *,
    by_sec: Mapping[Coordinate, Sequence[tuple[int, Window]]],
) -> tuple[list[Coordinate], frozenset[int]]:
    """Find 1-hop intra-instrument cross-references to the top 3 ranked sections.

    Returns ``(updated_ordered_secs, citing_window_indices)``. At most one targeted citing
    section (citing <= 2 distinct sections in that window and overlapping the query) that is not
    already in the top 5 ranked sections is promoted into position 3 so related opt-out or
    exception provisions (e.g. WTR 1998 reg.5 citing reg.4) are reached.
    """
    del by_sec
    top_secs = ordered_secs[:3]
    top_tails = {s.rsplit("/", 1)[-1]: s for s in top_secs}
    citing_windows: set[int] = set()
    citing_sec_score: dict[Coordinate, tuple[int, int]] = {}

    for idx, w in enumerate(windows):
        sec = section_coordinate(w.coordinate)
        cited_secs = {
            m.canonical.split("/", 1)[0]
            for m in find_citations(w.text)
            if m.canonical and not is_relative(m.canonical)
        }
        if any(t in top_tails and top_tails[t] != sec for t in cited_secs):
            citing_windows.add(idx)
            if len(cited_secs) <= 2:  # noqa: PLR2004 - exclude omnibus exception lists
                h_ov = _term_overlap(w.heading, terms)
                t_ov = _term_overlap(w.text, terms)
                if h_ov + t_ov >= 1:
                    prev = citing_sec_score.get(sec, (-1, -1))
                    if (h_ov, t_ov) > prev:
                        citing_sec_score[sec] = (h_ov, t_ov)

    top_five = set(ordered_secs[:5])
    rank_pos = {sec: pos for pos, sec in enumerate(ordered_secs)}
    if len(ordered_secs) <= ROUND2_BREADTH_SECTIONS:
        return list(ordered_secs), frozenset(citing_windows)
    candidates = [
        sec
        for sec, _score in sorted(
            citing_sec_score.items(),
            key=lambda kv: (-kv[1][0], -kv[1][1], rank_pos.get(kv[0], len(ordered_secs))),
        )
        if sec in rank_pos and sec not in top_five
    ]
    updated = list(ordered_secs)
    for extra in candidates[:1]:
        if extra in updated:
            updated.remove(extra)
        updated.insert(min(3, len(updated)), extra)
    return updated, frozenset(citing_windows)


def _push_relative_refs(
    seed: tuple[int, Window, tuple[int, int] | None] | None,
    sub_to_item: Mapping[str, tuple[int, Window, tuple[int, int] | None]],
    push: Callable[[tuple[int, Window, tuple[int, int] | None]], None],
) -> None:
    if seed is None:
        return
    for match in _REL_SUBSECTION.finditer(seed[1].text):
        item = sub_to_item.get(match.group(1).lower())
        if item is not None:
            push(item)


def _order_section_windows(
    sec_windows: list[tuple[int, Window]],
    terms: frozenset[str],
    *,
    sec_coord: Coordinate = "",
    citing_windows: frozenset[int] = frozenset(),
) -> list[tuple[int, Window, tuple[int, int] | None]]:
    scored: list[tuple[int, Window, tuple[int, int] | None]] = []
    best_candidate: tuple[int, Window, tuple[int, int] | None] | None = None
    best_tuple = (-1, -1, -1)
    sub_to_item: dict[str, tuple[int, Window, tuple[int, int] | None]] = {}
    prefix = f"{sec_coord}/" if sec_coord else ""

    for idx, w in sec_windows:
        span, distinct, matches = highlight_span(w.text, terms)
        item = (idx, w, span)
        scored.append(item)
        if prefix and w.coordinate.startswith(prefix):
            sub_to_item[w.coordinate[len(prefix) :].lower()] = item
        if distinct > 0:
            tup = (w.status is TemporalStatus.IN_FORCE, distinct, matches)
            if tup > best_tuple:
                best_tuple = tup
                best_candidate = item

    ordered: list[tuple[int, Window, tuple[int, int] | None]] = []
    used: set[int] = set()

    def push(item: tuple[int, Window, tuple[int, int] | None]) -> None:
        if item[0] not in used:
            ordered.append(item)
            used.add(item[0])

    if best_candidate is not None:
        push(best_candidate)
    op1 = sub_to_item.get("1")
    if op1 is not None and op1[1].status is TemporalStatus.IN_FORCE:
        push(op1)
        _push_relative_refs(op1, sub_to_item, push)
    for item in scored:
        if item[0] in citing_windows:
            push(item)
            break
    if best_candidate is not None and best_candidate is not op1:
        _push_relative_refs(best_candidate, sub_to_item, push)
    for item in scored:
        push(item)
    return ordered


def _group_section_windows(
    windows: Sequence[Window],
    ranked_sections: Sequence[tuple[Coordinate, float]],
    terms: frozenset[str],
) -> list[list[tuple[int, Window, tuple[int, int] | None]]]:
    by_sec: dict[Coordinate, list[tuple[int, Window]]] = {}
    for idx, w in enumerate(windows):
        by_sec.setdefault(section_coordinate(w.coordinate), []).append((idx, w))

    ordered_secs: list[Coordinate] = []
    seen_sections: set[Coordinate] = set()
    for sec_coord, _ in ranked_sections:
        if sec_coord in seen_sections:
            continue
        seen_sections.add(sec_coord)
        if sec_coord in by_sec:
            ordered_secs.append(sec_coord)
        else:
            # Handle sub-section ranked keys or exact-prefix matches in synthetic tests.
            prefix = f"{sec_coord}/"
            matched = [
                (i, w)
                for i, w in enumerate(windows)
                if w.coordinate == sec_coord or w.coordinate.startswith(prefix)
            ]
            if matched:
                by_sec[sec_coord] = matched
                ordered_secs.append(sec_coord)

    if not ordered_secs:
        return []

    ordered_secs, citing_windows = _intra_instrument_xrefs(
        windows, ordered_secs, terms, by_sec=by_sec
    )
    return [
        _order_section_windows(
            by_sec[sec_coord],
            terms,
            sec_coord=sec_coord,
            citing_windows=citing_windows,
        )
        for sec_coord in ordered_secs
    ]


def _add_round3_depth(
    section_lists: list[list[tuple[int, Window, tuple[int, int] | None]]],
    chosen: dict[int, Window],
    max_windows: int,
    try_add: Callable[[int, Window, tuple[int, int] | None], bool],
) -> None:
    depth_secs = section_lists[1 : min(len(section_lists), 3)]
    for _ in range(TOP_SECTION_WINDOWS - 1):
        for sec in depth_secs:
            if len(chosen) >= max_windows:
                return
            for item in sec:
                if item[0] not in chosen:
                    try_add(*item)
                    break


def _add_round4_remaining(
    section_lists: list[list[tuple[int, Window, tuple[int, int] | None]]],
    chosen: dict[int, Window],
    max_windows: int,
    try_add: Callable[[int, Window, tuple[int, int] | None], bool],
) -> None:
    sec_counts = [sum(1 for idx, _, _ in sec if idx in chosen) for sec in section_lists]

    def _round_robin(*, cap: int | None) -> None:
        while len(chosen) < max_windows:
            progressed = False
            for i, sec in enumerate(section_lists):
                if len(chosen) >= max_windows:
                    return
                if cap is not None and sec_counts[i] >= cap:
                    continue
                for item in sec:
                    if item[0] not in chosen and try_add(*item):
                        sec_counts[i] += 1
                        progressed = True
                        break
            if not progressed:
                return

    _round_robin(cap=TOP_SECTION_WINDOWS)
    _round_robin(cap=None)


def _premise_passage(passage: Passage, window: Window) -> Passage:
    """Apply ``window.highlight`` trimming to ``passage`` so the judge sees the same text."""
    if window.highlight is None:
        return passage
    trimmed = trim_window_text(window.text, window.highlight)
    if trimmed == passage.text:
        return passage
    return dataclasses.replace(passage, text=trimmed, limbs=())


def _allocate_ranked_windows(
    section_lists: list[list[tuple[int, Window, tuple[int, int] | None]]],
    max_windows: int,
    budget_tokens: int,
) -> tuple[dict[int, Window], int]:
    chosen: dict[int, Window] = {}
    used = 0

    def try_add(idx: int, w: Window, span: tuple[int, int] | None) -> bool:
        nonlocal used
        if idx in chosen or len(chosen) >= max_windows:
            return False
        if UNTRIMMED_MIN_CHARS < len(w.text) <= UNTRIMMED_MAX_CHARS:
            full_win = dataclasses.replace(w, highlight=None)
            full_cost = _cost(full_win)
            if used + full_cost <= budget_tokens:
                chosen[idx] = full_win
                used += full_cost
                return True
        trimmed = dataclasses.replace(w, highlight=span) if span is not None else w
        cost = _cost(trimmed)
        if used + cost > budget_tokens:
            if chosen:
                return False
            frame = len(render_passage(dataclasses.replace(w, text="", highlight=None)))
            room = int(budget_tokens * CHARS_PER_TOKEN) - frame - CONTEXT_MARGIN_CHARS - 8
            trimmed = dataclasses.replace(w, highlight=(0, max(1, min(len(w.text), room))))
            cost = _cost(trimmed)
        chosen[idx] = trimmed
        used += cost
        return True

    # Round 1: top section gets up to TOP_SECTION_WINDOWS windows
    for item in section_lists[0][:TOP_SECTION_WINDOWS]:
        if not try_add(*item) and len(chosen) >= max_windows:
            break

    # Round 2: ranks 2..ROUND2_BREADTH_SECTIONS get 1 window each
    for next_section in section_lists[1:ROUND2_BREADTH_SECTIONS]:
        if len(chosen) >= max_windows:
            break
        try_add(*next_section[0])

    # Round 3 & 4: with expanded window budget (> MAX_PROMPT_WINDOWS), add depth for ranks 2-3
    # and fill remaining slots round-robin (preferring sections under TOP_SECTION_WINDOWS) so no
    # single section drains all tail slots; otherwise keep legacy rank-order tail fill.
    if max_windows > MAX_PROMPT_WINDOWS:
        _add_round3_depth(section_lists, chosen, max_windows, try_add)
        _add_round4_remaining(section_lists, chosen, max_windows, try_add)
    else:
        for sec in section_lists:
            if len(chosen) >= max_windows:
                break
            for item in sec:
                if not try_add(*item) and len(chosen) >= max_windows:
                    break

    return chosen, used


def select_ranked_windows(
    windows: Sequence[Window],
    ranked_sections: Sequence[tuple[Coordinate, float]],
    query: str,
    *,
    budget_tokens: int = PREMISE_BUDGET_TOKENS,
    max_windows: int = MAX_PROMPT_WINDOWS,
) -> Selection:
    """Cap ``windows`` using section rank order spread across rounds (requirements R3, R8).

    Allocation over ranked sections runs in three rounds, each round in section rank order
    and respecting the token budget and window cap:
    (1) the top section gets up to ``TOP_SECTION_WINDOWS`` = 3 windows (its best-highlight
        window first, then its others in store order);
    (2) every next section gets its single best window (best highlight, else its first window);
    (3) any slots left go to further windows of the ranked sections in rank order.
    Final windows are returned in store order.
    """
    if budget_tokens < 1 or max_windows < 1:
        raise ValueError("budget_tokens and max_windows must be positive")
    if not windows:
        return Selection((), "none", 0, 0)
    full = sum(_cost(w) for w in windows)
    if full <= budget_tokens and len(windows) <= max_windows:
        return Selection(_renumber(windows), "all", len(windows), full)

    terms = query_terms(query)
    section_lists = _group_section_windows(windows, ranked_sections, terms)
    if not section_lists:
        return select_windows(windows, query, budget_tokens=budget_tokens, max_windows=max_windows)

    chosen, used = _allocate_ranked_windows(section_lists, max_windows, budget_tokens)
    if not chosen:
        return select_windows(windows, query, budget_tokens=budget_tokens, max_windows=max_windows)

    return Selection(_renumber(chosen[i] for i in sorted(chosen)), "ranked", len(windows), used)


def select_windows(
    windows: Sequence[Window],
    query: str,
    *,
    budget_tokens: int = PREMISE_BUDGET_TOKENS,
    max_windows: int = MAX_PROMPT_WINDOWS,
) -> Selection:
    """Cap ``windows`` for the prompt (module docstring, "Premise cap").

    Within the cap every window is kept, untrimmed. Over it, windows are ranked in force
    first, then by distinct query terms and matches (``extractive.highlight_span``), and added
    best first, each with its span as ``highlight`` (so the prompt trims it to the span and its
    margin), while they fit the budget. With no match at all, the leading windows are taken in
    order. A single window that alone passes the budget is trimmed to its start.

    Raises:
        ValueError: a non-positive budget or window count.
    """
    if budget_tokens < 1 or max_windows < 1:
        raise ValueError("budget_tokens and max_windows must be positive")
    if not windows:
        return Selection((), "none", 0, 0)
    full = sum(_cost(w) for w in windows)
    if full <= budget_tokens and len(windows) <= max_windows:
        return Selection(_renumber(windows), "all", len(windows), full)
    terms = query_terms(query)
    scored = []
    for index, window in enumerate(windows):
        span, distinct, matches = highlight_span(window.text, terms)
        scored.append(
            (window.status is not TemporalStatus.IN_FORCE, -distinct, -matches, index, span)
        )
    pool = [s for s in scored if s[1] < 0]
    strategy: Literal["lexical", "leading"] = "lexical"
    if not pool:
        strategy = "leading"
        pool = sorted(scored, key=lambda s: (s[0], s[3]))
    else:
        pool.sort(key=lambda s: s[:4])
    chosen: dict[int, Window] = {}
    used = 0
    for *_, index, span in pool:
        if len(chosen) >= max_windows:
            break
        window = windows[index]
        trimmed = dataclasses.replace(window, highlight=span) if span is not None else window
        cost = _cost(trimmed)
        if used + cost > budget_tokens:
            if chosen:
                continue
            # The best window alone is over budget: keep its start, cut to fit the budget
            # (the prompt keeps the highlight plus CONTEXT_MARGIN_CHARS after it).
            frame = len(render_passage(dataclasses.replace(window, text="", highlight=None)))
            room = int(budget_tokens * CHARS_PER_TOKEN) - frame - CONTEXT_MARGIN_CHARS - 8
            trimmed = dataclasses.replace(
                window, highlight=(0, max(1, min(len(window.text), room)))
            )
            cost = _cost(trimmed)
        chosen[index] = trimmed
        used += cost
    return Selection(_renumber(chosen[i] for i in sorted(chosen)), strategy, len(windows), used)


def merge_calls(
    generator_calls: Sequence[ProviderCall], judge_calls: Sequence[ProviderCall]
) -> tuple[ProviderCall, ...]:
    """The answer's calls in time order, judge entries taken from ``RotationJudgeChat``.

    The generator logs a placeholder for each judge reply (provider ``"unknown"``); the judge
    chat logs the same replies, in the same order, with the provider that served them. Each
    placeholder is replaced by its real record, so nothing is counted twice. If the counts
    differ (they never should), the generation calls come first, then the judge's.
    """
    placeholders = sum(c.purpose == "judge" for c in generator_calls)
    if placeholders != len(judge_calls):
        log.warning(
            "judge call count mismatch: %d placeholders, %d records",
            placeholders,
            len(judge_calls),
        )
        return tuple(c for c in generator_calls if c.purpose == "generate") + tuple(judge_calls)
    real = iter(judge_calls)
    return tuple(next(real) if c.purpose == "judge" else c for c in generator_calls)


# ---------------------------------------------------------------------------- the pipeline


@dataclass(slots=True)
class _Read:
    """What the read stage found for a bound result."""

    instruments: dict[Coordinate, dict[str, Any]] = field(default_factory=dict)
    rows: dict[Coordinate, list[dict[str, Any]]] = field(default_factory=dict)
    refused: list[tuple[StatusDecision, str]] = field(default_factory=list)
    """(decision, citation label) per refused bound coordinate."""


class _Stream:
    """Emits events in order, records their types and the first-source/sentence times."""

    def __init__(
        self, emit: Callable[[Event], None], request: AskRequest, outcome: Outcome
    ) -> None:
        self._emit = emit
        self.request = request
        self.outcome = outcome

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.request.started) * 1000.0

    def __call__(self, event: Event) -> None:
        if event.type in ("sources", "extractive") and self.outcome.first_source_ms is None:
            self.outcome.first_source_ms = self.elapsed_ms()
        if event.type == "sentence":
            if self.outcome.first_sentence_ms is None:
                self.outcome.first_sentence_ms = self.elapsed_ms()
            assert isinstance(event, SentenceEvent)  # noqa: S101 - narrowed by type
            if event.sentence.verdict == "supported" and self.outcome.first_supported_ms is None:
                self.outcome.first_supported_ms = self.elapsed_ms()
        self._emit(event)
        self.outcome.events.append(event.type)

    def done(self) -> None:
        self.outcome.total_ms = self.elapsed_ms()
        self(
            DoneEvent(
                timings=Timings(
                    first_source_ms=_round(self.outcome.first_source_ms),
                    first_sentence_ms=_round(self.outcome.first_sentence_ms),
                    total_ms=round(self.outcome.total_ms, 1),
                )
            )
        )


class _Cancellable:
    """The request's view of the shared rotation: refuses every call once the client has gone,
    so no provider call (generation or judge) is spent on an answer nobody will read."""

    def __init__(self, inner: Rotation, cancelled: Callable[[], bool]) -> None:
        self.inner = inner
        self.cancelled = cancelled

    def complete(self, req: ChatRequest) -> ChatResult:
        if self.cancelled():
            raise StreamClosed
        return self.inner.complete(req)

    def available(self) -> list[str]:
        return self.inner.available()


def _available(rotation: Rotation, purpose: Literal["generate", "judge"]) -> list[str]:
    if isinstance(rotation, ProviderRotation):
        return rotation.available(purpose)
    return rotation.available()


def _warming(rotation: Rotation) -> bool:
    return isinstance(rotation, ProviderRotation) and bool(rotation.warming())


def _label(coordinate: Coordinate, instrument: Mapping[str, Any]) -> str:
    head = provision_label(coordinate)
    name = instrument_name(coordinate, instrument)
    return f"{head} {name}" if head else name


def _minimal(coordinates: Sequence[Coordinate]) -> list[Coordinate]:
    """Bound coordinates with those inside another bound one removed, order kept."""
    out: list[Coordinate] = []
    unique = list(dict.fromkeys(coordinates))
    for c in unique:
        inside = any(b != c and (c.startswith(b + "/")) for b in unique)
        if not inside:
            out.append(c)
    return out


def _excluded(coordinate: str, excluded: Sequence[Coordinate]) -> bool:
    return any(coordinate == e or coordinate.startswith(e + "/") for e in excluded)


_MONTH_NUMBERS: Final = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}  # fmt: skip
_HINT_DATE: Final = re.compile(
    r"(?:(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+)?(?:(?P<m>[a-z]+)\.?,?\s+)?(?P<y>(?:1[2-9]|20)\d\d)(?!\d)"
)


def hint_as_at(hint: str | None) -> date | None:
    """The earliest date of the period a router ``temporal_hint`` names, or None.

    "as at 1 January 2010" is 2010-01-01, "in force in March 2010" 2010-03-01, "as at 2010"
    2010-01-01. The earliest day is taken so that a question about any part of the period
    before the version held is refused (``HISTORY_NOT_HELD``), never answered from later text.
    """
    if not hint:
        return None
    match = _HINT_DATE.search(hint.lower())
    if match is None:
        return None
    month = _MONTH_NUMBERS.get(match.group("m") or "", 1)
    day = int(match.group("d")) if match.group("d") and match.group("m") in _MONTH_NUMBERS else 1
    try:
        return date(int(match.group("y")), month, day)
    except ValueError:
        return date(int(match.group("y")), month, 1)


def _where(exc: BaseException) -> str:
    """Exception class and the innermost frame, without the message (which could quote text)."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return type(exc).__name__
    last = frames[-1]
    return f"{type(exc).__name__} at {last.filename.rsplit('/', 1)[-1]}:{last.lineno}"


def _mask_query(text: str, masked: Masked) -> str:
    out = text
    for ph, orig in sorted(masked.reverse_map.items(), key=lambda item: len(item[1]), reverse=True):
        out = out.replace(orig, ph)
    return out


class Pipeline:
    """Runs the read path for one request at a time per call; one instance serves all
    requests (it holds only shared, thread-safe parts)."""

    def __init__(
        self,
        *,
        router: Router,
        store: TextStore,
        rotation: Rotation,
        policy: Policy,
        config: PipelineConfig | None = None,
        modal_kv: ModalKV | None = None,
        primary_provider: str | None = None,
        daily_caps: DailyCapStore | None = None,
        daily_ask_cap: int = DEFAULT_DAILY_ASK_CAP,
        daily_budget: DailyBudgetStore | None = None,
        global_daily_answers: int = DEFAULT_GLOBAL_DAILY_ANSWERS,
        hosting_share: float = DEFAULT_HOSTING_SHARE,
        clock: Callable[[], datetime] | None = None,
        dictionary: SymSpell | None = None,
        dense: DenseRetriever | None = None,
        colbert: ColbertScorer | None = None,
        bound_ranker: InstrumentRanges | None = None,
        clarify_prior: ClarifyPrior | None = None,
        pit_limits: PitLimits | None = None,
    ) -> None:
        self.router = router
        self.store = store
        self.rotation = rotation
        self.policy = policy
        self.config = config or PipelineConfig()
        self.modal_kv = modal_kv
        self.dense = dense
        self.colbert = colbert
        self.clarify_prior = clarify_prior
        self.pit_limits = pit_limits
        if bound_ranker is None:
            concepts = getattr(router, "concepts", None) or getattr(router, "_concepts", None)
            if concepts is not None:
                bound_ranker = InstrumentRanges()
                bound_ranker.start_background_build(concepts)
        self.bound_ranker = bound_ranker
        self._logged_bound_rank_fallback = False
        if primary_provider is None and isinstance(rotation, ProviderRotation):
            order = rotation.order("generate")
            primary_provider = order[0] if order else None
        self.primary_provider = primary_provider
        self.daily_caps = daily_caps
        self.daily_ask_cap = daily_ask_cap
        self.daily_budget = daily_budget
        self.global_daily_answers = global_daily_answers
        self.hosting_share = hosting_share
        self._clock = clock
        self.dictionary = dictionary
        self._vocabulary: frozenset[str] | None = None
        if (
            self.dictionary is not None
            and hasattr(self.dictionary, "words")
            and self.dictionary.words
        ):
            self._vocabulary = frozenset(k.lower() for k in self.dictionary.words)

    def _get_vocabulary(self) -> frozenset[str] | None:
        if self._vocabulary is None:
            dict_obj = self._get_dictionary()
            if dict_obj is not None and hasattr(dict_obj, "words") and dict_obj.words:
                self._vocabulary = frozenset(k.lower() for k in dict_obj.words)
        return self._vocabulary

    def _get_dictionary(self) -> SymSpell:
        if self.dictionary is not None:
            return self.dictionary
        env_dict = os.environ.get("SYMSPELL_DICT")
        if env_dict and Path(env_dict).is_file():
            self.dictionary = load_dictionary(Path(env_dict))
            return self.dictionary
        concepts = getattr(self.router, "_concepts", None) or getattr(self.router, "concepts", None)
        if concepts is not None:
            self.dictionary = build_symspell_in_memory(concepts)
        else:
            self.dictionary = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
        return self.dictionary

    def _current_utc_day(self) -> date:
        if self._clock is not None:
            return self._clock().astimezone(UTC).date()
        return datetime.now(UTC).date()

    def _log_bound_rank_fallback(self) -> None:
        if not self._logged_bound_rank_fallback:
            self._logged_bound_rank_fallback = True
            log.info("bound ranking not ready or unavailable; falling back to lexical selection")

    def _expand_bound_result(self, result: RouteResult, query: str) -> RouteResult:
        if not result.coordinates:
            return result
        expanded: list[str] = []
        for c in result.coordinates:
            expanded.extend(expand_siblings(query, str(c), self.store))
        if expanded and expanded != [str(c) for c in result.coordinates]:
            new_coords = tuple(RouterCoordinate.parse(s) for s in expanded)
            return dataclasses.replace(result, coordinates=new_coords)
        return result

    def __repr__(self) -> str:
        return f"Pipeline(rotation={self.rotation!r}, modal_kv={self.modal_kv is not None})"

    # ------------------------------------------------------------------ entry point

    def run(
        self,
        request: AskRequest,
        emit: Callable[[Event], None],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> Outcome:
        """Answer ``request``, sending each event to ``emit``. Never raises.

        ``emit`` raises :class:`StreamClosed` when the client has gone; ``cancelled`` is
        polled at each sentence boundary. Either stops the work there, with no receipt.
        """
        outcome = Outcome(query_sha256=request.query_sha256)
        stream = _Stream(emit, request, outcome)
        try:
            self._run(request, stream, cancelled)
        except StreamClosed:
            outcome.mode = "closed"
            outcome.fact = None
        except Exception as exc:  # noqa: BLE001 - the stream must end legally whatever failed
            outcome.error = _where(exc)
            outcome.mode = "error"
            outcome.fact = None
            # No traceback or message: either could quote model text. Class and frame only.
            log.error(  # noqa: TRY400
                "pipeline failed: %s (query_sha256=%s)", outcome.error, outcome.query_sha256
            )
            if "done" not in outcome.events:
                try:
                    stream(ErrorEvent(code="internal_error", message=ERROR_MESSAGE))
                    stream.done()
                except StreamClosed:
                    outcome.mode = "closed"
        return outcome

    def _normalise_and_route(
        self, raw: str, context: RouteContext | None
    ) -> tuple[Normalised, Masked, RouteResult]:
        citation_spans = find_protected_spans(raw)
        analysis = analyse(raw, protected_spans=citation_spans)
        router_index = getattr(self.router, "index", None)
        vocab = self._get_vocabulary()
        masked = mask_pii(
            raw,
            analysis,
            router_index=router_index,
            protected_spans=citation_spans,
            vocabulary=vocab,
        )
        name_spans = list(masked.name_spans)

        dict_obj = self._get_dictionary()
        protect_spans = list(citation_spans) + name_spans
        norm = normalise(raw, dictionary=dict_obj, protect=protect_spans)
        norm_result = self.router.route(norm.text, context=context)
        return norm, masked, norm_result

    def _emit_route_with_corrections(
        self,
        stream: _Stream,
        result: RouteResult,
        norm_corrections: Sequence[tuple[str, str]],
    ) -> None:
        stream.outcome.route_status = result.status.value
        combined_corrections = tuple(
            dict.fromkeys((*norm_corrections, *((a, b) for a, b in result.corrections)))
        )
        route_event = RouteEvent.from_route_result(result)
        if combined_corrections:
            route_event = dataclasses.replace(route_event, corrections=combined_corrections)
        stream(route_event)

        notice = corrections_notice(combined_corrections)
        if notice is not None:
            stream(notice)

    def _handle_bare_section(
        self,
        stream: _Stream,
        query: str,
        result: RouteResult,
    ) -> None:
        cleaned = remove_bare_section(query, result.citations)
        router_index = getattr(self.router, "index", None)
        snapshot = getattr(router_index, "snapshot", "") or self.store.snapshot

        out_of_scope = check_out_of_scope(cleaned, snapshot)
        if out_of_scope is not None:
            stream(out_of_scope)
            self._receipt(stream, result, mode="refusal")
            return

        outcome = discover_step(
            self.router,
            cleaned,
            dense=self.dense,
            colbert=self.colbert,
            store=self.store,
            prior=self.clarify_prior,
        )
        if isinstance(outcome, RefusalEvent):  # out_of_scope
            stream(outcome)
            self._receipt(stream, result, mode="refusal")
            return

        if isinstance(outcome, Suggest) and outcome.candidates:
            clarify = ClarifyEvent(
                question=CLARIFY_RELATED_PROVISIONS,
                candidates=outcome.candidates,
            )
        else:
            clarify = ClarifyEvent(
                question=CLARIFY_WHICH_ACT,
                candidates=(),
            )

        stream.outcome.mode = "clarify"
        stream(clarify)
        stream.done()

    def _discover_or_refuse(
        self, stream: _Stream, raw: str, norm_text: str, norm_result: RouteResult
    ) -> None:
        router_index = getattr(self.router, "index", None)
        snapshot = getattr(router_index, "snapshot", "") or self.store.snapshot

        # R1 Private documents: provision_without_instrument (or ROUTE_UNRESOLVED)
        if is_named_private_document(raw, self.router) or is_named_private_document(
            norm_text, self.router
        ):
            stream(private_document_refusal(snapshot))
            self._receipt(stream, norm_result, mode="refusal")
            return

        out_of_scope = check_out_of_scope(raw, snapshot) or check_out_of_scope(norm_text, snapshot)
        if out_of_scope is not None:
            stream(out_of_scope)
            self._receipt(stream, norm_result, mode="refusal")
            return

        outcome = discover_step(
            self.router,
            norm_text,
            dense=self.dense,
            colbert=self.colbert,
            store=self.store,
            prior=self.clarify_prior,
        )
        if isinstance(outcome, RefusalEvent):  # out_of_scope
            stream(outcome)
            self._receipt(stream, norm_result, mode="refusal")
            return
        if isinstance(outcome, NoEvidence):
            stream(outcome.receipt)
            self._receipt(stream, norm_result, mode="refusal")
            return
        if isinstance(outcome, Suggest):
            stream.outcome.mode = "clarify"
            stream(outcome.clarify_event)
            stream.done()
            return

    def _handle_unresolved(
        self,
        stream: _Stream,
        raw: str,
        norm_text: str,
        norm_result: RouteResult,
        norm_corrections: Sequence[tuple[str, str]],
    ) -> None:
        self._emit_route_with_corrections(stream, norm_result, norm_corrections)

        # R1 Private documents
        if (
            norm_result.reason == "provision_without_instrument"
            or norm_result.status is RouteStatus.UNRESOLVED
        ) and (
            is_named_private_document(raw, self.router)
            or is_named_private_document(norm_text, self.router)
        ):
            stream(private_document_refusal(self.store.snapshot))
            self._receipt(stream, norm_result, mode="refusal")
            return

        if norm_result.status is RouteStatus.AMBIGUOUS:
            if norm_result.reason == "provision_without_instrument":
                self._handle_bare_section(stream, norm_text, norm_result)
                return

            stream.outcome.mode = "clarify"
            stream(clarify_event(norm_result, router=self.router, store=self.store))
            stream.done()
            return

        if norm_result.status is RouteStatus.UNRESOLVED:
            if norm_result.reason == "provision_without_instrument":
                self._handle_bare_section(stream, norm_text, norm_result)
                return

            self._discover_or_refuse(stream, raw, norm_text, norm_result)
            return

        stream(route_refusal(norm_result))
        self._receipt(stream, norm_result, mode="refusal")

    def _handle_raw_fallback(self, stream: _Stream, raw_result: RouteResult) -> None:
        stream.outcome.route_status = raw_result.status.value
        stream(RouteEvent.from_route_result(raw_result))

        if raw_result.status is RouteStatus.AMBIGUOUS:
            if raw_result.reason == "provision_without_instrument":
                # R1 Private documents
                if is_named_private_document(stream.request.query, self.router):
                    stream(private_document_refusal(self.store.snapshot))
                    self._receipt(stream, raw_result, mode="refusal")
                    return
                # R2 Bare section without a document
                self._handle_bare_section(stream, stream.request.query, raw_result)
                return

            stream.outcome.mode = "clarify"
            stream(clarify_event(raw_result, router=self.router, store=self.store))
            stream.done()
            return

        stream(route_refusal(raw_result))
        self._receipt(stream, raw_result, mode="refusal")

    def _run(self, request: AskRequest, stream: _Stream, cancelled: Callable[[], bool]) -> None:
        raw = request.query
        context = RouteContext(coordinates=request.context) if request.context else None

        # R2: Route the RAW query first.
        raw_result = self.router.route(raw, context=context)

        # If the raw result is ROUTE_BOUNDED, use it and the raw text;
        # spelling correction is not applied and no corrections notice is sent.
        if raw_result.status is RouteStatus.BOUNDED:
            raw_result = self._expand_bound_result(raw_result, raw)
            stream.outcome.route_status = raw_result.status.value
            stream(RouteEvent.from_route_result(raw_result))
            self._bounded(request, raw_result, stream, cancelled, norm_text=raw)
            return

        norm, masked, norm_result = self._normalise_and_route(raw, context)

        # R2: use the normalised result only if it is ROUTE_BOUNDED,
        # or if the raw result was ROUTE_UNRESOLVED (then discovery runs on the normalised text);
        # in every other case keep the raw result (raw abstentions and clarifications stand).
        if norm_result.status is RouteStatus.BOUNDED:
            norm_result = self._expand_bound_result(norm_result, raw)
            self._emit_route_with_corrections(stream, norm_result, norm.corrections)
            self._bounded(
                request, norm_result, stream, cancelled, masked=masked, norm_text=norm.text
            )
            return

        if raw_result.status is RouteStatus.UNRESOLVED:
            self._handle_unresolved(stream, raw, norm.text, norm_result, norm.corrections)
            return

        self._handle_raw_fallback(stream, raw_result)

    # ------------------------------------------------------------------ bound

    def _read(self, request: AskRequest, result: RouteResult) -> _Read:
        partition_filter(result)  # validates every coordinate; raises FilterError
        expanded: list[str] = []
        for c in result.coordinates:
            expanded.extend(expand_siblings(request.query, str(c), self.store))
        bound = _minimal([str(c) for c in (expanded or result.coordinates)])
        excluded = [str(c) for c in result.excluded]
        read = _Read()
        for coordinate in bound:
            inst = self.store.instrument(coordinate)
            if inst is None:
                raise LookupError("the router bound an instrument the store does not hold")
            inst_coordinate = str(inst.get("coordinate") or instrument_coordinate(coordinate))
            read.instruments.setdefault(inst_coordinate, inst)
            label = _label(coordinate, inst)
            # Instrument-level status from the record alone: no body read when it refuses.
            head = instrument_status(
                inst, as_at=request.as_at, repealed=result.repealed, coordinate=coordinate
            )
            if head.status is not TemporalStatus.IN_FORCE:
                read.refused.append((head, label))
                continue
            rows = self.store.get(coordinate)
            decision = decide_status(
                rows, coordinate, as_at=request.as_at, repealed=result.repealed
            )
            if decision.status is not TemporalStatus.IN_FORCE:
                read.refused.append((decision, label))
                continue
            kept = read.rows.setdefault(inst_coordinate, [inst])
            seen = {str(r.get("coordinate")) for r in kept[1:]}
            for row in rows:
                if row.get("record_type") == "instrument":
                    continue
                row_coord = str(row.get("coordinate"))
                if row_coord in seen or _excluded(row_coord, excluded):
                    continue
                seen.add(row_coord)
                kept.append(row)
        return read

    def _budget_notice(self, request: AskRequest, today: date) -> NoticeEvent | None:
        if (
            request.client_key is not None
            and self.daily_caps is not None
            and self.daily_ask_cap > 0
            and self.daily_caps.get(request.client_key, today) >= self.daily_ask_cap
        ):
            return daily_cap_notice()

        if self.daily_budget is not None and self.global_daily_answers > 0:
            budget = self.daily_budget.get(today)
            hosting_cap = int(self.global_daily_answers * self.hosting_share)
            is_hosting = request.net_class == "hosting"
            over_global = budget.total >= self.global_daily_answers
            over_hosting = is_hosting and budget.hosting >= hosting_cap
            if over_global or over_hosting:
                return global_budget_notice()

        return None

    def _bounded(  # noqa: PLR0912, PLR0915
        self,
        request: AskRequest,
        result: RouteResult,
        stream: _Stream,
        cancelled: Callable[[], bool],
        *,
        masked: Masked | None = None,
        norm_text: str | None = None,
    ) -> None:
        if masked is None:
            citation_spans = find_protected_spans(request.query)
            analysis = analyse(request.query, protected_spans=citation_spans)
            vocab = self._get_vocabulary()
            masked = mask_pii(
                request.query,
                analysis,
                router_index=getattr(self.router, "index", None),
                protected_spans=citation_spans,
                vocabulary=vocab,
            )
        if norm_text is None:
            norm_text = request.query
        pit_as_at = (
            request.as_at if request.as_at is not None else parse_exact_date(result.temporal_hint)
        )
        if request.as_at is None and result.temporal_hint:
            as_at = hint_as_at(result.temporal_hint)
            if as_at is None:
                stream(CURRENT_TEXT_NOTICE)
            else:
                request = dataclasses.replace(request, as_at=as_at)
        if self.pit_limits is not None and pit_as_at is not None and not result.excluded:
            partition_filter(result)
            pit_bound = _minimal([str(c) for c in result.coordinates])
            if len(pit_bound) == 1:
                pit_row = self.pit_limits.lookup(pit_bound[0], pit_as_at)
                if pit_row is not None:
                    self._answer_pit_limit(stream, result, pit_bound[0], pit_as_at, pit_row)
                    return
        read = self._read(request, result)
        version_dates = {
            c: (v if isinstance(v := inst.get("version_date"), str) else None)
            for c, inst in read.instruments.items()
        }
        if not read.rows:
            decision, label = read.refused[0]
            stream(status_refusal(decision, subject=label, snapshot=self.store.snapshot))
            self._receipt(stream, result, mode="refusal", version_dates=version_dates)
            return
        for decision, label in read.refused:
            message = status_refusal(decision, subject=label, snapshot=self.store.snapshot).message
            stream(NoticeEvent(code="status_excluded", message=message))

        windows, passages, titles, citations, provisions = self._windows(request, result, read)
        bound = _minimal([str(c) for c in result.coordinates])
        if (
            windows
            and is_quote_request(norm_text)
            and bound
            and all(c != instrument_coordinate(c) for c in bound)
        ):
            shown = _renumber(
                dataclasses.replace(w, highlight=None) for w in windows[:MAX_EXTRACTIVE_WINDOWS]
            )
            stream.outcome.windows_total = len(windows)
            stream.outcome.windows_sent = len(shown)
            stream.outcome.selection = "quote"
            stream(SourcesEvent(windows=shown))
            stream(ExtractiveEvent(reason="quote_requested", windows=shown))
            if len(windows) > MAX_EXTRACTIVE_WINDOWS:
                stream(quote_truncated_notice(len(shown), len(windows)))
            self._receipt(
                stream,
                result,
                mode="extractive",
                version_dates=version_dates,
                passages={w.passage_id: w.coordinate for w in shown},
                spans={},
            )
            return
        selection = self._select_windows_for_bound(windows, norm_text, bound)
        stream.outcome.windows_total = selection.total
        stream.outcome.windows_sent = len(selection.windows)
        stream.outcome.selection = selection.strategy
        shown = selection.windows
        stream(SourcesEvent(windows=shown))
        model_windows = tuple(w for w in shown if w.status is TemporalStatus.IN_FORCE)
        spans = {w.coordinate: w.highlight for w in shown if w.highlight is not None}
        passage_map = {w.passage_id: w.coordinate for w in shown}
        if not model_windows:
            stream(NO_TEXT_NOTICE)
            self._receipt(
                stream,
                result,
                mode="generated",
                version_dates=version_dates,
                passages=passage_map,
                spans=spans,
            )
            return
        premise = Premise(
            tuple(_premise_passage(passages[w.coordinate], w) for w in model_windows),
            frozenset(citations),
            tuple(dict.fromkeys(titles)),
        )
        today = self._current_utc_day()
        notice = self._budget_notice(request, today)
        if notice is not None:
            stream(notice)
            spans = self._extractive(
                stream, request, model_windows, "providers_unavailable", query=norm_text
            )
            self._receipt(
                stream,
                result,
                mode="extractive",
                version_dates=version_dates,
                passages=passage_map,
                spans=spans,
            )
            return

        mode, sentences, calls, extra_spans = self._answer(
            request,
            stream,
            cancelled=cancelled,
            premise=premise,
            windows=model_windows,
            read=read,
            provisions=provisions,
            masked=masked,
            norm_text=norm_text,
        )
        if mode == "generated" and bool(calls):
            if (
                request.client_key is not None
                and self.daily_caps is not None
                and self.daily_ask_cap > 0
            ):
                self.daily_caps.increment(request.client_key, today)
            if self.daily_budget is not None and self.global_daily_answers > 0:
                self.daily_budget.increment(today, hosting=(request.net_class == "hosting"))
        spans.update(extra_spans)
        self._receipt(
            stream,
            result,
            mode=mode,
            version_dates=version_dates,
            passages=passage_map,
            spans=spans,
            sentences=sentences,
            calls=calls,
        )

    def _select_windows_for_bound(
        self,
        windows: Sequence[Window],
        norm_text: str,
        bound: list[str],
    ) -> Selection:
        is_whole_instrument = len(bound) == 1 and len(bound[0].split("/")) in (4, 5)
        full_cost = sum(_cost(w) for w in windows)
        windows_fit = (
            full_cost <= self.config.premise_budget_tokens
            and len(windows) <= self.config.max_prompt_windows
        )
        if not is_whole_instrument or windows_fit:
            return select_windows(
                windows,
                norm_text,
                budget_tokens=self.config.premise_budget_tokens,
                max_windows=self.config.max_prompt_windows,
            )

        ranked_sections: list[tuple[Coordinate, float]] = []
        if self.bound_ranker is not None and self.bound_ranker.ready:
            concepts = getattr(self.router, "concepts", None) or getattr(
                self.router, "_concepts", None
            )
            if concepts is not None:
                ranked_sections = rank_sections(
                    norm_text,
                    bound[0],
                    concepts=concepts,
                    ranges=self.bound_ranker,
                    dense=self.dense,
                )
        if ranked_sections:
            return select_ranked_windows(
                windows,
                ranked_sections,
                norm_text,
                budget_tokens=self.config.premise_budget_tokens,
                max_windows=self.config.max_prompt_windows,
            )

        self._log_bound_rank_fallback()
        return select_windows(
            windows,
            norm_text,
            budget_tokens=self.config.premise_budget_tokens,
            max_windows=self.config.max_prompt_windows,
        )

    def _windows(
        self, request: AskRequest, result: RouteResult, read: _Read
    ) -> tuple[
        list[Window],
        dict[Coordinate, Passage],
        list[str],
        set[str],
        dict[Coordinate, dict[str, Any]],
    ]:
        windows: list[Window] = []
        passages: dict[Coordinate, Passage] = {}
        titles: list[str] = []
        citations: set[str] = set()
        provisions: dict[Coordinate, dict[str, Any]] = {}
        for rows in read.rows.values():
            premise = Premise.from_records(rows)
            titles.extend(premise.titles)
            citations |= premise.citations
            for row in rows[1:]:
                provisions.setdefault(str(row["coordinate"]), row)
            for passage in premise.passages:
                if passage.coordinate:
                    passages.setdefault(passage.coordinate, passage)
            windows.extend(
                build_windows(premise, rows, as_at=request.as_at, repealed=result.repealed)
            )
        return windows, passages, titles, citations, provisions

    # ------------------------------------------------------------------ generation

    def _answer(
        self,
        request: AskRequest,
        stream: _Stream,
        *,
        cancelled: Callable[[], bool],
        premise: Premise,
        windows: tuple[Window, ...],
        read: _Read,
        provisions: Mapping[Coordinate, Mapping[str, Any]],
        masked: Masked,
        norm_text: str,
    ) -> tuple[
        AnswerMode,
        tuple[SentenceOut, ...],
        tuple[ProviderCall, ...],
        dict[Coordinate, tuple[int, int]],
    ]:
        """Generate and audit; returns the mode, sentences, calls and extractive spans."""
        masked_query = _mask_query(norm_text, masked)

        if not (gen_ready := _available(self.rotation, "generate")) or not _available(
            self.rotation, "judge"
        ):
            reason: ExtractiveReason = (
                "provider_warming" if _warming(self.rotation) else "providers_unavailable"
            )
            stream.outcome.failures.append("none_available")
            return (
                "extractive",
                (),
                (),
                self._extractive(stream, request, windows, reason, query=norm_text),
            )

        rotation = _Cancellable(self.rotation, cancelled)
        judge_chat = RotationJudgeChat(rotation)
        by_id = {w.passage_id: w for w in windows}
        sentences: list[SentenceOut] = []
        noticed = False

        def deliver(sentence: SentenceOut, gen_calls: Sequence[ProviderCall]) -> None:
            nonlocal noticed
            if cancelled():
                raise StreamClosed
            if not noticed:
                noticed = True
                first = next((c for c in gen_calls if c.purpose == "generate"), None)
                if (
                    first is not None
                    and self.primary_provider is not None
                    and first.provider != self.primary_provider
                ):
                    stream(provider_fallback_notice())
            sentences.append(sentence)
            self._deliver_sentence(
                stream, sentence, by_id=by_id, read=read, provisions=provisions, masked=masked
            )

        use_modal = self.modal_kv is not None and gen_ready[0] == self.modal_kv.provider
        generator: ChatContinuationGenerator | ModalPathGenerator | None = None
        if use_modal:
            assert self.modal_kv is not None  # noqa: S101 - checked above
            if not self.modal_kv.probe():  # cold: the probe starts it; answer meanwhile
                stream.outcome.failures.append(f"{self.modal_kv.provider}:warming")
                spans = self._extractive(
                    stream, request, windows, "provider_warming", query=norm_text
                )
                return "extractive", (), (), spans
        try:
            if use_modal:
                assert self.modal_kv is not None  # noqa: S101 - checked above
                modal_gen = ModalPathGenerator(
                    self.modal_kv.make_backend(),
                    judge_chat,
                    self.policy,
                    query=masked_query,
                    premise=premise,
                    windows=windows,
                    mode=self.config.mode,
                    on_sentence=lambda s: deliver(s, modal_gen.calls),
                    started=request.started,
                    answer_budget_s=self.config.generation.answer_budget_s,
                )
                generator = modal_gen
                for _ in modal_gen:  # sentences were delivered by on_sentence as decided
                    pass
            else:
                chat_gen = ChatContinuationGenerator(
                    rotation,
                    judge_chat,
                    self.policy,
                    query=masked_query,
                    premise=premise,
                    windows=windows,
                    mode=self.config.mode,
                    settings=self.config.generation,
                    started=request.started,
                    answer_budget_s=self.config.generation.answer_budget_s,
                )
                generator = chat_gen
                for sentence in chat_gen:
                    deliver(sentence, chat_gen.calls)
        except AllProvidersUnavailable as exc:
            stream.outcome.failures.extend(f"{f.provider}:{f.kind}" for f in exc.failures)
            if generator is not None and generator.stop_reason is not None:
                stream.outcome.stop_reason = generator.stop_reason
            warming = any(isinstance(f, ProviderWarming) for f in exc.failures)
            reason = "provider_warming" if warming else "providers_unavailable"
            return self._incomplete_answer(
                stream,
                request,
                windows=windows,
                norm_text=norm_text,
                sentences=sentences,
                generator=generator,
                judge_chat=judge_chat,
                reason=reason,
            )
        assert generator is not None  # noqa: S101 - set before iterating
        stream.outcome.stop_reason = generator.stop_reason
        if generator.stop_reason == "time_budget":
            if not sentences:
                stream.outcome.failures.append("time_budget")
            return self._incomplete_answer(
                stream,
                request,
                windows=windows,
                norm_text=norm_text,
                sentences=sentences,
                generator=generator,
                judge_chat=judge_chat,
                reason="providers_unavailable",
            )
        return "generated", tuple(sentences), merge_calls(generator.calls, judge_chat.calls), {}

    def _deliver_sentence(
        self,
        stream: _Stream,
        sentence: SentenceOut,
        *,
        by_id: Mapping[str, Window],
        read: _Read,
        provisions: Mapping[Coordinate, Mapping[str, Any]],
        masked: Masked,
    ) -> None:
        unmasked_text = masked.unmask(sentence.text)
        user_sentence = dataclasses.replace(sentence, text=unmasked_text)
        stream(
            SentenceEvent(
                sentence=user_sentence, citations=self._cite(sentence, by_id, read, provisions)
            )
        )

    def _incomplete_answer(
        self,
        stream: _Stream,
        request: AskRequest,
        *,
        windows: Sequence[Window],
        norm_text: str,
        sentences: Sequence[SentenceOut],
        generator: ChatContinuationGenerator | ModalPathGenerator | None,
        judge_chat: RotationJudgeChat,
        reason: ExtractiveReason,
    ) -> tuple[
        AnswerMode,
        tuple[SentenceOut, ...],
        tuple[ProviderCall, ...],
        dict[Coordinate, tuple[int, int]],
    ]:
        done_calls = merge_calls(generator.calls if generator is not None else (), judge_chat.calls)
        if not sentences:
            return (
                "extractive",
                (),
                done_calls,
                self._extractive(stream, request, windows, reason, query=norm_text),
            )
        stream(INCOMPLETE_NOTICE)
        return "generated", tuple(sentences), done_calls, {}

    def _extractive(
        self,
        stream: _Stream,
        request: AskRequest,
        windows: Sequence[Window],
        reason: ExtractiveReason,
        query: str | None = None,
    ) -> dict[Coordinate, tuple[int, int]]:
        if reason == "provider_warming":
            stream(provider_warming_notice())
        q = query or request.query
        event = extractive_answer(windows, q, reason, max_windows=self.config.max_prompt_windows)
        stream(event)
        return {w.coordinate: w.highlight for w in event.windows if w.highlight is not None}

    def _cite(
        self,
        sentence: SentenceOut,
        by_id: Mapping[str, Window],
        read: _Read,
        provisions: Mapping[Coordinate, Mapping[str, Any]],
    ) -> tuple[RenderedCitation, ...]:
        if sentence.verdict == "refused":
            return ()
        out: list[RenderedCitation] = []
        for pid in sentence.passage_ids:
            window = by_id.get(pid)
            if window is None:
                continue
            inst = read.instruments.get(instrument_coordinate(window.coordinate))
            if inst is None:
                continue
            out.append(
                render_citation(pid, window.coordinate, inst, provisions.get(window.coordinate))
            )
        return tuple(out)

    def _answer_pit_limit(
        self,
        stream: _Stream,
        result: RouteResult,
        bound_coordinate: Coordinate,
        as_at: date,
        row: PitLimitRow,
    ) -> None:
        window = build_pit_window(row)
        sentence = render_pit_sentence(row, as_at)
        citation = render_pit_citation(row)
        version_dates: dict[Coordinate, str | None] = {row.si: row.valid_from.isoformat()}
        inst = self.store.instrument(bound_coordinate)
        if inst is not None:
            inst_coord = str(inst.get("coordinate") or instrument_coordinate(bound_coordinate))
            version_dates[inst_coord] = (
                v if isinstance(v := inst.get("version_date"), str) else None
            )
        stream.outcome.windows_total = 1
        stream.outcome.windows_sent = 1
        stream.outcome.selection = "pit_limits"
        stream(SourcesEvent(windows=(window,)))
        stream(SentenceEvent(sentence=sentence, citations=(citation,)))
        self._receipt(
            stream,
            result,
            mode="generated",
            version_dates=version_dates,
            passages={window.passage_id: window.coordinate},
            spans={},
            sentences=(sentence,),
            calls=(),
        )

    # ------------------------------------------------------------------ receipt

    def _receipt(
        self,
        stream: _Stream,
        result: RouteResult,
        *,
        mode: AnswerMode,
        version_dates: Mapping[Coordinate, str | None] | None = None,
        passages: Mapping[str, Coordinate] | None = None,
        spans: Mapping[Coordinate, tuple[int, int]] | None = None,
        sentences: tuple[SentenceOut, ...] = (),
        calls: tuple[ProviderCall, ...] = (),
    ) -> None:
        fact = AnswerFactObject(
            query_sha256=stream.request.query_sha256,
            route_status=result.status.value,
            coordinates=tuple(str(c) for c in result.coordinates),
            passages=dict(passages or {}),
            version_dates=dict(version_dates or {}),
            spans=dict(spans or {}),
            sentences=sentences,
            corpus_id=self.store.corpus_id,
            calls=calls,
            policy_hash=self.policy.policy_hash,
            mode=mode,
        )
        stream.outcome.mode = mode
        stream.outcome.fact = fact
        stream(ReceiptEvent(answer_id=fact.answer_id, fact=fact))
        stream.done()
