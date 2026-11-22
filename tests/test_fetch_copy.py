"""Tests for fetch_artefacts.py --copy-to (R3)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]


def load_deploy_script(name: str) -> ModuleType:
    script_path = REPO / "deploy" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fetch_artefacts = load_deploy_script("fetch_artefacts")


@pytest.fixture
def fake_copy_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    index_dir = root / "index"
    index_dir.mkdir()
    nested_dir = index_dir / "nested"
    nested_dir.mkdir()
    store_dir = root / "store"
    store_dir.mkdir()

    file1 = store_dir / "file1.txt"
    file1_bytes = b"store file 1 contents"
    file1.write_bytes(file1_bytes)
    file1_sha = hashlib.sha256(file1_bytes).hexdigest()

    main_manifest = root / "corpus-manifest.json"
    main_manifest.write_text(
        json.dumps(
            {
                "files": {
                    "store/file1.txt": {"sha256": file1_sha, "size": len(file1_bytes)},
                }
            }
        ),
        encoding="utf-8",
    )

    deep_file = nested_dir / "deep.txt"
    deep_bytes = b"deep nested file contents"
    deep_file.write_bytes(deep_bytes)
    deep_sha = hashlib.sha256(deep_bytes).hexdigest()

    nested_manifest = nested_dir / "nested-manifest.json"
    nested_manifest_content = json.dumps(
        {
            "files": {
                "deep.txt": {"sha256": deep_sha, "size": len(deep_bytes)},
            }
        }
    ).encode("utf-8")
    nested_manifest.write_bytes(nested_manifest_content)
    nested_manifest_sha = hashlib.sha256(nested_manifest_content).hexdigest()

    extra_data = index_dir / "extra.bin"
    extra_bytes = b"extra binary data"
    extra_data.write_bytes(extra_bytes)
    extra_sha = hashlib.sha256(extra_bytes).hexdigest()

    index_manifest = index_dir / "index-manifest.json"
    index_manifest_content = json.dumps(
        {
            "files": {
                "nested/nested-manifest.json": {
                    "sha256": nested_manifest_sha,
                    "size": len(nested_manifest_content),
                },
                "extra.bin": {"sha256": extra_sha, "size": len(extra_bytes)},
            }
        }
    ).encode("utf-8")
    index_manifest.write_bytes(index_manifest_content)
    index_manifest_sha = hashlib.sha256(index_manifest_content).hexdigest()

    unlisted_file = root / "unlisted.txt"
    unlisted_file.write_text("unlisted file contents", encoding="utf-8")

    revision = "a" * 40
    endpoint = "https://huggingface.co"
    dataset = "chapter-and-verse/artefacts"

    monkeypatch.setenv("HF_DATASET", dataset)
    monkeypatch.setenv("HF_REVISION", revision)
    monkeypatch.setenv("HF_ENDPOINT", endpoint)
    monkeypatch.setenv("EXTRA_MANIFESTS", "index/index-manifest.json")

    source = fetch_artefacts.Source(endpoint, dataset, revision)
    main_sha = hashlib.sha256(main_manifest.read_bytes()).hexdigest()
    extras = {
        "index/index-manifest.json": index_manifest_sha,
    }
    marker_data = source.marker(main_sha, extras)
    (root / ".artefacts-source.json").write_text(json.dumps(marker_data), encoding="utf-8")
    return root


def test_copy_to_byte_identical(fake_copy_root: Path, tmp_path: Path) -> None:
    dst = tmp_path / "dst"
    code = fetch_artefacts.main(["--data-dir", str(fake_copy_root), "--copy-to", str(dst)])
    assert code == 0

    src_files = {p.relative_to(fake_copy_root): p for p in fake_copy_root.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dst): p for p in dst.rglob("*") if p.is_file()}
    assert set(src_files.keys()) == set(dst_files.keys())
    for rel, src_path in src_files.items():
        assert src_path.read_bytes() == dst_files[rel].read_bytes()
    assert (dst / "unlisted.txt").read_text(encoding="utf-8") == "unlisted file contents"
    assert (dst / ".artefacts-source.json").is_file()


def test_copy_to_tampered_listed_file(fake_copy_root: Path, tmp_path: Path) -> None:
    (fake_copy_root / "store" / "file1.txt").write_bytes(b"tampered store file")
    dst = tmp_path / "dst"
    code = fetch_artefacts.main(["--data-dir", str(fake_copy_root), "--copy-to", str(dst)])
    assert code == 1


def test_copy_to_wrong_marker(fake_copy_root: Path, tmp_path: Path) -> None:
    marker_path = fake_copy_root / ".artefacts-source.json"
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    data["revision"] = "b" * 40
    marker_path.write_text(json.dumps(data), encoding="utf-8")
    dst = tmp_path / "dst"
    code = fetch_artefacts.main(["--data-dir", str(fake_copy_root), "--copy-to", str(dst)])
    assert code == 3
    assert not dst.exists() or not any(dst.iterdir())


def test_copy_to_tampered_nested_manifest(fake_copy_root: Path, tmp_path: Path) -> None:
    nested_manifest = fake_copy_root / "index" / "nested" / "nested-manifest.json"
    nested_manifest.write_bytes(b'{"files": {}}')
    dst = tmp_path / "dst"
    code = fetch_artefacts.main(["--data-dir", str(fake_copy_root), "--copy-to", str(dst)])
    assert code == 1


def test_copy_to_missing_listed_file(fake_copy_root: Path, tmp_path: Path) -> None:
    (fake_copy_root / "index" / "extra.bin").unlink()
    dst = tmp_path / "dst"
    code = fetch_artefacts.main(["--data-dir", str(fake_copy_root), "--copy-to", str(dst)])
    assert code == 1
