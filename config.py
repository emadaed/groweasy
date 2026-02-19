#config.py   (root folder)
# config.py
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 1. Basic Security
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # 2. Redis Connection Logic (Aligned for Railway Internal & External)
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    
    # If Railway provides just the password/host, we build the internal URL
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    
    # Standardize on redis:// for Celery and Session compatibility
    REDIS_URL = _redis_url
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # 3. Session Settings (Required to prevent app crash)
    SESSION_TYPE = 'redis' if (_redis_url and 'redis' in _redis_url) else 'filesystem'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'invoice_sess:'
    SESSION_FILE_DIR = '/tmp/flask_sessions'

    # 4. API Keys
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
