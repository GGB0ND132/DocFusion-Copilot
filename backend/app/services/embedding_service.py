"""Embedding 服务：为文档块生成向量嵌入并持久化。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import OpenAIEmbeddings
    from app.models.domain import DocumentBlock
    from app.repositories.base import Repository

logger = logging.getLogger(__name__)

# 每批次最多处理的文本数量（避免单次 API 请求过大）
_BATCH_SIZE = 64


class EmbeddingService:
    """为文档块生成 embedding 向量并存入仓储。"""

    def __init__(self, embedding_model: OpenAIEmbeddings, repository: Repository) -> None:
        self._model = embedding_model
        self._repository = repository

    @property
    def is_configured(self) -> bool:
        """Embedding API 是否已配置。"""
        return bool(getattr(self._model, "openai_api_key", None)) and self._model.openai_api_key != "sk-placeholder"

    def embed_blocks(self, blocks: list[DocumentBlock]) -> int:
        """为一批文档块生成 embedding 并存储，返回成功条数。"""
        if not self.is_configured or not blocks:
            return 0

        texts = [block.text for block in blocks]
        count = 0
        for i in range(0, len(texts), _BATCH_SIZE):
            batch_texts = texts[i: i + _BATCH_SIZE]
            batch_blocks = blocks[i: i + _BATCH_SIZE]
            try:
                embeddings = self._model.embed_documents(batch_texts)
                for block, emb in zip(batch_blocks, embeddings):
                    self._repository.upsert_block_embedding(block.block_id, emb)
                    count += 1
            except Exception:
                logger.exception("Embedding batch %d–%d failed", i, i + len(batch_texts))
        return count

    def embed_query(self, text: str) -> list[float]:
        """为单条查询文本生成 embedding。"""
        return self._model.embed_query(text)
