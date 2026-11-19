"""Extractive answer: provision text with a lexical highlight, no model (plan section 5 stage 9).

Used when no provider can answer (``providers_unavailable``) or while Modal cold-starts
(``provider_warming``). Each window gets the span of its text that best overlaps the query's
content words, found with the router's own folding and tokenising
(``legal_rag_router.normalise``), so "Compensatory", "compensatory" and an accented or
look-alike spelling meet. No model and no ColBERT (Slice 3 adds the learned span).

Span rule, deterministic: the query's content terms are its folded word and number tokens,
minus stop words and citation words (``section``, ``act``, ...), with a trailing plural ``s``
dropped. A window token matches when its folded, de-pluralised form is a query term and it is
not a bracketed label such as ``(1)``. The span
covers the window of matches, at most :data:`MAX_HIGHLIGHT_CHARS` long, holding the most
distinct terms; ties go to more matches, then the earlier start. ``None`` when nothing matches.
At most :data:`MAX_EXTRACTIVE_WINDOWS` windows are returned: the best scoring, in their
original order (passage ids are kept, so receipts still resolve).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Final

from legal_rag_router.normalise import PARTICLES, tokenise

from chapterverse.contracts import ExtractiveEvent, ExtractiveReason, Window

__all__ = [
    "MAX_EXTRACTIVE_WINDOWS",
    "MAX_HIGHLIGHT_CHARS",
    "extractive_answer",
    "highlight_span",
    "query_terms",
]

MAX_EXTRACTIVE_WINDOWS: Final = 8
MAX_HIGHLIGHT_CHARS: Final = 400
_MIN_WORD_CHARS: Final = 3

_STOP_WORDS: Final = frozenset(
    {
        # function words
        "about", "after", "all", "also", "any", "are", "been", "before", "being", "but", "can",
        "could", "did", "does", "for", "from", "had", "has", "have", "how", "into", "its",
        "may", "more", "must", "not", "only", "other", "our", "out", "over", "shall", "should",
        "such", "than", "that", "their", "them", "then", "there", "these", "they", "this",
        "those", "under", "upon", "was", "were", "what", "when", "where", "whether", "which",
        "who", "whom", "why", "will", "with", "within", "would", "you", "your",
        # citation and question words
        "act", "article", "chapter", "law", "legislation", "order", "para", "paragraph",
        "part", "reg", "regulation", "rule", "say", "says", "sch", "schedule", "sec",
        "section", "statute", "subsection", "tell", "uk",
    }
)  # fmt: skip


def _norm(token: str) -> str:
    """Drop one trailing plural ``s`` from a long word (``awards`` meets ``award``)."""
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):  # noqa: PLR2004
        return token[:-1]
    return token


def _is_label(text: str, start: int, end: int) -> bool:
    """A bracketed sub-division label such as ``(1)`` or ``(a)``: structure, not content."""
    return text[start - 1 : start] == "(" and text[end : end + 1] == ")"


def query_terms(query: str) -> frozenset[str]:
    """The query's content terms, folded and de-pluralised."""
    terms: set[str] = set()
    for token in tokenise(query):
        if token.kind == "number":
            terms.add(token.text)
        elif (
            token.kind == "word"
            and len(token.text) >= _MIN_WORD_CHARS
            and token.text not in _STOP_WORDS
            and token.text not in PARTICLES
        ):
            terms.add(_norm(token.text))
    return frozenset(terms)


def highlight_span(
    text: str, terms: frozenset[str], *, max_chars: int = MAX_HIGHLIGHT_CHARS
) -> tuple[tuple[int, int] | None, int, int]:
    """``(span, distinct, matches)``: the best span of ``text`` for ``terms`` and its score.

    The span is ``(start, end)`` in ``text``, end exclusive, at most ``max_chars`` long and
    starting and ending on a matched token; None (score 0, 0) when no token matches.
    """
    if not terms or not text:
        return None, 0, 0
    hits = [
        (token.start, token.end, _norm(token.text))
        for token in tokenise(text)
        if token.kind != "punct"
        and _norm(token.text) in terms
        and not _is_label(text, token.start, token.end)
    ]
    best: tuple[int, int, int, int] | None = None  # (-distinct, -matches, start, end)
    for i, (start, end, first) in enumerate(hits):
        seen = {first}
        count = 1
        last_end = end
        for _, e, term in hits[i + 1 :]:
            if e - start > max_chars:
                break
            seen.add(term)
            count += 1
            last_end = e
        key = (-len(seen), -count, start, last_end)
        if best is None or key < best:
            best = key
    if best is None:
        return None, 0, 0
    distinct, matches, start, end = -best[0], -best[1], best[2], best[3]
    if end - start > max_chars:  # a single token longer than the cap
        end = start + max_chars
    return (start, end), distinct, matches


def extractive_answer(
    windows: Sequence[Window],
    query: str,
    reason: ExtractiveReason,
    *,
    max_windows: int = MAX_EXTRACTIVE_WINDOWS,
    max_highlight_chars: int = MAX_HIGHLIGHT_CHARS,
) -> ExtractiveEvent:
    """The extractive event: the windows, each with its lexical highlight, and ``reason``.

    Windows keep their passage ids and order. When there are more than ``max_windows``, the
    best scoring are kept (most distinct terms, then most matches, then earliest); a window
    with no match keeps ``highlight=None``.
    """
    if max_windows < 1 or max_highlight_chars < 1:
        raise ValueError("max_windows and max_highlight_chars must be positive")
    terms = query_terms(query)
    scored: list[tuple[int, int, int, Window]] = []
    for index, window in enumerate(windows):
        span, distinct, matches = highlight_span(window.text, terms, max_chars=max_highlight_chars)
        scored.append((distinct, matches, index, dataclasses.replace(window, highlight=span)))
    if len(scored) > max_windows:
        keep = sorted(scored, key=lambda item: (-item[0], -item[1], item[2]))[:max_windows]
        scored = sorted(keep, key=lambda item: item[2])
    return ExtractiveEvent(reason=reason, windows=tuple(item[3] for item in scored))
