# core/utils.py - BULLETPROOF VERSION
from PIL import Image
import io
import base64
import logging

logging.basicConfig(level=logging.DEBUG)

def process_uploaded_logo(logo_file, max_kb=150, max_width=150, max_height=150):
    """
    Ultra-safe logo processing:
    - Max 150KB (smaller = safer)
    - Max 150x150px
    - Force JPEG (no transparency, smaller, WeasyPrint loves it)
    - Aggressive optimization
    """
    if not logo_file or not logo_file.filename:
        return None

    logo_file.seek(0, io.SEEK_END)
    size_kb = logo_file.tell() / 1024
    logo_file.seek(0)

    if size_kb > max_kb:
        raise ValueError(f"Logo too large: {size_kb:.1f}KB (max {max_kb}KB). Use smaller image.")

    try:
        img = Image.open(logo_file)
        logging.debug(f"Original logo format: {img.format}, size: {img.size}, mode: {img.mode}")

        # Force RGB (remove alpha)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        # Resize aggressively
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        # Save as JPEG (smaller, no transparency issues)
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85, optimize=True)

        logo_b64_clean = base64.b64encode(buffered.getvalue()).decode('utf-8')
        final_size_kb = len(buffered.getvalue()) / 1024
        logging.debug(f"Processed logo: JPEG, {final_size_kb:.1f}KB")

        return logo_b64_clean

    except Exception as e:
        logging.error(f"Logo processing FAILED: {e}")
        raise ValueError("Invalid image. Try a simple JPG/PNG under 150KB.")
