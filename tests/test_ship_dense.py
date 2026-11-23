"""Container start path test with dense index and model (requirement R6).

Tests:
- builds data dir in tmp with fixture store, small dense index, and models dir
- runs fetch_artefacts.py --verify-only on it (succeeds, exit 0)
- runs build_runtime with DENSE_DIR and DENSE_MODEL_DIR pointing into it and no repo
  artefacts consulted; startup succeeds and logs dense on
- a tampered vectors.i8 makes fetch verification fail (exit 1)
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from legal_rag_router.concepts import load_concepts

from chapterverse.app import build_runtime
from chapterverse.contracts import canonical_json
from chapterverse.dense.store import DenseLoadError
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


def _prepare_test_data_dir(
    data_dir: Path,
    built_store: Path,
    fixture_data_root: Path,
    real_model_dir: Path,
    real_manifest_path: Path,
) -> tuple[int, Path, Path, Path]:
    data_dir.mkdir()
    shutil.copytree(built_store, data_dir, dirs_exist_ok=True)
    shutil.copytree(fixture_data_root / "index", data_dir / "index")
    shutil.copytree(fixture_data_root / "concepts", data_dir / "concepts")

    sym_dir = data_dir / "symspell"
    build_dictionary(open_store(data_dir), sym_dir)

    concepts = load_concepts(fixture_data_root / "concepts")
    doc_count = concepts.doc_count
    dim = 384
    dense_dir = data_dir / "dense"
    dense_dir.mkdir()

    vecs = np.zeros((doc_count, dim), dtype=np.int8)
    codes = np.zeros((doc_count, dim // 8), dtype=np.uint8)
    scales = np.ones(dim, dtype=np.float32)

    vecs_file = dense_dir / "vectors.i8"
    codes_file = dense_dir / "codes.bin"
    scales_file = dense_dir / "scales.f32"
    vecs_file.write_bytes(vecs.tobytes())
    codes_file.write_bytes(codes.tobytes())
    scales_file.write_bytes(scales.tobytes())

    store = open_store(data_dir)
    first_instrument = getattr(concepts, "first_instrument_doc", doc_count)
    dense_manifest = {
        "format_version": 1,
        "kind": "chapterverse-dense-index",
        "model": "bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "dim": dim,
        "count": doc_count,
        "corpus_id": store.corpus_id,
        "snapshot": store.snapshot,
        "first_instrument_doc": first_instrument,
        "quantisation": "int8_scalar_per_dim+binary_sign",
        "files": {
            "codes.bin": {
                "sha256": hashlib.sha256(codes_file.read_bytes()).hexdigest(),
                "size": codes_file.stat().st_size,
            },
            "scales.f32": {
                "sha256": hashlib.sha256(scales_file.read_bytes()).hexdigest(),
                "size": scales_file.stat().st_size,
            },
            "vectors.i8": {
                "sha256": hashlib.sha256(vecs_file.read_bytes()).hexdigest(),
                "size": vecs_file.stat().st_size,
            },
        },
    }
    (dense_dir / "dense-manifest.json").write_bytes(canonical_json(dense_manifest))

    models_dir = data_dir / "models"
    models_dir.mkdir()
    shutil.copytree(real_model_dir, models_dir / "bge-small-en-v1.5")

    manifest_data = json.loads(real_manifest_path.read_bytes())
    model_entry = manifest_data["bge-small-en-v1.5"]
    restricted_manifest = {"bge-small-en-v1.5": model_entry}
    restricted_bytes = canonical_json(restricted_manifest)
    (models_dir / "models-manifest.json").write_bytes(restricted_bytes)

    files_dict: dict[str, dict[str, int | str]] = {
        "models-manifest.json": {
            "sha256": hashlib.sha256(restricted_bytes).hexdigest(),
            "size": len(restricted_bytes),
        }
    }
    for rel in model_entry["files"]:
        local_file = models_dir / "bge-small-en-v1.5" / rel
        files_dict[f"bge-small-en-v1.5/{rel}"] = {
            "sha256": hashlib.sha256(local_file.read_bytes()).hexdigest(),
            "size": local_file.stat().st_size,
        }
    files_manifest = {"files": dict(sorted(files_dict.items()))}
    (models_dir / "models-files-manifest.json").write_bytes(canonical_json(files_manifest))

    return doc_count, dense_dir, models_dir, vecs_file


def test_container_start_path_and_dense_verification(
    built_store: Path,
    fixture_data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R6: Full start path verification and fail-closed check on tampered vectors.i8."""
    real_model_dir = REPO / "artefacts" / "models" / "bge-small-en-v1.5"
    real_manifest_path = REPO / "artefacts" / "models" / "models-manifest.json"
    if not (real_model_dir.is_dir() and real_manifest_path.is_file()):
        pytest.skip("real bge-small-en-v1.5 model artefacts not present")

    data_dir = tmp_path / "data"
    doc_count, dense_dir, models_dir, vecs_file = _prepare_test_data_dir(
        data_dir, built_store, fixture_data_root, real_model_dir, real_manifest_path
    )

    code = fetch_artefacts.main(["--data-dir", str(data_dir), "--verify-only"])
    assert code == 0

    monkeypatch.chdir(tmp_path)
    caplog.set_level(logging.INFO)

    env = {
        "STORE_DIR": str(data_dir),
        "LEGAL_DATA_ROOT": str(data_dir),
        "SYMSPELL_DICT": str(data_dir / "symspell" / "dictionary.txt"),
        "DENSE_DIR": str(dense_dir),
        "DENSE_MODEL_DIR": str(models_dir),
        "GEN_PROVIDERS": "groq",
        "JUDGE_PROVIDERS": "groq",
        "CAP_SALT": "a" * 32,
    }

    rt = build_runtime(env)
    assert rt is not None
    assert rt.pipeline.dense is not None
    assert rt.pipeline.dense.store.count == doc_count

    dense_logs = [r.getMessage() for r in caplog.records if "dense retrieval on:" in r.getMessage()]
    assert len(dense_logs) == 1, [r.getMessage() for r in caplog.records]
    assert f"{doc_count} vectors loaded in" in dense_logs[0]

    orig_vecs_bytes = vecs_file.read_bytes()
    tampered_vecs = bytearray(orig_vecs_bytes)
    tampered_vecs[0] ^= 0xFF
    vecs_file.write_bytes(bytes(tampered_vecs))

    code_tampered = fetch_artefacts.main(["--data-dir", str(data_dir), "--verify-only"])
    assert code_tampered == 1

    with pytest.raises(DenseLoadError):
        build_runtime(env)
