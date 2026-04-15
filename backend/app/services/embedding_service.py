"""Embedding 服务：为文档块生成向量嵌入并持久化。"""

from __future__ import annotations

import logging
import time
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import OpenAIEmbeddings
    from app.models.domain import DocumentBlock
    from app.repositories.base import Repository

logger = logging.getLogger(__name__)

# 每批次最多处理的文本数量（避免单次 API 请求过大）
_BATCH_SIZE = 64


def _filename_prefix(file_name: str) -> str:
    """从文件名生成嵌入前缀，重复两次以提高权重。"""
    if not file_name:
        return ""
    stem = PurePosixPath(file_name).stem
    return f"[{stem}] [{stem}] "


class EmbeddingService:
    """为文档块生成 embedding 向量并存入仓储。"""

    def __init__(self, embedding_model: OpenAIEmbeddings | None, repository: Repository) -> None:
        self._model = embedding_model
        self._repository = repository

    @property
    def is_configured(self) -> bool:
        """Embedding API 是否已配置。"""
        if self._model is None:
            return False
        return bool(getattr(self._model, "openai_api_key", None)) and self._model.openai_api_key != "sk-placeholder"

    def embed_blocks(self, blocks: list[DocumentBlock], *, file_name: str = "") -> int:
        """为一批文档块生成 embedding 并存储，返回成功条数。

        file_name: 来源文件名，注入到每个 block 的嵌入文本前缀中，
                   使文件名成为向量空间的强信号。
        """
        if not self.is_configured or not blocks:
            return 0

        prefix = _filename_prefix(file_name)
        texts = [f"{prefix}{block.text}" for block in blocks]
        count = 0
        for i in range(0, len(texts), _BATCH_SIZE):
            batch_texts = texts[i: i + _BATCH_SIZE]
            batch_blocks = blocks[i: i + _BATCH_SIZE]
            for attempt in range(3):
                try:
                    embeddings = self._model.embed_documents(batch_texts)
                    for block, emb in zip(batch_blocks, embeddings):
                        self._repository.upsert_block_embedding(block.block_id, emb)
                        count += 1
                    break
                except Exception as exc:
                    if attempt < 2 and "429" in str(exc):
                        wait = 10 * (attempt + 1)
                        logger.warning("Rate limited on batch %d, retrying in %ds", i, wait)
                        time.sleep(wait)
                    else:
                        logger.exception("Embedding batch %d–%d failed", i, i + len(batch_texts))
        return count

    def embed_query(self, text: str) -> list[float]:
        """为单条查询文本生成 embedding。"""
        return self._model.embed_query(text)
