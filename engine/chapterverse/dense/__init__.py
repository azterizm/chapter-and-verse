"""Dense retrieval package for Chapter & Verse."""

from __future__ import annotations

from chapterverse.dense.doctext import DocTextBuilder, doc_text, iter_doc_text, iter_doc_texts
from chapterverse.dense.embed import MODEL_NAMES, ModelLoadError, OnnxEmbedder, load_model
from chapterverse.dense.retriever import DenseHit, DenseRetriever
from chapterverse.dense.store import BinaryInt8Store, DenseLoadError, VectorStore

__all__ = [
    "MODEL_NAMES",
    "BinaryInt8Store",
    "DenseHit",
    "DenseLoadError",
    "DenseRetriever",
    "DocTextBuilder",
    "ModelLoadError",
    "OnnxEmbedder",
    "VectorStore",
    "doc_text",
    "iter_doc_text",
    "iter_doc_texts",
    "load_model",
]
