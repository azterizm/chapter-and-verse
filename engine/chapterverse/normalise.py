"""Query normalisation: masking, SymSpell spelling correction, and unmasking.

Follows Plan §5 stage 2 and slice 2 requirements (R1, R2, R4).
Protected spans (citations, identifiers, numbers, acronyms, quotes) are masked,
unprotected words are spell-corrected against the SymSpell dictionary, and the
text is unmasked.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from legal_rag_router.grammars.uk_grammar import GRAMMAR as UK_GRAMMAR
from legal_rag_router.identifiers import scan_identifiers
from legal_rag_router.normalise import fold
from symspellpy import SymSpell, Verbosity  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from legal_rag_router import Router

__all__ = ["Normalised", "find_protected_spans", "load_dictionary", "normalise"]

logger = logging.getLogger(__name__)

# R4: never change words of 3 letters or fewer
MIN_CORRECTION_WORD_LEN = 3

# R2: token containing a digit (with optional currency or punctuation)
_DIGIT_TOKEN_RE = re.compile(r"(?:\$|£|€)?\b\d[\d,.]*\b|\b[\w./-]*\d[\w./-]*\b")

# R2: tokens in ALL CAPS of 2 to 6 letters (acronyms such as ERA, TUPE, PACE)
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")

# R2: quoted text (double quotes, curly quotes, non-contraction single quotes)
_QUOTED_RE = re.compile(
    r"\"[^\"]*\"|\u201c[^\u201d]*\u201d|(?<!\w)\'[^\']+\'(?!\w)|\u2018[^\u2019]*\u2019"
)

# Word token pattern for spelling correction
_WORD_RE = re.compile(r"\b[A-Za-z]+\b")

# Internal placeholder template
_TAG_FMT = "\x00CV_PROTECTED_{}\x00"
_TAG_SPLIT_RE = re.compile(r"(\x00CV_PROTECTED_\d+\x00)")

_NEGATING_PREFIXES: Final = ("un", "in", "im", "il", "ir", "non", "dis", "mis")
_MIN_INFLECTION_PREFIX_LEN: Final = 5
_MIN_INFLECTION_WORD_LEN: Final = 6

_VOWELS: Final = frozenset("aeiouy")


@dataclass(frozen=True, slots=True)
class Normalised:
    """The result of query normalisation.

    Attributes:
        text: The corrected query string with protected spans preserved.
        corrections: Tuple of (original_word, corrected_word) in order of appearance.
        masked_spans: Tuple of (start, end) offsets of protected spans in the original query.
    """

    text: str
    corrections: tuple[tuple[str, str], ...]
    masked_spans: tuple[tuple[int, int], ...]


def load_dictionary(path: Path | str) -> SymSpell:
    """Load a SymSpell dictionary from a file path."""
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    sym.load_dictionary(str(path), term_index=0, count_index=1, separator=" ", encoding="utf-8")
    return sym


def _raw_protected_spans(query: str, router: Router | None) -> list[tuple[int, int]]:
    """Gather all raw candidate protected spans from grammar, identifiers and patterns."""
    raw_spans: list[tuple[int, int]] = [(m.start, m.end) for m in scan_identifiers(query)]

    folded = fold(query)
    raw_spans.extend((p.start, p.end) for p in UK_GRAMMAR.provisions(query, folded))
    raw_spans.extend((n.start, n.end) for n in UK_GRAMMAR.numbers(query, folded))
    raw_spans.extend(
        (c.start, c.end)
        for c in UK_GRAMMAR.cues(query, folded)
        if c.kind in ("date", "temporal", "out_of_coverage")
    )

    if router is not None:
        try:
            route_res = router.route(query)
            raw_spans.extend((cit.span[0], cit.span[1]) for cit in route_res.citations)
        except (AttributeError, KeyError, ValueError):
            pass

    raw_spans.extend((m.start(), m.end()) for m in _DIGIT_TOKEN_RE.finditer(query))
    raw_spans.extend((m.start(), m.end()) for m in _ACRONYM_RE.finditer(query))
    raw_spans.extend((m.start(), m.end()) for m in _QUOTED_RE.finditer(query))
    return raw_spans


def find_protected_spans(
    query: str,
    *,
    router: Router | None = None,
    extra_spans: Sequence[tuple[int, int]] = (),
) -> list[tuple[int, int]]:
    """Identify all protected spans according to R2 rules and resolve overlaps."""
    raw_spans = _raw_protected_spans(query, router)
    raw_spans.extend(extra_spans)

    # Overlap resolution following legal-rag-mask core mechanism:
    # Prioritise longer spans first
    raw_spans.sort(key=lambda s: s[1] - s[0], reverse=True)

    valid_spans: list[tuple[int, int]] = []
    for start, end in raw_spans:
        if start >= end:
            continue
        overlap = any(max(start, v_start) < min(end, v_end) for v_start, v_end in valid_spans)
        if not overlap:
            valid_spans.append((start, end))

    # Sort ascending by start offset
    valid_spans.sort(key=lambda s: s[0])
    return valid_spans


_find_protected_spans = find_protected_spans


def _apply_casing(original: str, candidate: str) -> str:
    """R4: preserve original casing pattern."""
    if original.isupper():
        return candidate.upper()
    if original.istitle() or (original[0].isupper() and original[1:].islower()):
        return candidate.capitalize()
    return candidate.lower()


def _is_middle_of_sentence(preceding: str) -> bool:
    """Return True if word is in the middle of a sentence (not at sentence start)."""
    cleaned = preceding.rstrip(" \t\n\r\"'\u201c\u201d\u2018\u2019([{\\-")
    if not cleaned:
        return False
    return cleaned[-1] not in ".?!"


def _has_prefix_added_or_removed(orig: str, sugg: str) -> bool:
    """Return True if sugg adds or removes a negating prefix relative to orig."""
    for p in _NEGATING_PREFIXES:
        if orig.startswith(p) and not sugg.startswith(p):
            rem = orig[len(p) :].lstrip("-")
            if sugg == rem:
                return True
        if sugg.startswith(p) and not orig.startswith(p):
            rem = sugg[len(p) :].lstrip("-")
            if orig == rem:
                return True
    return False


_MAX_EDIT_DIST_1_LEN: Final = 7


def _is_correction_candidate(
    word: str,
    lower_word: str,
    *,
    is_middle_of_sentence: bool,
) -> bool:
    """Return True if word is eligible for spelling correction under R2 safety rules."""
    if len(word) <= MIN_CORRECTION_WORD_LEN or any(c.isdigit() for c in word):
        return False
    if is_middle_of_sentence and word[0].isupper():
        return False
    # A token with no vowel (y counts) is not a word: never correct it.
    return any(c in _VOWELS for c in lower_word)


def _correct_word(  # noqa: PLR0911
    word: str,
    sym: SymSpell,
    *,
    is_middle_of_sentence: bool = False,
) -> tuple[str, tuple[str, str] | None, bool, bool]:
    """Check and correct a single word under R4 correction policy and R2 safety rules.

    Returns:
        (replacement_word, correction_pair_or_none, evaluated_bool, miss_bool)
    """
    lower_word = word.lower()
    if not _is_correction_candidate(word, lower_word, is_middle_of_sentence=is_middle_of_sentence):
        return word, None, False, False

    # R2 safety rule: never apply a correction when original word is in the dictionary
    if lower_word in sym.words:
        return word, None, True, False

    # R1: allowed edit distance is 1 for 4 to 7 letters, 2 for 8 letters or more
    max_dist = 1 if len(word) <= _MAX_EDIT_DIST_1_LEN else 2

    suggs = sym.lookup(lower_word, Verbosity.ALL, max_edit_distance=max_dist)
    if not suggs:
        return word, None, True, True

    min_dist = suggs[0].distance
    if min_dist > max_dist:
        return word, None, True, False

    closest = [s for s in suggs if s.distance == min_dist]

    is_unique_best = len(closest) == 1 or (len(closest) > 1 and closest[0].count > closest[1].count)
    if not is_unique_best:
        return word, None, True, False

    best_sugg = closest[0]
    if best_sugg.distance > max_dist:
        return word, None, True, False

    best_term = best_sugg.term

    # R2 safety rule: never apply a correction when original word is in the dictionary
    if lower_word in sym.words:
        return word, None, True, False

    # R2 safety rule: never apply a correction when original and suggestion share the same first 5
    # letters and differ only in the ending (inflection or derivation), unless the original has
    # no dictionary entry and the edit distance is 1 and the word is 6 or more letters
    if (
        len(lower_word) >= _MIN_INFLECTION_PREFIX_LEN
        and len(best_term) >= _MIN_INFLECTION_PREFIX_LEN
        and lower_word[:_MIN_INFLECTION_PREFIX_LEN] == best_term[:_MIN_INFLECTION_PREFIX_LEN]
    ):
        allowed = (
            (lower_word not in sym.words)
            and (min_dist == 1)
            and (len(lower_word) >= _MIN_INFLECTION_WORD_LEN)
        )
        if not allowed:
            return word, None, True, False

    # R2 safety rule: never apply a correction that adds or removes a negating prefix
    # (un, in, im, il, ir, non, dis, mis) relative to the original
    if _has_prefix_added_or_removed(lower_word, best_term):
        return word, None, True, False

    corrected_word = _apply_casing(word, best_term)
    if corrected_word != word:
        return corrected_word, (word, corrected_word), True, False
    return word, None, True, False


def _correct_segment(
    seg: str,
    sym: SymSpell,
    *,
    query_pos: int = 0,
    query: str = "",
) -> tuple[str, list[tuple[str, str]], int, int]:
    """Correct spelling in an unprotected text segment."""
    matches = list(_WORD_RE.finditer(seg))
    seg_chars = list(seg)
    seg_corrections: list[tuple[str, str]] = []
    evaluated = 0
    misses = 0

    # Process right-to-left to safely modify character slices
    for m in reversed(matches):
        word = m.group()
        word_start = query_pos + m.start()
        preceding = query[:word_start] if query else seg[: m.start()]
        is_middle = _is_middle_of_sentence(preceding)

        corrected_word, corr, was_eval, was_miss = _correct_word(
            word, sym, is_middle_of_sentence=is_middle
        )
        if was_eval:
            evaluated += 1
        if was_miss:
            misses += 1
        if corr is not None:
            seg_corrections.append(corr)
            seg_chars[m.start() : m.end()] = list(corrected_word)

    seg_corrections.reverse()
    return "".join(seg_chars), seg_corrections, evaluated, misses


def normalise(
    query: str,
    *,
    dictionary: SymSpell | Path | str,
    router: Router | None = None,
    protect: Sequence[tuple[int, int]] = (),
) -> Normalised:
    """Normalise a query string: mask protected spans, correct spelling, unmask.

    Args:
        query: User query string.
        dictionary: SymSpell instance or path to dictionary.txt.
        router: Optional Router instance to assist citation span detection.
        protect: Optional extra character spans to protect from spelling correction.

    Returns:
        Normalised dataclass with corrected text, corrections list, and masked spans.
    """
    if not query:
        return Normalised(text="", corrections=(), masked_spans=())

    sym: SymSpell = dictionary if isinstance(dictionary, SymSpell) else load_dictionary(dictionary)

    valid_spans = find_protected_spans(query, router=router, extra_spans=protect)

    # Masking mechanism (legal-rag-mask placeholder & reverse-map)
    span_tags = [
        (start, end, _TAG_FMT.format(i), query[start:end])
        for i, (start, end) in enumerate(valid_spans)
    ]

    masked_text = query
    reverse_map: dict[str, str] = {}
    for start, end, tag, original in sorted(span_tags, key=lambda x: x[0], reverse=True):
        masked_text = masked_text[:start] + tag + masked_text[end:]
        reverse_map[tag] = original

    segments = _TAG_SPLIT_RE.split(masked_text)
    corrected_segments: list[str] = []
    corrections: list[tuple[str, str]] = []
    words_evaluated = 0
    miss_count = 0
    query_pos = 0

    for seg in segments:
        if seg in reverse_map:
            corrected_segments.append(seg)
            query_pos += len(reverse_map[seg])
            continue

        c_seg, seg_corrs, seg_eval, seg_miss = _correct_segment(
            seg, sym, query_pos=query_pos, query=query
        )
        query_pos += len(seg)
        corrected_segments.append(c_seg)
        corrections.extend(seg_corrs)
        words_evaluated += 1 if seg_eval else 0
        miss_count += seg_miss

    miss_rate = (miss_count / words_evaluated) if words_evaluated > 0 else 0.0
    logger.debug(
        "normalise: evaluated=%d misses=%d miss_rate=%.4f",
        words_evaluated,
        miss_count,
        miss_rate,
    )

    reconstructed = "".join(corrected_segments)
    for tag, original in reverse_map.items():
        reconstructed = reconstructed.replace(tag, original)

    return Normalised(
        text=reconstructed,
        corrections=tuple(corrections),
        masked_spans=tuple(valid_spans),
    )
