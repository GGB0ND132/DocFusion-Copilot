from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.container import get_container
from app.schemas.common import BlockResponse, DocumentResponse, FactResponse
from app.schemas.documents import (
    DocumentBatchUploadAcceptedResponse,
    DocumentBatchUploadItemResponse,
    DocumentUploadAcceptedResponse,
)
from app.utils.ids import new_id

router = APIRouter()


@router.post("/upload", response_model=DocumentUploadAcceptedResponse)
async def upload_document(
    file: UploadFile = File(...),
    document_set_id: str | None = Form(default=None),
) -> DocumentUploadAcceptedResponse:
    """接收源文档并加入异步解析队列。
    Accept a source document and queue an asynchronous parsing task.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing uploaded file name.")
    content = await file.read()
    try:
        document, task = get_container().document_service.upload_document(
            file.filename,
            content,
            document_set_id=document_set_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return DocumentUploadAcceptedResponse(
        task_id=task.task_id,
        status=task.status,
        document=DocumentResponse.model_validate(document),
        document_set_id=document_set_id,
    )


@router.post("/upload-batch", response_model=DocumentBatchUploadAcceptedResponse)
async def upload_document_batch(
    files: list[UploadFile] = File(...),
    document_set_id: str | None = Form(default=None),
) -> DocumentBatchUploadAcceptedResponse:
    """接收一批源文档并加入异步解析队列。
    Accept a batch of source documents and queue asynchronous parsing tasks.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No uploaded files were provided.")

    resolved_document_set_id = document_set_id or new_id("docset")
    items: list[DocumentBatchUploadItemResponse] = []
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=400, detail="One uploaded file is missing its file name.")
        content = await file.read()
        try:
            document, task = get_container().document_service.upload_document(
                file.filename,
                content,
                document_set_id=resolved_document_set_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        items.append(
            DocumentBatchUploadItemResponse(
                task_id=task.task_id,
                status=task.status,
                document=DocumentResponse.model_validate(document),
            )
        )
    return DocumentBatchUploadAcceptedResponse(document_set_id=resolved_document_set_id, items=items)


@router.delete("/{doc_id}")
def delete_document(doc_id: str) -> dict:
    """删除文档及其关联的 Block、Fact 和物理文件。
    Delete a document and cascade-remove its blocks, facts and stored file.
    """
    try:
        doc = get_container().document_service.delete_document(doc_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"doc_id": doc.doc_id, "deleted": True}


@router.post("/batch-delete")
def batch_delete_documents(doc_ids: list[str] = Body(..., embed=True)) -> dict:
    """批量删除文档及其关联数据。
    Batch-delete documents and cascade-remove their blocks, facts and stored files.
    """
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for doc_id in doc_ids:
        try:
            get_container().document_service.delete_document(doc_id)
            deleted.append(doc_id)
        except ValueError as exc:
            errors.append({"doc_id": doc_id, "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


@router.get("", response_model=list[DocumentResponse])
def list_documents() -> list[DocumentResponse]:
    """列出后端仓储当前已知的全部文档。
    List documents currently known to the backend repository.
    """
    documents = get_container().document_service.list_documents()
    return [DocumentResponse.model_validate(document) for document in documents]


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(doc_id: str) -> DocumentResponse:
    """按 id 获取单个文档。
    Fetch a single document by id.
    """
    document = get_container().document_service.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentResponse.model_validate(document)


@router.get("/{doc_id}/blocks")
def get_document_blocks(
    doc_id: str,
    limit: int | None = Query(default=None, ge=1, le=500, description="每页条数，不传则返回全部"),
    offset: int = Query(default=0, ge=0, description="偏移量"),
) -> dict:
    """获取指定文档的解析块，支持分页。
    Fetch parsed blocks for a document, with optional pagination.
    """
    document = get_container().document_service.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    container = get_container()
    blocks = container.repository.list_blocks(doc_id, limit=limit, offset=offset)
    total = container.repository.count_blocks(doc_id)
    items = [BlockResponse.model_validate(block) for block in blocks]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/{doc_id}/facts", response_model=list[FactResponse])
def get_document_facts(
    doc_id: str,
    canonical_only: bool = Query(default=False),
    status: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
) -> list[FactResponse]:
    """获取指定文档关联的事实记录。
    Fetch fact records associated with a document.
    """
    document = get_container().document_service.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    facts = get_container().document_service.get_document_facts(
        doc_id,
        canonical_only=canonical_only,
        status=status,
        min_confidence=min_confidence,
    )
    return [FactResponse.model_validate(fact) for fact in facts]


@router.get("/{doc_id}/raw")
def get_document_raw(doc_id: str):
    """返回上传的原始文件。
    Return the raw uploaded file for in-browser preview or download.
    """
    document = get_container().document_service.get_document(doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    stored = Path(document.stored_path)
    uploads_dir = get_container().settings.uploads_dir
    if not stored.exists() or not str(stored.resolve()).startswith(str(uploads_dir.resolve())):
        raise HTTPException(status_code=404, detail="File not found on disk.")
    media_types = {
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    suffix = stored.suffix.lower()
    return FileResponse(
        path=stored,
        media_type=media_types.get(suffix, "application/octet-stream"),
        filename=document.file_name,
    )
