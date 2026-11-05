"""Downloadable renovation report (requirement 5.8).

Built to be handed to a contractor: it shows the original and redesigned
photos, the measured surfaces *with the method and error band used*, the
quantities with their formulas, and a full cost breakdown -- then closes with
the assumptions and limitations, so nobody mistakes it for a quotation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .catalog import limitations as catalog_limitations
from .costing import CostEstimate
from .quantities import QuantityLine

INK = colors.HexColor("#1F2933")
MUTED = colors.HexColor("#6B7A8C")
ACCENT = colors.HexColor("#1D5C96")
RULE = colors.HexColor("#D5DCE4")
BAND = colors.HexColor("#F2F5F9")

#: Fonts that actually carry the rupee sign (U+20B9). ReportLab's built-in
#: Helvetica does not, and would silently draw a black box instead.
_FONT_CANDIDATES = [
    ("DejaVuSans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", ["C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu"]),
    ("Arial", "arial.ttf", "arialbd.ttf", ["C:/Windows/Fonts"]),
    ("SegoeUI", "segoeui.ttf", "segoeuib.ttf", ["C:/Windows/Fonts"]),
]


def _register_fonts() -> tuple[str, str, bool]:
    """Return (regular, bold, unicode_ok)."""
    for family, regular, bold, dirs in _FONT_CANDIDATES:
        for d in dirs:
            reg_path, bold_path = Path(d) / regular, Path(d) / bold
            if reg_path.exists():
                try:
                    pdfmetrics.registerFont(TTFont(family, str(reg_path)))
                    bold_name = family
                    if bold_path.exists():
                        bold_name = f"{family}-Bold"
                        pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
                    return family, bold_name, True
                except Exception:  # pragma: no cover - font loading is best effort
                    continue
    return "Helvetica", "Helvetica-Bold", False


FONT, FONT_BOLD, UNICODE_OK = _register_fonts()


def _money_prefix(symbol: str) -> str:
    """Fall back to 'Rs.' when the embedded font cannot draw the symbol."""
    if symbol == "\u20b9" and not UNICODE_OK:
        return "Rs. "
    return symbol


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=FONT_BOLD, fontSize=22,
            leading=26, textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName=FONT, fontSize=10.5,
            leading=14, textColor=MUTED, spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName=FONT_BOLD, fontSize=13,
            leading=16, textColor=ACCENT, spaceBefore=14, spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName=FONT_BOLD, fontSize=10.5,
            leading=13, textColor=INK, spaceBefore=8, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName=FONT, fontSize=9.2,
            leading=13, textColor=INK,
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontName=FONT, fontSize=7.8,
            leading=10.5, textColor=MUTED,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName=FONT, fontSize=8.2, leading=11,
            textColor=INK,
        ),
        "cellsmall": ParagraphStyle(
            "cellsmall", parent=base["Normal"], fontName=FONT, fontSize=7.2,
            leading=9.5, textColor=MUTED,
        ),
    }


def _table(data: list[list[Any]], widths: list[float], align_right: Sequence[int] = ()):
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), 8.2),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 1), (-1, -1), FONT),
        ("FONTSIZE", (0, 1), (-1, -1), 8.2),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
    ]
    for col in align_right:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _fit_image(path: Path, max_w: float, max_h: float) -> RLImage | None:
    if not path or not Path(path).exists():
        return None
    try:
        reader = ImageReader(str(path))
        iw, ih = reader.getSize()
    except Exception:
        return None
    scale = min(max_w / iw, max_h / ih)
    return RLImage(str(path), width=iw * scale, height=ih * scale)


def _footer(canvas, doc):  # noqa: ANN001
    canvas.saveState()
    canvas.setFont(FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(
        18 * mm, 12 * mm,
        "Advisory estimate produced by the AI Exterior Renovation planner - not a quotation.",
    )
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, 15 * mm, A4[0] - 18 * mm, 15 * mm)
    canvas.restoreState()


def build_report(
    path: Path,
    *,
    project_name: str,
    original_image: Path | None,
    render_image: Path | None,
    regions: list[dict[str, Any]],
    quantities: list[QuantityLine],
    estimate: CostEstimate,
    materials_used: list[dict[str, Any]],
    cost_settings: dict[str, Any],
    warnings: Sequence[str] = (),
    catalog_disclaimer: str = "",
) -> Path:
    st = _styles()
    sym = _money_prefix(estimate.currency_symbol)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title=f"Renovation estimate - {project_name}",
        author="AI Exterior Renovation & Cost Estimation System",
    )
    content_w = doc.width
    flow: list[Any] = []

    def money(v: float) -> str:
        return f"{sym}{v:,.0f}"

    # ---------------- header ------------------------------------------
    flow.append(Paragraph("Exterior Renovation Estimate", st["title"]))
    flow.append(
        Paragraph(
            f"<b>{project_name}</b> &nbsp;|&nbsp; prepared "
            f"{datetime.now().strftime('%d %B %Y')}",
            st["subtitle"],
        )
    )

    # ---------------- headline numbers --------------------------------
    # "Linear work" (railings, roof edges) only earns its column when a
    # project actually has some -- most are wall/roof jobs with none, and a
    # column that reads 0 rft on every report is noise, not information.
    headline_cols = ["Estimated project cost", "Likely range", "Surface measured"]
    headline_row = [
        money(estimate.grand_total),
        f"{money(estimate.range_low)} - {money(estimate.range_high)}",
        f"{estimate.total_area_sqft:,.0f} sq ft",
    ]
    headline_widths = [content_w * 0.28, content_w * 0.32, content_w * 0.40]
    if estimate.total_length_ft > 0:
        headline_cols.append("Linear work")
        headline_row.append(f"{estimate.total_length_ft:,.0f} rft")
        headline_widths = [content_w * 0.28, content_w * 0.32, content_w * 0.22, content_w * 0.18]

    headline = _table([headline_cols, headline_row], headline_widths)
    headline.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 1), (-1, 1), FONT_BOLD),
                ("FONTSIZE", (0, 1), (-1, 1), 12),
                ("TEXTCOLOR", (0, 1), (0, 1), ACCENT),
                ("BACKGROUND", (0, 1), (-1, 1), BAND),
            ]
        )
    )
    flow.append(headline)

    if warnings:
        flow.append(Spacer(1, 7))
        flow.append(
            Paragraph(
                "<b>Before you rely on this:</b> "
                + " &nbsp;&bull;&nbsp; ".join(warnings),
                st["small"],
            )
        )

    # ---------------- images -------------------------------------------
    flow.append(Paragraph("Before and after", st["h2"]))
    cell_w = (content_w - 8) / 2
    before = _fit_image(original_image, cell_w, 95 * mm)
    after = _fit_image(render_image, cell_w, 95 * mm)
    if before or after:
        row = [[before or Paragraph("No photo", st["cell"]),
                after or Paragraph("Not rendered yet", st["cell"])]]
        img_table = Table(
            [["Original photograph", "With selected materials"]] + row,
            colWidths=[cell_w, cell_w], hAlign="LEFT",
        )
        img_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
                    ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                    ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                    ("VALIGN", (0, 1), (-1, 1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ]
            )
        )
        flow.append(img_table)
        flow.append(Spacer(1, 4))
        flow.append(
            Paragraph(
                "The redesigned image re-surfaces the regions you marked using "
                "the original photograph's own lighting and perspective. Building "
                "geometry, windows and roofline are never altered.",
                st["small"],
            )
        )

    # ---------------- materials ------------------------------------------
    if materials_used:
        flow.append(Paragraph("Selected materials", st["h2"]))
        rows = [["Surface", "Material", "Durability", "Maintenance"]]
        for m in materials_used:
            dur = m.get("durability_years") or [0, 0]
            dur_txt = f"{dur[0]}-{dur[1]} yrs" if dur and dur[1] else "-"
            rows.append(
                [
                    Paragraph(m.get("regions", ""), st["cell"]),
                    Paragraph(f"<b>{m.get('name','')}</b>", st["cell"]),
                    dur_txt,
                    Paragraph(m.get("maintenance", ""), st["cellsmall"]),
                ]
            )
        flow.append(
            _table(
                rows,
                [content_w * 0.26, content_w * 0.26, content_w * 0.13, content_w * 0.35],
            )
        )

    # ---------------- measured surfaces ------------------------------------
    flow.append(PageBreak())
    flow.append(Paragraph("Measured surfaces", st["h2"]))
    flow.append(
        Paragraph(
            "Each surface shows how its size was arrived at. Measurements taken "
            "from a dimension you supplied are the most reliable; those scaled "
            "from a reference object carry more uncertainty; anything marked as "
            "a rough estimate should be checked before you commit money to it.",
            st["body"],
        )
    )
    flow.append(Spacer(1, 6))
    rows = [["Surface", "Type", "Size", "How it was measured", "Accuracy"]]
    for r in regions:
        size = (
            f"{r['area_sqft']:,.0f} sq ft"
            if r.get("area_sqft")
            else (f"{r['length_ft']:,.1f} rft" if r.get("length_ft") else "-")
        )
        lo, hi = r.get("error_low", 0), r.get("error_high", 0)
        rows.append(
            [
                Paragraph(r.get("name", ""), st["cell"]),
                r.get("category_label", ""),
                size,
                Paragraph(r.get("method_label", ""), st["cellsmall"]),
                f"-{lo * 100:.0f}% / +{hi * 100:.0f}%",
            ]
        )
    flow.append(
        _table(
            rows,
            [content_w * 0.24, content_w * 0.15, content_w * 0.15, content_w * 0.30, content_w * 0.16],
            align_right=(2,),
        )
    )

    # ---------------- quantities --------------------------------------------
    flow.append(Paragraph("Material quantities", st["h2"]))
    rows = [["Material", "Quantity", "To purchase", "How it was worked out"]]
    for q in quantities:
        buy = ""
        if q.packs:
            buy = " + ".join(f"{p.count} x {p.size:g} L" for p in q.packs)
        elif q.boxes:
            buy = f"{q.pieces} tiles ({q.boxes} boxes)"
        else:
            buy = f"{q.purchase_quantity:,.1f} {q.unit}"
        label = q.material_name + (" (included)" if q.derived else "")
        rows.append(
            [
                Paragraph(label, st["cell"]),
                f"{q.quantity:,.2f} {q.unit}",
                Paragraph(buy, st["cell"]),
                Paragraph(q.formula, st["cellsmall"]),
            ]
        )
    flow.append(
        _table(
            rows,
            [content_w * 0.22, content_w * 0.15, content_w * 0.18, content_w * 0.45],
            align_right=(1,),
        )
    )

    # ---------------- cost ---------------------------------------------------
    flow.append(PageBreak())
    flow.append(Paragraph("Cost breakdown", st["h2"]))
    rows = [["Item", "Qty", "Rate", "Material", "Labour", "Total"]]
    for ln in estimate.lines:
        rows.append(
            [
                Paragraph(
                    ln.material_name + (" <i>(included)</i>" if ln.derived else ""),
                    st["cell"],
                ),
                f"{ln.quantity:,.1f} {ln.unit}",
                f"{sym}{ln.material_rate:,.0f}",
                money(ln.material_cost),
                money(ln.labour_cost),
                money(ln.total),
            ]
        )
    rows.append(["", "", "", money(estimate.material_subtotal),
                 money(estimate.labour_subtotal), money(estimate.subtotal)])
    cost_table = _table(
        rows,
        [content_w * 0.28, content_w * 0.14, content_w * 0.12,
         content_w * 0.15, content_w * 0.14, content_w * 0.17],
        align_right=(1, 2, 3, 4, 5),
    )
    cost_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
                ("BACKGROUND", (0, -1), (-1, -1), BAND),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT),
            ]
        )
    )
    flow.append(cost_table)

    # add-ons + grand total
    rows = [["Added to the works subtotal", "Basis", "Amount"]]
    rows.append(["Works subtotal (materials + labour)", "", money(estimate.subtotal)])
    for a in estimate.addons:
        rows.append([a.label, Paragraph(a.basis, st["cellsmall"]), money(a.amount)])
    rows.append(["Estimated project total", "", money(estimate.grand_total)])
    totals = _table(
        rows, [content_w * 0.42, content_w * 0.36, content_w * 0.22], align_right=(2,)
    )
    totals.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, -1), (-1, -1), FONT_BOLD),
                ("FONTSIZE", (0, -1), (-1, -1), 11),
                ("TEXTCOLOR", (0, -1), (-1, -1), ACCENT),
                ("BACKGROUND", (0, -1), (-1, -1), BAND),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT),
            ]
        )
    )
    flow.append(Spacer(1, 8))
    flow.append(totals)
    flow.append(Spacer(1, 5))
    flow.append(
        Paragraph(
            f"Allowing for measurement uncertainty, budget between "
            f"<b>{money(estimate.range_low)}</b> and "
            f"<b>{money(estimate.range_high)}</b>. That range covers error in the "
            "measured areas only - it does not cover differences between "
            "contractors' rates.",
            st["body"],
        )
    )

    # ---------------- assumptions & limitations ------------------------------
    flow.append(PageBreak())
    flow.append(Paragraph("Assumptions and limitations", st["h2"]))
    flow.append(
        Paragraph(
            "This estimate was produced from a single photograph, with no site "
            "visit, no drawings and no measuring equipment. Everything below "
            "affects how far you should trust the numbers.",
            st["body"],
        )
    )
    flow.append(Spacer(1, 6))

    items = list(catalog_limitations())
    for note in estimate.notes:
        items.append(note)
    if catalog_disclaimer:
        items.append(catalog_disclaimer)

    for text in items:
        flow.append(
            Paragraph(f"&bull;&nbsp;&nbsp;{text}", st["body"])
        )
        flow.append(Spacer(1, 3))

    flow.append(Spacer(1, 8))
    flow.append(Paragraph("Cost settings used", st["h3"]))
    setting_rows = [["Setting", "Value"]]
    pretty = {
        "scaffolding_rate_per_sqft": "Scaffolding rate (per sq ft)",
        "scaffolding_enabled": "Scaffolding included",
        "transport_lumpsum": "Transport & handling (lump sum)",
        "contractor_overhead_pct": "Contractor overhead & profit (%)",
        "contingency_pct": "Contingency (%)",
        "gst_pct": "GST (%)",
        "gst_enabled": "GST applied",
    }
    for key, label in pretty.items():
        if key in cost_settings:
            setting_rows.append([label, str(cost_settings[key])])
    flow.append(
        _table(setting_rows, [content_w * 0.6, content_w * 0.4], align_right=(1,))
    )

    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "<b>This document is a planning aid, not a quotation.</b> Use it to "
            "have a better-informed conversation with your contractor, and ask "
            "them to price against their own measurements.",
            st["body"],
        )
    )

    doc.build(flow, onFirstPage=_footer, onLaterPages=_footer)
    return path
