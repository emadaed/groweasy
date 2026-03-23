# app/services/invoice_service.py - FINAL PROFESSIONAL VERSION

import logging
from decimal import Decimal
from app.services.db import DB_ENGINE
from app.services.number_generator import NumberGenerator
from app.services.auth import save_user_invoice
from app.services.purchases import save_purchase_order
from app.services.inventory import InventoryManager
from app.services.invoice_logic import prepare_invoice_data
from app.services.invoice_logic_po import prepare_po_data
from app.services.account import check_invoice_limit, increment_invoice_count, has_feature
from sqlalchemy import text

logger = logging.getLogger(__name__)

class InvoiceService:
    def __init__(self, user_id):
        self.user_id = user_id
        self.errors = []
        self.warnings = []
        # Fetch account_id from users table
        with DB_ENGINE.connect() as conn:
            row = conn.execute(
                text("SELECT account_id FROM users WHERE id = :uid"),
                {"uid": user_id}
            ).first()
            self.account_id = row[0] if row else None

    def create_invoice(self, form_data, files=None):
        try:
            invoice_data = prepare_invoice_data(form_data, files=files)
            # Check invoice limit
            if self.account_id:
                allowed, msg = check_invoice_limit(self.account_id)
                if not allowed:
                    self.errors.append(msg)
                    return None, self.errors

            # Generate number
            invoice_data['invoice_number'] = NumberGenerator.generate_invoice_number(self.account_id)
            # Save
            save_user_invoice(self.user_id, self.account_id, invoice_data)

            # NEW: Insert items into invoice_items table
            from app.services.db import DB_ENGINE
            from sqlalchemy import text
            with DB_ENGINE.begin() as conn:
                # Get the newly created invoice ID
                result = conn.execute(text("""
                    SELECT id FROM user_invoices
                    WHERE user_id = :uid AND invoice_number = :inv_num
                """), {'uid': self.user_id, 'inv_num': invoice_data['invoice_number']}).fetchone()

                if result:
                    invoice_id = result[0]
                    # Insert each item into invoice_items
                    for item in invoice_data.get('items', []):
                        conn.execute(text("""
                            INSERT INTO invoice_items (invoice_id, product_id, quantity, unit_price, total)
                            VALUES (:inv_id, :prod_id, :qty, :price, :total)
                        """), {
                            'inv_id': invoice_id,
                            'prod_id': item['product_id'],
                            'qty': item['qty'],
                            'price': item['price'],
                            'total': item['total']
                        })

            #Update stock - decrease for sales (EXACT DECIMAL QUANTITY)
            movement_type = 'sale'

            for item in invoice_data.get('items', []):
                if item.get('product_id'):
                    # Ensure qty is Decimal
                    qty_sold = Decimal(str(item['qty']))  # safe conversion from float
                    success = InventoryManager.update_stock_delta(
                        self.user_id,
                        self.account_id,
                        item['product_id'],
                        -qty_sold,                             # Decimal negative
                        movement_type,
                        reference_id=invoice_data['invoice_number'],
                        notes=f"Sold {qty_sold:.3f} {item.get('unit_type', 'unit')} via invoice {invoice_data['invoice_number']}"
                    )
                    if not success:
                        product_name = item.get('name', 'Unknown')
                        with DB_ENGINE.connect() as conn:
                            stock = conn.execute(text(
                                "SELECT current_stock FROM inventory_items WHERE id = :pid"
                            ), {"pid": item['product_id']}).scalar() or Decimal('0')
                        self.warnings.append(
                            f"Stock update failed for {product_name}: "
                            f"requested -{qty_sold:.3f}, current stock {stock}"
                        )
                        # Increment invoice count
                        if self.account_id:
                            increment_invoice_count(self.account_id)

            return invoice_data, self.errors or self.warnings

        except Exception as e:
            logger.error(f"Invoice creation failed: {e}", exc_info=True)
            self.errors.append("System error during invoice creation")
            return None, self.errors

    def create_purchase_order(self, form_data, files=None):
        try:
            po_data = prepare_po_data(form_data, files=files)
            
            # Check if plan allows purchase orders
            if self.account_id:
                if not has_feature(self.account_id, 'purchase_orders'):
                    self.errors.append("Your plan does not include purchase orders. Upgrade to Growth or Pro.")
                    return None, self.errors

            po_data['po_number'] = NumberGenerator.generate_po_number(self.account_id)
            po_data['invoice_type'] = 'P'

            save_purchase_order(self.user_id, self.account_id, po_data)
            return po_data, self.errors or self.warnings

        except Exception as e:
            logger.error(f"PO creation failed: {e}", exc_info=True)
            self.errors.append("System error during PO creation")
            return None, self.errors

    def get_invoice(self, invoice_number):
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT invoice_data FROM user_invoices
                    WHERE account_id = :aid AND invoice_number = :invoice_number
                """), {"aid": self.account_id, "invoice_number": invoice_number}).fetchone()
                if result:
                    return json.loads(result[0])
        except Exception as e:
            logger.error(f"Error fetching invoice: {e}")
        return None

    def get_purchase_order(self, po_number):
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT order_data FROM purchase_orders
                    WHERE account_id = :aid AND po_number = :po_number
                """), {"aid": self.account_id, "po_number": po_number}).fetchone()
                if result:
                    return json.loads(result[0])
        except Exception as e:
            logger.error(f"Error fetching PO: {e}")
        return None


    def get_invoice_by_number(self, invoice_number):
        import json
        from sqlalchemy import text
        from app.services.db import DB_ENGINE

        query = text("""
            SELECT invoice_data FROM user_invoices 
            WHERE invoice_number = :inv_num AND account_id = :aid
        """)

        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(query, {
                    "inv_num": invoice_number, 
                    "aid": self.account_id
                }).fetchone()

                if result and result[0]:
                    if isinstance(result[0], str):
                        return json.loads(result[0])
                    return result[0]
                return None
        except Exception as e:
            logger.error(f"Error fetching invoice from DB: {e}")
            return None
