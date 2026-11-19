"""Text-store reader: provision rows by coordinate (agent B1).

``open_store(path)`` opens a store directory built by ``build/textstore.py``, checks every file
against ``corpus-manifest.json`` and returns a ``TextStore``. The format is described in
:mod:`chapterverse.store.format`.
"""

from chapterverse.store.format import FORMAT_VERSION, MANIFEST_NAME
from chapterverse.store.manifest import StoreLoadError
from chapterverse.store.reader import CompressedTextStore, open_store

__all__ = [
    "FORMAT_VERSION",
    "MANIFEST_NAME",
    "CompressedTextStore",
    "StoreLoadError",
    "open_store",
]
