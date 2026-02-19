# app/services/middleware.py

from flask import g, request
import secrets

def init_middleware(app):
    """
    Registers all middleware handlers to the Flask app.
    Call this in your create_app() or main app.py file.
    """

    @app.before_request
    def set_nonce():
        """Generates a unique nonce for every single request."""
        if not request.path.startswith('/static/'):
            # This 'g.nonce' is what we use in both the CSP header and the HTML templates
            g.nonce = secrets.token_urlsafe(16)
        else:
            g.nonce = None

    @app.after_request
    def add_security_headers(response):
        if request.path.startswith('/static/'): return response
        nonce = getattr(g, 'nonce', None)

        # We must explicitly allow Cloudflare and jsdelivr for both scripts AND connections
        csp = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
            "img-src 'self' data: blob: https:",
            "font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com fonts.gstatic.com",
            # connect-src MUST include cloudflare because that's where you're loading Chart.js from now
            "connect-src 'self' https://*.jsdelivr.net https://*.cloudflare.com https://*.sentry.io",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "base-uri 'self'"
        ]
        
        response.headers['Content-Security-Policy'] = '; '.join(csp)
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=(), payment=()'
        
        if not request.host.startswith(('localhost', '127.0.0.1')):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

    @app.after_request
    def add_cache_headers(response):
        """Ensures dashboard data isn't leaked via browser cache."""
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response
