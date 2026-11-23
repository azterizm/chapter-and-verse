"""Modal dense embedding pipeline (requirement R2).

Builds document embeddings on Modal L4 GPU containers and writes memmapped .npy arrays
preserving document order.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Final, Literal, cast

import numpy as np

try:
    import modal  # type: ignore[import-not-found]

    HAS_MODAL = True
except ImportError:
    modal = None
    HAS_MODAL = False

__all__ = [
    "APP_NAME",
    "HAS_MODAL",
    "Embedder",
    "iter_shards",
    "main",
    "run_dense_embed",
]

APP_NAME: Final = "cv-embed"
DEFAULT_MODELS: Final = ("bge-small-en-v1.5", "e5-small-v2", "gte-small")
DEFAULT_DOCS_PER_SECOND_PER_CONTAINER: Final = 1500.0


def iter_shards(
    doc_ids: Iterable[int],
    store: Any,
    concepts: Any,
    shard_size: int,
) -> Iterator[list[str]]:
    """Lazily generate document text shards for embedding."""
    from chapterverse.dense.doctext import doc_text  # noqa: PLC0415

    shard: list[str] = []
    for doc_id in doc_ids:
        shard.append(doc_text(doc_id, store, concepts))
        if len(shard) >= shard_size:
            yield shard
            shard = []
    if shard:
        yield shard


def _resolve_target_ids(
    doc_ids: str,
    limit: int,
    concepts: Any,
) -> tuple[list[int], Path | None]:
    if doc_ids:
        path_obj = Path(doc_ids).expanduser()
        if not path_obj.is_file():
            raise FileNotFoundError(f"doc_ids file not found: {path_obj}")
        raw_ids: list[int] = json.loads(path_obj.read_text(encoding="utf-8"))
        target_ids = raw_ids[:limit] if limit > 0 else raw_ids
        return target_ids, path_obj

    total_docs = concepts.doc_count if hasattr(concepts, "doc_count") else len(concepts.doc_table)
    count_target = limit if limit > 0 else total_docs
    return list(range(count_target)), None


def _resolve_model_metadata(
    models_dir: str | Path,
    model: str,
) -> tuple[dict[str, Any], int]:
    models_root = Path(models_dir).expanduser()
    manifest_path = models_root / "models-manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            manifest = {}

    dim = 384
    cfg_path = models_root / model / "config.json"
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            dim = int(cfg.get("hidden_size", dim))
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    return manifest, dim


def _check_spend_guard(
    count: int,
    shard: int,
    max_usd: float,
    ledger_mod: Any,
) -> tuple[int, int, float]:
    num_shards = max(1, (count + shard - 1) // shard) if count > 0 else 1
    containers = min(4, num_shards) if count > 0 else 1

    estimate = (count / DEFAULT_DOCS_PER_SECOND_PER_CONTAINER) / containers if count > 0 else 0.0
    est_total_seconds = estimate * containers
    est_usd = float(ledger_mod.usd("L4", est_total_seconds))

    print(  # noqa: T201
        f"Estimate: {estimate:.1f}s wall across {containers} containers (${est_usd:.4f})"
    )

    ledger_mod.check("L4", est_total_seconds)
    if est_usd > max_usd:
        budget_error_cls = getattr(ledger_mod, "ModalBudgetError", RuntimeError)
        raise budget_error_cls(f"Estimated spend ${est_usd:.4f} exceeds max_usd ${max_usd:.2f}")

    return num_shards, containers, est_usd


def _write_outputs(
    out_dir: Path,
    model: str,
    manifest: dict[str, Any],
    *,
    target_ids: list[int],
    doc_ids_path_obj: Path | None,
    dim: int,
    wall_seconds: float,
    spent_usd: float,
) -> dict[str, Any]:
    if doc_ids_path_obj is not None:
        doc_ids_out = out_dir / f"{model}.doc_ids.json"
        doc_ids_out.write_text(json.dumps(target_ids) + "\n", encoding="utf-8")

    model_entry = manifest.get(model, {})
    revision = str(model_entry.get("revision", ""))
    files_sha256 = {
        k: v["sha256"] for k, v in model_entry.get("files", {}).items() if "sha256" in v
    }

    meta = {
        "model": model,
        "revision": revision,
        "files_sha256": files_sha256,
        "count": len(target_ids),
        "dim": dim,
        "providers": ["CUDAExecutionProvider", "CPUExecutionProvider"],
        "seconds": round(wall_seconds, 2),
        "estimated_usd": round(spent_usd, 4),
    }
    meta_path = out_dir / f"{model}.meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def run_dense_embed(
    model: str,
    out: str | Path,
    *,
    doc_ids: str = "",
    limit: int = 0,
    shard: int = 4096,
    max_usd: float = 2.0,
    store_dir: str | Path = "artefacts/store",
    data_root: str | Path = "~/Code/legal-rag-router/data",
    models_dir: str | Path = "artefacts/models",
    store: Any = None,
    concepts: Any = None,
    map_fn: Callable[[Iterator[list[str]]], Iterable[bytes]] | None = None,
    ledger_mod: Any = None,
) -> dict[str, Any]:
    """Execute dense embedding pipeline and write ordered .npy and metadata.

    Factored plain function without Modal import dependency for local execution and tests (R4).
    """
    if store is None:
        from chapterverse.store import open_store  # noqa: PLC0415

        store = open_store(Path(store_dir).expanduser())

    if concepts is None:
        from legal_rag_router.concepts import load_concepts  # noqa: PLC0415

        concepts_dir = Path(data_root).expanduser() / "concepts"
        concepts = load_concepts(concepts_dir)

    target_ids, doc_ids_path_obj = _resolve_target_ids(doc_ids, limit, concepts)
    count = len(target_ids)

    if ledger_mod is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from deploy.modal import ledger as ledger_mod  # noqa: PLC0415
        except ImportError:
            import ledger as ledger_mod  # type: ignore[import-not-found,no-redef]  # noqa: PLC0415

    _num_shards, containers, _est_usd = _check_spend_guard(count, shard, max_usd, ledger_mod)

    out_dir = Path(out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    npy_path = out_dir / f"{model}.npy"

    manifest, dim = _resolve_model_metadata(models_dir, model)

    mmap = np.lib.format.open_memmap(
        str(npy_path),
        mode="w+",
        dtype=np.float32,
        shape=(count, dim),
    )

    t0 = time.perf_counter()
    shard_gen = iter_shards(target_ids, store, concepts, shard)

    if map_fn is None:
        if not HAS_MODAL:
            raise RuntimeError("Modal is not available; pass map_fn for local execution")
        embedder = Embedder()
        shard_results: Iterable[bytes] = embedder.embed.starmap(
            ((model, "doc", s) for s in shard_gen),
            order_outputs=True,
        )
    else:
        shard_results = map_fn(shard_gen)

    offset = 0
    for shard_bytes in shard_results:
        vecs = np.load(io.BytesIO(shard_bytes))
        n_rows = len(vecs)
        if n_rows > 0:
            mmap[offset : offset + n_rows] = vecs
            offset += n_rows

    mmap.flush()
    del mmap
    wall_seconds = time.perf_counter() - t0

    if offset != count:
        raise RuntimeError(f"Expected {count} rows, wrote {offset}")

    containers_used = containers
    spent_seconds = wall_seconds * containers_used
    spent_usd = float(ledger_mod.usd("L4", spent_seconds))
    workspace = os.environ.get("MODAL_PROFILE", "default profile")
    note = f"{count} docs, {model}, {wall_seconds:.1f}s wall, workspace {workspace}"
    ledger_mod.record(f"dense-embed {model}", "L4", spent_seconds, note)
    print(  # noqa: T201
        f"Recorded spend: ${spent_usd:.4f} ({spent_seconds:.1f} GPU-s, {wall_seconds:.1f}s wall)"
    )

    meta = _write_outputs(
        out_dir,
        model,
        manifest,
        target_ids=target_ids,
        doc_ids_path_obj=doc_ids_path_obj,
        dim=dim,
        wall_seconds=wall_seconds,
        spent_usd=spent_usd,
    )

    return {
        "npy_path": npy_path,
        "count": count,
        "dim": dim,
        "wall_seconds": wall_seconds,
        "spent_usd": spent_usd,
        "meta": meta,
    }


if HAS_MODAL:
    app = modal.App(APP_NAME)
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install("onnxruntime-gpu[cuda,cudnn]==1.32.0", "tokenizers==0.23.3", "numpy==2.5.5")
        .env({"MODELS_DIR": "/models", "PYTHONPATH": "/root:/app"})
        .add_local_file("engine/chapterverse/dense/embed.py", remote_path="/root/cv_dense_embed.py")
        .add_local_dir(
            "artefacts/models/bge-small-en-v1.5",
            remote_path="/models/bge-small-en-v1.5",
        )
        .add_local_dir("artefacts/models/e5-small-v2", remote_path="/models/e5-small-v2")
        .add_local_dir("artefacts/models/gte-small", remote_path="/models/gte-small")
        .add_local_file(
            "artefacts/models/models-manifest.json",
            remote_path="/models/models-manifest.json",
        )
    )

    @app.cls(
        image=image,
        gpu=["L4", "A10G"],
        cpu=4.0,
        memory=8192,
        max_containers=4,
        timeout=1800,
        scaledown_window=60,
    )
    class Embedder:
        @modal.enter()  # type: ignore[untyped-decorator]
        def enter(self) -> None:
            self._models: dict[str, Any] = {}

        @modal.method()  # type: ignore[untyped-decorator]
        def embed(
            self,
            model: str,
            kind: Literal["doc", "query"],
            texts: list[str],
        ) -> bytes:
            if not hasattr(self, "_models"):
                self._models = {}
            if model not in self._models:
                models_dir_path = Path(os.environ.get("MODELS_DIR", "/models"))
                if not models_dir_path.is_dir():
                    models_dir_path = Path("artefacts/models")

                import onnxruntime  # type: ignore[import-untyped]  # noqa: PLC0415

                # CUDA and cuDNN come from the pip extras; load them before the session.
                onnxruntime.preload_dlls()
                try:
                    from chapterverse.dense.embed import load_model  # noqa: PLC0415
                except ImportError:
                    from cv_dense_embed import load_model  # type: ignore[import-not-found,no-redef]  # noqa: PLC0415

                embedder = load_model(
                    model,
                    models_dir_path,
                    batch_size=256,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                    pad_to_multiple_of=64,
                )
                if "CUDAExecutionProvider" not in embedder.active_providers:
                    raise RuntimeError(
                        f"CUDAExecutionProvider not active for {model} "
                        f"(active: {embedder.active_providers})"
                    )
                self._models[model] = embedder

            embedder = self._models[model]
            if kind == "doc":
                vecs = embedder.embed_docs(texts)
            elif kind == "query":
                if len(texts) == 0:
                    vecs = np.zeros((0, embedder.dim), dtype=np.float32)
                else:
                    vecs = np.array([embedder.embed_query(t) for t in texts], dtype=np.float32)
            else:
                raise ValueError(f"Unknown kind: {kind!r}")

            buf = io.BytesIO()
            np.save(buf, vecs.astype(np.float32))
            return buf.getvalue()

    @app.local_entrypoint()  # type: ignore[untyped-decorator]
    def main(  # noqa: PLR0917
        model: str,
        out: str,
        doc_ids: str = "",
        limit: int = 0,
        shard: int = 4096,
        max_usd: float = 2.0,
        store: str = "artefacts/store",
        data_root: str = "~/Code/legal-rag-router/data",
    ) -> None:
        embedder = Embedder()

        def modal_map(shard_iter: Iterator[list[str]]) -> Iterable[bytes]:
            return cast(
                Iterable[bytes],
                embedder.embed.starmap(
                    ((model, "doc", s) for s in shard_iter),
                    order_outputs=True,
                ),
            )

        run_dense_embed(
            model=model,
            out=out,
            doc_ids=doc_ids,
            limit=limit,
            shard=shard,
            max_usd=max_usd,
            store_dir=store,
            data_root=data_root,
            map_fn=modal_map,
        )

else:

    class Embedder:  # type: ignore[no-redef]
        """Fallback Embedder stub when modal is not installed."""

        def __init__(self) -> None:
            self._models: dict[str, Any] = {}

        def embed(
            self,
            model: str,
            kind: Literal["doc", "query"],
            texts: list[str],
        ) -> bytes:
            models_dir_path = Path(os.environ.get("MODELS_DIR", "artefacts/models"))
            try:
                from chapterverse.dense.embed import load_model  # noqa: PLC0415
            except ImportError:
                from cv_dense_embed import load_model  # type: ignore[no-redef]  # noqa: PLC0415

            if model not in self._models:
                embedder = load_model(
                    model,
                    models_dir_path,
                    batch_size=256,
                    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                    pad_to_multiple_of=64,
                )
                if "CUDAExecutionProvider" not in embedder.active_providers:
                    raise RuntimeError(
                        f"CUDAExecutionProvider not active for {model} "
                        f"(active: {embedder.active_providers})"
                    )
                self._models[model] = embedder

            embedder = self._models[model]
            if kind == "doc":
                vecs = embedder.embed_docs(texts)
            elif kind == "query":
                if len(texts) == 0:
                    vecs = np.zeros((0, embedder.dim), dtype=np.float32)
                else:
                    vecs = np.array([embedder.embed_query(t) for t in texts], dtype=np.float32)
            else:
                raise ValueError(f"Unknown kind: {kind!r}")

            buf = io.BytesIO()
            np.save(buf, vecs.astype(np.float32))
            return buf.getvalue()

    def main(  # noqa: PLR0917
        model: str,
        out: str,
        doc_ids: str = "",
        limit: int = 0,
        shard: int = 4096,
        max_usd: float = 2.0,
        store: str = "artefacts/store",
        data_root: str = "~/Code/legal-rag-router/data",
    ) -> None:
        run_dense_embed(
            model=model,
            out=out,
            doc_ids=doc_ids,
            limit=limit,
            shard=shard,
            max_usd=max_usd,
            store_dir=store,
            data_root=data_root,
        )
