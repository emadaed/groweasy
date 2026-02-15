#config.py
import os
from datetime import timedelta

class Config:
    # 1. Core Flask Security
    SECRET_KEY = os.environ.get('SECRET_KEY', 'default-very-secret-key')
    
    # 2. Database Configuration
    # This pulls your Railway/Production DB URL or defaults to a local one
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # 3. Session Configuration (Redis)
    SESSION_TYPE = 'redis'
    SESSION_PERMANENT = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    
    # 4. Security & Performance
    COMPRESS_ALGORITHM = 'gzip'
    COMPRESS_LEVEL = 6
    
    # 5. Rate Limiting (Using Redis)
    RATELIMIT_STORAGE_URI = os.environ.get('REDIS_URL', 'memory://')
