"""Document text representation for dense retrieval (requirement R1).

Builds deterministic document text for embedding from the opened TextStore
and router's ConceptIndex.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from typing import Any, Final

from legal_rag_router.concepts import ConceptDoc, ConceptIndex
from legal_rag_verifier.premise import Premise

from chapterverse.citations import instrument_name, provision_label
from chapterverse.contracts import TextStore

__all__ = [
    "DocTextBuilder",
    "doc_text",
    "iter_doc_text",
    "iter_doc_texts",
    "set_default_store_and_concepts",
]

_DEFAULTS: Final[dict[str, Any]] = {"store": None, "concepts": None}
_LOCK: Final = threading.Lock()


def set_default_store_and_concepts(store: TextStore | None, concepts: ConceptIndex | None) -> None:
    """Set global default TextStore and ConceptIndex instances for doc_text."""
    with _LOCK:
        _DEFAULTS["store"] = store
        _DEFAULTS["concepts"] = concepts


def _instrument_doc_text(doc: ConceptDoc, s: TextStore) -> str:
    inst = s.instrument(doc.coordinate)
    title = (inst.get("title") if inst else None) or doc.heading or ""
    prefix = f"[{title}]"
    long_title = inst.get("long_title") if inst else None
    if not long_title:
        try:
            rows = s.get(doc.coordinate)
            headings = [str(r["title"]) for r in rows[1:11] if r.get("title")]
            body = " ".join(headings)
        except (KeyError, ValueError, OSError):
            body = ""
    else:
        body = str(long_title)
    full = f"{prefix} {title} {body}" if title and title != body else f"{prefix} {body}"
    return " ".join(full.split())


def _provision_doc_text(doc: ConceptDoc, s: TextStore) -> str:
    try:
        rows = s.get(doc.coordinate)
    except KeyError:
        inst = s.instrument(doc.coordinate)
        inst_title = instrument_name(doc.coordinate, inst) if inst else ""
        plabel = provision_label(doc.coordinate)
        heading = doc.heading or ""
        prov_desc = f"{plabel} {heading}".strip()
        prefix_parts = [f"[{inst_title}]"]
        if prov_desc:
            prefix_parts.append(f"[{prov_desc}]")
        prefix = " ".join(prefix_parts)
        full = f"{prefix} {heading}".strip()
        return " ".join(full.split())

    inst_row = rows[0]
    prov_row = rows[1] if len(rows) > 1 else {}

    inst_title = instrument_name(doc.coordinate, inst_row) or str(inst_row.get("title", ""))
    prefix_parts = [f"[{inst_title}]"]

    crossheading = prov_row.get("crossheading")
    if crossheading:
        prefix_parts.append(f"[{crossheading}]")

    plabel = provision_label(doc.coordinate)
    pheading = doc.heading or str(prov_row.get("title") or "")
    prov_desc = f"{plabel} {pheading}".strip()
    if prov_desc:
        prefix_parts.append(f"[{prov_desc}]")

    prefix = " ".join(prefix_parts)

    premise = Premise.from_records(rows)
    if premise.passages:
        body = " ".join(p.text for p in premise.passages)
    else:
        body = " ".join(
            str(t)
            for r in rows[1:]
            for t in (r.get("text"), r.get("text_after"))
            if t and not str(t).startswith(". . .")
        )

    heading = doc.heading or str(prov_row.get("title") or "")
    full = f"{prefix} {heading} {body}"
    return " ".join(full.split())


def doc_text(
    doc_id: int,
    store: TextStore | None = None,
    concepts: ConceptIndex | None = None,
) -> str:
    """Deterministic document text for a concept doc_id.

    Format:
    Prefix: [<instrument short title with year>] [<Part/Chapter>] [<provision label/heading>]
    followed by the provision heading and body text, with whitespace collapsed.
    For instrument docs: prefix with title, then long title or first provision headings.

    Raises:
        ValueError: store or concepts not provided and no defaults configured.
        KeyError: doc_id not in concepts.
    """
    s = store if store is not None else _DEFAULTS["store"]
    c = concepts if concepts is not None else _DEFAULTS["concepts"]
    if s is None or c is None:
        raise ValueError("TextStore and ConceptIndex must be provided or configured")

    doc = c.doc(doc_id)
    if doc.kind == "i":
        return _instrument_doc_text(doc, s)
    return _provision_doc_text(doc, s)


def iter_doc_text(
    doc_ids: Iterable[int],
    store: TextStore | None = None,
    concepts: ConceptIndex | None = None,
) -> Iterator[str]:
    """Yield document text for each doc_id in order."""
    s = store if store is not None else _DEFAULTS["store"]
    c = concepts if concepts is not None else _DEFAULTS["concepts"]
    if s is None or c is None:
        raise ValueError("TextStore and ConceptIndex must be provided or configured")
    for doc_id in doc_ids:
        yield doc_text(doc_id, s, c)


iter_doc_texts = iter_doc_text


class DocTextBuilder:
    """Helper wrapping TextStore and ConceptIndex for document text generation."""

    def __init__(self, store: TextStore, concepts: ConceptIndex) -> None:
        self.store = store
        self.concepts = concepts

    def doc_text(self, doc_id: int) -> str:
        return doc_text(doc_id, self.store, self.concepts)

    def iter_doc_text(self, doc_ids: Iterable[int]) -> Iterator[str]:
        return iter_doc_text(doc_ids, self.store, self.concepts)

    iter_doc_texts = iter_doc_text

    def __call__(self, doc_id: int) -> str:
        return self.doc_text(doc_id)
