"""Build and load SymSpell dictionary from store records and surface word forms."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from symspellpy import SymSpell  # type: ignore[import-untyped]

from chapterverse.contracts import TextStore
from chapterverse.store import open_store

__all__ = [
    "WORD_RE",
    "build_dictionary",
    "build_symspell_in_memory",
    "extract_term_frequencies",
]

# Surface word forms: lower-cased, letters and internal hyphens or apostrophes only
WORD_RE = re.compile(r"[a-z]+(?:['\-][a-z]+)*")

_MIN_COMPOUND_LEN: Final = 3
_PROVISION_FIELDS: Final = ("text", "text_after", "title", "crossheading")
_INSTRUMENT_FIELDS: Final = ("title", "long_title")


def _extract_from_text(text: str, freqs: Counter[str]) -> None:
    clean = text.replace("\u2019", "'").lower()
    for word in WORD_RE.findall(clean):
        freqs[word] += 1
        if "-" in word:
            unhyphenated = word.replace("-", "")
            if len(unhyphenated) >= _MIN_COMPOUND_LEN:
                freqs[unhyphenated] += 1


def _extract_from_record(rec: dict[str, Any], freqs: Counter[str]) -> None:
    rec_type = rec.get("record_type")
    if rec_type == "provision":
        for field in _PROVISION_FIELDS:
            val = rec.get(field)
            if val and isinstance(val, str):
                _extract_from_text(val, freqs)
    elif rec_type == "instrument":
        for field in _INSTRUMENT_FIELDS:
            val = rec.get(field)
            if val and isinstance(val, str):
                _extract_from_text(val, freqs)
    else:
        # Fallback if record_type is omitted
        for field in (*_PROVISION_FIELDS, *_INSTRUMENT_FIELDS):
            val = rec.get(field)
            if val and isinstance(val, str):
                _extract_from_text(val, freqs)


def _extract_from_jsonl_files(paths: Iterable[Path]) -> Counter[str]:
    freqs: Counter[str] = Counter()
    for path in paths:
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if line:
                    _extract_from_record(json.loads(line), freqs)
    return freqs


def _extract_from_text_store(store: TextStore) -> Counter[str]:
    freqs: Counter[str] = Counter()
    if hasattr(store, "iter_records"):
        for rec in store.iter_records():
            _extract_from_record(rec, freqs)
    elif isinstance(store, Iterable):
        for rec in store:
            _extract_from_record(rec, freqs)
    else:
        root = getattr(store, "root", None)
        if isinstance(root, Path):
            jsonl_files = _find_jsonl_files(root)
            if jsonl_files:
                return _extract_from_jsonl_files(jsonl_files)
    return freqs


def _find_jsonl_files(path: Path) -> list[Path]:
    if path.is_file() and path.suffix == ".jsonl":
        return [path]
    if (path / "uk").is_dir():
        return sorted((path / "uk").rglob("*.jsonl"))
    if (path.parent / "uk").is_dir():
        return sorted((path.parent / "uk").rglob("*.jsonl"))
    if path.is_dir():
        return sorted(path.rglob("*.jsonl"))
    return []


def _extract_from_concepts(path: Path) -> Counter[str]:
    from legal_rag_router import load_concepts  # noqa: PLC0415

    concepts = load_concepts(path)
    freqs: Counter[str] = Counter()
    for term, raw in concepts.term_table.items():
        count = int(raw.split(",")[1])
        clean = term.replace("\u2019", "'").lower()
        for word in WORD_RE.findall(clean):
            freqs[word] += count
    for doc_id in range(concepts.first_instrument_doc, concepts.doc_count):
        heading = concepts.doc(doc_id).heading
        if heading:
            clean = heading.replace("\u2019", "'").lower()
            for word in WORD_RE.findall(clean):
                freqs[word] += 1
    return freqs


def extract_term_frequencies(
    store_or_source: TextStore | Path | str | None = None,
) -> Counter[str]:
    """Extract vocabulary frequencies from store records or JSONL files.

    Words are extracted from provision text, text_after, title, crossheading,
    and instrument title and long_title.
    """
    if store_or_source is None:
        store_or_source = Path("artefacts/store")

    if isinstance(store_or_source, TextStore):
        return _extract_from_text_store(store_or_source)

    path = Path(store_or_source)
    if (path / "text.zst").is_file() and (path / "corpus-manifest.json").is_file():
        return _extract_from_text_store(open_store(path))

    if (path / "store" / "text.zst").is_file():
        return _extract_from_text_store(open_store(path / "store"))

    jsonl_files = _find_jsonl_files(path)
    if jsonl_files:
        return _extract_from_jsonl_files(jsonl_files)

    if (path / "terms.tbl").is_file() or (path / "concepts-manifest.json").is_file():
        return _extract_from_concepts(path)

    return _extract_from_text_store(open_store(path))


def build_symspell_in_memory(
    store_or_source: TextStore | Path | str | None = None,
) -> SymSpell:
    """Build a SymSpell instance in memory from store records."""
    freqs = extract_term_frequencies(store_or_source)
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    for term, count in freqs.items():
        sym.create_dictionary_entry(term, count)
    return sym


def _find_snapshot(source: TextStore | Path | str | None) -> str:
    """Determine snapshot date string from store manifest, index manifest, or default."""
    if isinstance(source, TextStore):
        return source.snapshot
    if source is not None:
        p = Path(source)
        manifest_candidates = [
            p / "corpus-manifest.json",
            p / "store" / "corpus-manifest.json",
            p / "index" / "index-manifest.json",
            p / "concepts" / "concepts-manifest.json",
            p.parent / "index" / "index-manifest.json",
            p.parent / "concepts" / "concepts-manifest.json",
        ]
        for mc in manifest_candidates:
            if mc.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError, KeyError):
                    data = json.loads(mc.read_text(encoding="utf-8"))
                    if "snapshot" in data:
                        return str(data["snapshot"])
    return "2026-09-28"


def build_dictionary(
    store_or_source: TextStore | Path | str | None = None,
    out_dir: Path | str = Path("artefacts/symspell"),
) -> tuple[int, int, float, str]:
    """Build dictionary.txt and manifest.json from store records.

    Returns:
        (term_count, file_size_bytes, elapsed_seconds, sha256_hex)
    """
    start_time = time.perf_counter()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    snapshot = _find_snapshot(store_or_source)
    freqs = extract_term_frequencies(store_or_source)

    dict_path = out_path / "dictionary.txt"

    # Deterministic alphabetical sort
    sorted_terms = sorted(freqs.keys())
    hasher = hashlib.sha256()

    with dict_path.open("wb") as fh:
        for term in sorted_terms:
            line = f"{term} {freqs[term]}\n".encode()
            fh.write(line)
            hasher.update(line)

    file_size = dict_path.stat().st_size
    sha256_digest = hasher.hexdigest()

    manifest = {
        "counts": {
            "terms": len(sorted_terms),
        },
        "files": {
            "dictionary.txt": {
                "sha256": sha256_digest,
                "size": file_size,
            }
        },
        "format_version": 1,
        "kind": "symspell-dictionary",
        "snapshot": snapshot,
    }

    manifest_path = out_path / "manifest.json"
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    )
    manifest_path.write_bytes(manifest_bytes)

    elapsed = time.perf_counter() - start_time
    return len(sorted_terms), file_size, elapsed, sha256_digest
