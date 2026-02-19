# app.services.tasks.py
from celery import Celery
import qrcode
import base64
from pathlib import Path
from app.services.qr_engine import generate_qr_base64
from app.services.ai_service import get_gemini_insights
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
import os
from config import Config
from app.services.services import InvoiceService
from flask import current_app

# Initialize Celery using the Config class we just updated
celery = Celery('groweasy', 
                broker=Config.CELERY_BROKER_URL, 
                backend=Config.CELERY_RESULT_BACKEND)
print(f"✅ Celery broker URL: {celery.conf.broker_url}")


@celery.task
def generate_preview(user_id, data):
    service = InvoiceService(user_id)
    service.data = data
    qr_b64 = generate_simple_qr(data)
    # Store result in Redis
    result = {'qr': qr_b64, 'success': True}
    service.redis_client.setex(f"preview:{user_id}", 300, json.dumps(result))
    return result

@celery.task(bind=True, max_retries=3)
def process_ai_insight(self, user_id, data, custom_prompt=None):
    """Background task to call Gemini and save results to DB"""
    try:
        # 1. Call the AI
        response_text = get_gemini_insights(data, custom_prompt=custom_prompt)
        
        # 2. Update the DB
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE ai_insights 
                SET content = :content, status = 'completed', updated_at = CURRENT_TIMESTAMP
                WHERE user_id = :uid AND status = 'pending'
            """), {'content': response_text, 'uid': user_id})
            
        return {"status": "success"}
    except Exception as exc:
        if "429" in str(exc):
            raise self.retry(exc=exc, countdown=60)
        
        with DB_ENGINE.begin() as conn:
            conn.execute(text("UPDATE ai_insights SET status = 'failed' WHERE user_id = :uid"), {'uid': user_id})
        raise exc
