# config.py
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 1. Security
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # 2. Redis / Rate Limiter URL Logic
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        if '@' in _redis_url:
            password, host = _redis_url.split('@', 1)
            _redis_url = f"redis://default:{password}@{host}:6379"
        else:
            _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    
    REDIS_URL = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # 3. Celery Specific (CRITICAL for Connection Refused fix)
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url

    # 4. Session Settings
    SESSION_TYPE = 'redis' if 'redis' in _redis_url else 'filesystem'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'invoice_sess:'

    # 5. Gemini
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
