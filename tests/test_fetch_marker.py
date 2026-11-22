"""Tests for fetch_artefacts.py --marker-only (R2)."""

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
def fake_artefacts_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    index_dir = root / "index"
    index_dir.mkdir()
    concepts_dir = root / "concepts"
    concepts_dir.mkdir()
    symspell_dir = root / "symspell"
    symspell_dir.mkdir()

    main_manifest = root / "corpus-manifest.json"
    main_manifest.write_text('{"files": {}}', encoding="utf-8")
    (index_dir / "index-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (concepts_dir / "concepts-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (symspell_dir / "symspell-manifest.json").write_text('{"files": {}}', encoding="utf-8")

    revision = "a" * 40
    endpoint = "https://huggingface.co"
    dataset = "chapter-and-verse/artefacts"

    monkeypatch.setenv("HF_DATASET", dataset)
    monkeypatch.setenv("HF_REVISION", revision)
    monkeypatch.setenv("HF_ENDPOINT", endpoint)

    source = fetch_artefacts.Source(endpoint, dataset, revision)
    main_sha = hashlib.sha256(main_manifest.read_bytes()).hexdigest()
    extras = {
        "index/index-manifest.json": hashlib.sha256(
            (index_dir / "index-manifest.json").read_bytes()
        ).hexdigest(),
        "concepts/concepts-manifest.json": hashlib.sha256(
            (concepts_dir / "concepts-manifest.json").read_bytes()
        ).hexdigest(),
        "symspell/symspell-manifest.json": hashlib.sha256(
            (symspell_dir / "symspell-manifest.json").read_bytes()
        ).hexdigest(),
    }
    marker_data = source.marker(main_sha, extras)
    (root / ".artefacts-source.json").write_text(json.dumps(marker_data), encoding="utf-8")
    return root


def test_marker_only_match(fake_artefacts_root: Path) -> None:
    code = fetch_artefacts.main(["--data-dir", str(fake_artefacts_root), "--marker-only"])
    assert code == 0


def test_marker_only_missing_marker(fake_artefacts_root: Path) -> None:
    (fake_artefacts_root / ".artefacts-source.json").unlink()
    code = fetch_artefacts.main(["--data-dir", str(fake_artefacts_root), "--marker-only"])
    assert code == 3


def test_marker_only_other_revision(fake_artefacts_root: Path) -> None:
    marker_path = fake_artefacts_root / ".artefacts-source.json"
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    data["revision"] = "b" * 40
    marker_path.write_text(json.dumps(data), encoding="utf-8")
    code = fetch_artefacts.main(["--data-dir", str(fake_artefacts_root), "--marker-only"])
    assert code == 3


def test_marker_only_missing_extra_manifest(fake_artefacts_root: Path) -> None:
    (fake_artefacts_root / "symspell" / "symspell-manifest.json").unlink()
    code = fetch_artefacts.main(["--data-dir", str(fake_artefacts_root), "--marker-only"])
    assert code == 3


def test_marker_only_not_a_directory(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    code = fetch_artefacts.main(["--data-dir", str(non_existent), "--marker-only"])
    assert code == 2
