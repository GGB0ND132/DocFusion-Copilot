"""LLM-driven data transformation pipeline for template filling.

Instead of mechanical field matching, this module lets an LLM:
1. Inspect the template schema and source data schemas
2. Generate pandas transformation code (aggregation, merging, filtering)
3. Execute the code safely to produce a result DataFrame
4. Map the DataFrame to template cells
"""
from __future__ import annotations

import json
import logging
import re
import traceback
from collections import defaultdict
from io import StringIO
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from app.core.openai_client import OpenAICompatibleClient
    from app.models.domain import FactRecord
    from app.repositories.base import Repository

logger = logging.getLogger("docfusion.llm_transform")

# ---------------------------------------------------------------------------
# 1. Load source documents as DataFrames
# ---------------------------------------------------------------------------

def load_source_dataframes(
    repository: "Repository",
    document_ids: list[str],
    *,
    max_rows_per_doc: int = 20_000,
) -> list[dict]:
    """Convert documents to DataFrames.  Returns a list of dicts:
    {"doc_id": str, "file_name": str, "df": pd.DataFrame, "source": "blocks"|"facts"|"text"}
    """
    results: list[dict] = []
    for doc_id in document_ids[:5]:
        doc = repository.get_document(doc_id)
        if doc is None:
            continue

        # Try structured blocks first (xlsx tables)
        blocks = repository.list_blocks(doc_id)
        rows: list[dict] = []
        headers: list[str] | None = None
        text_blocks: list[str] = []
        for b in blocks:
            rv = b.metadata.get("row_values") if b.metadata else None
            if rv and isinstance(rv, dict):
                rows.append(rv)
                if headers is None:
                    h = b.metadata.get("headers")
                    if h:
                        headers = list(h)
            elif b.block_type in ("paragraph", "heading") and b.text:
                text_blocks.append(b.text)
        if rows and headers:
            df = pd.DataFrame(rows[:max_rows_per_doc])
            # Reorder columns to match headers, keep extra columns
            ordered = [c for c in headers if c in df.columns]
            extra = [c for c in df.columns if c not in headers]
            df = df[ordered + extra]
            results.append({
                "doc_id": doc_id,
                "file_name": doc.file_name,
                "df": df,
                "source": "blocks",
            })
            continue

        # Fallback: fact records → DataFrame
        facts: list[FactRecord] = repository.list_facts(
            canonical_only=False, document_ids={doc_id},
        )

        # For text-heavy documents (many paragraphs, no structured rows),
        # always prefer direct text extraction over sparse facts.
        # The LLM text extraction will be more complete.
        if text_blocks and len(text_blocks) > 10:
            logger.info(
                "Text-heavy source '%s' (%d text blocks, %d facts) → using text extraction path",
                doc.file_name, len(text_blocks), len(facts),
            )
            results.append({
                "doc_id": doc_id,
                "file_name": doc.file_name,
                "text_blocks": text_blocks,
                "source": "text",
            })
            continue

        if facts:
            entity_data: dict[str, dict[str, object]] = defaultdict(dict)
            for fact in facts:
                key = fact.entity_name or "_unknown_"
                val = fact.value_num if fact.value_num is not None else fact.value_text
                entity_data[key][fact.field_name] = val
            df = pd.DataFrame.from_dict(entity_data, orient="index")
            df.index.name = "entity"
            df = df.reset_index()
            results.append({
                "doc_id": doc_id,
                "file_name": doc.file_name,
                "df": df,
                "source": "facts",
            })
            continue

        # Fallback: collect raw text for LLM extraction later
        if text_blocks:
            results.append({
                "doc_id": doc_id,
                "file_name": doc.file_name,
                "text_blocks": text_blocks,
                "source": "text",
            })
    return results


# ---------------------------------------------------------------------------
# 1b. Rule-based text compression – strip prose, keep data-bearing text
# ---------------------------------------------------------------------------

# Regex: a meaningful numeric token (not bare single digits like section numbers)
_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


def compress_text_blocks(
    text_blocks: list[str],
    *,
    min_numbers: int = 3,
) -> list[str]:
    """Rule-based compression of prose text blocks.

    Strategy:
    1. Drop paragraphs with fewer than *min_numbers* numeric tokens
       (section titles, narrative intros, conclusions).
    2. For kept paragraphs, split into clauses (by ，；。,;) and keep only
       clauses that contain at least one number.  This strips filler prose
       while preserving all data-bearing text intact.
    """
    compressed: list[str] = []

    for blk in text_blocks:
        blk = blk.strip()
        if not blk:
            continue

        # Count meaningful numeric tokens
        nums = [n for n in _NUM_RE.findall(blk) if len(n) > 1]
        if len(nums) < min_numbers:
            continue

        # ── Clause-level filtering ──
        # Split on Chinese punctuation ONLY (not ASCII comma which appears in numbers)
        clauses = re.split(r"[，；。]", blk)
        kept: list[str] = []
        for idx, clause in enumerate(clauses):
            clause = clause.strip()
            if not clause:
                continue
            # Keep clause if it contains a meaningful number
            if _NUM_RE.search(clause) and any(len(n) > 1 for n in _NUM_RE.findall(clause)):
                kept.append(clause)
            elif idx == 0:
                # Always keep the first clause — it contains the entity name
                kept.append(clause)

        line = "，".join(kept)
        if line:
            compressed.append(line)

    original_chars = sum(len(b) for b in text_blocks)
    result_chars = sum(len(b) for b in compressed)
    logger.info(
        "compress_text_blocks: %d→%d blocks, %d→%d chars (%.0f%% reduction)",
        len(text_blocks), len(compressed),
        original_chars, result_chars,
        (1 - result_chars / max(original_chars, 1)) * 100,
    )
    return compressed


def _extract_single_chunk(
    openai_client: "OpenAICompatibleClient",
    chunk_text: str,
    col_desc: str,
    system_prompt: str,
    chunk_idx: int,
    total_chunks: int,
) -> list[dict]:
    """Extract rows from a single text chunk via LLM (thread-safe helper)."""
    prompt = (
        f"目标列名: [{col_desc}]\n\n"
        f"文本:\n{chunk_text}\n\n"
        "请提取所有实体的数据,输出JSON数组。"
    )
    for attempt in range(2):
        try:
            raw = openai_client.create_text_completion(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.0,
            )
            json_match = re.search(r'\[.*\]', raw, re.DOTALL)
            if json_match:
                rows = json.loads(json_match.group())
                if isinstance(rows, list):
                    logger.info("Text extraction chunk %d/%d: extracted %d rows", chunk_idx + 1, total_chunks, len(rows))
                    return rows
        except Exception as exc:
            logger.warning("Text extraction chunk %d/%d attempt %d failed: %s", chunk_idx + 1, total_chunks, attempt + 1, exc)
            if attempt == 0:
                continue
            break
    return []


def extract_text_to_dataframe(
    openai_client: "OpenAICompatibleClient",
    text_blocks: list[str],
    template_columns: list[str],
    *,
    max_chars_per_chunk: int = 3000,
) -> pd.DataFrame:
    """Use LLM to extract structured data from prose text blocks.

    Sends text in character-based chunks **in parallel**, asks LLM to extract
    rows matching template_columns.  Returns a DataFrame with columns matching
    template_columns.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # ── Build character-based chunks ──
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for blk in text_blocks:
        blk = blk.strip()
        if not blk:
            continue
        if buf and buf_len + len(blk) > max_chars_per_chunk:
            chunks.append("\n".join(buf))
            buf = []
            buf_len = 0
        buf.append(blk)
        buf_len += len(blk) + 1
    if buf:
        chunks.append("\n".join(buf))

    logger.info(
        "extract_text_to_dataframe: %d blocks → %d chunks (max %d chars), columns=%s",
        len(text_blocks), len(chunks), max_chars_per_chunk, template_columns,
    )

    col_desc = ", ".join(template_columns)
    system_prompt = (
        "你是高精度数据提取引擎。从文本段落中提取表格数据。\n"
        "规则:\n"
        "1. 每个实体(城市/国家/人物等)提取一行,键必须与目标列名完全一致。\n"
        "2. 数值只保留纯数字(去掉'亿元''万人''元'等单位),保留小数。\n"
        "   如'56,708.71亿元'→56708.71, '2,487.45万人'→2487.45。\n"
        "   注意去掉数字中的逗号: '47,815.5'→47815.5。\n"
        "3. 注意列名中已包含单位提示(如'GDP总量（亿元）'),数值应与该单位对应。\n"
        "4. 文本中找不到的字段填null。即使部分字段缺失也要保留该实体行。\n"
        "5. 按文本出现顺序排列。\n"
        "6. 注意: 同一文档中可能存在多种描述格式(如详细段落 vs 简短列举),\n"
        "   所有格式的实体都要提取，不要遗漏。\n"
        "7. 只输出JSON数组,不要附加任何解释文字。"
    )

    # ── Parallel extraction: send all chunks to LLM concurrently ──
    all_rows: list[dict] = []
    if len(chunks) == 1:
        # Single chunk: no thread overhead
        all_rows = _extract_single_chunk(openai_client, chunks[0], col_desc, system_prompt, 0, 1)
    else:
        chunk_rows: dict[int, list[dict]] = {}
        with ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as executor:
            futures = {
                executor.submit(
                    _extract_single_chunk, openai_client, chunk_text,
                    col_desc, system_prompt, ci, len(chunks),
                ): ci
                for ci, chunk_text in enumerate(chunks)
            }
            for future in as_completed(futures):
                ci = futures[future]
                try:
                    chunk_rows[ci] = future.result()
                except Exception as exc:
                    logger.warning("Text extraction chunk %d/%d thread failed: %s", ci + 1, len(chunks), exc)
                    chunk_rows[ci] = []
        # Merge in chunk order to preserve text appearance order
        for ci in sorted(chunk_rows.keys()):
            all_rows.extend(chunk_rows[ci])

    if not all_rows:
        return pd.DataFrame(columns=template_columns)

    df = pd.DataFrame(all_rows)
    # Ensure all template columns exist
    for col in template_columns:
        if col not in df.columns:
            df[col] = None
    # Convert numeric columns
    for col in template_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="ignore")
    logger.info("extract_text_to_dataframe: total %d rows extracted", len(df))
    return df[template_columns]


# ---------------------------------------------------------------------------
# 2. Build template schema description
# ---------------------------------------------------------------------------

def describe_template_schema(template_path, *, max_sample_rows: int = 3) -> dict:
    """Return a dict with 'columns', 'sample_rows', and 'description' of the template."""
    suffix = str(template_path).rsplit(".", 1)[-1].lower()
    if suffix == "xlsx":
        return _describe_xlsx_template(template_path, max_sample_rows)
    elif suffix == "docx":
        return _describe_docx_template(template_path, max_sample_rows)
    return {"columns": [], "sample_rows": [], "description": "Unknown format"}


def _describe_xlsx_template(template_path, max_sample_rows: int) -> dict:
    from app.utils.spreadsheet import load_xlsx
    wb = load_xlsx(template_path)
    all_cols: list[str] = []
    sample_rows: list[dict] = []
    for sheet in wb.sheets:
        if not sheet.rows:
            continue
        # Find header row (first row) — SpreadsheetRow has .values: list[str]
        header_row = sheet.rows[0]
        cols = [v.strip() for v in header_row.values if v and v.strip()]
        if not cols:
            continue
        all_cols = cols
        # Collect sample data rows
        for row in sheet.rows[1 : 1 + max_sample_rows]:
            row_data = {}
            for i, val in enumerate(row.values):
                if i < len(cols) and cols[i]:
                    row_data[cols[i]] = val
            sample_rows.append(row_data)
        break  # Only first sheet for now
    return {
        "columns": all_cols,
        "sample_rows": sample_rows,
        "description": f"Excel template with columns: {all_cols}",
    }


def _describe_docx_template(template_path, max_sample_rows: int) -> dict:
    from app.utils.wordprocessing import load_docx_tables
    doc = load_docx_tables(template_path)
    tables_info: list[dict] = []
    all_cols: list[str] = []
    for table in doc.tables:
        if not table.rows:
            continue
        header_row = table.rows[0]
        if hasattr(header_row, 'values'):
            cols = [v.strip() for v in header_row.values if v and v.strip()]
        elif hasattr(header_row, 'cells'):
            cols = [str(c.value or '').strip() for c in header_row.cells if c.value]
        else:
            cols = []
        if not cols:
            continue
        if not all_cols:
            all_cols = cols
        ctx = getattr(table, "context_text", "") or ""
        tables_info.append({
            "table_index": getattr(table, "table_index", len(tables_info)),
            "columns": cols,
            "context_text": ctx[:300],
            "data_rows": len(table.rows) - 1,
        })
    description = f"Word template with {len(tables_info)} table(s)."
    if tables_info:
        for ti in tables_info:
            description += f"\n  Table {ti['table_index']}: columns={ti['columns']}"
            if ti['context_text']:
                description += f", context=「{ti['context_text'][:100]}」"
    return {
        "columns": all_cols,
        "sample_rows": [],
        "description": description,
        "tables": tables_info,
    }


# ---------------------------------------------------------------------------
# 3. Describe source DataFrames for LLM / Direct text extraction
# ---------------------------------------------------------------------------

def _extract_text_as_csv(
    openai_client: "OpenAICompatibleClient",
    text_blocks: list[str],
    template_columns: list[str],
) -> pd.DataFrame | None:
    """Extract structured data from prose text by asking LLM to output CSV.

    Sends the **full text** in a single call and requests compact CSV output.
    For 100 rows × 5 columns, CSV is only ~5 KB of output — well within model
    limits and much faster than generating 100 rows of JSON.
    """
    full_text = "\n".join(b.strip() for b in text_blocks if b.strip())
    col_header = ",".join(template_columns)

    system_prompt = (
        "你是高精度数据提取引擎。从文本中提取所有实体的结构化数据。\n"
        "规则:\n"
        "1. 严格按 CSV 格式输出：第一行是表头，之后每行一个实体。\n"
        "2. 字段间用逗号分隔，不要加引号(除非值本身包含逗号)。\n"
        "3. 数值只保留纯数字(去掉 亿元/万人/元 等单位)，保留小数。\n"
        "   例如 '56,708.71亿元' → 56708.71；'2,487.45万' → 2487.45。\n"
        "4. 注意列名中的单位提示：GDP总量（亿元） → 数字应为亿元级别。\n"
        "5. 找不到的值填空(两个逗号之间不写内容)。\n"
        "6. 按文本出现顺序排列，不要遗漏任何实体。\n"
        "7. 注意：文本中早期城市是长段描述(可能跨句)，后期城市是简短列举，\n"
        "   两种格式都要提取，不要遗漏。\n"
        "8. 只输出 CSV，开头不要任何解释文字。"
    )

    prompt = f"目标列名: {col_header}\n\n文本:\n{full_text}\n\n请提取所有实体数据，输出CSV。"

    try:
        raw = openai_client.create_text_completion(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning("_extract_text_as_csv: LLM call failed: %s", exc)
        return None

    # Parse CSV response
    # Strip markdown code fences if present
    csv_text = raw.strip()
    if csv_text.startswith("```"):
        csv_text = re.sub(r"^```\w*\n?", "", csv_text)
        csv_text = re.sub(r"\n?```$", "", csv_text)
    csv_text = csv_text.strip()

    if not csv_text:
        return None

    try:
        df = pd.read_csv(StringIO(csv_text))
    except Exception as exc:
        logger.warning("_extract_text_as_csv: CSV parse failed: %s", exc)
        # Try fixing common issues: extra whitespace in headers
        lines = csv_text.split("\n")
        if lines:
            lines[0] = ",".join(h.strip() for h in lines[0].split(","))
            try:
                df = pd.read_csv(StringIO("\n".join(lines)))
            except Exception:
                return None

    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]

    # Ensure template columns exist
    for col in template_columns:
        if col not in df.columns:
            df[col] = None

    # Convert numeric columns
    for col in template_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="ignore")

    logger.info(
        "_extract_text_as_csv: parsed %d rows, columns=%s",
        len(df), list(df.columns),
    )
    return df[template_columns] if not df.empty else None


def describe_text_source(text_blocks: list[str], file_name: str, df_index: int) -> str:
    """Describe a text-based source for the LLM, including representative samples.

    The source is exposed as ``df_{df_index}`` with a single ``content`` column
    whose rows are original paragraphs.  We include representative text samples
    (head + middle + tail) so the LLM can see all format variations without
    sending the full text (which would make the prompt too large and slow).
    """
    total_chars = sum(len(b) for b in text_blocks)
    n = len(text_blocks)
    buf = StringIO()
    buf.write(f"文件: {file_name} (文本类文档,非结构化数据)\n")
    buf.write(f"段落数: {n}, 总字符数: {total_chars}\n")
    buf.write(
        f"变量名: df_{df_index} — 该DataFrame有1列 'content'，每行是一个原始段落(共{n}行)。\n"
        f"你需要遍历 df_{df_index}['content'] 中的所有段落，提取模板需要的字段。\n"
        f"注意：即使某些段落格式不同或部分字段缺失，也要保留该行，缺失字段填 None。\n\n"
    )

    # Show representative samples: first 8, middle 4, last 4
    MAX_CHARS = 6000
    char_budget = MAX_CHARS
    buf.write("代表性文本样本:\n---\n")
    # Head: first 8 paragraphs
    head_n = min(8, n)
    for i in range(head_n):
        line = text_blocks[i][:500]
        if char_budget <= 0:
            break
        buf.write(f"[段落{i}] {line}\n")
        char_budget -= len(line) + 20
    # Middle: 4 paragraphs from middle
    if n > 16:
        mid_start = n // 2 - 2
        buf.write(f"\n... (省略段落 {head_n} ~ {mid_start-1}) ...\n\n")
        for i in range(mid_start, min(mid_start + 4, n)):
            line = text_blocks[i][:500]
            if char_budget <= 0:
                break
            buf.write(f"[段落{i}] {line}\n")
            char_budget -= len(line) + 20
    # Tail: last 4 paragraphs
    if n > 20:
        tail_start = max(n - 4, 0)
        buf.write(f"\n... (省略段落 ~ {tail_start-1}) ...\n\n")
        for i in range(tail_start, n):
            line = text_blocks[i][:500]
            if char_budget <= 0:
                break
            buf.write(f"[段落{i}] {line}\n")
            char_budget -= len(line) + 20
    buf.write("---\n")
    return buf.getvalue()


def describe_dataframe(df: pd.DataFrame, file_name: str, *, max_sample: int = 5) -> str:
    """Produce a concise text description of a DataFrame for the LLM prompt."""
    buf = StringIO()
    buf.write(f"文件: {file_name}\n")
    buf.write(f"行数: {len(df)}, 列数: {len(df.columns)}\n")
    buf.write(f"列名: {list(df.columns)}\n")
    buf.write(f"数据类型:\n{df.dtypes.to_string()}\n")
    buf.write(f"\n前{min(max_sample, len(df))}行样本:\n")
    buf.write(df.head(max_sample).to_string(index=False))
    buf.write("\n")
    # Show unique value counts for likely categorical columns
    for col in df.columns:
        nuniq = df[col].nunique()
        if 1 < nuniq <= 30:
            vals = df[col].dropna().unique()[:15]
            buf.write(f"\n列 '{col}' 的不同值({nuniq}个): {list(vals)}")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 4. Generate transformation code via LLM
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
你是数据转换专家。你的任务是根据用户指令，将源数据转换为符合模板结构的 DataFrame。

## 规则
1. 只使用 pandas (pd)、numpy (np) 和 re，不要导入其他库。
2. 源数据 DataFrame 变量名为 df_0, df_1, ... (按顺序)，已预先加载。
3. 最终结果必须赋值给变量 `result_df`，它是一个 pandas DataFrame。
   - 如果模板有多张表(如docx中3个表各自有筛选条件)，则分别生成 result_df_0, result_df_1, result_df_2 ...
   - 同时也赋值 result_df = result_df_0 (作为默认)。
4. `result_df` 的列名必须与模板列名完全一致。
5. 仔细分析模板结构：
   - 如果模板没有日期/时间列，说明需要按实体（如国家）聚合多天的数据。
   - 聚合策略：数值列用 sum 求总和（如病例数、检测数），人均指标和人口用 first 或 mean。
   - 如果源数据包含省/州级数据，但模板只有国家列，需要汇总到国家级别。
6. 日期过滤：如果用户指定了日期范围，先过滤再聚合。日期列可能是字符串格式。
7. 多源数据合并：如果有多个 DataFrame，先分别处理再用 concat/merge 合并。
8. 处理数据类型：数值列先用 pd.to_numeric(errors='coerce') 转换。
9. 结果不要包含全部为 NaN 的行。部分字段缺失的行应保留(缺失列填 NaN)。
10. 对于文本类源数据（df 只有 'content' 列），你需要用 re 正则或字符串操作解析每段文本。
    - re 模块已注入，可直接使用 re.search、re.findall 等。
    - **重要**: 同一文档中可能存在多种文本格式（如详细描述 vs 简短列举），都要处理。
    - **重要**: 不要使用 `if all([...])` 来过滤行。即使某些字段提取失败，也要保留该行，缺失字段填 None。
    - 数值中可能包含逗号(如 56,708.71)，注意去掉逗号后转 float。
    - 用多种正则模式匹配不同格式，确保不遗漏任何实体。
11. 只输出 Python 代码，不要包含任何解释文字。用 ```python 和 ``` 包裹代码。
"""


def generate_transform_code(
    openai_client: "OpenAICompatibleClient",
    *,
    template_schema: dict,
    source_descriptions: list[str],
    user_instruction: str,
) -> str:
    """Ask LLM to generate pandas transformation code."""
    user_parts = [
        "## 模板结构",
        f"列名: {template_schema['columns']}",
    ]
    if template_schema.get("sample_rows"):
        user_parts.append(f"样本行: {template_schema['sample_rows']}")
    # Multi-table docx: include per-table info
    if template_schema.get("tables"):
        user_parts.append(f"\n模板包含 {len(template_schema['tables'])} 张表:")
        for ti in template_schema["tables"]:
            ctx = ti.get("context_text", "")
            user_parts.append(
                f"  表{ti['table_index']}: 列名={ti['columns']}, 数据行数={ti.get('data_rows', '?')}"
                + (f", 上下文描述=「{ctx[:150]}」" if ctx else "")
            )
        user_parts.append(
            "请为每张表分别生成 result_df_0, result_df_1, ... 并设 result_df = result_df_0。"
            "每张表的筛选条件从上下文描述中提取。"
        )

    user_parts.append("\n## 源数据")
    for i, desc in enumerate(source_descriptions):
        user_parts.append(f"\n### df_{i}\n{desc}")

    user_parts.append(f"\n## 用户指令\n{user_instruction}")

    user_prompt = "\n".join(user_parts)

    raw = openai_client.create_text_completion(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
    )
    return _extract_python_code(raw)


def _extract_python_code(text: str) -> str:
    """Extract code from markdown code blocks or return as-is."""
    # Try ```python ... ``` blocks
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try ``` ... ``` blocks
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Assume the whole thing is code
    return text.strip()


# ---------------------------------------------------------------------------
# 5. Execute transform code safely
# ---------------------------------------------------------------------------

def execute_transform_safely(
    code: str,
    dataframes: list[pd.DataFrame],
    *,
    timeout_seconds: float = 30.0,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """Execute LLM-generated pandas code in a restricted namespace.
    Returns result_df, or a dict mapping 'result_df_0' etc. to DataFrames for multi-table."""
    restricted_globals: dict = {
        "__builtins__": {
            "range": range,
            "len": len,
            "str": str,
            "int": int,
            "float": float,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "sorted": sorted,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "isinstance": isinstance,
            "print": print,
            "True": True,
            "False": False,
            "None": None,
        },
        "pd": pd,
        "np": np,
        "re": re,
    }
    # Inject source DataFrames
    for i, df in enumerate(dataframes):
        restricted_globals[f"df_{i}"] = df.copy()

    # Strip import statements — pd and np are already injected
    code = re.sub(r'^\s*import\s+\w+.*$', '', code, flags=re.MULTILINE)
    code = re.sub(r'^\s*from\s+\w+\s+import.*$', '', code, flags=re.MULTILINE)

    local_ns: dict = {}
    try:
        exec(code, restricted_globals, local_ns)  # noqa: S102
    except Exception as exc:
        logger.error("LLM transform code execution failed:\n%s\nError: %s", code, exc)
        raise ValueError(f"LLM 生成的代码执行失败: {exc}") from exc

    result_df = local_ns.get("result_df")
    if result_df is None:
        result_df = restricted_globals.get("result_df")
    if not isinstance(result_df, pd.DataFrame):
        raise ValueError("LLM 代码未生成 result_df DataFrame")
    if result_df.empty:
        raise ValueError("LLM 代码生成的 result_df 为空")

    # Check for per-table DataFrames (result_df_0, result_df_1, ...)
    per_table: dict[int, pd.DataFrame] = {}
    for key in list(local_ns.keys()) + list(restricted_globals.keys()):
        if key.startswith("result_df_") and key != "result_df":
            suffix = key[len("result_df_"):]
            if suffix.isdigit():
                val = local_ns.get(key) or restricted_globals.get(key)
                if isinstance(val, pd.DataFrame) and not val.empty:
                    per_table[int(suffix)] = val

    if per_table:
        per_table[-1] = result_df  # marker: -1 = default result_df
        return per_table  # type: ignore[return-value]

    return result_df


# ---------------------------------------------------------------------------
# 6. Write DataFrame to template
# ---------------------------------------------------------------------------

def write_dataframe_to_xlsx(
    template_path,
    output_path,
    result_df: pd.DataFrame,
    template_columns: list[str],
) -> list:
    """Write a DataFrame into an xlsx template, returning FilledCellRecord list."""
    from app.models.domain import FilledCellRecord
    from app.utils.spreadsheet import CellWrite, apply_xlsx_updates, build_cell_ref, load_xlsx

    wb = load_xlsx(template_path)
    all_updates: list[CellWrite] = []
    all_filled: list[FilledCellRecord] = []

    for sheet in wb.sheets:
        if not sheet.rows:
            continue
        # Find header row — SpreadsheetRow has .values: list[str]
        header_row = sheet.rows[0]
        col_names = [str(v or "").strip() for v in header_row.values]
        # Map template columns to cell column indices
        col_map: dict[str, int] = {}
        for idx, name in enumerate(col_names):
            if name in result_df.columns:
                col_map[name] = idx

        if not col_map:
            continue

        # Determine the starting data row
        data_start_row = header_row.row_index + 1

        for row_idx, (_, data_row) in enumerate(result_df.iterrows()):
            target_row = data_start_row + row_idx
            for col_name, col_idx in col_map.items():
                raw_val = data_row.get(col_name)
                if raw_val is None or (isinstance(raw_val, float) and np.isnan(raw_val)):
                    continue
                cell_value: str | float | int = raw_val
                if isinstance(raw_val, (np.integer,)):
                    cell_value = int(raw_val)
                elif isinstance(raw_val, (np.floating, float)):
                    cell_value = float(raw_val)
                    if cell_value == int(cell_value):
                        cell_value = int(cell_value)
                else:
                    cell_value = str(raw_val)

                cell_ref = build_cell_ref(target_row, col_idx + 1)  # 1-based column index
                all_updates.append(CellWrite(
                    sheet_name=sheet.name,
                    cell_ref=cell_ref,
                    value=cell_value,
                ))
                all_filled.append(FilledCellRecord(
                    sheet_name=sheet.name,
                    cell_ref=cell_ref,
                    entity_name=str(data_row.get(col_names[0], "")),
                    field_name=col_name,
                    value=cell_value,
                    fact_id="llm_transform",
                    confidence=1.0,
                    evidence_text=f"LLM transform: {col_name}={raw_val}",
                ))
        break  # Only first sheet

    if all_updates:
        apply_xlsx_updates(template_path, output_path, all_updates)
    return all_filled


def write_dataframe_to_docx(
    template_path,
    output_path,
    result_df: pd.DataFrame,
    template_columns: list[str],
    *,
    per_table_dfs: dict[int, pd.DataFrame] | None = None,
) -> list:
    """Write DataFrame(s) into a docx template, returning FilledCellRecord list.
    
    If per_table_dfs is provided, each table uses its own DataFrame.
    Otherwise, result_df is used for all matching tables.
    """
    from app.models.domain import FilledCellRecord
    from app.utils.wordprocessing import WordCellWrite, apply_docx_updates, load_docx_tables

    doc = load_docx_tables(template_path)
    all_updates: list[WordCellWrite] = []
    all_filled: list[FilledCellRecord] = []

    for table in doc.tables:
        if not table.rows:
            continue
        header_row = table.rows[0]
        col_names = [str(c.value or "").strip() for c in header_row.cells]

        # Pick the DataFrame for this table
        t_idx = getattr(table, "table_index", 0)
        if per_table_dfs and t_idx in per_table_dfs:
            df_for_table = per_table_dfs[t_idx]
        else:
            df_for_table = result_df

        col_map: dict[str, int] = {}
        for idx, name in enumerate(col_names):
            if name in df_for_table.columns:
                col_map[name] = idx
        if not col_map:
            continue

        data_start_row = header_row.row_index + 1
        for row_idx, (_, data_row) in enumerate(df_for_table.iterrows()):
            target_row = data_start_row + row_idx
            for col_name, col_idx in col_map.items():
                raw_val = data_row.get(col_name)
                if raw_val is None or (isinstance(raw_val, float) and np.isnan(raw_val)):
                    continue
                cell_value = raw_val
                if isinstance(raw_val, (np.integer,)):
                    cell_value = int(raw_val)
                elif isinstance(raw_val, (np.floating, float)):
                    cell_value = float(raw_val)
                else:
                    cell_value = str(raw_val)

                all_updates.append(WordCellWrite(
                    table_index=t_idx,
                    row_index=target_row,
                    column_index=col_idx,
                    value=cell_value,
                ))
                all_filled.append(FilledCellRecord(
                    sheet_name="",
                    cell_ref=f"T{t_idx}R{target_row}C{col_idx}",
                    entity_name=str(data_row.get(col_names[0], "")),
                    field_name=col_name,
                    value=cell_value,
                    fact_id="llm_transform",
                    confidence=1.0,
                    evidence_text=f"LLM transform: {col_name}={raw_val}",
                ))

    if all_updates:
        apply_docx_updates(template_path, output_path, all_updates)
    return all_filled


# ---------------------------------------------------------------------------
# 7. Orchestrator: full LLM transform pipeline
# ---------------------------------------------------------------------------

def run_llm_transform_pipeline(
    *,
    openai_client: "OpenAICompatibleClient",
    repository: "Repository",
    template_path,
    output_path,
    document_ids: list[str],
    user_requirement: str,
) -> list | None:
    """Run the full LLM transform pipeline.
    Returns a list of FilledCellRecord on success, or None if not applicable.
    """
    if not openai_client.is_configured:
        logger.info("LLM transform skipped: OpenAI client not configured")
        return None

    # Step 1: Load source DataFrames
    sources = load_source_dataframes(repository, document_ids)
    if not sources:
        logger.info("LLM transform skipped: no source DataFrames loaded")
        return None

    # Step 2: Describe template
    template_schema = describe_template_schema(template_path)
    if not template_schema["columns"]:
        logger.info("LLM transform skipped: no template columns detected")
        return None

    # Step 1b: For text sources, use direct LLM extraction (faster than code gen).
    # The LLM reads the text directly and extracts structured data as JSON.
    text_source_idx: list[int] = []
    for i, src in enumerate(sources):
        if src.get("source") != "text" or not src.get("text_blocks"):
            continue
        text_blocks = src["text_blocks"]
        total_chars = sum(len(b) for b in text_blocks)
        logger.info(
            "Text source '%s': %d paragraphs, %d chars → direct extraction",
            src["file_name"], len(text_blocks), total_chars,
        )
        text_source_idx.append(i)

    # If there are text sources, compress then extract them directly with LLM
    if text_source_idx:
        for i in text_source_idx:
            src = sources[i]
            try:
                raw_blocks = src["text_blocks"]
                # ── Rule-based compression: strip prose, keep data ──
                compressed = compress_text_blocks(raw_blocks)
                blocks_to_use = compressed if compressed else raw_blocks
                df = extract_text_to_dataframe(
                    openai_client,
                    blocks_to_use,
                    template_schema["columns"],
                    max_chars_per_chunk=2500,  # small chunks to stay within DeepSeek gateway timeout
                )
                if df is not None and not df.empty:
                    sources[i]["df"] = df
                    sources[i]["source"] = "text_extracted"
                    logger.info(
                        "Direct text extraction for '%s': %d rows",
                        src["file_name"], len(df),
                    )
                else:
                    logger.warning("Direct text extraction returned empty for '%s'", src["file_name"])
            except Exception as exc:
                logger.warning("Direct text extraction failed for '%s': %s", src["file_name"], exc)

    # For any remaining text sources that weren't extracted, convert to content DataFrame for code gen
    for i, src in enumerate(sources):
        if src.get("source") != "text" or not src.get("text_blocks"):
            continue
        text_blocks = src["text_blocks"]
        logger.info(
            "Text source '%s': fallback to content DataFrame for code gen",
            src["file_name"],
        )
        sources[i]["df"] = pd.DataFrame({"content": text_blocks})
        sources[i]["source"] = "text_as_df"

    # Filter out sources that still have no DataFrame
    sources = [s for s in sources if "df" in s]
    if not sources:
        logger.info("LLM transform skipped: no usable source DataFrames")
        return None

    # Step 3-5: If all sources are already extracted (no text_as_df needing code gen),
    # skip code generation entirely and use the DataFrames directly.
    need_code_gen = any(s.get("source") == "text_as_df" for s in sources)

    if not need_code_gen and len(sources) == 1:
        # Single source already extracted: use its DataFrame directly
        result_df = sources[0]["df"]
        per_table_dfs = None
        logger.info(
            "LLM transform: using directly extracted DataFrame (no code gen needed), shape=%s",
            result_df.shape,
        )
    else:
        # Step 3: Describe source data (use text description for text sources)
        source_descriptions: list[str] = []
        for idx, s in enumerate(sources):
            if s.get("source") == "text_as_df" and s.get("text_blocks"):
                source_descriptions.append(
                    describe_text_source(s["text_blocks"], s["file_name"], idx)
                )
            else:
                source_descriptions.append(describe_dataframe(s["df"], s["file_name"]))

        # Step 4: Generate transform code
        logger.info(
            "LLM transform: generating code for %d sources → template(%s)",
            len(sources), template_schema["columns"],
        )
        code = generate_transform_code(
            openai_client,
            template_schema=template_schema,
            source_descriptions=source_descriptions,
            user_instruction=user_requirement or "将源数据填入模板",
        )
        logger.info("LLM transform: generated code:\n%s", code)

        # Step 5: Execute
        try:
            exec_result = execute_transform_safely(
                code, [s["df"] for s in sources],
            )
        except ValueError as exc:
            logger.warning("LLM transform execution failed: %s", exc)
            return None

        # Handle per-table results (dict) vs single DataFrame
        per_table_dfs = None
        if isinstance(exec_result, dict):
            per_table_dfs = {k: v for k, v in exec_result.items() if k >= 0}
            result_df = exec_result.get(-1) or next(iter(per_table_dfs.values()))
            logger.info(
                "LLM transform: per-table results: %s",
                {k: f"shape={v.shape}" for k, v in per_table_dfs.items()},
            )
        else:
            result_df = exec_result

    logger.info("LLM transform: result_df shape=%s, columns=%s", result_df.shape, list(result_df.columns))

    # Step 6: Write to template
    suffix = str(template_path).rsplit(".", 1)[-1].lower()
    try:
        if suffix == "xlsx":
            filled = write_dataframe_to_xlsx(
                template_path, output_path, result_df, template_schema["columns"],
            )
        elif suffix == "docx":
            filled = write_dataframe_to_docx(
                template_path, output_path, result_df, template_schema["columns"],
                per_table_dfs=per_table_dfs,
            )
        else:
            return None
        logger.info("LLM transform: wrote %d filled cells to template", len(filled))
        return filled if filled else None
    except Exception as exc:
        logger.error("LLM transform: write to template failed: %s", exc, exc_info=True)
        return None
