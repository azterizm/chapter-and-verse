#!/usr/bin/env python3
"""Fetch and verify the Chapter & Verse artefacts at boot. Standard library only.

The artefacts are the text store and the router ``index/`` and ``concepts/`` directories,
published as a public Hugging Face Dataset. Layout of the dataset (and of ``DATA_DIR``)::

    corpus-manifest.json             text store manifest (build/publish_dataset.py), at the root
    <store files>                    as listed in it (STORE_DIR=/data)
    index/index-manifest.json, ...   router index (LEGAL_DATA_ROOT=/data)
    concepts/concepts-manifest.json, ...
    symspell/symspell-manifest.json, ... spelling dictionary

A manifest holds ``files``, either a mapping ``{"relative/path": {"sha256": "<hex>",
"size": <int>}}`` (``bytes`` is accepted for ``size``; either is optional) or a list of
``{"path": ..., "sha256": ..., "size": ...}``. Paths are relative to the manifest's directory.
``corpus-manifest.json`` is the main manifest (pin it with ``MANIFEST_SHA256``). The router's
manifests are named in ``EXTRA_MANIFESTS``; each is fetched, hashed, and recorded in the source
marker, and every file it lists is verified. A listed file whose name ends in ``-manifest.json``
and that holds ``files`` is expanded the same way, after it has itself been verified.

Behaviour:

- Every file is checked by SHA-256. Any mismatch, missing file, bad path or bad manifest exits
  non-zero (fail closed); the server is never started on unverified data.
- If ``DATA_DIR`` already holds a manifest from the same dataset and revision and every file
  verifies, nothing is downloaded and no network call is made.
- Downloads go to ``<file>.part`` and resume with an HTTP Range request after an interruption.
  A file is moved into place only after its hash matches. The manifest is written last.
- With ``HF_DATASET`` unset, the script only verifies a mounted ``DATA_DIR`` (read-only is fine).

Environment:

==================  =========================================================================
DATA_DIR            target directory (default ``/data``)
HF_DATASET          ``owner/name`` of the public dataset; unset means verify only
HF_REVISION         a 40-hex commit id of the dataset (a branch name is refused unless
                    ``ARTEFACTS_ALLOW_BRANCH=1``)
HF_ENDPOINT         default ``https://huggingface.co``
MANIFEST_NAME       default ``corpus-manifest.json``
MANIFEST_SHA256     optional pin of the manifest's own SHA-256; strongly recommended
EXTRA_MANIFESTS     comma list (empty for none), default
                    ``index/index-manifest.json,concepts/concepts-manifest.json,symspell/symspell-manifest.json``
ARTEFACTS_WORKERS   parallel downloads (default 4)
==================  =========================================================================

Exit codes: 0 verified, 1 verification or download failure, 2 configuration error.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

CHUNK = 1 << 20
MAX_MANIFEST_BYTES = 64 << 20
RETRIES = 5
TIMEOUT_S = 60
SOURCE_MARKER = ".artefacts-source.json"
USER_AGENT = "chapter-and-verse-fetch/1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HTTP_PARTIAL = 206
HTTP_RANGE_NOT_SATISFIABLE = 416
HTTP_FATAL = (401, 403, 404)
MAX_WORKERS = 16


class FetchError(Exception):
    """Download or verification failed. Exit code 1."""


class ConfigError(Exception):
    """The environment or manifest is unusable. Exit code 2."""


@dataclass(frozen=True, slots=True)
class Entry:
    path: str
    sha256: str
    size: int | None


def log(message: str) -> None:
    print(f"fetch_artefacts: {message}", file=sys.stderr, flush=True)  # noqa: T201


# ---------------------------------------------------------------- manifest


def safe_relpath(raw: object) -> str:
    """A normalised relative POSIX path, or ConfigError. Refuses absolute paths and ``..``."""
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ConfigError(f"bad path in manifest: {raw!r}")
    p = PurePosixPath(raw)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise ConfigError(f"bad path in manifest: {raw!r}")
    if p.name.endswith(".part") or p.name == SOURCE_MARKER:
        raise ConfigError(f"reserved file name in manifest: {raw!r}")
    if not raw.isprintable():
        raise ConfigError(f"bad path in manifest: {raw!r}")
    return p.as_posix()


def parse_files(manifest: Any, prefix: str = "") -> list[Entry]:
    """The ``files`` of a manifest as entries, paths joined to ``prefix``."""
    if not isinstance(manifest, dict) or "files" not in manifest:
        raise ConfigError("manifest has no 'files'")
    files = manifest["files"]
    items: list[tuple[object, object]]
    if isinstance(files, dict):
        items = list(files.items())
    elif isinstance(files, list):
        items = [(f.get("path") if isinstance(f, dict) else None, f) for f in files]
    else:
        raise ConfigError("manifest 'files' is neither a mapping nor a list")
    out: list[Entry] = []
    for raw_path, meta in items:
        if not isinstance(meta, dict):
            raise ConfigError(f"bad entry for {raw_path!r}")
        rel = safe_relpath(raw_path)
        path = safe_relpath(f"{prefix}/{rel}" if prefix else rel)
        digest = meta.get("sha256")
        if not isinstance(digest, str) or not HEX64.match(digest):
            raise ConfigError(f"bad sha256 for {path}")
        size = meta.get("size", meta.get("bytes"))
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
            raise ConfigError(f"bad size for {path}")
        out.append(Entry(path, digest, size))
    return out


def merge(entries: list[Entry], extra: list[Entry]) -> list[Entry]:
    """Add ``extra`` to ``entries``; the same path with a different hash is an error."""
    by_path = {e.path: e for e in entries}
    for e in extra:
        known = by_path.get(e.path)
        if known is None:
            by_path[e.path] = e
        elif known.sha256 != e.sha256:
            raise ConfigError(f"manifests disagree on {e.path}")
    return list(by_path.values())


def nested_manifest_entries(root: Path, entry: Entry) -> list[Entry]:
    """Entries of a verified nested ``*-manifest.json``, or none if it holds no ``files``."""
    if not entry.path.endswith("-manifest.json"):
        return []
    try:
        data = json.loads((root / entry.path).read_bytes())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read nested manifest {entry.path}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict | list):
        return []
    parent = PurePosixPath(entry.path).parent.as_posix()
    return parse_files(data, "" if parent == "." else parent)


# ---------------------------------------------------------------- hashing


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify_local(root: Path, entry: Entry) -> bool:
    """True when ``root/entry.path`` exists with the expected size and hash."""
    target = root / entry.path
    try:
        if not target.is_file() or target.is_symlink():
            return False
        if entry.size is not None and target.stat().st_size != entry.size:
            return False
        return sha256_file(target) == entry.sha256
    except OSError:
        return False


def verify_all(root: Path, entries: list[Entry]) -> tuple[list[Entry], list[Entry]]:
    """(all entries, entries that failed), expanding verified nested manifests."""
    bad: list[Entry] = []
    checked: set[str] = set()
    queue = list(entries)
    while queue:
        entry = queue.pop()
        if entry.path in checked:
            continue
        checked.add(entry.path)
        if not verify_local(root, entry):
            bad.append(entry)
            continue
        nested = nested_manifest_entries(root, entry)
        if nested:
            entries = merge(entries, nested)
            queue.extend(nested)
    return entries, bad


# ---------------------------------------------------------------- download


@dataclass(frozen=True, slots=True)
class Source:
    endpoint: str
    dataset: str
    revision: str

    def url(self, path: str) -> str:
        quoted = urllib.parse.quote(path, safe="/")
        return f"{self.endpoint}/datasets/{self.dataset}/resolve/{self.revision}/{quoted}"

    def marker(self, manifest_sha256: str, extras: dict[str, str]) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "dataset": self.dataset,
            "revision": self.revision,
            "manifest_sha256": manifest_sha256,
            "extra_manifests": dict(sorted(extras.items())),
        }


def _open(url: str, start: int) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    if start:
        req.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)  # noqa: S310


def fetch_bytes(source: Source, path: str, limit: int) -> bytes:
    for attempt in range(1, RETRIES + 1):
        try:
            with _open(source.url(path), 0) as resp:
                data: bytes = resp.read(limit + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == RETRIES:
                raise FetchError(f"{path}: {exc}") from None
            time.sleep(2**attempt)
            continue
        if len(data) > limit:
            raise FetchError(f"{path} is larger than {limit} bytes")
        return data
    raise AssertionError("unreachable")


def download(source: Source, root: Path, entry: Entry) -> None:
    """Download one file to ``<path>.part``, resuming, then verify and move it into place."""
    target = root / entry.path
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, RETRIES + 1):
        start = part.stat().st_size if part.exists() else 0
        if entry.size is not None and start > entry.size:
            part.unlink()
            start = 0
        try:
            if entry.size is None or start < entry.size:
                with _open(source.url(entry.path), start) as resp:
                    status = getattr(resp, "status", 200)
                    # 206 appends to the part; 200 is a full body, so start again.
                    mode = "ab" if start and status == HTTP_PARTIAL else "wb"
                    with part.open(mode) as out:
                        shutil.copyfileobj(resp, out, CHUNK)
        except urllib.error.HTTPError as exc:
            # 416 on a resume means the range starts past the end: the part may be complete.
            if not (exc.code == HTTP_RANGE_NOT_SATISFIABLE and start):
                if exc.code in HTTP_FATAL or attempt == RETRIES:
                    raise FetchError(f"{entry.path}: HTTP {exc.code}") from None
                time.sleep(2**attempt)
                continue
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt == RETRIES:
                raise FetchError(f"{entry.path}: {exc}") from None
            log(f"{entry.path}: {exc}; retry {attempt} of {RETRIES - 1}")
            time.sleep(2**attempt)
            continue
        if sha256_file(part) == entry.sha256:
            part.replace(target)
            return
        log(f"{entry.path}: checksum mismatch after download; discarding the partial file")
        part.unlink()
        if attempt == RETRIES:
            break
    raise FetchError(f"{entry.path}: checksum mismatch")


@contextlib.contextmanager
def locked(root: Path) -> Iterator[None]:
    """An exclusive lock on ``root``, so two containers never write the same volume at once."""
    import fcntl  # noqa: PLC0415 - POSIX only, and only needed when writing

    with (root / ".fetch.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------- main


def read_marker(root: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((root / SOURCE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_manifest(raw: bytes, pinned: str | None, where: str) -> Any:
    digest = hashlib.sha256(raw).hexdigest()
    if pinned and digest != pinned:
        raise FetchError(f"{where} manifest sha256 {digest} does not match MANIFEST_SHA256")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ConfigError(f"{where} manifest is not JSON: {exc}") from None


@dataclass(frozen=True, slots=True)
class Settings:
    source: Source | None
    manifest_name: str
    pinned: str | None
    extras: tuple[str, ...]
    workers: int


def local_extras(root: Path, settings: Settings) -> dict[str, str] | None:
    """sha256 of each local extra manifest, or None when one is missing."""
    out: dict[str, str] = {}
    for name in settings.extras:
        path = root / name
        if not path.is_file():
            return None
        out[name] = sha256_file(path)
    return out


def already_verified(root: Path, settings: Settings) -> bool:
    """True when ``root`` holds a verified copy of the wanted source (no network needed)."""
    source, pinned = settings.source, settings.pinned
    manifest_path = root / settings.manifest_name
    extras = local_extras(root, settings)
    if not manifest_path.is_file() or extras is None:
        if source is None:
            raise FetchError(f"{root} lacks {settings.manifest_name} or an extra manifest")
        return False
    local_raw = manifest_path.read_bytes()
    local_sha = hashlib.sha256(local_raw).hexdigest()
    same_source = source is None or read_marker(root) == source.marker(local_sha, extras)
    if not same_source or (pinned is not None and pinned != local_sha):
        if source is None:
            raise FetchError(f"{manifest_path} does not match MANIFEST_SHA256")
        return False
    entries = parse_files(load_manifest(local_raw, pinned, "local"))
    entries = merge(entries, [Entry(n, h, None) for n, h in extras.items()])
    entries, bad = verify_all(root, entries)
    if not bad:
        log(f"verified {len(entries)} files in {root}; no download needed")
        return True
    if source is None:
        for e in bad[:20]:
            log(f"missing or mismatched: {e.path}")
        raise FetchError(f"{len(bad)} files failed verification in {root}")
    log(f"missing or mismatched files: {len(bad)}; fetching")
    return False


def download_all(root: Path, source: Source, entries: list[Entry], workers: int) -> list[Entry]:
    """Download every entry that does not verify, expanding nested manifests as they land."""
    done: set[str] = set()
    pending = list(entries)
    while pending:
        todo = [e for e in pending if e.path not in done]
        pending = []
        missing = [e for e in todo if not verify_local(root, e)]
        log(f"present: {len(todo) - len(missing)}, to download: {len(missing)}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(download, source, root, e): e for e in missing}
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # re-raises FetchError and stops the run
                log(f"ok {futures[fut].path}")
        for e in todo:
            done.add(e.path)
            nested = nested_manifest_entries(root, e)
            if nested:
                entries = merge(entries, nested)
                pending.extend(n for n in nested if n.path not in done)
    return entries


def run(root: Path, settings: Settings) -> int:
    # 1. Fast path: the data dir already holds a verified copy of the wanted source.
    if already_verified(root, settings):
        return 0
    source = settings.source
    if source is None:  # already_verified raises in this case; kept for the type checker
        raise ConfigError("HF_DATASET is unset")
    manifest_path = root / settings.manifest_name

    # 2. Fetch the manifests (the main one in memory, written last) and every file that does
    # not verify. Extra manifests are entries keyed by the hash of the bytes just fetched.
    raw = fetch_bytes(source, settings.manifest_name, MAX_MANIFEST_BYTES)
    manifest = load_manifest(raw, settings.pinned, "remote")
    manifest_sha = hashlib.sha256(raw).hexdigest()
    extras: dict[str, str] = {}
    extra_entries: list[Entry] = []
    for name in settings.extras:
        blob = fetch_bytes(source, name, MAX_MANIFEST_BYTES)
        extras[name] = hashlib.sha256(blob).hexdigest()
        extra_entries.append(Entry(name, extras[name], len(blob)))
    base = merge(parse_files(manifest), extra_entries)
    need = sum(e.size or 0 for e in base)
    free = shutil.disk_usage(root).free
    if need > free:
        log(f"warning: manifests list {need} bytes, {free} bytes free (some may be present)")

    with locked(root):
        entries = download_all(root, source, base, settings.workers)
        # 3. Final full verification, then the manifest and source marker.
        (root / SOURCE_MARKER).unlink(missing_ok=True)
        write_atomic(manifest_path, raw)
        _, bad = verify_all(root, base)
        if bad:
            manifest_path.unlink(missing_ok=True)
            raise FetchError(f"{len(bad)} files failed final verification, e.g. {bad[0].path}")
        marker = json.dumps(source.marker(manifest_sha, extras), sort_keys=True).encode()
        write_atomic(root / SOURCE_MARKER, marker + b"\n")
    log(f"verified {len(entries)} files from {source.dataset}@{source.revision}")
    return 0


DEFAULT_EXTRAS = (
    "index/index-manifest.json,concepts/concepts-manifest.json,symspell/symspell-manifest.json"
)


def config_from_env(env: dict[str, str]) -> Settings:
    dataset = env.get("HF_DATASET", "").strip()
    revision = env.get("HF_REVISION", "").strip()
    endpoint = env.get("HF_ENDPOINT", "https://huggingface.co").strip().rstrip("/")
    manifest_name = safe_relpath(env.get("MANIFEST_NAME", "corpus-manifest.json").strip())
    extras = tuple(
        safe_relpath(n.strip())
        for n in env.get("EXTRA_MANIFESTS", DEFAULT_EXTRAS).split(",")
        if n.strip()
    )
    if manifest_name in extras or len(set(extras)) != len(extras):
        raise ConfigError("EXTRA_MANIFESTS repeats a manifest")
    pinned = env.get("MANIFEST_SHA256", "").strip().lower() or None
    if pinned and not HEX64.match(pinned):
        raise ConfigError("MANIFEST_SHA256 is not 64 hex characters")
    try:
        workers = int(env.get("ARTEFACTS_WORKERS", "4"))
    except ValueError:
        raise ConfigError("ARTEFACTS_WORKERS is not an integer") from None
    if not 1 <= workers <= MAX_WORKERS:
        raise ConfigError(f"ARTEFACTS_WORKERS must be 1 to {MAX_WORKERS}")
    source = None
    if dataset:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", dataset):
            raise ConfigError("HF_DATASET must look like owner/name")
        if not revision:
            raise ConfigError("HF_REVISION is required with HF_DATASET")
        if not HEX40.match(revision) and env.get("ARTEFACTS_ALLOW_BRANCH") != "1":
            raise ConfigError("HF_REVISION must be a 40-hex commit id")
        if not endpoint.startswith("https://"):
            raise ConfigError("HF_ENDPOINT must be https")
        source = Source(endpoint, dataset, urllib.parse.quote(revision, safe=""))
    return Settings(source, manifest_name, pinned, extras, workers)


def check_root(root: Path) -> None:
    if not root.is_dir():
        raise ConfigError(f"{root} is not a directory")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", "/data"))
    parser.add_argument("--verify-only", action="store_true", help="never download")
    args = parser.parse_args(argv)
    root = Path(args.data_dir)
    try:
        settings = config_from_env(dict(os.environ))
        if args.verify_only:
            settings = Settings(None, settings.manifest_name, settings.pinned, settings.extras, 1)
        check_root(root)
        return run(root, settings)
    except ConfigError as exc:
        log(f"configuration error: {exc}")
        return 2
    except FetchError as exc:
        log(f"failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
