# app.services.tasks.py
from celery import shared_task
from app.services.ai_service import get_gemini_insights
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
from celery import Celery
from app.services.services import InvoiceService
from flask import current_app

celery = Celery('groweasy')

@celery.task
def generate_preview(user_id, data):
    service = InvoiceService(user_id)
    service.data = data
    qr_b64 = generate_simple_qr(data)
    # Store result in Redis
    result = {'qr': qr_b64, 'success': True}
    service.redis_client.setex(f"preview:{user_id}", 300, json.dumps(result))
    return result

@shared_task(bind=True, max_retries=3)
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
        # If rate limited (429), retry in 60 seconds
        if "429" in str(exc):
            raise self.retry(exc=exc, countdown=60)
        
        with DB_ENGINE.begin() as conn:
            conn.execute(text("UPDATE ai_insights SET status = 'failed' WHERE user_id = :uid"), {'uid': user_id})
        raise exc
