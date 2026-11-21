"""Build golden SymSpell dictionary from concept index vocabulary and instrument titles.

Usage:
    uv run python build/symspell_dict.py [--data-root PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from chapterverse.symspell_build import build_dictionary


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
