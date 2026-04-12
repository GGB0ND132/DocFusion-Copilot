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
        extra_messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured or self._raw_client is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        try:
            messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
            if extra_messages:
                messages.extend(extra_messages)
            messages.append({"role": "user", "content": user_prompt})
            kwargs: dict[str, Any] = {
                "model": self.model,
                "temperature": temperature,
                "messages": messages,
            }
            if json_schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "docfusion_response", "schema": json_schema},
                }
            try:
                response = self._raw_client.chat.completions.create(**kwargs)
            except APIError as first_err:
                # 部分模型（如 deepseek-chat）不支持 json_schema，降级为 json_object
                if json_schema is not None and "response_format" in str(first_err):
                    logger.info("json_schema not supported, falling back to json_object")
                    kwargs["response_format"] = {"type": "json_object"}
                    # 将 schema 要求追加到 system prompt 中，确保输出结构
                    schema_hint = f"\n\n请严格按此 JSON schema 输出：{json.dumps(json_schema, ensure_ascii=False)}"
                    messages[0]["content"] += schema_hint
                    response = self._raw_client.chat.completions.create(**kwargs)
                else:
                    raise
            if not response.choices:
                raise OpenAIClientError("OpenAI API returned empty choices")
            content = response.choices[0].message.content or ""
            # 推理模型可能返回 <think>...</think> 前缀，剥离后再解析 JSON
            import re as _re
            content = _re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
            return json.loads(content)
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OpenAIClientError(f"Invalid JSON from API: {exc}") from exc

    # ── plain text completion (for code generation) ──
    def create_text_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        """Return raw text completion without JSON parsing."""
        if not self.is_configured or self._raw_client is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        try:
            response = self._raw_client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            if not response.choices:
                raise OpenAIClientError("OpenAI API returned empty choices")
            content = response.choices[0].message.content or ""
            import re as _re
            content = _re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
            return content
        except (APIError, APIConnectionError, APITimeoutError) as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc

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
