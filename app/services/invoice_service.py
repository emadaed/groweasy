# core/invoice_service.py - FINAL PROFESSIONAL VERSION

import logging
from app.services.db import DB_ENGINE
from app.services.number_generator import NumberGenerator
from app.services.auth import save_user_invoice
from app.services.purchases import save_purchase_order
from app.services.inventory import InventoryManager
from app.services.invoice_logic import prepare_invoice_data
from app.services.invoice_logic_po import prepare_po_data

logger = logging.getLogger(__name__)

class InvoiceService:
    def __init__(self, user_id):
        self.user_id = user_id
        self.errors = []
        self.warnings = []

    def create_invoice(self, form_data, files=None):
        try:
            invoice_data = prepare_invoice_data(form_data, files=files)

            # Generate number
            invoice_data['invoice_number'] = NumberGenerator.generate_invoice_number(self.user_id)

            # Save
            save_user_invoice(self.user_id, invoice_data)

            # Update stock - decrease for sales
            movement_type = 'sale'
            quantity_multiplier = -1

            for item in invoice_data.get('items', []):
                if item.get('product_id'):
                    success = InventoryManager.update_stock_delta(
                        self.user_id,
                        item['product_id'],
                        quantity_multiplier * item['qty'],
                        movement_type,
                        invoice_data['invoice_number'],
                        f"Sale via invoice {invoice_data['invoice_number']}"
                    )
                    if not success:
                        self.warnings.append(f"Stock update failed for {item['name']}")

            return invoice_data, self.errors or self.warnings

        except Exception as e:
            logger.error(f"Invoice creation failed: {e}", exc_info=True)
            self.errors.append("System error during invoice creation")
            return None, self.errors

    def create_purchase_order(self, form_data, files=None):
        try:
            po_data = prepare_po_data(form_data, files=files)

            po_data['po_number'] = NumberGenerator.generate_po_number(self.user_id)
            po_data['invoice_type'] = 'P'

            save_purchase_order(self.user_id, po_data)
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
                    WHERE user_id = :user_id AND invoice_number = :invoice_number
                """), {"user_id": self.user_id, "invoice_number": invoice_number}).fetchone()
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
                    WHERE user_id = :user_id AND po_number = :po_number
                """), {"user_id": self.user_id, "po_number": po_number}).fetchone()
                if result:
                    return json.loads(result[0])
        except Exception as e:
            logger.error(f"Error fetching PO: {e}")
        return None


    def get_invoice_by_number(self, invoice_number):
        """Fetches a saved invoice and its items from the database"""
        from sqlalchemy import text
        from app.services.db import DB_ENGINE
        
        query = text("""
            SELECT * FROM invoices 
            WHERE invoice_number = :inv_num AND user_id = :user_id
        """)
        
        items_query = text("""
            SELECT * FROM invoice_items 
            WHERE invoice_number = :inv_num
        """)
        
        with DB_ENGINE.connect() as conn:
            inv = conn.execute(query, {"inv_num": invoice_number, "user_id": self.user_id}).fetchone()
            if not inv:
                return None
            
            # Convert row to dictionary
            invoice_data = dict(inv._mapping)
            
            # Fetch items
            items = conn.execute(items_query, {"inv_num": invoice_number}).fetchall()
            invoice_data['items'] = [dict(item._mapping) for item in items]
            
            return invoice_data
