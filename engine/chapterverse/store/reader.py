"""The text-store reader: :class:`CompressedTextStore` implements ``TextStore`` over a store
directory written by ``build/textstore.py`` (format: :mod:`chapterverse.store.format`).

Opening a store checks the size and SHA-256 of every file against ``corpus-manifest.json``
and then memory-maps the files; a damaged store fails here, never at query time. After that a
lookup is two binary searches in the coordinate table, the decompression of the frames that
hold the instrument record line and the provision's lines, and one ``json.loads`` per row.
Rows are parsed with the standard library ``json``, as ``load_provision`` parses them, so the
dicts are equal to its output by construction.

In resident mode (``STORE_RESIDENT=1``), every frame is decompressed once into memory at open
and shared by all worker threads; queries then slice directly from memory with no decompression.

The store is immutable once open. The table and frame index are read-only maps, and each
thread gets its own zstd decompressor (in mapped mode), so one instance can serve every request
thread.
"""

from __future__ import annotations

import json
import logging
import mmap
import os
import re
import sys
import threading
from array import array
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import zstandard
from legal_rag_router.table import SortedTable
from legal_rag_verifier.corpus import instrument_coordinate

from chapterverse.store.format import (
    DICT_NAME,
    FRAMES_NAME,
    TABLE_STEM,
    TEXT_NAME,
    decode_instrument,
    decode_runs,
    is_instrument_value,
)
from chapterverse.store.manifest import StoreLoadError, corpus_id, read_manifest, verify_files

__all__ = [
    "DEFAULT_RESIDENT_MAX_BYTES",
    "CompressedTextStore",
    "clear_resident_cache",
    "open_store",
]

log = logging.getLogger(__name__)

DEFAULT_RESIDENT_MAX_BYTES: Final = 8 * (1 << 30)  # 8 GiB
_U64: Final = "Q"
_CONTROL: Final = re.compile(r"[\x00-\x1f]")
_CHUNK: Final = 1 << 22  # 4 MiB

_RESIDENT_CACHE: dict[str, bytes] = {}
_RESIDENT_LOCK = threading.Lock()


def clear_resident_cache() -> None:
    """Clear the process-level cache of resident decompressed store buffers."""
    with _RESIDENT_LOCK:
        _RESIDENT_CACHE.clear()


def _map(path: Path) -> mmap.mmap | bytes:
    if path.stat().st_size == 0:
        return b""
    with path.open("rb") as fh:
        return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)


def _u64_view(raw: mmap.mmap | bytes) -> Sequence[int]:
    if sys.byteorder == "little":
        return memoryview(raw).cast(_U64)
    values = array(_U64, bytes(raw))  # pragma: no cover - big-endian hosts only
    values.byteswap()  # pragma: no cover
    return values  # pragma: no cover


class CompressedTextStore:
    """Read-only, memory-mapped or resident, thread-safe text store. Use :func:`open_store`."""

    def __init__(
        self,
        directory: str | Path,
        *,
        resident: bool | None = None,
        max_resident_bytes: int | None = None,
    ) -> None:
        root = Path(directory)
        manifest = read_manifest(root)
        verify_files(root, manifest)
        self._root = root
        self._manifest: Mapping[str, Any] = MappingProxyType(manifest)
        self._corpus_id = corpus_id(manifest)
        self._snapshot = str(manifest["snapshot"])
        counts: Mapping[str, Any] = manifest.get("counts", {})

        self._table = SortedTable.open(root / f"{TABLE_STEM}.tbl", root / f"{TABLE_STEM}.off")
        self._text = _map(root / TEXT_NAME)
        self._text_view = memoryview(self._text)
        frames_raw = _map(root / FRAMES_NAME)
        index = _u64_view(frames_raw)
        width = len(index) // 2
        if len(index) % 2 or width < 1:
            raise StoreLoadError(f"{FRAMES_NAME} has a bad length")
        self._frames_raw = frames_raw
        self._ustart: Sequence[int] = index[:width]
        self._cstart: Sequence[int] = index[width:]
        self._frame_count = width - 1

        dictionary = None
        if manifest.get("compression", {}).get("dictionary"):
            dictionary = zstandard.ZstdCompressionDict((root / DICT_NAME).read_bytes())
        self._dictionary = dictionary
        self._local = threading.local()

        expected = {
            "frames": (self._frame_count, counts.get("frames")),
            "source_bytes": (self._ustart[-1], counts.get("source_bytes")),
            "compressed_bytes": (self._cstart[-1], len(self._text)),
            "coordinates": (len(self._table), counts.get("coordinates")),
        }
        for name, (found, wanted) in expected.items():
            if found != wanted:
                raise StoreLoadError(f"store inconsistent: {name} is {found}, expected {wanted}")

        if resident is None:
            resident = os.environ.get("STORE_RESIDENT", "").strip() == "1"

        if max_resident_bytes is None:
            max_bytes_env = os.environ.get("STORE_RESIDENT_MAX_BYTES", "").strip()
            max_resident_bytes = int(max_bytes_env) if max_bytes_env else DEFAULT_RESIDENT_MAX_BYTES

        source_bytes = self._ustart[-1]
        if resident and source_bytes > max_resident_bytes:
            log.warning(
                "decompressed store size %d bytes exceeds STORE_RESIDENT_MAX_BYTES "
                "(%d bytes); staying mapped",
                source_bytes,
                max_resident_bytes,
            )
            resident = False

        decompressed = None
        if resident:
            with _RESIDENT_LOCK:
                decompressed = _RESIDENT_CACHE.get(self._corpus_id)
                if decompressed is None:
                    decompressed = self._decompress_all(root)
                    _RESIDENT_CACHE[self._corpus_id] = decompressed
        self._decompressed = decompressed

        if self._frame_count and self._decompressed is None:
            # Decompress one frame on this thread: proves the dictionary loads, and builds
            # the shared decompression dictionary before any worker thread can race to.
            self._frame(0)

    # ------------------------------------------------------------------ TextStore

    @property
    def corpus_id(self) -> str:
        return self._corpus_id

    @property
    def snapshot(self) -> str:
        return self._snapshot

    def get(self, coordinate: str) -> list[dict[str, Any]]:
        found = self._locate(coordinate)
        if found is None:
            raise KeyError(coordinate)
        instrument, base, head, size, _ = found
        if coordinate == instrument:
            return self._rows(((base, base + size),))
        value = self._table.get(coordinate)
        if value is None or is_instrument_value(value):
            raise KeyError(coordinate)
        spans = [(base, base + head)]
        spans.extend((base + start, base + end) for start, end in decode_runs(value))
        return self._rows(spans)

    def instrument(self, coordinate: str) -> dict[str, Any] | None:
        found = self._locate(coordinate)
        if found is None:
            return None
        _, base, head, _, _ = found
        return self._rows(((base, base + head),))[0]

    def has_text(self, instrument_coordinate: str) -> bool:
        found = self._locate(instrument_coordinate)
        if found is None:
            raise KeyError(instrument_coordinate)
        return found[4]

    # ------------------------------------------------------------------ extras

    @property
    def is_resident(self) -> bool:
        """True when the store holds decompressed text resident in memory."""
        return self._decompressed is not None

    @property
    def manifest(self) -> Mapping[str, Any]:
        """The parsed ``corpus-manifest.json`` (read-only view)."""
        return self._manifest

    @property
    def directory(self) -> Path:
        return self._root

    def __len__(self) -> int:
        """Number of coordinates held (instruments and provisions)."""
        return len(self._table)

    def __contains__(self, coordinate: object) -> bool:
        if not isinstance(coordinate, str):
            return False
        found = self._locate(coordinate)
        if found is None:
            return False
        return coordinate == found[0] or self._table.get(coordinate) is not None

    def __repr__(self) -> str:
        return f"CompressedTextStore({str(self._root)!r}, corpus_id={self._corpus_id[:12]}...)"

    # ------------------------------------------------------------------ internals

    def _locate(self, coordinate: str) -> tuple[str, int, int, int, bool] | None:
        """``(instrument, base, head, size, has_text)`` of the instrument holding
        ``coordinate``, or ``None`` when the store does not hold it."""
        if not coordinate or _CONTROL.search(coordinate):
            return None
        instrument = instrument_coordinate(coordinate)
        value = self._table.get(instrument)
        if value is None or not is_instrument_value(value):
            return None
        base, head, size, has_text = decode_instrument(value)
        return instrument, base, head, size, has_text

    def _decompress_all(self, root: Path) -> bytes:
        if self._frame_count == 0:
            return b""
        dctx = zstandard.ZstdDecompressor(dict_data=self._dictionary)
        text_path = root / TEXT_NAME
        with text_path.open("rb") as fh:
            reader = dctx.stream_reader(fh, read_across_frames=True)
            chunks: list[bytes] = []
            while True:
                chunk = reader.read(_CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
            buf = b"".join(chunks)
        if len(buf) != self._ustart[-1]:
            raise StoreLoadError(
                f"store inconsistent: decompressed {len(buf)} bytes, expected {self._ustart[-1]}"
            )
        return buf

    def _decompressor(self) -> zstandard.ZstdDecompressor:
        dctx: zstandard.ZstdDecompressor | None = getattr(self._local, "dctx", None)
        if dctx is None:
            dctx = zstandard.ZstdDecompressor(dict_data=self._dictionary)
            self._local.dctx = dctx
        return dctx

    def _frame(self, index: int) -> bytes:
        begin, end = self._cstart[index], self._cstart[index + 1]
        return self._decompressor().decompress(self._text_view[begin:end])

    def _read(self, spans: Sequence[tuple[int, int]]) -> bytes:
        """The decompressed bytes of ``spans`` (stream offsets), joined in order."""
        if self._decompressed is not None:
            if len(spans) == 1:
                begin, end = spans[0]
                return self._decompressed[begin:end]
            return b"".join(self._decompressed[begin:end] for begin, end in spans)
        ustart = self._ustart
        blocks: dict[int, bytes] = {}
        parts: list[bytes | memoryview] = []
        for begin, end in spans:
            index = bisect_right(ustart, begin) - 1
            position = begin
            while position < end:
                frame_begin, frame_end = ustart[index], ustart[index + 1]
                block = blocks.get(index)
                if block is None:
                    block = blocks[index] = self._frame(index)
                stop = min(end, frame_end)
                parts.append(memoryview(block)[position - frame_begin : stop - frame_begin])
                position = stop
                index += 1
        return b"".join(parts)

    def _rows(self, spans: Sequence[tuple[int, int]]) -> list[dict[str, Any]]:
        # Decode line by line, as load_provision reads. Decoding the whole buffer to one str
        # first measured the same speed but left RSS 390 MiB higher after repeated
        # whole-instrument reads (memory the allocator kept, not a leak).
        lines = self._read(spans).split(b"\n")
        lines.pop()  # every span ends with a newline
        return [json.loads(line.decode("utf-8")) for line in lines]


def open_store(
    path: str | Path,
    *,
    resident: bool | None = None,
    max_resident_bytes: int | None = None,
) -> CompressedTextStore:
    """Open, verify and map the store at ``path``.

    Raises:
        StoreLoadError: the manifest is missing or of another format, or any file is missing
            or does not match its size and SHA-256.
    """
    return CompressedTextStore(path, resident=resident, max_resident_bytes=max_resident_bytes)
