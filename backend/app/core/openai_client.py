from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, TypeVar

from openai import OpenAI, APIError, APIConnectionError, APITimeoutError
from pydantic import BaseModel

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenAIClientError(RuntimeError):
    """OpenAI 兼容接口调用异常。"""


@dataclass(slots=True)
class OpenAICompatibleClient:
    """OpenAI 兼容聊天接口客户端，集成 instructor 结构化输出。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 45.0
    embedding_model: str = ""
    _raw_client: Any = field(init=False, repr=False, default=None)
    _instructor_client: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if self.is_configured:
            self._raw_client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url.rstrip("/"),
                timeout=self.timeout_seconds,
            )
            try:
                import instructor
                self._instructor_client = instructor.from_openai(self._raw_client)
            except Exception:
                logger.warning("instructor patch failed, structured completions unavailable")
                self._instructor_client = None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    # ── backward-compatible JSON completion ──
    def create_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        if not self.is_configured or self._raw_client is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if json_schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "docfusion_response", "schema": json_schema},
                }
            response = self._raw_client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            return json.loads(content)
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpenAIClientError(f"Invalid JSON from API: {exc}") from exc

    # ── NEW: instructor structured completion ──
    def create_structured_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
        temperature: float = 0.0,
        max_retries: int = 2,
    ) -> T:
        if not self.is_configured:
            raise OpenAIClientError("OpenAI client is not configured.")
        if self._instructor_client is None:
            raise OpenAIClientError("instructor client is not available.")
        try:
            return self._instructor_client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                max_retries=max_retries,
                response_model=response_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc

    # ── NEW: embeddings ──
    def create_embedding(self, text: str) -> list[float]:
        if not self.is_configured or self._raw_client is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        model = self.embedding_model or self.model
        try:
            response = self._raw_client.embeddings.create(model=model, input=[text])
            return response.data[0].embedding
        except Exception as exc:
            logger.warning("Embedding API failed (%s), returning zero vector", exc)
            return [0.0] * 1536
