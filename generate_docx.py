"""
DocFusion Copilot - Markdown to DOCX converter
Reads the project markdown document and generates a formatted Word document.
Competition format: A4, margins 2cm+1cm binding, KaiTi body, single spacing.
"""
import os, re
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MD_FILE = os.path.join(BASE_DIR, "\u9879\u76ee\u6587\u6863\u8d44\u6599", "\u9879\u76ee\u8be6\u7ec6\u65b9\u6848.md")
OUTPUT_FILE = os.path.join(BASE_DIR, "\u9879\u76ee\u6587\u6863\u8d44\u6599", "\u9879\u76ee\u8be6\u7ec6\u65b9\u6848.docx")

FONT_BODY = "\u6977\u4f53"
FONT_HEADING = "\u9ed1\u4f53"
FONT_TITLE = "\u534e\u6587\u4e2d\u5b8b"
FONT_EN = "Times New Roman"

LQ = "\u201c"  # left double quotation mark
RQ = "\u201d"  # right double quotation mark


def make_run(paragraph, text, font_name=FONT_BODY, font_size=Pt(12),
             bold=False, italic=False, color=None, superscript=False):
    run = paragraph.add_run(text)
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), font_name)
    run.font.size = font_size
    run.font.bold = bold
    run.font.italic = italic
    run.font.superscript = superscript
    if color:
        run.font.color.rgb = color
    return run


def add_styled_heading(doc, text, level):
    p = doc.add_paragraph()
    sizes = {0: Pt(18), 1: Pt(15), 2: Pt(13), 3: Pt(12)}
    spacings = {0: (Pt(24), Pt(18)), 1: (Pt(18), Pt(12)), 2: (Pt(12), Pt(8)), 3: (Pt(8), Pt(6))}
    font = FONT_HEADING if level < 3 else FONT_BODY
    make_run(p, text, font_name=font, font_size=sizes.get(level, Pt(12)), bold=True)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = spacings.get(level, (Pt(8), Pt(6)))
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    if level == 0:
        pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return p


def add_body_paragraph(doc, text, indent=True):
    """Add a body paragraph, handling **bold**, <sup>[n]</sup> markers."""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(6)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    if indent:
        pf.first_line_indent = Cm(0.74)

    # Split by superscript refs and bold markers
    tokens = re.split(r"(<sup>\[\d+\]</sup>|\*\*[^*]+\*\*)", text)
    for tok in tokens:
        if not tok:
            continue
        sup = re.match(r"<sup>\[(\d+)\]</sup>", tok)
        if sup:
            make_run(p, f"[{sup.group(1)}]", font_size=Pt(8), superscript=True)
        elif tok.startswith("**") and tok.endswith("**"):
            make_run(p, tok[2:-2], bold=True)
        else:
            make_run(p, tok)
    return p


def add_placeholder(doc, caption):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.space_before = Pt(12)
    pf.space_after = Pt(4)
    make_run(p, f"[\u6b64\u5904\u63d2\u5165\u56fe\u7247: {caption}]",
             font_size=Pt(10), italic=True, color=RGBColor(0x99, 0x99, 0x99))
    # Caption
    pc = doc.add_paragraph()
    pc.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pc.paragraph_format.space_before = Pt(6)
    pc.paragraph_format.space_after = Pt(12)
    make_run(pc, caption, font_size=Pt(10))


def add_reference_entry(doc, number, text):
    p = doc.add_paragraph()
    run = p.add_run(f"[{number}] {text}")
    run.font.name = FONT_EN
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_BODY)
    run.font.size = Pt(9)
    pf = p.paragraph_format
    pf.space_before = Pt(2)
    pf.space_after = Pt(2)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.first_line_indent = Cm(-0.74)
    pf.left_indent = Cm(0.74)


def add_todo(doc, text=None):
    p = doc.add_paragraph()
    make_run(p, text or "[\u5f85\u8865\u5145]",
             italic=True, color=RGBColor(0xFF, 0x66, 0x00))
    pf = p.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(6)
    pf.first_line_indent = Cm(0.74)


def add_page_break(doc):
    p = doc.add_paragraph()
    p.add_run().add_break()


def parse_and_build(doc, md_text):
    """Parse markdown and build the docx, section by section."""
    lines = md_text.split("\n")
    i = 0
    in_mermaid = False
    in_references = False
    ref_count = 0

    while i < len(lines):
        line = lines[i]

        # Skip mermaid code blocks
        if line.strip().startswith("```mermaid"):
            in_mermaid = True
            i += 1
            continue
        if in_mermaid:
            if line.strip() == "```":
                in_mermaid = False
            i += 1
            continue

        # Skip HTML-only lines and empty lines
        if line.strip() == "---" or line.strip() == "":
            i += 1
            continue

        # Skip the title block and TOC
        if line.startswith("# ") and i < 5:
            i += 1
            continue
        if line.startswith("**DocFusion") or line.startswith("*") and "\u8d5b\u9898" in line:
            i += 1
            continue
        if line.strip().startswith("- ["):
            i += 1
            continue
        if line.strip().startswith("## \u76ee\u5f55"):
            i += 1
            continue

        # Chapter headings (## )
        if line.startswith("## "):
            title = line[3:].strip()
            if title == "\u53c2\u8003\u6587\u732e":
                in_references = True
                add_page_break(doc)
                add_styled_heading(doc, title, level=0)
                i += 1
                continue
            add_page_break(doc)
            add_styled_heading(doc, title, level=0)
            i += 1
            continue

        # Section headings (### )
        if line.startswith("### "):
            title = line[4:].strip()
            add_styled_heading(doc, title, level=1)
            i += 1
            continue

        # Sub-section headings (#### )
        if line.startswith("#### "):
            title = line[5:].strip()
            add_styled_heading(doc, title, level=2)
            i += 1
            continue

        # References ([1] ... )
        if in_references and line.startswith("["):
            ref_match = re.match(r"\[(\d+)\]\s*(.*)", line)
            if ref_match:
                ref_count += 1
                ref_text = ref_match.group(2)
                add_reference_entry(doc, ref_count, ref_text)
                i += 1
                continue

        # Figure captions
        if line.strip().startswith("**\u56fe ") or line.strip().startswith("**\u56fe\u00a0"):
            fig_text = line.strip().replace("**", "")
            add_placeholder(doc, fig_text)
            i += 1
            continue

        # [placeholder] lines
        if line.strip().startswith("[\u5f85\u8865\u5145"):
            add_todo(doc, line.strip())
            i += 1
            continue

        if line.strip().startswith("[\u56e2\u961f\u5177\u4f53"):
            add_todo(doc, line.strip())
            i += 1
            continue

        # Bold sub-headings like **（1）xxx**
        bold_heading = re.match(r"^\*\*[\uff08\(][\d\u4e00-\u9fff]+[\uff09\)].+\*\*$", line.strip())
        if bold_heading:
            text = line.strip()[2:-2]
            add_styled_heading(doc, text, level=3)
            i += 1
            continue

        # Regular paragraph text
        if line.strip() and not line.startswith("```") and not line.startswith("|") and not line.startswith("style "):
            # Collect multi-line paragraph
            para_text = line.strip()
            i += 1
            continue

        i += 1


def generate():
    # Read markdown
    with open(MD_FILE, "r", encoding="utf-8") as f:
        md_text = f.read()

    doc = Document()

    # Page setup
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(3)   # 2cm + 1cm binding
    section.right_margin = Cm(2)

    # Header
    hp = section.header.paragraphs[0]
    hp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = hp.add_run("\u3010A23\u3011\u57fa\u4e8e\u5927\u8bed\u8a00\u6a21\u578b\u7684\u6587\u6863\u7406\u89e3\u4e0e\u591a\u6e90\u6570\u636e\u878d\u5408\u7cfb\u7edf \u2014 \u9879\u76ee\u8be6\u7ec6\u65b9\u6848")
    run.font.name = FONT_BODY
    run._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_BODY)
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)

    # Footer with page numbers
    fp = section.footer.paragraphs[0]
    fp.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for xml_str in [
        f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>',
        f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>',
        f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>'
    ]:
        r = fp.add_run()
        r._element.append(parse_xml(xml_str))
        r.font.size = Pt(10)

    # ── Cover Page ──
    for _ in range(6):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    make_run(p, "\u57fa\u4e8e\u5927\u8bed\u8a00\u6a21\u578b\u7684\u6587\u6863\u7406\u89e3\u4e0e\u591a\u6e90\u6570\u636e\u878d\u5408\u7cfb\u7edf",
             font_name=FONT_TITLE, font_size=Pt(22), bold=True)

    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(12)
    make_run(p, "DocFusion Copilot \u2014 \u6587\u6863\u667a\u878d\u52a9\u624b",
             font_name=FONT_HEADING, font_size=Pt(15), color=RGBColor(0x4A, 0x90, 0xD9))

    for _ in range(4):
        doc.add_paragraph()
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    make_run(p, "\u8d5b\u9898\uff1a\u3010A23\u3011\u57fa\u4e8e\u5927\u8bed\u8a00\u6a21\u578b\u7684\u6587\u6863\u7406\u89e3\u4e0e\u591a\u6e90\u6570\u636e\u878d\u5408\u7cfb\u7edf\u3010\u91d1\u9675\u79d1\u6280\u5b66\u9662\u3011")
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    make_run(p, "\u7b2c\u5341\u4e03\u5c4a\u4e2d\u56fd\u5927\u5b66\u751f\u670d\u52a1\u5916\u5305\u521b\u65b0\u521b\u4e1a\u5927\u8d5b")
    add_page_break(doc)

    # ── TOC Page ──
    p = doc.add_paragraph()
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    make_run(p, "\u76ee  \u5f55", font_name=FONT_HEADING, font_size=Pt(18), bold=True)

    toc = [
        ("\u7b2c\u4e00\u7ae0 \u7eea\u8bba", 0),
        ("1.1 \u9879\u76ee\u80cc\u666f", 1), ("1.2 \u56fd\u5185\u5916\u7814\u7a76\u73b0\u72b6", 1),
        ("1.3 \u76ee\u524d\u5b58\u5728\u7684\u95ee\u9898", 1), ("1.4 \u4e3b\u8981\u7814\u7a76\u5185\u5bb9", 1),
        ("1.5 \u7279\u8272\u7efc\u8ff0", 1),
        ("\u7b2c\u4e8c\u7ae0 \u89e3\u51b3\u65b9\u6848\u4e0e\u6838\u5fc3\u6280\u672f", 0),
        ("2.1 \u89e3\u51b3\u65b9\u6848\u4e0e\u6280\u672f\u8def\u7ebf", 1),
        ("2.2 \u6587\u6863\u667a\u80fd\u89e3\u6790\u4e0e\u7ed3\u6784\u5316\u5904\u7406", 1),
        ("2.2.1 \u591a\u683c\u5f0f\u6587\u6863\u89e3\u6790\u5f15\u64ce", 2),
        ("2.2.2 \u6587\u6863\u5206\u5757\u4e0e\u7ed3\u6784\u8bc6\u522b", 2),
        ("2.2.3 \u4fe1\u606f\u62bd\u53d6\u4e0e\u4e8b\u5b9e\u5f52\u4e00\u5316", 2),
        ("2.3 \u591a\u6e90\u6570\u636e\u878d\u5408\u4e0e\u77e5\u8bc6\u5e93\u6784\u5efa", 1),
        ("2.4 \u667a\u80fd\u6a21\u677f\u56de\u586b\u4e0e\u8ffd\u6eaf\u673a\u5236", 1),
        ("2.5 \u9879\u76ee\u5e94\u7528\uff1aDocFusion \u6587\u6863\u667a\u878d\u52a9\u624b\u7cfb\u7edf", 1),
        ("2.6 \u9879\u76ee\u5e94\u7528\u6587\u6863", 1), ("2.7 \u5b9e\u9a8c\u4e0e\u5206\u6790", 1),
        ("\u7b2c\u4e09\u7ae0 \u9879\u76ee\u7ba1\u7406\u4e0e\u4eba\u5458\u67b6\u6784", 0),
        ("3.1 \u4eba\u5458\u67b6\u6784", 1), ("3.2 \u4efb\u52a1\u5206\u914d\u4e0e\u8fdb\u5ea6\u5b89\u6392", 1),
        ("3.2.1 \u9879\u76ee\u751f\u547d\u5468\u671f\u4e0e\u7ec4\u7ec7", 2),
        ("3.2.2 \u9879\u76ee\u8fc7\u7a0b\u7ba1\u7406\u4e0e\u8d28\u91cf\u8bc4\u4f30", 2),
        ("3.2.3 \u9879\u76ee\u98ce\u9669\u5206\u6790", 2), ("3.2.4 \u9879\u76ee\u8bc4\u5ba1", 2),
        ("\u7b2c\u56db\u7ae0 \u53ef\u884c\u6027\u5206\u6790", 0),
        ("4.1 \u7ecf\u6d4e\u53ef\u884c\u6027", 1), ("4.2 \u793e\u4f1a\u53ef\u884c\u6027", 1),
        ("4.3 \u653f\u7b56\u53ef\u884c\u6027", 1), ("4.4 \u4eba\u5458\u53ef\u884c\u6027", 1),
        ("4.5 \u6cd5\u5f8b\u53ef\u884c\u6027", 1),
        ("\u7b2c\u4e94\u7ae0 \u7ed3\u8bed", 0), ("\u53c2\u8003\u6587\u732e", 0),
    ]
    for text, level in toc:
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = Cm(level * 0.7)
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        make_run(p, text, font_size=Pt(12 if level == 0 else 11), bold=(level == 0))

    add_page_break(doc)

    # ── Now build the content from markdown ──
    # Instead of complex parsing, build directly from structured data
    build_chapter1(doc, md_text)
    build_chapter2(doc)
    build_chapter3(doc)
    build_chapter4(doc, md_text)
    build_chapter5(doc)
    build_references(doc)

    doc.save(OUTPUT_FILE)
    print(f"\u6587\u6863\u5df2\u4fdd\u5b58\u81f3: {OUTPUT_FILE}")


def extract_section(md_text, start_heading, end_heading=None):
    """Extract raw text between two markdown headings."""
    lines = md_text.split("\n")
    collecting = False
    result = []
    for line in lines:
        if start_heading in line:
            collecting = True
            continue
        if end_heading and end_heading in line and collecting:
            break
        if collecting:
            result.append(line)
    return "\n".join(result)


def add_md_paragraphs(doc, text):
    """Add paragraphs from markdown text, handling bold, sup, and headings."""
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line == "---":
            i += 1
            continue
        if line.startswith("```"):
            # Skip code blocks
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            i += 1
            continue
        if line.startswith("**\u56fe ") or line.startswith("**\u56fe\u00a0"):
            add_placeholder(doc, line.replace("**", ""))
            i += 1
            continue
        if line.startswith("[") and ("\u5f85\u8865\u5145" in line or "\u56e2\u961f\u5177\u4f53" in line):
            add_todo(doc, line)
            i += 1
            continue
        # Bold sub-heading
        if re.match(r"^\*\*[\uff08\(].+[\uff09\)].+\*\*$", line):
            add_styled_heading(doc, line[2:-2], level=3)
            i += 1
            continue
        # Regular text
        add_body_paragraph(doc, line)
        i += 1


def build_chapter1(doc, md_text):
    """Build Chapter 1 from the markdown source."""
    add_styled_heading(doc, "\u7b2c\u4e00\u7ae0 \u7eea\u8bba", level=0)

    # 1.1
    sec = extract_section(md_text, "### 1.1", "### 1.2")
    add_styled_heading(doc, "1.1 \u9879\u76ee\u80cc\u666f", level=1)
    add_md_paragraphs(doc, sec)

    # 1.2
    sec = extract_section(md_text, "### 1.2", "### 1.3")
    add_styled_heading(doc, "1.2 \u56fd\u5185\u5916\u7814\u7a76\u73b0\u72b6", level=1)
    add_md_paragraphs(doc, sec)

    # 1.3
    sec = extract_section(md_text, "### 1.3", "### 1.4")
    add_styled_heading(doc, "1.3 \u76ee\u524d\u5b58\u5728\u7684\u95ee\u9898", level=1)
    add_md_paragraphs(doc, sec)

    # 1.4
    sec = extract_section(md_text, "### 1.4", "### 1.5")
    add_styled_heading(doc, "1.4 \u4e3b\u8981\u7814\u7a76\u5185\u5bb9", level=1)
    add_md_paragraphs(doc, sec)

    # 1.5
    sec = extract_section(md_text, "### 1.5", "## \u7b2c\u4e8c\u7ae0")
    add_styled_heading(doc, "1.5 \u7279\u8272\u7efc\u8ff0", level=1)
    add_md_paragraphs(doc, sec)


def build_chapter2(doc):
    add_page_break(doc)
    add_styled_heading(doc, "\u7b2c\u4e8c\u7ae0 \u89e3\u51b3\u65b9\u6848\u4e0e\u6838\u5fc3\u6280\u672f", level=0)

    intro = (
        "\u672c\u7ae0\u8be6\u7ec6\u9610\u8ff0DocFusion Copilot\u7cfb\u7edf\u7684\u6574\u4f53\u89e3\u51b3\u65b9\u6848\u3001"
        "\u6838\u5fc3\u6280\u672f\u6a21\u5757\u8bbe\u8ba1\u4e0e\u5b9e\u73b0\u7ec6\u8282\u3002\u7cfb\u7edf\u9075\u5faa"
        + LQ + "\u5148\u5efa\u5e93\u3001\u518d\u79d2\u586b" + RQ
        + "\u7684\u4e24\u9636\u6bb5\u67b6\u6784\uff0c\u5728\u9636\u6bb5\u4e00\u5b8c\u6210\u6587\u6863\u9884\u5904\u7406\u3001"
        "\u4e8b\u5b9e\u62bd\u53d6\u548c\u77e5\u8bc6\u5e93\u6784\u5efa\uff0c\u5728\u9636\u6bb5\u4e8c\u5b9e\u73b0\u6a21\u677f\u8bed\u4e49\u7406\u89e3\u548c"
        "\u667a\u80fd\u56de\u586b\uff0c\u4ece\u800c\u6ee1\u8db3\u8d5b\u9898\u5bf9\u51c6\u786e\u7387\uff08\u226580%\uff09\u548c\u54cd\u5e94\u65f6\u95f4\uff08\u226490\u79d2/\u6a21\u677f\uff09\u7684\u53cc\u91cd\u8981\u6c42\u3002"
    )
    add_body_paragraph(doc, intro)

    add_styled_heading(doc, "2.1 \u89e3\u51b3\u65b9\u6848\u4e0e\u6280\u672f\u8def\u7ebf", level=1)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1a\u6574\u4f53\u65b9\u6848\u6982\u8ff0\u3001\u6280\u672f\u6808\u8868\u683c\u3001\u4e24\u9636\u6bb5\u67b6\u6784\u56fe\u3001\u6280\u672f\u8def\u7ebf\u56fe]")
    add_placeholder(doc, "\u56fe 2-1 DocFusion \u7cfb\u7edf\u4e24\u9636\u6bb5\u6574\u4f53\u67b6\u6784")
    add_placeholder(doc, "\u56fe 2-2 DocFusion \u7cfb\u7edf\u6280\u672f\u67b6\u6784\u56fe")
    add_placeholder(doc, "\u56fe 2-3 \u9879\u76ee\u56db\u5468\u51b2\u523a\u6280\u672f\u8def\u7ebf\u56fe")

    for title in [
        "2.2 \u6587\u6863\u667a\u80fd\u89e3\u6790\u4e0e\u7ed3\u6784\u5316\u5904\u7406",
    ]:
        add_styled_heading(doc, title, level=1)
        add_todo(doc)

    for title, hint in [
        ("2.2.1 \u591a\u683c\u5f0f\u6587\u6863\u89e3\u6790\u5f15\u64ce", "ParserRegistry\u5de5\u5382\u6a21\u5f0f\u3001\u4e94\u79cd\u89e3\u6790\u5668\u8bbe\u8ba1"),
        ("2.2.2 \u6587\u6863\u5206\u5757\u4e0e\u7ed3\u6784\u8bc6\u522b", "DocumentBlock\u7edf\u4e00\u4e2d\u95f4\u8868\u793a\u3001section_path\u5c42\u7ea7\u8def\u5f84"),
        ("2.2.3 \u4fe1\u606f\u62bd\u53d6\u4e0e\u4e8b\u5b9e\u5f52\u4e00\u5316", "\u5206\u5c42\u56db\u7ea7\u62bd\u53d6\u6d41\u7a0b\u3001\u89c4\u5219+LLM\u6df7\u5408\u7b56\u7565"),
    ]:
        add_styled_heading(doc, title, level=2)
        add_todo(doc, f"[\u5f85\u8865\u5145\uff1a{hint}]")

    for title, hint in [
        ("2.3 \u591a\u6e90\u6570\u636e\u878d\u5408\u4e0e\u77e5\u8bc6\u5e93\u6784\u5efa", "\u5b9e\u4f53\u5bf9\u9f50\u3001\u5b57\u6bb5\u6807\u51c6\u5316\u3001\u51b2\u7a81\u6d88\u89e3\u52a0\u6743\u7b97\u6cd5\u3001Fact\u6570\u636e\u6a21\u578b"),
        ("2.4 \u667a\u80fd\u6a21\u677f\u56de\u586b\u4e0e\u8ffd\u6eaf\u673a\u5236", "\u6a21\u677f\u7406\u89e3\u56db\u6b65\u6cd5\u3001TemplateIntent\u3001\u586b\u5145\u903b\u8f91\u3001\u8ffd\u6eaf\u8bbe\u8ba1"),
    ]:
        add_styled_heading(doc, title, level=1)
        add_todo(doc, f"[\u5f85\u8865\u5145\uff1a{hint}]")

    add_styled_heading(doc, "2.5 \u9879\u76ee\u5e94\u7528\uff1aDocFusion \u6587\u6863\u667a\u878d\u52a9\u624b\u7cfb\u7edf", level=1)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1a\u7cfb\u7edf\u529f\u80fd\u63cf\u8ff0\uff08\u5de5\u4f5c\u53f0\u3001Agent\u5bf9\u8bdd\u9875\u9762\uff09]")
    add_placeholder(doc, "\u56fe 2-4 DocFusion \u7cfb\u7edf\u4e09\u5927\u6838\u5fc3\u529f\u80fd\u6a21\u5757")

    add_styled_heading(doc, "2.6 \u9879\u76ee\u5e94\u7528\u6587\u6863", level=1)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1aAPI\u63a5\u53e3\u5217\u8868\u3001Docker Compose\u90e8\u7f72\u6d41\u7a0b\u3001\u73af\u5883\u8981\u6c42]")

    add_styled_heading(doc, "2.7 \u5b9e\u9a8c\u4e0e\u5206\u6790", level=1)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1a5\u4e2a\u6a21\u677f\u573a\u666f\u5b9e\u9a8c\u8bbe\u8ba1\u3001\u8bc4\u4ef7\u6307\u6807\u3001\u5bf9\u6bd4\u5b9e\u9a8c\u3001\u7ed3\u679c\u5206\u6790]")


def build_chapter3(doc):
    add_page_break(doc)
    add_styled_heading(doc, "\u7b2c\u4e09\u7ae0 \u9879\u76ee\u7ba1\u7406\u4e0e\u4eba\u5458\u67b6\u6784", level=0)

    add_styled_heading(doc, "3.1 \u4eba\u5458\u67b6\u6784", level=1)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1a\u56e2\u961f\u6784\u6210\u4e0e\u89d2\u8272\u5206\u5de5]")

    add_styled_heading(doc, "3.2 \u4efb\u52a1\u5206\u914d\u4e0e\u8fdb\u5ea6\u5b89\u6392", level=1)

    for title, hint in [
        ("3.2.1 \u9879\u76ee\u751f\u547d\u5468\u671f\u4e0e\u7ec4\u7ec7", "\u56db\u5468\u51b2\u523a\u751f\u547d\u5468\u671f\u3001\u654f\u6377\u8fed\u4ee3\u7ec4\u7ec7"),
        ("3.2.2 \u9879\u76ee\u8fc7\u7a0b\u7ba1\u7406\u4e0e\u8d28\u91cf\u8bc4\u4f30", "\u4ee3\u7801\u5ba1\u67e5\u3001\u6d4b\u8bd5\u3001\u6587\u6863\u5ba1\u6838\u6d41\u7a0b"),
        ("3.2.3 \u9879\u76ee\u98ce\u9669\u5206\u6790", "\u6280\u672f\u98ce\u9669\u3001\u7ba1\u7406\u98ce\u9669\u3001\u5e94\u5bf9\u7b56\u7565"),
        ("3.2.4 \u9879\u76ee\u8bc4\u5ba1", "\u91cc\u7a0b\u7891\u8bc4\u5ba1\u8282\u70b9"),
    ]:
        add_styled_heading(doc, title, level=2)
        add_todo(doc, f"[\u5f85\u8865\u5145\uff1a{hint}]")


def build_chapter4(doc, md_text):
    """Build Chapter 4 from markdown source."""
    add_page_break(doc)
    add_styled_heading(doc, "\u7b2c\u56db\u7ae0 \u53ef\u884c\u6027\u5206\u6790", level=0)

    sections = [
        ("4.1", "\u7ecf\u6d4e\u53ef\u884c\u6027", "### 4.1", "### 4.2"),
        ("4.2", "\u793e\u4f1a\u53ef\u884c\u6027", "### 4.2", "### 4.3"),
        ("4.3", "\u653f\u7b56\u53ef\u884c\u6027", "### 4.3", "### 4.4"),
        ("4.4", "\u4eba\u5458\u53ef\u884c\u6027", "### 4.4", "### 4.5"),
        ("4.5", "\u6cd5\u5f8b\u53ef\u884c\u6027", "### 4.5", "## \u7b2c\u4e94\u7ae0"),
    ]
    for num, title, start, end in sections:
        sec = extract_section(md_text, start, end)
        add_styled_heading(doc, f"{num} {title}", level=1)
        add_md_paragraphs(doc, sec)


def build_chapter5(doc):
    add_page_break(doc)
    add_styled_heading(doc, "\u7b2c\u4e94\u7ae0 \u7ed3\u8bed", level=0)
    add_todo(doc, "[\u5f85\u8865\u5145\uff1a\u9879\u76ee\u603b\u7ed3\u3001\u5c55\u671b]")


def build_references(doc):
    add_page_break(doc)
    add_styled_heading(doc, "\u53c2\u8003\u6587\u732e", level=0)

    refs = [
        "Xu, Y., Li, M., Cui, L., Huang, S., Wei, F., & Zhou, M. (2020). LayoutLM: Pre-training of text and layout for document image understanding. Proceedings of the 26th ACM SIGKDD International Conference on Knowledge Discovery & Data Mining, 1192-1200.",
        "Huang, Y., Lv, T., Cui, L., Lu, Y., & Wei, F. (2022). LayoutLMv3: Pre-training for document AI with unified text and image masking. Proceedings of the 30th ACM International Conference on Multimedia, 4083-4091.",
        "Kim, G., Hong, T., Yim, M., Nam, J., Park, J., Yim, J., ... & Park, S. (2022). OCR-free document understanding transformer. European Conference on Computer Vision (ECCV), 498-517. Springer.",
        "Kim, G., Hong, T., Yim, M., Park, J., Yim, J., Hwang, W., ... & Park, S. (2023). Unified structure learning for OCR-free document understanding. arXiv preprint arXiv:2305.02122.",
        "Wang, Z., Liu, J., Li, Y., Tong, Y., & Jiang, J. (2024). Neural optical understanding for academic documents. arXiv preprint arXiv:2404.17241.",
        "Wei, X., Cui, X., Cheng, N., Wang, X., Zhang, X., Huang, S., ... & Han, W. (2023). Zero-shot information extraction via chatting with ChatGPT. arXiv preprint arXiv:2302.10205.",
        "Xu, Y., Xu, Y., Lv, T., Cui, L., Wei, F., Wang, G., ... & Zhou, M. (2022). Information extraction from visually rich documents with font style embeddings. Document Analysis and Recognition - ICDAR 2022, 129-145.",
        "Lewis, P., Perez, E., Piktus, A., Petroni, F., Karpukhin, V., Goyal, N., ... & Kiela, D. (2020). Retrieval-augmented generation for knowledge-intensive NLP tasks. Advances in Neural Information Processing Systems, 33, 9459-9474.",
        "Gao, Y., Xiong, Y., Gao, X., Jia, K., Pan, J., Bi, Y., ... & Wang, H. (2024). Retrieval-augmented generation for large language models: A survey. arXiv preprint arXiv:2312.10997.",
        "Cuconasu, F., Trappolini, G., Siciliano, F., Filice, S., Campagnano, C., Maarek, Y., ... & Tonellotto, N. (2024). The power of noise: Redefining retrieval for RAG systems. Proceedings of the 47th International ACM SIGIR Conference, 719-729.",
        "Gao, T., Yen, H., Yu, J., & Chen, D. (2023). Enabling large language models to generate text with citations. Proceedings of the 2023 Conference on Empirical Methods in Natural Language Processing, 6465-6488.",
        "Zheng, X., Burdick, D., Popa, L., Zhong, X., & Wang, N. R. (2024). Towards comprehensive table extraction from unstructured documents. Document Analysis and Recognition - ICDAR 2024, 37-53.",
        "Sui, Y., Zhou, M., Zhou, M., Han, S., & Zhang, D. (2024). Large language models on tables: A survey. arXiv preprint arXiv:2402.17944.",
        "Ly, N. T., Nguyen, A., & Bui, H. (2023). Weakly supervised table parsing via pre-training. Document Analysis and Recognition - ICDAR 2023, 218-234.",
        "Ji, Z., Lee, N., Frieske, R., Yu, T., Su, D., Xu, Y., ... & Fung, P. (2023). Survey of hallucination in natural language generation. ACM Computing Surveys, 55(12), 1-38.",
        "Li, Z., Xu, C., Wang, S., Xu, Z., Zhang, Q., & Sui, Z. (2024). Do LLMs know when to refuse? A survey on trustworthy generation. arXiv preprint arXiv:2402.11633.",
        "Luo, C., Cheng, Z., Huang, Q., & Qi, J. (2024). A layout-aware generative language model for multimodal document understanding. Proceedings of the AAAI Conference on Artificial Intelligence, 38(4), 3885-3893.",
    ]
    for idx, ref in enumerate(refs, 1):
        add_reference_entry(doc, idx, ref)


if __name__ == "__main__":
    generate()
