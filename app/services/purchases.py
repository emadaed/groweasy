# app/services/purchases.py
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

def ensure_purchase_table_migrated():
    from app.services.db import DB_ENGINE
    from sqlalchemy import text
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                ALTER TABLE purchase_orders 
                ADD COLUMN IF NOT EXISTS supplier_id INTEGER;
            '''))
            conn.execute(text('''
                ALTER TABLE purchase_orders 
                ADD COLUMN IF NOT EXISTS grand_total DECIMAL(15, 2);
            '''))
    except Exception as e:
        print(f"Migration Notice (Purchase Orders): {e}")

def save_purchase_order(user_id, account_id, order_data):
    from app.services.db import DB_ENGINE
    from sqlalchemy import text
    import json
    from datetime import datetime

    ensure_purchase_table_migrated()
    try:
        with DB_ENGINE.begin() as conn:
            from app.services.number_generator import NumberGenerator
            po_number = NumberGenerator.generate_po_number(account_id)

            supplier_id = order_data.get('supplier_id')
            supplier_name = order_data.get('supplier_name', 'Unknown Supplier')
            order_date = order_data.get('po_date') or datetime.now().strftime('%Y-%m-%d')
            delivery_date = order_data.get('delivery_date')
            grand_total = float(order_data.get('grand_total', 0))

            order_data['po_number'] = po_number
            order_json = json.dumps(order_data)

            conn.execute(text('''
                INSERT INTO purchase_orders 
                (user_id, account_id, po_number, supplier_id, supplier_name, order_date, delivery_date, grand_total, order_data)
                VALUES (:user_id, :aid, :po_number, :supplier_id, :supplier_name, :order_date, :delivery_date, :grand_total, :order_json)
            '''), {
                "user_id": user_id,
                "aid": account_id,
                "po_number": po_number,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "order_date": order_date,
                "delivery_date": delivery_date if delivery_date else None,
                "grand_total": grand_total,
                "order_json": order_json
            })

            if supplier_id:
                conn.execute(text('''
                    UPDATE suppliers SET 
                        order_count = order_count + 1,
                        total_purchased = total_purchased + :grand_total,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND account_id = :aid
                '''), {
                    "grand_total": grand_total,
                    "id": int(supplier_id),
                    "aid": account_id
                })
                print(f"✅ Purchase Order Linked & Supplier {supplier_name} stats updated.")

        return True
    except Exception as e:
        print(f"❌ Final Save Error: {e}")
        return False

def get_purchase_orders(account_id, limit=50, offset=0):
    with DB_ENGINE.connect() as conn:
        orders = conn.execute(text('''
            SELECT id, po_number, supplier_name, order_date, delivery_date,
                   grand_total, status, created_at, order_data
            FROM purchase_orders
            WHERE account_id = :aid
            ORDER BY order_date DESC, created_at DESC
            LIMIT :limit OFFSET :offset
        '''), {"aid": account_id, "limit": limit, "offset": offset}).fetchall()

    result = []
    for order in orders:
        try:
            order_data = json.loads(order[8])
        except (json.JSONDecodeError, TypeError):
            order_data = {}
        items = order_data.get('items', [])
        item_count = len(items) if isinstance(items, list) else 0
        result.append({
            'id': order[0],
            'po_number': order[1],
            'supplier_name': order[2],
            'order_date': order[3],
            'delivery_date': order[4],
            'grand_total': float(order[5]),
            'status': order[6],
            'created_at': order[7],
            'data': order_data,
            'item_count': item_count
        })
    return result

def get_purchase_order(account_id, po_number):
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text('''
                SELECT order_data FROM purchase_orders
                WHERE account_id = :aid AND po_number = :po_number
            '''), {"aid": account_id, "po_number": po_number}).fetchone()
            if result:
                return json.loads(result[0])
        return None
    except Exception as e:
        logger.error(f"Error fetching PO: {e}")
        return None

def get_purchase_orders_api(account_id, limit=100, offset=0):
    """Return a list of POs with basic info."""
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, po_number, supplier_name, order_date, delivery_date, grand_total, status, created_at
            FROM purchase_orders
            WHERE account_id = :aid
            ORDER BY order_date DESC
            LIMIT :limit OFFSET :offset
        """), {"aid": account_id, "limit": limit, "offset": offset}).fetchall()
    return [{
        'id': r[0],
        'po_number': r[1],
        'supplier_name': r[2],
        'order_date': r[3].isoformat() if r[3] else None,
        'delivery_date': r[4].isoformat() if r[4] else None,
        'grand_total': float(r[5]),
        'status': r[6],
        'created_at': r[7].isoformat() if r[7] else None
    } for r in rows]

def get_purchase_order_by_number_api(account_id, po_number):
    """Fetch a single PO by its number."""
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, po_number, supplier_name, order_date, delivery_date, grand_total, status, created_at, order_data
            FROM purchase_orders
            WHERE account_id = :aid AND po_number = :po_number
        """), {"aid": account_id, "po_number": po_number}).first()
    if row:
        return {
            'id': row[0],
            'po_number': row[1],
            'supplier_name': row[2],
            'order_date': row[3].isoformat() if row[3] else None,
            'delivery_date': row[4].isoformat() if row[4] else None,
            'grand_total': float(row[5]),
            'status': row[6],
            'created_at': row[7].isoformat() if row[7] else None,
            'order_data': row[8]  # full JSON if needed
        }
    return None
