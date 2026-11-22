"""Tests for S3.1 SymSpell dictionary artefact (requirements R1-R7)."""

from __future__ import annotations

import importlib.util
import json
import logging
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from symspellpy import SymSpell, Verbosity  # type: ignore[import-untyped]

from chapterverse.app import _load_slice2_dictionary, build_runtime, create_app
from chapterverse.normalise import load_dictionary
from chapterverse.store import open_store
from chapterverse.symspell_build import build_dictionary

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

if TYPE_CHECKING:
    from tests.test_pipeline_fakes import parse_sse
else:
    from test_pipeline_fakes import parse_sse


@pytest.fixture(scope="module")
def built_store(tmp_path_factory: pytest.TempPathFactory, fixture_data_root: Path) -> Path:
    out = tmp_path_factory.mktemp("store") / "store"
    cmd = [
        sys.executable,
        str(REPO / "build" / "textstore.py"),
        "build",
        "--data",
        str(fixture_data_root),
        "--out",
        str(out),
        "--workers",
        "1",
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=REPO)  # noqa: S603
    return out


@pytest.fixture(scope="module")
def fixture_symspell(built_store: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("symspell")
    store = open_store(built_store)
    build_dictionary(store, out)
    return out


def test_r7a_runtime_loads_dictionary_from_file_and_notices_misspellings(
    fixture_data_root: Path,
    built_store: Path,
    fixture_symspell: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R7(a): Build a dictionary from fixture store, run build_runtime with SYMSPELL_DICT:

    log says 'file', misspelled uncited query gets corrections notice.
    """
    caplog.set_level(logging.INFO)
    dict_file = fixture_symspell / "dictionary.txt"
    env = {
        "LEGAL_DATA_ROOT": str(fixture_data_root),
        "STORE_DIR": str(built_store),
        "GEN_PROVIDERS": "groq",
        "JUDGE_PROVIDERS": "groq",
        "CAP_SALT": "a" * 32,
        "SYMSPELL_DICT": str(dict_file),
    }

    app = create_app(lambda: build_runtime(env))
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200

        messages = [r.getMessage() for r in caplog.records]
        file_logs = [m for m in messages if "dictionary from file:" in m]
        assert len(file_logs) == 1, messages
        match = re.search(r"dictionary from file: (\d+) terms in ([\d.]+)s", file_logs[0])
        assert match is not None
        assert int(match.group(1)) > 0

        query = "unfiar dismisal compensatory award cap"
        res = client.post("/api/ask", json={"query": query})
        assert res.status_code == 200
        events = parse_sse(res.content)
        event_types = [t for t, _, _ in events]
        assert event_types == ["route", "notice", "clarify", "done"]

        notice_event = events[1][1]
        assert notice_event["code"] == "corrections_applied"
        assert '"unfiar" read as "unfair"' in notice_event["message"]


def test_r7b_tampered_dictionary_falls_back_to_store(
    fixture_data_root: Path,
    built_store: Path,
    fixture_symspell: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R7(b): Tampered dictionary byte falls back to store, runtime still works."""
    caplog.set_level(logging.INFO)
    tampered_dir = tmp_path / "tampered"
    shutil.copytree(fixture_symspell, tampered_dir)
    dict_path = tampered_dir / "dictionary.txt"
    data = bytearray(dict_path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    dict_path.write_bytes(bytes(data))

    env = {
        "LEGAL_DATA_ROOT": str(fixture_data_root),
        "STORE_DIR": str(built_store),
        "GEN_PROVIDERS": "groq",
        "JUDGE_PROVIDERS": "groq",
        "CAP_SALT": "a" * 32,
        "SYMSPELL_DICT": str(dict_path),
    }

    app = create_app(lambda: build_runtime(env))
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200

        messages = [r.getMessage() for r in caplog.records]
        warnings = [m for m in messages if "sha256 mismatch" in m]
        assert len(warnings) == 1, messages
        store_logs = [m for m in messages if "dictionary built from store:" in m]
        assert len(store_logs) == 1, messages

        query = "unfiar dismisal compensatory award cap"
        res = client.post("/api/ask", json={"query": query})
        assert res.status_code == 200
        events = parse_sse(res.content)
        assert [t for t, _, _ in events] == ["route", "notice", "clarify", "done"]


def test_r7b_wrong_corpus_id_falls_back_to_store(
    fixture_data_root: Path,
    built_store: Path,
    fixture_symspell: Path,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R7(b): Manifest with wrong corpus_id falls back to store, runtime still works."""
    caplog.set_level(logging.INFO)
    wrong_dir = tmp_path / "wrong_cid"
    shutil.copytree(fixture_symspell, wrong_dir)
    mpath = wrong_dir / "symspell-manifest.json"
    mdata = json.loads(mpath.read_bytes())
    mdata["corpus_id"] = "f" * 64
    mpath.write_text(json.dumps(mdata), encoding="utf-8")

    env = {
        "LEGAL_DATA_ROOT": str(fixture_data_root),
        "STORE_DIR": str(built_store),
        "GEN_PROVIDERS": "groq",
        "JUDGE_PROVIDERS": "groq",
        "CAP_SALT": "a" * 32,
        "SYMSPELL_DICT": str(wrong_dir / "dictionary.txt"),
    }

    app = create_app(lambda: build_runtime(env))
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200

        messages = [r.getMessage() for r in caplog.records]
        warnings = [m for m in messages if "corpus_id mismatch" in m]
        assert len(warnings) == 1, messages
        store_logs = [m for m in messages if "dictionary built from store:" in m]
        assert len(store_logs) == 1, messages

        query = "unfiar dismisal compensatory award cap"
        res = client.post("/api/ask", json={"query": query})
        assert res.status_code == 200


def test_r7c_no_symspell_dict_falls_back_to_store(
    built_store: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R7(c): No SYMSPELL_DICT: store path still works."""
    caplog.set_level(logging.INFO)
    store = open_store(built_store)
    sym = _load_slice2_dictionary({}, store)
    assert sym.word_count > 0

    messages = [r.getMessage() for r in caplog.records]
    store_logs = [m for m in messages if "dictionary built from store:" in m]
    assert len(store_logs) == 1, messages


def test_r7d_fetch_artefacts_verifies_nested_symspell_and_fails_on_tampering(
    built_store: Path,
    fixture_data_root: Path,
    fixture_symspell: Path,
    tmp_path: Path,
) -> None:
    """R7(d): fetch_artefacts verifies nested manifest and fails on tampered dictionary."""
    root = tmp_path / "data"
    root.mkdir()
    shutil.copytree(built_store, root, dirs_exist_ok=True)
    shutil.copytree(fixture_data_root / "index", root / "index")
    shutil.copytree(fixture_data_root / "concepts", root / "concepts")
    shutil.copytree(fixture_symspell, root / "symspell")

    extras = (
        "index/index-manifest.json",
        "concepts/concepts-manifest.json",
        "symspell/symspell-manifest.json",
    )
    settings = fetch_artefacts.Settings(
        source=None,
        manifest_name="corpus-manifest.json",
        pinned=None,
        extras=extras,
        workers=1,
    )
    assert fetch_artefacts.already_verified(root, settings) is True

    # Tamper dictionary.txt
    dict_file = root / "symspell" / "dictionary.txt"
    dict_file.write_bytes(dict_file.read_bytes() + b"tamper")
    with pytest.raises(fetch_artefacts.FetchError, match="failed verification"):
        fetch_artefacts.already_verified(root, settings)


def test_r7f_no_module_under_engine_references_artefacts_symspell() -> None:
    """R7(f): No module under engine/ references artefacts/symspell (grep test)."""
    engine_dir = REPO / "engine"
    offenders: list[str] = []
    for p in sorted(engine_dir.rglob("*.py")):
        content = p.read_text(encoding="utf-8")
        if "artefacts/symspell" in content:
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, f"Modules under engine/ reference artefacts/symspell: {offenders}"


def _apply_one_edit(word: str, rng: random.Random) -> str:
    if len(word) <= 1:
        return word + "x"
    op = rng.choice(["delete", "insert", "replace", "transpose"])
    idx = rng.randint(0, len(word) - 1)
    if op == "delete":
        return word[:idx] + word[idx + 1 :]
    if op == "insert":
        char = rng.choice("abcdefghijklmnopqrstuvwxyz")
        return word[:idx] + char + word[idx:]
    if op == "replace":
        char = rng.choice("abcdefghijklmnopqrstuvwxyz")
        return word[:idx] + char + word[idx + 1 :]
    if idx == len(word) - 1:
        idx -= 1
    return word[:idx] + word[idx + 1] + word[idx] + word[idx + 2 :]


def test_r3_dictionary_loader_identical_results(fixture_symspell: Path) -> None:
    """R3: 2,000 words sampled from dictionary with one edit applied (fixed seed) return

    identical suggestions for lookup(Verbosity.CLOSEST, max_edit_distance=2) and lookup_compound.
    """
    dict_path = fixture_symspell / "dictionary.txt"

    words: list[str] = []
    with dict_path.open("r", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if parts:
                words.append(parts[0])

    rng = random.Random(42)
    sample_count = min(2000, len(words))
    sample = rng.sample(words, sample_count)
    edited = [_apply_one_edit(w, rng) for w in sample]

    sym_new = load_dictionary(dict_path)
    sym_old = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
    sym_old.load_dictionary(
        str(dict_path), term_index=0, count_index=1, separator=" ", encoding="utf-8"
    )

    for w in edited:
        r_new = sym_new.lookup(w, Verbosity.CLOSEST, max_edit_distance=2)
        r_old = sym_old.lookup(w, Verbosity.CLOSEST, max_edit_distance=2)
        assert [(s.term, s.distance, s.count) for s in r_new] == [
            (s.term, s.distance, s.count) for s in r_old
        ]

        c_new = sym_new.lookup_compound(w, max_edit_distance=2)
        c_old = sym_old.lookup_compound(w, max_edit_distance=2)
        assert [(s.term, s.distance, s.count) for s in c_new] == [
            (s.term, s.distance, s.count) for s in c_old
        ]
