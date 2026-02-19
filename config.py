# config.py (root folder)
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 1. Security
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # 2. Redis Logic (CRITICAL FIX)
    raw_redis = os.getenv('REDIS_URL', 'memory://')
    
    # If it's already a full redis:// URL, use it as is.
    if raw_redis.startswith('redis://') or raw_redis.startswith('rediss://'):
        _redis_url = raw_redis
    elif raw_redis != 'memory://':
        # Fallback for old Railway internal formats
        _redis_url = f"redis://default:{raw_redis}@redis.railway.internal:6379"
    else:
        _redis_url = 'memory://'
    
    REDIS_URL = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # 3. Celery Specific
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
