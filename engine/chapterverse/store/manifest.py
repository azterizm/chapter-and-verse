"""``corpus-manifest.json``: what a store holds, where it came from, and the hash of every file.

The manifest is written as :func:`chapterverse.contracts.canonical_json` bytes, so the file's
own SHA-256 is the ``corpus_id``. The reader recomputes the id from the parsed manifest, so the
id does not depend on how the file is formatted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

from chapterverse.contracts import canonical_json, sha256_hex
from chapterverse.store.format import (
    DICT_NAME,
    FORMAT_KIND,
    FORMAT_VERSION,
    FRAMES_NAME,
    MANIFEST_NAME,
    TABLE_STEM,
    TEXT_NAME,
)

__all__ = [
    "REQUIRED_FILES",
    "SOURCE_KEYS",
    "StoreLoadError",
    "corpus_id",
    "file_entry",
    "make_manifest",
    "manifest_bytes",
    "read_manifest",
    "sha256_file",
    "source_entry",
    "verify_files",
]

REQUIRED_FILES: Final = (TEXT_NAME, FRAMES_NAME, f"{TABLE_STEM}.tbl", f"{TABLE_STEM}.off")
SOURCE_KEYS: Final = (
    "id",
    "name",
    "jurisdiction",
    "url",
    "licence",
    "licence_url",
    "terms_url",
    "date_checked",
    "attribution",
    "notes",
)
_CHUNK: Final = 1 << 22


class StoreLoadError(RuntimeError):
    """The store cannot be opened: missing file, bad hash, unknown format, inconsistent data."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path) -> dict[str, Any]:
    return {"size": path.stat().st_size, "sha256": sha256_file(path)}


def source_entry(router_manifest: Mapping[str, Any], jurisdiction: str = "uk") -> dict[str, Any]:
    """The source and licence entry for ``jurisdiction`` from the router's ``MANIFEST.json``.

    Raises:
        ValueError: no source covers the jurisdiction, or it has no attribution.
    """
    for source in router_manifest.get("sources", ()):
        if source.get("jurisdiction") == jurisdiction or jurisdiction in source.get(
            "record_paths", ()
        ):
            entry = {key: source[key] for key in SOURCE_KEYS if key in source}
            if not entry.get("attribution") or not entry.get("licence"):
                raise ValueError(f"source {source.get('id')!r} has no licence or attribution")
            return entry
    raise ValueError(f"no source for jurisdiction {jurisdiction!r} in MANIFEST.json")


def make_manifest(
    *,
    snapshot: str,
    source: Mapping[str, Any],
    counts: Mapping[str, int],
    compression: Mapping[str, Any],
    builder: Mapping[str, str],
    store_dir: Path,
    files: tuple[str, ...],
) -> dict[str, Any]:
    """The manifest for the files ``files`` in ``store_dir`` (hashed here)."""
    return {
        "format_version": FORMAT_VERSION,
        "kind": FORMAT_KIND,
        "snapshot": snapshot,
        "source": dict(source),
        "counts": dict(counts),
        "compression": dict(compression),
        "builder": dict(builder),
        "files": {name: file_entry(store_dir / name) for name in sorted(files)},
    }


def manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return canonical_json(manifest)


def corpus_id(manifest: Mapping[str, Any]) -> str:
    """sha256 of the manifest's canonical bytes."""
    return sha256_hex(canonical_json(manifest))


def read_manifest(store_dir: Path) -> dict[str, Any]:
    """Parse and check the manifest's shape (not the file hashes; see :func:`verify_files`).

    Raises:
        StoreLoadError: missing, not JSON, unknown kind or format version, no file list.
    """
    path = store_dir / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise StoreLoadError(f"no {MANIFEST_NAME} in {store_dir}") from None
    except ValueError as exc:
        raise StoreLoadError(f"{MANIFEST_NAME} is not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != FORMAT_KIND:
        raise StoreLoadError(f"{MANIFEST_NAME} does not describe a {FORMAT_KIND}")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise StoreLoadError(
            f"unsupported store format version {manifest.get('format_version')!r}; "
            f"this reader reads {FORMAT_VERSION}"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise StoreLoadError(f"{MANIFEST_NAME} lists no files")
    needed = list(REQUIRED_FILES)
    if manifest.get("compression", {}).get("dictionary"):
        needed.append(DICT_NAME)
    for name in needed:
        entry = files.get(name)
        if not isinstance(entry, dict) or "sha256" not in entry or "size" not in entry:
            raise StoreLoadError(f"{MANIFEST_NAME} has no size and hash for {name}")
    return manifest


def _check_file(store_dir: Path, name: str, entry: Mapping[str, Any]) -> str | None:
    path = store_dir / name
    if not path.is_file():
        return f"missing store file {name}"
    if path.stat().st_size != entry["size"]:
        return f"size mismatch for {name}: the store is corrupt or was modified"
    if sha256_file(path) != entry["sha256"]:
        return f"hash mismatch for {name}: the store is corrupt or was modified"
    return None


def verify_files(store_dir: Path, manifest: Mapping[str, Any]) -> None:
    """Check the size and SHA-256 of every file the manifest lists (hashed in parallel).

    Raises:
        StoreLoadError: a file is missing or does not match.
    """
    files: Mapping[str, Mapping[str, Any]] = manifest["files"]
    for name in files:
        if Path(name).name != name or name == MANIFEST_NAME:
            raise StoreLoadError(f"{MANIFEST_NAME} lists a bad file name {name!r}")
    with ThreadPoolExecutor(max_workers=max(1, len(files))) as pool:
        problems = list(pool.map(lambda kv: _check_file(store_dir, kv[0], kv[1]), files.items()))
    for problem in problems:
        if problem is not None:
            raise StoreLoadError(problem)
