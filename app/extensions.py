#app/extensions.py
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress

# Global instances initialized without the 'app' context
limiter = Limiter(key_func=get_remote_address)
compress = Compress()
