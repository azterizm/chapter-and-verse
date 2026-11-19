# Chapter & Verse

A free public web app that answers questions about UK legislation. Every sentence of an answer is checked against the provision text before it is shown. Citations are rendered by the system, not written by the model. When the text does not support an answer, it refuses.

Status: Slice 1 in progress. Nothing is deployed yet.

| Part | Where |
|---|---|
| Engine (Python package `chapterverse`) | `engine/` |
| Write path | `build/` |
| Frontend | `web/` |
| Architecture and contracts | `docs/ARCHITECTURE.md` |

Routing uses [legal-rag-router](https://pypi.org/project/legal-rag-router/) and verification uses [legal-rag-verifier](https://pypi.org/project/legal-rag-verifier/).

## Development

```
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Tests run against a small fixture corpus in `tests/fixtures/`. Tests marked `data` need the full corpus and run only when `LEGAL_DATA_ROOT` is set.

## Licence

AGPL-3.0-only. See `LICENSE`. Legislation data is Crown copyright under the Open Government Licence v3.0. See `NOTICE`.
