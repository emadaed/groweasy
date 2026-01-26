# tasks.py
from celery import Celery
from core.services import InvoiceService
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
