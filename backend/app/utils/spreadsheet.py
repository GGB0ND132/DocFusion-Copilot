from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WORKBOOK_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

NS = {
    "main": MAIN_NS,
    "rel": PACKAGE_REL_NS,
}

ET.register_namespace("", MAIN_NS)
ET.register_namespace("r", WORKBOOK_REL_NS)

_CELL_REF_RE = re.compile(r"(?P<column>[A-Z]+)(?P<row>\d+)")
_XML_DECLARATION_RE = re.compile(r"^\s*(<\?xml[^>]+\?>)")
_WORKSHEET_ROOT_TAG_RE = re.compile(r"<worksheet\b[^>]*>")
_XMLNS_DECLARATION_RE = re.compile(r'\sxmlns(?::(?P<prefix>[\w.-]+))?="(?P<uri>[^"]+)"')


@dataclass(slots=True)
class SpreadsheetRow:
    """工作表中单行的内存表示。    In-memory representation of one worksheet row."""

    row_index: int
    values: list[str]


@dataclass(slots=True)
class SpreadsheetSheet:
    """单个工作表的内存表示。    In-memory representation of one worksheet."""

    name: str
    rows: list[SpreadsheetRow]


@dataclass(slots=True)
class SpreadsheetDocument:
    """XLSX 工作簿的内存表示。    In-memory representation of an XLSX workbook."""

    sheets: list[SpreadsheetSheet]


@dataclass(slots=True)
class CellWrite:
    """单个工作簿单元格的计划写入操作。    Planned write operation for one workbook cell."""

    sheet_name: str
    cell_ref: str
    value: str | float | int


def _q(tag: str) -> str:
    """为 SpreadsheetML 标签补齐主工作表命名空间。    Qualify a SpreadsheetML tag with the main worksheet namespace."""
    return f"{{{MAIN_NS}}}{tag}"


def column_letters_to_index(column_letters: str) -> int:
    """将 Excel 列字母如 `AB` 转换为从 1 开始的列索引。    Convert Excel column letters like `AB` into a 1-based index."""
    value = 0
    for char in column_letters:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def index_to_column_letters(index: int) -> str:
    """将从 1 开始的列索引转换为 Excel 列字母。    Convert a 1-based column index into Excel letters."""
    result = []
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result.append(chr(ord("A") + remainder))
    return "".join(reversed(result))


def split_cell_ref(cell_ref: str) -> tuple[int, int]:
    """将单元格引用拆分为行号和列号。    Split a cell reference into row and column indices."""
    match = _CELL_REF_RE.fullmatch(cell_ref.upper())
    if not match:
        raise ValueError(f"Invalid cell reference: {cell_ref}")
    column_index = column_letters_to_index(match.group("column"))
    row_index = int(match.group("row"))
    return row_index, column_index


def build_cell_ref(row_index: int, column_index: int) -> str:
    """根据行列索引构造 Excel 单元格引用。    Build an Excel cell reference from row and column indices."""
    return f"{index_to_column_letters(column_index)}{row_index}"


def load_xlsx(path: str | Path) -> SpreadsheetDocument:
    """读取精简版 XLSX 工作簿到内存工作表和行对象中。    Read a minimal XLSX workbook into in-memory sheet and row objects."""
    workbook_path = Path(path)
    with zipfile.ZipFile(workbook_path, "r") as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_targets = _load_sheet_targets(archive)
        sheets: list[SpreadsheetSheet] = []

        for sheet_name, target in sheet_targets:
            root = ET.fromstring(archive.read(target))
            sheet_data = root.find("main:sheetData", NS)
            rows: list[SpreadsheetRow] = []
            if sheet_data is None:
                sheets.append(SpreadsheetSheet(name=sheet_name, rows=[]))
                continue

            max_column = 0
            row_maps: list[tuple[int, dict[int, str]]] = []
            for row_el in sheet_data.findall("main:row", NS):
                row_index = int(row_el.get("r", "0"))
                value_map: dict[int, str] = {}
                for cell_el in row_el.findall("main:c", NS):
                    cell_ref = cell_el.get("r")
                    if not cell_ref:
                        continue
                    _, column_index = split_cell_ref(cell_ref)
                    value_map[column_index] = _read_cell_value(cell_el, shared_strings)
                    max_column = max(max_column, column_index)
                row_maps.append((row_index, value_map))

            for row_index, value_map in row_maps:
                values = [value_map.get(column_index, "") for column_index in range(1, max_column + 1)]
                rows.append(SpreadsheetRow(row_index=row_index, values=values))

            sheets.append(SpreadsheetSheet(name=sheet_name, rows=rows))

    return SpreadsheetDocument(sheets=sheets)


def apply_xlsx_updates(
    template_path: str | Path,
    output_path: str | Path,
    updates: list[CellWrite],
) -> None:
    """在保留未修改 ZIP 条目的前提下应用工作簿单元格更新。    Apply cell updates to a workbook while preserving untouched ZIP entries."""
    template_file = Path(template_path)
    destination_file = Path(output_path)
    destination_file.parent.mkdir(parents=True, exist_ok=True)

    if not updates:
        shutil.copyfile(template_file, destination_file)
        return

    grouped_updates: dict[str, list[CellWrite]] = {}
    for update in updates:
        grouped_updates.setdefault(update.sheet_name, []).append(update)

    with zipfile.ZipFile(template_file, "r") as source_archive:
        sheet_targets = dict(_load_sheet_targets(source_archive))
        modified_entries: dict[str, bytes] = {}

        for sheet_name, sheet_updates in grouped_updates.items():
            sheet_target = sheet_targets.get(sheet_name)
            if not sheet_target:
                raise ValueError(f"Sheet '{sheet_name}' not found in workbook.")
            original_sheet_payload = source_archive.read(sheet_target)
            root = ET.fromstring(original_sheet_payload)
            sheet_data = root.find("main:sheetData", NS)
            if sheet_data is None:
                sheet_data = ET.SubElement(root, _q("sheetData"))

            for update in sorted(sheet_updates, key=lambda item: split_cell_ref(item.cell_ref)):
                row_index, _ = split_cell_ref(update.cell_ref)
                row_el = _get_or_create_row(sheet_data, row_index)
                cell_el = _get_or_create_cell(row_el, update.cell_ref.upper())
                _set_cell_value(cell_el, update.value)

            _update_sheet_dimension(root)
            modified_entries[sheet_target] = _serialize_worksheet_xml(root, original_sheet_payload)

        with zipfile.ZipFile(destination_file, "w", compression=zipfile.ZIP_DEFLATED) as destination_archive:
            for file_info in source_archive.infolist():
                payload = modified_entries.get(file_info.filename, source_archive.read(file_info.filename))
                destination_archive.writestr(file_info, payload)


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """如果存在，则从工作簿压缩包加载共享字符串表。    Load the shared-string table from a workbook archive if present."""
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall("main:si", NS):
        text = "".join(node.text or "" for node in item.findall(".//main:t", NS))
        values.append(text)
    return values


def _load_sheet_targets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """解析工作簿中的工作表名称与 XML 路径映射。    Resolve workbook sheet names to worksheet XML paths."""
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relations = {
        relation.get("Id"): f"xl/{relation.get('Target')}"
        for relation in rels_root.findall("rel:Relationship", NS)
    }

    targets: list[tuple[str, str]] = []
    for sheet_el in workbook_root.findall("main:sheets/main:sheet", {"main": MAIN_NS}):
        relation_id = sheet_el.get(f"{{{WORKBOOK_REL_NS}}}id")
        if not relation_id:
            continue
        targets.append((sheet_el.get("name", "Sheet1"), relations[relation_id]))
    return targets


def _read_cell_value(cell_el: ET.Element, shared_strings: list[str]) -> str:
    """从工作表 XML 单元格元素中读取文本值。    Read a textual cell value from a worksheet XML cell element."""
    cell_type = cell_el.get("t")
    value_node = cell_el.find("main:v", NS)
    if cell_type == "s" and value_node is not None and value_node.text is not None:
        index = int(value_node.text)
        return shared_strings[index] if index < len(shared_strings) else ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell_el.findall(".//main:t", NS))
    return value_node.text if value_node is not None and value_node.text is not None else ""


def _get_or_create_row(sheet_data: ET.Element, row_index: int) -> ET.Element:
    """返回指定索引对应的行元素，必要时创建并按顺序插入。    Return the row element for an index, creating and ordering it if needed."""
    for row_el in sheet_data.findall("main:row", NS):
        if int(row_el.get("r", "0")) == row_index:
            return row_el

    new_row = ET.Element(_q("row"), {"r": str(row_index)})
    inserted = False
    for position, row_el in enumerate(sheet_data.findall("main:row", NS)):
        if int(row_el.get("r", "0")) > row_index:
            sheet_data.insert(position, new_row)
            inserted = True
            break
    if not inserted:
        sheet_data.append(new_row)
    return new_row


def _get_or_create_cell(row_el: ET.Element, cell_ref: str) -> ET.Element:
    """返回指定引用的单元格元素，不存在时则创建。    Return the cell element for a reference, creating it if missing."""
    _, target_column = split_cell_ref(cell_ref)
    for cell_el in row_el.findall("main:c", NS):
        existing_ref = cell_el.get("r")
        if existing_ref == cell_ref:
            return cell_el

    new_cell = ET.Element(_q("c"), {"r": cell_ref})
    inserted = False
    for position, cell_el in enumerate(row_el.findall("main:c", NS)):
        existing_ref = cell_el.get("r")
        if existing_ref:
            _, existing_column = split_cell_ref(existing_ref)
            if existing_column > target_column:
                row_el.insert(position, new_cell)
                inserted = True
                break
    if not inserted:
        row_el.append(new_cell)
    return new_cell


def _set_cell_value(cell_el: ET.Element, value: str | float | int) -> None:
    """将 Python 值写入工作表单元格 XML 元素。    Write a Python value into a worksheet cell element."""
    cell_ref = cell_el.get("r", "")
    style_id = cell_el.get("s")
    cell_el.attrib.clear()
    cell_el.set("r", cell_ref)
    if style_id is not None:
        cell_el.set("s", style_id)

    for child in list(cell_el):
        cell_el.remove(child)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value_node = ET.SubElement(cell_el, _q("v"))
        if isinstance(value, float) and value.is_integer():
            value_node.text = str(int(value))
        elif isinstance(value, float):
            value_node.text = f"{value:.6f}".rstrip("0").rstrip(".")
        else:
            value_node.text = str(value)
        return

    cell_el.set("t", "inlineStr")
    inline_str = ET.SubElement(cell_el, _q("is"))
    text_node = ET.SubElement(inline_str, _q("t"))
    text_node.text = str(value)


def _update_sheet_dimension(root: ET.Element) -> None:
    """更新工作表已使用区域的 dimension 范围。    Update the worksheet used-range dimension."""
    sheet_data = root.find("main:sheetData", NS)
    if sheet_data is None:
        return

    cell_refs = [
        cell_el.get("r", "").upper()
        for cell_el in sheet_data.findall(".//main:c", NS)
        if cell_el.get("r")
    ]
    if not cell_refs:
        return

    bounds = [split_cell_ref(cell_ref) for cell_ref in cell_refs]
    min_row = min(row_index for row_index, _ in bounds)
    max_row = max(row_index for row_index, _ in bounds)
    min_col = min(column_index for _, column_index in bounds)
    max_col = max(column_index for _, column_index in bounds)
    start_ref = build_cell_ref(min_row, min_col)
    end_ref = build_cell_ref(max_row, max_col)
    dimension_ref = start_ref if start_ref == end_ref else f"{start_ref}:{end_ref}"

    dimension_el = root.find("main:dimension", NS)
    if dimension_el is None:
        dimension_el = ET.Element(_q("dimension"))
        root.insert(0, dimension_el)
    dimension_el.set("ref", dimension_ref)


def _serialize_worksheet_xml(root: ET.Element, original_payload: bytes) -> bytes:
    """序列化工作表 XML，并保留原始根节点命名空间声明。    Serialize worksheet XML while preserving original root namespace declarations."""
    original_text = original_payload.decode("utf-8")
    original_declaration = _extract_xml_declaration(original_text)
    root_tag = _extract_worksheet_root_tag(original_text)
    if root_tag:
        _register_namespaces_from_root_tag(root_tag)

    serialized_text = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    if original_declaration:
        serialized_text = _XML_DECLARATION_RE.sub(original_declaration, serialized_text, count=1)
    if root_tag:
        serialized_text = _WORKSHEET_ROOT_TAG_RE.sub(root_tag, serialized_text, count=1)
    return serialized_text.encode("utf-8")


def _extract_xml_declaration(xml_text: str) -> str:
    """提取原始 XML 声明头。    Extract the original XML declaration header."""
    match = _XML_DECLARATION_RE.search(xml_text)
    return match.group(1) if match else ""


def _extract_worksheet_root_tag(xml_text: str) -> str:
    """提取原始 worksheet 根起始标签。    Extract the raw worksheet root start tag."""
    match = _WORKSHEET_ROOT_TAG_RE.search(xml_text)
    return match.group(0) if match else ""


def _register_namespaces_from_root_tag(root_tag: str) -> None:
    """重新注册原始根节点上声明的命名空间前缀。    Re-register the namespace prefixes declared on the original root tag."""
    for match in _XMLNS_DECLARATION_RE.finditer(root_tag):
        prefix = match.group("prefix") or ""
        uri = match.group("uri")
        if prefix == "xml":
            continue
        ET.register_namespace(prefix, uri)
