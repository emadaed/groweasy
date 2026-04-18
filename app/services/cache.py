# app/services/cache.py
"""
Application cache — Redis in production, SimpleCache as dev fallback.

The original code passed os.getenv('REDIS_URL') directly to Flask-Caching.
If REDIS_URL is not set (e.g. a fresh local dev environment), the value is
None and Flask-Caching raises a connection error on startup.

Fixed: detect missing REDIS_URL and fall back to SimpleCache with a loud
warning.  In production on Railway, REDIS_URL is always set, so this only
affects local dev.
"""
import os
import logging
from flask_caching import Cache

logger = logging.getLogger(__name__)
cache = Cache()


def init_cache(app):
    redis_url = os.getenv('REDIS_URL')

    if redis_url:
        cache_config = {
            'CACHE_TYPE': 'RedisCache',
            'CACHE_REDIS_URL': redis_url,
            'CACHE_DEFAULT_TIMEOUT': 300,
        }
        logger.info("Cache: using Redis")
    else:
        # Safe local fallback — NOT suitable for multi-worker production
        cache_config = {
            'CACHE_TYPE': 'SimpleCache',
            'CACHE_DEFAULT_TIMEOUT': 300,
        }
        logger.warning(
            "REDIS_URL not set — falling back to SimpleCache (per-process, "
            "not shared across workers). Set REDIS_URL for production."
        )

    cache.init_app(app, config=cache_config)


@cache.memoize(timeout=300)
def get_user_profile_cached(user_id):
    from app.services.auth import get_user_profile
    return get_user_profile(user_id)
