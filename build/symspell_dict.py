"""Build golden SymSpell dictionary from store records (surface word forms).

Usage:
    uv run python build/symspell_dict.py [--store-dir PATH] [--data-root PATH] [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import resource
import sys
from collections.abc import Sequence
from pathlib import Path

from chapterverse.symspell_build import build_dictionary


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _resolve_store_path(  # noqa: PLR0911
    store_dir: Path | None, data_root: Path | None, concepts: Path | None
) -> Path | None:
    if store_dir is not None:
        return store_dir

    env_store = os.environ.get("STORE_DIR")
    if env_store and Path(env_store).exists():
        return Path(env_store).expanduser()

    root = data_root
    if root is None:
        env_root = os.environ.get("LEGAL_DATA_ROOT")
        if env_root:
            root = Path(env_root).expanduser()

    if root is not None:
        if (root / "store").is_dir():
            return root / "store"
        if (root / "corpus-manifest.json").is_file() or (root / "uk").is_dir():
            return root

    default_store = Path("artefacts/store")
    if default_store.is_dir():
        return default_store

    if concepts is not None and concepts.is_dir():
        return concepts

    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SymSpell dictionary.")
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Path to store directory (defaults to STORE_DIR env var or artefacts/store).",
    )
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

    store_path = _resolve_store_path(args.store_dir, args.data_root, args.concepts)
    if store_path is None or not store_path.exists():
        msg = (
            "error: store directory not found. "
            "Set STORE_DIR, LEGAL_DATA_ROOT, or pass --store-dir/--data-root."
        )
        print(msg, file=sys.stderr)
        return 1

    term_count, file_size, elapsed, sha256_digest = build_dictionary(store_path, args.out)

    peak_rss_mb = _peak_rss_bytes() / (1024 * 1024)

    print(f"Terms: {term_count}")
    print(f"Size: {file_size} bytes ({file_size / (1024 * 1024):.2f} MB)")
    print(f"Build time: {elapsed:.2f} s")
    print(f"Peak RSS: {peak_rss_mb:.2f} MB")
    print(f"SHA-256: {sha256_digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
