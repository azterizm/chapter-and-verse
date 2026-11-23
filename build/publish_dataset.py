"""Upload the text store, router indexes, SymSpell dictionary, dense index and model
to a Hugging Face Dataset, with an OGL v3.0 dataset card.

    uv run --with huggingface_hub python build/publish_dataset.py [--store DIR] [--data ROOT]
    uv run --with huggingface_hub python build/publish_dataset.py --repo OWNER/NAME --yes

Layout of the dataset (what ``deploy/fetch_artefacts.py`` fetches for one boot):

    corpus-manifest.json, <store files>     the text store, at the root
    index/index-manifest.json, ...          the router index, as the router wrote it
    concepts/concepts-manifest.json, ...    the router concept index, as the router wrote it
    symspell/symspell-manifest.json, ...    the SymSpell dictionary and manifest
    dense/dense-manifest.json, ...          the dense index
    models/models-files-manifest.json, ...  the query model
    README.md                               the dataset card

``--data`` (default ``$LEGAL_DATA_ROOT``) is the data root holding ``index/`` and
``concepts/``. ``--symspell`` (default ``artefacts/symspell``) holds the spelling
dictionary and manifest. ``--dense`` (default ``artefacts/dense/index``) holds the
quantised dense index. ``--models`` (default ``artefacts/models``) holds the ONNX
models directory and ``models-manifest.json``. ``--dense-model`` (default
``bge-small-en-v1.5``) chooses which query model to package.

Every file is checked against its own manifest first, both router manifests and the
dense manifest must carry the store's snapshot date, and the symspell and dense
manifests must carry the store's snapshot date and corpus_id.

Without ``--yes`` it is a dry run: it runs the checks and prints what would be uploaded and the
dataset card. A real upload needs both ``--repo`` and ``--yes``, and puts everything in one
commit, so one revision pins a consistent set. The token comes from ``HF_TOKEN`` or the
``huggingface_hub`` login. ``huggingface_hub`` is imported only for a real upload, so the dry
run works without it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from chapterverse.contracts import canonical_json
from chapterverse.store.format import MANIFEST_NAME
from chapterverse.store.manifest import (
    StoreLoadError,
    corpus_id,
    read_manifest,
    sha256_file,
    verify_files,
)

CARD_NAME: Final = "README.md"
ROUTER_DIRS: Final = (("index", "index-manifest.json"), ("concepts", "concepts-manifest.json"))


class PublishError(RuntimeError):
    """Something to be uploaded is missing, damaged or inconsistent."""


@dataclass(frozen=True, slots=True)
class Upload:
    path_in_repo: str
    local: Path | None
    size: int
    sha256: str
    content: bytes | None = None


@dataclass(frozen=True, slots=True)
class RouterDir:
    name: str
    manifest_name: str
    manifest: Mapping[str, Any]
    files: tuple[Upload, ...]  # the manifest first, then every file it lists

    @property
    def manifest_sha256(self) -> str:
        return self.files[0].sha256

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


# ---------------------------------------------------------------------------- checks


def store_uploads(store: Path) -> tuple[dict[str, Any], list[Upload]]:
    """The store manifest and its files, after checking every size and hash."""
    try:
        manifest = read_manifest(store)
        verify_files(store, manifest)
    except StoreLoadError as exc:
        raise PublishError(f"store: {exc}") from exc
    uploads = [
        Upload(name, store / name, entry["size"], entry["sha256"])
        for name, entry in sorted(manifest["files"].items())
    ]
    path = store / MANIFEST_NAME
    uploads.insert(0, Upload(MANIFEST_NAME, path, path.stat().st_size, sha256_file(path)))
    return manifest, uploads


def router_dir(data: Path, name: str, manifest_name: str, snapshot: str) -> RouterDir:
    """One router directory, checked against the manifest the router wrote for it."""
    root = data / name
    path = root / manifest_name
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise PublishError(f"no {name}/{manifest_name} under {data}") from None
    except ValueError as exc:
        raise PublishError(f"{name}/{manifest_name} is not valid JSON") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise PublishError(f"{name}/{manifest_name} lists no files")
    if manifest.get("snapshot") != snapshot:
        raise PublishError(
            f"{name}/{manifest_name} has snapshot {manifest.get('snapshot')!r}, "
            f"the store has {snapshot!r}"
        )
    uploads = [Upload(f"{name}/{manifest_name}", path, path.stat().st_size, sha256_file(path))]
    for rel, entry in sorted(files.items()):
        local = root / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or not local.is_file():
            raise PublishError(f"{name}/{rel}: missing or a bad path")
        size, digest = local.stat().st_size, sha256_file(local)
        wanted = entry.get("size", entry.get("bytes"))
        if digest != entry.get("sha256") or (wanted is not None and wanted != size):
            raise PublishError(f"{name}/{rel}: does not match {manifest_name}")
        uploads.append(Upload(f"{name}/{rel}", local, size, digest))
    return RouterDir(name, manifest_name, manifest, tuple(uploads))


def symspell_dir(data: Path, snapshot: str, cid: str) -> RouterDir:
    """The symspell directory, checked against its manifest and the store's snapshot/corpus_id."""
    name = "symspell"
    manifest_name = "symspell-manifest.json"
    path = data / manifest_name
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise PublishError(f"no {name}/{manifest_name} under {data}") from None
    except ValueError as exc:
        raise PublishError(f"{name}/{manifest_name} is not valid JSON") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise PublishError(f"{name}/{manifest_name} lists no files")
    if manifest.get("snapshot") != snapshot:
        raise PublishError(
            f"{name}/{manifest_name} has snapshot {manifest.get('snapshot')!r}, "
            f"the store has {snapshot!r}"
        )
    if manifest.get("corpus_id") != cid:
        raise PublishError(
            f"{name}/{manifest_name} has corpus_id {manifest.get('corpus_id')!r}, "
            f"the store has {cid!r}"
        )
    dict_entry = files.get("dictionary.txt")
    if not isinstance(dict_entry, dict):
        raise PublishError(f"{name}/{manifest_name} has no entry for dictionary.txt")
    dict_local = data / "dictionary.txt"
    if not dict_local.is_file():
        raise PublishError(f"{name}/dictionary.txt: missing")
    size, digest = dict_local.stat().st_size, sha256_file(dict_local)
    wanted = dict_entry.get("size", dict_entry.get("bytes"))
    if digest != dict_entry.get("sha256") or (wanted is not None and wanted != size):
        raise PublishError(f"{name}/dictionary.txt: does not match {manifest_name}")
    uploads = [
        Upload(f"{name}/{manifest_name}", path, path.stat().st_size, sha256_file(path)),
        Upload(f"{name}/dictionary.txt", dict_local, size, digest),
    ]
    return RouterDir(name, manifest_name, manifest, tuple(uploads))


def dense_dir(data: Path, snapshot: str, cid: str) -> RouterDir:
    """The dense index directory, checked against dense-manifest.json and the store."""
    name = "dense"
    manifest_name = "dense-manifest.json"
    path = data / manifest_name
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise PublishError(f"no {name}/{manifest_name} under {data}") from None
    except ValueError as exc:
        raise PublishError(f"{name}/{manifest_name} is not valid JSON") from exc
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, dict) or not files:
        raise PublishError(f"{name}/{manifest_name} lists no files")
    if manifest.get("snapshot") != snapshot:
        raise PublishError(
            f"{name}/{manifest_name} has snapshot {manifest.get('snapshot')!r}, "
            f"the store has {snapshot!r}"
        )
    if manifest.get("corpus_id") != cid:
        raise PublishError(
            f"{name}/{manifest_name} has corpus_id {manifest.get('corpus_id')!r}, "
            f"the store has {cid!r}"
        )
    uploads = [Upload(f"{name}/{manifest_name}", path, path.stat().st_size, sha256_file(path))]
    for rel, entry in sorted(files.items()):
        local = data / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or not local.is_file():
            raise PublishError(f"{name}/{rel}: missing or a bad path")
        size, digest = local.stat().st_size, sha256_file(local)
        wanted = entry.get("size", entry.get("bytes"))
        if digest != entry.get("sha256") or (wanted is not None and wanted != size):
            raise PublishError(f"{name}/{rel}: does not match {manifest_name}")
        uploads.append(Upload(f"{name}/{rel}", local, size, digest))
    return RouterDir(name, manifest_name, manifest, tuple(uploads))


def models_dir(data: Path, model_name: str) -> tuple[RouterDir, str, str]:
    """Check model files, generate restricted manifest and files manifest."""
    name = "models"
    manifest_path = data / "models-manifest.json"
    try:
        all_models = json.loads(manifest_path.read_bytes())
    except FileNotFoundError:
        raise PublishError(f"no {name}/models-manifest.json under {data}") from None
    except ValueError as exc:
        raise PublishError(f"{name}/models-manifest.json is not valid JSON") from exc

    if not isinstance(all_models, dict) or model_name not in all_models:
        raise PublishError(f"model {model_name!r} not in {name}/models-manifest.json")

    model_entry = all_models[model_name]
    model_files = model_entry.get("files") if isinstance(model_entry, dict) else None
    if not isinstance(model_files, dict) or not model_files:
        raise PublishError(f"model {model_name!r} has no files in {name}/models-manifest.json")

    model_dir = data / model_name
    model_uploads: list[Upload] = []
    files_manifest_entries: dict[str, dict[str, Any]] = {}

    for rel, entry in sorted(model_files.items()):
        local = model_dir / rel
        if Path(rel).is_absolute() or ".." in Path(rel).parts or not local.is_file():
            raise PublishError(f"{name}/{model_name}/{rel}: missing or a bad path")
        size, digest = local.stat().st_size, sha256_file(local)
        wanted = entry.get("size", entry.get("bytes"))
        if digest != entry.get("sha256") or (wanted is not None and wanted != size):
            raise PublishError(f"{name}/{model_name}/{rel}: does not match models-manifest.json")
        rel_in_models = f"{model_name}/{rel}"
        model_uploads.append(Upload(f"{name}/{rel_in_models}", local, size, digest))
        files_manifest_entries[rel_in_models] = {"sha256": digest, "size": size}

    # Restricted models-manifest.json with just the requested model
    restricted_manifest = {model_name: model_entry}
    restricted_bytes = canonical_json(restricted_manifest)
    restricted_sha = hashlib.sha256(restricted_bytes).hexdigest()
    restricted_size = len(restricted_bytes)
    files_manifest_entries["models-manifest.json"] = {
        "sha256": restricted_sha,
        "size": restricted_size,
    }

    # models-files-manifest.json covering models-manifest.json and all model files
    files_manifest = {"files": dict(sorted(files_manifest_entries.items()))}
    files_manifest_bytes = canonical_json(files_manifest)
    files_manifest_sha = hashlib.sha256(files_manifest_bytes).hexdigest()
    files_manifest_size = len(files_manifest_bytes)

    manifest_upload = Upload(
        f"{name}/models-files-manifest.json",
        local=None,
        size=files_manifest_size,
        sha256=files_manifest_sha,
        content=files_manifest_bytes,
    )
    restricted_upload = Upload(
        f"{name}/models-manifest.json",
        local=None,
        size=restricted_size,
        sha256=restricted_sha,
        content=restricted_bytes,
    )

    all_uploads = (manifest_upload, restricted_upload, *model_uploads)
    repo = str(model_entry.get("repo", ""))
    revision = str(model_entry.get("revision", ""))
    return (
        RouterDir(name, "models-files-manifest.json", files_manifest, all_uploads),
        repo,
        revision,
    )


# ---------------------------------------------------------------------------- card


def dataset_card(
    manifest: Mapping[str, Any],
    routers: Sequence[RouterDir],
    model_info: tuple[str, str, str] | None = None,
) -> str:
    """The dataset card: OGL v3.0 licence and attribution, contents, counts. Facts only."""
    source = manifest["source"]
    counts = manifest["counts"]
    compression = manifest["compression"]
    cid = corpus_id(manifest)
    files = manifest["files"]
    model_licence_lines = []
    if model_info:
        _name, m_repo, m_rev = model_info
        model_licence_lines = [
            "",
            f"Model licence: MIT, {m_repo} revision {m_rev}",
        ]
    lines = [
        "---",
        "license: other",
        "license_name: ogl-3.0",
        f"license_link: {source['licence_url']}",
        "language:",
        "- en",
        "pretty_name: Chapter and Verse UK legislation text store",
        "tags:",
        "- legal",
        "- legislation",
        "- uk",
        "---",
        "",
        "# Chapter and Verse UK legislation text store",
        "",
        "UK legislation records from legislation.gov.uk, stored for exact lookup by coordinate",
        "(for example `uk/ukpga/1996/18/s124`), with the citation router index and concept",
        "index built from the same records. These are the data files of the Chapter and Verse",
        "app. Each text row is a JSON record copied byte for byte from the source records.",
        "",
        "## Licence",
        "",
        str(source["attribution"]),
        "",
        f"Licence: Open Government Licence v3.0, {source['licence_url']}",
        *model_licence_lines,
        "",
        str(source.get("notes", "")),
        "",
        "## Version",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| corpus_id | `{cid}` |",
        f"| Snapshot date | {manifest['snapshot']} |",
        f"| Source checked | {source.get('date_checked', '')} |",
        f"| Store format version | {manifest['format_version']} |",
        f"| Compression | zstd level {compression['level']}, blocks {compression['blocks']} |",
        "",
        "`corpus_id` is the SHA-256 of `corpus-manifest.json`.",
        "",
        "## Counts",
        "",
        "| Count | Value |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in sorted(counts.items())),
        "",
        "## Files",
        "",
        "| Path | Bytes | SHA-256 |",
        "|---|---|---|",
        *(f"| `{name}` | {entry['size']} | `{entry['sha256']}` |" for name, entry in files.items()),
        f"| `{MANIFEST_NAME}` | | corpus_id |",
        *(f"| `{r.name}/` | {r.size} | `{r.manifest_name}` {r.manifest_sha256} |" for r in routers),
        "",
        "Each directory holds a manifest with the SHA-256 of every file in it.",
        "",
        "## Use",
        "",
        "```python",
        "from chapterverse.store import open_store",
        "",
        'store = open_store("path/to/this/dataset")',
        'rows = store.get("uk/ukpga/1996/18/s124")',
        "```",
        "",
        "Opening the store checks the size and SHA-256 of every file against the manifest.",
        "The format is described in `engine/chapterverse/store/format.py`.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- upload


def upload(repo: str, uploads: Sequence[Upload], card: str, message: str) -> str:
    """Upload everything to the dataset ``repo`` in one commit; returns the commit URL."""
    hub = importlib.import_module("huggingface_hub")  # optional; needed only here
    api = hub.HfApi()
    api.create_repo(repo, repo_type="dataset", exist_ok=True)
    operations = [
        hub.CommitOperationAdd(
            path_in_repo=u.path_in_repo,
            path_or_fileobj=u.content if u.content is not None else str(u.local),
        )
        for u in uploads
    ]
    operations.append(
        hub.CommitOperationAdd(path_in_repo=CARD_NAME, path_or_fileobj=card.encode("utf-8"))
    )
    commit = api.create_commit(
        repo_id=repo, repo_type="dataset", operations=operations, commit_message=message
    )
    return str(commit)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload the artefacts to a Hugging Face Dataset.")
    parser.add_argument("--store", type=Path, default=Path("artefacts/store"))
    parser.add_argument("--data", help="data root with index/ and concepts/ ($LEGAL_DATA_ROOT)")
    parser.add_argument(
        "--symspell",
        type=Path,
        default=Path("artefacts/symspell"),
        help="symspell directory (defaults to artefacts/symspell)",
    )
    parser.add_argument(
        "--dense",
        type=Path,
        default=Path("artefacts/dense/index"),
        help="dense index directory (defaults to artefacts/dense/index)",
    )
    parser.add_argument(
        "--models",
        type=Path,
        default=Path("artefacts/models"),
        help="models directory (defaults to artefacts/models)",
    )
    parser.add_argument(
        "--dense-model",
        type=str,
        default="bge-small-en-v1.5",
        help="dense query model name (defaults to bge-small-en-v1.5)",
    )
    parser.add_argument("--repo", help="dataset repository, OWNER/NAME")
    parser.add_argument("--yes", action="store_true", help="really upload (needs --repo)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="print what would be uploaded (the default; --yes turns it off)",
    )
    args = parser.parse_args(argv)
    if args.yes and not args.repo:
        parser.error("--yes needs --repo")
    data_root = args.data or os.environ.get("LEGAL_DATA_ROOT")
    if not data_root:
        parser.error("set --data or LEGAL_DATA_ROOT (the router index and concept index)")
    data = Path(data_root).expanduser()
    try:
        manifest, uploads = store_uploads(args.store)
        cid = corpus_id(manifest)
        routers = [router_dir(data, n, m, str(manifest["snapshot"])) for n, m in ROUTER_DIRS]
        symspell = symspell_dir(args.symspell, str(manifest["snapshot"]), cid)
        dense = dense_dir(args.dense, str(manifest["snapshot"]), cid)
        models, model_repo, model_rev = models_dir(args.models, args.dense_model)
        all_dirs = [*routers, symspell, dense, models]
    except PublishError as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return 1
    for d in all_dirs:
        uploads.extend(d.files)
    card = dataset_card(manifest, all_dirs, (args.dense_model, model_repo, model_rev))
    message = f"Artefacts for corpus {cid}, snapshot {manifest['snapshot']}"
    plan = {
        "dry_run": not args.yes,
        "repo": args.repo,
        "corpus_id": cid,
        "snapshot": manifest["snapshot"],
        "commit_message": message,
        "extra_manifests": {f"{d.name}/{d.manifest_name}": d.manifest_sha256 for d in all_dirs},
        "total_bytes": sum(u.size for u in uploads) + len(card.encode("utf-8")),
        "files": [
            *({"path": u.path_in_repo, "bytes": u.size, "sha256": u.sha256} for u in uploads),
            {"path": CARD_NAME, "bytes": len(card.encode("utf-8"))},
        ],
    }
    print(json.dumps(plan, indent=2))
    if not args.yes:
        print(card)
        return 0
    print(upload(args.repo, uploads, card, message))
    return 0


if __name__ == "__main__":
    sys.exit(main())
