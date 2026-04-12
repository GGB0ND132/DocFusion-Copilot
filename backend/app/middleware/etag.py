"""轻量级 ETag 中间件：对 GET 响应体计算哈希并支持 304 Not Modified。
Lightweight ETag middleware: hash GET response bodies and return 304 when unchanged.
"""
from __future__ import annotations

import hashlib
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class ETagMiddleware(BaseHTTPMiddleware):
    """为 GET 请求的 JSON 响应自动添加 ETag，匹配时返回 304。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method != "GET":
            return await call_next(request)

        response = await call_next(request)

        # Only process JSON-like responses with 200 status
        content_type = response.headers.get("content-type", "")
        if response.status_code != 200 or "json" not in content_type:
            return response

        # Read the body from the streaming response
        body_chunks: list[bytes] = []
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        body = b"".join(body_chunks)

        # Compute ETag
        etag = f'W/"{hashlib.md5(body).hexdigest()}"'

        # Check If-None-Match
        if_none_match = request.headers.get("if-none-match", "")
        if if_none_match == etag:
            return Response(status_code=304, headers={"etag": etag})

        return Response(
            content=body,
            status_code=response.status_code,
            headers={**dict(response.headers), "etag": etag},
            media_type=response.media_type,
        )
