# app/services/inventory.py
from app.services.email import send_email
from decimal import Decimal
from app.services.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime
import logging
from app.services.webhooks import fire_webhook

logger = logging.getLogger(__name__)

class InventoryManager:

    @staticmethod
    def add_product(user_id, account_id, product_data):
        try:
            with DB_ENGINE.begin() as conn:
                # Check if product exists (active or inactive) with same user and sku
                existing = conn.execute(text("""
                    SELECT id, is_active, current_stock FROM inventory_items
                    WHERE user_id = :user_id AND sku = :sku
                """), {"user_id": user_id, "sku": product_data.get('sku')}).fetchone()

                if existing:
                    product_id, is_active, old_stock = existing
                    if is_active:
                        # Already active – skip insertion, return existing id
                        logger.info(f"Product with SKU {product_data['sku']} already active, returning existing ID {product_id}")
                        return product_id

                    # Inactive product – reactivate and update all fields
                    logger.info(f"Reactivating product ID {product_id} with SKU {product_data['sku']}")

                    # Convert new_stock to Decimal for arithmetic
                    new_stock = Decimal(str(product_data.get('current_stock', 0)))

                    conn.execute(text("""
                        UPDATE inventory_items
                        SET name = :name,
                            category = :category,
                            description = :description,
                            min_stock_level = :min_stock_level,
                            cost_price = :cost_price,
                            selling_price = :selling_price,
                            supplier = :supplier,
                            location = :location,
                            unit_type = :unit_type,
                            is_perishable = :is_perishable,
                            expiry_date = :expiry_date,
                            batch_number = :batch_number,
                            barcode = :barcode,
                            pack_size = :pack_size,
                            weight_kg = :weight_kg,
                            current_stock = :current_stock,
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE id = :id
                    """), {
                        "id": product_id,
                        "name": product_data['name'],
                        "category": product_data.get('category'),
                        "description": product_data.get('description'),
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
                        "current_stock": new_stock
                    })

                    # Log stock movement if quantity changed
                    if new_stock != old_stock:
                        conn.execute(text("""
                            INSERT INTO stock_movements
                            (user_id, product_id, movement_type, quantity, notes)
                            VALUES (:user_id, :product_id, 'reactivation', :quantity, 'Reactivated with stock update')
                        """), {
                            "user_id": user_id,
                            "product_id": product_id,
                            "quantity": new_stock - old_stock
                        })

                    # Fire webhook (reactivation)
                    fire_webhook(account_id, 'product.created', {
                        'product_id': product_id,
                        'name': product_data['name'],
                        'sku': product_data.get('sku'),
                        'category': product_data.get('category'),
                        'current_stock': float(new_stock)   # keep webhook payload as float
                    })

                    logger.info(f"Product reactivated: {product_data['name']} (ID: {product_id})")
                    return product_id

                else:
                    # Insert new product
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

                    product_id = result[0]
                    if product_data.get('current_stock', 0) > 0:
                        conn.execute(text('''
                            INSERT INTO stock_movements
                            (user_id, product_id, movement_type, quantity, notes)
                            VALUES (:user_id, :product_id, 'initial', :quantity, 'Initial stock')
                        '''), {
                            "user_id": user_id,
                            "product_id": product_id,
                            "quantity": product_data.get('current_stock', 0)
                        })

                    # Fire webhook for new product
                    fire_webhook(account_id, 'product.created', {
                        'product_id': product_id,
                        'name': product_data['name'],
                        'sku': product_data.get('sku'),
                        'category': product_data.get('category'),
                        'current_stock': product_data.get('current_stock', 0)
                    })

                    logger.info(f"Product added: {product_data['name']} (ID: {product_id})")
                    return product_id

        except Exception as e:
            logger.error(f"Error adding product: {e}", exc_info=True)
            return None

    @staticmethod
    def add_product_with_location(user_id, account_id, product_data, location_data=None):
        """
        Add product with optional location assignment
        location_data: {'location_id': 1, 'quantity': 50} or [{'location_id':1,'quantity':30}, ...]
        """
        try:
            # First add product using existing method
            product_id = InventoryManager.add_product(user_id, account_id, product_data)
            
            if not product_id:
                return None
            
            # If location data provided, add stock to locations
            if location_data:
                from app.services.location_inventory import LocationInventoryManager
                
                if isinstance(location_data, list):
                    for loc in location_data:
                        LocationInventoryManager.add_product_to_location(
                            product_id, loc['location_id'], loc['quantity'], user_id
                        )
                else:
                    LocationInventoryManager.add_product_to_location(
                        product_id, location_data['location_id'], 
                        location_data['quantity'], user_id
                    )
            
            return product_id
        except Exception as e:
            logger.error(f"Error adding product with location: {e}")
            return None

    @staticmethod
    def get_product_details_with_locations(account_id, product_id):
        """Get product details including location breakdown"""
        product = InventoryManager.get_product_details(account_id, product_id)
        if product:
            from app.services.location_inventory import LocationInventoryManager
            product['location_breakdown'] = LocationInventoryManager.get_product_location_breakdown(product_id)
        return product
    
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
    def update_stock_delta(user_id, account_id, product_id, quantity_delta, movement_type, reference_id=None, notes=None, location_id=None):
        """
        Update stock. If location_id provided, update location-specific stock (product_locations).
        Otherwise fall back to global inventory_items.current_stock.
        """
        from app.services.location_inventory import LocationInventoryManager
        from decimal import Decimal

        # NEW: Location-aware deduction (sales, damage, transfers)
        if location_id is not None:
            try:
                if quantity_delta < 0:
                    # Removing stock (sale, damage)
                    success = LocationInventoryManager.remove_from_location(
                        product_id=product_id,
                        location_id=location_id,
                        quantity=abs(quantity_delta),
                        user_id=user_id
                    )
                else:
                    # Adding stock (return, purchase)
                    success = LocationInventoryManager.add_product_to_location(
                        product_id=product_id,
                        location_id=location_id,
                        quantity=quantity_delta,
                        user_id=user_id
                    )
                
                if success:
                    # Optionally sync global stock (for legacy reports)
                    with DB_ENGINE.begin() as conn:
                        total = conn.execute(text("""
                            SELECT COALESCE(SUM(quantity), 0) FROM product_locations WHERE product_id = :pid
                        """), {"pid": product_id}).scalar()
                        conn.execute(text("""
                            UPDATE inventory_items SET current_stock = :total WHERE id = :pid
                        """), {"total": total, "pid": product_id})
                    
                    # Log movement — clean type, location_id stored as column
                    with DB_ENGINE.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO stock_movements
                            (user_id, product_id, movement_type, quantity,
                             reference_id, notes, location_id)
                            VALUES (:uid, :pid, :type, :qty, :ref, :notes, :loc_id)
                        """), {
                            "uid":    user_id,
                            "pid":    product_id,
                            "type":   movement_type,        # clean — no _location_X suffix
                            "qty":    quantity_delta,
                            "ref":    reference_id,
                            "notes":  notes or '',
                            "loc_id": location_id
                        })
                    return True
                return False
            except Exception as e:
                logger.error(f"Location stock update failed: {e}", exc_info=True)
                return False

        # LEGACY: Global stock update (no location provided)
        try:
            with DB_ENGINE.begin() as conn:
                # Lock row
                result = conn.execute(text("""
                    SELECT current_stock FROM inventory_items
                    WHERE id = :pid AND account_id = :aid AND is_active = TRUE
                    FOR UPDATE
                """), {"pid": product_id, "aid": account_id}).fetchone()
                if not result:
                    logger.warning(f"Product {product_id} not found or inactive")
                    return False
                
                current_stock = Decimal(str(result[0]))
                delta = Decimal(str(quantity_delta))
                new_stock = current_stock + delta
                if new_stock < 0:
                    logger.warning(f"Insufficient stock for product {product_id}")
                    return False
                
                conn.execute(text("""
                    UPDATE inventory_items SET current_stock = :new WHERE id = :pid
                """), {"new": new_stock, "pid": product_id})
                
                conn.execute(text("""
                    INSERT INTO stock_movements
                    (user_id, product_id, movement_type, quantity, reference_id, notes)
                    VALUES (:uid, :pid, :type, :qty, :ref, :notes)
                """), {
                    "uid": user_id,
                    "pid": product_id,
                    "type": movement_type,
                    "qty": quantity_delta,
                    "ref": reference_id,
                    "notes": notes
                })
                logger.info(f"Global stock updated: {current_stock} → {new_stock} ({movement_type})")
                return True
        except Exception as e:
            logger.error(f"Global stock update failed: {e}", exc_info=True)
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
        """
        Fetch all active inventory items with their location breakdowns.        
        """
        try:
            with DB_ENGINE.connect() as conn:
                # Single query: products + all their location stock in one shot
                rows = conn.execute(text("""
                    SELECT
                        i.id, i.name, i.sku, i.category, i.current_stock,
                        i.min_stock_level, i.cost_price, i.selling_price,
                        i.supplier, i.location,
                        l.id          AS loc_id,
                        l.location_name,
                        l.location_code,
                        l.location_type,
                        pl.quantity   AS loc_qty,
                        pl.reserved_quantity
                    FROM inventory_items i
                    LEFT JOIN product_locations pl ON pl.product_id = i.id
                    LEFT JOIN locations l ON l.id = pl.location_id AND l.is_active = TRUE
                    WHERE i.account_id = :aid AND i.is_active = TRUE
                    ORDER BY i.name, l.location_name
                """), {"aid": account_id}).fetchall()

            # Assemble: group location rows per product
            from collections import defaultdict
            from decimal import Decimal

            product_map: dict = {}   # product_id -> item dict
            loc_map: dict = defaultdict(list)  # product_id -> [location dicts]

            for row in rows:
                pid = row.id
                if pid not in product_map:
                    product_map[pid] = {
                        'id': pid,
                        'name': row.name,
                        'sku': row.sku or 'N/A',
                        'category': row.category or '',
                        'current_stock': float(row.current_stock) if row.current_stock else 0,
                        'min_stock_level': row.min_stock_level or 10,
                        'cost_price': float(row.cost_price) if row.cost_price else 0.0,
                        'selling_price': float(row.selling_price) if row.selling_price else 0.0,
                        'supplier': row.supplier or '',
                        'location': row.location or 'Main',
                        'location_breakdown': None
                    }

                if row.loc_id is not None:
                    loc_map[pid].append({
                        'location_id': row.loc_id,
                        'location_name': row.location_name,
                        'location_code': row.location_code,
                        'quantity': float(row.loc_qty) if row.loc_qty else 0.0,
                        'reserved': float(row.reserved_quantity) if row.reserved_quantity else 0.0,
                        'type': row.location_type
                    })

            # Attach location breakdown to each product
            for pid, locs in loc_map.items():
                if pid in product_map:
                    total = sum(l['quantity'] for l in locs)
                    product_map[pid]['location_breakdown'] = {
                        'total_stock': total,
                        'locations': locs
                    }

            return list(product_map.values())

        except Exception as e:
            logger.error(f"Error fetching inventory items: {e}", exc_info=True)
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

    @staticmethod
    def get_stock_movements(account_id, product_id=None, limit=100, offset=0):
        """Get stock movements for the account, optionally filtered by product."""
        with DB_ENGINE.connect() as conn:
            base_query = """
                SELECT sm.id, sm.product_id, i.name as product_name, i.sku,
                       sm.movement_type, sm.quantity, sm.reference_id, sm.notes, sm.created_at
                FROM stock_movements sm
                JOIN inventory_items i ON sm.product_id = i.id
                WHERE i.account_id = :aid
            """
            params = {"aid": account_id}
            if product_id:
                base_query += " AND sm.product_id = :pid"
                params["pid"] = product_id
            base_query += " ORDER BY sm.created_at DESC LIMIT :limit OFFSET :offset"
            params["limit"] = limit
            params["offset"] = offset
            rows = conn.execute(text(base_query), params).fetchall()

        return [{
            'id': r[0],
            'product_id': r[1],
            'product_name': r[2],
            'sku': r[3] or '—',
            'movement_type': r[4],
            'quantity': r[5],
            'reference_id': r[6],
            'notes': r[7],
            'created_at': r[8].isoformat() if r[8] else None
        } for r in rows]
    
