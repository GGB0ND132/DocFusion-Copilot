from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import RLock

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VectorEntry:
    id: str
    doc_id: str
    text_preview: str
    vector: np.ndarray


@dataclass
class InMemoryVectorStore:
    """Thread-safe in-memory vector index using numpy cosine similarity."""

    _entries: list[VectorEntry] = field(default_factory=list, repr=False)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def add(self, entry_id: str, doc_id: str, text_preview: str, vector: list[float]) -> None:
        vec = np.asarray(vector, dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        with self._lock:
            self._entries.append(VectorEntry(id=entry_id, doc_id=doc_id, text_preview=text_preview, vector=vec))

    def search(self, query_vector: list[float], top_k: int = 10, doc_ids: set[str] | None = None) -> list[tuple[str, str, float]]:
        """Return top-k (entry_id, doc_id, similarity) tuples."""
        qvec = np.asarray(query_vector, dtype=np.float32)
        norm = np.linalg.norm(qvec)
        if norm > 0:
            qvec = qvec / norm
        with self._lock:
            candidates = self._entries
            if doc_ids:
                candidates = [e for e in candidates if e.doc_id in doc_ids]
            if not candidates:
                return []
            matrix = np.stack([e.vector for e in candidates])
            scores = matrix @ qvec
            top_indices = np.argsort(scores)[::-1][:top_k]
            return [(candidates[i].id, candidates[i].doc_id, float(scores[i])) for i in top_indices if scores[i] > 0]

    def remove_by_doc(self, doc_id: str) -> int:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.doc_id != doc_id]
            return before - len(self._entries)

    def count(self) -> int:
        with self._lock:
            return len(self._entries)
