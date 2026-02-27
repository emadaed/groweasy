# app/services/tasks.py
from celery import Celery
import qrcode
import base64
from pathlib import Path
from app import logger, generate_simple_qr  # move to app/utils/qr.py
from app.services.qr_engine import generate_qr_base64
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
import os
from io import BytesIO
from config import Config
from app.services.services import InvoiceService

# Initialize Celery using the Config class
celery = Celery('groweasy',
                broker=Config.CELERY_BROKER_URL,
                backend=Config.CELERY_RESULT_BACKEND)
celery.conf.broker_connection_retry_on_startup = True
print(f"✅ Celery broker URL: {celery.conf.broker_url}")

@celery.task
def generate_preview(user_id, data):
    service = InvoiceService(user_id)
    service.data = data
    qr_b64 = generate_simple_qr(data)   # make sure this function is imported/defined
    result = {'qr': qr_b64, 'success': True}
    service.redis_client.setex(f"preview:{user_id}", 300, json.dumps(result))
    return result


# app/services/tasks.py (append at the end)
from app.services.ai_orchestrator import AIOrchestrator
from app.services.ai_context import fetch_general_metrics
from celery.schedules import crontab

# ... existing code ...

@celery.task
def generate_daily_tips():
    """
    Runs every 12 hours to generate business tips for all users.
    """
    from app.services.db import DB_ENGINE
    from sqlalchemy import text

    with DB_ENGINE.connect() as conn:
        user_ids = conn.execute(text("SELECT id FROM users")).fetchall()

    for (user_id,) in user_ids:
        try:
            # Get general metrics
            metrics = fetch_general_metrics(user_id)
            # Build prompts
            system = "You are an expert business advisor. Based on the user's current metrics, give 3 actionable tips to improve business performance."
            user = f"Metrics: Revenue {metrics['revenue']}, Expenses {metrics['expenses']}, Profit {metrics['profit']}, Inventory Value {metrics['inventory_value']}. Provide 3 concise tips."
            # Use orchestrator (start with groq, fallback)
            orchestrator = AIOrchestrator()
            tips = orchestrator.generate_insights(system, user, use_deep_history=False)

            # Store in DB
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    INSERT INTO ai_insights (user_id, insight_type, content, status)
                    VALUES (:uid, 'cached_tips', :content, 'completed')
                    ON CONFLICT (user_id, insight_type) DO UPDATE
                    SET content = EXCLUDED.content, updated_at = NOW()
                """), {"uid": user_id, "content": tips})
        except Exception as e:
            logger.error(f"Failed to generate tips for user {user_id}: {e}")

# Add beat schedule (if using celery beat)
celery.conf.beat_schedule = {
    'generate-daily-tips': {
        'task': 'app.services.tasks.generate_daily_tips',
        'schedule': crontab(minute=0, hour='*/12'),  # every 12 hours
    },
}

# app/services/tasks.py (append)
from app.services.ai_orchestrator import AIOrchestrator
from app.services.ai_context import fetch_context
import json

@celery.task(bind=True, max_retries=3)
def process_ai_query(self, user_id, user_question, use_deep=False):
    """
    Celery task for on‑demand AI query.
    """
    try:
        # 1. Fetch context based on question
        extra_system, context = fetch_context(user_id, user_question)

        # 2. Build system prompt
        system_prompt = (
            "You are an expert ERP business analyst. Provide concise, actionable insights "
            "based on the provided data. Answer the user's question directly. "
            + extra_system
        )

        # 3. Build user prompt with context
        context_str = json.dumps(context, indent=2)
        user_prompt = f"User question: {user_question}\n\nRelevant business data:\n{context_str}"

        # 4. Call orchestrator
        orchestrator = AIOrchestrator()
        answer = orchestrator.generate_insights(system_prompt, user_prompt, use_deep_history=use_deep)

        # 5. Store result
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO ai_insights (user_id, task_id, insight_type, content, status)
                VALUES (:uid, :task_id, 'query', :content, 'completed')
            """), {
                'uid': user_id,
                'task_id': self.request.id,
                'content': answer
            })
        return {"status": "success", "answer": answer}

    except Exception as exc:
        # Log failure
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO ai_insights (user_id, task_id, insight_type, status)
                VALUES (:uid, :task_id, 'query', 'failed')
            """), {
                'uid': user_id,
                'task_id': self.request.id
            })
        # Retry on rate limit (429)
        if "429" in str(exc):
            raise self.retry(exc=exc, countdown=60)
        raise exc



