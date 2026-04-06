from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.catalog import FIELD_ALIASES, INTENT_KEYWORDS
from app.core.logging import ErrorCode, get_logger, log_operation
from app.core.openai_client import OpenAIClientError, OpenAICompatibleClient
from app.models.domain import ConversationRecord
from app.repositories.base import Repository
from app.utils.normalizers import find_entity_mentions

_ALLOWED_INTENTS = {
    "small_talk",
    "extract_facts",
    "query_facts",
    "extract_and_fill_template",
    "trace_fact",
    "edit_document",
    "summarize_document",
    "reformat_document",
    "query_status",
    "general_qa",
    "extract_fields",
    "export_results",
}

_SMALL_TALK_EXACT = {
    "你好",
    "您好",
    "hi",
    "hello",
    "嗨",
    "哈喽",
    "在吗",
    "在不在",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
    "早安",
    "晚安",
    "谢谢",
    "多谢",
    "感谢",
    "辛苦了",
    "help",
}
_SMALL_TALK_CONTAINS = (
    "你是谁",
    "你能做什么",
    "你可以做什么",
    "怎么用",
    "如何使用",
    "能帮我做什么",
    "帮助",
)

_REPLACE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[将把](?P<old>.+?)(?:替换为|改为|改成)(?P<new>.+)"),
    re.compile(r"把文中(?P<old>.+?)(?:替换为|改为|改成)(?P<new>.+)"),
)
_TRAILING_PUNCTUATION = "。！？!?，,；;：:"
_QUOTE_CHARS = "\"'“”‘’《》「」『』"


class AgentService:
    """面向 MVP 自然语言接口的规则式规划服务。    Rule-based planner for the MVP natural-language endpoint."""

    _MAX_HISTORY = 40
    _TRIM_TO = 30
    _CONTEXT_WINDOW = 20
    _logger = get_logger("agent_service")

    def __init__(self, repository: Repository, openai_client: OpenAICompatibleClient) -> None:
        """绑定共享仓储依赖。    Bind the service to the shared repository."""

        self._repository = repository
        self._openai_client = openai_client
        self._conversations: dict[str, list[dict[str, str]]] = {}

    def get_conversation(self, context_id: str) -> list[dict[str, str]]:
        """返回指定对话的历史消息列表。    Return the message history for a conversation."""
        if context_id in self._conversations:
            return list(self._conversations[context_id])
        record = self._repository.get_conversation(context_id)
        if record:
            msgs: list[dict[str, str]] = [{"role": str(m.get("role", "")), "content": str(m.get("content", ""))} for m in record.messages]
            self._conversations[context_id] = msgs
            return list(msgs)
        return []

    def _get_cached_history_ref(self, context_id: str) -> list[dict[str, str]]:
        """???????????????    Return a mutable cached history reference."""
        history = self._conversations.get(context_id)
        if history is not None:
            return history
        history = self.get_conversation(context_id)
        self._conversations[context_id] = history
        return history

    def clear_conversation(self, context_id: str) -> None:
        """清空指定对话的历史记录。    Clear the message history for a conversation."""
        self._conversations.pop(context_id, None)
        self._repository.delete_conversation(context_id)

    def list_conversations(self) -> list[ConversationRecord]:
        """列出全部对话记录。    List all persisted conversations."""
        return self._repository.list_conversations()

    def _ensure_conversation(self, context_id: str) -> None:
        """确保 context_id 在仓储中有对应的对话记录。"""
        if self._repository.get_conversation(context_id) is None:
            now = datetime.now(timezone.utc)
            self._repository.create_conversation(ConversationRecord(
                conversation_id=context_id,
                title="",
                created_at=now,
                updated_at=now,
            ))

    def _sync_to_repo(self, context_id: str) -> None:
        """将内存中的对话历史同步到仓储。"""
        history = self._conversations.get(context_id, [])
        record = self._repository.get_conversation(context_id)
        if record is None:
            return
        record.messages = list(history)  # type: ignore[assignment]
        record.updated_at = datetime.now(timezone.utc)
        # Auto-title from first user message
        if not record.title:
            for msg in history:
                if msg.get("role") == "user":
                    text = str(msg.get("content", ""))
                    record.title = text[:30] + ("…" if len(text) > 30 else "")
                    break
        self._repository.update_conversation(record)

    def _append_message(self, context_id: str, role: str, content: str) -> None:
        """向指定对话追加一条消息，超长时自动截断。    Append a message and auto-trim when the history grows too long."""
        self._ensure_conversation(context_id)
        history = self._get_cached_history_ref(context_id)
        history.append({"role": role, "content": content})
        if len(history) > self._MAX_HISTORY:
            self._conversations[context_id] = history[-self._TRIM_TO:]
        self._sync_to_repo(context_id)

    def _get_history_for_llm(self, context_id: str) -> list[dict[str, str]]:
        """返回用于传入 LLM 的最近 N 条历史消息。    Return recent history messages for LLM context."""
        history = self._get_cached_history_ref(context_id)
        return history[-self._CONTEXT_WINDOW:]

    def chat(
        self,
        message: str,
        context_id: str | None = None,
        *,
        store_preview_message: bool = True,
    ) -> dict[str, object]:
        """将用户消息转换为轻量意图与预览结果。    Turn a user message into a lightweight intent and preview payload."""

        with log_operation(self._logger, "agent_chat"):
            if context_id:
                self._append_message(context_id, "user", message)

            plan = self._plan_message(message, context_id=context_id)
            intent = str(plan["intent"])
            fields = [str(field) for field in plan.get("fields", [])]
            entities = [str(entity) for entity in plan.get("entities", [])]
            edits = self._normalize_edits(plan.get("edits", []))
            preview_facts = self._preview_facts(fields, entities)
            result = {
                "intent": intent,
                "entities": entities or (["城市"] if "城市" in message else []),
                "fields": fields,
                "target": str(
                    plan.get("target")
                    or ("uploaded_template" if intent == "extract_and_fill_template" else "fact_store")
                ),
                "need_db_store": bool(plan.get("need_db_store", intent in {"extract_and_fill_template", "extract_facts"})),
                "context_id": context_id,
                "preview": preview_facts,
                "edits": edits,
                "planner": "openai" if plan.get("planner") == "openai" else "rules",
            }

            if context_id and store_preview_message:
                self._append_message(context_id, "assistant", self._build_preview_message(result))

            return result

    def record_execution_result(self, context_id: str | None, summary: str) -> None:
        """灏嗘墽琛岀殑鏈€缁堝洖澶嶅悓姝ュ埌瀵硅瘽璁板綍銆?
        Persist the final execution reply into the conversation history.
        """

        if not context_id:
            return
        cleaned = summary.strip()
        if not cleaned:
            return
        history = self._conversations.setdefault(context_id, self.get_conversation(context_id))
        if history and history[-1].get("role") == "assistant":
            history[-1]["content"] = cleaned
            self._sync_to_repo(context_id)
            return
        self._append_message(context_id, "assistant", cleaned)

    def _plan_message(self, message: str, *, context_id: str | None = None) -> dict[str, object]:
        """为自然语言消息生成结构化执行计划。    Generate a structured execution plan for a natural-language message."""

        if self._openai_client.is_configured:
            try:
                return self._plan_with_openai_llm(message, context_id=context_id)
            except OpenAIClientError:
                pass
        return self._fallback_plan(message)

    def _plan_with_openai_legacy(self, message: str, *, context_id: str | None = None) -> dict[str, object]:
        """使用 OpenAI 兼容接口生成结构化计划。    Generate a structured plan using an OpenAI-compatible API."""

        extra_messages = self._get_history_for_llm(context_id) if context_id else None

        payload = self._openai_client.create_json_completion(
            system_prompt=(
                "你是 DocFusion 后端的指令规划器。"
                "请把用户消息转换成稳定、简洁、可执行的 JSON 计划。"
                "intent 只能从以下值中选择："
                "extract_facts, query_facts, extract_and_fill_template, trace_fact, edit_document, summarize_document, reformat_document, query_status, general_qa, extract_fields, export_results。\n\n"
                "以下是典型示例：\n"
                '用户：帮我智能填表 → {"intent":"extract_and_fill_template","entities":[],"fields":[],"target":"uploaded_template","need_db_store":true,"edits":[]}\n'
                '用户：查一下上海的GDP → {"intent":"query_facts","entities":["上海"],"fields":["GDP总量"],"target":"fact_store","need_db_store":false,"edits":[]}\n'
                '用户：将南京甲公司替换为南京采购中心 → {"intent":"edit_document","entities":[],"fields":[],"target":"fact_store","need_db_store":false,"edits":[{"old_text":"南京甲公司","new_text":"南京采购中心"}]}\n'
                '用户：请总结这份文档 → {"intent":"summarize_document","entities":[],"fields":[],"target":"fact_store","need_db_store":false,"edits":[]}\n'
                '用户：请帮我整理一下格式 → {"intent":"reformat_document","entities":[],"fields":[],"target":"fact_store","need_db_store":false,"edits":[]}\n'
                '用户：从这些报告中提取各城市的常住人口和GDP数据 → {"intent":"extract_facts","entities":["城市"],"fields":["常住人口","GDP总量"],"target":"fact_store","need_db_store":true,"edits":[]}\n'
                '用户：追溯fact_001的来源 → {"intent":"trace_fact","entities":[],"fields":[],"target":"fact_store","need_db_store":false,"edits":[]}\n'
                '用户：提取所有城市的GDP和人均收入字段 → {"intent":"extract_fields","entities":["城市"],"fields":["GDP总量","人均收入"],"target":"fact_store","need_db_store":false,"edits":[]}\n'
                '用户：把提取结果导出为xlsx → {"intent":"export_results","entities":[],"fields":[],"target":"fact_store","need_db_store":false,"edits":[]}\n'
            ),
            user_prompt=f"用户消息：{message}",
            json_schema={
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "target": {"type": "string"},
                    "need_db_store": {"type": "boolean"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                            "required": ["old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["intent", "entities", "fields", "target", "need_db_store", "edits"],
                "additionalProperties": False,
            },
            extra_messages=extra_messages,
        )
        payload["planner"] = "openai"
        return payload

    def _plan_with_openai_llm(self, message: str, *, context_id: str | None = None) -> dict[str, object]:
        """使用 OpenAI 优先分析用户意图。  Use OpenAI first for intent analysis."""

        extra_messages = self._get_history_for_llm(context_id) if context_id else None
        payload = self._openai_client.create_json_completion(
            system_prompt=(
                "You are the intent planner for the DocFusion backend. "
                "Convert the user's latest message into a concise executable JSON plan. "
                "Allowed intents are: small_talk, extract_facts, query_facts, "
                "extract_and_fill_template, trace_fact, edit_document, summarize_document, "
                "reformat_document, query_status, general_qa, extract_fields, export_results. "
                "Use small_talk for greetings, thanks, capability questions, and light chit-chat. "
                "Use general_qa for open-ended questions that still need a natural-language answer. "
                "Use extract_and_fill_template when the user wants a template, spreadsheet, or table filled. "
                "Keep entities and fields short and literal. Return JSON only."
            ),
            user_prompt=f"User message:\n{message}",
            json_schema={
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "target": {"type": "string"},
                    "need_db_store": {"type": "boolean"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                            "required": ["old_text", "new_text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["intent", "entities", "fields", "target", "need_db_store", "edits"],
                "additionalProperties": False,
            },
            extra_messages=extra_messages,
        )
        intent = str(payload.get("intent", "general_qa")).strip() or "general_qa"
        if intent not in _ALLOWED_INTENTS:
            intent = "general_qa"
        return {
            "intent": intent,
            "entities": [str(entity).strip() for entity in payload.get("entities", []) if str(entity).strip()],
            "fields": [str(field).strip() for field in payload.get("fields", []) if str(field).strip()],
            "target": str(
                payload.get("target")
                or ("uploaded_template" if intent == "extract_and_fill_template" else "fact_store")
            ),
            "need_db_store": bool(
                payload.get("need_db_store", intent in {"extract_and_fill_template", "extract_facts"})
            ),
            "edits": self._normalize_edits(payload.get("edits", [])),
            "planner": "openai",
        }

    def _infer_intent(self, message: str) -> str:
        """根据关键词规则推断最匹配的意图。    Infer the best-matching intent from keyword rules."""

        edits = self._infer_edits(message)
        if edits:
            return "edit_document"
        if self._is_small_talk(message):
            return "small_talk"

        lowered = message.lower()
        best_intent: str | None = None
        best_keyword_len = 0
        for intent, keywords in INTENT_KEYWORDS.items():
            for keyword in keywords:
                if (keyword in lowered or keyword in message) and len(keyword) > best_keyword_len:
                    best_keyword_len = len(keyword)
                    best_intent = intent
        if best_intent is not None:
            return best_intent
        if any(
            keyword in message
            for keyword in (
                "\u8bf4\u4e86\u4ec0\u4e48",
                "\u8bb2\u4e86\u4ec0\u4e48",
                "\u5199\u4e86\u4ec0\u4e48",
                "\u4e3b\u8981\u8bb2\u4ec0\u4e48",
                "\u5185\u5bb9\u662f\u4ec0\u4e48",
            )
        ):
            return "summarize_document"
        if any(keyword in message for keyword in ("摘要", "总结", "概述")):
            return "summarize_document"
        if any(keyword in message for keyword in ("排版", "格式", "整理", "规范", "重排", "清理")):
            return "reformat_document"
        return "general_qa"

    def _fallback_plan(self, message: str) -> dict[str, object]:
        """使用本地规则生成执行计划。    Generate an execution plan using local rules."""

        intent = self._infer_intent(message)
        return {
            "intent": intent,
            "entities": find_entity_mentions(message),
            "fields": self._infer_fields(message),
            "target": "uploaded_template" if intent == "extract_and_fill_template" else "fact_store",
            "need_db_store": intent in {"extract_and_fill_template", "extract_facts"},
            "edits": self._infer_edits(message),
            "planner": "rules",
        }

    def _infer_fields(self, message: str) -> list[str]:
        """识别消息中提到的业务字段。    Detect requested business fields mentioned in the message."""

        lowered = message.lower()
        fields: list[str] = []
        for canonical_name, aliases in FIELD_ALIASES.items():
            candidates = {canonical_name, *aliases}
            if any(candidate.lower() in lowered or candidate in message for candidate in candidates):
                fields.append(canonical_name)
        return fields

    def _infer_edits(self, message: str) -> list[dict[str, str]]:
        """从消息中提取简单文本替换指令。    Extract simple text-replacement edits from the message."""

        edits: list[dict[str, str]] = []
        for pattern in _REPLACE_PATTERNS:
            match = pattern.search(message)
            if not match:
                continue
            old_text = self._clean_edit_text(match.group("old"))
            new_text = self._clean_edit_text(match.group("new"))
            if old_text and new_text and old_text != new_text:
                edits.append({"old_text": old_text, "new_text": new_text})
        return edits

    def _clean_edit_text(self, value: str) -> str:
        """清理替换指令中的引号和尾部标点。    Clean quotes and trailing punctuation from edit text."""

        cleaned = value.strip().strip(_QUOTE_CHARS).strip()
        while cleaned and cleaned[-1] in _TRAILING_PUNCTUATION:
            cleaned = cleaned[:-1].rstrip()
        return cleaned.strip(_QUOTE_CHARS).strip()

    def _is_small_talk(self, message: str) -> bool:
        """鍒ゆ柇娑堟伅鏄惁灞炰簬瀵掓殏銆佹眰鍔╂垨绠€鍗曢棽鑱娿€?
        Determine whether a message is a greeting, help request, or light chit-chat.
        """

        lowered = message.lower().strip()
        collapsed = re.sub(r"\s+", "", lowered).strip("!?,.，。！？~～")
        if collapsed in _SMALL_TALK_EXACT:
            return True
        return any(keyword in lowered or keyword in message for keyword in _SMALL_TALK_CONTAINS)

    def _build_preview_message(self, result: dict[str, object]) -> str:
        """涓虹粨鏋勫寲璁″垝鐢熸垚鏇磋嚜鐒剁殑瀵硅瘽棰勮銆?
        Build a more natural assistant preview message for a structured plan.
        """

        intent = str(result.get("intent", ""))
        entities = [str(entity) for entity in result.get("entities", [])]
        fields = [str(field) for field in result.get("fields", [])]
        if intent == "small_talk":
            return "我已识别为日常对话，会直接用自然语言回复你。"
        if intent == "extract_and_fill_template":
            return "我已理解为模板回填请求，等待模板文件或继续执行填表。"
        if intent == "query_facts":
            scope_parts: list[str] = []
            if entities:
                scope_parts.append(f"实体：{', '.join(entities)}")
            if fields:
                scope_parts.append(f"字段：{', '.join(fields)}")
            suffix = f"（{'；'.join(scope_parts)}）" if scope_parts else ""
            return f"我会先从已解析事实中检索相关数据{suffix}。"
        if intent == "summarize_document":
            return "我会先阅读已解析内容，并给出简洁摘要。"
        if intent == "reformat_document":
            return "我会按你的要求整理文档格式并生成结果文件。"
        if intent == "edit_document":
            return "我会根据你的编辑要求生成修改后的文档。"
        if intent == "query_status":
            return "我会先汇总当前文档、事实和任务状态。"
        if intent == "extract_fields":
            return "我会按你指定的字段范围提取结构化结果。"
        if intent == "export_results":
            return "我会把当前结果整理成可下载文件。"
        return "我已理解你的请求，正在准备合适的处理方式。"

    def _normalize_edits(self, raw_edits: object) -> list[dict[str, str]]:
        """把计划中的编辑描述标准化为替换对。    Normalize edit descriptions from a plan into replacement pairs."""

        normalized: list[dict[str, str]] = []
        if not isinstance(raw_edits, list):
            return normalized
        for item in raw_edits:
            if not isinstance(item, dict):
                continue
            old_text = self._clean_edit_text(str(item.get("old_text", "")))
            new_text = self._clean_edit_text(str(item.get("new_text", "")))
            if old_text and new_text and old_text != new_text:
                normalized.append({"old_text": old_text, "new_text": new_text})
        return normalized

    def _preview_facts(self, fields: list[str], entities: list[str]) -> list[dict[str, object]]:
        """返回与请求匹配的 canonical 事实预览。    Return a small preview of matching canonical facts for the request."""

        preview: list[dict[str, object]] = []
        source_entities = [entity for entity in entities if entity != "城市"] or [None]
        source_fields = fields or [None]
        for entity_name in source_entities:
            for field_name in source_fields:
                facts = self._repository.list_facts(
                    entity_name=entity_name,
                    field_name=field_name,
                    canonical_only=True,
                )
                for fact in facts[:5]:
                    preview.append(
                        {
                            "fact_id": fact.fact_id,
                            "entity_name": fact.entity_name,
                            "field_name": fact.field_name,
                            "value_num": fact.value_num,
                            "value_text": fact.value_text,
                            "unit": fact.unit,
                            "year": fact.year,
                            "confidence": fact.confidence,
                        }
                    )
        return preview[:10]
