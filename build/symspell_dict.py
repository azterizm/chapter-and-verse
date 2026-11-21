"""Build golden SymSpell dictionary from concept index vocabulary and instrument titles.

Usage:
    uv run python build/symspell_dict.py [--data-root PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from legal_rag_router import load_concepts

WORD_RE = re.compile(r"[a-z0-9]+")


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SymSpell dictionary.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Path to legal data root (defaults to LEGAL_DATA_ROOT env var).",
    )
    parser.add_argument(
        "--concepts",
        type=Path,
        default=None,
        help="Path directly to concepts index directory.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("artefacts/symspell"),
        help="Output directory (defaults to artefacts/symspell).",
    )
    args = parser.parse_args(argv)

    concepts_path: Path | None = args.concepts
    if concepts_path is None:
        data_root = args.data_root
        if data_root is None:
            env_val = os.environ.get("LEGAL_DATA_ROOT")
            if env_val:
                data_root = Path(env_val).expanduser()
        if data_root is not None:
            if (data_root / "concepts").is_dir():
                concepts_path = data_root / "concepts"
            elif (data_root / "concepts-manifest.json").is_file():
                concepts_path = data_root

    if concepts_path is None or not concepts_path.is_dir():
        msg = (
            "error: concepts directory not found. "
            "Set LEGAL_DATA_ROOT or pass --concepts/--data-root."
        )
        print(msg, file=sys.stderr)
        return 1

    term_count, file_size, elapsed, sha256_digest = build_dictionary(concepts_path, args.out)

    print(f"Terms: {term_count}")
    print(f"Size: {file_size} bytes ({file_size / (1024 * 1024):.2f} MB)")
    print(f"Build time: {elapsed:.2f} s")
    print(f"SHA-256: {sha256_digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
