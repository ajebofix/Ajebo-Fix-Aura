# from weasyprint import HTML, CSS
from flask import render_template, current_app
from io import BytesIO

try:
    import weasyprint
except ImportError:
    weasyprint = None

def render_assessment_pdf(*, report_data):
    """
    Renders a finalized vehicle assessment report to PDF.

    INPUT:
        report_data (dict) — output from build_assessment_report()

    OUTPUT:
        BytesIO (PDF)
    """

    # --------------------------------------------
    # Render HTML using Jinja
    # --------------------------------------------
    html_content = render_template(
        "reports/assessment_report.html",
        report=report_data,
    )

    # --------------------------------------------
    # Generate PDF
    # --------------------------------------------
    pdf_file = BytesIO()

    HTML(
        string=html_content,
        base_url=current_app.root_path,
    ).write_pdf(
        pdf_file,
        stylesheets=[
            CSS(
                string="""
                @page {
                    size: A4;
                    margin: 22mm;
                }
                body {
                    font-family: Arial, Helvetica, sans-serif;
                }
                """
            )
        ],
    )

    pdf_file.seek(0)
    return pdf_file

if weasyprint is None:
    raise RuntimeError("PDF generator is not available in this environment.")