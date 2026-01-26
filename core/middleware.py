# core/middleware.py

from flask import g, request
import secrets


def security_headers(app):
    """
    Add security headers to all responses.
    Implements CSP with nonce for inline scripts.
    """

    @app.before_request
    def set_nonce():
        """Generate a unique nonce for each request"""
        if not request.path.startswith('/static/'):
            g.nonce = secrets.token_urlsafe(16)
        else:
            g.nonce = None

    @app.after_request
    def add_security_headers(response):
        """Add security headers to response"""

        # Skip CSP for static files
        if request.path.startswith('/static/'):
            return response

        # Build CSP with nonce for HTML pages
        nonce = getattr(g, 'nonce', None)

        if nonce:
            csp = [
                "default-src 'self'",
                f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net",
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
                "img-src 'self' data: blob: https:",
                "font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com fonts.gstatic.com",
                "connect-src 'self'",
                "frame-ancestors 'none'",
                "form-action 'self'",
                "base-uri 'self'"
            ]
        else:
            csp = [
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: blob: https:",
                "font-src 'self'",
                "connect-src 'self'",
                "frame-ancestors 'none'"
            ]

        response.headers['Content-Security-Policy'] = '; '.join(csp)
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=(), payment=()'

        # HSTS only in production
        if not request.host.startswith('localhost') and not request.host.startswith('127.0.0.1'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

        return response

    @app.after_request
    def add_cache_headers(response):
        """Add appropriate cache headers"""
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        else:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'

        return response
