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
    timeout_seconds: float = 180.0
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
                model = model.bind(
                    response_format={
                        "type": "json_schema",
                        "json_schema": {"name": "docfusion_response", "schema": json_schema},
                    }
                )

            response = model.invoke(messages)
            content = response.content or ""
            # 推理模型可能返回 <think>...</think> 前缀，剥离后再解析 JSON
            import re as _re
            content = _re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OpenAIClientError(f"Invalid JSON from API: {exc}") from exc
        except Exception as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc

    def create_text_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> str:
        """Return raw text completion without JSON parsing."""
        if not self.is_configured or self._chat_model is None:
            raise OpenAIClientError("OpenAI client is not configured.")
        try:
            msgs: list[Any] = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            model = self._chat_model
            if temperature != 0.0:
                model = model.with_config(configurable={"temperature": temperature})
            response = model.invoke(msgs)
            content = response.content or ""
            import re as _re
            content = _re.sub(r"<think>[\s\S]*?</think>\s*", "", content).strip()
            return content
        except Exception as exc:
            raise OpenAIClientError(f"OpenAI API error: {exc}") from exc
