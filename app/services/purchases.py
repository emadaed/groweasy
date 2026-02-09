# core/purchases.py - Purchase Order & Supplier Management (Postgres Ready) - FIXED
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
from datetime import datetime

# app/services/purchases.py

def ensure_purchase_table_migrated():
    """Safety check to add supplier_id column to purchase_orders table if missing"""
    from app.services.db import DB_ENGINE
    from sqlalchemy import text
    try:
        with DB_ENGINE.begin() as conn:
            # Add supplier_id column if it doesn't exist
            conn.execute(text('''
                ALTER TABLE purchase_orders 
                ADD COLUMN IF NOT EXISTS supplier_id INTEGER;
            '''))
            # Also ensure grand_total exists (sometimes named total_amount in old versions)
            conn.execute(text('''
                ALTER TABLE purchase_orders 
                ADD COLUMN IF NOT EXISTS grand_total DECIMAL(15, 2);
            '''))
    except Exception as e:
        print(f"Migration Notice (Purchase Orders): {e}")

def save_purchase_order(user_id, order_data):
    """Save purchase order and link to professional Supplier record using ID"""
    from app.services.db import DB_ENGINE
    from sqlalchemy import text
    import json
    from datetime import datetime
    
    # RUN MIGRATION FIRST
    ensure_purchase_table_migrated()
    
    try:
        with DB_ENGINE.begin() as conn:
            # 1. Generate fresh PO number
            from app.services.number_generator import NumberGenerator
            po_number = NumberGenerator.generate_po_number(user_id)

            # 2. Extract Data
            # Use .get() to avoid KeyErrors
            supplier_id = order_data.get('supplier_id')
            supplier_name = order_data.get('supplier_name', 'Unknown Supplier')
            order_date = order_data.get('po_date') or datetime.now().strftime('%Y-%m-%d')
            delivery_date = order_data.get('delivery_date')
            grand_total = float(order_data.get('grand_total', 0))

            # 3. Prepare JSON blob
            order_data['po_number'] = po_number
            order_json = json.dumps(order_data)

            # 4. Insert PO (Now safe because of migration)
            conn.execute(text('''
                INSERT INTO purchase_orders 
                (user_id, po_number, supplier_id, supplier_name, order_date, delivery_date, grand_total, order_data)
                VALUES (:user_id, :po_number, :supplier_id, :supplier_name, :order_date, :delivery_date, :grand_total, :order_json)
            '''), {
                "user_id": user_id,
                "po_number": po_number,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "order_date": order_date,
                "delivery_date": delivery_date if delivery_date else None,
                "grand_total": grand_total,
                "order_json": order_json
            })

            # 5. Update Supplier Stats
            if supplier_id:
                conn.execute(text('''
                    UPDATE suppliers SET 
                        order_count = order_count + 1,
                        total_purchased = total_purchased + :grand_total,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND user_id = :user_id
                '''), {
                    "grand_total": grand_total,
                    "id": int(supplier_id),
                    "user_id": user_id
                })
                print(f"✅ Purchase Order Linked & Supplier {supplier_name} stats updated.")

        return True
    except Exception as e:
        print(f"❌ Final Save Error: {e}")
        return False

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

##def get_suppliers(user_id):
##    """Get all suppliers"""
##    with DB_ENGINE.connect() as conn:
##        suppliers = conn.execute(text('''
##            SELECT id, name, email, phone, address, tax_id, total_purchased, order_count
##            FROM suppliers WHERE user_id = :user_id ORDER BY name
##        '''), {"user_id": user_id}).fetchall()
##
##    result = []
##    for supplier in suppliers:
##        result.append({
##            'id': supplier[0],
##            'name': supplier[1],
##            'email': supplier[2],
##            'phone': supplier[3],
##            'address': supplier[4],
##            'tax_id': supplier[5],
##            'total_purchased': float(supplier[6]) if supplier[6] else 0,
##            'order_count': supplier[7]
##        })
##    return result

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
