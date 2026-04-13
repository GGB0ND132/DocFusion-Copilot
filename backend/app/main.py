from __future__ import annotations

import os
import sys
from pathlib import Path


def _ensure_backend_on_sys_path() -> None:
    """在直接运行当前文件时将 backend 根目录加入 `sys.path`。
    Add the backend root to `sys.path` when running this file directly.
    """
    if __package__ not in {None, ""}:
        return
    backend_root = Path(__file__).resolve().parents[1]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


_ensure_backend_on_sys_path()

from app.core.config import get_settings
from app.core.logging import setup_structured_logging


def create_app():
    """构建并返回 FastAPI 应用实例。
    Build and return the FastAPI application instance.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.middleware.gzip import GZipMiddleware

    from app.api.v1.router import api_router
    from app.core.container import get_container
    from app.middleware.etag import ETagMiddleware

    setup_structured_logging()
    settings = get_settings()
    get_container()
    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        summary="Competition-ready MVP backend for document parsing, fact extraction and template filling.",
    )
    app.add_middleware(ETagMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=settings.cors_allow_methods,
        allow_headers=settings.cors_allow_headers,
        expose_headers=settings.cors_expose_headers,
    )
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """返回轻量级健康检查结果。
        Return a lightweight liveness probe response.
        """
        return {"status": "ok"}

    return app


_APP_IMPORT_ERROR: ModuleNotFoundError | None = None
try:
    app = create_app()
except ModuleNotFoundError as exc:
    _APP_IMPORT_ERROR = exc
    if __name__ != "__main__":
        raise
    app = None


def run() -> None:
    """在脚本方式执行当前模块时使用 uvicorn 启动后端。
    Start the backend with uvicorn when this module is executed as a script.
    """
    if _APP_IMPORT_ERROR is not None:
        missing_name = _APP_IMPORT_ERROR.name or "dependency"
        raise SystemExit(
            "Unable to start backend because required module "
            f"'{missing_name}' is missing. Run `pip install -r backend/requirements.txt` first."
        )

    import asyncio
    import uvicorn

    settings = get_settings()
    workers = int(os.environ.get("DOCFUSION_UVICORN_WORKERS", "1"))
    if workers > 1:
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=settings.port,
            workers=workers,
        )
    else:
        loop = asyncio.new_event_loop()
        loop.set_default_executor(
            __import__("concurrent.futures").futures.ThreadPoolExecutor(
                max_workers=int(os.environ.get("DOCFUSION_THREAD_POOL_SIZE", "8"))
            )
        )
        asyncio.set_event_loop(loop)
        uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
