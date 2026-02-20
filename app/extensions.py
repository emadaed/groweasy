#app/extensions.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
import redis
from config import Config

# Global instances initialized without the 'app' context
limiter = Limiter(key_func=get_remote_address)
compress = Compress()


# Redis client for per‑user rate limiting (reuses existing REDIS_URL)
redis_client = redis.from_url(Config.REDIS_URL, decode_responses=True)
