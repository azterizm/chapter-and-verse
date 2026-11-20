"""Build the text store from the router's JSONL records, or benchmark a built store.

    uv run python build/textstore.py build [--data ROOT] [--out DIR] [--level 19]
                                           [--blocks packed:16384] [--dict-size 112640]
    uv run python build/textstore.py bench --store DIR [--data ROOT] [--sample 2000]

``--data`` defaults to ``$LEGAL_DATA_ROOT``; it must hold ``uk/`` (the records),
``index/index-manifest.json`` (the snapshot date) and ``MANIFEST.json`` (source and licence).
The records are read only. Format: ``chapterverse.store.format``. See ``build/README.md``.

Block policies (``--blocks``); the instrument record line always gets its own frame except
under ``instrument``:

    instrument   one frame per source file
    toplevel     one frame per top-level provision and its descendants
    fixed:N      frames of whole lines, up to N bytes
    packed:N     whole top-level provisions packed up to N bytes; a larger one is split at
                 line ends
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import mmap
import os
import random
import re
import resource
import shutil
import statistics
import subprocess
import sys
import time
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from importlib.metadata import version
from multiprocessing import Pool
from multiprocessing.pool import Pool as PoolType
from pathlib import Path
from typing import Any, Final

import zstandard
from legal_rag_verifier.corpus import instrument_coordinate

from chapterverse.store.format import (
    DICT_NAME,
    FRAMES_NAME,
    MANIFEST_NAME,
    TABLE_STEM,
    TEXT_NAME,
    encode_instrument,
    encode_runs,
)
from chapterverse.store.manifest import make_manifest, manifest_bytes, source_entry

DEFAULT_LEVEL: Final = 19
DEFAULT_BLOCKS: Final = "packed:16384"
DEFAULT_DICT_SIZE: Final = 112640  # 110 KiB, zstd's default dictionary size
MIN_DICT_SIZE: Final = 512
DICT_SAMPLE_FACTOR: Final = 100
DICT_K: Final = 1024  # fixed cover parameters: training is deterministic and fast
DICT_D: Final = 8
DICT_SAMPLE_MAX: Final = 16384
_MAX_TABLE_BYTES: Final = (1 << 32) - 1
_CALENDAR_PARTS: Final = 4
_CONTROL: Final = re.compile(r"[\x00-\x1f]")


class BuildError(RuntimeError):
    """The source data breaks an assumption the store relies on."""


# ============================================================================ block policy


@dataclass(frozen=True, slots=True)
class Policy:
    kind: str  # "instrument" | "toplevel" | "fixed" | "packed"
    limit: int = 0

    @classmethod
    def parse(cls, text: str) -> Policy:
        kind, _, size = text.partition(":")
        if kind in ("instrument", "toplevel") and not size:
            return cls(kind)
        if kind in ("fixed", "packed") and size.isdigit() and int(size) > 0:
            return cls(kind, int(size))
        raise ValueError(f"bad block policy {text!r}")

    def __str__(self) -> str:
        return f"{self.kind}:{self.limit}" if self.limit else self.kind


def _pack(atoms: Sequence[int], limit: int) -> list[int]:
    """Greedy cut points over atom boundaries ``atoms`` (ascending): a frame grows until the
    next atom would take it past ``limit``; a single atom larger than ``limit`` stays whole."""
    cuts: list[int] = []
    frame_start = atoms[0]
    for start, end in itertools.pairwise(atoms):
        if end - frame_start > limit and start > frame_start:
            cuts.append(start)
            frame_start = start
    return cuts


def plan_cuts(policy: Policy, starts: Sequence[int], toplevel: Sequence[bool]) -> list[int]:
    """Frame boundaries for one file: ascending byte offsets from 0 to the file length.

    ``starts`` holds each line's start plus the file length; ``toplevel[i]`` is true when
    line ``i`` is a direct child of the instrument.
    """
    size = starts[-1]
    if policy.kind == "instrument" or len(starts) <= 2:  # noqa: PLR2004
        return [0, size]
    head = starts[1]
    lines = range(1, len(starts) - 1)
    unit_starts = [starts[i] for i in lines if i == 1 or toplevel[i]]
    units = [*unit_starts, size]
    if policy.kind == "toplevel":
        inner = unit_starts[1:]
    elif policy.kind == "fixed":
        inner = _pack([starts[i] for i in lines] + [size], policy.limit)
    else:
        atoms: list[int] = []
        for begin, end in itertools.pairwise(units):
            if end - begin <= policy.limit:
                atoms.append(begin)
            else:  # an oversized unit: its own lines are the atoms
                atoms.extend(starts[bisect_left(starts, begin) : bisect_left(starts, end)])
        atoms.append(size)
        inner = _pack(atoms, policy.limit)
    return [0, head, *inner, size]


# ============================================================================ phase 0 and A


@dataclass(frozen=True, slots=True)
class FileInfo:
    instrument: str
    relpath: str
    size: int
    head: int
    has_text: bool
    provisions: int
    cuts: tuple[int, ...]
    table: bytes  # sorted "key\tvalue\n" lines for the provisions
    table_offsets: bytes  # array("I") of line starts within ``table``
    last_key: str


def first_coordinate(path: str) -> tuple[str, str]:
    with open(path, "rb") as fh:  # noqa: PTH123
        line = fh.readline()
    try:
        return str(json.loads(line)["coordinate"]), path
    except (ValueError, KeyError, TypeError) as exc:
        raise BuildError(f"{path}: first line is not a record with a coordinate") from exc


def _expected_location(root: Path, instrument: str, path: Path) -> bool:
    """Whether ``load_provision`` resolves ``instrument`` to ``path`` (``instrument_path``,
    without its directory glob for regnal-year Acts)."""
    parts = instrument.split("/")
    name = "_".join(parts) + ".jsonl"
    rel = path.relative_to(root).parts
    if len(rel) != 4 or rel[3] != name or rel[:2] != tuple(parts[:2]):  # noqa: PLR2004
        return False
    return len(parts) != _CALENDAR_PARTS or rel[2] == parts[2]


def _parse(path: Path, root: Path) -> tuple[bytes, array[int], str, list[str], bool]:
    """Read and check one source file: ``(raw, line starts, instrument, provision
    coordinates, has_text)``. Raises :class:`BuildError` on anything the store cannot hold
    exactly as ``load_provision`` reads it."""
    raw = path.read_bytes()
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise BuildError(f"{path}: must end with a newline and hold no carriage return")
    starts = array("Q", [0])
    position = raw.find(b"\n")
    while position != -1:
        starts.append(position + 1)
        position = raw.find(b"\n", position + 1)
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n")[:-1]]
    first = rows[0]
    instrument = first.get("coordinate")
    if first.get("record_type") != "instrument" or not isinstance(instrument, str):
        raise BuildError(f"{path}: the first line is not an instrument record")
    if instrument_coordinate(instrument) != instrument:
        raise BuildError(f"{path}: {instrument!r} is not an instrument coordinate")
    if not _expected_location(root, instrument, path):
        raise BuildError(f"{path}: load_provision would not resolve {instrument} to this file")
    prefix = instrument + "/"
    coordinates: list[str] = []
    for row in rows[1:]:
        coordinate = row.get("coordinate")
        if row.get("record_type") != "provision" or not isinstance(coordinate, str):
            raise BuildError(f"{path}: a row after the first is not a provision record")
        if not coordinate.startswith(prefix) or _CONTROL.search(coordinate):
            raise BuildError(f"{path}: bad provision coordinate {coordinate!r}")
        coordinates.append(coordinate)
    if len(set(coordinates)) != len(coordinates):
        raise BuildError(f"{path}: duplicate provision coordinates")
    return raw, starts, instrument, coordinates, first.get("structure") != "metadata_only"


def subtree_runs(coordinates: Sequence[str], depth: int) -> dict[str, list[list[int]]]:
    """For each provision, the ``[first, end)`` line runs of itself and its descendants.
    Line 0 is the instrument record, so provision ``i`` of ``coordinates`` is line ``i + 1``."""
    existing = set(coordinates)
    runs: dict[str, list[list[int]]] = {}
    for line, coordinate in enumerate(coordinates, start=1):
        parts = coordinate.split("/")
        for cut in range(depth + 2, len(parts) + 1):
            ancestor = "/".join(parts[:cut])
            if ancestor not in existing:
                continue
            spans = runs.setdefault(ancestor, [])
            if spans and spans[-1][1] == line:
                spans[-1][1] = line + 1
            else:
                spans.append([line, line + 1])
    return runs


def analyse(args: tuple[str, str, Policy]) -> FileInfo:
    """Check one source file and compute its frame cuts and table lines."""
    path_text, root_text, policy = args
    path, root = Path(path_text), Path(root_text)
    raw, starts, instrument, coordinates, has_text = _parse(path, root)
    depth = instrument.count("/")
    runs = subtree_runs(coordinates, depth)
    table = bytearray()
    offsets = array("I")
    for coordinate in sorted(runs):
        offsets.append(len(table))
        value = encode_runs([(starts[a], starts[b]) for a, b in runs[coordinate]])
        table += f"{coordinate}\t{value}\n".encode()
    if sys.byteorder != "little":  # pragma: no cover - big-endian hosts only
        offsets.byteswap()
    toplevel = [False] + [c.count("/") == depth + 1 for c in coordinates]
    return FileInfo(
        instrument=instrument,
        relpath=path.relative_to(root).as_posix(),
        size=len(raw),
        head=starts[1],
        has_text=has_text,
        provisions=len(coordinates),
        cuts=tuple(plan_cuts(policy, starts, toplevel)),
        table=bytes(table),
        table_offsets=offsets.tobytes(),
        last_key=max(runs) if runs else instrument,
    )


# ============================================================================ phase B and C

_COMPRESSOR: zstandard.ZstdCompressor | None = None


def _init_compressor(level: int, dictionary: bytes | None) -> None:
    global _COMPRESSOR  # noqa: PLW0603
    dict_data = zstandard.ZstdCompressionDict(dictionary) if dictionary else None
    _COMPRESSOR = zstandard.ZstdCompressor(
        level=level, dict_data=dict_data, write_checksum=False, write_content_size=True
    )


def compress_file(args: tuple[str, tuple[int, ...]]) -> list[bytes]:
    path, cuts = args
    if _COMPRESSOR is None:
        raise RuntimeError("compressor not initialised")
    raw = Path(path).read_bytes()
    return [_COMPRESSOR.compress(raw[a:b]) for a, b in itertools.pairwise(cuts)]


def _sample_pieces(raw: bytes, cuts: Sequence[int]) -> Iterator[bytes]:
    """The frames of one file as training samples, each split at line ends into pieces of at
    most DICT_SAMPLE_MAX bytes (a longer single line stays whole). Under ``packed:16384`` the
    pieces are the frames themselves."""
    for begin, end in itertools.pairwise(cuts):
        start = begin
        while start < end:
            stop = min(end, start + DICT_SAMPLE_MAX)
            if stop < end:
                newline = raw.rfind(b"\n", start, stop)
                stop = newline + 1 if newline >= start else raw.index(b"\n", stop) + 1
            yield raw[start:stop]
            start = stop


def train_dictionary(files: Sequence[tuple[Path, FileInfo]], dict_size: int, level: int) -> bytes:
    """A zstd dictionary trained on whole frames from files picked by the SHA-256 of their
    coordinate, so the sample does not depend on file order or a random seed."""
    order = sorted(files, key=lambda f: hashlib.sha256(f[1].instrument.encode()).digest())
    budget = dict_size * DICT_SAMPLE_FACTOR
    samples: list[bytes | bytearray | memoryview] = []
    for path, info in order:
        raw = path.read_bytes()
        samples.extend(_sample_pieces(raw, info.cuts))
        budget -= len(raw)
        if budget <= 0:
            break
    trained = zstandard.train_dictionary(
        dict_size, samples, k=min(DICT_K, dict_size // 4), d=DICT_D, level=level, threads=1
    )
    return trained.as_bytes()


# ============================================================================ table writer


def _check_key_order(previous: bytes | None, key: bytes) -> bool:
    return previous is None or previous < key


def write_table(blocks: Iterable[tuple[bytes, bytes, int]], tbl: Path, off: Path) -> int:
    """Write ``SortedTable`` files from blocks of sorted lines (``lines``, ``offsets``,
    ``count``) given in key order. Returns the number of keys.

    Raises:
        BuildError: keys out of order across blocks, or the table passes 4 GiB.
    """
    position = 0
    count = 0
    with tbl.open("wb") as tbl_fh, off.open("wb") as off_fh:
        for lines, raw_offsets, n in blocks:
            local = array("I")
            local.frombytes(raw_offsets)
            if sys.byteorder != "little":  # pragma: no cover
                local.byteswap()
            shifted = array("I", (x + position for x in local))
            if sys.byteorder != "little":  # pragma: no cover
                shifted.byteswap()
            tbl_fh.write(lines)
            off_fh.write(shifted.tobytes())
            position += len(lines)
            count += n
            if position > _MAX_TABLE_BYTES:
                raise BuildError("coordinate table exceeds 4 GiB")
    return count


def _instrument_block(info: FileInfo, base: int) -> tuple[bytes, bytes, int]:
    value = encode_instrument(base, info.head, info.size, has_text=info.has_text)
    line = f"{info.instrument}\t{value}\n".encode()
    offsets = array("I", [0])
    rest = array("I")
    rest.frombytes(info.table_offsets)
    if sys.byteorder != "little":  # pragma: no cover
        rest.byteswap()
    offsets.extend(x + len(line) for x in rest)
    if sys.byteorder != "little":  # pragma: no cover
        offsets.byteswap()
    return line + info.table, offsets.tobytes(), 1 + len(rest)


def sorted_blocks(blocks: Sequence[tuple[bytes, bytes, int]]) -> list[tuple[bytes, bytes, int]]:
    """One block holding every line of ``blocks`` sorted by key (the slow, general path)."""
    lines = [line for lines, _, _ in blocks for line in lines.split(b"\n")[:-1]]
    lines.sort(key=lambda line: line.split(b"\t", 1)[0])
    offsets = array("I")
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    if sys.byteorder != "little":  # pragma: no cover
        offsets.byteswap()
    return [(b"".join(line + b"\n" for line in lines), offsets.tobytes(), len(lines))]


# ============================================================================ build


def _peak_rss_bytes(who: int) -> int:
    peak = resource.getrusage(who).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _source_files(root: Path) -> list[str]:
    return sorted(str(p) for p in (root / "uk").rglob("*.jsonl"))


@dataclass(frozen=True, slots=True)
class _Settings:
    data: Path
    policy: Policy
    level: int
    dict_size: int
    workers: int
    say: Callable[[str], None]


def _order_sources(pool: PoolType, paths: list[str]) -> list[str]:
    """Source files in instrument-coordinate order; two files for one instrument fail."""
    firsts = pool.map(first_coordinate, paths, chunksize=256)
    firsts.sort()
    for (a, pa), (b, pb) in itertools.pairwise(firsts):
        if a == b:
            raise BuildError(f"two files hold instrument {a}: {pa} and {pb}")
    return [p for _, p in firsts]


def _table_phase(
    pool: PoolType, ordered: list[str], cfg: _Settings, partial: Path
) -> tuple[list[FileInfo], int, int]:
    """Check every file and write the coordinate table: ``(infos, coordinates, bytes)``."""
    infos: list[FileInfo] = []
    blocks: list[tuple[bytes, bytes, int]] = []
    in_order = True
    previous: bytes | None = None
    base = 0
    tasks = ((p, str(cfg.data), cfg.policy) for p in ordered)
    for info in pool.imap(analyse, tasks, chunksize=64):
        blocks.append(_instrument_block(info, base))
        in_order = in_order and _check_key_order(previous, info.instrument.encode())
        previous = info.last_key.encode()
        infos.append(replace(info, table=b"", table_offsets=b""))
        base += info.size
    if not in_order:
        cfg.say("instrument key ranges overlap: sorting the whole table")
        blocks = sorted_blocks(blocks)
    tbl, off = partial / f"{TABLE_STEM}.tbl", partial / f"{TABLE_STEM}.off"
    return infos, write_table(blocks, tbl, off), base


def _compress_phase(
    ordered: list[str],
    infos: list[FileInfo],
    cfg: _Settings,
    partial: Path,
    dictionary: bytes | None,
) -> int:
    """Write ``text.zst`` and ``frames.u64``; returns the frame count."""
    ustart = array("Q", [0])
    cstart = array("Q", [0])
    init = (cfg.level, dictionary)
    with (
        (partial / TEXT_NAME).open("wb") as text_fh,
        Pool(cfg.workers, initializer=_init_compressor, initargs=init) as pool,
    ):
        jobs = ((p, i.cuts) for p, i in zip(ordered, infos, strict=True))
        results = pool.imap(compress_file, jobs, chunksize=16)
        for info, frames in zip(infos, results, strict=True):
            bounds = itertools.pairwise(info.cuts)
            for (begin, end), frame in zip(bounds, frames, strict=True):
                text_fh.write(frame)
                ustart.append(ustart[-1] + (end - begin))
                cstart.append(cstart[-1] + len(frame))
    if sys.byteorder != "little":  # pragma: no cover
        ustart.byteswap()
        cstart.byteswap()
    (partial / FRAMES_NAME).write_bytes(ustart.tobytes() + cstart.tobytes())
    return len(ustart) - 1


def _build_into(
    partial: Path, cfg: _Settings, snapshot: str, source: dict[str, Any]
) -> dict[str, Any]:
    paths = _source_files(cfg.data)
    if not paths:
        raise BuildError(f"no JSONL records under {cfg.data / 'uk'}")
    cfg.say(f"{len(paths)} source files")
    with Pool(cfg.workers) as pool:
        ordered = _order_sources(pool, paths)
        cfg.say("phase A: checking records and writing the coordinate table")
        infos, coordinates, source_bytes = _table_phase(pool, ordered, cfg, partial)
    cfg.say(f"{coordinates} coordinates")

    dictionary: bytes | None = None
    # A dictionary needs about DICT_SAMPLE_FACTOR times its size in samples; a smaller source
    # (the test fixture) gets a smaller dictionary, or none.
    dict_size = min(cfg.dict_size, source_bytes // DICT_SAMPLE_FACTOR)
    if dict_size >= MIN_DICT_SIZE:
        cfg.say(f"training a {dict_size}-byte dictionary")
        sources = [(Path(p), i) for p, i in zip(ordered, infos, strict=True)]
        dictionary = train_dictionary(sources, dict_size, cfg.level)
        (partial / DICT_NAME).write_bytes(dictionary)
    elif cfg.dict_size:
        cfg.say(f"source too small for a dictionary ({source_bytes} bytes): none trained")

    cfg.say(f"phase C: compressing ({cfg.policy}, level {cfg.level})")
    frames = _compress_phase(ordered, infos, cfg, partial, dictionary)

    files = [TEXT_NAME, FRAMES_NAME, f"{TABLE_STEM}.tbl", f"{TABLE_STEM}.off"]
    if dictionary is not None:
        files.append(DICT_NAME)
    counts = {
        "instruments": len(infos),
        "instruments_metadata_only": sum(1 for i in infos if not i.has_text),
        "provisions": sum(i.provisions for i in infos),
        "coordinates": coordinates,
        "frames": frames,
        "source_bytes": source_bytes,
    }
    manifest = make_manifest(
        snapshot=snapshot,
        source=source,
        counts=counts,
        compression={
            "codec": "zstd",
            "level": cfg.level,
            "blocks": str(cfg.policy),
            "dictionary": DICT_NAME if dictionary is not None else None,
            "dictionary_size": len(dictionary) if dictionary is not None else 0,
        },
        builder={
            "chapterverse": version("chapterverse"),
            "zstandard": zstandard.__version__,
            "zstd": ".".join(str(part) for part in zstandard.ZSTD_VERSION),
        },
        store_dir=partial,
        files=tuple(files),
    )
    (partial / MANIFEST_NAME).write_bytes(manifest_bytes(manifest))
    return manifest


def build(
    data: Path,
    out: Path,
    *,
    index: Path | None = None,
    level: int = DEFAULT_LEVEL,
    policy: Policy | None = None,
    dict_size: int = DEFAULT_DICT_SIZE,
    workers: int | None = None,
    log: bool = True,
) -> dict[str, Any]:
    """Build a store from ``data`` into ``out`` and return the manifest.

    The store is written to ``<out>.partial`` and renamed into place only when complete, so a
    failed build leaves any earlier store at ``out`` untouched.
    """
    started = time.perf_counter()

    def say(message: str) -> None:
        if log:
            print(f"[{time.perf_counter() - started:8.1f}s] {message}", file=sys.stderr)

    cfg = _Settings(
        data=data,
        policy=policy or Policy.parse(DEFAULT_BLOCKS),
        level=level,
        dict_size=dict_size,
        workers=workers or min(os.cpu_count() or 1, 10),
        say=say,
    )
    index = index or data / "index"
    snapshot = str(json.loads((index / "index-manifest.json").read_text("utf-8"))["snapshot"])
    source = source_entry(json.loads((data / "MANIFEST.json").read_text("utf-8")))
    if out.exists() and any(out.iterdir()) and not (out / MANIFEST_NAME).is_file():
        raise BuildError(f"{out} exists, is not empty and holds no store; refusing to replace")
    partial = out.with_name(out.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    try:
        manifest = _build_into(partial, cfg, snapshot, source)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    if out.exists():
        shutil.rmtree(out)
    partial.rename(out)
    counts = manifest["counts"]
    say(
        f"done: {counts['frames']} frames, {manifest['files'][TEXT_NAME]['size']} compressed "
        f"bytes, peak RSS parent {_peak_rss_bytes(resource.RUSAGE_SELF) >> 20} MiB, "
        f"largest worker {_peak_rss_bytes(resource.RUSAGE_CHILDREN) >> 20} MiB"
    )
    return manifest


# ============================================================================ bench


def _rss_bytes() -> int:
    """Current resident set size of this process."""
    statm = Path("/proc/self/statm")
    if statm.exists():
        return int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    out = subprocess.run(  # noqa: S603
        ["/bin/ps", "-o", "rss=", "-p", str(os.getpid())],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip()) * 1024


def _percentiles(samples: Sequence[float]) -> dict[str, float]:
    ordered = sorted(samples)
    cut = statistics.quantiles(ordered, n=100, method="inclusive")
    return {
        "n": len(ordered),
        "p50_us": round(cut[49] * 1e6, 1),
        "p99_us": round(cut[98] * 1e6, 1),
        "max_us": round(ordered[-1] * 1e6, 1),
    }


def _time_calls(fn: Any, args: Iterable[Any]) -> list[float]:
    timings = []
    for arg in args:
        t0 = time.perf_counter()
        fn(arg)
        timings.append(time.perf_counter() - t0)
    return timings


def sample_coordinates(store_dir: Path, n: int, seed: int) -> list[str]:
    """``n`` coordinates drawn uniformly from the store's coordinate table."""
    rng = random.Random(seed)  # noqa: S311 - sampling, not security
    tbl = store_dir / f"{TABLE_STEM}.tbl"
    offsets = array("I", (store_dir / f"{TABLE_STEM}.off").read_bytes())
    if sys.byteorder != "little":  # pragma: no cover
        offsets.byteswap()
    with tbl.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as data:
        picks = rng.sample(range(len(offsets)), min(n, len(offsets)))
        out = []
        for i in picks:
            start = offsets[i]
            out.append(data[start : data.find(b"\t", start)].decode())
    return out


def bench(store_dir: Path, data: Path | None, n: int, seed: int) -> dict[str, Any]:
    from legal_rag_verifier.corpus import load_provision  # noqa: PLC0415

    from chapterverse.store import CompressedTextStore  # noqa: PLC0415

    rss_before = _rss_bytes()
    t0 = time.perf_counter()
    store = CompressedTextStore(store_dir)
    load_s = time.perf_counter() - t0
    rss_loaded = _rss_bytes()
    report: dict[str, Any] = {
        "store": str(store_dir),
        "compression": dict(store.manifest["compression"]),
        "store_bytes": sum(f["size"] for f in store.manifest["files"].values()),
        "load_s": round(load_s, 3),
        "rss_before_load_mib": round(rss_before / 2**20, 1),
        "rss_after_load_mib": round(rss_loaded / 2**20, 1),
    }
    era = "uk/ukpga/1996/18"
    report["get_s124"] = _percentiles(_time_calls(store.get, [f"{era}/s124"] * 2000))
    report["get_era_whole"] = _percentiles(_time_calls(store.get, [era] * 50))
    largest = "uk/ukpga/2010/4"  # Corporation Tax Act 2010: the largest file, 12.4 MB
    if store.instrument(largest) is not None:
        report["get_largest_whole"] = _percentiles(_time_calls(store.get, [largest] * 20))
    sample = sample_coordinates(store_dir, n, seed)
    report["get_sample_cold"] = _percentiles(_time_calls(store.get, sample))
    report["get_sample_warm"] = _percentiles(_time_calls(store.get, sample))
    sections = [c for c in sample if c.count("/") == instrument_coordinate(c).count("/") + 1]
    report["get_sample_toplevel"] = _percentiles(_time_calls(store.get, sections))
    report["instrument_sample"] = _percentiles(_time_calls(store.instrument, sample))
    report["rss_after_queries_mib"] = round(_rss_bytes() / 2**20, 1)
    if data is not None:
        report["load_provision_s124"] = _percentiles(
            _time_calls(lambda c: load_provision(data, c), [f"{era}/s124"] * 200)
        )
    return report


# ============================================================================ CLI


def _policy_arg(text: str) -> Policy:
    try:
        return Policy.parse(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _data_root(value: str | None) -> Path:
    root = value or os.environ.get("LEGAL_DATA_ROOT")
    if not root:
        raise SystemExit("set --data or LEGAL_DATA_ROOT")
    return Path(root).expanduser()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build a store")
    b.add_argument("--data", help="data root (default $LEGAL_DATA_ROOT)")
    b.add_argument("--index", type=Path, help="router index (default <data>/index)")
    b.add_argument("--out", type=Path, default=Path("artefacts/store"))
    b.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    b.add_argument("--blocks", type=_policy_arg, default=Policy.parse(DEFAULT_BLOCKS))
    b.add_argument("--dict-size", type=int, default=DEFAULT_DICT_SIZE)
    b.add_argument("--workers", type=int)
    m = sub.add_parser("bench", help="measure load time, RSS and get() latency")
    m.add_argument("--store", type=Path, default=Path("artefacts/store"))
    m.add_argument("--data", help="also time load_provision on this data root")
    m.add_argument("--sample", type=int, default=2000)
    m.add_argument("--seed", type=int, default=20261119)
    args = parser.parse_args(argv)
    if args.command == "build":
        t0 = time.perf_counter()
        manifest = build(
            _data_root(args.data),
            args.out,
            index=args.index,
            level=args.level,
            policy=args.blocks,
            dict_size=args.dict_size,
            workers=args.workers,
        )
        sizes = {name: entry["size"] for name, entry in manifest["files"].items()}
        print(
            json.dumps(
                {
                    "out": str(args.out),
                    "wall_s": round(time.perf_counter() - t0, 1),
                    "peak_rss_parent_mib": _peak_rss_bytes(resource.RUSAGE_SELF) >> 20,
                    "peak_rss_worker_mib": _peak_rss_bytes(resource.RUSAGE_CHILDREN) >> 20,
                    "store_bytes": sum(sizes.values()),
                    "files": sizes,
                    "counts": manifest["counts"],
                    "corpus_id": hashlib.sha256(manifest_bytes(manifest)).hexdigest(),
                },
                indent=2,
            )
        )
        return 0
    data = _data_root(args.data) if args.data else None
    print(json.dumps(bench(args.store, data, args.sample, args.seed), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
