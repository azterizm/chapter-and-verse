"""Shared fixtures. CI runs with the committed fixture corpus only (tests/fixtures/data/).

* ``fixture_data_root``: a data root shaped like the real one (``uk/``, ``index/``,
  ``concepts/``, ``catalogue/``), built by ``scripts/build_fixtures.py``.
* ``fixture_roles``: ``tests/fixtures/fixture.json``, what each fixture instrument is for.
* ``fixture_router``: a ``legal_rag_router.Router`` over the fixture index and concept index.
* ``fixture_text_store``: :class:`JsonlTextStore`, a test double of the ``TextStore`` protocol
  over the fixture records (the real store is agent B1's).
* ``real_data_root``: the real corpus at ``LEGAL_DATA_ROOT``; skips when unset. Tests that need
  it are marked ``@pytest.mark.data`` and are skipped when it is unset.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from legal_rag_router import Router
from legal_rag_verifier.corpus import instrument_path, load_provision

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_DATA = FIXTURES / "data"


class JsonlTextStore:
    """``TextStore`` over a data root of router JSONL records, via ``load_provision``."""

    def __init__(self, root: Path, *, corpus_id: str = "fixture", snapshot: str = "") -> None:
        self.root = root
        self.corpus_id = corpus_id
        self.snapshot = snapshot

    def get(self, coordinate: str) -> list[dict[str, Any]]:
        try:
            return load_provision(self.root, coordinate)
        except FileNotFoundError:
            raise KeyError(coordinate) from None

    def instrument(self, coordinate: str) -> dict[str, Any] | None:
        try:
            path = instrument_path(self.root, coordinate)
            with path.open(encoding="utf-8") as fh:
                row: dict[str, Any] = json.loads(fh.readline())
        except FileNotFoundError:
            return None
        return row

    def has_text(self, instrument_coordinate: str) -> bool:
        row = self.instrument(instrument_coordinate)
        if row is None:
            raise KeyError(instrument_coordinate)
        return row.get("structure") != "metadata_only"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    if os.environ.get("LEGAL_DATA_ROOT"):
        return
    skip = pytest.mark.skip(reason="needs the real corpus: set LEGAL_DATA_ROOT")
    for item in items:
        if "data" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def fixture_data_root() -> Path:
    return FIXTURE_DATA


@pytest.fixture(scope="session")
def fixture_roles() -> dict[str, Any]:
    roles: dict[str, Any] = json.loads((FIXTURES / "fixture.json").read_text(encoding="utf-8"))
    return roles


@pytest.fixture(scope="session")
def fixture_router(fixture_data_root: Path) -> Router:
    return Router.from_path(fixture_data_root / "index", concepts=fixture_data_root / "concepts")


@pytest.fixture(scope="session")
def fixture_text_store(fixture_data_root: Path, fixture_roles: dict[str, Any]) -> JsonlTextStore:
    return JsonlTextStore(fixture_data_root, snapshot=str(fixture_roles["snapshot"]))


@pytest.fixture(scope="session")
def real_data_root() -> Path:
    value = os.environ.get("LEGAL_DATA_ROOT")
    if not value:
        pytest.skip("needs the real corpus: set LEGAL_DATA_ROOT")
    root = Path(value).expanduser()
    if not (root / "uk").is_dir():
        pytest.skip(f"LEGAL_DATA_ROOT={root} has no uk/ directory")
    return root
