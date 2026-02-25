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


# new route and methods

@main_bp.route("/dashboard")
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    from app.services.db import DB_ENGINE
    from sqlalchemy import text

    with DB_ENGINE.connect() as conn:
        # 1. Revenue from paid invoices
        revenue = conn.execute(text("""
            SELECT COALESCE(SUM(grand_total), 0) FROM user_invoices
            WHERE user_id = :uid AND status = 'paid'
        """), {"uid": user_id}).scalar() or 0

        # 2. Total expenses
        expenses = conn.execute(text("""
            SELECT COALESCE(SUM(amount), 0) FROM expenses
            WHERE user_id = :uid
        """), {"uid": user_id}).scalar() or 0

        # 3. Inventory value
        inventory_value = conn.execute(text("""
            SELECT COALESCE(SUM(current_stock * cost_price), 0) FROM inventory_items
            WHERE user_id = :uid
        """), {"uid": user_id}).scalar() or 0

        # 4. Tax liability from JSON (field may be 'tax' or 'tax_amount')
        tax = conn.execute(text("""
            SELECT COALESCE(SUM(
                COALESCE(
                    (invoice_data::jsonb->>'tax')::numeric,
                    (invoice_data::jsonb->>'tax_amount')::numeric,
                    0
                )
            ), 0)
            FROM user_invoices
            WHERE user_id = :uid
        """), {"uid": user_id}).scalar() or 0

        # 5. Existing inventory stats
        total_products = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :uid AND is_active = TRUE
        """), {"uid": user_id}).scalar() or 0

        low_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :uid AND current_stock <= min_stock_level AND current_stock > 0
        """), {"uid": user_id}).scalar() or 0

        out_of_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :uid AND current_stock = 0
        """), {"uid": user_id}).scalar() or 0

        # 6. Deadstock
        deadstock_items = conn.execute(text("""
            SELECT i.id, i.name
            FROM inventory_items i
            WHERE i.user_id = :uid AND i.id NOT IN (
                SELECT DISTINCT product_id FROM invoice_items ii
                JOIN user_invoices ui ON ii.invoice_id = ui.id
                WHERE ui.user_id = :uid AND ui.invoice_date > NOW() - INTERVAL '90 days'
            )
        """), {"uid": user_id}).fetchall()
        deadstock = [{"id": r.id, "name": r.name} for r in deadstock_items]

        # 7. Reorder items
        reorder_rows = conn.execute(text("""
            SELECT id, name, current_stock, min_stock_level
            FROM inventory_items
            WHERE user_id = :uid AND current_stock <= min_stock_level
            ORDER BY current_stock ASC
        """), {"uid": user_id}).fetchall()
        reorder_items = [{"id": r.id, "name": r.name, "current": r.current_stock, "min": r.min_stock_level} for r in reorder_rows]

        # 8. Market basket
        basket_rows = conn.execute(text("""
            SELECT a.product_id AS prod1, b.product_id AS prod2, COUNT(*) AS times
            FROM invoice_items a
            JOIN invoice_items b ON a.invoice_id = b.invoice_id AND a.product_id < b.product_id
            JOIN user_invoices ui ON a.invoice_id = ui.id
            WHERE ui.user_id = :uid
            GROUP BY prod1, prod2
            ORDER BY times DESC
            LIMIT 5
        """), {"uid": user_id}).fetchall()
        market_basket = []
        for r in basket_rows:
            prod1_name = conn.execute(text("SELECT name FROM inventory_items WHERE id = :id"), {"id": r.prod1}).scalar()
            prod2_name = conn.execute(text("SELECT name FROM inventory_items WHERE id = :id"), {"id": r.prod2}).scalar()
            market_basket.append({"pair": f"{prod1_name} & {prod2_name}", "times": r.times})

        # 9. Cached AI tip
        ai_tip = conn.execute(text("""
            SELECT content FROM ai_insights
            WHERE user_id = :uid AND insight_type = 'cached_tips'
            ORDER BY created_at DESC
            LIMIT 1
        """), {"uid": user_id}).scalar()

    # Data dict for template
    user_profile = get_user_profile_cached(user_id)
    data = {
        "revenue": revenue,
        "net_profit": revenue - expenses,
        "inventory_value": inventory_value,
        "tax_liability": tax,
        "costs": expenses
    }

    return render_template(
        "dashboard.html",
        user_email=session.get('email', 'User'),
        data=data,
        deadstock=deadstock,
        reorder_items=reorder_items,
        market_basket=market_basket,
        ai_tip=ai_tip,
        total_products=total_products,
        low_stock_items=low_stock_items,
        out_of_stock_items=out_of_stock_items,
        currency_symbol="د.إ",
        show_fbr = user_profile.get('show_fbr_fields', False),
        nonce=g.nonce
    )
