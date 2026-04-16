"""结构化日志与统一错误码定义。
Structured logging and unified error code definitions.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Any, Generator


# ── Error Code System ──
# E1xxx: 解析错误  Parsing errors
# E2xxx: 抽取错误  Extraction errors
# E3xxx: 回填错误  Template fill errors
# E4xxx: Agent 错误 Agent errors

class ErrorCode:
    # Parsing
    PARSE_READ_FAILURE = "E1002"
    # Extraction
    EXTRACT_NO_FACTS = "E2001"


class StructuredFormatter(logging.Formatter):
    """JSON 格式的结构化日志格式器。
    JSON-based structured log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge extra structured fields
        for key in ("request_id", "doc_id", "task_id", "duration_ms", "error_code", "detail"):
            value = getattr(record, key, None)
            if value is not None:
                log_data[key] = value
        if record.exc_info and record.exc_info[1]:
            log_data["exception"] = str(record.exc_info[1])
        return json.dumps(log_data, ensure_ascii=False)


def setup_structured_logging(level: int = logging.INFO) -> None:
    """配置全局结构化日志。
    Configure global structured logging."""

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    root = logging.getLogger("docfusion")
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """获取 docfusion 子日志器。
    Get a docfusion child logger."""

    return logging.getLogger(f"docfusion.{name}")


@contextmanager
def log_operation(
    logger: logging.Logger,
    operation: str,
    *,
    doc_id: str | None = None,
    task_id: str | None = None,
    request_id: str | None = None,
) -> Generator[dict[str, Any], None, None]:
    """上下文管理器：自动记录操作耗时和异常。
    Context manager that logs operation duration and exceptions."""

    rid = request_id or uuid.uuid4().hex[:12]
    ctx: dict[str, Any] = {"request_id": rid, "doc_id": doc_id, "task_id": task_id}
    start = time.perf_counter()
    logger.info(
        f"{operation} started",
        extra={"request_id": rid, "doc_id": doc_id, "task_id": task_id},
    )
    try:
        yield ctx
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        error_code = ctx.get("error_code", "E0000")
        logger.error(
            f"{operation} failed: {exc}",
            extra={
                "request_id": rid,
                "doc_id": doc_id,
                "task_id": task_id,
                "duration_ms": duration_ms,
                "error_code": error_code,
            },
        )
        raise
    else:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            f"{operation} completed in {duration_ms}ms",
            extra={
                "request_id": rid,
                "doc_id": doc_id,
                "task_id": task_id,
                "duration_ms": duration_ms,
            },
        )
