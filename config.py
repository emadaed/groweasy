#config.py   (root folder)
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # Use the full Railway URL directly to prevent "Connection Refused"
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    
    # Ensure it always starts with redis:// for Celery/Kombu
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    
    REDIS_URL = _redis_url
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url
