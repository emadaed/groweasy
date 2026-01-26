# core/inventory.py - FINAL COMPLETE & TESTED VERSION

from core.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class InventoryManager:

    @staticmethod
    def add_product(user_id, product_data):
        """Add new product to inventory - YOUR ORIGINAL CODE (PERFECT)"""
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text('''
                    INSERT INTO inventory_items
                    (user_id, name, sku, category, description, current_stock,
                     min_stock_level, cost_price, selling_price, supplier, location)
                    VALUES (:user_id, :name, :sku, :category, :description, :current_stock,
                            :min_stock_level, :cost_price, :selling_price, :supplier, :location)
                    RETURNING id
                '''), {
                    "user_id": user_id,
                    "name": product_data['name'],
                    "sku": product_data.get('sku'),
                    "category": product_data.get('category'),
                    "description": product_data.get('description'),
                    "current_stock": product_data.get('current_stock', 0),
                    "min_stock_level": product_data.get('min_stock_level', 5),
                    "cost_price": product_data.get('cost_price', 0.0),
                    "selling_price": product_data.get('selling_price', 0.0),
                    "supplier": product_data.get('supplier'),
                    "location": product_data.get('location')
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

                logger.info(f"Product added: {product_data['name']} (ID: {result[0] if result else 'None'})")
                return result[0] if result else None
        except Exception as e:
            logger.error(f"Error adding product: {e}")
            return None

    @staticmethod
    def update_product(user_id, product_id, product_data):
        """Update existing product"""
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

                # Handle stock adjustment if current_stock changed
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
    def get_product_details(user_id, product_id):
        """Get product details - FIXES THE ERROR"""
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, name, sku, category, description, current_stock,
                           min_stock_level, cost_price, selling_price, supplier, location
                    FROM inventory_items
                    WHERE id = :product_id AND user_id = :user_id AND is_active = TRUE
                '''), {"product_id": product_id, "user_id": user_id}).fetchone()

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
    def update_stock_delta(user_id, product_id, quantity_delta, movement_type, reference_id=None, notes=None):
        """Update stock by delta - used by invoice/PO"""
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text('''
                    SELECT name, current_stock FROM inventory_items
                    WHERE id = :product_id AND user_id = :user_id AND is_active = TRUE
                    FOR UPDATE
                '''), {"product_id": product_id, "user_id": user_id}).fetchone()

                if not result:
                    return False

                product_name, current_stock = result
                new_stock = current_stock + quantity_delta

                if new_stock < 0:
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

                return True
        except Exception as e:
            logger.error(f"Stock delta update failed: {e}")
            return False


    @staticmethod
    def get_low_stock_alerts(user_id, threshold=None):
        """Get items below min_stock_level or fallback threshold"""
        try:
            with DB_ENGINE.connect() as conn:
                query = text('''
                    SELECT name, sku, current_stock, min_stock_level
                    FROM inventory_items
                    WHERE user_id = :user_id
                      AND is_active = TRUE
                      AND current_stock <= COALESCE(min_stock_level, :threshold)
                    ORDER BY current_stock ASC
                ''')
                result = conn.execute(query, {"user_id": user_id, "threshold": threshold or 10})

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
    def get_inventory_items(user_id):
        """Get all active inventory items for the user"""
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, name, sku, category, current_stock, min_stock_level,
                           cost_price, selling_price, supplier, location
                    FROM inventory_items
                    WHERE user_id = :user_id AND is_active = TRUE
                    ORDER BY name
                '''), {"user_id": user_id})

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

