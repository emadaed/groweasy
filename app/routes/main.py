from flask import Blueprint, render_template, jsonify, session, g, current_app, redirect, url_for
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


@main_bp.route('/admin/backup')
def admin_backup():
    """Manual database backup trigger (admin only)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # Simple admin check (first user is admin)
    if session['user_id'] != 1:
        return jsonify({'error': 'Admin only'}), 403

    try:
        import subprocess
        result = subprocess.run(['python', 'backup_db.py'],
                              capture_output=True,
                              text=True,
                              timeout=30)

        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'Backup created successfully',
                'output': result.stdout
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result.stderr
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500
    

@main_bp.route('/debug')
def debug():
    """Debug route to check what's working"""
    debug_info = {
        'session': dict(session),
        'routes': [str(rule) for rule in app.url_map.iter_rules()],
        'user_authenticated': bool(session.get('user_id'))
    }
    return jsonify(debug_info)

@main_bp.route('/')
def home():
    """Home page - redirect to login or dashboard"""
    if 'user_id' in session:
        return redirect(url_for('main.dashboard'))
    else:
        return redirect(url_for('auth.login'))

@main_bp.route("/dashboard")
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    from app.services.auth import get_business_summary, get_client_analytics

    with DB_ENGINE.connect() as conn:
        total_products = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE
        """), {"user_id": session['user_id']}).scalar()

        low_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND current_stock <= min_stock_level AND current_stock > 0
        """), {"user_id": session['user_id']}).scalar()

        out_of_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND current_stock = 0
        """), {"user_id": session['user_id']}).scalar()

    return render_template(
        "dashboard.html",
        user_email=session['user_email'],
        get_business_summary=get_business_summary,
        get_client_analytics=get_client_analytics,
        total_products=total_products,
        low_stock_items=low_stock_items,
        out_of_stock_items=out_of_stock_items,
        nonce=g.nonce
    )


    
