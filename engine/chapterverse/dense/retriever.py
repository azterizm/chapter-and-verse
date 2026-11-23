"""Dense retriever implementation (requirement R3).

Wraps OnnxEmbedder, VectorStore, and ConceptIndex, exposing search(text, k)
with timing spans for query embedding and vector search.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from chapterverse.contracts import Coordinate
from chapterverse.dense.store import VectorStore

__all__ = [
    "DenseHit",
    "DenseRetriever",
]


@dataclass(frozen=True, slots=True)
class DenseHit:
    """A single dense retrieval candidate hit."""

    doc_id: int
    coordinate: Coordinate
    heading: str | None
    rank: int
    score: float


class DenseRetriever:
    """Dense retriever over embedded queries and a VectorStore."""

    def __init__(
        self,
        embedder: Any,
        store: VectorStore,
        concepts: Any,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.concepts = concepts
        self.last_embed_ms: float = 0.0
        self.last_search_ms: float = 0.0

    @property
    def timings(self) -> dict[str, float]:
        """Exposed latency timings of the last search call in milliseconds."""
        return {
            "embed_ms": self.last_embed_ms,
            "search_ms": self.last_search_ms,
        }

    def search(self, text: str, k: int = 10) -> list[DenseHit]:
        """Embed query text and search top-k candidates in the vector store."""
        t0 = time.perf_counter()
        query_vec = self.embedder.embed_query(text)
        self.last_embed_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        raw_hits = self.store.search(query_vec, k=k)
        self.last_search_ms = (time.perf_counter() - t1) * 1000.0

        hits: list[DenseHit] = []
        for rank, (doc_id, score) in enumerate(raw_hits, start=1):
            doc = self.concepts.doc(doc_id)
            hits.append(
                DenseHit(
                    doc_id=doc_id,
                    coordinate=str(doc.coordinate),
                    heading=doc.heading,
                    rank=rank,
                    score=float(score),
                )
            )
        return hits
