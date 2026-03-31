from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.openai_client import OpenAICompatibleClient
from app.models.domain import DocumentBlock

logger = logging.getLogger(__name__)


@dataclass
class BlockEmbedder:
    """Compute and cache embeddings for document blocks."""

    client: OpenAICompatibleClient
    max_text_length: int = 512
    _cache: dict[str, list[float]] = field(default_factory=dict, repr=False)

    def embed_block(self, block: DocumentBlock) -> list[float]:
        if block.block_id in self._cache:
            return self._cache[block.block_id]
        text = block.text[:self.max_text_length].strip()
        if not text:
            return [0.0] * 1536
        embedding = self.client.create_embedding(text)
        self._cache[block.block_id] = embedding
        return embedding

    def embed_text(self, text: str) -> list[float]:
        text = text[:self.max_text_length].strip()
        if not text:
            return [0.0] * 1536
        return self.client.create_embedding(text)

    def embed_blocks(self, blocks: list[DocumentBlock]) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        for block in blocks:
            result[block.block_id] = self.embed_block(block)
        return result
