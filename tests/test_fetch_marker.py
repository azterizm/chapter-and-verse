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
    dense_dir = root / "dense"
    dense_dir.mkdir()
    models_dir = root / "models"
    models_dir.mkdir()
    clarify_dir = root / "clarify"
    clarify_dir.mkdir()
    pit_dir = root / "pit"
    pit_dir.mkdir()

    main_manifest = root / "corpus-manifest.json"
    main_manifest.write_text('{"files": {}}', encoding="utf-8")
    (index_dir / "index-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (concepts_dir / "concepts-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (symspell_dir / "symspell-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (dense_dir / "dense-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (models_dir / "models-files-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (clarify_dir / "clarify-manifest.json").write_text('{"files": {}}', encoding="utf-8")
    (pit_dir / "pit-manifest.json").write_text('{"files": {}}', encoding="utf-8")

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
        "dense/dense-manifest.json": hashlib.sha256(
            (dense_dir / "dense-manifest.json").read_bytes()
        ).hexdigest(),
        "models/models-files-manifest.json": hashlib.sha256(
            (models_dir / "models-files-manifest.json").read_bytes()
        ).hexdigest(),
        "clarify/clarify-manifest.json": hashlib.sha256(
            (clarify_dir / "clarify-manifest.json").read_bytes()
        ).hexdigest(),
        "pit/pit-manifest.json": hashlib.sha256(
            (pit_dir / "pit-manifest.json").read_bytes()
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


def test_marker_only_ram_copy_passes_and_fails_after_marker_changed(
    fake_artefacts_root: Path, tmp_path: Path
) -> None:
    ram_dir = tmp_path / "ram"
    ram_dir.mkdir()
    (ram_dir / "index").mkdir()
    (ram_dir / "concepts").mkdir()
    (ram_dir / "symspell").mkdir()
    (ram_dir / "dense").mkdir()
    (ram_dir / "models").mkdir()
    (ram_dir / "clarify").mkdir()
    (ram_dir / "pit").mkdir()

    (ram_dir / "corpus-manifest.json").write_bytes(
        (fake_artefacts_root / "corpus-manifest.json").read_bytes()
    )
    (ram_dir / "index" / "index-manifest.json").write_bytes(
        (fake_artefacts_root / "index" / "index-manifest.json").read_bytes()
    )
    (ram_dir / "concepts" / "concepts-manifest.json").write_bytes(
        (fake_artefacts_root / "concepts" / "concepts-manifest.json").read_bytes()
    )
    (ram_dir / "symspell" / "symspell-manifest.json").write_bytes(
        (fake_artefacts_root / "symspell" / "symspell-manifest.json").read_bytes()
    )
    (ram_dir / "dense" / "dense-manifest.json").write_bytes(
        (fake_artefacts_root / "dense" / "dense-manifest.json").read_bytes()
    )
    (ram_dir / "models" / "models-files-manifest.json").write_bytes(
        (fake_artefacts_root / "models" / "models-files-manifest.json").read_bytes()
    )
    (ram_dir / "clarify" / "clarify-manifest.json").write_bytes(
        (fake_artefacts_root / "clarify" / "clarify-manifest.json").read_bytes()
    )
    (ram_dir / "pit" / "pit-manifest.json").write_bytes(
        (fake_artefacts_root / "pit" / "pit-manifest.json").read_bytes()
    )
    (ram_dir / ".artefacts-source.json").write_bytes(
        (fake_artefacts_root / ".artefacts-source.json").read_bytes()
    )

    code = fetch_artefacts.main(["--data-dir", str(ram_dir), "--marker-only"])
    assert code == 0

    marker_file = ram_dir / ".artefacts-source.json"
    data = json.loads(marker_file.read_text(encoding="utf-8"))
    data["revision"] = "c" * 40
    marker_file.write_text(json.dumps(data), encoding="utf-8")
    code_after = fetch_artefacts.main(["--data-dir", str(ram_dir), "--marker-only"])
    assert code_after == 3


def test_verify_only_parallel_on_marker_fixture(fake_artefacts_root: Path) -> None:
    code = fetch_artefacts.main(["--data-dir", str(fake_artefacts_root), "--verify-only"])
    assert code == 0


def test_default_extras_include_clarify_manifest() -> None:
    settings = fetch_artefacts.config_from_env({})
    assert "clarify/clarify-manifest.json" in settings.extras
    assert "clarify/clarify-manifest.json" in fetch_artefacts.DEFAULT_EXTRAS


def test_default_extras_include_pit_manifest() -> None:
    settings = fetch_artefacts.config_from_env({})
    assert "pit/pit-manifest.json" in settings.extras
    assert "pit/pit-manifest.json" in fetch_artefacts.DEFAULT_EXTRAS
