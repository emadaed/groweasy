#app/routes/api.py
from flask import Blueprint, session, request, jsonify, current_app
from sqlalchemy import text
import json
import time
import os
import datetime as dt_module
from datetime import datetime, date, timedelta
from app.services.db import DB_ENGINE
from app import limiter
from app.extensions import csrf

api_bp = Blueprint('api', __name__)

@api_bp.route('/api/purchase_order/<po_number>/complete', methods=['POST'])
def complete_purchase_order(po_number):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    account_id = session['account_id']
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE purchase_orders SET status = 'completed'
                WHERE account_id = :aid AND po_number = :po_number
            """), {"aid": account_id, "po_number": po_number})
        return jsonify({'success': True, 'message': f'PO {po_number} marked as completed'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# NOTE: @csrf.exempt removed — this is a state-changing POST that modifies the
# database. There is no valid reason to exempt it from CSRF protection.
@api_bp.route('/api/purchase_order/<po_number>/cancel', methods=['POST'])
def cancel_purchase_order(po_number):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    account_id = session['account_id']
    try:
        data = request.get_json()
        reason = data.get('reason', 'No reason provided')
        with DB_ENGINE.begin() as conn:
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE account_id = :aid AND po_number = :po_number
            """), {"aid": account_id, "po_number": po_number}).fetchone()
            if result:
                order_data = json.loads(result[0])
                order_data['cancellation_reason'] = reason
                order_data['cancelled_at'] = datetime.now().isoformat()
                conn.execute(text("""
                    UPDATE purchase_orders
                    SET status = 'cancelled', order_data = :order_data
                    WHERE account_id = :aid AND po_number = :po_number
                """), {
                    "aid": account_id,
                    "po_number": po_number,
                    "order_data": json.dumps(order_data)
                })
        return jsonify({'success': True, 'message': f'PO {po_number} cancelled'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@api_bp.route("/api/purchase_order/<po_number>")
@limiter.limit("30 per minute")
def get_purchase_order_details(po_number):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    account_id = session['account_id']
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data, status, created_at FROM purchase_orders
                WHERE account_id = :aid AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"aid": account_id, "po_number": po_number}).fetchone()
        if not result:
            return jsonify({'error': 'Purchase order not found'}), 404
        order_data = json.loads(result[0])
        order_data['status'] = result[1]
        order_data['created_at'] = result[2].isoformat() if result[2] else None
        # Enrich items
        if 'items' in order_data:
            product_ids = []
            for item in order_data['items']:
                pid = item.get('product_id')
                if pid:
                    try:
                        product_ids.append(int(pid))
                    except (ValueError, TypeError):
                        pass
            if product_ids:
                with DB_ENGINE.connect() as conn:
                    product_names = conn.execute(text("""
                        SELECT id, name FROM inventory_items
                        WHERE id = ANY(:ids) AND account_id = :aid
                    """), {"ids": product_ids, "aid": account_id}).fetchall()
                    name_map = {row.id: row.name for row in product_names}
                for item in order_data['items']:
                    pid = item.get('product_id')
                    if pid:
                        try:
                            pid_int = int(pid)
                            item['name'] = name_map.get(pid_int, f"Product {pid_int}")
                        except:
                            item['name'] = f"Product {pid}"
                    else:
                        item['name'] = item.get('name', 'Unknown Item')
        return jsonify(order_data), 200
    except Exception as e:
        current_app.logger.error(f"Error fetching PO details: {e}")
        return jsonify({'error': 'Internal server error'}), 500
