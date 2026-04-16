# app/utils/qr.py
"""
QR code and document utility helpers.

Previously these lived in app/__init__.py which caused circular import risk
as the codebase grew. Moved here so imports are clean and predictable.

Usage:
    from app.utils.qr import generate_simple_qr, clear_pending_invoice, template_exists
"""

import json
import base64


def generate_simple_qr(data):
    """Generate a QR code for a document and return it as a base64 PNG string."""
    try:
        import qrcode
        from io import BytesIO

        qr_data = {
            'doc_number': data.get('invoice_number', ''),
            'date': data.get('invoice_date', ''),
            'total': data.get('grand_total', 0)
        }

        qr = qrcode.QRCode(version=1, box_size=5, border=2)
        qr.add_data(json.dumps(qr_data))
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")

        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        # Non-fatal: QR is cosmetic. Log and return None so callers can skip it.
        import logging
        logging.getLogger(__name__).error(f"QR generation error: {e}")
        return None


def clear_pending_invoice(user_id):
    """Clear pending invoice data from session storage."""
    try:
        from app.services.session_storage import SessionStorage
        SessionStorage.clear_data(user_id, 'last_invoice')
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error clearing pending invoice: {e}")
        return False


def template_exists(template_name):
    """Return True if a Jinja2 template exists in the current app."""
    try:
        from flask import current_app
        current_app.jinja_env.get_template(template_name)
        return True
    except Exception:
        return False
