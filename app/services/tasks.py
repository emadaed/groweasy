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

