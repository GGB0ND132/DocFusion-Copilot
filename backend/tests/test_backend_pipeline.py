from __future__ import annotations

import io
import json
import re
import shutil
import sys
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.core.openai_client import OpenAICompatibleClient
from app.models.domain import DocumentRecord, DocumentStatus, FactRecord
from app.parsers.factory import ParserRegistry
from app.repositories.memory import InMemoryRepository
from app.repositories.postgres import PostgresRepository
from app.services.agent_service import AgentService
from app.services.benchmark_service import BenchmarkService
from app.services.document_interaction_service import DocumentInteractionService
from app.services.document_service import DocumentService
from app.services.fact_extraction import FactExtractionService
from app.services.fact_service import FactService
from app.services.template_service import TemplateService
from app.services.trace_service import TraceService
from app.tasks.executor import TaskExecutor
from app.utils.spreadsheet import CellWrite, apply_xlsx_updates, load_xlsx
from app.utils.wordprocessing import WordCellWrite, apply_docx_updates, load_docx_tables


class BackendPipelineTests(unittest.TestCase):
    """后端核心业务链路的端到端测试。    End-to-end tests for the backend core business pipeline."""

    def setUp(self) -> None:
        """为每个用例创建隔离的测试工作区。    Create an isolated test workspace for each case."""

        tests_root = Path(__file__).resolve().parents[2] / ".tmp_test_runs"
        tests_root.mkdir(parents=True, exist_ok=True)
        self._workspace_root = tests_root / f"backend_{uuid.uuid4().hex}"
        self._workspace_root.mkdir(parents=True, exist_ok=False)
        self.settings = Settings(workspace_root=self._workspace_root)
        self.settings.ensure_directories()
        self.repository = InMemoryRepository()
        self.executor = TaskExecutor(max_workers=1)
        self.document_service = DocumentService(
            repository=self.repository,
            parser_registry=ParserRegistry(),
            extraction_service=FactExtractionService(),
            executor=self.executor,
            settings=self.settings,
        )
        self.template_service = TemplateService(
            repository=self.repository,
            executor=self.executor,
            settings=self.settings,
            openai_client=OpenAICompatibleClient(
                api_key="",
                base_url="",
                model="gpt-4o-mini",
            ),
        )
        self.benchmark_service = BenchmarkService(
            repository=self.repository,
            executor=self.executor,
            settings=self.settings,
            template_service=self.template_service,
        )
        self.trace_service = TraceService(repository=self.repository)
        self.fact_service = FactService(repository=self.repository)
        self.agent_service = AgentService(
            repository=self.repository,
            openai_client=OpenAICompatibleClient(
                api_key="",
                base_url="",
                model="gpt-4o-mini",
            ),
        )
        self.document_interaction_service = DocumentInteractionService(
            repository=self.repository,
            agent_service=self.agent_service,
            template_service=self.template_service,
            settings=self.settings,
            openai_client=OpenAICompatibleClient(
                api_key="",
                base_url="",
                model="gpt-4o-mini",
            ),
        )

    def tearDown(self) -> None:
        """释放测试资源。    Release resources created by the test case."""

        self.executor.shutdown()
        shutil.rmtree(self._workspace_root, ignore_errors=True)

    def test_text_document_upload_extracts_expected_city_facts(self) -> None:
        """上传文本报告后应得到四条城市事实。    Uploading a text report should yield four city facts."""

        content = (
            "2025年，上海GDP总量56,708.71亿元，常住人口2,487.45万人，"
            "人均GDP228020元，一般公共预算收入8,500.91亿元。"
        )
        _, task = self.document_service.upload_document("city_report.txt", content.encode("utf-8"))
        self.executor.wait(task.task_id, timeout=5)

        facts = self.repository.list_facts(canonical_only=True)
        fields = {fact.field_name for fact in facts}

        self.assertEqual(4, len(facts))
        self.assertEqual({"GDP总量", "常住人口", "人均GDP", "一般公共预算收入"}, fields)
        self.assertTrue(all(fact.entity_name == "上海" for fact in facts))

    def test_template_fill_populates_xlsx_cells(self) -> None:
        """XLSX 模板回填应把事实写回单元格。    XLSX template filling should write facts back into cells."""

        self._seed_city_facts()
        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）", "常住人口（万人）"],
            rows=[
                ["上海", "", ""],
                ["北京", "", ""],
            ],
        )

        task = self.template_service.submit_fill_task(
            template_name="city_template.xlsx",
            content=template_bytes,
            document_ids=["doc_seed"],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        workbook = load_xlsx(Path(result.output_path))
        first_sheet = workbook.sheets[0]
        rows = {row.row_index: row.values for row in first_sheet.rows}

        self.assertEqual("56708.71", rows[2][1])
        self.assertEqual("2487.45", rows[2][2])
        self.assertEqual("52073.4", rows[3][1])
        self.assertGreaterEqual(len(result.filled_cells), 4)

    def test_document_service_returns_scope_local_canonical_facts(self) -> None:
        """文档事实查询应在当前文档范围内返回局部 canonical。    Document fact queries should return scope-local canonical facts within the selected document."""

        scoped_fact, _global_fact = self._seed_scope_conflicting_facts()

        facts = self.document_service.get_document_facts("doc_scope", canonical_only=True)

        self.assertEqual(1, len(facts))
        self.assertEqual(scoped_fact.fact_id, facts[0].fact_id)
        self.assertTrue(facts[0].is_canonical)

    def test_template_fill_uses_scope_local_canonical_facts(self) -> None:
        """模板回填应按当前选中文档范围挑选 canonical，而不是被库里其他文档挤掉。    Template filling should choose canonical facts within the selected document scope instead of being shadowed by other documents in the repository."""

        scoped_fact, _global_fact = self._seed_scope_conflicting_facts()
        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）"],
            rows=[["上海", ""]],
        )

        task = self.template_service.submit_fill_task(
            template_name="scoped_template.xlsx",
            content=template_bytes,
            document_ids=["doc_scope"],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        self.assertEqual(1, len(result.filled_cells))
        workbook = load_xlsx(Path(result.output_path))
        first_sheet = workbook.sheets[0]
        rows = {row.row_index: row.values for row in first_sheet.rows}

        expected_value = str(scoped_fact.value_num)
        self.assertEqual(expected_value, rows[2][1])
        self.assertEqual(expected_value, str(result.filled_cells[0].value))

    def test_template_fill_skips_entity_only_rows_when_no_field_matches(self) -> None:
        """若模板字段没有任何匹配事实，则不应只把实体名灌进第一列。    When template fields have no matching facts, the filler should not inject entity names into the first column only."""

        self._seed_city_facts()
        template_bytes = build_simple_template_xlsx(
            headers=["国家/地区", "病例数"],
            rows=[["", ""]],
        )

        task = self.template_service.submit_fill_task(
            template_name="no_match_template.xlsx",
            content=template_bytes,
            document_ids=["doc_seed"],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        self.assertEqual(0, len(result.filled_cells))
        workbook = load_xlsx(Path(result.output_path))
        rows = {row.row_index: row.values for row in workbook.sheets[0].rows}
        self.assertEqual("", rows[2][0])
        self.assertEqual("", rows[2][1])

    def test_epidemic_summary_template_fill_extracts_expected_fields(self) -> None:
        """疫情综述文档应能回填地区、大洲、人均 GDP、人口、检测数和病例数。    Epidemic summary documents should fill region, continent, per-capita GDP, population, test counts and case counts."""

        content = (
            "2020年7月27日，地处Asia（亚洲）的中国持续推进疫情防控。"
            "广东省常住人口约1.26亿，人均 GDP 约 9.6 万元，7月27日无本土新增确诊病例，新增 4 例境外输入无症状感染者，"
            "当日核酸检测量约 38.2 万份。"
        )
        document, parse_task = self.document_service.upload_document(
            "covid_summary.txt",
            content.encode("utf-8"),
            document_set_id="set_covid_template",
        )
        self.executor.wait(parse_task.task_id, timeout=5)

        template_bytes = build_simple_template_xlsx(
            headers=["国家/地区", "大洲", "人均GDP", "人口", "每日检测数", "病例数"],
            rows=[["", "", "", "", "", ""]],
        )
        task = self.template_service.submit_fill_task(
            template_name="covid_template.xlsx",
            content=template_bytes,
            document_ids=[document.doc_id],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        workbook = load_xlsx(Path(result.output_path))
        rows = {row.row_index: row.values for row in workbook.sheets[0].rows}

        self.assertEqual("广东", rows[2][0])
        self.assertEqual("亚洲", rows[2][1])
        self.assertEqual("96000", rows[2][2])
        self.assertEqual("12600", rows[2][3])
        self.assertEqual("38.2", rows[2][4])
        self.assertEqual("4", rows[2][5])

    def test_apply_xlsx_updates_preserves_worksheet_root_declarations(self) -> None:
        """XLSX 写回应保留工作表根节点的命名空间声明。    XLSX writes should preserve worksheet root namespace declarations."""

        template_bytes = build_excel_compatible_template_xlsx(
            headers=["国家/地区", "大洲", "病例数"],
            rows=[],
        )
        template_path = self.settings.temp_dir / "excel_compatible_template.xlsx"
        output_path = self.settings.outputs_dir / "excel_compatible_output.xlsx"
        template_path.write_bytes(template_bytes)

        apply_xlsx_updates(
            template_path,
            output_path,
            [CellWrite(sheet_name="Sheet1", cell_ref="A2", value="中国")],
        )

        with zipfile.ZipFile(template_path, "r") as template_archive:
            input_xml = template_archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        with zipfile.ZipFile(output_path, "r") as output_archive:
            output_xml = output_archive.read("xl/worksheets/sheet1.xml").decode("utf-8")

        input_root = re.search(r"<worksheet\b[^>]*>", input_xml)
        output_root = re.search(r"<worksheet\b[^>]*>", output_xml)
        self.assertIsNotNone(input_root)
        self.assertIsNotNone(output_root)
        self.assertEqual(input_root.group(0), output_root.group(0))
        self.assertIn('ref="A1:C2"', output_xml)

    def test_template_fill_populates_docx_table_cells(self) -> None:
        """DOCX 表格模板回填应写入事实值。    DOCX table-template filling should write fact values into table cells."""

        self._seed_city_facts()
        template_bytes = build_simple_template_docx(
            headers=["城市", "GDP总量（亿元）", "常住人口（万人）"],
            rows=[
                ["上海", "", ""],
                ["北京", "", ""],
            ],
        )

        task = self.template_service.submit_fill_task(
            template_name="city_template.docx",
            content=template_bytes,
            document_ids=["doc_seed"],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        document = load_docx_tables(Path(result.output_path))
        first_table = document.tables[0]
        rows = {row.row_index: row.values for row in first_table.rows}

        self.assertEqual("56708.71", rows[2][1])
        self.assertEqual("2487.45", rows[2][2])
        self.assertEqual("52073.4", rows[3][1])
        self.assertGreaterEqual(len(result.filled_cells), 4)

    def test_postgres_fact_rows_auto_compute_conflict_group_id(self) -> None:
        """PostgreSQL 事实行应自动生成 conflict group id。    PostgreSQL fact rows should auto-compute conflict group ids."""

        fact = FactRecord(
            fact_id="fact_conflict",
            entity_type="city",
            entity_name="上海",
            field_name="GDP总量",
            value_num=56708.71,
            value_text="56708.71",
            unit="亿元",
            year=2025,
            source_doc_id="doc_seed",
            source_block_id="block_seed",
            source_span="2025年上海GDP总量56,708.71亿元",
            confidence=0.98,
        )

        row = PostgresRepository._fact_to_row(fact)

        self.assertEqual("city::上海::GDP总量::2025::亿元", row.conflict_group_id)

    def test_air_quality_xlsx_can_fill_docx_template(self) -> None:
        """环境监测 XLSX 文档应能回填到 DOCX 表格模板。    An air-quality XLSX document should fill a DOCX table template."""

        source_bytes = build_simple_template_xlsx(
            headers=[
                "序号",
                "监测时间",
                "城市",
                "区",
                "站点编码",
                "站点名称",
                "空气质量指数",
                "PM10监测值",
                "PM2.5监测值",
                "首要污染物",
                "污染类型",
            ],
            rows=[
                ["1", "2025-11-25 09:00:00", "枣庄市", "滕州市", "370481001", "滕州北辛中学", "29", "32", "7", "—", "优"],
                ["2", "2025-11-25 09:00:00", "东营市", "东营经济技术开发区", "370500052", "东营市环保局", "22", "22", "2", "—", "优"],
            ],
        )
        document, parse_task = self.document_service.upload_document(
            "air_quality.xlsx",
            source_bytes,
            document_set_id="set_air_quality",
        )
        self.executor.wait(parse_task.task_id, timeout=5)

        template_bytes = build_word_compatible_template_docx(
            headers=["城市", "区", "站点名称", "空气质量指数", "PM10监测值", "PM2.5监测值", "首要污染物", "污染类型"],
            rows=[["", "", "", "", "", "", "", ""], ["", "", "", "", "", "", "", ""]],
        )
        task = self.template_service.submit_fill_task(
            template_name="air_quality_template.docx",
            content=template_bytes,
            document_ids=[document.doc_id],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        output_document = load_docx_tables(Path(result.output_path))
        first_table = output_document.tables[0]
        rows = {row.row_index: row.values for row in first_table.rows}

        self.assertEqual("枣庄市", rows[2][0])
        self.assertEqual("滕州市", rows[2][1])
        self.assertEqual("滕州北辛中学", rows[2][2])
        self.assertEqual("29", rows[2][3])
        self.assertEqual("32", rows[2][4])
        self.assertEqual("7", rows[2][5])
        self.assertEqual("优", rows[2][7])

    def test_apply_docx_updates_preserves_word_root_declarations(self) -> None:
        """DOCX 写回应保留 Word 根节点上的命名空间声明。    DOCX writes should preserve the Word root namespace declarations."""

        template_bytes = build_word_compatible_template_docx(
            headers=["城市", "站点名称", "空气质量指数"],
            rows=[["", "", ""]],
        )
        template_path = self.settings.temp_dir / "word_compatible_template.docx"
        output_path = self.settings.outputs_dir / "word_compatible_output.docx"
        template_path.write_bytes(template_bytes)

        apply_docx_updates(
            template_path,
            output_path,
            [WordCellWrite(table_index=1, row_index=2, column_index=1, value="枣庄市")],
        )

        with zipfile.ZipFile(template_path, "r") as template_archive:
            input_xml = template_archive.read("word/document.xml").decode("utf-8")
        with zipfile.ZipFile(output_path, "r") as output_archive:
            output_xml = output_archive.read("word/document.xml").decode("utf-8")

        input_root = re.search(r"<w:document\b[^>]*>", input_xml)
        output_root = re.search(r"<w:document\b[^>]*>", output_xml)
        self.assertIn('mc:Ignorable="w14 w15 w16se w16cid w16 w16cex w16sdtdh w16sdtfl w16du wp14"', output_xml)
        self.assertIn("xmlns:mc=", output_xml)
        self.assertIsNotNone(input_root)
        self.assertIsNotNone(output_root)
        self.assertEqual(input_root.group(0), output_root.group(0))

    def test_trace_service_reports_template_usage(self) -> None:
        """追溯事实时应包含模板使用位置。    Fact tracing should include template usage positions."""

        seeded_facts = self._seed_city_facts()
        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）"],
            rows=[["上海", ""]],
        )

        task = self.template_service.submit_fill_task(
            template_name="trace_template.xlsx",
            content=template_bytes,
            document_ids=["doc_seed"],
            fill_mode="canonical",
        )
        self.executor.wait(task.task_id, timeout=5)

        trace = self.trace_service.get_fact_trace(seeded_facts[0].fact_id)
        self.assertIsNotNone(trace)
        self.assertEqual("上海", trace["fact"].entity_name)
        self.assertTrue(trace["usages"])
        self.assertEqual("B2", trace["usages"][0]["cell_ref"])

    def test_document_interaction_service_can_summarize_documents(self) -> None:
        """文档交互服务应能输出规则摘要。    The document interaction service should produce a deterministic summary."""

        content = (
            "# 城市发展报告\n\n"
            "2025年，上海GDP总量56,708.71亿元，常住人口2,487.45万人。"
        )
        _, task = self.document_service.upload_document("summary.md", content.encode("utf-8"))
        self.executor.wait(task.task_id, timeout=5)

        result = self.document_interaction_service.execute(message="请总结这份文档", context_id="ctx_demo")

        self.assertEqual("summarize_document", result["intent"])
        self.assertEqual("summary", result["execution_type"])
        self.assertIn("共处理", result["summary"])
        self.assertEqual("ctx_demo", result["context_id"])

    def test_document_interaction_service_can_reformat_text_documents(self) -> None:
        """文本整理应输出规范化的 Markdown。    Text cleanup should output normalized Markdown."""

        content = "一、总览  \n\n\n  2025年上海GDP总量56,708.71亿元。\n"
        _, task = self.document_service.upload_document("format.md", content.encode("utf-8"))
        self.executor.wait(task.task_id, timeout=5)

        result = self.document_interaction_service.execute(message="请帮我整理一下格式")

        self.assertEqual("reformat_document", result["intent"])
        self.assertEqual("reformat", result["execution_type"])
        self.assertEqual(1, len(result["artifacts"]))
        artifact_path = Path(result["artifacts"][0]["output_path"])
        self.assertTrue(artifact_path.exists())
        formatted_text = artifact_path.read_text(encoding="utf-8")
        self.assertIn("# 总览", formatted_text)

    def test_document_interaction_service_can_reformat_docx_documents(self) -> None:
        """DOCX 整理应输出规范化后的文件。    DOCX cleanup should output a normalized file artifact."""

        content = build_simple_document_docx(
            paragraphs=[
                "一、  总览",
                "  2025年上海GDP总量56,708.71亿元。 ",
            ]
        )
        _, task = self.document_service.upload_document("format.docx", content)
        self.executor.wait(task.task_id, timeout=5)

        result = self.document_interaction_service.execute(message="请帮我整理一下格式")

        self.assertEqual("reformat_document", result["intent"])
        self.assertEqual("reformat", result["execution_type"])
        artifact_path = Path(result["artifacts"][0]["output_path"])
        with zipfile.ZipFile(artifact_path, "r") as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("Heading1", document_xml)
        self.assertIn("一、总览", document_xml)

    def test_document_interaction_service_can_edit_text_documents(self) -> None:
        """自然语言编辑应生成修改后的文本文件。    Natural-language editing should generate an edited text file."""

        content = "合同甲方为南京甲公司，乙方为南京乙公司。"
        _, task = self.document_service.upload_document("edit.txt", content.encode("utf-8"))
        self.executor.wait(task.task_id, timeout=5)

        result = self.document_interaction_service.execute(message="将南京甲公司替换为南京采购中心")

        self.assertEqual("edit_document", result["intent"])
        self.assertEqual("edit", result["execution_type"])
        self.assertEqual(1, len(result["artifacts"]))
        self.assertEqual(1, result["artifacts"][0]["change_count"])
        artifact_path = Path(result["artifacts"][0]["output_path"])
        edited_text = artifact_path.read_text(encoding="utf-8")
        self.assertIn("南京采购中心", edited_text)
        self.assertNotIn("南京甲公司", edited_text)

    def test_document_interaction_service_can_edit_docx_documents(self) -> None:
        """自然语言编辑应生成修改后的 DOCX 文件。    Natural-language editing should generate an edited DOCX file."""

        content = build_simple_document_docx(paragraphs=["甲方为南京甲公司。"])
        _, task = self.document_service.upload_document("edit.docx", content)
        self.executor.wait(task.task_id, timeout=5)

        result = self.document_interaction_service.execute(message="把南京甲公司改为南京采购中心")

        self.assertEqual("edit_document", result["intent"])
        self.assertEqual("edit", result["execution_type"])
        self.assertEqual(1, result["artifacts"][0]["change_count"])
        artifact_path = Path(result["artifacts"][0]["output_path"])
        with zipfile.ZipFile(artifact_path, "r") as archive:
            document_xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("南京采购中心", document_xml)

    def test_document_interaction_service_can_queue_template_fill_from_natural_language_entry(self) -> None:
        """自然语言执行入口应能直接接收模板文件并提交回填任务。    The natural-language execution entry should accept a template file and queue a fill task."""

        _, city_task = self.document_service.upload_document(
            "city_report.txt",
            "2025年，上海GDP总量56,708.71亿元，常住人口2,487.45万人。".encode("utf-8"),
            document_set_id="set_agent",
        )
        self.executor.wait(city_task.task_id, timeout=5)

        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）", "常住人口（万人）"],
            rows=[["上海", "", ""]],
        )
        response = self.document_interaction_service.execute(
            message="请根据这批文档把这个表格填好",
            document_set_id="set_agent",
            template_name="city_agent_template.xlsx",
            template_content=template_bytes,
            fill_mode="canonical",
            auto_match=True,
        )

        self.assertEqual("extract_and_fill_template", response["intent"])
        self.assertEqual("template_fill_task", response["execution_type"])
        self.assertIsNotNone(response["task_id"])
        self.assertEqual("queued", response["task_status"])
        self.assertEqual("city_agent_template.xlsx", response["template_name"])

        self.executor.wait(response["task_id"], timeout=5)
        result = self.template_service.get_result(response["task_id"])
        self.assertIsNotNone(result)
        workbook = load_xlsx(Path(result.output_path))
        first_sheet = workbook.sheets[0]
        rows = {row.row_index: row.values for row in first_sheet.rows}
        self.assertEqual("56708.71", rows[2][1])
        self.assertEqual("2487.45", rows[2][2])

    def test_fact_review_can_recompute_canonical_selection(self) -> None:
        """人工复核应能更新事实状态并重算 canonical 结果。    Manual review should update fact status and recompute the canonical winner."""

        seeded_facts = self._seed_city_facts()
        backup_fact = FactRecord(
            fact_id="fact_sh_gdp_backup",
            entity_type=seeded_facts[0].entity_type,
            entity_name=seeded_facts[0].entity_name,
            field_name=seeded_facts[0].field_name,
            value_num=56000.0,
            value_text="56,000.00",
            unit=seeded_facts[0].unit,
            year=seeded_facts[0].year,
            source_doc_id="doc_seed",
            source_block_id="blk_3",
            source_span="backup fact",
            confidence=0.85,
        )
        self.repository.add_facts([backup_fact])

        reviewed = self.fact_service.review_fact(
            seeded_facts[0].fact_id,
            status="rejected",
            reviewer="tester",
            note="manual validation failed",
        )

        self.assertIsNotNone(reviewed)
        self.assertEqual("rejected", reviewed.status)
        self.assertEqual("tester", reviewed.metadata["reviewer"])

        canonical_facts = self.repository.list_facts(
            entity_name=seeded_facts[0].entity_name,
            field_name=seeded_facts[0].field_name,
            canonical_only=True,
        )
        self.assertEqual(["fact_sh_gdp_backup"], [fact.fact_id for fact in canonical_facts])

    def test_fact_evaluation_reports_perfect_accuracy_for_matching_labels(self) -> None:
        """事实评测应在标注完全匹配时给出满分。    Fact evaluation should report perfect accuracy for matching labels."""

        self._seed_city_facts()
        annotations = {
            "facts": [
                {"entity_name": "上海", "field_name": "GDP总量", "value_num": 56708.71, "unit": "亿元", "year": 2025},
                {"entity_name": "上海", "field_name": "常住人口", "value_num": 2487.45, "unit": "万人", "year": 2025},
                {"entity_name": "北京", "field_name": "GDP总量", "value_num": 52073.4, "unit": "亿元", "year": 2025},
                {"entity_name": "北京", "field_name": "常住人口", "value_num": 2185.3, "unit": "万人", "year": 2025},
            ]
        }

        task = self.benchmark_service.submit_fact_evaluation(
            annotation_name="facts.json",
            content=json.dumps(annotations, ensure_ascii=False).encode("utf-8"),
            document_ids=["doc_seed"],
            canonical_only=True,
        )
        self.executor.wait(task.task_id, timeout=5)

        task_record = self.repository.get_task(task.task_id)
        self.assertIsNotNone(task_record)
        self.assertEqual("succeeded", str(task_record.status))
        self.assertEqual(1.0, task_record.result["accuracy"])
        report = self.benchmark_service.get_report(task.task_id)
        self.assertIsNotNone(report)
        self.assertEqual(4, report["matched_count"])
        self.assertTrue(report["meets_threshold_0_80"])

    def test_template_benchmark_reports_perfect_accuracy_for_matching_expected_result(self) -> None:
        """模板基准测试应在结果完全匹配时给出满分。    Template benchmark should report perfect accuracy for a perfect fill."""

        self._seed_city_facts()
        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）", "常住人口（万人）"],
            rows=[
                ["上海", "", ""],
                ["北京", "", ""],
            ],
        )
        expected_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）", "常住人口（万人）"],
            rows=[
                ["上海", "56708.71", "2487.45"],
                ["北京", "52073.4", "2185.3"],
            ],
        )

        task = self.benchmark_service.submit_template_benchmark(
            template_name="city_template.xlsx",
            template_content=template_bytes,
            expected_result_name="city_template_expected.xlsx",
            expected_result_content=expected_bytes,
            fill_mode="canonical",
            document_ids=["doc_seed"],
        )
        self.executor.wait(task.task_id, timeout=5)

        task_record = self.repository.get_task(task.task_id)
        self.assertIsNotNone(task_record)
        self.assertEqual("succeeded", str(task_record.status))
        self.assertEqual(1.0, task_record.result["accuracy"])
        self.assertEqual(4, task_record.result["total_compared_cells"])
        report = self.benchmark_service.get_report(task.task_id)
        self.assertIsNotNone(report)
        self.assertEqual(4, report["matched_cells"])
        self.assertTrue(report["meets_threshold_0_80"])

    def test_template_fill_auto_matches_relevant_documents_and_tracks_elapsed_seconds(self) -> None:
        """普通模板回填应自动筛选相关文档并记录耗时。    Regular template filling should auto-match relevant documents and record elapsed time."""

        _, city_task = self.document_service.upload_document(
            "city_report.txt",
            "2025年，上海GDP总量56,708.71亿元，常住人口2,487.45万人。".encode("utf-8"),
            document_set_id="set_auto",
        )
        _, contract_task = self.document_service.upload_document(
            "contract_report.txt",
            "合同金额为800万元，甲方为南京采购中心，乙方为南京信息公司。".encode("utf-8"),
            document_set_id="set_auto",
        )
        self.executor.wait(city_task.task_id, timeout=5)
        self.executor.wait(contract_task.task_id, timeout=5)

        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）"],
            rows=[["上海", ""]],
        )
        task = self.template_service.submit_fill_task(
            template_name="city_summary_template.xlsx",
            content=template_bytes,
            document_set_id="set_auto",
            auto_match=True,
        )
        self.executor.wait(task.task_id, timeout=5)

        task_record = self.repository.get_task(task.task_id)
        self.assertIsNotNone(task_record)
        self.assertEqual("succeeded", str(task_record.status))
        self.assertEqual("rules", task_record.result["match_mode"])
        self.assertEqual(1, len(task_record.result["matched_document_ids"]))
        matched_document = self.repository.get_document(task_record.result["matched_document_ids"][0])
        self.assertIsNotNone(matched_document)
        self.assertEqual("city_report.txt", matched_document.file_name)
        self.assertGreaterEqual(task_record.result["elapsed_seconds"], 0.0)

    def test_template_fill_respects_document_set_scope(self) -> None:
        """模板回填应只在指定文档批次内检索内容。    Template filling should only search within the requested document batch."""

        _, sh_task = self.document_service.upload_document(
            "shanghai.txt",
            "2025年，上海GDP总量56,708.71亿元。".encode("utf-8"),
            document_set_id="set_a",
        )
        _, bj_task = self.document_service.upload_document(
            "beijing.txt",
            "2025年，北京GDP总量52,073.40亿元。".encode("utf-8"),
            document_set_id="set_b",
        )
        self.executor.wait(sh_task.task_id, timeout=5)
        self.executor.wait(bj_task.task_id, timeout=5)

        template_bytes = build_simple_template_xlsx(
            headers=["城市", "GDP总量（亿元）"],
            rows=[["北京", ""]],
        )
        task = self.template_service.submit_fill_task(
            template_name="beijing_template.xlsx",
            content=template_bytes,
            document_set_id="set_b",
            auto_match=True,
        )
        self.executor.wait(task.task_id, timeout=5)

        task_record = self.repository.get_task(task.task_id)
        self.assertIsNotNone(task_record)
        self.assertEqual(1, len(task_record.result["matched_document_ids"]))
        matched_document = self.repository.get_document(task_record.result["matched_document_ids"][0])
        self.assertIsNotNone(matched_document)
        self.assertEqual("beijing.txt", matched_document.file_name)

        result = self.template_service.get_result(task.task_id)
        self.assertIsNotNone(result)
        workbook = load_xlsx(Path(result.output_path))
        first_sheet = workbook.sheets[0]
        rows = {row.row_index: row.values for row in first_sheet.rows}
        self.assertEqual("52073.4", rows[2][1])

    def _seed_city_facts(self) -> list[FactRecord]:
        """插入模板测试所需的 canonical 事实。    Insert canonical facts required by template tests."""

        self.repository.add_document(
            DocumentRecord(
                doc_id="doc_seed",
                file_name="seed.txt",
                stored_path="seed.txt",
                doc_type="txt",
                upload_time=datetime.now(timezone.utc),
                status=DocumentStatus.parsed,
                metadata={},
            )
        )
        return self.repository.add_facts(
            [
                FactRecord(
                    fact_id="fact_sh_gdp",
                    entity_type="city",
                    entity_name="上海",
                    field_name="GDP总量",
                    value_num=56708.71,
                    value_text="56,708.71",
                    unit="亿元",
                    year=2025,
                    source_doc_id="doc_seed",
                    source_block_id="blk_1",
                    source_span="上海GDP总量56,708.71亿元",
                    confidence=0.98,
                ),
                FactRecord(
                    fact_id="fact_sh_pop",
                    entity_type="city",
                    entity_name="上海",
                    field_name="常住人口",
                    value_num=2487.45,
                    value_text="2,487.45",
                    unit="万人",
                    year=2025,
                    source_doc_id="doc_seed",
                    source_block_id="blk_1",
                    source_span="上海常住人口2487.45万人",
                    confidence=0.97,
                ),
                FactRecord(
                    fact_id="fact_bj_gdp",
                    entity_type="city",
                    entity_name="北京",
                    field_name="GDP总量",
                    value_num=52073.4,
                    value_text="52,073.40",
                    unit="亿元",
                    year=2025,
                    source_doc_id="doc_seed",
                    source_block_id="blk_2",
                    source_span="北京GDP总量52,073.40亿元",
                    confidence=0.96,
                ),
                FactRecord(
                    fact_id="fact_bj_pop",
                    entity_type="city",
                    entity_name="北京",
                    field_name="常住人口",
                    value_num=2185.3,
                    value_text="2,185.30",
                    unit="万人",
                    year=2025,
                    source_doc_id="doc_seed",
                    source_block_id="blk_2",
                    source_span="北京常住人口2185.30万人",
                    confidence=0.95,
                ),
            ]
        )

    def _seed_scope_conflicting_facts(self) -> tuple[FactRecord, FactRecord]:
        """插入两个来自不同文档但属于同一冲突组的事实，用于验证局部 canonical 选择。    Insert two same-group facts from different documents to verify scope-local canonical selection."""

        for doc_id in ("doc_scope", "doc_global"):
            self.repository.add_document(
                DocumentRecord(
                    doc_id=doc_id,
                    file_name=f"{doc_id}.txt",
                    stored_path=f"{doc_id}.txt",
                    doc_type="txt",
                    upload_time=datetime.now(timezone.utc),
                    status=DocumentStatus.parsed,
                    metadata={"document_set_id": "set_scope"},
                )
            )

        stored_facts = self.repository.add_facts(
            [
                FactRecord(
                    fact_id="fact_scope_gdp",
                    entity_type="city",
                    entity_name="上海",
                    field_name="GDP总量",
                    value_num=111.1,
                    value_text="111.1",
                    unit="亿元",
                    year=2025,
                    source_doc_id="doc_scope",
                    source_block_id="blk_scope",
                    source_span="上海GDP总量111.1亿元",
                    confidence=0.61,
                ),
                FactRecord(
                    fact_id="fact_global_gdp",
                    entity_type="city",
                    entity_name="上海",
                    field_name="GDP总量",
                    value_num=222.2,
                    value_text="222.2",
                    unit="亿元",
                    year=2025,
                    source_doc_id="doc_global",
                    source_block_id="blk_global",
                    source_span="上海GDP总量222.2亿元",
                    confidence=0.99,
                ),
            ]
        )
        return stored_facts[0], stored_facts[1]


def build_simple_template_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    """在内存中构造最小可用 XLSX 模板。    Build a minimal usable XLSX template in memory."""

    sheet_rows = [headers, *rows]
    sheet_xml_rows = []
    for row_index, row_values in enumerate(sheet_rows, start=1):
        cells = []
        for column_index, value in enumerate(row_values, start=1):
            column_letter = chr(ord("A") + column_index - 1)
            cell_ref = f"{column_letter}{row_index}"
            cells.append(f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        sheet_xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    workbook_bytes = io.BytesIO()
    with zipfile.ZipFile(workbook_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    {''.join(sheet_xml_rows)}
  </sheetData>
</worksheet>
""",
        )
    return workbook_bytes.getvalue()


def build_excel_compatible_template_xlsx(headers: list[str], rows: list[list[str]]) -> bytes:
    """在内存中构造带有 Excel 常见命名空间声明的 XLSX 模板。    Build an XLSX template with Excel-style namespace declarations."""

    shared_strings = list(headers)
    header_cells = []
    for column_index, _header in enumerate(headers, start=1):
        column_letter = chr(ord("A") + column_index - 1)
        header_cells.append(f'<c r="{column_letter}1" t="s"><v>{column_index - 1}</v></c>')

    workbook_bytes = io.BytesIO()
    with zipfile.ZipFile(workbook_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">
  {''.join(f"<si><t>{escape(value)}</t></si>" for value in shared_strings)}
</sst>
""",
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="等线"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>
""",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
    mc:Ignorable="x14ac xr xr2 xr3"
    xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
    xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
    xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
    xmlns:xr3="http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"
    xr:uid="{{00000000-0001-0000-0000-000000000000}}">
  <dimension ref="A1:{chr(ord('A') + len(headers) - 1)}1"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="14" x14ac:dyDescent="0.3"/>
  <sheetData>
    <row r="1" spans="1:{len(headers)}" x14ac:dyDescent="0.3">{''.join(header_cells)}</row>
  </sheetData>
</worksheet>
""",
        )
    return workbook_bytes.getvalue()


def build_simple_template_docx(headers: list[str], rows: list[list[str]]) -> bytes:
    """在内存中构造最小 DOCX 表格模板。    Build a minimal DOCX table template in memory."""

    return build_simple_document_docx(table_rows=[headers, *rows])


def build_word_compatible_template_docx(headers: list[str], rows: list[list[str]]) -> bytes:
    """在内存中构造带有 Word 常见命名空间声明的 DOCX 表格模板。    Build a DOCX table template with Word-style namespace declarations."""

    row_xml = []
    for row in [headers, *rows]:
        cells = "".join(
            f"<w:tc><w:p><w:r><w:t>{escape(value)}</w:t></w:r></w:p></w:tc>"
            for value in row
        )
        row_xml.append(f"<w:tr>{cells}</w:tr>")

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
    xmlns:cx="http://schemas.microsoft.com/office/drawing/2014/chartex"
    xmlns:cx1="http://schemas.microsoft.com/office/drawing/2015/9/8/chartex"
    xmlns:cx2="http://schemas.microsoft.com/office/drawing/2015/10/21/chartex"
    xmlns:cx3="http://schemas.microsoft.com/office/drawing/2016/5/9/chartex"
    xmlns:cx4="http://schemas.microsoft.com/office/drawing/2016/5/10/chartex"
    xmlns:cx5="http://schemas.microsoft.com/office/drawing/2016/5/11/chartex"
    xmlns:cx6="http://schemas.microsoft.com/office/drawing/2016/5/12/chartex"
    xmlns:cx7="http://schemas.microsoft.com/office/drawing/2016/5/13/chartex"
    xmlns:cx8="http://schemas.microsoft.com/office/drawing/2016/5/14/chartex"
    xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
    xmlns:aink="http://schemas.microsoft.com/office/drawing/2016/ink"
    xmlns:am3d="http://schemas.microsoft.com/office/drawing/2017/model3d"
    xmlns:o="urn:schemas-microsoft-com:office:office"
    xmlns:oel="http://schemas.microsoft.com/office/2019/extlst"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
    xmlns:v="urn:schemas-microsoft-com:vml"
    xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
    xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    xmlns:w10="urn:schemas-microsoft-com:office:word"
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
    xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"
    xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex"
    xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid"
    xmlns:w16="http://schemas.microsoft.com/office/word/2018/wordml"
    xmlns:w16du="http://schemas.microsoft.com/office/word/2023/wordml/word16du"
    xmlns:w16sdtdh="http://schemas.microsoft.com/office/word/2020/wordml/sdtdatahash"
    xmlns:w16sdtfl="http://schemas.microsoft.com/office/word/2024/wordml/sdtformatlock"
    xmlns:w16se="http://schemas.microsoft.com/office/word/2015/wordml/symex"
    xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
    xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
    xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
    xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
    mc:Ignorable="w14 w15 w16se w16cid w16 w16cex w16sdtdh w16sdtfl w16du wp14">
  <w:body>
    <w:tbl>{''.join(row_xml)}</w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>
"""

    docx_bytes = io.BytesIO()
    with zipfile.ZipFile(docx_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        archive.writestr("word/document.xml", document_xml)
    return docx_bytes.getvalue()


def build_simple_document_docx(
    *,
    paragraphs: list[str] | None = None,
    table_rows: list[list[str]] | None = None,
) -> bytes:
    """在内存中构造最小 DOCX 文档。    Build a minimal DOCX document in memory."""

    paragraph_xml = "".join(
        f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
        for text in (paragraphs or [])
    )
    table_xml = ""
    if table_rows:
        row_xml = []
        for row in table_rows:
            cells = "".join(
                f"<w:tc><w:p><w:r><w:t>{escape(value)}</w:t></w:r></w:p></w:tc>"
                for value in row
            )
            row_xml.append(f"<w:tr>{cells}</w:tr>")
        table_xml = f"<w:tbl>{''.join(row_xml)}</w:tbl>"

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {paragraph_xml}
    {table_xml}
    <w:sectPr/>
  </w:body>
</w:document>
"""

    docx_bytes = io.BytesIO()
    with zipfile.ZipFile(docx_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        archive.writestr("word/document.xml", document_xml)
    return docx_bytes.getvalue()
