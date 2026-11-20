"""On-disk format of the text store, version 1. Shared by the writer (``build/textstore.py``)
and the reader (:mod:`chapterverse.store.reader`).

A store is a directory of five or six files:

=======================  ===================================================================
``corpus-manifest.json`` Format version, snapshot, source and licence, counts, and the size
                         and SHA-256 of every other file. Canonical JSON; its SHA-256 is the
                         ``corpus_id``
``text.zst``             Independent zstd frames, one after another. Decompressed and joined
                         in order, they are every source JSONL file byte for byte, files in
                         instrument-coordinate order
``frames.u64``           Little-endian uint64, two arrays of ``frames + 1`` entries: the start
                         of each frame in the decompressed stream, then in ``text.zst``. The
                         last entry of each is the total length
``coordinates.tbl/.off`` The router's ``SortedTable`` format, keyed by exact coordinate
``dictionary.zdict``     The zstd dictionary every frame was compressed with (only when the
                         manifest names one)
=======================  ===================================================================

Table values:

* instrument coordinate: ``@{base},{head},{size},{t|m}``. The file starts at ``base`` in the
  decompressed stream; its first ``head`` bytes are the instrument record line (newline
  included); ``size`` is the file length; ``t`` means text is held, ``m`` that the record says
  ``structure: "metadata_only"``.
* provision coordinate: ``{start},{length}`` runs joined by ``;``, offsets relative to the
  file start. The runs are the lines of the provision and all its descendants, in file order.
  Most provisions need one run; a Part whose chapters are interleaved with other rows needs
  several.

Frames never span two files and always end at a line end.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = [
    "DICT_NAME",
    "FORMAT_KIND",
    "FORMAT_VERSION",
    "FRAMES_NAME",
    "MANIFEST_NAME",
    "TABLE_STEM",
    "TEXT_NAME",
    "decode_instrument",
    "decode_runs",
    "encode_instrument",
    "encode_runs",
    "is_instrument_value",
]

FORMAT_VERSION: Final = 1
FORMAT_KIND: Final = "chapterverse-text-store"
MANIFEST_NAME: Final = "corpus-manifest.json"
TEXT_NAME: Final = "text.zst"
FRAMES_NAME: Final = "frames.u64"
TABLE_STEM: Final = "coordinates"
DICT_NAME: Final = "dictionary.zdict"

_INSTRUMENT_MARK: Final = "@"
_HAS_TEXT: Final = "t"
_METADATA_ONLY: Final = "m"


def encode_instrument(base: int, head: int, size: int, *, has_text: bool) -> str:
    """Table value of an instrument coordinate."""
    flag = _HAS_TEXT if has_text else _METADATA_ONLY
    return f"{_INSTRUMENT_MARK}{base},{head},{size},{flag}"


def is_instrument_value(value: str) -> bool:
    return value.startswith(_INSTRUMENT_MARK)


def decode_instrument(value: str) -> tuple[int, int, int, bool]:
    """``(base, head, size, has_text)`` from an instrument table value.

    Raises:
        ValueError: the value is not an instrument value.
    """
    if not value.startswith(_INSTRUMENT_MARK):
        raise ValueError(f"not an instrument value: {value!r}")
    base, head, size, flag = value[1:].split(",")
    if flag not in (_HAS_TEXT, _METADATA_ONLY):
        raise ValueError(f"bad instrument flag: {value!r}")
    return int(base), int(head), int(size), flag == _HAS_TEXT


def encode_runs(runs: Sequence[tuple[int, int]]) -> str:
    """Table value of a provision coordinate: ``(start, end)`` byte runs, end exclusive."""
    if not runs:
        raise ValueError("a provision needs at least one run")
    return ";".join(f"{start},{end - start}" for start, end in runs)


def decode_runs(value: str) -> list[tuple[int, int]]:
    """``(start, end)`` runs, end exclusive, relative to the file start."""
    runs: list[tuple[int, int]] = []
    for run in value.split(";"):
        start, length = run.split(",")
        begin = int(start)
        runs.append((begin, begin + int(length)))
    return runs
