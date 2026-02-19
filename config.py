#config.py   (root folder)
# config.py
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 1. Basic Security
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # 2. Redis Connection Logic (Fixed for Railway)
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    
    # If Railway provides a full redis:// URL, use it. 
    # Otherwise, format it for the internal network.
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    
    REDIS_URL = _redis_url
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # 3. Session Settings (CRITICAL: Added back to fix KeyError)
    SESSION_TYPE = 'redis' if (_redis_url and 'redis' in _redis_url) else 'filesystem'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'invoice_sess:'
    SESSION_FILE_DIR = '/tmp/flask_sessions'

    # 4. API Keys
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
