# core/pdf_generator.py - FINAL CLEAN VERSION

import logging
from datetime import datetime
from flask import render_template, request
from pathlib import Path
import base64
import json
from core.pdf_engine import generate_pdf
from core.qr_engine import generate_qr_base64

logger = logging.getLogger(__name__)

def generate_invoice_pdf(service_data):
    return _generate_pdf(service_data, template="invoice_pdf.html")

def generate_purchase_order_pdf(service_data):
    return _generate_pdf(service_data, template="purchase_order_pdf.html")

def _generate_pdf(service_data, template):
    try:
        # Ensure items is a list (critical fix for multiple items)
        items = service_data.get('items', [])
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except:
                items = []
        service_data['items'] = items

        # Generate QR
        doc_number = service_data.get('invoice_number') or service_data.get('po_number', 'INV-001')
        payment_data = f"Payment for {doc_number}"
        logo_path = "static/images/logo.png"

        custom_qr_b64 = generate_qr_base64(
            data=payment_data,
            logo_path=logo_path if Path(logo_path).exists() else None,
            fill_color="#2c5aa0",
            back_color="white"
        )

        # Load logo for header
        logo_b64 = None
        possible_paths = [
            "static/images/logo.png",
            "static/img/logo.png",
            "static/assets/logo.png",
            "static/logo.png"
        ]
        for path in possible_paths:
            if Path(path).exists():
                with open(path, "rb") as f:
                    logo_b64 = base64.b64encode(f.read()).decode('utf-8')
                break

        # Context
        context = {
            "data": service_data,
            "custom_qr_b64": custom_qr_b64,
            "logo_b64": logo_b64,
            "currency_symbol": service_data.get('currency_symbol', 'Rs.'),
        }

        # Render
        rendered_html = render_template(template, **context)

        # Base URL
        base_url = request.url_root if request else "https://growe.up.railway.app/"

        # Generate PDF
        pdf_bytes = generate_pdf(rendered_html, base_url=base_url)

        logger.info(f"PDF generated: {len(pdf_bytes)} bytes")
        return pdf_bytes

    except Exception as e:
        logger.error(f"PDF error: {e}", exc_info=True)
        error_html = "<html><body><h2>PDF Generation Failed</h2><p>Please try again.</p></body></html>"
        return generate_pdf(error_html)
