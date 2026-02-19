# config.py (root folder)
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-key-for-dev')
    
    # Robust Redis Detection for Railway
    _redis_url = os.getenv('REDIS_URL', 'memory://')
    
    # If the URL is missing the protocol, build the internal Railway path
    if _redis_url and '://' not in _redis_url and _redis_url != 'memory://':
        _redis_url = f"redis://default:{_redis_url}@redis.railway.internal:6379"
    
    # Assign to all necessary service variables
    REDIS_URL = _redis_url
    CELERY_BROKER_URL = _redis_url
    CELERY_RESULT_BACKEND = _redis_url
    RATELIMIT_STORAGE_URI = _redis_url

    # Session Settings to prevent KeyErrors
    SESSION_TYPE = 'redis' if (_redis_url and 'redis' in _redis_url) else 'filesystem'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=1)
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'invoice_sess:'
    SESSION_FILE_DIR = '/tmp/flask_sessions'

    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
