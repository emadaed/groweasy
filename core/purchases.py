# core/purchases.py - Purchase Order & Supplier Management (Postgres Ready) - FIXED
from core.db import DB_ENGINE
from sqlalchemy import text
import json
from datetime import datetime

def init_purchase_tables():
    """Initialize purchase order and supplier tables"""
    with DB_ENGINE.begin() as conn:
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                address TEXT,
                tax_id TEXT,
                total_purchased DECIMAL(10,2) DEFAULT 0,
                order_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                po_number TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                order_date DATE NOT NULL,
                delivery_date DATE,
                grand_total DECIMAL(10,2) NOT NULL,
                status TEXT DEFAULT 'pending',
                order_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''))

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_purchase_orders ON purchase_orders(user_id, order_date)"))

def save_purchase_order(user_id, order_data):
    """Save purchase order and auto-update supplier - FIXED"""
    with DB_ENGINE.begin() as conn:
        # Generate fresh PO number
        from core.number_generator import NumberGenerator
        po_number = NumberGenerator.generate_po_number(user_id)

        print(f"🔍 Generated fresh PO number: {po_number}")

        # FIX: Use correct PO field names
        supplier_name = order_data.get('supplier_name', 'Unknown Supplier')  # FIXED
        order_date = order_data.get('po_date', datetime.now().strftime('%Y-%m-%d'))  # FIXED
        delivery_date = order_data.get('delivery_date', '')  # FIXED
        grand_total = float(order_data.get('grand_total', 0))

        # Update order_data with correct PO number (remove invoice_number)
        order_data['po_number'] = po_number
        if 'invoice_number' in order_data:
            del order_data['invoice_number']  # Remove invoice field

        order_json = json.dumps(order_data)

        # Convert empty dates to None for PostgreSQL
        if not order_date:
            order_date = datetime.now().strftime('%Y-%m-%d')  # Default to today
        if not delivery_date:
            delivery_date = None

        print(f"📅 PO Date: {order_date}, Delivery: {delivery_date}")

        conn.execute(text('''
            INSERT INTO purchase_orders
            (user_id, po_number, supplier_name, order_date, delivery_date, grand_total, order_data)
            VALUES (:user_id, :po_number, :supplier_name, :order_date, :delivery_date, :grand_total, :order_json)
        '''), {
            "user_id": user_id,
            "po_number": po_number,
            "supplier_name": supplier_name,
            "order_date": order_date,
            "delivery_date": delivery_date,
            "grand_total": grand_total,
            "order_json": order_json
        })

        print(f"✅ Purchase Order {po_number} saved for {supplier_name}")

        # Auto-save supplier - FIXED: Use correct supplier fields
        supplier_data = {
            'name': supplier_name,
            'email': order_data.get('supplier_email', ''),
            'phone': order_data.get('supplier_phone', ''),
            'address': order_data.get('supplier_address', ''),
            'tax_id': order_data.get('supplier_tax_id', '')
        }

        result = conn.execute(text("SELECT id FROM suppliers WHERE user_id = :user_id AND name = :name"),
                             {"user_id": user_id, "name": supplier_data['name']}).fetchone()

        if result:
            conn.execute(text('''
                UPDATE suppliers SET
                email=:email, phone=:phone, address=:address, tax_id=:tax_id,
                order_count = order_count + 1,
                total_purchased = total_purchased + :grand_total,
                updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
            '''), {
                "email": supplier_data['email'],
                "phone": supplier_data['phone'],
                "address": supplier_data['address'],
                "tax_id": supplier_data['tax_id'],
                "grand_total": grand_total,
                "id": result[0]
            })
        else:
            conn.execute(text('''
                INSERT INTO suppliers
                (user_id, name, email, phone, address, tax_id, total_purchased, order_count)
                VALUES (:user_id, :name, :email, :phone, :address, :tax_id, :grand_total, 1)
            '''), {
                "user_id": user_id,
                "name": supplier_data['name'],
                "email": supplier_data['email'],
                "phone": supplier_data['phone'],
                "address": supplier_data['address'],
                "tax_id": supplier_data['tax_id'],
                "grand_total": grand_total
            })

        print(f"✅ Supplier {supplier_name} updated/created")

    return True


def get_purchase_orders(user_id, limit=50, offset=0):
    """Get purchase orders for user"""
    with DB_ENGINE.connect() as conn:
        orders = conn.execute(text('''
            SELECT id, po_number, supplier_name, order_date, delivery_date,
                   grand_total, status, created_at, order_data
            FROM purchase_orders
            WHERE user_id = :user_id
            ORDER BY order_date DESC, created_at DESC
            LIMIT :limit OFFSET :offset
        '''), {"user_id": user_id, "limit": limit, "offset": offset}).fetchall()

    result = []
    for order in orders:
        result.append({
            'id': order[0],
            'po_number': order[1],
            'supplier_name': order[2],
            'order_date': order[3],
            'delivery_date': order[4],
            'grand_total': float(order[5]),
            'status': order[6],
            'created_at': order[7],
            'data': json.loads(order[8])
        })
    return result

def get_suppliers(user_id):
    """Get all suppliers"""
    with DB_ENGINE.connect() as conn:
        suppliers = conn.execute(text('''
            SELECT id, name, email, phone, address, tax_id, total_purchased, order_count
            FROM suppliers WHERE user_id = :user_id ORDER BY name
        '''), {"user_id": user_id}).fetchall()

    result = []
    for supplier in suppliers:
        result.append({
            'id': supplier[0],
            'name': supplier[1],
            'email': supplier[2],
            'phone': supplier[3],
            'address': supplier[4],
            'tax_id': supplier[5],
            'total_purchased': float(supplier[6]) if supplier[6] else 0,
            'order_count': supplier[7]
        })
    return result

def get_purchase_order(user_id, po_number):
    """Get single purchase order by number"""
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text('''
                SELECT order_data FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
            '''), {"user_id": user_id, "po_number": po_number}).fetchone()
            if result:
                return json.loads(result[0])
        return None
    except Exception as e:
        logger.error(f"Error fetching PO: {e}")
        return None
