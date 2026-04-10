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
from app.services.benchmark_service import BenchmarkService
from app.services.agent_service import AgentService
from app.services.document_service import DocumentService
from app.services.document_interaction_service import DocumentInteractionService
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
    document_service: DocumentService
    template_service: TemplateService
    benchmark_service: BenchmarkService
    fact_service: FactService
    trace_service: TraceService
    agent_service: AgentService
    document_interaction_service: DocumentInteractionService


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
    document_service = DocumentService(
        repository=repository,
        parser_registry=parser_registry,
        extraction_service=extraction_service,
        executor=executor,
        settings=settings,
    )
    template_service = TemplateService(
        repository=repository,
        executor=executor,
        settings=settings,
        openai_client=openai_client,
        extraction_service=extraction_service,
    )
    benchmark_service = BenchmarkService(
        repository=repository,
        executor=executor,
        settings=settings,
        template_service=template_service,
    )
    fact_service = FactService(repository=repository)
    trace_service = TraceService(repository=repository)
    agent_service = AgentService(repository=repository, openai_client=openai_client)
    document_interaction_service = DocumentInteractionService(
        repository=repository,
        agent_service=agent_service,
        template_service=template_service,
        settings=settings,
        openai_client=openai_client,
    )
    return ServiceContainer(
        settings=settings,
        repository=repository,
        executor=executor,
        openai_client=openai_client,
        parser_registry=parser_registry,
        extraction_service=extraction_service,
        document_service=document_service,
        template_service=template_service,
        benchmark_service=benchmark_service,
        fact_service=fact_service,
        trace_service=trace_service,
        agent_service=agent_service,
        document_interaction_service=document_interaction_service,
    )
