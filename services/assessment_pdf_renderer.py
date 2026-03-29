from weasyprint import pisa
from flask import render_template, current_app
from io import BytesIO


def render_assessment_pdf(*, report_data):

    # ----------------------------------------
    # Render HTML
    # ----------------------------------------

    html = render_template(
        "reports/assessment_report.html",
        report=report_data,
    )

    html_content = render_template(
        "reports/assessment_report.html",
        report=report_data,
    )

    # ----------------------------------------
    # Convert to PDF
    # ----------------------------------------

    pdf_file = BytesIO()

    pisa_status = pisa.CreatePDF(
        html,
        dest=pdf_file,
        encoding="UTF-8",
    )

    if pisa_status.err:
        raise Exception("PDF generation failed with xhtml2pdf")

    pdf_file.seek(0)

    # return pdf_file
    return html_content
