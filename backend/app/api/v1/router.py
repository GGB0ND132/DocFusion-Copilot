from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints.agent import router as agent_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.facts import router as facts_router
from app.api.v1.endpoints.tasks import router as tasks_router
from app.api.v1.endpoints.templates import router as templates_router

api_router = APIRouter()
api_router.include_router(documents_router, prefix="/documents", tags=["documents"])
api_router.include_router(tasks_router, prefix="/tasks", tags=["tasks"])
api_router.include_router(templates_router, prefix="/templates", tags=["templates"])
api_router.include_router(agent_router, prefix="/agent", tags=["agent"])
api_router.include_router(facts_router, prefix="/facts", tags=["facts"])
