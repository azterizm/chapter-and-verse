"""Personally Identifiable Information (PII) masking pass (plan §5 stage 3).

Replaces person and organisation names, emails, UK phone numbers, UK postcodes,
and UK National Insurance numbers with stable placeholders. Instrument titles
and names in citations are never masked. Reverse map is kept in memory only
for the request and never logged.
"""

from __future__ import annotations

import functools
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from legal_rag_router.normalise import split_title_year, title_key, title_words
from spacy.tokens import Doc, Token

from chapterverse.nlp import Analysis

__all__ = [
    "CORPORATE_SUFFIXES",
    "LEGAL_PUBLIC_STOPLIST",
    "MONTH_AND_DAY_NAMES",
    "Masked",
    "mask_pii",
]

CORPORATE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        "ltd",
        "limited",
        "llp",
        "plc",
        "inc",
        "corp",
        "corporation",
        "co",
        "company",
        "llc",
        "gmbh",
        "sa",
    }
)

MONTH_AND_DAY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "sept",
        "oct",
        "nov",
        "dec",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    }
)

LEGAL_PUBLIC_STOPLIST: Final[frozenset[str]] = frozenset(
    {
        "act",
        "regulations",
        "order",
        "secretary",
        "state",
        "crown",
        "parliament",
        "england",
        "wales",
        "scotland",
        "northern ireland",
        "uk",
        "united kingdom",
        "hmrc",
        "acas",
        "employment tribunal",
        "tribunal",
        "court",
        "eu",
        "ni",
    }
)

SINGLE_WORD_STOPLIST: Final[frozenset[str]] = frozenset(
    {w for item in LEGAL_PUBLIC_STOPLIST if " " not in item for w in (item,)}
)

EMAIL_RE: Final = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE: Final = re.compile(
    r"(?:\+44\s*(?:\(0\)\s*)?|\b0)(?:(?:\d{2}\s*\d{4}\s*\d{4})|(?:\d{3}\s*\d{3}\s*\d{4})|(?:\d{4}\s*\d{3}\s*\d{3})|(?:\d{4}\s*\d{5,6})|(?:\d{5}\s*\d{4,5})|(?:\d{3}\s*\d{4})|(?:[1-9]\d{8,9}))\b|\(?0\d{2,4}\)?[\s.-]*\d{3,4}[\s.-]*\d{3,4}\b"
)
POSTCODE_RE: Final = re.compile(
    r"\b(?:[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}|GIR\s*0AA)\b",
    re.IGNORECASE,
)
# R6: "NI" is masked only as part of a National Insurance number match.
NINO_RE: Final = re.compile(
    r"\b(?:NI\s*(?::\s*)?)?(?:[A-Za-z]{2}\s*(?:\d\s*){6}[A-Za-z]|[A-Za-z]{2}\s*\d{6}\s*[A-Za-z])\b",
    re.IGNORECASE,
)
SECRETARY_OF_STATE_RE: Final = re.compile(
    r"\bSecretary\s+of\s+State(?:\s+for\s+[A-Za-z\s]+)?\b",
    re.IGNORECASE,
)
EXCLUDED_LEGAL_ROLES_RE: Final = re.compile(
    r"\b(?:Attorney\s+General|Lord\s+Chancellor|Crown|Parliament|High\s+Court|Court\s+of\s+Appeal|Supreme\s+Court)\b",
    re.IGNORECASE,
)
INSTRUMENT_TITLE_PATTERNS: Final = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9\s,&'-]+?\s+(?:Act|Regulations|Order|Rules|Measure|Code)(?:\s+(?:1[2-9]\d\d|20\d\d))?)\b"
)

# (start, end, category, text, priority)
Candidate = tuple[int, int, str, str, int]


@dataclass(frozen=True, slots=True)
class Masked:
    """Masked text with an in-memory reverse mapping."""

    text: str
    reverse_map: dict[str, str]

    def unmask(self, text: str | None = None) -> str:
        """Replace all masked placeholders back with their original values."""
        target = self.text if text is None else text
        for placeholder, original in self.reverse_map.items():
            target = target.replace(placeholder, original)
        return target


@functools.lru_cache(maxsize=1)
def _load_symspell_words() -> frozenset[str] | None:
    """Load vocabulary from SymSpell dictionary if available."""
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / "artefacts" / "symspell" / "dictionary.txt",
        Path("artefacts/symspell/dictionary.txt"),
    ]
    for p in candidates:
        if p.is_file():
            try:
                words: set[str] = set()
                with p.open(encoding="utf-8") as f:
                    for line in f:
                        parts = line.strip().split()
                        if parts:
                            words.add(parts[0].lower())
                return frozenset(words)
            except OSError:
                return None
    return None


def _has_corporate_suffix(text: str) -> bool:
    """Check if text ends in a recognised corporate ending."""
    words = text.strip().rstrip(".").split()
    if not words:
        return False
    return words[-1].lower() in CORPORATE_SUFFIXES


def _overlaps(s1: int, e1: int, s2: int, e2: int) -> bool:
    """True if span (s1, e1) overlaps span (s2, e2)."""
    return max(s1, s2) < min(e1, e2)


def _is_instrument_title(title_text: str, router_index: Any) -> bool:
    """Check if title_text matches a known legislation title in router_index."""
    if router_index is None:
        return False
    idx = getattr(router_index, "index", router_index)
    tables = getattr(idx, "tables", {})
    titles_tbl = tables.get("titles")
    cov_titles_tbl = tables.get("coverage_titles")
    if not titles_tbl and not cov_titles_tbl:
        return False

    base, year = split_title_year(title_text)
    words = title_words(base)
    if not words:
        return False
    keys = (title_key(words, year), title_key(words))
    for k in keys:
        if (titles_tbl and k in titles_tbl) or (cov_titles_tbl and k in cov_titles_tbl):
            return True
    return False


def _collect_regex_candidates(text: str) -> list[Candidate]:
    """Collect regex matches for EMAIL, PHONE, POSTCODE, and NI."""
    candidates: list[Candidate] = []
    candidates.extend((m.start(), m.end(), "EMAIL", m.group(), 2) for m in EMAIL_RE.finditer(text))
    candidates.extend((m.start(), m.end(), "PHONE", m.group(), 2) for m in PHONE_RE.finditer(text))
    candidates.extend(
        (m.start(), m.end(), "POSTCODE", m.group(), 2) for m in POSTCODE_RE.finditer(text)
    )
    candidates.extend((m.start(), m.end(), "NI", m.group(), 2) for m in NINO_RE.finditer(text))
    return candidates


def _is_sentence_initial_single_token_span(
    start: int,
    end: int,
    doc: Doc | None,
) -> bool:
    """True if the span corresponds to the first non-space token of a sentence on its own."""
    if doc is None:
        return False
    for sent in doc.sents:
        first_tok = next((t for t in sent if not t.is_space and not t.is_punct), None)
        if (
            first_tok is not None
            and first_tok.idx == start
            and first_tok.idx + len(first_tok.text) >= end
        ):
            return True
    return False


def _collect_ner_candidates(
    analysis: Analysis,
    all_protected: list[tuple[int, int]],
    router_index: Any,
) -> list[Candidate]:
    """Collect candidate spans from spaCy NER entities in analysis."""
    candidates: list[Candidate] = []
    for ent in analysis.entities:
        category: str | None = None
        if ent.label == "PERSON":
            category = "PERSON"
        elif ent.label == "ORG" or (ent.label == "FAC" and _has_corporate_suffix(ent.text)):
            category = "ORG"

        if category is None:
            continue

        stripped = ent.text.strip(" \t\n\r.,;:?!")
        if not stripped or stripped.upper() in ("NI", "N.I."):
            continue
        if stripped.casefold() in LEGAL_PUBLIC_STOPLIST:
            continue

        lead = ent.text.index(stripped)
        start = ent.span[0] + lead
        end = start + len(stripped)

        if category == "PERSON" and _is_sentence_initial_single_token_span(
            start, end, analysis.doc
        ):
            continue
        if any(_overlaps(start, end, ps[0], ps[1]) for ps in all_protected):
            continue
        if _is_instrument_title(stripped, router_index):
            continue
        if re.search(
            r"\b(?:Act|Regulations|Order|Rules)\b", stripped, re.IGNORECASE
        ) and not _has_corporate_suffix(stripped):
            continue

        candidates.append((start, end, category, stripped, 1))
    return candidates


def _process_run(
    run_tokens: list[Token],
    first_word_i: int,
    text: str,
    router_index: Any,
) -> Candidate | None:
    """Validate a candidate token run and return a Candidate if accepted."""
    if run_tokens[0].i == first_word_i and len(run_tokens) == 1:
        return None

    run_start = run_tokens[0].idx
    run_end = run_tokens[-1].idx + len(run_tokens[-1].text)
    run_text = text[run_start:run_end].strip()
    if not run_text:
        return None

    folded = run_text.casefold()
    if folded in LEGAL_PUBLIC_STOPLIST:
        return None
    if _is_instrument_title(run_text, router_index):
        return None
    if re.search(
        r"\b(?:Act|Regulations|Order|Rules)\b", run_text, re.IGNORECASE
    ) and not _has_corporate_suffix(run_text):
        return None

    return (run_start, run_end, "NAME", run_text, 0)


def _is_fallback_token(
    tok: Token,
    symspell_words: frozenset[str] | None,
    all_protected: list[tuple[int, int]],
    existing_candidates: list[Candidate],
) -> bool:
    """Check whether a token qualifies as a fallback name candidate."""
    if tok.is_space or tok.is_punct or not any(c.isalpha() for c in tok.text):
        return False
    tok_lower = tok.text.lower()
    if tok_lower in MONTH_AND_DAY_NAMES or tok_lower in SINGLE_WORD_STOPLIST:
        return False
    tok_start = tok.idx
    tok_end = tok_start + len(tok.text)
    if any(_overlaps(tok_start, tok_end, ps[0], ps[1]) for ps in all_protected):
        return False
    if any(_overlaps(tok_start, tok_end, c[0], c[1]) for c in existing_candidates):
        return False
    if symspell_words is not None:
        return (tok.pos_ == "PROPN" or tok.text[:1].isupper()) and (tok_lower not in symspell_words)
    return tok.pos_ == "PROPN"


def _collect_fallback_candidates(
    text: str,
    doc: Doc | None,
    existing_candidates: list[Candidate],
    all_protected: list[tuple[int, int]],
    router_index: Any,
) -> list[Candidate]:
    """R5 recall fallback for names: PROPN / capitalised words absent from SymSpell."""
    if doc is None:
        return []

    symspell_words = _load_symspell_words()
    candidates: list[Candidate] = []

    for sent in doc.sents:
        first_word_tok = next((t for t in sent if not t.is_space and not t.is_punct), None)
        first_word_i = first_word_tok.i if first_word_tok is not None else -1

        current_run: list[Token] = []

        for tok in sent:
            if not _is_fallback_token(tok, symspell_words, all_protected, existing_candidates):
                if current_run:
                    cand = _process_run(current_run, first_word_i, text, router_index)
                    if cand is not None:
                        candidates.append(cand)
                    current_run = []
                continue

            if not current_run:
                current_run = [tok]
                continue

            prev = current_run[-1]
            between = text[prev.idx + len(prev.text) : tok.idx]
            if between.strip() in ("", "-"):
                current_run.append(tok)
            else:
                cand = _process_run(current_run, first_word_i, text, router_index)
                if cand is not None:
                    candidates.append(cand)
                current_run = [tok]

        if current_run:
            cand = _process_run(current_run, first_word_i, text, router_index)
            if cand is not None:
                candidates.append(cand)

    return candidates


def _resolve_candidates(
    candidates: list[Candidate],
    all_protected: list[tuple[int, int]],
) -> list[tuple[int, int, str, str]]:
    """Select non-overlapping candidates sorted by priority desc, length desc, start asc."""
    valid = [
        c
        for c in candidates
        if not any(_overlaps(c[0], c[1], ps[0], ps[1]) for ps in all_protected)
    ]
    valid.sort(key=lambda c: (-c[4], -(c[1] - c[0]), c[0]))

    chosen: list[tuple[int, int, str, str]] = []
    for start, end, category, c_text, _ in valid:
        if not any(_overlaps(start, end, ch[0], ch[1]) for ch in chosen):
            chosen.append((start, end, category, c_text))

    chosen.sort(key=lambda ch: ch[0])
    return chosen


def mask_pii(
    text: str,
    analysis: Analysis,
    *,
    router_index: Any = None,
    protected_spans: Sequence[tuple[int, int]] = (),
) -> Masked:
    """Mask PII entities with stable placeholders [PERSON 1], [ORG 1], [EMAIL 1], [NAME 1]...

    Args:
        text: Original query or text.
        analysis: Analysis object from `analyse(text)`.
        router_index: Optional RouterIndex or Router instance to verify instrument titles.
        protected_spans: Additional character spans never to mask.

    Returns:
        Masked dataclass containing masked text and in-memory reverse map.
    """
    if not text:
        return Masked(text="", reverse_map={})

    all_protected: list[tuple[int, int]] = list(analysis.protected_spans) + list(protected_spans)

    # Protect Secretary of State, major constitutional bodies, and multi-word public institutions
    all_protected.extend((m.start(), m.end()) for m in SECRETARY_OF_STATE_RE.finditer(text))
    all_protected.extend((m.start(), m.end()) for m in EXCLUDED_LEGAL_ROLES_RE.finditer(text))
    all_protected.extend((m.start(), m.end()) for m in INSTRUMENT_TITLE_PATTERNS.finditer(text))
    for term in ("Employment Tribunal", "Northern Ireland", "United Kingdom"):
        all_protected.extend(
            (m.start(), m.end())
            for m in re.finditer(rf"\b{re.escape(term)}\b", text, re.IGNORECASE)
        )

    # Protect non-corporate facilities, places and geographic entities (e.g. Big Ben, Scotland)
    if analysis.doc is not None:
        all_protected.extend(
            (ent.start_char, ent.end_char)
            for ent in analysis.doc.ents
            if ent.label_ in ("GPE", "LOC")
            or (ent.label_ == "FAC" and not _has_corporate_suffix(ent.text))
        )

    candidates = _collect_regex_candidates(text)
    ner_candidates = _collect_ner_candidates(analysis, all_protected, router_index)
    candidates.extend(ner_candidates)

    fallback_candidates = _collect_fallback_candidates(
        text,
        analysis.doc,
        candidates,
        all_protected,
        router_index,
    )
    candidates.extend(fallback_candidates)

    chosen = _resolve_candidates(candidates, all_protected)

    category_counters: dict[str, int] = defaultdict(int)
    name_to_placeholder: dict[tuple[str, str], str] = {}
    reverse_map: dict[str, str] = {}
    span_replacements: list[tuple[int, int, str]] = []

    for start, end, category, c_text in chosen:
        key = (category, c_text.strip().casefold())
        if key in name_to_placeholder:
            placeholder = name_to_placeholder[key]
        else:
            category_counters[category] += 1
            placeholder = f"[{category} {category_counters[category]}]"
            name_to_placeholder[key] = placeholder
            reverse_map[placeholder] = c_text

        span_replacements.append((start, end, placeholder))

    # Sort replacements right-to-left (descending) so offsets stay valid during replacement
    span_replacements.sort(key=lambda r: r[0], reverse=True)

    masked_text = text
    for start, end, placeholder in span_replacements:
        masked_text = masked_text[:start] + placeholder + masked_text[end:]

    return Masked(text=masked_text, reverse_map=reverse_map)
