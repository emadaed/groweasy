from decimal import Decimal
from sqlalchemy import text
from app.services.db import DB_ENGINE
from app.services.inventory import InventoryManager
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class LocationInventoryManager:
    
    @staticmethod
    def create_location(account_id, location_data):
        """Create a new storage location"""
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    INSERT INTO locations 
                    (account_id, parent_location_id, location_code, location_name, 
                     location_type, address, manager_name, phone)
                    VALUES 
                    (:account_id, :parent_id, :code, :name, :type, :address, :manager, :phone)
                    RETURNING id
                """), {
                    "account_id": account_id,
                    "parent_id": location_data.get('parent_location_id'),
                    "code": location_data['location_code'],
                    "name": location_data['location_name'],
                    "type": location_data.get('location_type', 'storage'),
                    "address": location_data.get('address'),
                    "manager": location_data.get('manager_name'),
                    "phone": location_data.get('phone')
                })
                return result.scalar()
        except Exception as e:
            logger.error(f"Error creating location: {e}")
            return None
    
    @staticmethod
    def get_account_locations(account_id, include_inactive=False):
        """Get all locations for an account"""
        try:
            with DB_ENGINE.connect() as conn:
                query = """
                    SELECT id, parent_location_id, location_code, location_name, 
                           location_type, is_active, address, manager_name, phone,
                           created_at
                    FROM locations
                    WHERE account_id = :aid
                """
                if not include_inactive:
                    query += " AND is_active = TRUE"
                query += " ORDER BY location_name"
                
                rows = conn.execute(text(query), {"aid": account_id}).fetchall()
                return [dict(row._mapping) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching locations: {e}")
            return []
    
    @staticmethod
    def add_product_to_location(product_id, location_id, quantity, user_id):
        """Add stock to a specific location"""
        try:
            with DB_ENGINE.begin() as conn:
                # Check if product already exists in this location
                existing = conn.execute(text("""
                    SELECT id, quantity FROM product_locations
                    WHERE product_id = :pid AND location_id = :lid
                """), {"pid": product_id, "lid": location_id}).fetchone()
                
                if existing:
                    # Update existing
                    new_quantity = Decimal(str(existing[1])) + Decimal(str(quantity))
                    conn.execute(text("""
                        UPDATE product_locations
                        SET quantity = :qty, updated_at = NOW()
                        WHERE product_id = :pid AND location_id = :lid
                    """), {"qty": new_quantity, "pid": product_id, "lid": location_id})
                else:
                    # Insert new
                    conn.execute(text("""
                        INSERT INTO product_locations (product_id, location_id, quantity)
                        VALUES (:pid, :lid, :qty)
                    """), {"pid": product_id, "lid": location_id, "qty": quantity})
                
                # Log movement
                conn.execute(text("""
                    INSERT INTO stock_movements
                    (user_id, product_id, movement_type, quantity, notes)
                    VALUES (:uid, :pid, 'location_add', :qty, 'Added to location')
                """), {"uid": user_id, "pid": product_id, "qty": quantity})
                
                return True
        except Exception as e:
            logger.error(f"Error adding product to location: {e}")
            return False
    
    @staticmethod
    def transfer_between_locations(account_id, product_id, from_location_id, 
                                   to_location_id, quantity, user_id, notes=None):
        """Transfer stock between locations"""
        try:
            with DB_ENGINE.begin() as conn:
                # Generate transfer number
                transfer_number = f"TRF-{datetime.now().strftime('%Y%m%d%H%M%S')}-{product_id}"
                
                # Create transfer record
                result = conn.execute(text("""
                    INSERT INTO location_transfers
                    (transfer_number, account_id, product_id, from_location_id, 
                     to_location_id, quantity, requested_by, notes)
                    VALUES
                    (:num, :aid, :pid, :from_loc, :to_loc, :qty, :uid, :notes)
                    RETURNING id
                """), {
                    "num": transfer_number,
                    "aid": account_id,
                    "pid": product_id,
                    "from_loc": from_location_id,
                    "to_loc": to_location_id,
                    "qty": quantity,
                    "uid": user_id,
                    "notes": notes
                })
                
                transfer_id = result.scalar()
                
                # Deduct from source location
                success = LocationInventoryManager.remove_from_location(
                    product_id, from_location_id, quantity, user_id
                )
                
                if not success:
                    raise Exception("Failed to remove from source location")
                
                # Add to destination location
                success = LocationInventoryManager.add_product_to_location(
                    product_id, to_location_id, quantity, user_id
                )
                
                if not success:
                    # Rollback will happen automatically
                    raise Exception("Failed to add to destination location")
                
                # Mark transfer as completed
                conn.execute(text("""
                    UPDATE location_transfers
                    SET status = 'completed', completed_at = NOW()
                    WHERE id = :tid
                """), {"tid": transfer_id})
                
                logger.info(f"Transfer {transfer_number} completed: {quantity} units")
                return transfer_number
                
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return None
    
    @staticmethod
    def remove_from_location(product_id, location_id, quantity, user_id):
        """Remove stock from a specific location"""
        try:
            with DB_ENGINE.begin() as conn:
                # Check current stock
                current = conn.execute(text("""
                    SELECT quantity FROM product_locations
                    WHERE product_id = :pid AND location_id = :lid
                """), {"pid": product_id, "lid": location_id}).fetchone()
                
                if not current:
                    logger.warning(f"Product {product_id} not found in location {location_id}")
                    return False
                
                new_quantity = Decimal(str(current[0])) - Decimal(str(quantity))
                
                if new_quantity < 0:
                    logger.warning(f"Insufficient stock in location {location_id}")
                    return False
                
                if new_quantity == 0:
                    # Remove record if zero
                    conn.execute(text("""
                        DELETE FROM product_locations
                        WHERE product_id = :pid AND location_id = :lid
                    """), {"pid": product_id, "lid": location_id})
                else:
                    # Update quantity
                    conn.execute(text("""
                        UPDATE product_locations
                        SET quantity = :qty, updated_at = NOW()
                        WHERE product_id = :pid AND location_id = :lid
                    """), {"qty": new_quantity, "pid": product_id, "lid": location_id})
                
                # Log movement
                conn.execute(text("""
                    INSERT INTO stock_movements
                    (user_id, product_id, movement_type, quantity, notes)
                    VALUES (:uid, :pid, 'location_remove', :qty, 'Removed from location')
                """), {"uid": user_id, "pid": product_id, "qty": -quantity})
                
                return True
        except Exception as e:
            logger.error(f"Error removing from location: {e}")
            return False
    
    @staticmethod
    def get_product_location_breakdown(product_id):
        """Get stock breakdown by location for a product"""
        try:
            with DB_ENGINE.connect() as conn:
                rows = conn.execute(text("""
                    SELECT l.location_name, l.location_code, pl.quantity,
                           pl.reserved_quantity, l.location_type
                    FROM product_locations pl
                    JOIN locations l ON pl.location_id = l.id
                    WHERE pl.product_id = :pid AND l.is_active = TRUE
                    ORDER BY l.location_name
                """), {"pid": product_id}).fetchall()
                
                breakdown = []
                total = Decimal('0')
                for row in rows:
                    total += row.quantity
                    breakdown.append({
                        'location_name': row.location_name,
                        'location_code': row.location_code,
                        'quantity': float(row.quantity),
                        'reserved': float(row.reserved_quantity) if row.reserved_quantity else 0,
                        'type': row.location_type
                    })
                
                return {
                    'total_stock': float(total),
                    'locations': breakdown
                }
        except Exception as e:
            logger.error(f"Error getting location breakdown: {e}")
            return {'total_stock': 0, 'locations': []}
    
    @staticmethod
    def get_location_stock_value(account_id, location_id=None):
        """Get total inventory value for a location or all locations"""
        try:
            with DB_ENGINE.connect() as conn:
                query = """
                    SELECT 
                        COALESCE(SUM(pl.quantity * i.cost_price), 0) as total_value
                    FROM product_locations pl
                    JOIN inventory_items i ON pl.product_id = i.id
                    JOIN locations l ON pl.location_id = l.id
                    WHERE l.account_id = :aid AND l.is_active = TRUE
                """
                params = {"aid": account_id}
                
                if location_id:
                    query += " AND pl.location_id = :lid"
                    params["lid"] = location_id
                
                result = conn.execute(text(query), params).scalar()
                return float(result) if result else 0.0
        except Exception as e:
            logger.error(f"Error calculating location value: {e}")
            return 0.0
