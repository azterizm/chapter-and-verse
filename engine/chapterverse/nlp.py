"""spaCy natural language processing pass (plan §5 stage 3).

Thread-safe lazy-loaded spaCy pass yielding:
* noun chunks
* terms: unigrams and bigrams of NOUN, PROPN, ADJ, NUM lemmas
* intent spans: text split at coordinating conjunctions between clauses
* named entities: (text, label, span) for PII masking
* cues: date, negation, existence question cues
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import spacy
from spacy.language import Language
from spacy.tokens import Doc, Span, Token

__all__ = [
    "Analysis",
    "Cues",
    "Entity",
    "analyse",
    "get_nlp",
]

TARGET_POS: Final[frozenset[str]] = frozenset({"NOUN", "PROPN", "ADJ", "NUM"})
NEGATION_WORDS: Final[frozenset[str]] = frozenset(
    {"not", "never", "no", "neither", "nor", "none", "without"}
)
EXIST_LEMMAS: Final[frozenset[str]] = frozenset({"exist", "existence"})
EXIST_AUX_RE: Final = re.compile(
    r"\b(?:is|are|was|were|will\s+there\s+be|has\s+there\s+been|does\s+there)\s+there\b",
    re.IGNORECASE,
)
EXIST_Q_RE: Final = re.compile(
    r"\b(?:does|do|did|can|could|is|are|will|would)\b.*\bexist\b",
    re.IGNORECASE,
)
DATE_RE: Final = re.compile(
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b(?:19\d\d|20\d\d)\b",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=1)
def get_nlp() -> Language:
    """Thread-safe lazy-load of the ``en_core_web_sm`` model."""
    return spacy.load("en_core_web_sm")


@dataclass(frozen=True, slots=True)
class Entity:
    """One named entity extracted from the spaCy doc."""

    text: str
    label: str
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class Cues:
    """Extracted query cues for downstream filtering and routing."""

    has_date: bool
    has_negation: bool
    has_existence_question: bool


@dataclass(frozen=True, slots=True)
class Analysis:
    """Full linguistic analysis of a query."""

    noun_chunks: tuple[str, ...]
    terms: tuple[str, ...]
    intent_spans: tuple[str, ...]
    entities: tuple[Entity, ...]
    cues: Cues
    protected_spans: tuple[tuple[int, int], ...] = ()
    doc: Doc | None = None


def _merge_spans(spans: Sequence[tuple[int, int]], text_len: int) -> list[tuple[int, int]]:
    """Clean, bound, and merge overlapping or adjacent protected spans."""
    valid = [(s, e) for s, e in spans if 0 <= s < e <= text_len]
    if not valid:
        return []
    valid.sort(key=lambda x: (x[0], -x[1]))
    merged: list[tuple[int, int]] = []
    for s, e in valid:
        if not merged:
            merged.append((s, e))
        else:
            prev_s, prev_e = merged[-1]
            if s <= prev_e:
                merged[-1] = (prev_s, max(prev_e, e))
            else:
                merged.append((s, e))
    return merged


def _is_clausal_node(token: Token) -> bool:
    """Check whether a token represents a clausal predicate."""
    if token.pos_ in ("VERB", "AUX"):
        return True
    return any(
        child.dep_ in ("nsubj", "nsubjpass", "csubj", "csubjpass", "expl")
        for child in token.children
    )


def _find_clausal_conjunction_splits(doc: Doc) -> list[int]:
    """Find character offsets of coordinating conjunctions linking two clauses."""
    splits: list[int] = []
    for token in doc:
        if token.pos_ == "CCONJ" or token.dep_ == "cc":
            head = token.head
            conjuncts = [c for c in head.children if c.dep_ == "conj"]
            if _is_clausal_node(head) and any(_is_clausal_node(c) for c in conjuncts):
                splits.append(token.idx)
    return sorted(set(splits))


def _split_intent_spans(text: str, split_indices: list[int]) -> tuple[str, ...]:
    """Split text at identified clausal conjunction indices."""
    trimmed = text.strip()
    if not trimmed:
        return ()
    if not split_indices:
        return (trimmed,)

    spans: list[str] = []
    last_idx = 0
    for idx in split_indices:
        seg = text[last_idx:idx].strip().rstrip(",;").strip()
        if seg:
            spans.append(seg)
        m = re.match(r"\w+\s*", text[idx:])
        conj_len = m.end() if m else len(text[idx:])
        last_idx = idx + conj_len

    tail = text[last_idx:].strip()
    if tail:
        spans.append(tail)
    return tuple(spans)


def _detect_has_date(doc: Doc, text: str, protected_spans: Sequence[tuple[int, int]]) -> bool:
    """Detect date presence, ignoring dates within protected citation spans."""
    for ent in doc.ents:
        if ent.label_ == "DATE" and not any(
            max(ent.start_char, ps[0]) < min(ent.end_char, ps[1]) for ps in protected_spans
        ):
            return True
    for m in DATE_RE.finditer(text):
        if not any(max(m.start(), ps[0]) < min(m.end(), ps[1]) for ps in protected_spans):
            return True
    return False


def _detect_has_negation(doc: Doc) -> bool:
    """Detect negation via dependency parse or morphology."""
    for tok in doc:
        if tok.dep_ == "neg" or tok.lower_ in NEGATION_WORDS or "n't" in tok.text.lower():
            return True
    return False


def _detect_has_existence(doc: Doc, text: str) -> bool:
    """Detect existence questions such as 'is there a law' or 'does ... exist'."""
    for tok in doc:
        if tok.dep_ == "expl" and tok.lower_ == "there" and (tok.head.i < tok.i or "?" in text):
            return True
        if tok.lemma_.lower() in EXIST_LEMMAS:
            return True
    return bool(EXIST_AUX_RE.search(text) or EXIST_Q_RE.search(text))


def _is_sentence_initial_single_token(ent: Span) -> bool:
    """True if ent is a single token that is the first word of its sentence."""
    if len(ent) > 1:
        return False
    tok = ent[0]
    sent = ent.sent
    first_tok = next((t for t in sent if not t.is_space and not t.is_punct), None)
    return first_tok is not None and tok.i == first_tok.i


def _extract_terms(doc: Doc) -> tuple[str, ...]:
    """Extract unigrams and bigrams of NOUN, PROPN, ADJ, NUM lemmas."""
    content_tokens = [t for t in doc if not t.is_space and not t.is_punct]
    unigrams = [t.lemma_.lower() for t in content_tokens if t.pos_ in TARGET_POS]

    bigrams: list[str] = []
    for i in range(len(content_tokens) - 1):
        t1 = content_tokens[i]
        t2 = content_tokens[i + 1]
        if t1.pos_ in TARGET_POS and t2.pos_ in TARGET_POS:
            has_punct_between = any(doc[k].is_punct for k in range(t1.i + 1, t2.i))
            if not has_punct_between:
                bigrams.append(f"{t1.lemma_.lower()} {t2.lemma_.lower()}")

    return (*unigrams, *bigrams)


def _extract_entities(doc: Doc) -> tuple[Entity, ...]:
    """Extract named entities, filtering single-token sentence-initial PERSON spans and NI."""
    raw_entities: list[Entity] = []
    for ent in doc.ents:
        if ent.label_ == "PERSON" and _is_sentence_initial_single_token(ent):
            continue
        if ent.text.strip().upper() in ("NI", "N.I."):
            continue
        raw_entities.append(
            Entity(text=ent.text, label=ent.label_, span=(ent.start_char, ent.end_char))
        )
    return tuple(raw_entities)


def _retokenize_protected(
    doc: Doc,
    merged_protected: list[tuple[int, int]],
    nlp: Language,
) -> Doc:
    """Merge protected character spans into single tokens and re-run pipeline."""
    char_spans = []
    for start, end in merged_protected:
        sp = doc.char_span(start, end, alignment_mode="expand")
        if sp is not None:
            char_spans.append(sp)
    if char_spans:
        with doc.retokenize() as retokenizer:
            for sp in char_spans:
                retokenizer.merge(sp)
    for _, proc in nlp.pipeline:
        doc = proc(doc)
    return doc


def analyse(text: str, protected_spans: Sequence[tuple[int, int]] = ()) -> Analysis:
    """Perform a single spaCy NLP pass on text.

    Args:
        text: Query or input text.
        protected_spans: Optional sequence of (start, end) character spans (e.g. citations)
            that are merged into single tokens so the parser does not split them.

    Returns:
        Analysis dataclass containing noun chunks, content terms, intent spans,
        named entities, cues, and the underlying doc.
    """
    if not text.strip():
        return Analysis(
            noun_chunks=(),
            terms=(),
            intent_spans=(),
            entities=(),
            cues=Cues(has_date=False, has_negation=False, has_existence_question=False),
            protected_spans=(),
            doc=None,
        )

    nlp = get_nlp()
    merged_protected = _merge_spans(protected_spans, len(text))

    if merged_protected:
        doc = _retokenize_protected(nlp.make_doc(text), merged_protected, nlp)
    else:
        doc = nlp(text)

    noun_chunks = tuple(chunk.text for chunk in doc.noun_chunks)
    terms = _extract_terms(doc)
    split_indices = _find_clausal_conjunction_splits(doc)
    intent_spans = _split_intent_spans(text, split_indices)
    entities = _extract_entities(doc)
    cues = Cues(
        has_date=_detect_has_date(doc, text, merged_protected),
        has_negation=_detect_has_negation(doc),
        has_existence_question=_detect_has_existence(doc, text),
    )

    return Analysis(
        noun_chunks=noun_chunks,
        terms=terms,
        intent_spans=intent_spans,
        entities=entities,
        cues=cues,
        protected_spans=tuple(merged_protected),
        doc=doc,
    )
