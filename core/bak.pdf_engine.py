# core/pdf_engine.py - Updated for WeasyPrint 66.0
import io
import logging
from pathlib import Path
from weasyprint import HTML, CSS
from weasyprint.text.fonts import FontConfiguration  # ← Fixed import for v66+

logger = logging.getLogger(__name__)

HAS_WEASYPRINT = True
logger.info("✅ WeasyPrint 66 loaded - ready for perfect PDFs")

def generate_pdf(html_content, base_url=None):
    try:
        font_config = FontConfiguration()

        css = CSS(string='''
            @page { size: A4; margin: 15mm; }
            body { font-family: Arial, Helvetica, sans-serif; line-height: 1.4; }
            table { width: 100%; border-collapse: collapse; }
            th, td { border: 1px solid #ddd; padding: 8px; }
            img { max-width: 100%; height: auto; image-rendering: crisp-edges; }
            @media print {
                .no-print { display: none !important; }
            }
        ''', font_config=font_config)

        if base_url is None:
            base_url = str(Path(__file__).parent.parent.resolve())

        html = HTML(string=html_content, base_url=base_url)

        buffer = io.BytesIO()
        html.write_pdf(buffer, stylesheets=[css], font_config=font_config)
        buffer.seek(0)

        pdf_bytes = buffer.getvalue()
        logger.info(f"✅ PDF generated: {len(pdf_bytes)} bytes")
        return pdf_bytes

    except Exception as e:
        logger.error(f"WeasyPrint error: {e}", exc_info=True)
        error_html = f"""
        <html><body style="font-family:Arial;padding:50px;text-align:center;">
        <h2>PDF Generation Failed</h2>
        <p>{str(e)}</p>
        <p>Please try again.</p>
        </body></html>
        """
        return generate_pdf(error_html)  # Recursive fallback
