# app/services/utils.py
"""
Utility helpers: logo processing, success messages.

FIX: The original file had `logging.basicConfig(level=logging.DEBUG)` at
module level.  This is imported early in the app startup chain, so it ran
before Flask or Sentry had a chance to configure logging.  It set ALL
loggers — including SQLAlchemy, werkzeug, and every third-party library —
to DEBUG level, flooding production logs with internal query details and
request internals.  Removed entirely; Flask configures logging correctly
through its own init sequence.
"""
from PIL import Image
import io
import base64
import logging

logger = logging.getLogger(__name__)


def process_uploaded_logo(logo_file, max_kb=150, max_width=150, max_height=150):
    """
    Process and validate an uploaded logo file.

    - Enforces size limit (bytes check before PIL opens the file)
    - Strips alpha channel (WeasyPrint handles JPEG reliably)
    - Resizes to max dimensions
    - Returns base64-encoded JPEG string, or None if no file given
    - Raises ValueError with a user-friendly message on invalid input
    """
    if not logo_file or not logo_file.filename:
        return None

    logo_file.seek(0, io.SEEK_END)
    size_kb = logo_file.tell() / 1024
    logo_file.seek(0)

    if size_kb > max_kb:
        raise ValueError(
            f"Logo too large: {size_kb:.1f}KB (max {max_kb}KB). Please use a smaller image."
        )

    try:
        img = Image.open(logo_file)
        logger.debug(f"Logo upload: format={img.format}, size={img.size}, mode={img.mode}")

        # Strip alpha channel — WeasyPrint handles JPEG cleanly
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")

        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=85, optimize=True)

        final_kb = len(buffered.getvalue()) / 1024
        logger.debug(f"Logo processed: JPEG {final_kb:.1f}KB")

        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    except ValueError:
        raise  # re-raise our own validation errors untouched
    except Exception as e:
        logger.error(f"Logo processing failed: {e}")
        raise ValueError("Invalid image file. Please use a JPG or PNG under 150KB.")


# ---------------------------------------------------------------------------
# Success messages
# ---------------------------------------------------------------------------

SUCCESS_MESSAGES = {
    'invoice_created': [
        "🎉 Invoice created! You're a billing boss!",
        "💰 Cha-ching! Another invoice done!",
        "✨ Invoice magic complete!",
        "🚀 Invoice sent to the moon!",
        "🎊 You're on fire! Invoice created!",
    ],
    'stock_updated': [
        "📦 Stock updated! Inventory ninja at work!",
        "✅ Stock levels looking good!",
        "🎯 Bullseye! Stock updated perfectly!",
        "💪 Stock management on point!",
    ],
    'login': [
        "🎉 Welcome back, superstar!",
        "👋 Great to see you again!",
        "✨ You're logged in! Let's make money!",
        "🚀 Ready to conquer the day?",
    ],
    'product_added': [
        "📦 Product added! Your inventory grows!",
        "✨ New product in the house!",
        "🎉 Inventory expanded successfully!",
        "💪 Another product conquered!",
    ],
}


def random_success_message(category: str = 'default') -> str:
    import random
    messages = SUCCESS_MESSAGES.get(category, SUCCESS_MESSAGES['invoice_created'])
    return random.choice(messages)
