"""Deterministic check for legal classification shifts (e.g. civil duty called an offence).

When a sentence contains a term of a legal classification class (such as criminal classification
terms: offence, criminal, guilty, conviction, imprisonment, etc.) and none of the cited passages
contains a term of the same class, the sentence fails with code ``CLASSIFICATION_SHIFT`` before
any judge call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Final

__all__ = [
    "CLASSIFICATION_CLASSES",
    "CLASSIFICATION_SHIFT",
    "CRIMINAL_TERMS",
    "TERM_CLASSES",
    "classification_failure",
]

CLASSIFICATION_SHIFT: Final = "CLASSIFICATION_SHIFT"

CRIMINAL_TERMS: Final[frozenset[str]] = frozenset(
    {
        "convicted",
        "conviction",
        "convictions",
        "crime",
        "crimes",
        "criminal",
        "guilty",
        "imprisonment",
        "indictable",
        "indictment",
        "indictments",
        "offence",
        "offences",
        "offender",
        "offenders",
        "prosecuted",
        "prosecution",
        "prosecutions",
        "summary conviction",
    }
)

TERM_CLASSES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "criminal": CRIMINAL_TERMS,
    }
)
CLASSIFICATION_CLASSES: Final[Mapping[str, frozenset[str]]] = TERM_CLASSES


def _compile_pattern(terms: Iterable[str]) -> re.Pattern[str]:
    parts: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        if " " in term:
            parts.append(r"\s+".join(re.escape(w) for w in term.split()))
        else:
            parts.append(re.escape(term))
    return re.compile(rf"\b(?:{'|'.join(parts)})\b", re.IGNORECASE)


_CLASS_PATTERNS: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {name: _compile_pattern(terms) for name, terms in TERM_CLASSES.items()}
)


def classification_failure(sentence: str, cited_texts: Sequence[str]) -> str | None:
    """Return a detail string if sentence makes an ungrounded classification shift, else None.

    Checks each classification class in :data:`TERM_CLASSES`. If ``sentence`` contains any term
    of a class and none of ``cited_texts`` contains a term of that same class, returns a detail
    string naming the ungrounded terms.
    """
    for pattern in _CLASS_PATTERNS.values():
        matches = list(pattern.finditer(sentence))
        if not matches:
            continue
        grounded = any(pattern.search(text) is not None for text in cited_texts)
        if not grounded:
            found = tuple(dict.fromkeys(m.group(0).lower() for m in matches))
            return ", ".join(found)
    return None
