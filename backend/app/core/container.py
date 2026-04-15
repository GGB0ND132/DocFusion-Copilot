from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import logging

from app.core.config import Settings, get_settings
from app.core.openai_client import OpenAICompatibleClient
from app.parsers.factory import ParserRegistry
from app.repositories.base import Repository
from app.repositories.memory import InMemoryRepository
from app.repositories.postgres import PostgresRepository

_logger = logging.getLogger("container")
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.fact_service import FactService
from app.services.fact_extraction import FactExtractionService
from app.services.template_service import TemplateService
from app.services.trace_service import TraceService
from app.tasks.executor import TaskExecutor


@dataclass(slots=True)
class ServiceContainer:
    """聚合核心服务，供 API 处理函数共享单例依赖图。
    Bundle all core services so API handlers can share one singleton graph.
    """

    settings: Settings
    repository: Repository
    executor: TaskExecutor
    openai_client: OpenAICompatibleClient
    parser_registry: ParserRegistry
    extraction_service: FactExtractionService
    embedding_service: EmbeddingService
    document_service: DocumentService
    template_service: TemplateService
    fact_service: FactService
    trace_service: TraceService
    agent_graph: object  # langgraph CompiledGraph


@lru_cache(maxsize=1)
def get_container() -> ServiceContainer:
    """创建并缓存应用级服务容器。
    Create and cache the application service container.
    """
    settings = get_settings()
    try:
        repository: Repository = PostgresRepository(settings.database_url, echo=settings.database_echo)
        repository.initialize()
    except Exception as exc:
        _logger.warning("PostgreSQL 不可用，回退到内存仓储: %s", exc)
        repository = InMemoryRepository()
    executor = TaskExecutor(max_workers=settings.max_workers)
    openai_client = OpenAICompatibleClient(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
        timeout_seconds=settings.openai_timeout_seconds,
    )
    parser_registry = ParserRegistry()
    extraction_service = FactExtractionService(openai_client=openai_client)

    # Embedding service (SiliconFlow bge-m3)
    from app.core.embeddings import build_embedding_model

    embedding_model = build_embedding_model(settings)
    embedding_service = EmbeddingService(
        embedding_model=embedding_model,
        repository=repository,
    )

    document_service = DocumentService(
        repository=repository,
        parser_registry=parser_registry,
        extraction_service=extraction_service,
        executor=executor,
        settings=settings,
        embedding_service=embedding_service,
    )
    template_service = TemplateService(
        repository=repository,
        executor=executor,
        settings=settings,
        openai_client=openai_client,
        extraction_service=extraction_service,
        embedding_service=embedding_service,
    )
    fact_service = FactService(repository=repository)
    trace_service = TraceService(repository=repository)

    # LangGraph agent
    from app.core.llm import build_chat_model
    from app.agent.tools import create_tools
    from app.agent.graph import build_graph

    chat_model = build_chat_model(settings)
    tools = create_tools(
        repository=repository,
        embedding_service=embedding_service,
        extraction_service=extraction_service,
        template_service=template_service,
        trace_service=trace_service,
        settings=settings,
    )
    agent_graph = build_graph(chat_model=chat_model, tools=tools)

    return ServiceContainer(
        settings=settings,
        repository=repository,
        executor=executor,
        openai_client=openai_client,
        parser_registry=parser_registry,
        extraction_service=extraction_service,
        embedding_service=embedding_service,
        document_service=document_service,
        template_service=template_service,
        fact_service=fact_service,
        trace_service=trace_service,
        agent_graph=agent_graph,
    )
