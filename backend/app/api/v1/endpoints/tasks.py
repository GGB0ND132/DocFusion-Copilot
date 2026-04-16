from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.core.container import get_container
from app.schemas.tasks import TaskResponse

router = APIRouter()
_logger = logging.getLogger(__name__)


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    task_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TaskResponse]:
    """列出任务记录，可按 task_type 过滤，按创建时间降序。
    List task records ordered by created_at DESC with optional type filter.
    """
    repo = get_container().repository
    tasks = repo.list_tasks(task_type=task_type, limit=limit)
    return [TaskResponse.model_validate(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task_status(task_id: str) -> TaskResponse:
    """获取异步任务的最新状态快照。
    Fetch the latest status snapshot for an asynchronous task.
    """
    task = get_container().repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}")
def delete_task(task_id: str) -> dict[str, object]:
    """删除任务记录及其产物文件。
    Delete a task record and its generated output file if any.
    """
    repo = get_container().repository
    result = repo.get_template_result(task_id)
    # Remove the produced file from storage, if present
    removed_file = False
    if result is not None:
        try:
            output_path = Path(result.output_path)
            if output_path.exists():
                output_path.unlink()
                removed_file = True
        except Exception as exc:  # noqa: BLE001
            _logger.warning("delete_task: failed to remove output file %s: %s", result.output_path, exc)
    deleted = repo.delete_task(task_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"task_id": task_id, "deleted": True, "removed_file": removed_file}
