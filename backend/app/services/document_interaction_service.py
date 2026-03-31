from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.config import Settings
from app.core.openai_client import OpenAIClientError, OpenAICompatibleClient
from app.models.domain import DocumentBlock, FactRecord
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


class DocumentInteractionService:
    """处理自然语言驱动的文档操作与内容查询。    Handle natural-language-driven document operations and content queries."""

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

        plan = self._agent_service.chat(message, context_id)
        resolved_document_ids = self._resolve_document_ids(document_ids, document_set_id)
        intent = str(plan["intent"])

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

        return {
            **plan,
            **execution,
        }

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
        """对文本类文档执行简单内容编辑。    Apply simple content edits to text-like documents."""

        raw_edits = plan.get("edits", [])
        edits = [
            (str(item.get("old_text", "")).strip(), str(item.get("new_text", "")).strip())
            for item in raw_edits
            if isinstance(item, dict)
            and str(item.get("old_text", "")).strip()
            and str(item.get("new_text", "")).strip()
        ]
        if not edits:
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

            if document.doc_type == "docx":
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

        summary = f"Edited {len(artifacts)} documents and applied {total_changes} text replacements."
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
        blocks: list[DocumentBlock] = []
        facts = self._repository.list_facts(canonical_only=True, document_ids=set(document_ids))
        for doc_id in document_ids:
            blocks.extend(self._repository.list_blocks(doc_id))

        if self._openai_client.is_configured:
            try:
                summary = self._summarize_with_openai(plan, blocks, facts)
            except OpenAIClientError:
                summary = self._fallback_summary(blocks, facts, document_ids)
        else:
            summary = self._fallback_summary(blocks, facts, document_ids)

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

    def _reformat_documents(self, plan: dict[str, object], document_ids: list[str]) -> dict[str, object]:
        """对支持的文本类文档执行基础格式整理。    Apply basic formatting cleanup to supported text-like documents."""

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
            f"- [{block.block_type}] {' > '.join(block.section_path) if block.section_path else 'root'}: {block.text[:160]}"
            for block in blocks[:12]
        )
        fact_preview = "\n".join(
            f"- {fact.entity_name} / {fact.field_name} = {format_value(fact.value_num) or fact.value_text} {fact.unit or ''}".strip()
            for fact in facts[:20]
        )
        payload = self._openai_client.create_json_completion(
            system_prompt=(
                "你是文档处理后端的摘要模块。"
                "请根据文档块和结构化事实生成简洁中文摘要，禁止编造未给出的事实。"
            ),
            user_prompt=(
                f"用户意图: {plan.get('intent')}\n\n"
                f"文档块:\n{block_preview}\n\n"
                f"事实:\n{fact_preview}\n\n"
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

    def _fallback_summary(
        self,
        blocks: list[DocumentBlock],
        facts: list[FactRecord],
        document_ids: list[str],
    ) -> str:
        """使用规则方式生成摘要。    Generate a summary using deterministic fallback rules."""

        headings = [
            " > ".join(block.section_path)
            for block in blocks
            if block.block_type == "heading" and block.section_path
        ][:5]
        preview_facts = [
            f"{fact.entity_name}{fact.field_name}{format_value(fact.value_num) or fact.value_text}{fact.unit or ''}"
            for fact in facts[:5]
        ]
        sections = "；".join(headings) if headings else "无明显标题结构"
        indicators = "；".join(preview_facts) if preview_facts else "无已抽取指标"
        return (
            f"共处理 {len(document_ids)} 个文档，解析到 {len(blocks)} 个块，抽取 {len(facts)} 条 canonical 事实。"
            f"主要章节：{sections}。指标预览：{indicators}。"
        )

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
