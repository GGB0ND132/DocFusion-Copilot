from __future__ import annotations

from pathlib import Path

from app.models.domain import DocumentBlock
from app.parsers.base import DocumentParser
from app.utils.ids import new_id
from app.utils.spreadsheet import load_xlsx


class XlsxParser(DocumentParser):
    """将 XLSX 工作簿解析为按行组织的标准化表格块。
    Parse XLSX workbooks into normalized row-oriented table blocks.
    """

    supported_suffixes = (".xlsx",)

    def parse(self, path: Path, doc_id: str) -> list[DocumentBlock]:
        """将工作表中的行提取为结构化表格行块。
        Extract worksheet rows as structured table-row blocks.
        """
        workbook = load_xlsx(path)
        blocks: list[DocumentBlock] = []
        index = 0

        for sheet in workbook.sheets:
            if not sheet.rows:
                continue
            raw_headers = next((row.values for row in sheet.rows if any(cell.strip() for cell in row.values)), [])
            headers = self._normalize_headers(raw_headers)
            if not headers:
                continue

            for row in sheet.rows[1:]:
                trimmed_values = self._trim_row_values(row.values, len(headers))
                if not any(cell.strip() for cell in trimmed_values):
                    continue
                index += 1
                row_map = {
                    header: trimmed_values[position] if position < len(trimmed_values) else ""
                    for position, header in enumerate(headers)
                }
                blocks.append(
                    DocumentBlock(
                        block_id=new_id("blk"),
                        doc_id=doc_id,
                        block_type="table_row",
                        text=" | ".join(trimmed_values),
                        section_path=[sheet.name],
                        page_or_index=index,
                        metadata={
                            "sheet_name": sheet.name,
                            "headers": headers,
                            "row_values": row_map,
                            "row_index": row.row_index,
                        },
                    )
                )

        return blocks

    @staticmethod
    def _normalize_headers(headers: list[str]) -> list[str]:
        """鍘婚櫎绌虹櫧琛ㄥご骞跺幓鎺夊熬閮ㄧ┖鍒椼€?   Drop blank headers and trim trailing empty columns."""

        normalized = [header.strip() for header in headers]
        while normalized and not normalized[-1]:
            normalized.pop()
        return normalized

    @staticmethod
    def _trim_row_values(values: list[str], header_count: int) -> list[str]:
        """鎸夎〃澶村垪鏁版敹缂╄鍊煎垪琛ㄣ€?   Trim row values to the effective header width."""

        if header_count <= 0:
            return []
        trimmed = list(values[:header_count])
        if len(trimmed) < header_count:
            trimmed.extend("" for _ in range(header_count - len(trimmed)))
        return trimmed
