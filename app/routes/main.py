from flask import Blueprint, render_template, jsonify, session, g, current_app
from sqlalchemy import text
from datetime import datetime
from app.services.db import DB_ENGINE

main_bp = Blueprint('main', __name__)

@main_bp.route("/donate")
def donate():
    return render_template("donate.html", nonce=g.nonce)

@main_bp.route("/terms")
def terms():
    return render_template("terms.html", nonce=g.nonce)

@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html", nonce=g.nonce)

@main_bp.route("/about")
def about():
    return render_template("about.html", nonce=g.nonce)

@main_bp.route('/debug')
def debug():
    debug_info = {
        'session': dict(session),
        'user_authenticated': bool(session.get('user_id'))
    }
    return jsonify(debug_info)

@main_bp.route('/health')
def health_check():
    try:
        with DB_ENGINE.connect() as conn:
            user_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'database': 'connected',
            'users': user_count
        }), 200
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@main_bp.route('/api/status')
def system_status():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'status': 'operational', 'timestamp': datetime.now().isoformat()}), 200
