"""LangChain ChatOpenAI 封装，兼容 DeepSeek 等 OpenAI-compatible 端点。"""

from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI

from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_chat_model(settings: Settings) -> ChatOpenAI:
    """基于项目 Settings 构建 ChatOpenAI 实例。"""
    if not settings.openai_api_key:
        logger.warning("DOCFUSION_OPENAI_API_KEY 未设置，LLM 功能不可用")
    return ChatOpenAI(
        api_key=settings.openai_api_key or "sk-placeholder",
        base_url=settings.openai_base_url or None,
        model=settings.openai_model,
        temperature=0,
        timeout=settings.openai_timeout_seconds,
    )
