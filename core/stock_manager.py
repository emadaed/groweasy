"""
Stock Management System with Audit Trail
"""
import logging
from datetime import datetime
from sqlalchemy import text
from core.db import DB_ENGINE

logger = logging.getLogger(__name__)

class StockManager:
    @staticmethod
    def update_stock_from_document(user_id, document_data, document_type, document_number):
        """
        Update stock based on document (invoice/PO)
        Returns: (success, message)
        """
        try:
            items = document_data.get('items', [])
            if not items:
                return True, "No items to process"

            for item in items:
                product_id = item.get('product_id')
                product_name = item.get('name', 'Unknown')
                quantity = int(item.get('qty', 1))

                if not product_id:
                    logger.warning(f"No product_id for item: {product_name}")
                    continue

                # Get current stock
                with DB_ENGINE.connect() as conn:
                    result = conn.execute(text("""
                        SELECT current_stock FROM inventory_items
                        WHERE id = :product_id AND user_id = :user_id
                    """), {"product_id": product_id, "user_id": user_id}).fetchone()

                    if not result:
                        logger.error(f"Product not found: {product_id}")
                        continue

                    current_stock = result[0]

                    # Calculate new stock
                    if document_type == 'purchase_order':
                        new_stock = current_stock + quantity
                        movement_type = 'purchase'
                        notes = f"Purchased {quantity} units via PO: {document_number}"
                    else:  # invoice
                        if current_stock < quantity:
                            return False, f"Insufficient stock for '{product_name}'. Available: {current_stock}, Requested: {quantity}"
                        new_stock = current_stock - quantity
                        movement_type = 'sale'
                        notes = f"Sold {quantity} units via Invoice: {document_number}"

                    # Update stock
                    StockManager._update_stock_record(
                        user_id, product_id, new_stock,
                        movement_type, document_number, notes
                    )

                    # Update audit trail
                    StockManager._add_stock_audit(
                        user_id, product_id, quantity, movement_type,
                        document_number, document_type, notes
                    )

            return True, "Stock updated successfully"

        except Exception as e:
            logger.error(f"Stock update error: {e}")
            return False, f"Stock update failed: {str(e)}"

    @staticmethod
    def _update_stock_record(user_id, product_id, new_quantity, movement_type, reference_id, notes):
        """Update stock quantity"""
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE inventory_items
                SET current_stock = :new_quantity,
                    last_updated = CURRENT_TIMESTAMP
                WHERE id = :product_id AND user_id = :user_id
            """), {
                "user_id": user_id,
                "product_id": product_id,
                "new_quantity": new_quantity
            })

    @staticmethod
    def _add_stock_audit(user_id, product_id, quantity, movement_type, reference_id, doc_type, notes):
        """Add audit trail entry"""
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO stock_audit_trail
                (user_id, product_id, quantity_change, movement_type,
                 reference_id, document_type, notes, created_at)
                VALUES (:user_id, :product_id, :quantity_change, :movement_type,
                        :reference_id, :document_type, :notes, CURRENT_TIMESTAMP)
            """), {
                "user_id": user_id,
                "product_id": product_id,
                "quantity_change": quantity if movement_type == 'purchase' else -quantity,
                "movement_type": movement_type,
                "reference_id": reference_id,
                "document_type": doc_type,
                "notes": notes
            })

    @staticmethod
    def validate_stock_availability(user_id, items, document_type='invoice'):
        """
        Validate stock before creating document
        Returns: (is_valid, error_message)
        """
        if document_type == 'purchase_order':
            return True, ""  # No validation needed for purchases

        try:
            with DB_ENGINE.connect() as conn:
                for item in items:
                    if not item.get('product_id'):
                        continue

                    product_id = item['product_id']
                    requested_qty = int(item.get('qty', 1))

                    result = conn.execute(text("""
                        SELECT name, current_stock
                        FROM inventory_items
                        WHERE id = :product_id AND user_id = :user_id
                    """), {"product_id": product_id, "user_id": user_id}).fetchone()

                    if not result:
                        return False, f"Product ID {product_id} not found in inventory"

                    product_name, current_stock = result
                    if current_stock < requested_qty:
                        return False, f"Insufficient stock for '{product_name}'. Available: {current_stock}, Required: {requested_qty}"

            return True, ""

        except Exception as e:
            logger.error(f"Stock validation error: {e}")
            return False, f"Stock validation error: {str(e)}"
