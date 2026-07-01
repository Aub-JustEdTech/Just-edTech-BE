"""
PDF report generator for heatmap district citation exports.
Uses ReportLab Platypus for flow-based layout.
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Brand colours ─────────────────────────────────────────────────────────────
_BRAND_DARK = colors.HexColor("#1a1a2e")
_BRAND_BLUE = colors.HexColor("#4361ee")
_CHARTER_BLUE = colors.HexColor("#3a86ff")
_LIGHT_GREY = colors.HexColor("#f4f4f6")
_MID_GREY = colors.HexColor("#9ca3af")
_TEXT = colors.HexColor("#111827")
_SNIPPET_BG = colors.HexColor("#f8f9fa")

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "report_title": ParagraphStyle(
            "report_title",
            parent=base["Normal"],
            fontSize=20,
            fontName="Helvetica-Bold",
            textColor=_BRAND_DARK,
            spaceAfter=2 * mm,
        ),
        "meta_label": ParagraphStyle(
            "meta_label",
            parent=base["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=_MID_GREY,
            spaceAfter=1 * mm,
        ),
        "meta_value": ParagraphStyle(
            "meta_value",
            parent=base["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            textColor=_TEXT,
            spaceAfter=0,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            parent=base["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            textColor=_BRAND_BLUE,
            spaceBefore=4 * mm,
            spaceAfter=2 * mm,
        ),
        "citation_num": ParagraphStyle(
            "citation_num",
            parent=base["Normal"],
            fontSize=8,
            fontName="Helvetica-Bold",
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "doc_title": ParagraphStyle(
            "doc_title",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica-Bold",
            textColor=_TEXT,
            spaceAfter=1 * mm,
        ),
        "doc_meta": ParagraphStyle(
            "doc_meta",
            parent=base["Normal"],
            fontSize=8,
            fontName="Helvetica",
            textColor=_MID_GREY,
            spaceAfter=2 * mm,
        ),
        "snippet": ParagraphStyle(
            "snippet",
            parent=base["Normal"],
            fontSize=9,
            fontName="Helvetica",
            textColor=_TEXT,
            leading=13,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontSize=7,
            fontName="Helvetica",
            textColor=_MID_GREY,
            alignment=TA_CENTER,
        ),
    }


def _footer(canvas, doc):
    canvas.saveState()
    s = _styles()
    footer_text = f"JustEdTech  ·  Page {doc.page}  ·  Generated {date.today().strftime('%B %d, %Y')}"
    p = Paragraph(footer_text, s["footer"])
    w, h = p.wrap(PAGE_W - 2 * MARGIN, 10 * mm)
    p.drawOn(canvas, MARGIN, 10 * mm)
    canvas.restoreState()


def generate_citations_pdf(
    district_name: str,
    keyword: str,
    citations: list[dict],
    district_type: str = "public",
) -> bytes:
    """
    Build a PDF report for district citations and return the raw bytes.

    citations: list of dicts with keys:
        document_title, date, snippet, page_number, relevance_score
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=20 * mm,
        title=f"Citations — {district_name}",
        author="JustEdTech",
    )

    s = _styles()
    story = []

    # ── Header bar ────────────────────────────────────────────────────────────
    badge_color = _CHARTER_BLUE if district_type == "charter" else _BRAND_BLUE
    badge_label = "Charter School" if district_type == "charter" else "Public District"

    header_data = [
        [
            Paragraph("District Citations Report", s["report_title"]),
            Paragraph(badge_label, ParagraphStyle(
                "badge",
                fontSize=8,
                fontName="Helvetica-Bold",
                textColor=colors.white,
                alignment=TA_RIGHT,
            )),
        ]
    ]
    header_table = Table(header_data, colWidths=[PAGE_W - 2 * MARGIN - 30 * mm, 30 * mm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), badge_color),
        ("ROUNDEDCORNERS", [4]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (1, 0), (1, 0), 4),
        ("BOTTOMPADDING", (1, 0), (1, 0), 4),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("RIGHTPADDING", (1, 0), (1, 0), 6),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 3 * mm))

    # ── Meta row: district | keyword | date | count ───────────────────────────
    today = date.today().strftime("%B %d, %Y")
    meta_data = [
        [
            Paragraph("DISTRICT", s["meta_label"]),
            Paragraph("KEYWORD", s["meta_label"]),
            Paragraph("GENERATED", s["meta_label"]),
            Paragraph("CITATIONS", s["meta_label"]),
        ],
        [
            Paragraph(district_name, s["meta_value"]),
            Paragraph(keyword, s["meta_value"]),
            Paragraph(today, s["meta_value"]),
            Paragraph(str(len(citations)), s["meta_value"]),
        ],
    ]
    col_w = (PAGE_W - 2 * MARGIN) / 4
    meta_table = Table(meta_data, colWidths=[col_w] * 4)
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _LIGHT_GREY),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_LIGHT_GREY, colors.white]),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb")))
    story.append(Spacer(1, 4 * mm))

    if not citations:
        story.append(Paragraph("No citations found for this district and keyword.", s["doc_meta"]))
    else:
        story.append(Paragraph(f"Citations ({len(citations)})", s["section_heading"]))

        for i, c in enumerate(citations, start=1):
            doc_title = c.get("document_title") or "Unknown Document"
            date_str = c.get("date") or ""
            snippet = c.get("snippet") or ""
            page_num = c.get("page_number")
            relevance = c.get("relevance_score")

            meta_parts = []
            if date_str:
                meta_parts.append(date_str)
            if page_num is not None:
                meta_parts.append(f"Page {page_num}")
            if relevance is not None:
                meta_parts.append(f"Relevance {int(relevance * 100)}%")
            meta_str = "  ·  ".join(meta_parts)

            # Number badge + doc title + meta in a two-column row
            num_cell = Table(
                [[Paragraph(str(i), s["citation_num"])]],
                colWidths=[6 * mm],
                rowHeights=[6 * mm],
            )
            num_cell.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), _BRAND_BLUE),
                ("ROUNDEDCORNERS", [3]),
                ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
                ("TOPPADDING", (0, 0), (0, 0), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ]))

            header_row = Table(
                [[num_cell, Paragraph(doc_title, s["doc_title"])]],
                colWidths=[8 * mm, PAGE_W - 2 * MARGIN - 8 * mm],
            )
            header_row.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))

            # Snippet block with light background
            snippet_table = Table(
                [[Paragraph(f'"{snippet}"', s["snippet"])]],
                colWidths=[PAGE_W - 2 * MARGIN - 4 * mm],
            )
            snippet_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), _SNIPPET_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEAFTER", (0, 0), (0, -1), 3, _BRAND_BLUE),
            ]))

            card_content = [
                [header_row],
                [Paragraph(meta_str, s["doc_meta"])],
                [snippet_table],
            ]
            card = Table(card_content, colWidths=[PAGE_W - 2 * MARGIN])
            card.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))

            story.append(card)
            story.append(Spacer(1, 4 * mm))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
