"""The write path: ``build/textstore.py``, ``build/manifest.py`` and ``build/publish_dataset.py``
run as scripts on the fixture corpus."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from chapterverse.contracts import canonical_json
from chapterverse.store import CompressedTextStore, open_store
from chapterverse.store.format import MANIFEST_NAME, TEXT_NAME
from chapterverse.store.manifest import corpus_id, read_manifest
from chapterverse.symspell_build import build_dictionary

REPO = Path(__file__).resolve().parents[1]
BUILD = REPO / "build"


def run(script: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(BUILD / script), *args]
    return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=REPO)  # noqa: S603


def build(
    data: Path, out: Path, *extra: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    args = ["build", "--data", str(data), "--out", str(out), "--workers", "1", *extra]
    return run("textstore.py", *args, check=check)


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"build_{name}", BUILD / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def textstore() -> ModuleType:
    return load_script("textstore")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory, fixture_data_root: Path) -> Path:
    out = tmp_path_factory.mktemp("built") / "store"
    build(fixture_data_root, out)
    return out


@pytest.fixture(scope="module")
def built_symspell(built: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("symspell")
    build_dictionary(open_store(built), out)
    return out


def all_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted((root / "uk").rglob("*.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text("utf-8").splitlines())
    return rows


# ---------------------------------------------------------------------------- determinism


@pytest.mark.parametrize("extra", [(), ("--blocks", "packed:4096", "--dict-size", "900")])
def test_building_twice_gives_identical_files(
    fixture_data_root: Path, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    build(fixture_data_root, a, *extra)
    build(fixture_data_root, b, *extra, "--workers", "3")
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir())
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


# ---------------------------------------------------------------------------- manifest


def test_manifest_contents(built: Path, fixture_data_root: Path) -> None:
    raw = (built / MANIFEST_NAME).read_bytes()
    manifest = json.loads(raw)
    assert raw == canonical_json(manifest)  # the file is its own canonical form
    assert manifest["format_version"] == 1
    assert manifest["kind"] == "chapterverse-text-store"
    index = json.loads((fixture_data_root / "index" / "index-manifest.json").read_bytes())
    assert manifest["snapshot"] == index["snapshot"]
    source = json.loads((fixture_data_root / "MANIFEST.json").read_bytes())["sources"][0]
    assert manifest["source"]["licence"] == "OGL-3.0"
    assert manifest["source"]["attribution"] == source["attribution"]
    assert "Open Government Licence v3.0" in manifest["source"]["attribution"]
    assert manifest["source"]["licence_url"] == source["licence_url"]
    rows = all_rows(fixture_data_root)
    instruments = [r for r in rows if r["record_type"] == "instrument"]
    assert manifest["counts"] == manifest["counts"] | {
        "instruments": len(instruments),
        "instruments_metadata_only": 1,
        "provisions": len(rows) - len(instruments),
        "coordinates": len(rows),
        "source_bytes": sum(p.stat().st_size for p in (fixture_data_root / "uk").rglob("*.jsonl")),
    }
    for name, entry in manifest["files"].items():
        data = (built / name).read_bytes()
        assert entry == {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    assert str(fixture_data_root) not in raw.decode()  # no local paths in a published file


def test_corpus_id_is_the_manifest_hash(built: Path) -> None:
    store = CompressedTextStore(built)
    assert store.corpus_id == hashlib.sha256((built / MANIFEST_NAME).read_bytes()).hexdigest()
    shown = json.loads(run("manifest.py", "show", "--store", str(built)).stdout)
    assert shown["corpus_id"] == store.corpus_id
    assert shown["manifest_is_canonical"] is True
    assert json.loads(run("manifest.py", "verify", "--store", str(built)).stdout)["ok"] is True


def test_manifest_verify_reports_damage(built: Path, tmp_path: Path) -> None:
    copy = Path(shutil.copytree(built, tmp_path / "s"))
    text = copy / TEXT_NAME
    data = bytearray(text.read_bytes())
    data[len(data) // 2] ^= 0x80
    text.write_bytes(bytes(data))
    result = run("manifest.py", "verify", "--store", str(copy), check=False)
    assert result.returncode == 1
    assert "hash mismatch" in json.loads(result.stdout)["error"]


# ---------------------------------------------------------------------------- policies


@pytest.mark.parametrize(
    "blocks", ["instrument", "toplevel", "fixed:1", "fixed:2000", "packed:1", "packed:3000"]
)
def test_every_block_policy_gives_the_same_rows(
    built: Path, fixture_data_root: Path, tmp_path: Path, blocks: str
) -> None:
    other = tmp_path / "store"
    build(fixture_data_root, other, "--blocks", blocks)
    a, b = CompressedTextStore(built), CompressedTextStore(other)
    assert b.manifest["compression"]["blocks"] == blocks
    for row in all_rows(fixture_data_root):
        assert a.get(row["coordinate"]) == b.get(row["coordinate"])


def test_plan_cuts(textstore: ModuleType) -> None:
    policy = textstore.Policy.parse
    # Lines: instrument (10 bytes), then top-level a (5), a/1 (5), b (30), c (5).
    starts = [0, 10, 15, 20, 50, 55]
    top = [False, True, False, True, True]
    assert textstore.plan_cuts(policy("instrument"), starts, top) == [0, 55]
    assert textstore.plan_cuts(policy("toplevel"), starts, top) == [0, 10, 20, 50, 55]
    assert textstore.plan_cuts(policy("fixed:10"), starts, top) == [0, 10, 20, 50, 55]
    assert textstore.plan_cuts(policy("fixed:100"), starts, top) == [0, 10, 55]
    assert textstore.plan_cuts(policy("packed:12"), starts, top) == [0, 10, 20, 50, 55]
    assert textstore.plan_cuts(policy("packed:40"), starts, top) == [0, 10, 50, 55]
    assert textstore.plan_cuts(policy("packed:9"), [0, 10], [False]) == [0, 10]
    for bad in ("fixed", "fixed:0", "packed:x", "toplevel:5", "nope"):
        with pytest.raises(ValueError, match="bad block policy"):
            policy(bad)


def test_dictionary_samples_split_at_line_ends(textstore: ModuleType) -> None:
    limit = textstore.DICT_SAMPLE_MAX
    small = b"a" * 9 + b"\n"
    big = b"b" * (limit + 5) + b"\n"
    raw = small * 3 + big + small * (2 * limit // len(small))
    pieces = list(textstore._sample_pieces(raw, [0, len(small), len(raw)]))
    assert b"".join(pieces) == raw
    assert pieces[0] == small
    assert all(piece.endswith(b"\n") for piece in pieces)
    assert all(len(piece) <= limit or piece.count(b"\n") == 1 for piece in pieces)
    assert big in pieces  # a line longer than the limit stays whole


def test_sorted_blocks_fallback(textstore: ModuleType, tmp_path: Path) -> None:
    lines = [b"b/1\t@0,1,1,t\n", b"a\t@1,1,2,t\n", b"a/x\t1,1\n", b"b\t@3,1,1,m\n"]
    blocks = []
    for chunk in (lines[:1], lines[1:3], lines[3:]):
        offsets, position = [], 0
        for line in chunk:
            offsets.append(position)
            position += len(line)
        raw = b"".join(int(o).to_bytes(4, "little") for o in offsets)
        blocks.append((b"".join(chunk), raw, len(chunk)))
    tbl, off = tmp_path / "t.tbl", tmp_path / "t.off"
    count = textstore.write_table(textstore.sorted_blocks(blocks), tbl, off)
    from legal_rag_router.table import SortedTable, encode_table  # noqa: PLC0415

    items = [(k, v) for k, v in (line.decode().rstrip("\n").split("\t") for line in lines)]
    assert count == 4
    assert (tbl.read_bytes(), off.read_bytes()) == encode_table(items)
    assert SortedTable.open(tbl, off).get("a/x") == "1,1"


# ---------------------------------------------------------------------------- bad input


def _copy_data(root: Path, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    shutil.copytree(root, data)
    return data


ERA = Path("uk/ukpga/1996/uk_ukpga_1996_18.jsonl")


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda d: (d / ERA).write_bytes((d / ERA).read_bytes()[:-1]), "end with a newline"),
        (
            lambda d: (d / ERA).write_bytes((d / ERA).read_bytes().replace(b"\n", b"\r\n", 1)),
            "carriage return",
        ),
        (
            lambda d: (d / ERA).write_bytes(
                (d / ERA).read_bytes() + (d / ERA).read_bytes().split(b"\n")[1] + b"\n"
            ),
            "duplicate provision",
        ),
        (lambda d: (d / ERA).rename(d / "uk/ukpga/1980" / ERA.name), "would not resolve"),
        (
            lambda d: (d / ERA).write_bytes(b"\n".join((d / ERA).read_bytes().split(b"\n")[1:])),
            "not an instrument record",
        ),
    ],
)
def test_build_refuses_data_it_cannot_store_exactly(
    fixture_data_root: Path, tmp_path: Path, damage: Any, message: str
) -> None:
    data = _copy_data(fixture_data_root, tmp_path)
    damage(data)
    result = build(data, tmp_path / "out", check=False)
    assert result.returncode != 0
    assert message in result.stderr
    assert not (tmp_path / "out").exists()


def test_build_replaces_a_store_but_not_other_directories(
    fixture_data_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "store"
    build(fixture_data_root, out)
    first = (out / MANIFEST_NAME).read_bytes()
    build(fixture_data_root, out)
    assert (out / MANIFEST_NAME).read_bytes() == first
    assert not out.with_name("store.partial").exists()
    other = tmp_path / "other"
    other.mkdir()
    (other / "keep.txt").write_text("x")
    result = build(fixture_data_root, other, check=False)
    assert result.returncode != 0
    assert "refusing to replace" in result.stderr
    assert (other / "keep.txt").exists()


def test_build_reports_numbers(fixture_data_root: Path, tmp_path: Path) -> None:
    report = json.loads(build(fixture_data_root, tmp_path / "s").stdout)
    assert report["counts"]["instruments"] == 4
    assert report["store_bytes"] == sum(report["files"].values())
    assert report["corpus_id"] == open_store(tmp_path / "s").corpus_id


# ---------------------------------------------------------------------------- publish


_FORBIDDEN = re.compile(r"[\u2013\u2014\u2190-\u21ff\u2018\u2019\u201c\u201d\u2500-\u257f]")


def make_dense_fixture(store_path: Path, out: Path) -> Path:
    manifest = read_manifest(store_path)
    cid = corpus_id(manifest)
    snap = str(manifest["snapshot"])
    out.mkdir(parents=True, exist_ok=True)
    dim = 384
    count = 2
    vecs = b"\x00" * (count * dim)
    codes = b"\x00" * (count * (dim // 8))
    scales = b"\x00\x00\x80?" * dim
    (out / "vectors.i8").write_bytes(vecs)
    (out / "codes.bin").write_bytes(codes)
    (out / "scales.f32").write_bytes(scales)
    dense_manifest = {
        "format_version": 1,
        "kind": "chapterverse-dense-index",
        "model": "bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "dim": dim,
        "count": count,
        "corpus_id": cid,
        "snapshot": snap,
        "first_instrument_doc": count,
        "quantisation": "int8_scalar_per_dim+binary_sign",
        "files": {
            "codes.bin": {
                "sha256": hashlib.sha256(codes).hexdigest(),
                "size": len(codes),
            },
            "scales.f32": {
                "sha256": hashlib.sha256(scales).hexdigest(),
                "size": len(scales),
            },
            "vectors.i8": {
                "sha256": hashlib.sha256(vecs).hexdigest(),
                "size": len(vecs),
            },
        },
    }
    (out / "dense-manifest.json").write_bytes(canonical_json(dense_manifest))
    return out


def publish(built: Path, data: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    extra_list = list(extra)
    if "--dense" not in extra_list:
        dense_dir = built.parent / "dense"
        if not (dense_dir / "dense-manifest.json").is_file():
            make_dense_fixture(built, dense_dir)
        extra_list.extend(["--dense", str(dense_dir)])
    if "--models" not in extra_list:
        extra_list.extend(["--models", str(REPO / "artefacts" / "models")])
    if "--symspell" not in extra_list:
        sym_dir = built.parent / "symspell"
        if not (sym_dir / "symspell-manifest.json").is_file():
            manifest = read_manifest(built)
            cid = corpus_id(manifest)
            snap = str(manifest["snapshot"])
            sym_dir.mkdir(parents=True, exist_ok=True)
            dict_bytes = b"the\t100\n"
            (sym_dir / "dictionary.txt").write_bytes(dict_bytes)
            sym_manifest = {
                "format_version": 1,
                "kind": "chapterverse-symspell",
                "corpus_id": cid,
                "snapshot": snap,
                "files": {
                    "dictionary.txt": {
                        "sha256": hashlib.sha256(dict_bytes).hexdigest(),
                        "size": len(dict_bytes),
                    }
                },
            }
            (sym_dir / "symspell-manifest.json").write_bytes(canonical_json(sym_manifest))
        extra_list.extend(["--symspell", str(sym_dir)])
    return run(
        "publish_dataset.py", "--store", str(built), "--data", str(data), *extra_list, check=False
    )


def _verify_dry_run_files(
    plan: dict[str, Any],
    *,
    built: Path,
    fixture_data_root: Path,
    built_symspell: Path,
    dense_dir: Path,
    models_dir: Path,
    expected_names: set[str],
) -> None:
    names = [f["path"] for f in plan["files"]]
    assert len(names) == len(set(names))
    assert set(names) == expected_names
    for entry in plan["files"][:-1]:
        p = entry["path"]
        if p.startswith("models/"):
            if p not in ("models/models-files-manifest.json", "models/models-manifest.json"):
                rel_in_model = p.removeprefix("models/bge-small-en-v1.5/")
                local = models_dir / "bge-small-en-v1.5" / rel_in_model
                assert entry["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
        elif p.startswith("dense/"):
            local = dense_dir / Path(p).name
            assert entry["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
        elif p.startswith("symspell/"):
            local = built_symspell / Path(p).name
            assert entry["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
        elif "/" in p:
            local = fixture_data_root / p
            assert entry["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
        else:
            local = built / p
            assert entry["sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()


def test_publish_is_a_dry_run_by_default(
    built: Path, fixture_data_root: Path, built_symspell: Path
) -> None:
    result = publish(built, fixture_data_root, "--symspell", str(built_symspell))
    assert result.returncode == 0, result.stderr
    plan_text, _, card = result.stdout.partition("\n---\n")
    plan = json.loads(plan_text)
    assert plan["dry_run"] is True
    assert plan["corpus_id"] == open_store(built).corpus_id
    store_names = {*json.loads((built / MANIFEST_NAME).read_bytes())["files"], MANIFEST_NAME}
    router_names = set()
    for name, manifest_name in (
        ("index", "index-manifest.json"),
        ("concepts", "concepts-manifest.json"),
    ):
        manifest_path = fixture_data_root / name / manifest_name
        router_names |= {f"{name}/{manifest_name}"}
        router_names |= {f"{name}/{rel}" for rel in json.loads(manifest_path.read_bytes())["files"]}
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        assert plan["extra_manifests"][f"{name}/{manifest_name}"] == digest
    symspell_manifest_path = built_symspell / "symspell-manifest.json"
    symspell_digest = hashlib.sha256(symspell_manifest_path.read_bytes()).hexdigest()
    assert plan["extra_manifests"]["symspell/symspell-manifest.json"] == symspell_digest
    symspell_names = {"symspell/symspell-manifest.json", "symspell/dictionary.txt"}

    dense_dir = built.parent / "dense"
    dense_manifest_path = dense_dir / "dense-manifest.json"
    dense_digest = hashlib.sha256(dense_manifest_path.read_bytes()).hexdigest()
    assert plan["extra_manifests"]["dense/dense-manifest.json"] == dense_digest
    dense_names = {
        "dense/dense-manifest.json",
        *(f"dense/{rel}" for rel in json.loads(dense_manifest_path.read_bytes())["files"]),
    }

    models_dir = REPO / "artefacts" / "models"
    models_files_manifest = plan["extra_manifests"].get("models/models-files-manifest.json")
    assert models_files_manifest is not None
    model_manifest_data = json.loads((models_dir / "models-manifest.json").read_bytes())[
        "bge-small-en-v1.5"
    ]
    model_file_names = {
        "models/models-files-manifest.json",
        "models/models-manifest.json",
        *(f"models/bge-small-en-v1.5/{rel}" for rel in model_manifest_data["files"]),
    }

    all_names = (
        store_names | router_names | symspell_names | dense_names | model_file_names | {"README.md"}
    )
    _verify_dry_run_files(
        plan,
        built=built,
        fixture_data_root=fixture_data_root,
        built_symspell=built_symspell,
        dense_dir=dense_dir,
        models_dir=models_dir,
        expected_names=all_names,
    )
    assert "license_name: ogl-3.0" in card
    assert "Open Government Licence v3.0" in card
    assert plan["corpus_id"] in card
    assert "| `symspell/` |" in card
    assert "| `dense/` |" in card
    assert "| `models/` |" in card
    assert "Model licence: MIT" in card
    assert not _FORBIDDEN.search(card)
    assert "**" not in card


def test_publish_refuses_symspell_of_another_corpus_id(
    built: Path, fixture_data_root: Path, built_symspell: Path, tmp_path: Path
) -> None:
    sym_copy = tmp_path / "sym"
    shutil.copytree(built_symspell, sym_copy)
    mpath = sym_copy / "symspell-manifest.json"
    manifest = json.loads(mpath.read_bytes())
    manifest["corpus_id"] = "0" * 64
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    result = publish(built, fixture_data_root, "--symspell", str(sym_copy))
    assert result.returncode == 1
    assert "symspell/symspell-manifest.json has corpus_id" in result.stderr


def test_publish_refuses_dense_of_another_corpus_id(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    dense_copy = tmp_path / "dense"
    make_dense_fixture(built, dense_copy)
    mpath = dense_copy / "dense-manifest.json"
    manifest = json.loads(mpath.read_bytes())
    manifest["corpus_id"] = "0" * 64
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    result = publish(built, fixture_data_root, "--dense", str(dense_copy))
    assert result.returncode == 1
    assert "dense/dense-manifest.json has corpus_id" in result.stderr


def test_publish_refuses_dense_of_another_snapshot(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    dense_copy = tmp_path / "dense"
    make_dense_fixture(built, dense_copy)
    mpath = dense_copy / "dense-manifest.json"
    manifest = json.loads(mpath.read_bytes())
    manifest["snapshot"] = "2020-01-01"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    result = publish(built, fixture_data_root, "--dense", str(dense_copy))
    assert result.returncode == 1
    assert "dense/dense-manifest.json has snapshot" in result.stderr


def test_publish_refuses_a_damaged_dense_file(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    dense_copy = tmp_path / "dense"
    make_dense_fixture(built, dense_copy)
    vpath = dense_copy / "vectors.i8"
    vpath.write_bytes(vpath.read_bytes() + b"extra")
    result = publish(built, fixture_data_root, "--dense", str(dense_copy))
    assert result.returncode == 1
    assert "dense/vectors.i8: does not match dense-manifest.json" in result.stderr


def test_publish_refuses_missing_model(built: Path, fixture_data_root: Path) -> None:
    result = publish(built, fixture_data_root, "--dense-model", "nonexistent-model")
    assert result.returncode == 1
    assert "model 'nonexistent-model' not in models/models-manifest.json" in result.stderr


def test_publish_refuses_a_damaged_model_file(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    models_copy = tmp_path / "models"
    shutil.copytree(REPO / "artefacts" / "models", models_copy)
    target = models_copy / "bge-small-en-v1.5" / "config.json"
    target.write_bytes(target.read_bytes() + b"x")
    result = publish(built, fixture_data_root, "--models", str(models_copy))
    assert result.returncode == 1
    msg = "models/bge-small-en-v1.5/config.json: does not match models-manifest.json"
    assert msg in result.stderr


def test_publish_refuses_a_damaged_symspell_dictionary(
    built: Path, fixture_data_root: Path, built_symspell: Path, tmp_path: Path
) -> None:
    sym_copy = tmp_path / "sym"
    shutil.copytree(built_symspell, sym_copy)
    dpath = sym_copy / "dictionary.txt"
    dpath.write_bytes(dpath.read_bytes() + b"extra")
    result = publish(built, fixture_data_root, "--symspell", str(sym_copy))
    assert result.returncode == 1
    assert "symspell/dictionary.txt: does not match symspell-manifest.json" in result.stderr


def test_publish_needs_repo_with_yes(built: Path, fixture_data_root: Path) -> None:
    result = publish(built, fixture_data_root, "--yes")
    assert result.returncode == 2
    assert "--yes needs --repo" in result.stderr


def test_publish_refuses_a_damaged_store(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    copy = Path(shutil.copytree(built, tmp_path / "s"))
    (copy / TEXT_NAME).write_bytes(b"x" + (copy / TEXT_NAME).read_bytes()[1:])
    result = publish(copy, fixture_data_root, "--repo", "a/b", "--yes")
    assert result.returncode == 1
    assert "check failed: store: hash mismatch" in result.stderr


def test_publish_refuses_a_damaged_router_file(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    data = _copy_data(fixture_data_root, tmp_path)
    target = data / "concepts" / "terms.tbl"
    target.write_bytes(target.read_bytes() + b"x")
    result = publish(built, data, "--repo", "a/b", "--yes")
    assert result.returncode == 1
    assert "concepts/terms.tbl: does not match" in result.stderr


def test_publish_refuses_a_router_index_of_another_snapshot(
    built: Path, fixture_data_root: Path, tmp_path: Path
) -> None:
    data = _copy_data(fixture_data_root, tmp_path)
    path = data / "index" / "index-manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["snapshot"] = "2025-01-01"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    result = publish(built, data)
    assert result.returncode == 1
    assert "snapshot '2025-01-01'" in result.stderr
