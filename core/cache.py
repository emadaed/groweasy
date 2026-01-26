# core/cache.py
from flask_caching import Cache

cache = Cache()

def init_cache(app):
    cache.init_app(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 300})

@cache.memoize(timeout=300)  # 5 minutes
def get_user_profile_cached(user_id):
    from core.auth import get_user_profile
    return get_user_profile(user_id)
