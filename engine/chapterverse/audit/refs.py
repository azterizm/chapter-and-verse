"""Passage references in model text, and citation strings the model must not write.

The model cites passages by id only (``[P1]``, ``[P1, P3]``, ``[P1][P2]``); the system renders
the citation from the id. :func:`check_refs` strips the references from a sentence, collects
their ids, and fails the sentence when:

``UNKNOWN_PASSAGE``
    a reference names an id that is not in the prompt (``[P9]`` with five passages).
``MALFORMED_REF``
    something that looks like a reference is left after stripping (``(P1)``, ``[P1`` unclosed).
``MODEL_CITATION``
    the model wrote a citation string itself. The rule chosen:

    * any provision citation the verifier's claim extractor finds fails: ``section 124(1ZA)``,
      ``s.124``, ``regulation 5``, ``article 3``, and relative ones such as ``subsection (1ZA)``
      or ``paragraph (a)`` (the reader cannot resolve them; the rendered citation can);
    * an SI number (``SI 2024/123``) fails;
    * a structured identifier the router's scanner finds (a coordinate, an instrument id, a
      legislation.gov.uk URL) fails;
    * an instrument *name* in prose (``the Employment Rights Act 1996``, ``ERA 1996``) is allowed
      here, because the verifier grounds instrument names against the premise titles and rolls
      the sentence back (``UNGROUNDED_INSTRUMENT``) when it names any other instrument.

    A citation inside a negation ("there is no section 342") still fails: the verifier exempts
    it, this rule does not, because the model must never write one.
``NO_PASSAGE_REF``
    the sentence has no reference but states something checkable. A sentence with no reference
    is allowed only when it states nothing checkable: the verifier's claim extractor finds no
    figure, date, duration, citation or instrument in it, and its deontic finder no modal
    ("Yes.", "The passages do not say."). Such a sentence still goes to the verifier, and is
    emitted with empty ``passage_ids`` only if the verifier emits it.

All checks are deterministic and run before the judge, so a failed sentence costs no judge call.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from legal_rag_router.identifiers import scan_identifiers
from legal_rag_verifier.citations import (
    coordinate_tail,
    find_citations,
    is_relative,
    resolve_relative,
)
from legal_rag_verifier.claims import ClaimKind, extract_claims
from legal_rag_verifier.deontic import find_deontics

__all__ = [
    "MALFORMED_REF",
    "MODEL_CITATION",
    "NO_PASSAGE_REF",
    "REF_CODES",
    "UNKNOWN_PASSAGE",
    "RefCheck",
    "RefFailure",
    "check_refs",
    "is_checkable",
    "model_citations",
    "split_refs",
]

MODEL_CITATION: Final = "MODEL_CITATION"
UNKNOWN_PASSAGE: Final = "UNKNOWN_PASSAGE"
MALFORMED_REF: Final = "MALFORMED_REF"
NO_PASSAGE_REF: Final = "NO_PASSAGE_REF"
REF_CODES: Final = frozenset({MODEL_CITATION, UNKNOWN_PASSAGE, MALFORMED_REF, NO_PASSAGE_REF})

_ID = r"[Pp]\d+"
_SEP = r"\s*(?:,|;|&|\band\b)\s*"
#: One bracketed reference group, with the whitespace before it.
REF_GROUP: Final = re.compile(rf"\s*\[\s*({_ID}(?:{_SEP}{_ID})*)\s*\]")
#: One or more groups, as at the end of a sentence ("... [P1][P2].").
REF_GROUPS: Final = re.compile(rf"(?:{REF_GROUP.pattern})+")
_ONE_ID = re.compile(_ID)
_LEFTOVER = re.compile(r"[\[(]\s*[Pp]\d+")


@dataclass(frozen=True, slots=True)
class RefFailure:
    """Why a sentence fails the reference rules: a code from :data:`REF_CODES` and the text."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True, slots=True)
class RefCheck:
    """A sentence with its references removed, the ids it cited and any failures."""

    text: str
    passage_ids: tuple[str, ...]
    failures: tuple[RefFailure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def split_refs(sentence: str) -> tuple[str, tuple[str, ...]]:
    """``sentence`` with every reference group removed, and the ids in first-seen order.

    Ids are normalised to ``P<n>`` (``[p01]`` is ``P1``). Removing a group also removes the
    whitespace before it, so ``"£5 [P1]."`` becomes ``"£5."``.
    """
    ids: list[str] = []
    for match in REF_GROUP.finditer(sentence):
        for raw in _ONE_ID.findall(match.group(1)):
            pid = f"P{int(raw[1:])}"
            if pid not in ids:
                ids.append(pid)
    return REF_GROUP.sub("", sentence).strip(), tuple(ids)


def model_citations(text: str) -> tuple[str, ...]:
    """Citation strings in ``text`` that only the system may write (see the module docstring)."""
    found = [
        c.text
        for c in extract_claims(text)
        if c.kind is ClaimKind.CITATION
        or (c.kind is ClaimKind.INSTRUMENT and c.value.startswith("si:"))
    ]
    found += [text[m.start : m.end] for m in scan_identifiers(text)]
    return tuple(dict.fromkeys(found))


def is_checkable(text: str) -> bool:
    """True when ``text`` states a figure, date, duration, citation, instrument or modal."""
    return bool(extract_claims(text)) or bool(find_deontics(text))


def _grounded_citations(
    ids: Sequence[str],
    cited_texts: Mapping[str, str] | None,
    cited_coordinates: Mapping[str, str] | None,
) -> set[str]:
    grounded: set[str] = set()
    if cited_texts is not None:
        for pid in ids:
            if pid in cited_texts:
                pt = cited_texts[pid]
                base = (
                    coordinate_tail(cited_coordinates[pid])
                    if cited_coordinates is not None and pid in cited_coordinates
                    else ""
                )
                for match in find_citations(pt):
                    grounded.add(match.canonical)
                    if base and is_relative(match.canonical):
                        grounded.add(resolve_relative(match.canonical, base))
    if cited_coordinates is not None:
        for pid in ids:
            if pid in cited_coordinates:
                tail = coordinate_tail(cited_coordinates[pid])
                if tail:
                    parts = tail.split("/")
                    for i in range(1, len(parts) + 1):
                        grounded.add("/".join(parts[:i]))
    return grounded


def _citation_grounded(
    claim_val: str,
    ids: Sequence[str],
    grounded: set[str],
    cited_coordinates: Mapping[str, str] | None,
) -> bool:
    if not is_relative(claim_val):
        return claim_val in grounded
    if cited_coordinates is not None:
        for pid in ids:
            if pid in cited_coordinates:
                base = coordinate_tail(cited_coordinates[pid])
                if base and resolve_relative(claim_val, base) in grounded:
                    return True
    return claim_val in grounded


def check_refs(
    sentence: str,
    known_ids: Collection[str],
    cited_texts: Mapping[str, str] | None = None,
    cited_coordinates: Mapping[str, str] | None = None,
) -> RefCheck:
    """Strip and check the passage references of one sentence (rules in the module docstring).

    When ``cited_texts`` is provided (mapping passage id to text), a model-written provision
    reference that appears in the text of a passage the sentence cites is grounded and passes.
    When ``cited_coordinates`` is provided (mapping passage id to coordinate), a reference to
    the cited provision itself or an ancestor of it within the same instrument is also grounded.
    Relative references resolve against the cited passage's section.
    """
    text, ids = split_refs(sentence)
    failures: list[RefFailure] = []
    unknown = [i for i in ids if i not in known_ids]
    if unknown:
        failures.append(RefFailure(UNKNOWN_PASSAGE, ", ".join(unknown)))
    leftover = _LEFTOVER.search(text)
    if leftover is not None:
        failures.append(RefFailure(MALFORMED_REF, text[leftover.start() : leftover.end() + 8]))
    if cited_texts is None and cited_coordinates is None:
        cited = model_citations(text)
    else:
        grounded = _grounded_citations(ids, cited_texts, cited_coordinates)
        found: list[str] = []
        for c in extract_claims(text):
            if c.kind is ClaimKind.CITATION:
                if not _citation_grounded(c.value, ids, grounded, cited_coordinates):
                    found.append(c.text)
            elif c.kind is ClaimKind.INSTRUMENT and c.value.startswith("si:"):
                found.append(c.text)
        found += [text[m.start : m.end] for m in scan_identifiers(text)]
        cited = tuple(dict.fromkeys(found))
    if cited:
        failures.append(RefFailure(MODEL_CITATION, "; ".join(cited)))
    if not ids and not failures and is_checkable(text):
        failures.append(RefFailure(NO_PASSAGE_REF, text[:80]))
    return RefCheck(text=text, passage_ids=ids, failures=tuple(failures))
