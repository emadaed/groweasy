# app/services/tasks.py
from celery import Celery
import qrcode
import base64
from pathlib import Path
from app import generate_simple_qr  # move to app/utils/qr.py
from app.services.qr_engine import generate_qr_base64
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
import os
from io import BytesIO
from config import Config
from app.services.services import InvoiceService
import hashlib
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

### Initialize Celery using the Config class
##celery = Celery('groweasy',
##                broker=Config.CELERY_BROKER_URL,
##                backend=Config.CELERY_RESULT_BACKEND)
##celery.conf.broker_connection_retry_on_startup = True
##print(f"✅ Celery broker URL: {celery.conf.broker_url}")

##@celery.task
def generate_preview(user_id, data):
    service = InvoiceService(user_id)
    service.data = data
    qr_b64 = generate_simple_qr(data)   # make sure this function is imported/defined
    result = {'qr': qr_b64, 'success': True}
    service.redis_client.setex(f"preview:{user_id}", 300, json.dumps(result))
    return result


##from app.services.ai_orchestrator import AIOrchestrator
##from app.services.ai_context import fetch_general_metrics, fetch_context
##from celery.schedules import crontab
##
##def get_active_user_ids(days=1):
##    """Return user_ids with session activity in the last `days`."""
##    with DB_ENGINE.connect() as conn:
##        rows = conn.execute(text("""
##            SELECT DISTINCT user_id
##            FROM user_sessions
##            WHERE last_active > NOW() - INTERVAL ':days days'
##        """), {"days": days}).fetchall()
##    return [r[0] for r in rows]
##
##@celery.task
##def generate_daily_tips():
##    """
##    Runs every 12 hours to generate business tips for active users.
##    Skips users who already have a tip younger than 12 hours.
##    """
##    active_users = get_active_user_ids(days=7)
##    if not active_users:
##        logger.info("No active users for daily tips.")
##        return
##
##    for user_id in active_users:
##        try:
##            # Check if a fresh tip already exists (< 12 hours old)
##            with DB_ENGINE.connect() as conn:
##                existing = conn.execute(text("""
##                    SELECT 1 FROM ai_insights
##                    WHERE user_id = :uid AND insight_type = 'cached_tips'
##                    AND updated_at > NOW() - INTERVAL '12 hours'
##                """), {"uid": user_id}).fetchone()
##                if existing:
##                    continue   # skip this user
##
##            # Get general metrics
##            metrics = fetch_general_metrics(user_id)
##            system = "You are an expert business advisor. Based on the user's current metrics, give 3 actionable tips to improve business performance."
##            user = f"Metrics: Revenue {metrics['revenue']}, Expenses {metrics['expenses']}, Profit {metrics['profit']}, Inventory Value {metrics['inventory_value']}. Provide 3 concise tips."
##
##            orchestrator = AIOrchestrator()
##            tips = orchestrator.generate_insights(system, user, use_deep_history=False)
##
##            # Store with upsert
##            with DB_ENGINE.begin() as conn:
##                conn.execute(text("""
##                    INSERT INTO ai_insights (user_id, insight_type, content, status, updated_at)
##                    VALUES (:uid, 'cached_tips', :content, 'completed', NOW())
##                    ON CONFLICT (user_id, insight_type) DO UPDATE
##                    SET content = EXCLUDED.content,
##                        status = 'completed',
##                        updated_at = NOW();
##                """), {"uid": user_id, "content": tips})
##        except Exception as e:
##            logger.error(f"Failed to generate tips for user {user_id}: {e}")
##
### Add beat schedule (if using celery beat)
##celery.conf.beat_schedule = {
##    'generate-daily-tips': {
##        'task': 'app.services.tasks.generate_daily_tips',
##        'schedule': crontab(minute=0, hour=0),  # every 24 hours
##    },
##}
##
### Helper for prompt caching
##def generate_prompt_hash(user_question, context):
##    """Create a stable hash of the question and relevant context."""
##    import json
##    content = user_question + json.dumps(context, sort_keys=True)
##    return hashlib.sha256(content.encode()).hexdigest()
##
####@celery.task(bind=True, max_retries=3)
####def process_ai_query(self, user_id, user_question, use_deep=False):
####    """
####    Celery task for on‑demand AI query.
####    """
####    try:
####        # 1. Fetch context based on question
####        extra_system, context = fetch_context(user_id, user_question)
####
####        # 2. (Optional) Check cache for identical question
####        prompt_hash = generate_prompt_hash(user_question, context)
####        with DB_ENGINE.connect() as conn:
####            cached = conn.execute(text("""
####                SELECT content FROM ai_insights
####                WHERE user_id = :uid AND insight_type = 'query_cache'
####                  AND prompt_hash = :hash
####                  AND status = 'completed'
####                  AND updated_at > NOW() - INTERVAL '24 hours'
####                ORDER BY updated_at DESC LIMIT 1
####            """), {"uid": user_id, "hash": prompt_hash}).fetchone()
####        if cached:
####            answer = cached[0]
####            # Still store a 'query' record for this task (but with cached content)
####            with DB_ENGINE.begin() as conn:
####                conn.execute(text("""
####                    INSERT INTO ai_insights (user_id, task_id, insight_type, content, status, updated_at)
####                    VALUES (:uid, :task_id, 'query', :content, 'completed', NOW())
####                    ON CONFLICT (user_id, insight_type) DO UPDATE
####                    SET task_id = EXCLUDED.task_id,
####                        content = EXCLUDED.content,
####                        status = 'completed',
####                        updated_at = NOW();
####                """), {'uid': user_id, 'task_id': self.request.id, 'content': answer})
####            return {"status": "success", "answer": answer}
####
####        # 3. Build system prompt
####        system_prompt = (
####            "You are an expert ERP business analyst. Provide concise, actionable insights "
####            "based on the provided data. Answer the user's question directly. "
####            + extra_system
####        )
####
####        # 4. Build user prompt with context
####        context_str = json.dumps(context, indent=2)
####        user_prompt = f"User question: {user_question}\n\nRelevant business data:\n{context_str}"
####
####        # 5. Call orchestrator
####        orchestrator = AIOrchestrator()
####        answer = orchestrator.generate_insights(system_prompt, user_prompt, use_deep_history=use_deep)
####
####        # 6. Store result with upsert
####        with DB_ENGINE.begin() as conn:
####            conn.execute(text("""
####                INSERT INTO ai_insights (user_id, task_id, insight_type, content, status, updated_at)
####                VALUES (:uid, :task_id, 'query', :content, 'completed', NOW())
####                ON CONFLICT (user_id, insight_type) DO UPDATE
####                SET task_id = EXCLUDED.task_id,
####                    content = EXCLUDED.content,
####                    status = 'completed',
####                    updated_at = NOW();
####            """), {
####                'uid': user_id,
####                'task_id': self.request.id,
####                'content': answer
####            })
####
####        # 7. Also store cache entry (if desired)
####        with DB_ENGINE.begin() as conn:
####            conn.execute(text("""
####                INSERT INTO ai_insights (user_id, insight_type, prompt_hash, content, status, updated_at)
####                VALUES (:uid, 'query_cache', :hash, :content, 'completed', NOW())
####                ON CONFLICT (user_id, insight_type, prompt_hash) DO UPDATE
####                SET content = EXCLUDED.content,
####                    updated_at = NOW();
####            """), {
####                'uid': user_id,
####                'hash': prompt_hash,
####                'content': answer
####            })
####
####        return {"status": "success", "answer": answer}
####
####    except Exception as exc:
####        # Log failure with upsert
####        with DB_ENGINE.begin() as conn:
####            conn.execute(text("""
####                INSERT INTO ai_insights (user_id, task_id, insight_type, status, updated_at)
####                VALUES (:uid, :task_id, 'query', 'failed', NOW())
####                ON CONFLICT (user_id, insight_type) DO UPDATE
####                SET task_id = EXCLUDED.task_id,
####                    status = 'failed',
####                    updated_at = NOW();
####            """), {
####                'uid': user_id,
####                'task_id': self.request.id
####            })
####        if "429" in str(exc):
####            raise self.retry(exc=exc, countdown=60)
####        raise exc
