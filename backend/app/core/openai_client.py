"""向后兼容的 OpenAI 接口客户端。

内部委托 langchain_openai.ChatOpenAI，对外保留 TemplateService /
FactExtractionService 所依赖的 create_json_completion() 签名。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


class OpenAIClientError(RuntimeError):
    """OpenAI 兼容接口调用异常。"""


@dataclass(slots=True)
class OpenAICompatibleClient:
    """使用 langchain ChatOpenAI 作为底层的兼容性封装。"""

    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 45.0
    _chat_model: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        if self.is_configured:
            from langchain_openai import ChatOpenAI

            self._chat_model = ChatOpenAI(
                api_key=self.api_key,
                base_url=self.base_url.rstrip("/"),
                model=self.model,
                temperature=0,
                request_timeout=self.timeout_seconds,
            )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def create_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        extra_messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured or self._chat_model is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        try:
            messages: list[Any] = [SystemMessage(content=system_prompt)]
            if extra_messages:
                for msg in extra_messages:
                    role = msg.get("role", "user")
                    if role == "system":
                        messages.append(SystemMessage(content=msg["content"]))
                    else:
                        messages.append(HumanMessage(content=msg["content"]))
            messages.append(HumanMessage(content=user_prompt))

            model = self._chat_model
            if temperature != 0.0:
                model = model.with_config(configurable={"temperature": temperature})

            if json_schema is not None:
<<<<<<< HEAD
                model = model.bind(
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": "docfusion_response", "schema": json_schema},
                    }
                )

            response = model.invoke(messages)
            content = response.content or ""
=======
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
>>>>>>> 2552b228659033d875a73d402eceb5449821552e
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIClientError(f"Invalid JSON from API: {exc}") from exc
<<<<<<< HEAD
        except Exception as exc:
=======

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
>>>>>>> 2552b228659033d875a73d402eceb5449821552e
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc
