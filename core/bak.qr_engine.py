# core/qr_engine.py - Final Version (Compatible + Modern)

import qrcode
from PIL import Image
from io import BytesIO
import base64
from pathlib import Path

def generate_qr_base64(data, logo_path=None, fill_color="black", back_color="white"):
    """Modern function: returns base64 string for WeasyPrint"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color=fill_color, back_color=back_color).convert("RGB")

    if logo_path and Path(logo_path).exists():
        try:
            logo = Image.open(logo_path)
            logo_size = int(img.size[0] * 0.2)
            logo = logo.resize((logo_size, logo_size))
            pos = ((img.size[0] - logo_size) // 2, (img.size[1] - logo_size) // 2)
            img.paste(logo, pos, logo if logo.mode in ('RGBA', 'LA') else None)
        except Exception as e:
            print(f"Logo error: {e}")

    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

# Compatibility alias for old code
def make_qr_with_logo(data_text, logo_path=None, output_path=None):
    """
    Old function kept for backward compatibility
    Now returns base64 instead of saving file
    """
    base64_str = generate_qr_base64(data_text, logo_path=logo_path)
    # If old code expects file save, ignore output_path (we don't need it anymore)
    return base64_str
