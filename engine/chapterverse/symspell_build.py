"""Build and load SymSpell dictionary from concept index vocabulary and instrument titles."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

from legal_rag_router import load_concepts
from symspellpy import SymSpell  # type: ignore[import-untyped]

__all__ = [
    "WORD_RE",
    "build_dictionary",
    "build_symspell_in_memory",
    "extract_term_frequencies",
]

WORD_RE = re.compile(r"[a-z0-9]+")


def extract_term_frequencies(concepts: Any) -> Counter[str]:
    """Extract vocabulary frequencies from concept index term table and instrument titles."""
    freqs: Counter[str] = Counter()

    # 1. Concept index vocabulary
    for term, raw in concepts.term_table.items():
        count = int(raw.split(",")[1])
        freqs[term] += count

    # 2. Instrument titles
    for doc_id in range(concepts.first_instrument_doc, concepts.doc_count):
        heading = concepts.doc(doc_id).heading
        for word in WORD_RE.findall(heading.lower()):
            freqs[word] += 1

    return freqs


def build_symspell_in_memory(concepts_or_path: Any | Path | str) -> SymSpell:
    """Build a SymSpell instance in memory from a loaded concepts index or concepts directory."""
    if isinstance(concepts_or_path, (Path, str)):
        concepts = load_concepts(Path(concepts_or_path))
    else:
        concepts = concepts_or_path

    freqs = extract_term_frequencies(concepts)
    sym = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    for term, count in freqs.items():
        sym.create_dictionary_entry(term, count)
    return sym


def build_dictionary(
    concepts_dir: Path,
    out_dir: Path,
) -> tuple[int, int, float, str]:
    """Build dictionary.txt and manifest.json from concepts directory.

    Returns:
        (term_count, file_size_bytes, elapsed_seconds, sha256_hex)
    """
    start_time = time.perf_counter()

    concepts = load_concepts(concepts_dir)
    freqs = extract_term_frequencies(concepts)

    out_dir.mkdir(parents=True, exist_ok=True)
    dict_path = out_dir / "dictionary.txt"

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
        "format_version": 1,
        "kind": "symspell-dictionary",
        "snapshot": concepts.manifest.get("snapshot", ""),
        "files": {
            "dictionary.txt": {
                "sha256": sha256_digest,
                "size": file_size,
            }
        },
        "counts": {
            "terms": len(sorted_terms),
        },
    }

    manifest_path = out_dir / "manifest.json"
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n"
    )
    manifest_path.write_bytes(manifest_bytes)

    elapsed = time.perf_counter() - start_time
    return len(sorted_terms), file_size, elapsed, sha256_digest
