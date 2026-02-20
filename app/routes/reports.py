#app.routes.reports
from app.extensions import limiter
from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.services.db import DB_ENGINE  # Correct import from your db.py
from sqlalchemy import text
from app.services.tasks import process_ai_insight
import csv
import io
import json
import time
from app.extensions import redis_client

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Parses invoice_data JSON to get accurate tax and revenue"""
    with DB_ENGINE.connect() as conn:
        # 1. Get Invoices (Tax is stored inside invoice_data JSON)
        inv_res = conn.execute(text("""
            SELECT grand_total, invoice_data 
            FROM user_invoices WHERE user_id = :uid
        """), {'uid': user_id}).fetchall()
        
        total_revenue = 0.0
        total_tax = 0.0

        for row in inv_res:
            total_revenue += float(row.grand_total)
            try:
                # Based on your db.py, we parse the JSON data field
                data = json.loads(row.invoice_data)
                total_tax += float(data.get('tax', 0) or data.get('tax_amount', 0))
            except:
                total_tax += 0.0

        # 2. Inventory Value
        inv_val_res = conn.execute(text("""
            SELECT SUM(current_stock * cost_price) 
            FROM inventory_items WHERE user_id = :uid
        """), {'uid': user_id})
        inventory_value = inv_val_res.scalar() or 0.0

        # 3. Expenses
        exp_res = conn.execute(text("SELECT SUM(amount) FROM expenses WHERE user_id = :uid"), {'uid': user_id})
        total_expenses = exp_res.scalar() or 0.0

    return {
        "revenue": total_revenue,
        "inventory_value": float(inventory_value),
        "tax_liability": total_tax,
        "costs": float(total_expenses),
        "net_profit": total_revenue - float(total_expenses)
    }
#helper per use limit
def check_user_ai_limit(user_id, max_requests=5, period=3600):
    """
    Returns True if the user is allowed to make another request.
    Uses Redis sorted set to track timestamps.
    """
    key = f"user_ai_requests:{user_id}"
    now = time.time()
    # Remove requests older than 'period'
    redis_client.zremrangebyscore(key, 0, now - period)
    # Count remaining
    if redis_client.zcard(key) >= max_requests:
        return False
    # Add current request
    redis_client.zadd(key, {str(now): now})
    redis_client.expire(key, period)
    return True

@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    data = get_live_business_data(session['user_id'])
    ai_advice = session.get('ai_advice')

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol="د.إ",
                           nonce=getattr(g, 'nonce', ''))

@reports_bp.route('/reports/get_ai_status')
@limiter.limit("10 per minute")
def get_ai_status():
    """Polled by the frontend to see if the AI is done"""
    user_id = session.get('user_id')
    with DB_ENGINE.connect() as conn:
        res = conn.execute(text("""
            SELECT content, status FROM ai_insights 
            WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1
        """), {'uid': user_id}).fetchone()
    
    if res:
        return jsonify({"status": res.status, "answer": res.content})
    return jsonify({"status": "none"})


@reports_bp.route('/reports/ask_ai', methods=['POST'])
@limiter.limit("10 per minute")   # IP‑based limit (still useful)
def ask_ai():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    # --- HONEYPOT CHECK ---
    data = request.get_json()
    if data.get('honeypot'):
        current_app.logger.warning(f"Honeypot triggered for user {user_id}")
        # Lie to the bot: return 200 but do NOT queue the task
        return jsonify({"status": "queued", "message": "Your request is being processed."}), 200

    # --- PER‑USER RATE LIMIT ---
    if not check_user_ai_limit(user_id, max_requests=5, period=3600):
        return jsonify({
            "status": "limit",
            "message": "You've reached your hourly AI request limit (5). Please try later."
        }), 429

    # --- PROCEED WITH NORMAL FLOW ---
    user_prompt = data.get('prompt')
    if not user_prompt:
        return jsonify({"error": "Prompt is required"}), 400

    business_data = get_live_business_data(user_id)

    with DB_ENGINE.begin() as conn:
        conn.execute(text("DELETE FROM ai_insights WHERE user_id = :uid AND status = 'pending'"), {'uid': user_id})
        conn.execute(text("INSERT INTO ai_insights (user_id, status) VALUES (:uid, 'pending')"), {'uid': user_id})

    process_ai_insight.delay(user_id, business_data, custom_prompt=user_prompt)

    return jsonify({"status": "queued", "message": "Manager is analyzing your data..."})


@reports_bp.route('/reports/clear_ai', methods=['POST'])
def clear_ai():
    """This route was missing and caused the BuildError"""
    session.pop('ai_advice', None)
    return jsonify({"status": "cleared"})

@reports_bp.route('/reports/download/csv')
@limiter.limit("5 per minute")
def download_csv():
    user_id = session.get('user_id')
    data = get_live_business_data(user_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Total Revenue', data['revenue']])
    writer.writerow(['Net Profit', data['net_profit']])
    writer.writerow(['Tax Liability', data['tax_liability']])
    writer.writerow(['Stock Value', data['inventory_value']])
    return Response(output.getvalue(), mimetype="text/csv", 
                    headers={"Content-disposition": "attachment; filename=report.csv"})
