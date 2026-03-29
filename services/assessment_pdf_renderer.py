# from weasyprint import HTML, CSS
# from flask import render_template, current_app
# from io import BytesIO


# def render_assessment_pdf(*, report_data):

#     # ----------------------------------------
#     # Render HTML
#     # ----------------------------------------

#     html_content = render_template(
#         "reports/assessment_report.html",
#         report=report_data,
#     )

#     # ----------------------------------------
#     # Convert to PDF
#     # ----------------------------------------

#     pdf_file = BytesIO()

#     HTML(
#         string=html_content,
#         base_url=current_app.root_path,
#     ).write_pdf(
#         pdf_file,
#         stylesheets=[
#             CSS(
#                 string="""
#                 @page {
#                     size: A4;
#                     margin: 20mm;
#                 }
#                 body {
#                     font-family: Ariel, sans-serif;
#                 }
#                 """
#             )
#         ],
#     )

#     pdf_file.seek(0)

#     return pdf_file
