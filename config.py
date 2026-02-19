# config.py (root folder)
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    # 1. Basic Security
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # 2. Redis Connection Logic (Enhanced for Railway Stability)
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    
    # Robust Check: If it's not a full URL, we build the internal Railway path
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        # Handles cases where REDIS_URL is just the password or a hostname
        _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    elif _redis_url.startswith('redis://') and 'railway.internal' not in _redis_url:
        # If it's an external URL, we ensure it's used; but if internal is available, that's better
        pass
    
    # Apply to all services
    REDIS_URL = _redis_url
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # 3. Session Settings (Preserved exactly as requested)
    SESSION_TYPE = 'redis' if (_redis_url and 'redis' in _redis_url) else 'filesystem'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'invoice_sess:'
    SESSION_FILE_DIR = '/tmp/flask_sessions'

    # 4. API Keys
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
