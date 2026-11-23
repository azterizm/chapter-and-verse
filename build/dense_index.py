"""Build dense index from float32 corpus vectors (requirement R1).

Usage:
    uv run python build/dense_index.py --vectors NPY --store DIR --data-root ROOT --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from legal_rag_router.concepts import load_concepts

from chapterverse.contracts import canonical_json
from chapterverse.dense.embed import MANIFEST_NAME as MODELS_MANIFEST_NAME
from chapterverse.store import open_store
from chapterverse.store.manifest import sha256_file

CHUNK_ROWS = 65536
EXPECTED_NDIM = 2
EXPECTED_DIM = 384


def _find_meta(vectors_path: Path, meta_arg: Path | None) -> dict[str, Any]:
    if meta_arg is not None and meta_arg.is_file():
        return json.loads(meta_arg.read_text(encoding="utf-8"))

    candidates = [
        vectors_path.with_name(f"{vectors_path.stem}.meta.json"),
        vectors_path.with_suffix(".meta.json"),
    ]
    cv_scratch = os.environ.get("CV_SCRATCH")
    if cv_scratch:
        candidates.append(Path(cv_scratch) / "modal-tp" / f"{vectors_path.stem}.meta.json")

    for c in candidates:
        if c.is_file():
            return json.loads(c.read_text(encoding="utf-8"))

    # Fallback to default bge-small config if models-manifest exists
    models_manifest_path = Path("artefacts/models") / MODELS_MANIFEST_NAME
    if models_manifest_path.is_file():
        manifest = json.loads(models_manifest_path.read_text(encoding="utf-8"))
        model_name = "bge-small-en-v1.5"
        if model_name in manifest:
            entry = manifest[model_name]
            files_sha = {
                rel: info["sha256"]
                for rel, info in entry.get("files", {}).items()
                if "sha256" in info
            }
            return {
                "model": model_name,
                "revision": entry.get("revision", ""),
                "files_sha256": files_sha,
            }

    raise FileNotFoundError(f"cannot find metadata JSON for {vectors_path}")


def _check_model_meta(
    model_name: str,
    meta_revision: str,
    files_sha256: dict[str, str],
    models_dir: Path,
) -> dict[str, str]:
    models_manifest_path = models_dir / MODELS_MANIFEST_NAME
    if not models_manifest_path.is_file():
        return files_sha256

    models_manifest = json.loads(models_manifest_path.read_text(encoding="utf-8"))
    if model_name not in models_manifest:
        return files_sha256

    expected_rev = str(models_manifest[model_name].get("revision", ""))
    if meta_revision and expected_rev and meta_revision != expected_rev:
        raise ValueError(
            f"model revision mismatch: meta has {meta_revision!r}, "
            f"models-manifest has {expected_rev!r}"
        )

    expected_files = models_manifest[model_name].get("files", {})
    for rel_path, expected_sha in files_sha256.items():
        if rel_path in expected_files:
            actual_sha = expected_files[rel_path].get("sha256")
            if actual_sha and actual_sha != expected_sha:
                raise ValueError(f"sha256 mismatch for {rel_path}: {expected_sha} != {actual_sha}")

    if not files_sha256:
        return {rel: info["sha256"] for rel, info in expected_files.items() if "sha256" in info}
    return files_sha256


def _write_quantised_files(
    vectors: np.ndarray,
    scale: np.ndarray,
    count: int,
    out_dir: Path,
) -> dict[str, dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vecs_file = out_dir / "vectors.i8"
    codes_file = out_dir / "codes.bin"
    scales_file = out_dir / "scales.f32"

    with vecs_file.open("wb") as f_vecs, codes_file.open("wb") as f_codes:
        for start in range(0, count, CHUNK_ROWS):
            end = min(start + CHUNK_ROWS, count)
            chunk = vectors[start:end]
            q = np.clip(np.round(chunk / scale), -127, 127).astype(np.int8)
            f_vecs.write(q.tobytes())

            sign_bits = chunk > 0
            codes = np.packbits(sign_bits, axis=-1, bitorder="little").astype(np.uint8)
            f_codes.write(codes.tobytes())

    scales_file.write_bytes(scale.tobytes())

    return {
        "codes.bin": {"sha256": sha256_file(codes_file), "size": codes_file.stat().st_size},
        "scales.f32": {"sha256": sha256_file(scales_file), "size": scales_file.stat().st_size},
        "vectors.i8": {"sha256": sha256_file(vecs_file), "size": vecs_file.stat().st_size},
    }


def build_dense_index(
    vectors_path: Path,
    store_dir: Path,
    data_root: Path,
    out_dir: Path,
    *,
    meta_path: Path | None = None,
    models_dir: Path = Path("artefacts/models"),
    allow_partial: bool = False,
    quiet: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()

    store = open_store(store_dir)
    concepts_dir = data_root / "concepts" if (data_root / "concepts").is_dir() else data_root
    concepts = load_concepts(concepts_dir)
    first_instrument_doc = getattr(concepts, "first_instrument_doc", concepts.doc_count)

    if not vectors_path.is_file():
        raise FileNotFoundError(f"vector file not found: {vectors_path}")

    vectors = np.load(vectors_path, mmap_mode="r")
    if vectors.ndim != EXPECTED_NDIM:
        raise ValueError(f"expected 2D array, got shape {vectors.shape}")
    count, dim = vectors.shape
    if dim != EXPECTED_DIM:
        raise ValueError(f"expected {EXPECTED_DIM} dimensions, got {dim}")

    if not allow_partial and count != concepts.doc_count:
        raise ValueError(f"row count {count} != concept index doc_count {concepts.doc_count}")
    if allow_partial and count > concepts.doc_count:
        raise ValueError(f"row count {count} > concept index doc_count {concepts.doc_count}")

    meta = _find_meta(vectors_path, meta_path)
    model_name = str(meta.get("model", "bge-small-en-v1.5"))
    meta_revision = str(meta.get("revision", ""))
    files_sha256 = _check_model_meta(
        model_name, meta_revision, dict(meta.get("files_sha256", {})), models_dir
    )

    max_abs = np.zeros(dim, dtype=np.float32)
    for start in range(0, count, CHUNK_ROWS):
        end = min(start + CHUNK_ROWS, count)
        chunk = vectors[start:end]
        max_abs = np.maximum(max_abs, np.max(np.abs(chunk), axis=0).astype(np.float32))

    scale = max_abs / 127.0
    scale[scale == 0] = 1.0
    scales_f32 = scale.astype(np.float32)

    files_info = _write_quantised_files(vectors, scales_f32, count, out_dir)

    manifest: dict[str, Any] = {
        "format_version": 1,
        "kind": "chapterverse-dense-index",
        "model": model_name,
        "revision": meta_revision,
        "files_sha256": files_sha256,
        "model_files_sha256": files_sha256,
        "model_files": files_sha256,
        "dim": int(dim),
        "count": int(count),
        "corpus_id": store.corpus_id,
        "snapshot": store.snapshot,
        "first_instrument_doc": int(first_instrument_doc),
        "quantisation": "int8_scalar_per_dim+binary_sign",
        "files": files_info,
    }

    manifest_file = out_dir / "dense-manifest.json"
    manifest_bytes = canonical_json(manifest)
    manifest_file.write_bytes(manifest_bytes)

    elapsed = time.perf_counter() - t0
    if not quiet:
        print(f"Dense index built in {elapsed:.2f} s:")
        print(f"  Count: {count:,} vectors (dim={dim})")
        print(f"  Model: {model_name} (rev={meta_revision[:12]})")
        print(f"  Corpus ID: {store.corpus_id[:12]} (snapshot={store.snapshot})")
        print(f"  vectors.i8: {files_info['vectors.i8']['size']:,} bytes")
        print(f"  codes.bin:  {files_info['codes.bin']['size']:,} bytes")
        print(f"  scales.f32: {files_info['scales.f32']['size']:,} bytes")
        print(f"  dense-manifest.json: {len(manifest_bytes):,} bytes")

    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors", type=Path, required=True, help="Path to float32 .npy file")
    parser.add_argument("--store", type=Path, required=True, help="Path to text store directory")
    parser.add_argument("--data-root", type=Path, required=True, help="Path to legal data root")
    parser.add_argument("--out", type=Path, required=True, help="Output directory for dense index")
    parser.add_argument("--meta", type=Path, default=None, help="Optional path to meta.json")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path("artefacts/models"),
        help="Path to models directory (default: artefacts/models)",
    )
    parser.add_argument(
        "--allow-partial",
        "--partial",
        action="store_true",
        dest="allow_partial",
        help="Allow row count less than concept index doc_count",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode")

    args = parser.parse_args(argv)

    try:
        build_dense_index(
            args.vectors,
            args.store,
            args.data_root,
            args.out,
            meta_path=args.meta,
            models_dir=args.models_dir,
            allow_partial=args.allow_partial,
            quiet=args.quiet,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
