"""The text-store reader (``chapterverse.store``) against the ``JsonlTextStore`` double, and,
with ``LEGAL_DATA_ROOT`` set, against ``load_provision`` on the real corpus."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import zstandard
from legal_rag_router.table import SortedTable, encode_table
from legal_rag_verifier.corpus import instrument_coordinate, load_provision

from chapterverse.contracts import TextStore
from chapterverse.store import (
    CompressedTextStore,
    StoreLoadError,
    clear_resident_cache,
    open_store,
)
from chapterverse.store.format import DICT_NAME, FRAMES_NAME, MANIFEST_NAME, TEXT_NAME
from chapterverse.store.reader import _map

REPO = Path(__file__).resolve().parents[1]
TEXTSTORE = REPO / "build" / "textstore.py"

# Store variants built from the fixture: the default policy, frames small enough that one
# provision spans several frames, one frame per file, and a trained dictionary.
VARIANTS: dict[str, list[str]] = {
    "default": ["--dict-size", "0"],
    "tiny-frames": ["--blocks", "fixed:600", "--dict-size", "0"],
    "per-instrument": ["--blocks", "instrument", "--dict-size", "0"],
    "dictionary": ["--blocks", "packed:4096", "--dict-size", "900"],
}


def build_store(data: Path, out: Path, *extra: str) -> Path:
    cmd = [sys.executable, str(TEXTSTORE), "build", "--data", str(data), "--out", str(out)]
    cmd += ["--workers", "1", *extra]
    subprocess.run(cmd, check=True, capture_output=True, cwd=REPO)  # noqa: S603
    return out


def fixture_coordinates(root: Path) -> list[str]:
    out: list[str] = []
    for path in sorted((root / "uk").rglob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            out.extend(json.loads(line)["coordinate"] for line in fh)
    return out


@pytest.fixture(scope="module")
def stores(tmp_path_factory: pytest.TempPathFactory, fixture_data_root: Path) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("stores")
    return {
        name: build_store(fixture_data_root, base / name, *extra)
        for name, extra in VARIANTS.items()
    }


@pytest.fixture(
    scope="module",
    params=[(v, r) for v in sorted(VARIANTS) for r in (False, True)],
    ids=[f"{v}-{'resident' if r else 'mapped'}" for v in sorted(VARIANTS) for r in (False, True)],
)
def store(request: pytest.FixtureRequest, stores: dict[str, Path]) -> CompressedTextStore:
    variant, resident = request.param
    return CompressedTextStore(stores[variant], resident=resident)


@pytest.fixture(scope="module")
def coordinates(fixture_data_root: Path) -> list[str]:
    return fixture_coordinates(fixture_data_root)


# ---------------------------------------------------------------------------- equivalence


def test_open_store_returns_a_text_store(stores: dict[str, Path]) -> None:
    opened = open_store(stores["default"])
    assert isinstance(opened, TextStore)
    assert isinstance(opened, CompressedTextStore)
    assert opened.is_resident is False

    opened_res = open_store(stores["default"], resident=True)
    assert isinstance(opened_res, TextStore)
    assert isinstance(opened_res, CompressedTextStore)
    assert opened_res.is_resident is True


def test_get_equals_the_double_for_every_fixture_coordinate(
    store: CompressedTextStore, fixture_text_store: TextStore, coordinates: list[str]
) -> None:
    assert len(coordinates) == len(store)
    for coordinate in coordinates:
        got, want = store.get(coordinate), fixture_text_store.get(coordinate)
        assert got == want, coordinate
        assert repr(got) == repr(want), coordinate  # same key order and value types


def test_instrument_and_has_text_equal_the_double(
    store: CompressedTextStore, fixture_text_store: TextStore, coordinates: list[str]
) -> None:
    for coordinate in coordinates:
        assert store.instrument(coordinate) == fixture_text_store.instrument(coordinate)
        assert coordinate in store
    for coordinate in {instrument_coordinate(c) for c in coordinates}:
        assert store.has_text(coordinate) is fixture_text_store.has_text(coordinate)


def test_title_only_instrument(store: CompressedTextStore, fixture_roles: dict[str, Any]) -> None:
    title_only = fixture_roles["title_only"]["instrument"]
    assert title_only == "uk/ukpga/1980/15"
    rows = store.get(title_only)
    assert len(rows) == 1
    assert rows[0]["structure"] == "metadata_only"
    assert rows[0]["provision_count"] == 0
    assert store.instrument(title_only) == rows[0]
    assert store.has_text(title_only) is False
    assert store.has_text("uk/ukpga/1996/18") is True


def test_era_s124_rows(store: CompressedTextStore) -> None:
    rows = store.get("uk/ukpga/1996/18/s124")
    assert rows[0]["record_type"] == "instrument"
    assert rows[1]["coordinate"] == "uk/ukpga/1996/18/s124"
    assert all(r["coordinate"].startswith("uk/ukpga/1996/18/s124/") for r in rows[2:])
    assert not any(r["coordinate"].startswith("uk/ukpga/1996/18/s124A") for r in rows)


@pytest.mark.parametrize(
    "coordinate",
    [
        "uk/ukpga/1996/18/s99",  # not in the fixture (real ERA has it)
        "uk/ukpga/1996/18/s124/",
        "uk/ukpga/1996/18/S124",
        "uk/ukpga/1996/18/s124/1ZZ",
        "uk/ukpga/1996/999",
        "uk/ukpga/1996/999/s1",
        "uk/ukpga/Vict/24-25/100",
        "uk/ukpga/Vict/24-25/100/s1",
    ],
)
def test_unknown_coordinates_raise_key_error_like_the_double(
    store: CompressedTextStore, fixture_text_store: TextStore, coordinate: str
) -> None:
    with pytest.raises(KeyError):
        fixture_text_store.get(coordinate)
    with pytest.raises(KeyError):
        store.get(coordinate)
    assert coordinate not in store


@pytest.mark.parametrize(
    "coordinate",
    ["", "uk", "uk/ukpga", "uk/ukpga/1996", "uk/UKPGA/1996/18", "uk/ukpga/1996/18\t@0", "\n"],
)
def test_malformed_or_unknown_instrument(store: CompressedTextStore, coordinate: str) -> None:
    with pytest.raises(KeyError):
        store.get(coordinate)
    assert store.instrument(coordinate) is None
    with pytest.raises(KeyError):
        store.has_text(coordinate)


def test_unknown_instrument_is_none_and_has_text_raises(store: CompressedTextStore) -> None:
    assert store.instrument("uk/ukpga/1996/999") is None
    with pytest.raises(KeyError):
        store.has_text("uk/ukpga/1996/999")
    # instrument() answers for any coordinate inside a held instrument, as the double does.
    assert store.instrument("uk/ukpga/1996/18/s99") == store.instrument("uk/ukpga/1996/18")


def test_get_returns_fresh_rows(store: CompressedTextStore) -> None:
    first = store.get("uk/ukpga/1996/18/s124")
    first[0]["title"] = "changed"
    first.clear()
    assert store.get("uk/ukpga/1996/18/s124")[0]["title"] == "Employment Rights Act 1996"


def test_identity(stores: dict[str, Path], fixture_roles: dict[str, Any]) -> None:
    path = stores["default"]
    opened = CompressedTextStore(path)
    raw = (path / MANIFEST_NAME).read_bytes()
    assert opened.corpus_id == hashlib.sha256(raw).hexdigest()
    assert opened.snapshot == fixture_roles["snapshot"] == "2026-09-28"
    assert opened.manifest["source"]["licence"] == "OGL-3.0"
    # Different compression settings are a different build, so a different corpus_id.
    assert CompressedTextStore(stores["tiny-frames"]).corpus_id != opened.corpus_id


def test_many_threads_get_the_same_rows(store: CompressedTextStore, coordinates: list[str]) -> None:
    expected = {c: store.get(c) for c in coordinates}
    work = coordinates * 8
    random.Random(7).shuffle(work)
    barrier = threading.Barrier(8)

    def run(chunk: list[str]) -> bool:
        barrier.wait()
        return all(store.get(c) == expected[c] for c in chunk)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, [work[i::8] for i in range(8)]))
    assert all(results)


# ---------------------------------------------------------------------------- resident mode


def test_env_store_resident_selects_mode(
    stores: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = stores["default"]

    monkeypatch.delenv("STORE_RESIDENT", raising=False)
    s_default = open_store(path)
    assert s_default.is_resident is False

    monkeypatch.setenv("STORE_RESIDENT", "0")
    s_off = open_store(path)
    assert s_off.is_resident is False

    monkeypatch.setenv("STORE_RESIDENT", "1")
    s_on = open_store(path)
    assert s_on.is_resident is True


def test_store_resident_max_bytes_exceeded_stays_mapped(
    stores: dict[str, Path], caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = stores["default"]
    monkeypatch.setenv("STORE_RESIDENT", "1")
    monkeypatch.setenv("STORE_RESIDENT_MAX_BYTES", "100")

    with caplog.at_level(logging.WARNING):
        s = open_store(path)

    assert s.is_resident is False
    assert "exceeds STORE_RESIDENT_MAX_BYTES" in caplog.text
    assert "staying mapped" in caplog.text
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
    # Equivalence still holds via mapped fallback
    assert s.get("uk/ukpga/1996/18/s124")[0]["record_type"] == "instrument"


def test_store_resident_max_bytes_allows_resident(
    stores: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = stores["default"]
    monkeypatch.setenv("STORE_RESIDENT", "1")
    monkeypatch.setenv("STORE_RESIDENT_MAX_BYTES", str(10 * 1024 * 1024))
    s = open_store(path)
    assert s.is_resident is True


def test_resident_cache_shared_across_instances(stores: dict[str, Path]) -> None:
    clear_resident_cache()
    path = stores["default"]
    s1 = open_store(path, resident=True)
    s2 = open_store(path, resident=True)
    assert s1.is_resident is True
    assert s2.is_resident is True
    assert s1._decompressed is s2._decompressed
    clear_resident_cache()


def test_resident_and_mapped_give_byte_identical_results(
    stores: dict[str, Path], coordinates: list[str]
) -> None:
    path = stores["default"]
    mapped = CompressedTextStore(path, resident=False)
    resident = CompressedTextStore(path, resident=True)
    assert mapped.is_resident is False
    assert resident.is_resident is True

    for c in coordinates:
        assert mapped.get(c) == resident.get(c)
        assert repr(mapped.get(c)) == repr(resident.get(c))
        assert mapped.instrument(c) == resident.instrument(c)
        assert mapped.has_text(instrument_coordinate(c)) is resident.has_text(
            instrument_coordinate(c)
        )


def _single_stream_decompress(store: CompressedTextStore) -> bytes:
    dctx = zstandard.ZstdDecompressor(dict_data=store._dictionary)
    text_path = store.directory / TEXT_NAME
    with text_path.open("rb") as fh:
        reader = dctx.stream_reader(fh, read_across_frames=True)
        chunks: list[bytes] = []
        while True:
            chunk = reader.read(4 * 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)


@pytest.mark.parametrize("k", [1, 2, 3, 7])
def test_parallel_decompression_identity_with_threads(
    stores: dict[str, Path],
    fixture_data_root: Path,
    coordinates: list[str],
    monkeypatch: pytest.MonkeyPatch,
    k: int,
) -> None:
    monkeypatch.setenv("STORE_DECOMPRESS_THREADS", str(k))
    clear_resident_cache()
    for variant in ("default", "dictionary"):
        path = stores[variant]
        mapped = CompressedTextStore(path, resident=False)
        ref_buf = _single_stream_decompress(mapped)
        ref_sha256 = hashlib.sha256(ref_buf).hexdigest()

        resident = open_store(path, resident=True)
        assert resident.is_resident is True
        assert resident._decompressed is not None
        assert isinstance(resident._decompressed, (bytearray, bytes))
        assert hashlib.sha256(resident._decompressed).hexdigest() == ref_sha256

        for coordinate in coordinates:
            got = resident.get(coordinate)
            want = load_provision(fixture_data_root, coordinate)
            assert got == want
            found = resident._locate(coordinate)
            if found is not None:
                _, base, head, _, _ = found
                raw_bytes = resident._read([(base, base + head)])
                assert isinstance(raw_bytes, bytes)
                assert raw_bytes == ref_buf[base : base + head]
        clear_resident_cache()


def test_truncated_text_zst_raises_store_load_error(
    stores: dict[str, Path], tmp_path: Path
) -> None:
    path = _copy(stores["default"], tmp_path / "trunc")
    raw = (path / TEXT_NAME).read_bytes()
    (path / TEXT_NAME).write_bytes(raw[: len(raw) // 2])
    with pytest.raises(StoreLoadError):
        open_store(path, resident=True)


def test_corrupted_text_zst_raises_store_load_error(
    stores: dict[str, Path], tmp_path: Path
) -> None:
    path = _copy(stores["default"], tmp_path / "corrupt")
    (path / TEXT_NAME).write_bytes(b"\xff\x00" * 500)
    with pytest.raises(StoreLoadError):
        open_store(path, resident=True)


def test_decompress_all_direct_corruption_raises_store_load_error(
    stores: dict[str, Path], tmp_path: Path
) -> None:
    path = _copy(stores["default"], tmp_path / "direct_corrupt")
    store = CompressedTextStore(path, resident=False)
    target = path / TEXT_NAME
    raw = bytearray(target.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    target.write_bytes(bytes(raw))
    bad_store = CompressedTextStore.__new__(CompressedTextStore)
    bad_store._root = path
    bad_store._frame_count = store._frame_count
    bad_store._ustart = store._ustart
    bad_store._cstart = store._cstart
    bad_store._dictionary = store._dictionary
    bad_store._text = _map(target)
    bad_store._text_view = memoryview(bad_store._text)
    with pytest.raises(StoreLoadError):
        bad_store._decompress_all(path)


# ---------------------------------------------------------------------------- format


def test_table_is_the_routers_sorted_table_format(stores: dict[str, Path]) -> None:
    path = stores["default"]
    tbl, off = path / "coordinates.tbl", path / "coordinates.off"
    items = list(SortedTable.open(tbl, off).items())
    assert encode_table(items) == (tbl.read_bytes(), off.read_bytes())


def _frames(path: Path) -> Iterator[bytes]:
    manifest = json.loads((path / MANIFEST_NAME).read_bytes())
    raw = (path / FRAMES_NAME).read_bytes()
    values = memoryview(raw).cast("Q")
    width = len(values) // 2
    cstart = values[width:]
    text = (path / TEXT_NAME).read_bytes()
    dict_data = None
    if manifest["compression"]["dictionary"]:
        dict_data = zstandard.ZstdCompressionDict((path / DICT_NAME).read_bytes())
    dctx = zstandard.ZstdDecompressor(dict_data=dict_data)
    for i in range(width - 1):
        yield dctx.decompress(text[cstart[i] : cstart[i + 1]])


def test_frames_hold_the_source_files_byte_for_byte(
    stores: dict[str, Path], fixture_data_root: Path
) -> None:
    files = sorted(
        (json.loads(p.read_bytes().split(b"\n", 1)[0])["coordinate"], p.read_bytes())
        for p in (fixture_data_root / "uk").rglob("*.jsonl")
    )
    source = b"".join(raw for _, raw in files)
    for path in stores.values():
        assert b"".join(_frames(path)) == source


# ---------------------------------------------------------------------------- load checks


def _copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def _flip(path: Path, position: int) -> None:
    data = bytearray(path.read_bytes())
    data[position % len(data)] ^= 0x01
    path.write_bytes(bytes(data))


DATA_FILES = [TEXT_NAME, FRAMES_NAME, "coordinates.tbl", "coordinates.off"]


@pytest.mark.parametrize("name", [*DATA_FILES, DICT_NAME])
@pytest.mark.parametrize("where", [0.0, 0.5, 1.0])
def test_one_flipped_byte_fails_at_load(
    stores: dict[str, Path], tmp_path: Path, name: str, where: float
) -> None:
    path = _copy(stores["dictionary"], tmp_path / "s")
    target = path / name
    _flip(target, round((target.stat().st_size - 1) * where))  # first, middle or last byte
    with pytest.raises(StoreLoadError, match="hash mismatch"):
        open_store(path)


def test_flipped_byte_in_a_manifest_hash_fails(stores: dict[str, Path], tmp_path: Path) -> None:
    path = _copy(stores["default"], tmp_path / "s")
    manifest = path / MANIFEST_NAME
    raw = manifest.read_bytes()
    digest = json.loads(raw)["files"][TEXT_NAME]["sha256"].encode()
    position = raw.index(digest) + 10
    manifest.write_bytes(raw[:position] + bytes([raw[position] ^ 0x01]) + raw[position + 1 :])
    with pytest.raises(StoreLoadError, match=f"hash mismatch for {TEXT_NAME}"):
        open_store(path)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda p: (p / TEXT_NAME).unlink(), "missing store file"),
        (lambda p: (p / MANIFEST_NAME).unlink(), "no corpus-manifest.json"),
        (lambda p: (p / MANIFEST_NAME).write_bytes(b"{not json"), "not valid JSON"),
        (lambda p: (p / TEXT_NAME).write_bytes((p / TEXT_NAME).read_bytes() + b"\0"), "size"),
    ],
)
def test_damaged_store_fails_at_load(
    stores: dict[str, Path], tmp_path: Path, damage: Callable[[Path], object], message: str
) -> None:
    path = _copy(stores["default"], tmp_path / "s")
    damage(path)
    with pytest.raises(StoreLoadError, match=message):
        open_store(path)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("format_version", 2, "unsupported store format version 2"),
        ("kind", "something-else", "does not describe"),
        ("files", None, "lists no files"),
    ],
)
def test_manifest_shape_is_checked(
    stores: dict[str, Path], tmp_path: Path, key: str, value: object, message: str
) -> None:
    path = _copy(stores["default"], tmp_path / "s")
    manifest = json.loads((path / MANIFEST_NAME).read_bytes())
    manifest[key] = value
    (path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(StoreLoadError, match=message):
        open_store(path)


def test_counts_must_match_the_files(stores: dict[str, Path], tmp_path: Path) -> None:
    path = _copy(stores["default"], tmp_path / "s")
    manifest = json.loads((path / MANIFEST_NAME).read_bytes())
    manifest["counts"]["coordinates"] += 1
    (path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(StoreLoadError, match="coordinates"):
        open_store(path)


# ---------------------------------------------------------------------------- real corpus

SERIES_SAMPLE = 200
SEED = 20261119


def _stratum(path: Path) -> str:
    parts = path.stem.split("_")
    series = parts[1]
    return f"{series}-regnal" if not parts[2].isdigit() else series


def _noncontiguous(coords: list[str], instrument: str) -> list[str]:
    """Coordinates whose rows (self and descendants) do not form one run of lines."""
    existing = set(coords)
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    count: dict[str, int] = {}
    depth = instrument.count("/")
    for i, c in enumerate(coords):
        parts = c.split("/")
        for cut in range(depth + 2, len(parts) + 1):
            q = "/".join(parts[:cut])
            if q in existing:
                first.setdefault(q, i)
                last[q] = i
                count[q] = count.get(q, 0) + 1
    return sorted(q for q in count if last[q] - first[q] + 1 != count[q])


def _real_store() -> CompressedTextStore:
    path = Path(os.environ.get("STORE_DIR", "artefacts/store"))
    path = path if path.is_absolute() else REPO / path
    if not (path / MANIFEST_NAME).is_file():
        pytest.skip(f"no store at {path}: run build/textstore.py build first")
    return CompressedTextStore(path)


@pytest.mark.data
def test_real_store_equals_load_provision_on_a_seeded_sample(real_data_root: Path) -> None:
    store = _real_store()
    rng = random.Random(SEED)
    files = sorted((real_data_root / "uk").rglob("*.jsonl"))
    strata: dict[str, list[Path]] = {}
    for path in files:
        strata.setdefault(_stratum(path), []).append(path)
    assert set(strata) >= {"ukpga", "ukpga-regnal", "uksi", "wsi", "nisi"}

    sample: dict[str, str] = {
        "uk/ukpga/1996/18/s124": "ukpga",
        "uk/ukpga/1996/18": "ukpga",
        "uk/ukpga/1980/15": "ukpga",
        "uk/ukpga/2010/4/s1": "ukpga",
    }
    noncontiguous: set[str] = set()
    for name in sorted(strata):
        for path in rng.sample(strata[name], min(SERIES_SAMPLE, len(strata[name]))):
            with path.open(encoding="utf-8") as fh:
                coords = [json.loads(line)["coordinate"] for line in fh]
            instrument, provisions = coords[0], coords[1:]
            picks = [instrument, *rng.sample(provisions, min(3, len(provisions)))]
            top = [c for c in provisions if c.count("/") == instrument.count("/") + 1]
            picks += rng.sample(top, min(1, len(top)))
            split = _noncontiguous(provisions, instrument)[:3]
            noncontiguous.update(split)
            picks += split
            for c in picks:
                sample.setdefault(c, name)
    assert len(sample) >= 2000
    assert noncontiguous  # Parts whose chapters interleave with other rows

    mismatches = []
    kinds: dict[str, int] = {}
    for coordinate, name in sample.items():
        got = store.get(coordinate)
        want = load_provision(real_data_root, coordinate)
        if got != want or repr(got) != repr(want):
            mismatches.append(coordinate)
        kind = "instrument" if coordinate == instrument_coordinate(coordinate) else "provision"
        kinds[f"{name}/{kind}"] = kinds.get(f"{name}/{kind}", 0) + 1
    misses = [c + "/zz9" for c in list(sample)[:: max(1, len(sample) // 200)]]
    for coordinate in misses:
        with pytest.raises(KeyError):
            store.get(coordinate)
        with pytest.raises(KeyError):
            load_provision(real_data_root, coordinate)
    print(  # noqa: T201 - the numbers go in the report
        json.dumps(
            {
                "checked": len(sample),
                "mismatches": len(mismatches),
                "misses": len(misses),
                "noncontiguous": len(noncontiguous),
                **kinds,
            }
        )
    )
    assert mismatches == []


@pytest.mark.data
def test_real_store_holds_every_instrument(real_data_root: Path) -> None:
    store = _real_store()
    files = sorted((real_data_root / "uk").rglob("*.jsonl"))
    assert store.manifest["counts"]["instruments"] == len(files)
    router = json.loads((real_data_root / "index" / "index-manifest.json").read_bytes())
    assert store.snapshot == router["snapshot"]
    assert len(store) == router["counts"]["coordinates"]
    bad = []
    for path in files:
        with path.open("rb") as fh:
            record = json.loads(fh.readline())
        coordinate = record["coordinate"]
        if store.instrument(coordinate) != record or store.has_text(coordinate) is not (
            record["structure"] != "metadata_only"
        ):
            bad.append(coordinate)
    assert bad == []
