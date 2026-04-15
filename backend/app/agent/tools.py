"""LangGraph Agent Tools — 将底层 service 封装为 LLM 可调用的工具函数。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ── 工具函数需要访问的依赖会通过闭包注入 ──
# 下面的 create_tools() 工厂函数接收所有依赖并返回绑定了依赖的 tool 列表


def create_tools(
    *,
    repository: Any,
    embedding_service: Any,
    extraction_service: Any,
    template_service: Any,
    trace_service: Any,
    settings: Any,
) -> list:
    """创建绑定了服务依赖的 agent tool 函数列表。"""

    @tool
    def search_facts(
        entity_name: Optional[str] = None,
        field_name: Optional[str] = None,
        year: Optional[int] = None,
        min_confidence: Optional[float] = None,
        limit: int = 20,
    ) -> str:
        """精确查询已从文档中提取的结构化事实数据。

        可按实体名称（如城市名）、字段名称（如GDP总量、常住人口）、年份、最低置信度过滤。
        适合查询具体数值，如"北京的GDP是多少"、"2023年上海常住人口"。
        """
        facts = repository.list_facts(
            entity_name=entity_name,
            field_name=field_name,
            min_confidence=min_confidence,
            canonical_only=True,
        )
        if year is not None:
            facts = [f for f in facts if f.year == year]
        facts = facts[:limit]
        if not facts:
            return "未找到匹配的事实记录。"
        rows = []
        for f in facts:
            rows.append(
                f"- {f.entity_name} | {f.field_name} | {f.value_text} {f.unit or ''} "
                f"| 年份:{f.year} | 置信度:{f.confidence:.2f} | 来源:{f.source_doc_id}"
            )
        return f"找到 {len(facts)} 条事实：\n" + "\n".join(rows)

    @tool
    def vector_search(
        query: str,
        top_k: int = 10,
        document_ids: Optional[list[str]] = None,
    ) -> str:
        """语义搜索文档内容片段。

        根据语义相似度检索与查询最相关的文档段落。
        适合查找文档中关于某个话题的描述，如"关于教育投入的内容"、"环境质量报告的结论部分"。
        """
        if not embedding_service.is_configured:
            return "向量检索未配置（缺少 Embedding API 密钥）。"
        try:
            query_embedding = embedding_service.embed_query(query)
        except Exception as e:
            return f"生成查询向量失败：{e}"
        doc_id_set = set(document_ids) if document_ids else None
        blocks = repository.vector_search_blocks(
            query_embedding, top_k=top_k, document_ids=doc_id_set
        )
        if not blocks:
            return "未找到相关文档片段。"
        results = []
        for b, _score in blocks:
            text_preview = b.text[:300] + ("..." if len(b.text) > 300 else "")
            results.append(f"[{b.doc_id} / {b.block_type}]\n{text_preview}")
        return f"找到 {len(blocks)} 个相关片段：\n\n" + "\n---\n".join(results)

    @tool
    def list_documents(status_filter: Optional[str] = None) -> str:
        """查看系统中已上传的文档列表和处理状态。

        可选按状态过滤：uploaded（已上传）、parsing（解析中）、parsed（已解析）、failed（失败）。
        """
        from app.models.domain import DocumentStatus

        status = DocumentStatus(status_filter) if status_filter else None
        docs = repository.list_documents(status=status)
        if not docs:
            return "当前没有文档。"
        lines = []
        for d in docs:
            meta = d.metadata or {}
            fact_count = meta.get("fact_count", "?")
            lines.append(f"- [{d.status.value}] {d.file_name} (id={d.doc_id}, 事实数={fact_count})")
        return f"共 {len(docs)} 个文档：\n" + "\n".join(lines)

    @tool
    def get_document_content(doc_id: str, page: int = 0, page_size: int = 20) -> str:
        """读取指定文档的内容块（分页）。

        返回文档的解析文本块。page 从 0 开始，page_size 默认 20。
        """
        blocks = repository.list_blocks(doc_id, limit=page_size, offset=page * page_size)
        total = repository.count_blocks(doc_id)
        if not blocks:
            return f"文档 {doc_id} 没有内容块（总数={total}）。"
        lines = []
        for b in blocks:
            text_preview = b.text[:500] + ("..." if len(b.text) > 500 else "")
            lines.append(f"[{b.block_type}] {text_preview}")
        header = f"文档 {doc_id} 内容（第{page + 1}页，共{(total + page_size - 1) // page_size}页，总{total}块）：\n"
        return header + "\n---\n".join(lines)

    @tool
    def edit_document(doc_id: str, replacements: list[dict[str, str]]) -> str:
        """对文档进行文本替换编辑。

        replacements 是一个列表，每项包含 old_text（要替换的原文）和 new_text（替换后的文本）。
        目前支持 .docx 和 .txt 格式。
        """
        from app.utils.wordprocessing import replace_text_in_docx_document

        doc = repository.get_document(doc_id)
        if doc is None:
            return f"文档 {doc_id} 不存在。"
        source_path = Path(doc.stored_path)
        if not source_path.exists():
            return f"文档文件不存在于磁盘。"

        suffix = source_path.suffix.lower()
        output_name = f"edited_{source_path.name}"
        output_path = settings.outputs_dir / output_name

        pairs = [(r["old_text"], r["new_text"]) for r in replacements]

        if suffix == ".docx":
            count = replace_text_in_docx_document(str(source_path), str(output_path), pairs)
        elif suffix in (".txt", ".md"):
            text = source_path.read_text(encoding="utf-8")
            count = 0
            for old, new in pairs:
                if old in text:
                    text = text.replace(old, new, 1)
                    count += 1
            output_path.write_text(text, encoding="utf-8")
        else:
            return f"不支持编辑 {suffix} 格式的文档。"

        return f"编辑完成，共替换 {count} 处。输出文件：{output_name}"

    @tool
    def summarize_documents(doc_ids: list[str], focus_topic: Optional[str] = None) -> str:
        """生成一个或多个文档的内容摘要。

        可指定关注的主题方向。如不指定则生成全文概要。
        """
        all_texts: list[str] = []
        for doc_id in doc_ids:
            blocks = repository.list_blocks(doc_id, limit=50)
            doc = repository.get_document(doc_id)
            doc_name = doc.file_name if doc else doc_id
            text_parts = [f"## {doc_name}"]
            for b in blocks:
                text_parts.append(b.text[:300])
            all_texts.append("\n".join(text_parts))

        if not all_texts:
            return "没有找到指定文档的内容。"

        combined = "\n\n".join(all_texts)
        # 截断以适应 LLM context
        if len(combined) > 8000:
            combined = combined[:8000] + "\n...(内容已截断)"

        prompt = f"请总结以下文档内容"
        if focus_topic:
            prompt += f"，重点关注「{focus_topic}」相关内容"
        prompt += f"：\n\n{combined}"

        return prompt  # LLM 会处理这个返回值并生成摘要

    @tool
    def fill_template(
        template_name: Optional[str] = None,
        document_ids: Optional[list[str]] = None,
        fill_mode: str = "canonical",
        auto_match: bool = True,
        user_requirement: str = "",
    ) -> str:
        """使用已提取的事实数据自动回填 Excel 或 Word 模板。

        模板文件需要已通过前端上传。fill_mode 可选 canonical（仅用最高置信度事实）或 candidate（用所有候选事实）。
        """
        # 模板路径从 state 中传入（通过闭包访问不到 state，由 graph 节点额外处理）
        # 这里返回一个标记，让 graph 节点知道需要启动模板回填任务
        return json.dumps({
            "_action": "fill_template",
            "template_name": template_name,
            "document_ids": document_ids or [],
            "fill_mode": fill_mode,
            "auto_match": auto_match,
            "user_requirement": user_requirement,
        })

    @tool
    def extract_facts(doc_ids: list[str]) -> str:
        """从指定文档中重新提取结构化事实数据。

        对已解析的文档执行事实抽取（实体名、字段名、数值、单位、年份等）。
        """
        total_facts = 0
        for doc_id in doc_ids:
            doc = repository.get_document(doc_id)
            if doc is None:
                continue
            blocks = repository.list_blocks(doc_id)
            if not blocks:
                continue
            facts = extraction_service.extract(doc, blocks)
            if facts:
                repository.add_facts(facts)
                total_facts += len(facts)
        return f"从 {len(doc_ids)} 个文档中提取了 {total_facts} 条事实。"

    @tool
    def trace_fact(fact_id: str) -> str:
        """追溯某个事实的来源文档、原文以及在模板中的使用记录。

        输入 fact_id，返回该事实的完整证据链。
        """
        trace = trace_service.get_fact_trace(fact_id)
        if trace is None:
            return f"未找到事实 {fact_id}。"
        fact = trace["fact"]
        doc = trace.get("document")
        block = trace.get("block")
        usages = trace.get("usages", [])

        lines = [
            f"**事实**: {fact.entity_name} - {fact.field_name} = {fact.value_text} {fact.unit or ''}",
            f"**置信度**: {fact.confidence:.2f}",
            f"**来源文档**: {doc.file_name if doc else '未知'}",
            f"**原文**: {block.text[:200] if block else '未知'}",
        ]
        if usages:
            lines.append(f"**模板使用** ({len(usages)} 次):")
            for u in usages:
                lines.append(f"  - {u['output_file_name']} / {u['sheet_name']}!{u['cell_ref']}")
        return "\n".join(lines)

    return [
        search_facts,
        vector_search,
        list_documents,
        get_document_content,
        edit_document,
        summarize_documents,
        fill_template,
        extract_facts,
        trace_fact,
    ]
