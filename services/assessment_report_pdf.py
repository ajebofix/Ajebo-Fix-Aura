from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


_REPLACEMENTS = str.maketrans(
    {
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2022": "-",
        "\u00a0": " ",
    }
)


def _plain(value) -> str:
    if value is None:
        return "-"
    text = str(value).translate(_REPLACEMENTS).strip()
    return text or "-"


def _paragraph(value, style):
    return Paragraph(escape(_plain(value)), style)


def _section_title(text, style):
    return Paragraph(escape(text), style)


def _info_table(rows, styles, *, widths=None):
    data = [
        [
            _paragraph(label, styles["table_label"]),
            _paragraph(value, styles["table_value"]),
        ]
        for label, value in rows
    ]
    table = Table(data, colWidths=widths or [46 * mm, 118 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")),
            ]
        )
    )
    return table


def _draw_page_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D1D5DB"))
    canvas.setLineWidth(0.4)
    canvas.line(18 * mm, 14 * mm, A4[0] - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6B7280"))
    canvas.drawString(18 * mm, 9 * mm, "Ajebo Fix - Confidential Vehicle Health Record")
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_assessment_pdf(*, report: dict) -> bytes:
    """Render one owner-safe finalized assessment report as a real PDF file."""

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title="Vehicle Health & Risk Report",
        author="Ajebo Fix",
        subject="Finalized professional vehicle assessment",
    )

    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "AuraTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=21,
            leading=25,
            textColor=colors.HexColor("#111827"),
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "AuraSubtitle",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#6B7280"),
            alignment=TA_CENTER,
            spaceAfter=18,
        ),
        "section": ParagraphStyle(
            "AuraSection",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#111827"),
            spaceBefore=10,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "AuraBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=15,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=7,
        ),
        "muted": ParagraphStyle(
            "AuraMuted",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#6B7280"),
            spaceAfter=6,
        ),
        "table_label": ParagraphStyle(
            "AuraTableLabel",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#6B7280"),
        ),
        "table_value": ParagraphStyle(
            "AuraTableValue",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#111827"),
        ),
    }

    story = []
    title = report["title_page"]
    overview = report["vehicle_overview"]
    health = report["current_health_status"]
    risk = report["risk"]

    story.append(_paragraph("Vehicle Health & Risk Report", styles["title"]))
    story.append(
        _paragraph(
            "Professional assessment summary for informed ownership decisions.",
            styles["subtitle"],
        )
    )
    story.append(
        _info_table(
            [
                ("Powered by", title.get("powered_by", "Ajebo Fix")),
                (
                    "Issued on",
                    title["issued_date"].strftime("%Y-%m-%d")
                    if title.get("issued_date")
                    else "-",
                ),
                ("Vehicle VIN", title.get("vehicle_vin")),
                ("Engine", title.get("engine_number")),
                ("Mileage", title.get("current_mileage")),
            ],
            styles,
        )
    )

    story.append(_section_title("1. Vehicle Overview", styles["section"]))
    story.append(
        _info_table(
            [
                (
                    "Vehicle",
                    " ".join(
                        _plain(part)
                        for part in (
                            overview.get("brand"),
                            overview.get("model"),
                            overview.get("year"),
                        )
                        if part is not None
                    ),
                ),
                ("Engine Type", overview.get("engine_type")),
                ("Transmission", overview.get("transmission")),
                ("Usage Pattern", overview.get("usage_pattern")),
                (
                    "Ownership Duration",
                    f"{overview['ownership_duration']} days"
                    if overview.get("ownership_duration") is not None
                    else "-",
                ),
            ],
            styles,
        )
    )

    story.append(_section_title("2. Current Health Status", styles["section"]))
    story.append(
        _info_table(
            [
                ("Engine System", health.get("engine_system")),
                ("Transmission System", health.get("transmission_system")),
                ("Suspension & Steering", health.get("suspension_and_steering")),
                ("Electrical & Control Systems", health.get("electrical_and_controls")),
                ("Cooling & Lubrication", health.get("cooling_and_lubrication")),
            ],
            styles,
        )
    )
    story.append(
        _paragraph(
            "Health status reflects professional assessment and monitoring. "
            "It does not constitute a mechanical diagnosis.",
            styles["muted"],
        )
    )

    story.append(_section_title("Vehicle Risk Score", styles["section"]))
    story.append(
        _info_table(
            [
                ("Risk Level", risk.get("label")),
                ("Risk Score", f"{risk.get('score', 0)}/100"),
            ],
            styles,
        )
    )

    story.append(_section_title("3. Identified Risks", styles["section"]))
    if report["identified_risks"]:
        for index, item in enumerate(report["identified_risks"], start=1):
            story.append(_paragraph(f"Risk {index}", styles["table_value"]))
            story.append(
                _info_table(
                    [
                        ("Description", item.get("description")),
                        ("Likely Cause", item.get("likely_cause")),
                        ("Potential Consequence", item.get("potential_consequence")),
                    ],
                    styles,
                )
            )
            story.append(Spacer(1, 4 * mm))
    else:
        story.append(_paragraph("No immediate risks identified at this time.", styles["muted"]))

    urgency = report["urgency_classification"]
    story.append(_section_title("4. Urgency Classification", styles["section"]))
    story.append(
        _info_table(
            [
                ("Immediate Attention", ", ".join(urgency.get("immediate_attention") or []) or "None"),
                ("Monitoring Closely", ", ".join(urgency.get("monitoring_closely") or []) or "None"),
                (
                    "Preventive Recommendations",
                    ", ".join(urgency.get("preventive_recommendations") or []) or "None",
                ),
            ],
            styles,
        )
    )

    story.append(_section_title("5. Cost vs Consequence Analysis", styles["section"]))
    story.append(_paragraph(report["cost_vs_consequence"].get("summary"), styles["body"]))

    story.append(_section_title("6. Recommended Treatment Paths", styles["section"]))
    if report["treatment_paths"]:
        for option in report["treatment_paths"]:
            heading = f"Option {_plain(option.get('option_code'))}: {_plain(option.get('title'))}"
            story.append(_paragraph(heading, styles["table_value"]))
            story.append(_paragraph(option.get("description"), styles["body"]))
    else:
        story.append(_paragraph("No treatment paths documented.", styles["muted"]))

    recommendation = report["professional_recommendation"]
    story.append(_section_title("7. Professional Recommendation", styles["section"]))
    story.append(_paragraph(recommendation.get("statement"), styles["body"]))
    story.append(
        _paragraph(
            f"Advisor: {_plain(recommendation.get('advisor'))}",
            styles["muted"],
        )
    )

    if report.get("addenda"):
        story.append(PageBreak())
        story.append(_section_title("8. Corrections & Addenda", styles["section"]))
        story.append(
            _paragraph(
                "The original finalized assessment remains unchanged. The entries below are later, dated professional additions to that record.",
                styles["muted"],
            )
        )
        for addendum in report["addenda"]:
            category = _plain(addendum.get("category")).replace("_", " ").title()
            recorded = (
                addendum["created_at"].strftime("%Y-%m-%d %H:%M")
                if addendum.get("created_at")
                else "-"
            )
            story.append(_paragraph(category, styles["table_value"]))
            story.append(
                _info_table(
                    [
                        ("Recorded", recorded),
                        ("Advisor", addendum.get("advisor")),
                        ("Reason", addendum.get("reason")),
                    ],
                    styles,
                )
            )
            story.append(_paragraph(addendum.get("statement"), styles["body"]))
            story.append(Spacer(1, 4 * mm))

    story.append(Spacer(1, 6 * mm))
    story.append(
        _paragraph(
            "This report reflects professional assessment conducted by Ajebo Fix. "
            "It is intended to support informed decision-making and long-term vehicle care.",
            styles["muted"],
        )
    )

    document.build(
        story,
        onFirstPage=_draw_page_footer,
        onLaterPages=_draw_page_footer,
    )
    return buffer.getvalue()
