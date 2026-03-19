# app/decorators.py
from functools import wraps
from flask import session, abort, flash, redirect, url_for

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))
            user_role = session.get('role')
            if user_role not in roles:
                flash("You don't have permission to access this page.", "error")
                abort(403)
            return f(*args, **kwargs)
        return decorated_function
    return decorator
