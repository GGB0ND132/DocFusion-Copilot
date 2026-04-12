"""LLM-driven data transformation pipeline for template filling.

Instead of mechanical field matching, this module lets an LLM:
1. Inspect the template schema and source data schemas
2. Generate pandas transformation code (aggregation, merging, filtering)
3. Execute the code safely to produce a result DataFrame
4. Map the DataFrame to template cells
"""
from __future__ import annotations

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
    {"doc_id": str, "file_name": str, "df": pd.DataFrame, "source": "blocks"|"facts"}
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
        for b in blocks:
            rv = b.metadata.get("row_values") if b.metadata else None
            if rv and isinstance(rv, dict):
                rows.append(rv)
                if headers is None:
                    h = b.metadata.get("headers")
                    if h:
                        headers = list(h)
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
    return results


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
    all_cols: list[str] = []
    sample_rows: list[dict] = []
    for table in doc.tables:
        if not table.rows:
            continue
        header_row = table.rows[0]
        # WordTable rows may have .values or .cells with .value
        if hasattr(header_row, 'values'):
            cols = [v.strip() for v in header_row.values if v and v.strip()]
        elif hasattr(header_row, 'cells'):
            cols = [str(c.value or '').strip() for c in header_row.cells if c.value]
        else:
            cols = []
        if cols:
            all_cols = cols
            break
    return {
        "columns": all_cols,
        "sample_rows": sample_rows,
        "description": f"Word template with columns: {all_cols}",
    }


# ---------------------------------------------------------------------------
# 3. Describe source DataFrames for LLM
# ---------------------------------------------------------------------------

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
1. 只使用 pandas (pd) 和 numpy (np)，不要导入其他库。
2. 源数据 DataFrame 变量名为 df_0, df_1, ... (按顺序)，已预先加载。
3. 最终结果必须赋值给变量 `result_df`，它是一个 pandas DataFrame。
4. `result_df` 的列名必须与模板列名完全一致。
5. 仔细分析模板结构：
   - 如果模板没有日期/时间列，说明需要按实体（如国家）聚合多天的数据。
   - 聚合策略：数值列用 sum 求总和（如病例数、检测数），人均指标和人口用 first 或 mean。
   - 如果源数据包含省/州级数据，但模板只有国家列，需要汇总到国家级别。
     - 对中国省份数据：人口取 sum，人均GDP 取加权平均或 mean，病例数/检测数取 sum。
6. 日期过滤：如果用户指定了日期范围，先过滤再聚合。日期列可能是字符串格式。
7. 多源数据合并：如果有多个 DataFrame，先分别处理再用 concat/merge 合并。
   - 合并时注意列名对齐，缺失列填 NaN。
8. 处理数据类型：数值列先用 pd.to_numeric(errors='coerce') 转换。
9. 结果不要包含 NaN 行，用 dropna 或 fillna 处理。
10. 只输出 Python 代码，不要包含任何解释文字。用 ```python 和 ``` 包裹代码。
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
) -> pd.DataFrame:
    """Execute LLM-generated pandas code in a restricted namespace.
    Returns the resulting DataFrame or raises on failure."""
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
    }
    # Inject source DataFrames
    for i, df in enumerate(dataframes):
        restricted_globals[f"df_{i}"] = df.copy()

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
) -> list:
    """Write a DataFrame into a docx template, returning FilledCellRecord list."""
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
        col_map: dict[str, int] = {}
        for idx, name in enumerate(col_names):
            if name in result_df.columns:
                col_map[name] = idx
        if not col_map:
            continue

        data_start_row = header_row.row_index + 1
        for row_idx, (_, data_row) in enumerate(result_df.iterrows()):
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
                    table_index=table.table_index,
                    row_index=target_row,
                    column_index=col_idx,
                    value=cell_value,
                ))
                all_filled.append(FilledCellRecord(
                    sheet_name="",
                    cell_ref=f"T{table.table_index}R{target_row}C{col_idx}",
                    entity_name=str(data_row.get(col_names[0], "")),
                    field_name=col_name,
                    value=cell_value,
                    fact_id="llm_transform",
                    confidence=1.0,
                    evidence_text=f"LLM transform: {col_name}={raw_val}",
                ))
        break

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

    # Step 3: Describe source data
    source_descriptions = [
        describe_dataframe(s["df"], s["file_name"]) for s in sources
    ]

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
        result_df = execute_transform_safely(
            code, [s["df"] for s in sources],
        )
    except ValueError as exc:
        logger.warning("LLM transform execution failed: %s", exc)
        return None

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
            )
        else:
            return None
        logger.info("LLM transform: wrote %d filled cells to template", len(filled))
        return filled if filled else None
    except Exception as exc:
        logger.error("LLM transform: write to template failed: %s", exc, exc_info=True)
        return None
