"""Shared Just EdTech PDF report branding for WeasyPrint renderers.

Matches the heatmap report branding (apps/tenants heatmapReport.ts):
A4 portrait, 40 pt side margins, first-page dark header + four-colour
accent rule, continuation-page accent strip, and branded footer.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "Logo.jpeg"

BRAND_NAME = "Just EdTech"
BRAND_TAGLINE = "(A public benefit subsidiary of Education Justice Academy)"
BRAND_FOOTER = f"{BRAND_NAME} {BRAND_TAGLINE}"

BRAND_DARK = "#111614"
BRAND_DARK_TEXT = "#BEC6C3"
BRAND_NAVY = "#0B3C8A"
BODY_TEXT = "#374151"
FOOTER_MUTED = "#6B7280"
PAGE_NUM_COLOR = "#9CA3AF"
FOOTER_RULE = "#E5E7EB"
LINK_COLOR = "#2563EB"
ACCENT_COLORS = ("#7A64EB", "#52C4AA", "#EE5064", "#F5CD50")

HEADER_H_PT = 72
ACCENT_H_PT = 3
LOGO_SIZE_PT = 44
LOGO_BLEED_PT = 5
SIDE_MARGIN_PT = 40
CONTENT_GAP_AFTER_HEADER_PT = 32


@lru_cache(maxsize=1)
def logo_data_uri() -> str | None:
    """Return a data URI for the brand logo, or None if the file is missing."""
    try:
        data = LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("Brand logo not found at %s; header will omit it", LOGO_PATH)
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _accent_rule_html(*, running: bool = False) -> str:
    segments = "".join(
        f'<span class="accent-seg accent-seg-{idx}"></span>'
        for idx in range(len(ACCENT_COLORS))
    )
    running_attr = ' style="position: running(continuation-accent);"' if running else ""
    return (
        f'<div class="accent-rule"{running_attr}>'
        f'<div class="accent-rule-inner">{segments}</div>'
        f"</div>"
    )


def branded_header_html() -> str:
    """First-page brand band + accent rule, plus a running accent for later pages."""
    logo_uri = logo_data_uri()
    logo_html = ""
    if logo_uri:
        logo_html = (
            f'<div class="logo-frame">'
            f'<img src="{logo_uri}" alt="" />'
            f"</div>"
        )

    return f"""
{_accent_rule_html(running=True)}
<div class="brand-header">
  <div class="brand-header-inner">
    {logo_html}
    <div class="brand-text">
      <div class="brand-name">{BRAND_NAME}</div>
      <div class="brand-tagline">{BRAND_TAGLINE}</div>
    </div>
  </div>
</div>
{_accent_rule_html(running=False)}
"""


def branded_report_css() -> str:
    """Page chrome + body typography shared by conversation and district reports."""
    return f"""
@page {{
    size: A4;
    margin-top: 16pt;
    margin-right: 0;
    margin-bottom: 42pt;
    margin-left: 0;

    @top-left {{
        content: element(continuation-accent);
        vertical-align: top;
        width: 100%;
    }}

    @bottom-left {{
        content: "{BRAND_FOOTER}";
        font-family: Helvetica, Arial, sans-serif;
        font-size: 7.5pt;
        color: {FOOTER_MUTED};
        vertical-align: top;
        border-top: 0.5pt solid {FOOTER_RULE};
        padding-top: 8pt;
        padding-left: {SIDE_MARGIN_PT}pt;
        width: 70%;
    }}

    @bottom-right {{
        content: "Page " counter(page) " of " counter(pages);
        font-family: Helvetica, Arial, sans-serif;
        font-size: 8pt;
        color: {PAGE_NUM_COLOR};
        vertical-align: top;
        border-top: 0.5pt solid {FOOTER_RULE};
        padding-top: 8pt;
        padding-right: {SIDE_MARGIN_PT}pt;
        width: 30%;
        text-align: right;
    }}
}}

@page :first {{
    margin-top: 0;

    @top-left {{
        content: none;
    }}
}}

body {{
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10pt;
    color: {BODY_TEXT};
    line-height: 1.5;
    margin: 0;
}}

.brand-header {{
    background: {BRAND_DARK};
    height: {HEADER_H_PT}pt;
    width: 100%;
}}

.brand-header-inner {{
    display: flex;
    align-items: center;
    height: {HEADER_H_PT}pt;
    padding: 0 {SIDE_MARGIN_PT}pt;
    gap: 14pt;
}}

.logo-frame {{
    width: {LOGO_SIZE_PT}pt;
    height: {LOGO_SIZE_PT}pt;
    overflow: hidden;
    flex-shrink: 0;
}}

.logo-frame img {{
    width: {LOGO_SIZE_PT + 2 * LOGO_BLEED_PT}pt;
    height: {LOGO_SIZE_PT + 2 * LOGO_BLEED_PT}pt;
    margin: -{LOGO_BLEED_PT}pt 0 0 -{LOGO_BLEED_PT}pt;
    display: block;
}}

.brand-text {{
    display: flex;
    flex-direction: column;
    justify-content: center;
}}

.brand-name {{
    font-family: Helvetica, Arial, sans-serif;
    font-weight: bold;
    font-size: 17pt;
    color: #ffffff;
    line-height: 1.15;
}}

.brand-tagline {{
    font-family: Helvetica, Arial, sans-serif;
    font-size: 8.5pt;
    color: {BRAND_DARK_TEXT};
    line-height: 1.2;
    margin-top: 2pt;
}}

.accent-rule {{
    width: 100%;
    height: {ACCENT_H_PT}pt;
}}

.accent-rule-inner {{
    display: flex;
    width: 100%;
    height: {ACCENT_H_PT}pt;
}}

.accent-seg {{
    flex: 1;
    height: {ACCENT_H_PT}pt;
    display: block;
}}

.accent-seg-0 {{ background: {ACCENT_COLORS[0]}; }}
.accent-seg-1 {{ background: {ACCENT_COLORS[1]}; }}
.accent-seg-2 {{ background: {ACCENT_COLORS[2]}; }}
.accent-seg-3 {{ background: {ACCENT_COLORS[3]}; }}

.report-content {{
    padding: {CONTENT_GAP_AFTER_HEADER_PT}pt {SIDE_MARGIN_PT}pt 0 {SIDE_MARGIN_PT}pt;
}}

h1 {{
    font-size: 18pt;
    color: {BRAND_NAVY};
    margin: 0 0 12px 0;
    font-weight: bold;
}}

h2 {{
    font-size: 13pt;
    color: {BRAND_NAVY};
    margin: 22px 0 8px 0;
    font-weight: bold;
}}

h3 {{
    font-size: 11pt;
    color: {BRAND_NAVY};
    margin: 14px 0 6px 0;
    font-weight: bold;
}}

p {{
    margin: 0 0 10px 0;
    text-align: justify;
}}

ul, ol {{
    margin: 6px 0;
    padding-left: 22px;
}}

li {{
    margin: 4px 0;
}}

strong {{
    font-weight: bold;
    color: {BODY_TEXT};
}}

em {{
    font-style: italic;
}}

a {{
    color: {LINK_COLOR};
    text-decoration: underline;
}}

hr {{
    border: none;
    border-top: 1px solid #E5E7EB;
    margin: 14px 0;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin: 10px 0;
}}

th {{
    background: {BRAND_NAVY};
    color: #ffffff;
    font-weight: bold;
    text-align: left;
    padding: 6px 8px;
}}

td {{
    padding: 6px 8px;
    border-bottom: 0.5pt solid #E5E7EB;
    color: {BODY_TEXT};
}}

.meta {{
    color: {FOOTER_MUTED};
    font-size: 9pt;
}}
"""


def wrap_branded_document(body_inner_html: str, extra_css: str = "") -> str:
    """Assemble a full HTML document with brand chrome around report body markup."""
    return "\n".join(
        [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            '<meta charset="UTF-8">',
            "<style>",
            branded_report_css(),
            extra_css,
            "</style>",
            "</head>",
            "<body>",
            branded_header_html(),
            '<div class="report-content">',
            body_inner_html,
            "</div>",
            "</body>",
            "</html>",
        ]
    )
