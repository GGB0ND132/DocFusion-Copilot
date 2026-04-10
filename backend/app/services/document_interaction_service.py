from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import Settings
from app.core.logging import get_logger, log_operation
from app.core.openai_client import OpenAIClientError, OpenAICompatibleClient
from app.models.domain import DocumentBlock, DocumentRecord, FactRecord
from app.repositories.base import Repository
from app.services.agent_service import AgentService
from app.services.template_service import TemplateService
from app.utils.files import safe_filename
from app.utils.normalizers import format_value
from app.utils.wordprocessing import reformat_docx_document, replace_text_in_docx_document

_TEXT_HEADING_RE = re.compile(
    r"^(?P<prefix>(?:[一二三四五六七八九十]+[、.．]|\d{1,2}(?:\.\d{1,2}){0,2}[、.．]))\s*(?P<title>\S.*)$"
)
_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_CLEAR_CONTENT_RE = re.compile(
    r"(删除|清空|清除|移除).{0,6}(内容|文本|全部|所有)|"
    r"(内容|文本|全部).{0,4}(删除|清空|清除|移除)",
)


def _soft_sort_facts(
    facts: list[FactRecord],
    entities: list[str],
    fields: list[str],
    limit: int = 100,
) -> list[FactRecord]:
    """将匹配 entities/fields 的 facts 排到前面，其余排后面，截断到 limit 条。"""
    if not entities and not fields:
        return facts[:limit]
    entity_set = {e.rstrip("市省区县") for e in entities}
    field_set = set(fields)

    def _match_score(fact: FactRecord) -> int:
        score = 0
        name_norm = fact.entity_name.rstrip("市省区县")
        if entity_set and any(e in name_norm or name_norm in e for e in entity_set):
            score += 2
        if field_set and any(f in fact.field_name or fact.field_name in f for f in field_set):
            score += 1
        return score

    scored = sorted(facts, key=_match_score, reverse=True)
    return scored[:limit]


def _partition_facts(
    facts: list[FactRecord],
    entities: list[str],
    fields: list[str],
    limit: int = 100,
) -> tuple[list[FactRecord], list[FactRecord]]:
    """将事实分为直接相关和补充参考两组，总量不超过 limit。"""
    if not entities and not fields:
        return facts[:limit], []
    entity_set = {e.rstrip("市省区县") for e in entities}
    field_set = set(fields)

    matched: list[FactRecord] = []
    rest: list[FactRecord] = []
    for fact in facts:
        name_norm = fact.entity_name.rstrip("市省区县")
        hit_entity = entity_set and any(e in name_norm or name_norm in e for e in entity_set)
        hit_field = field_set and any(f in fact.field_name or fact.field_name in f for f in field_set)
        if hit_entity or hit_field:
            matched.append(fact)
        else:
            rest.append(fact)
    remaining = max(0, limit - len(matched))
    return matched[:limit], rest[:remaining]


def _soft_sort_blocks(
    blocks: list[DocumentBlock],
    entities: list[str],
    limit: int = 30,
) -> list[DocumentBlock]:
    """将包含实体名称的 blocks 排到前面。"""
    if not entities:
        return blocks[:limit]
    keywords = [e.rstrip("市省区县") for e in entities]

    def _match(block: DocumentBlock) -> int:
        return sum(1 for kw in keywords if kw in block.text)

    scored = sorted(blocks, key=_match, reverse=True)
    return scored[:limit]


class DocumentInteractionService:
    """处理自然语言驱动的文档操作与内容查询。    Handle natural-language-driven document operations and content queries."""

    _logger = get_logger("document_interaction")

    def __init__(
        self,
        repository: Repository,
        agent_service: AgentService,
        template_service: TemplateService,
        settings: Settings,
        openai_client: OpenAICompatibleClient,
    ) -> None:
        """初始化文档交互服务依赖。    Initialize the dependencies required by the document interaction service."""

        self._repository = repository
        self._agent_service = agent_service
        self._template_service = template_service
        self._settings = settings
        self._openai_client = openai_client

    def execute(
        self,
        *,
        message: str,
        document_ids: list[str] | None = None,
        document_set_id: str | None = None,
        context_id: str | None = None,
        template_name: str | None = None,
        template_content: bytes | None = None,
        fill_mode: str = "canonical",
        auto_match: bool = True,
        user_requirement: str = "",
    ) -> dict[str, object]:
        """执行自然语言描述的文档操作。    Execute a document operation described in natural language."""

        with log_operation(self._logger, "agent_execute"):
            plan = self._agent_service.chat(message, context_id, store_preview_message=False)
            resolved_document_ids = self._resolve_document_ids(document_ids, document_set_id)
            intent = str(plan["intent"])
            self._logger.info(f"intent resolved: {intent}", extra={"detail": intent})

            if template_content is not None:
                execution = self._queue_template_fill(
                    plan=plan,
                    template_name=template_name,
                    template_content=template_content,
                    document_set_id=document_set_id,
                    document_ids=document_ids or None,
                    fill_mode=fill_mode,
                    auto_match=auto_match,
                    user_requirement=user_requirement,
                )
            elif intent in {"extract_facts", "query_facts"}:
                execution = self._query_facts(plan, resolved_document_ids)
            elif intent == "edit_document":
                execution = self._edit_documents(plan, resolved_document_ids)
            elif intent == "summarize_document":
                execution = self._summarize_documents(plan, resolved_document_ids)
            elif intent == "reformat_document":
                execution = self._reformat_documents(plan, resolved_document_ids)
            elif intent == "extract_and_fill_template":
                execution = {
                    "execution_type": "plan_only",
                    "summary": "Template filling requires an uploaded template_file.",
                    "facts": [],
                    "artifacts": [],
                    "document_ids": resolved_document_ids,
                    "task_id": None,
                    "task_status": None,
                    "template_name": None,
                }
            elif intent == "query_status":
                execution = self._query_status(resolved_document_ids)
            elif intent == "small_talk":
                execution = self._small_talk(message, resolved_document_ids, bool(template_content))
            elif intent == "general_qa":
                execution = self._general_qa(message, plan, resolved_document_ids)
            elif intent == "extract_fields":
                execution = self._extract_fields(plan, resolved_document_ids)
            elif intent == "export_results":
                execution = self._export_results(plan, resolved_document_ids)
            else:
                execution = {
                    "execution_type": "plan_only",
                    "summary": "No executable backend operation matched the current request.",
                    "facts": [],
                    "artifacts": [],
                    "document_ids": resolved_document_ids,
                    "task_id": None,
                    "task_status": None,
                    "template_name": None,
                }

            merged = {
                **plan,
                **execution,
            }
            self._agent_service.record_execution_result(context_id, str(merged.get("summary", "")))
            return merged

    def _resolve_document_ids(
        self,
        explicit_document_ids: list[str] | None,
        document_set_id: str | None,
    ) -> list[str]:
        """解析需要操作的文档 id 列表。    Resolve the list of document ids targeted by the operation."""

        if explicit_document_ids:
            return [
                doc_id
                for doc_id in explicit_document_ids
                if (document := self._repository.get_document(doc_id)) is not None
                and not bool(document.metadata.get("skip_fact_extraction"))
            ]
        return self._template_service.resolve_document_ids(document_set_id, None)

    def _queue_template_fill(
        self,
        *,
        plan: dict[str, object],
        template_name: str | None,
        template_content: bytes,
        document_set_id: str | None,
        document_ids: list[str] | None,
        fill_mode: str,
        auto_match: bool,
        user_requirement: str = "",
    ) -> dict[str, object]:
        """通过自然语言入口提交模板回填任务。    Queue a template fill task through the natural-language execution entry."""

        if not template_name:
            raise ValueError("Missing template file name for template filling.")
        if not document_ids and not document_set_id:
            raise ValueError("请先上传并解析源文档，再提交模板回填。")

        task = self._template_service.submit_fill_task(
            template_name=template_name,
            content=template_content,
            fill_mode=fill_mode,
            document_set_id=document_set_id,
            document_ids=document_ids,
            auto_match=auto_match,
            user_requirement=user_requirement,
        )
        requested_document_ids = self._resolve_document_ids(document_ids, document_set_id) if not document_ids else document_ids
        summary = (
            f"Queued template fill task for {template_name}. "
            f"Poll /api/v1/tasks/{task.task_id} and download the result from "
            f"/api/v1/templates/result/{task.task_id} after it succeeds."
        )
        return {
            "intent": "extract_and_fill_template",
            "target": "uploaded_template",
            "execution_type": "template_fill_task",
            "summary": summary,
            "facts": [],
            "artifacts": [],
            "document_ids": requested_document_ids,
            "task_id": task.task_id,
            "task_status": str(task.status),
            "template_name": template_name,
        }

    def _query_facts(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """根据规划结果查询事实库。    Query the fact store according to the operation plan."""

        document_id_set = set(document_ids)
        fields = [str(field) for field in plan.get("fields", [])]
        entities = [str(entity) for entity in plan.get("entities", []) if entity != "城市"]

        matched_facts: list[FactRecord] = []
        if not fields and not entities:
            matched_facts = self._repository.list_facts(canonical_only=True, document_ids=document_id_set)
        else:
            for entity_name in entities or [None]:
                for field_name in fields or [None]:
                    matched_facts.extend(
                        self._repository.list_facts(
                            entity_name=entity_name,
                            field_name=field_name,
                            canonical_only=True,
                            document_ids=document_id_set,
                        )
                    )

        deduplicated: dict[str, FactRecord] = {fact.fact_id: fact for fact in matched_facts}
        facts = list(deduplicated.values())
        summary = f"Matched {len(facts)} facts from {len(document_ids)} parsed documents."
        artifacts: list[dict[str, object]] = []
        if facts:
            artifact_name = "facts_query_result.json"
            output_path = self._settings.outputs_dir / artifact_name
            fact_dicts = [
                {
                    "fact_id": f.fact_id,
                    "entity_name": f.entity_name,
                    "field_name": f.field_name,
                    "value_num": f.value_num,
                    "value_text": f.value_text,
                    "unit": f.unit,
                    "year": f.year,
                    "confidence": f.confidence,
                }
                for f in facts
            ]
            output_path.write_text(json.dumps(fact_dicts, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append({
                "doc_id": "",
                "operation": "query_facts",
                "file_name": artifact_name,
                "output_path": str(output_path),
                "change_count": len(facts),
            })
        return {
            "execution_type": "fact_query",
            "summary": summary,
            "facts": facts,
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _edit_documents(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """对文本类文档执行简单内容编辑，支持 LLM 辅助理解复杂编辑指令。
        Apply content edits to text-like documents, with LLM-assisted complex edit parsing."""

        user_intent = str(plan.get("target", "") or plan.get("original_message", ""))
        clear_all = bool(_CLEAR_CONTENT_RE.search(user_intent))

        raw_edits = plan.get("edits", [])
        edits: list[tuple[str, str]] = [
            (str(item.get("old_text", "")).strip(), str(item.get("new_text", "")))
            for item in raw_edits
            if isinstance(item, dict)
            and str(item.get("old_text", "")).strip()
        ]

        # LLM fallback: try to derive edits from document content + user intent
        if not edits and not clear_all and self._openai_client.is_configured and document_ids:
            edits = self._derive_edits_with_llm(plan, document_ids)

        if not edits and not clear_all:
            return {
                "execution_type": "plan_only",
                "summary": "No concrete replacement pair was extracted from the request.",
                "facts": [],
                "artifacts": [],
                "document_ids": document_ids,
            }

        artifacts: list[dict[str, object]] = []
        total_changes = 0
        for doc_id in document_ids:
            document = self._repository.get_document(doc_id)
            if (
                document is None
                or document.doc_type not in {"docx", "md", "txt"}
                or bool(document.metadata.get("skip_fact_extraction"))
            ):
                continue

            source_path = Path(document.stored_path)
            artifact_name = f"{doc_id}_edited_{safe_filename(document.file_name)}"
            output_path = self._settings.outputs_dir / artifact_name

            if clear_all:
                # 清空文件内容
                if document.doc_type == "docx":
                    from app.utils.wordprocessing import create_empty_docx
                    create_empty_docx(output_path)
                else:
                    output_path.write_text("", encoding="utf-8")
                change_count = 1
            elif document.doc_type == "docx":
                change_count = replace_text_in_docx_document(source_path, output_path, edits)
            else:
                content = source_path.read_text(encoding="utf-8", errors="ignore")
                updated_content, change_count = self._apply_text_edits(content, edits)
                output_path.write_text(updated_content, encoding="utf-8")

            total_changes += change_count
            artifacts.append(
                {
                    "doc_id": doc_id,
                    "operation": "edit_document",
                    "file_name": artifact_name,
                    "output_path": str(output_path),
                    "change_count": change_count,
                }
            )

        if clear_all:
            summary = f"已清空 {len(artifacts)} 份文档的内容。请点击下方按钮下载编辑后的文件。"
        elif total_changes > 0:
            summary = f"已编辑 {len(artifacts)} 份文档，共完成 {total_changes} 处替换。请点击下方按钮下载编辑后的文件。"
        else:
            summary = f"已处理 {len(artifacts)} 份文档，但未找到匹配的替换内容。请检查原文中是否存在指定的文本。"
        return {
            "execution_type": "edit",
            "summary": summary,
            "facts": [],
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _summarize_documents(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """汇总文档块和事实，生成摘要。    Summarize document blocks and facts into a compact document summary."""

        document_ids = [
            doc_id
            for doc_id in document_ids
            if (document := self._repository.get_document(doc_id)) is not None
            and not bool(document.metadata.get("skip_fact_extraction"))
        ]
        all_blocks: list[DocumentBlock] = []
        facts = self._repository.list_facts(canonical_only=True, document_ids=set(document_ids))
        for doc_id in document_ids:
            all_blocks.extend(self._repository.list_blocks(doc_id))

        # ── 软预过滤：匹配的 facts/blocks 排前面，不完全排除不匹配的 ──
        entities = [str(e) for e in plan.get("entities", []) if e and e != "城市"]
        fields = [str(f) for f in plan.get("fields", [])]
        facts = _soft_sort_facts(facts, entities, fields)
        blocks = _soft_sort_blocks(all_blocks, entities)

        summary = self._build_summary_text(plan, blocks, facts, document_ids)

        artifacts: list[dict[str, object]] = []
        artifact_name = f"summary_{document_ids[0] if document_ids else 'all'}.md"
        output_path = self._settings.outputs_dir / artifact_name
        output_path.write_text(f"# 文档摘要\n\n{summary}\n", encoding="utf-8")
        artifacts.append({
            "doc_id": document_ids[0] if document_ids else "",
            "operation": "summarize_document",
            "file_name": artifact_name,
            "output_path": str(output_path),
            "change_count": None,
        })
        return {
            "execution_type": "summary",
            "summary": summary,
            "facts": facts[:20],
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _build_summary_text(
        self,
        plan: dict[str, object],
        blocks: list[DocumentBlock],
        facts: list[FactRecord],
        document_ids: list[str],
    ) -> str:
        """缁熶竴鐢熸垚鎽樿鏂囨湰銆?   Build summary text through the LLM path with a natural fallback."""

        if self._openai_client.is_configured:
            try:
                return self._summarize_with_openai(plan, blocks, facts)
            except OpenAIClientError:
                pass
        return self._fallback_summary(blocks, facts, document_ids)

    def _reformat_documents(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """对支持的文本类文档执行基础格式整理，支持 LLM 解析用户格式要求。
        Apply formatting cleanup with optional LLM-parsed format requirements."""

        format_spec = self._parse_format_spec(plan) if self._openai_client.is_configured else {}
        artifacts: list[dict[str, object]] = []
        for doc_id in document_ids:
            document = self._repository.get_document(doc_id)
            if (
                document is None
                or document.doc_type not in {"docx", "md", "txt"}
                or bool(document.metadata.get("skip_fact_extraction"))
            ):
                continue

            source_path = Path(document.stored_path)
            artifact_name = f"{doc_id}_formatted_{safe_filename(document.file_name)}"
            output_path = self._settings.outputs_dir / artifact_name
            if document.doc_type == "docx":
                reformat_docx_document(source_path, output_path)
            else:
                content = source_path.read_text(encoding="utf-8", errors="ignore")
                formatted_content = self._reformat_text(content, document.doc_type)
                output_path.write_text(formatted_content, encoding="utf-8")
            artifacts.append(
                {
                    "doc_id": doc_id,
                    "operation": "reformat_document",
                    "file_name": artifact_name,
                    "output_path": str(output_path),
                    "change_count": None,
                }
            )

        summary = f"Generated {len(artifacts)} formatted output files."
        return {
            "execution_type": "reformat",
            "summary": summary,
            "facts": [],
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _summarize_with_openai(
        self,
        plan: dict[str, object],
        blocks: list[DocumentBlock],
        facts: list[FactRecord],
    ) -> str:
        """使用 OpenAI 兼容接口生成摘要。    Generate a summary using an OpenAI-compatible API."""

        block_preview = "\n".join(
            f"- [{block.block_type}] {' > '.join(block.section_path) if block.section_path else 'root'}: {block.text[:200]}"
            for block in blocks[:30]
        )
        fact_preview = "\n".join(
            f"- {fact.entity_name} / {fact.field_name} = {format_value(fact.value_num) or fact.value_text} {fact.unit or ''}".strip()
            for fact in facts[:100]
        )
        user_message = str(plan.get("original_message", plan.get("intent", "")))
        entities = [str(e) for e in plan.get("entities", []) if e and e != "城市"]
        fields = [str(f) for f in plan.get("fields", [])]
        entity_hint = f"\n用户关注的实体：{'、'.join(entities)}。必须以这些实体的数据为主，其他实体仅在对比时提及。" if entities else ""
        field_hint = f"\n用户关注的字段：{'、'.join(fields)}。\n优先围绕这些字段组织摘要。" if fields else ""
        payload = self._openai_client.create_json_completion(
            system_prompt=(
                "你是 DocFusion 文档融合系统的摘要与问答模块。\n"
                "你会收到文档块和结构化事实。请根据用户的具体问题筛选相关内容作答。\n"
                "规则：\n"
                "1. 只使用与用户问题直接相关的事实和文档块，忽略不相关的内容。\n"
                "2. 如果用户指定了地区、主题或时间范围，严格聚焦到该范围。\n"
                "3. 禁止编造任何未在事实或文档块中出现的数据。\n"
                "4. 用简洁的中文回答，包含关键数据和单位。\n"
                "5. 如果提供的事实不足以完整回答问题，如实说明已知部分并指出缺失。"
                f"{entity_hint}{field_hint}"
            ),
            user_prompt=(
                f"用户问题: {user_message}\n\n"
                f"文档块（按章节排列）:\n{block_preview}\n\n"
                f"结构化事实（实体 / 字段 = 值 单位）:\n{fact_preview}\n\n"
                '请输出 JSON: {"summary": "..."}'
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        )
        return str(payload["summary"]).strip()

    def _reformat_text(self, content: str, doc_type: str) -> str:
        """对文本文档内容执行基础排版规范化。    Apply basic layout normalization to text content."""

        lines = [line.rstrip() for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        normalized_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if doc_type == "md":
                if stripped.startswith("#"):
                    hashes = len(stripped) - len(stripped.lstrip("#"))
                    title = stripped[hashes:].strip()
                    normalized_lines.append(f"{'#' * max(1, hashes)} {title}".rstrip())
                    continue
                match = _TEXT_HEADING_RE.match(stripped)
                if match:
                    prefix = match.group("prefix")
                    title = match.group("title").strip() or stripped
                    level = prefix.rstrip("、.．").count(".") + 1 if prefix[0].isdigit() else 1
                    normalized_lines.append(f"{'#' * min(level, 3)} {title}")
                    continue
            normalized_lines.append(stripped if stripped else "")
        normalized_text = "\n".join(normalized_lines).strip() + "\n"
        return _MULTI_BLANK_LINES_RE.sub("\n\n", normalized_text)

    def _parse_format_spec(self, plan: dict[str, object]) -> dict[str, str]:
        """使用 LLM 从用户的格式化请求中解析格式规格。
        Use LLM to parse format specifications from user's reformat request."""

        target = str(plan.get("target", ""))
        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是文档格式分析器。从用户描述中提取格式要求。"
                    "如果用户没有明确指定某个属性，对应值留空字符串。"
                ),
                user_prompt=(
                    f"用户要求: {target}\n\n"
                    '请输出 JSON: {{"heading_level": "用户要求的标题级别（如h1/h2）",'
                    '"font_name": "字体名",'
                    '"font_size": "字号（如12pt）",'
                    '"notes": "其他格式要求描述"}}'
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "heading_level": {"type": "string"},
                        "font_name": {"type": "string"},
                        "font_size": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["heading_level", "font_name", "font_size", "notes"],
                    "additionalProperties": False,
                },
            )
            return {k: str(v) for k, v in payload.items() if v}
        except OpenAIClientError:
            return {}

    def _apply_text_edits(self, content: str, edits: list[tuple[str, str]]) -> tuple[str, int]:
        """对纯文本内容执行替换并统计变更次数。    Apply text replacements to plain content and count changes."""

        updated_content = content
        total_changes = 0
        for old_text, new_text in edits:
            change_count = updated_content.count(old_text)
            if change_count <= 0:
                continue
            updated_content = updated_content.replace(old_text, new_text)
            total_changes += change_count
        return updated_content, total_changes

    def _derive_edits_with_llm(
        self, plan: dict[str, object], document_ids: list[str],
    ) -> list[tuple[str, str]]:
        """使用 LLM 从文档内容和用户意图推导编辑对。
        Use LLM to derive replacement pairs from document content and user intent."""

        snippets: list[str] = []
        for doc_id in document_ids[:3]:
            blocks = self._repository.list_blocks(doc_id)
            for block in blocks[:10]:
                snippets.append(block.text[:200])
        content_preview = "\n".join(snippets)[:2000]
        intent_text = str(plan.get("target", ""))

        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是文档编辑助手。根据用户意图和文档片段，输出需要执行的文本替换对。"
                    "每一对包含 old_text（原文中存在的文本）和 new_text（替换后的文本）。"
                    "如果用户要求删除某段文本，new_text 应为空字符串。"
                    "最多输出 10 对替换。如果无法确定具体替换，返回空数组。"
                ),
                user_prompt=(
                    f"用户意图: {intent_text}\n\n"
                    f"文档片段:\n{content_preview}\n\n"
                    '请输出 JSON: {"edits": [{"old_text": "...", "new_text": "..."}]}'
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "edits": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "old_text": {"type": "string"},
                                    "new_text": {"type": "string"},
                                },
                                "required": ["old_text", "new_text"],
                            },
                        },
                    },
                    "required": ["edits"],
                    "additionalProperties": False,
                },
            )
            return [
                (str(e["old_text"]).strip(), str(e["new_text"]).strip())
                for e in payload.get("edits", [])
                if isinstance(e, dict) and str(e.get("old_text", "")).strip()
            ]
        except OpenAIClientError:
            return []

    def _extract_fields(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """按用户指定的实体/字段过滤事实并返回结构化结果。
        Extract user-specified entities/fields from the fact store and return structured results."""

        entities = [str(e) for e in plan.get("entities", [])]
        fields = [str(f) for f in plan.get("fields", [])]
        all_facts = self._repository.list_facts(
            canonical_only=True, document_ids=set(document_ids) if document_ids else None,
        )

        matched = all_facts
        if entities:
            matched = [
                f for f in matched
                if f.entity_name in entities or any(e in f.entity_name for e in entities)
            ]
        if fields:
            matched = [f for f in matched if f.field_name in fields]
        if not matched:
            matched = all_facts[:50]

        rows: list[dict[str, object]] = [
            {
                "entity_name": f.entity_name,
                "field_name": f.field_name,
                "value": format_value(f.value_num) if f.value_num is not None else f.value_text,
                "unit": f.unit or "",
                "year": f.year,
                "confidence": f.confidence,
                "source_doc_id": f.source_doc_id,
            }
            for f in matched
        ]
        artifacts: list[dict[str, object]] = []
        artifact_name = "extracted_fields.json"
        output_path = self._settings.outputs_dir / artifact_name
        output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append({
            "doc_id": "",
            "operation": "extract_fields",
            "file_name": artifact_name,
            "output_path": str(output_path),
            "change_count": len(rows),
        })

        entity_label = "、".join(entities) if entities else "全部实体"
        field_label = "、".join(fields) if fields else "全部字段"
        summary = f"已提取 {entity_label} 的 {field_label} 共 {len(rows)} 条记录。"
        return {
            "execution_type": "extract",
            "summary": summary,
            "facts": matched[:20],
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _export_results(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """将事实导出为 xlsx / json 文件。
        Export facts to xlsx or json file."""

        entities = [str(e) for e in plan.get("entities", [])]
        fields_filter = [str(f) for f in plan.get("fields", [])]
        all_facts = self._repository.list_facts(
            canonical_only=True, document_ids=set(document_ids) if document_ids else None,
        )

        matched = all_facts
        if entities:
            matched = [
                f for f in matched
                if f.entity_name in entities or any(e in f.entity_name for e in entities)
            ]
        if fields_filter:
            matched = [f for f in matched if f.field_name in fields_filter]

        rows: list[dict[str, object]] = [
            {
                "实体": f.entity_name,
                "字段": f.field_name,
                "数值": format_value(f.value_num) if f.value_num is not None else f.value_text,
                "单位": f.unit or "",
                "年份": f.year,
                "置信度": f.confidence,
                "来源文档": f.source_doc_id,
            }
            for f in matched
        ]

        artifacts: list[dict[str, object]] = []

        # Always produce JSON
        json_name = "export_results.json"
        json_path = self._settings.outputs_dir / json_name
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        artifacts.append({
            "doc_id": "",
            "operation": "export_results",
            "file_name": json_name,
            "output_path": str(json_path),
            "change_count": len(rows),
        })

        # Try to produce xlsx via openpyxl
        try:
            from openpyxl import Workbook

            xlsx_name = "export_results.xlsx"
            xlsx_path = self._settings.outputs_dir / xlsx_name
            wb = Workbook()
            ws = wb.active
            ws.title = "导出结果"
            if rows:
                headers = list(rows[0].keys())
                ws.append(headers)
                for row in rows:
                    ws.append([row.get(h) for h in headers])
            wb.save(str(xlsx_path))
            artifacts.append({
                "doc_id": "",
                "operation": "export_results",
                "file_name": xlsx_name,
                "output_path": str(xlsx_path),
                "change_count": len(rows),
            })
        except ImportError:
            pass  # openpyxl not installed, skip xlsx export

        summary = f"已导出 {len(rows)} 条事实记录，共生成 {len(artifacts)} 个文件。"
        return {
            "execution_type": "export",
            "summary": summary,
            "facts": matched[:10],
            "artifacts": artifacts,
            "document_ids": document_ids,
        }

    def _query_status(self, document_ids: list[str]) -> dict[str, object]:
        """返回文档库当前状态统计。    Return a summary of the current document store status."""
        all_documents = self._repository.list_documents()
        parsed = [doc for doc in all_documents if doc.status == "parsed"]
        facts = self._repository.list_facts(canonical_only=True)
        summary = (
            f"当前系统共有 {len(all_documents)} 个文档（其中 {len(parsed)} 个已解析），"
            f"已抽取 {len(facts)} 条 canonical 事实。"
        )
        return {
            "execution_type": "status",
            "summary": summary,
            "facts": [],
            "artifacts": [],
            "document_ids": document_ids,
        }

    def _small_talk_legacy(
        self,
        message: str,
        document_ids: list[str],
        has_template_file: bool,
    ) -> dict[str, object]:
        """鐢熸垚鏇磋嚜鐒剁殑瀵掓殏涓庡姛鑳借鏄庡洖澶嶃€?
        Generate a more natural reply for greetings and lightweight chit-chat.
        """

        normalized = message.strip().lower()
        if any(keyword in normalized for keyword in ("谢谢", "感谢", "多谢", "辛苦了")):
            summary = "不客气，我们继续。你可以让我总结文档、查询已抽取事实，或者上传模板后直接帮你回填。"
        elif any(keyword in normalized for keyword in ("你是谁", "你能做什么", "你可以做什么", "怎么用", "如何使用", "帮助", "help")):
            if document_ids:
                summary = (
                    f"我是 DocFusion Agent。当前我已经能访问 {len(document_ids)} 份已解析文档，"
                    "可以帮你总结内容、查询指标、追溯事实来源，也可以在你上传 Word/Excel 模板后自动回填。"
                )
            else:
                summary = (
                    "我是 DocFusion Agent。你可以先上传原始文档，然后让我总结、查询、导出结果；"
                    "如果你已经有 Word/Excel 模板，也可以上传后让我自动回填。"
                )
        else:
            parts = ["你好，我在。"]
            if document_ids:
                parts.append(f"当前这轮我能使用 {len(document_ids)} 份已解析文档。")
            else:
                parts.append("你可以先上传原始文档，或者直接告诉我想查询、总结什么。")
            if has_template_file:
                parts.append("我也收到了模板文件，继续描述要求就可以开始回填。")
            else:
                parts.append("如果有模板，也可以上传 Word 或 Excel 后让我自动回填。")
            summary = " ".join(parts)

        return {
            "execution_type": "conversation",
            "summary": summary,
            "facts": [],
            "artifacts": [],
            "document_ids": document_ids,
        }

    def _general_qa_legacy(self, message: str, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """基于已有事实回答用户的通用问题（LLM-First：不预过滤，让 LLM 自行判断相关性）。
        Answer general questions using available facts (LLM-First: no pre-filtering)."""
        facts = self._repository.list_facts(canonical_only=True, document_ids=set(document_ids) if document_ids else None)
        scoped_blocks = [
            block
            for doc_id in document_ids
            for block in self._repository.list_blocks(doc_id)
        ]

        if document_ids and self._looks_like_content_summary_request(message):
            summary = self._build_summary_text(
                {**plan, "intent": "summarize_document", "original_message": message},
                scoped_blocks,
                facts,
                document_ids,
            )
            return {
                "execution_type": "qa",
                "summary": summary,
                "facts": facts[:10],
                "artifacts": [],
                "document_ids": document_ids,
            }

        if not facts:
            return {
                "execution_type": "qa",
                "summary": self._fallback_no_data_qa(),
                "facts": [],
                "artifacts": [],
                "document_ids": document_ids,
            }

        if self._openai_client.is_configured:
            try:
                fact_text = "\n".join(
                    f"- {f.entity_name} / {f.field_name} = {format_value(f.value_num) or f.value_text} {f.unit or ''}".strip()
                    for f in facts[:100]
                )
                payload = self._openai_client.create_json_completion(
                    system_prompt=(
                        "你是 DocFusion 文档融合问答系统。你会收到文档范围内的全部结构化事实。\n"
                        "请仔细阅读所有事实，自行筛选与用户问题相关的数据来回答。\n"
                        "规则：\n"
                        "1. 只引用与问题直接相关的事实，忽略无关数据。\n"
                        "2. 如果用户指定了地区/主题/时间，严格聚焦到该范围。\n"
                        "3. 禁止编造事实中没有的数据。如果事实不足以完整回答，如实说明。\n"
                        "4. 回答使用简洁中文，包含关键数值和单位。"
                    ),
                    user_prompt=f"全部已知事实:\n{fact_text}\n\n用户问题: {message}\n\n请输出 JSON: {{\"answer\": \"...\"}}",
                    json_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                )
                summary = str(payload.get("answer", "")).strip()
            except OpenAIClientError:
                summary = self._fallback_qa(facts)
        else:
            summary = self._fallback_qa(facts)

        return {
            "execution_type": "qa",
            "summary": summary,
            "facts": facts[:10],
            "artifacts": [],
            "document_ids": document_ids,
        }

    def _answer_with_openai(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: str,
    ) -> str:
        """使用 OpenAI 生成自然语言回复，失败时回退。  Use OpenAI for replies with fallback."""

        if not self._openai_client.is_configured:
            return fallback
        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                    },
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            )
        except OpenAIClientError:
            return fallback
        answer = str(payload.get("answer", "")).strip()
        return answer or fallback

    def _fallback_small_talk(
        self,
        message: str,
        document_ids: list[str],
        has_template_file: bool,
    ) -> str:
        """本地兜底的轻对话回复。  Local fallback reply for light conversation."""

        normalized = message.strip().lower()
        if any(keyword in normalized for keyword in ("谢谢", "感谢", "多谢", "辛苦了")):
            return "不客气，我们继续。你可以让我总结文档、查询事实，或者上传模板后直接帮你回填。"
        if any(keyword in normalized for keyword in ("你是谁", "你能做什么", "你可以做什么", "怎么用", "如何使用", "帮助", "help")):
            if document_ids:
                return (
                    f"我是 DocFusion Agent。当前这轮我能访问 {len(document_ids)} 份已解析文档，"
                    "可以帮你总结内容、查询结构化事实、追溯来源，也可以在上传 Word 或 Excel 模板后自动回填。"
                )
            return (
                "我是 DocFusion Agent。你可以先上传原始文档，然后让我总结、查询、导出结果；"
                "如果你已经有 Word 或 Excel 模板，也可以上传后让我自动回填。"
            )
        parts = ["你好，我在。"]
        if document_ids:
            parts.append(f"当前我可以使用 {len(document_ids)} 份已解析文档。")
        else:
            parts.append("当前还没有可直接引用的已解析文档。")
        if has_template_file:
            parts.append("我也收到了模板文件，你继续描述需求就可以开始处理。")
        else:
            parts.append("如果你有 Word 或 Excel 模板，也可以上传后让我自动回填。")
        return " ".join(parts)

    def _fallback_no_data_qa(self) -> str:
        """本地兜底的无数据问答回复。  Local fallback reply when no facts are available."""

        return "我暂时还没有可参考的数据。你可以先上传文档，或者直接告诉我想总结、查询或回填什么。"

    def _small_talk(
        self,
        message: str,
        document_ids: list[str],
        has_template_file: bool,
    ) -> dict[str, object]:
        """优先用模型生成寒暄回复。  Prefer model-generated replies for greetings and chit-chat."""

        fallback = self._fallback_small_talk(message, document_ids, has_template_file)
        summary = self._answer_with_openai(
            system_prompt=(
                "You are DocFusion Agent. Reply in natural, concise Chinese. "
                "The user is greeting you, making light chit-chat, or asking what you can do. "
                "Do not invent facts or claim documents were analyzed when they were not."
            ),
            user_prompt=(
                f"User message: {message}\n"
                f"Parsed document count: {len(document_ids)}\n"
                f"Template file attached: {'yes' if has_template_file else 'no'}\n"
                "Respond warmly and helpfully."
            ),
            fallback=fallback,
        )
        return {
            "execution_type": "conversation",
            "summary": summary,
            "facts": [],
            "artifacts": [],
            "document_ids": document_ids,
        }

    def _get_scoped_documents(self, document_ids: list[str]) -> list[DocumentRecord]:
        """返回当前问答范围内的已解析文档。  Return parsed documents in the current QA scope."""

        documents: list[DocumentRecord] = []
        seen: set[str] = set()
        for doc_id in document_ids:
            if doc_id in seen:
                continue
            seen.add(doc_id)
            document = self._repository.get_document(doc_id)
            if document is None or bool(document.metadata.get("skip_fact_extraction")):
                continue
            documents.append(document)
        return documents

    def _build_document_scope_prompt(self, documents: list[DocumentRecord]) -> str:
        """构建用于模型问答的文档范围摘要。  Build a scoped document summary for LLM prompts."""

        lines: list[str] = []
        for document in documents[:5]:
            blocks = self._repository.list_blocks(document.doc_id)
            heading = next(
                (block.text.strip() for block in blocks if block.block_type == "heading" and block.text.strip()),
                "",
            )
            snippet = next(
                (block.text.strip().replace("\n", " ")[:120] for block in blocks if block.text.strip()),
                "",
            )
            line = f"- {document.file_name} ({document.doc_type}, {len(blocks)} blocks)"
            if heading:
                line += f"\n  heading: {heading}"
            if snippet:
                line += f"\n  preview: {snippet}"
            lines.append(line)
        return "\n".join(lines)

    def _fallback_document_qa(self, documents: list[DocumentRecord]) -> str:
        """基于文档清单生成兜底回复。  Generate a fallback reply from document scope information."""

        names = [document.file_name for document in documents[:5]]
        lines = [f"当前工作台里有 {len(documents)} 份已解析文档：{'、'.join(names)}。"]
        first_document = documents[0] if documents else None
        if first_document is not None:
            blocks = self._repository.list_blocks(first_document.doc_id)
            if blocks:
                heading = next(
                    (block.text.strip() for block in blocks if block.block_type == "heading" and block.text.strip()),
                    "",
                )
                preview = next((block.text.strip() for block in blocks if block.text.strip()), "")
                if heading:
                    lines.append(f"其中《{first_document.file_name}》的标题是“{heading}”。")
                elif preview:
                    lines.append(f"我已经能读取《{first_document.file_name}》的内容，开头大致是：{preview[:80]}。")
        lines.append("这批文档目前可能还没抽取出结构化事实，但我已经可以继续帮你总结、检索内容或继续做字段抽取。")
        return "".join(lines)

    def _general_qa(self, message: str, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """优先用模型回答通用问题（软预过滤：匹配的 facts 排前面）。
        Prefer model-generated answers for general QA (soft pre-filtering: matched facts first)."""

        all_facts = self._repository.list_facts(canonical_only=True, document_ids=set(document_ids) if document_ids else None)
        all_blocks = [
            block
            for doc_id in document_ids
            for block in self._repository.list_blocks(doc_id)
        ]

        # ── 软预过滤：匹配的 facts/blocks 排前面 ──
        entities = [str(e) for e in plan.get("entities", []) if e and e != "城市"]
        fields = [str(f) for f in plan.get("fields", [])]
        facts = _soft_sort_facts(all_facts, entities, fields)
        scoped_blocks = _soft_sort_blocks(all_blocks, entities)

        if document_ids and self._looks_like_content_summary_request(message):
            summary = self._build_summary_text(
                {**plan, "intent": "summarize_document", "original_message": message},
                scoped_blocks,
                facts,
                document_ids,
            )
            return {
                "execution_type": "qa",
                "summary": summary,
                "facts": facts[:10],
                "artifacts": [],
                "document_ids": document_ids,
            }

        if not all_facts:
            scoped_documents = self._get_scoped_documents(document_ids)
            if scoped_documents:
                summary = self._answer_with_openai(
                    system_prompt=(
                        "You are DocFusion Agent. The workspace has parsed documents, but there may be no extracted facts yet. "
                        "Answer in natural Chinese based on the document list and block previews only. "
                        "Be honest about what is available and suggest useful next steps."
                    ),
                    user_prompt=(
                        f"User question: {message}\n\n"
                        f"Workspace document summary:\n{self._build_document_scope_prompt(scoped_documents)}"
                    ),
                    fallback=self._fallback_document_qa(scoped_documents),
                )
                return {
                    "execution_type": "qa",
                    "summary": summary,
                    "facts": [],
                    "artifacts": [],
                    "document_ids": document_ids,
                }
            summary = self._answer_with_openai(
                system_prompt=(
                    "You are DocFusion Agent. There is currently no extracted fact data available for this request. "
                    "Reply in natural Chinese, be honest about the limitation, and suggest the next best step."
                ),
                user_prompt=(
                    f"User question: {message}\n"
                    f"Parsed document count in scope: {len(document_ids)}\n"
                    "Explain that there is no usable structured data yet and guide the user."
                ),
                fallback=self._fallback_no_data_qa(),
            )
            return {
                "execution_type": "qa",
                "summary": summary,
                "facts": [],
                "artifacts": [],
                "document_ids": document_ids,
            }

        if self._openai_client.is_configured:
            try:
                matched_facts, extra_facts = _partition_facts(all_facts, entities, fields)
                primary_text = "\n".join(
                    f"- {f.entity_name} / {f.field_name} = {format_value(f.value_num) or f.value_text} {f.unit or ''}".strip()
                    for f in matched_facts
                )
                extra_text = "\n".join(
                    f"- {f.entity_name} / {f.field_name} = {format_value(f.value_num) or f.value_text} {f.unit or ''}".strip()
                    for f in extra_facts[:30]
                )
                entity_hint = f"\n用户关注的实体：{'、'.join(entities)}。必须以这些实体的数据为主来回答，不要混入其他实体的数据。" if entities else ""
                field_hint = f"\n用户关注的字段：{'、'.join(fields)}。优先引用这些字段。" if fields else ""
                fact_section = f"【直接相关事实】\n{primary_text}" if primary_text else "【直接相关事实】\n（无匹配）"
                if extra_text:
                    fact_section += f"\n\n【补充参考（可能无关，仅在直接相关事实不足时酌情引用）】\n{extra_text}"
                payload = self._openai_client.create_json_completion(
                    system_prompt=(
                        "你是 DocFusion 文档融合问答系统。你会收到两组事实：【直接相关事实】和【补充参考】。\n"
                        "规则：\n"
                        "1. 优先且主要使用【直接相关事实】来回答。\n"
                        "2. 只有在直接相关事实明显不足时，才谨慎引用【补充参考】中确实相关的条目。\n"
                        "3. 如果用户指定了地区/主题/时间，严格聚焦到该范围，不要混入其他地区或主题。\n"
                        "4. 禁止编造事实中没有的数据。如果事实不足以完整回答，明确说明'根据已有数据'。\n"
                        "5. 回答使用简洁中文，包含关键数值和单位。\n"
                        "6. 如果【直接相关事实】为空且【补充参考】也无相关信息，明确告知用户'未找到与该问题直接相关的结构数据'。"
                        f"{entity_hint}{field_hint}"
                    ),
                    user_prompt=f"{fact_section}\n\n用户问题: {message}\n\n请输出 JSON: {{\"answer\": \"...\"}}",
                    json_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                )
                summary = str(payload.get("answer", "")).strip() or self._fallback_qa(facts)
            except OpenAIClientError:
                summary = self._fallback_qa(facts)
        else:
            summary = self._fallback_qa(facts)

        return {
            "execution_type": "qa",
            "summary": summary,
            "facts": facts[:10],
            "artifacts": [],
            "document_ids": document_ids,
        }

    @staticmethod
    def _clean_summary_text(text: str) -> str:
        """清理用于摘要的文本片段。   Clean text fragments used by fallback summaries."""

        normalized = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        return normalized.strip("，。；： ")

    def _pick_document_title(self, document: DocumentRecord, blocks: list[DocumentBlock]) -> str:
        """选择更易读的文档标题。   Pick the most human-readable title for a document."""

        for block in blocks:
            text = self._clean_summary_text(block.text)
            if block.block_type == "heading" and text:
                return text
        return Path(document.file_name).stem or document.file_name

    def _collect_section_titles(self, blocks: list[DocumentBlock]) -> list[str]:
        """提取文档中的主要章节。   Extract the main section titles from document blocks."""

        titles: list[str] = []
        seen: set[str] = set()
        for block in blocks:
            if block.block_type != "heading":
                continue
            title = self._clean_summary_text(block.text)
            if not title or title in seen:
                continue
            seen.add(title)
            titles.append(title)
        return titles[:5]

    def _collect_indicator_preview(self, facts: list[FactRecord]) -> list[str]:
        """提取用于摘要的指标预览。   Collect a small indicator preview for fallback summaries."""

        previews: list[str] = []
        seen: set[tuple[str, str]] = set()
        for fact in facts:
            key = (fact.entity_name, fact.field_name)
            if key in seen:
                continue
            seen.add(key)
            value = format_value(fact.value_num) if fact.value_num is not None else fact.value_text
            if not value:
                continue
            unit = f" {fact.unit}" if fact.unit else ""
            previews.append(f"{fact.entity_name}{fact.field_name}为{value}{unit}")
        return previews[:5]

    @staticmethod
    def _looks_like_content_summary_request(message: str) -> bool:
        """判断用户是否在请求文档内容摘要。   Detect whether the user is asking for a content-oriented summary."""

        normalized = " ".join(message.strip().split())
        direct_keywords = (
            "\u603b\u7ed3",
            "\u6982\u62ec",
            "\u6982\u8ff0",
            "\u6458\u8981",
            "\u4e3b\u8981\u5185\u5bb9",
            "\u5185\u5bb9\u603b\u7ed3",
            "\u6587\u6863\u603b\u7ed3",
        )
        question_keywords = (
            "\u8bf4\u4e86\u4ec0\u4e48",
            "\u8bb2\u4e86\u4ec0\u4e48",
            "\u5199\u4e86\u4ec0\u4e48",
            "\u4e3b\u8981\u8bb2\u4ec0\u4e48",
            "\u8bb2\u7684\u662f\u4ec0\u4e48",
            "\u5185\u5bb9\u662f\u4ec0\u4e48",
        )
        return any(keyword in normalized for keyword in (*direct_keywords, *question_keywords))

    def _fallback_summary(
        self,
        blocks: list[DocumentBlock],
        facts: list[FactRecord],
        document_ids: list[str],
    ) -> str:
        """使用更偏主题归纳的兜底摘要。   Generate a topic-oriented fallback summary instead of copying the opening text."""

        scoped_documents = self._get_scoped_documents(document_ids)
        if not scoped_documents:
            return "当前还没有可用于总结的已解析文档。你可以先上传文档，或指定要我概括的文档范围。"

        blocks_by_doc: dict[str, list[DocumentBlock]] = {}
        for block in blocks:
            blocks_by_doc.setdefault(block.doc_id, []).append(block)

        facts_by_doc: dict[str, list[FactRecord]] = {}
        for fact in facts:
            facts_by_doc.setdefault(fact.source_doc_id, []).append(fact)

        summaries: list[str] = []
        for document in scoped_documents[:3]:
            doc_blocks = blocks_by_doc.get(document.doc_id, [])
            doc_facts = facts_by_doc.get(document.doc_id, [])
            title = self._pick_document_title(document, doc_blocks)
            sections = self._collect_section_titles(doc_blocks)
            lead = self._collect_summary_lead(doc_blocks)
            indicators = self._collect_indicator_preview(doc_facts)
            kind = self._infer_document_kind(title)
            topic = self._derive_topic_from_title(title)

            parts = [f"《{title}》"]
            if topic:
                parts.append(f"是一份关于{topic}的{kind}")
            elif kind:
                parts.append(f"是一份{kind}")

            if lead:
                parts.append(f"主要讲的是{lead}")
            elif sections:
                parts.append(f"重点包括{self._format_list_phrase(sections[:4])}")
            else:
                parts.append("已经完成了解析，如需更细的摘要我可以继续按章节展开")

            if indicators:
                parts.append(f"当前识别到的关键指标有{self._format_list_phrase(indicators[:3], delimiter='；')}")

            summaries.append("，".join(parts).rstrip("，") + "。")

        if len(scoped_documents) > 3:
            summaries.append(f"当前范围内共 {len(scoped_documents)} 份文档，其余文档我也可以继续逐份细化摘要。")
        elif not facts:
            summaries.append("这批文档目前还没有稳定的结构化事实，但我已经可以继续按章节总结、检索原文或帮你提取指定字段。")

        return " ".join(summaries).strip()

    def _collect_summary_lead(self, blocks: list[DocumentBlock]) -> str:
        """提取用于兜底摘要的主题开场。   Extract a short thematic lead for fallback summaries."""

        lead_fragments: list[str] = []
        for block in blocks:
            if block.block_type not in {"paragraph", "table_row"}:
                continue
            text = self._clean_summary_text(block.text)
            if len(text) < 12 or self._is_summary_boilerplate(text):
                continue
            lead_fragments.append(text[:48])
            if len(lead_fragments) >= 2:
                break
        if not lead_fragments:
            return ""
        if len(lead_fragments) == 1:
            return lead_fragments[0]
        return f"{lead_fragments[0]}，并提到{lead_fragments[1]}"

    @staticmethod
    def _is_summary_boilerplate(text: str) -> bool:
        """判断是否为摘要中应尽量跳过的公文套话。   Detect boilerplate lines that should be skipped in summaries."""

        boilerplates = (
            "发布时间",
            "发布单位",
            "中华人民共和国",
            "文化和旅游部",
            "发布日期",
            "作者",
            "来源",
        )
        return any(keyword in text for keyword in boilerplates)

    @staticmethod
    def _infer_document_kind(title: str) -> str:
        """推断文档类型。   Infer a document kind from the title."""

        kind_map = (
            ("统计公报", "统计公报"),
            ("公报", "公报"),
            ("报告", "报告"),
            ("通报", "通报"),
            ("方案", "方案"),
            ("白皮书", "白皮书"),
            ("年报", "年度报告"),
            ("季报", "季度报告"),
        )
        for keyword, label in kind_map:
            if keyword in title:
                return label
        return "文档"

    def _derive_topic_from_title(self, title: str) -> str:
        """从标题中提取主题。   Derive a topic phrase from the title."""

        cleaned = self._clean_summary_text(title)
        cleaned = re.sub(r"^\d{4}年", "", cleaned)
        cleaned = re.sub(r"[一二三四五六七八九十]+、", "", cleaned)
        generic_words = ("统计公报", "公报", "报告", "通报", "方案", "白皮书", "年报", "季报")
        for word in generic_words:
            cleaned = cleaned.replace(word, "")
        cleaned = cleaned.strip("关于")
        cleaned = self._clean_summary_text(cleaned)
        return cleaned or title

    @staticmethod
    def _format_list_phrase(items: list[str], delimiter: str = "、") -> str:
        """格式化摘要中的列表短语。   Format list phrases used inside summaries."""

        cleaned = [item.strip() for item in items if item.strip()]
        return delimiter.join(cleaned)

    @staticmethod
    def _fallback_qa(facts: list[FactRecord]) -> str:
        """使用规则方式生成问答回复。    Generate a QA response using deterministic rules."""
        if not facts:
            return "当前事实库中未找到与您问题相关的数据。请先上传相关文档。"
        lines = [f"根据已有数据，找到以下相关事实："]
        for fact in facts[:10]:
            val = format_value(fact.value_num) if fact.value_num is not None else fact.value_text
            lines.append(f"- {fact.entity_name} {fact.field_name}: {val} {fact.unit or ''}")
        return "\n".join(lines)
