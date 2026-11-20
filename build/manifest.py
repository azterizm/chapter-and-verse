"""Show or check a store's ``corpus-manifest.json``.

    uv run python build/manifest.py show [--store DIR]     corpus_id, snapshot, counts, files
    uv run python build/manifest.py verify [--store DIR]   re-hash every file; exit 1 on mismatch

The manifest is written by ``build/textstore.py``; the code that writes and checks it is
``chapterverse.store.manifest``, shared with the reader. ``corpus_id`` is the sha256 of the
manifest's canonical JSON bytes, which are also the file's exact bytes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from chapterverse.store.format import MANIFEST_NAME
from chapterverse.store.manifest import (
    StoreLoadError,
    corpus_id,
    manifest_bytes,
    read_manifest,
    verify_files,
)


def summary(store: Path) -> dict[str, object]:
    manifest = read_manifest(store)
    raw = (store / MANIFEST_NAME).read_bytes()
    return {
        "store": str(store),
        "corpus_id": corpus_id(manifest),
        "manifest_is_canonical": raw == manifest_bytes(manifest),
        "format_version": manifest["format_version"],
        "snapshot": manifest["snapshot"],
        "licence": manifest["source"].get("licence"),
        "counts": manifest["counts"],
        "compression": manifest["compression"],
        "files": manifest["files"],
        "store_bytes": sum(entry["size"] for entry in manifest["files"].values()) + len(raw),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show or check a store manifest.")
    parser.add_argument("command", choices=("show", "verify"))
    parser.add_argument("--store", type=Path, default=Path("artefacts/store"))
    args = parser.parse_args(argv)
    try:
        report = summary(args.store)
        if args.command == "verify":
            verify_files(args.store, read_manifest(args.store))
            report = {"store": report["store"], "corpus_id": report["corpus_id"], "ok": True}
    except StoreLoadError as exc:
        print(json.dumps({"store": str(args.store), "ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
