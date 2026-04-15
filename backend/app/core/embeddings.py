"""Embedding 模型封装（硅基流动 bge-m3，OpenAI 兼容 API）。"""

from __future__ import annotations

import logging

from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_embedding_model(settings: Settings) -> OpenAIEmbeddings | None:
    """基于项目 Settings 构建 OpenAIEmbeddings 实例。未配置时返回 None。"""
    if not settings.embedding_api_key:
        return None
    return OpenAIEmbeddings(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        model=settings.embedding_model,
    )
