#app/extensions.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
import redis
from config import Config

# Global instances initialized without the 'app' context
limiter = Limiter(key_func=get_remote_address)
compress = Compress()


#redis
_redis_pool = redis.ConnectionPool.from_url(
    Config.REDIS_URL,
    decode_responses=True,
    max_connections=20
)

def get_redis():
    return redis.Redis(connection_pool=_redis_pool)
