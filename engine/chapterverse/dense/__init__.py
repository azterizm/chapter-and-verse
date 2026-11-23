"""Dense retrieval package for Chapter & Verse."""

from __future__ import annotations

from chapterverse.dense.doctext import DocTextBuilder, doc_text, iter_doc_text, iter_doc_texts
from chapterverse.dense.embed import MODEL_NAMES, ModelLoadError, OnnxEmbedder, load_model

__all__ = [
    "MODEL_NAMES",
    "DocTextBuilder",
    "ModelLoadError",
    "OnnxEmbedder",
    "doc_text",
    "iter_doc_text",
    "iter_doc_texts",
    "load_model",
]
