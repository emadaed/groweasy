# ============================================================================
# main.py - COMPLETE FIXED VERSION 29--01-2026 06-02-2026 Refactoring done.
# ============================================================================

import os
from app import create_app

app = create_app()

# ============================================================================
# STATIC ROOT FILES FOR SEO & GOOGLE VERIFICATION 03-04-2026
# Add these routes AFTER app is created but BEFORE running
# ============================================================================

@app.route('/robots.txt')
def serve_robots():
    """Serve robots.txt for search engine crawlers"""
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'robots.txt')

@app.route('/sitemap.xml')
def serve_sitemap():
    """Serve sitemap.xml for Google indexing"""
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'sitemap.xml')

@app.route('/googlea6503ed4086cdf42.html')
def serve_google_verification():
    """Serve Google Search Console verification file"""
    from flask import send_from_directory
    return send_from_directory(app.static_folder, 'googlea6503ed4086cdf42.html')

# ============================================================================
# END STATIC ROOT FILES
# ============================================================================

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
