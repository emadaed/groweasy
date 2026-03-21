# app/services/inventory.py
from decimal import Decimal
from app.services.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class InventoryManager:

    @staticmethod
    def add_product(user_id, account_id, product_data):
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text('''
                    INSERT INTO inventory_items
                    (user_id, account_id, name, sku, category, description, current_stock,
                     min_stock_level, cost_price, selling_price, supplier, location,
                     unit_type, is_perishable, expiry_date, batch_number, barcode,
                     pack_size, weight_kg)
                    VALUES 
                    (:user_id, :account_id, :name, :sku, :category, :description, :current_stock,
                     :min_stock_level, :cost_price, :selling_price, :supplier, :location,
                     :unit_type, :is_perishable, :expiry_date, :batch_number, :barcode,
                     :pack_size, :weight_kg)
                    RETURNING id
                '''), {
                    "user_id": user_id,
                    "account_id": account_id,
                    "name": product_data['name'],
                    "sku": product_data.get('sku'),
                    "category": product_data.get('category'),
                    "description": product_data.get('description'),
                    "current_stock": product_data.get('current_stock', 0),
                    "min_stock_level": product_data.get('min_stock_level', 5),
                    "cost_price": product_data.get('cost_price', 0.0),
                    "selling_price": product_data.get('selling_price', 0.0),
                    "supplier": product_data.get('supplier'),
                    "location": product_data.get('location'),
                    "unit_type": product_data.get('unit_type', 'piece'),
                    "is_perishable": product_data.get('is_perishable', False),
                    "expiry_date": product_data.get('expiry_date'),
                    "batch_number": product_data.get('batch_number'),
                    "barcode": product_data.get('barcode'),
                    "pack_size": product_data.get('pack_size', 1.0),
                    "weight_kg": product_data.get('weight_kg'),
                }).fetchone()

                if result and product_data.get('current_stock', 0) > 0:
                    product_id = result[0]
                    conn.execute(text('''
                        INSERT INTO stock_movements
                        (user_id, product_id, movement_type, quantity, notes)
                        VALUES (:user_id, :product_id, 'initial', :quantity, 'Initial stock')
                    '''), {
                        "user_id": user_id,
                        "product_id": product_id,
                        "quantity": product_data.get('current_stock', 0)
                    })

                logger.info(f"Product added with full details: {product_data['name']} (ID: {result[0] if result else 'None'})")
                return result[0] if result else None

        except Exception as e:
            logger.error(f"Error adding product: {e}")
            return None

    @staticmethod
    def update_product(user_id, product_id, product_data):
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text('''
                    UPDATE inventory_items
                    SET name = :name, sku = :sku, category = :category, description = :description,
                        min_stock_level = :min_stock_level, cost_price = :cost_price,
                        selling_price = :selling_price, supplier = :supplier, location = :location,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :product_id AND user_id = :user_id
                '''), {
                    "name": product_data['name'],
                    "sku": product_data.get('sku'),
                    "category": product_data.get('category'),
                    "description": product_data.get('description'),
                    "min_stock_level": product_data.get('min_stock_level', 5),
                    "cost_price": product_data.get('cost_price', 0.0),
                    "selling_price": product_data.get('selling_price', 0.0),
                    "supplier": product_data.get('supplier'),
                    "location": product_data.get('location'),
                    "product_id": product_id,
                    "user_id": user_id
                })

                if 'current_stock' in product_data:
                    current = conn.execute(text('''
                        SELECT current_stock FROM inventory_items WHERE id = :product_id
                    '''), {"product_id": product_id}).fetchone()
                    if current:
                        old_stock = current[0]
                        new_stock = product_data['current_stock']
                        if new_stock != old_stock:
                            quantity_delta = new_stock - old_stock
                            conn.execute(text('''
                                INSERT INTO stock_movements
                                (user_id, product_id, movement_type, quantity, notes)
                                VALUES (:user_id, :product_id, 'adjustment', :quantity, 'Manual stock adjustment')
                            '''), {
                                "user_id": user_id,
                                "product_id": product_id,
                                "quantity": quantity_delta
                            })
                            conn.execute(text('''
                                UPDATE inventory_items SET current_stock = :new_stock WHERE id = :product_id
                            '''), {"new_stock": new_stock, "product_id": product_id})
                return True
        except Exception as e:
            logger.error(f"Error updating product: {e}")
            return False

    @staticmethod
    def get_product_details(account_id, product_id):
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, name, sku, category, description, current_stock,
                           min_stock_level, cost_price, selling_price, supplier, location
                    FROM inventory_items
                    WHERE id = :product_id AND account_id = :aid AND is_active = TRUE
                '''), {"product_id": product_id, "aid": account_id}).fetchone()

                if result:
                    return {
                        'id': result.id,
                        'name': result.name,
                        'sku': result.sku or '',
                        'category': result.category or '',
                        'description': result.description or '',
                        'current_stock': result.current_stock,
                        'min_stock_level': result.min_stock_level or 5,
                        'cost_price': float(result.cost_price) if result.cost_price else 0.0,
                        'selling_price': float(result.selling_price) if result.selling_price else 0.0,
                        'supplier': result.supplier or '',
                        'location': result.location or ''
                    }
                return None
        except Exception as e:
            logger.error(f"Error getting product details: {e}")
            return None

    @staticmethod
    def update_stock_delta(user_id, account_id, product_id, quantity_delta, movement_type, reference_id=None, notes=None):
        try:
            with DB_ENGINE.begin() as conn:
                logger.info(f"update_stock_delta: user={user_id}, prod={product_id}, delta={quantity_delta}")

                result = conn.execute(text('''
                    SELECT name, current_stock FROM inventory_items
                    WHERE id = :product_id AND account_id = :aid AND is_active = TRUE
                    FOR UPDATE
                '''), {"product_id": product_id, "aid": account_id}).fetchone()

                if not result:
                    logger.warning(f"Product not found or inactive: id={product_id}, account={account_id}")
                    return False

                product_name, current_stock = result
                if not isinstance(current_stock, Decimal):
                    current_stock = Decimal(str(current_stock))

                if isinstance(quantity_delta, (int, float)):
                    quantity_delta = Decimal(str(quantity_delta))
                elif isinstance(quantity_delta, str):
                    quantity_delta = Decimal(quantity_delta)

                new_stock = current_stock + quantity_delta
                if new_stock < 0:
                    logger.warning(f"Negative stock prevented for product {product_id}")
                    return False

                conn.execute(text('''
                    UPDATE inventory_items SET current_stock = :new_stock WHERE id = :product_id
                '''), {"new_stock": new_stock, "product_id": product_id})

                conn.execute(text('''
                    INSERT INTO stock_movements
                    (user_id, product_id, movement_type, quantity, reference_id, notes)
                    VALUES (:user_id, :product_id, :movement_type, :quantity, :reference_id, :notes)
                '''), {
                    "user_id": user_id,
                    "product_id": product_id,
                    "movement_type": movement_type,
                    "quantity": quantity_delta,
                    "reference_id": reference_id,
                    "notes": notes
                })

                logger.info(f"Stock updated: {current_stock} → {new_stock} ({movement_type})")
                return True

        except Exception as e:
            logger.error(f"Stock delta update failed: {e}", exc_info=True)
            return False

    @staticmethod
    def delete_product(user_id, account_id, product_id, reason=None):
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    SELECT name, current_stock FROM inventory_items
                    WHERE id = :product_id AND account_id = :aid AND is_active = TRUE
                """), {"product_id": product_id, "aid": account_id}).fetchone()
                if not result:
                    return False

                product_name, current_stock = result

                conn.execute(text("""
                    UPDATE inventory_items SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :product_id AND account_id = :aid
                """), {"product_id": product_id, "aid": account_id})

                if current_stock > 0:
                    conn.execute(text("""
                        INSERT INTO stock_movements (user_id, product_id, movement_type, quantity, notes)
                        VALUES (:user_id, :product_id, 'deletion', :quantity, :notes)
                    """), {
                        "user_id": user_id,
                        "product_id": product_id,
                        "quantity": -current_stock,
                        "notes": reason or "Product deleted"
                    })

                logger.info(f"Product {product_name} (ID: {product_id}) soft deleted")
                return True
        except Exception as e:
            logger.error(f"Error deleting product: {e}")
            return False

    @staticmethod
    def get_low_stock_alerts(account_id, threshold=None):
        try:
            with DB_ENGINE.connect() as conn:
                query = text('''
                    SELECT name, sku, current_stock, min_stock_level
                    FROM inventory_items
                    WHERE account_id = :aid
                      AND is_active = TRUE
                      AND current_stock <= COALESCE(min_stock_level, :threshold)
                    ORDER BY current_stock ASC
                ''')
                result = conn.execute(query, {"aid": account_id, "threshold": threshold or 10})
                alerts = []
                for row in result:
                    alerts.append({
                        'name': row.name,
                        'sku': row.sku or 'N/A',
                        'current_stock': row.current_stock,
                        'reorder_level': row.min_stock_level or threshold or 10,
                    })
                return alerts
        except Exception as e:
            logger.error(f"Low stock alert error: {e}")
            return []

    @staticmethod
    def get_inventory_items(account_id):
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, name, sku, category, current_stock, min_stock_level,
                           cost_price, selling_price, supplier, location
                    FROM inventory_items
                    WHERE account_id = :aid AND is_active = TRUE
                    ORDER BY name
                '''), {"aid": account_id})
                items = []
                for row in result:
                    items.append({
                        'id': row.id,
                        'name': row.name,
                        'sku': row.sku or 'N/A',
                        'category': row.category or '',
                        'current_stock': row.current_stock,
                        'min_stock_level': row.min_stock_level or 10,
                        'cost_price': float(row.cost_price) if row.cost_price else 0.0,
                        'selling_price': float(row.selling_price) if row.selling_price else 0.0,
                        'supplier': row.supplier or '',
                        'location': row.location or ''
                    })
                return items
        except Exception as e:
            logger.error(f"Error fetching inventory: {e}")
            return []

    @staticmethod
    def get_inventory_report(account_id):
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT name, sku, barcode, category, current_stock, unit_type,
                           min_stock_level, cost_price, selling_price, supplier, location,
                           is_perishable, expiry_date, batch_number
                    FROM inventory_items
                    WHERE account_id = :aid AND is_active = TRUE
                    ORDER BY name
                '''), {"aid": account_id})
                report_data = []
                for row in result:
                    report_data.append({
                        'name': row.name,
                        'sku': row.sku or 'N/A',
                        'barcode': row.barcode or 'N/A',
                        'category': row.category or '',
                        'current_stock': f"{float(row.current_stock):.3f}" if row.current_stock is not None else '0.000',
                        'unit_type': row.unit_type or 'piece',
                        'min_stock': row.min_stock_level or 0,
                        'cost_price': float(row.cost_price) if row.cost_price is not None else 0.0,
                        'selling_price': float(row.selling_price) if row.selling_price is not None else 0.0,
                        'supplier': row.supplier or '',
                        'location': row.location or '',
                        'is_perishable': 'Yes' if row.is_perishable else 'No',
                        'expiry_date': row.expiry_date.strftime('%Y-%m-%d') if row.expiry_date else '',
                        'batch_number': row.batch_number or '',
                    })
                return report_data
        except Exception as e:
            logger.error(f"Error generating inventory report data: {e}")
            return []
