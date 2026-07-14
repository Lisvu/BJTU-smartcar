#!/usr/bin/env python3
from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase import pdfmetrics


BASE = Path(__file__).resolve().parent
SOURCE = BASE / "SMARTCAR_USER_MANUAL.md"
OUTPUT = BASE / "智能巡检小车系统使用手册.pdf"


class ManualDocTemplate(BaseDocTemplate):
    def afterFlowable(self, flowable):
        level = getattr(flowable, "_toc_level", None)
        text = getattr(flowable, "_toc_text", None)
        if level is not None and text:
            key = getattr(flowable, "_bookmark_name", None)
            if key:
                self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (level, text, self.page, key))


def register_fonts():
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def make_styles():
    sample = getSampleStyleSheet()
    base = ParagraphStyle(
        "BodyCN",
        parent=sample["BodyText"],
        fontName="STSong-Light",
        fontSize=10.5,
        leading=16,
        alignment=TA_LEFT,
        wordWrap="CJK",
        spaceAfter=5,
    )
    styles = {
        "Title": ParagraphStyle(
            "TitleCN",
            parent=base,
            fontSize=24,
            leading=32,
            alignment=TA_CENTER,
            spaceAfter=18,
        ),
        "Subtitle": ParagraphStyle(
            "SubtitleCN",
            parent=base,
            fontSize=12,
            leading=20,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#475569"),
            spaceAfter=8,
        ),
        "H1": ParagraphStyle(
            "H1CN",
            parent=base,
            fontSize=17,
            leading=23,
            spaceBefore=14,
            spaceAfter=9,
            textColor=colors.HexColor("#0f172a"),
        ),
        "H2": ParagraphStyle(
            "H2CN",
            parent=base,
            fontSize=14,
            leading=20,
            spaceBefore=10,
            spaceAfter=7,
            textColor=colors.HexColor("#1e293b"),
        ),
        "H3": ParagraphStyle(
            "H3CN",
            parent=base,
            fontSize=12,
            leading=18,
            spaceBefore=8,
            spaceAfter=5,
            textColor=colors.HexColor("#334155"),
        ),
        "Body": base,
        "Bullet": ParagraphStyle(
            "BulletCN",
            parent=base,
            leftIndent=14,
            firstLineIndent=-8,
            spaceAfter=3,
        ),
        "Code": ParagraphStyle(
            "CodeCN",
            parent=base,
            fontName="STSong-Light",
            fontSize=8.8,
            leading=12,
            textColor=colors.HexColor("#111827"),
            backColor=colors.HexColor("#f8fafc"),
            borderColor=colors.HexColor("#e2e8f0"),
            borderWidth=0.5,
            borderPadding=5,
            spaceBefore=4,
            spaceAfter=7,
        ),
        "TOCTitle": ParagraphStyle(
            "TOCTitleCN",
            parent=base,
            fontSize=18,
            leading=26,
            alignment=TA_CENTER,
            spaceAfter=14,
        ),
        "Footer": ParagraphStyle(
            "FooterCN",
            parent=base,
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#64748b"),
        ),
    }
    return styles


def clean_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<font color='#0f766e'>\1</font>", text)
    text = text.replace("**", "")
    return text


def add_heading(story, styles, text: str, level: int, counter: int):
    style = styles["H1"] if level == 1 else styles["H2"] if level == 2 else styles["H3"]
    p = Paragraph(clean_inline(text), style)
    p._toc_level = min(level - 1, 2)
    p._toc_text = re.sub(r"<[^>]+>", "", text)
    p._bookmark_name = f"h{level}_{counter}"
    story.append(p)


def markdown_table(lines, styles):
    rows = []
    for line in lines:
        parts = [clean_inline(part.strip()) for part in line.strip().strip("|").split("|")]
        if parts and not all(re.fullmatch(r":?-{3,}:?", p) for p in parts):
            rows.append([Paragraph(p, styles["Body"]) for p in parts])
    if not rows:
        return []
    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [table, Spacer(1, 7)]


def parse_markdown(text: str, styles):
    story = []
    lines = text.splitlines()
    i = 0
    heading_counter = 0
    code_buf = []
    table_buf = []
    skip_manual_toc = False

    while i < len(lines):
        line = lines[i]

        if line.startswith("# "):
            title = line[2:].strip()
            story.append(Paragraph(clean_inline(title), styles["Title"]))
            story.append(Paragraph("项目使用手册 · 面向室内复杂环境巡检", styles["Subtitle"]))
            story.append(Paragraph("版本 V1.0 · 2026-07-14", styles["Subtitle"]))
            story.append(Spacer(1, 1.2 * cm))
            toc = TableOfContents()
            toc.levelStyles = [
                ParagraphStyle("TOCLevel1", fontName="STSong-Light", fontSize=10.5, leading=15, leftIndent=0),
                ParagraphStyle("TOCLevel2", fontName="STSong-Light", fontSize=9.5, leading=14, leftIndent=14),
                ParagraphStyle("TOCLevel3", fontName="STSong-Light", fontSize=9, leading=13, leftIndent=28),
            ]
            story.append(Paragraph("目录", styles["TOCTitle"]))
            story.append(toc)
            story.append(PageBreak())
            i += 1
            continue

        if line.strip() == "## 目录":
            skip_manual_toc = True
            i += 1
            continue
        if skip_manual_toc:
            if line.startswith("## "):
                skip_manual_toc = False
            else:
                i += 1
                continue

        if line.startswith("```"):
            code_buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_buf.append(lines[i])
                i += 1
            story.append(Preformatted("\n".join(code_buf), styles["Code"], maxLineLength=92))
            i += 1
            continue

        if line.startswith("|") and line.rstrip().endswith("|"):
            table_buf = []
            while i < len(lines) and lines[i].startswith("|") and lines[i].rstrip().endswith("|"):
                table_buf.append(lines[i])
                i += 1
            story.extend(markdown_table(table_buf, styles))
            continue

        if line.startswith("## "):
            heading_counter += 1
            add_heading(story, styles, line[3:].strip(), 1, heading_counter)
        elif line.startswith("### "):
            heading_counter += 1
            add_heading(story, styles, line[4:].strip(), 2, heading_counter)
        elif line.startswith("#### "):
            heading_counter += 1
            add_heading(story, styles, line[5:].strip(), 3, heading_counter)
        elif line.strip().startswith("- "):
            story.append(Paragraph("• " + clean_inline(line.strip()[2:]), styles["Bullet"]))
        elif re.match(r"^\d+\.\s+", line.strip()):
            story.append(Paragraph(clean_inline(line.strip()), styles["Bullet"]))
        elif line.strip():
            story.append(Paragraph(clean_inline(line.strip()), styles["Body"]))
        else:
            story.append(Spacer(1, 4))
        i += 1
    return story


def draw_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("STSong-Light", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(2 * cm, 1.2 * cm, "面向室内复杂环境的智能巡检小车系统使用手册")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"第 {doc.page} 页")
    canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
    canvas.line(2 * cm, 1.55 * cm, A4[0] - 2 * cm, 1.55 * cm)
    canvas.restoreState()


def main():
    register_fonts()
    styles = make_styles()
    story = parse_markdown(SOURCE.read_text(encoding="utf-8"), styles)
    doc = ManualDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=2.0 * cm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="manual", frames=[frame], onPage=draw_page)])
    doc.multiBuild(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()
