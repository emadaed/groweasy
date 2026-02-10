# app/services/supplier.py
import secrets
import logging
from datetime import datetime
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)

class SupplierManager:
    @staticmethod
    def ensure_table_exists():
        """Ensures the table exists and has all professional ERP columns"""
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE IF NOT EXISTS suppliers (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        name VARCHAR(255) NOT NULL,
                        vendor_id VARCHAR(50) UNIQUE,
                        contact_person VARCHAR(255),
                        email VARCHAR(255),
                        phone VARCHAR(50),
                        address TEXT,
                        tax_id VARCHAR(100),
                        payment_terms VARCHAR(100),
                        bank_details TEXT,
                        total_purchased DECIMAL(15, 2) DEFAULT 0,
                        order_count INTEGER DEFAULT 0,
                        status VARCHAR(20) DEFAULT 'Active',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                '''))
        except Exception as e:
            logger.error(f"Migration error: {e}")

    @staticmethod
    def add_supplier(user_id, data):
        SupplierManager.ensure_table_exists()
        
        # Check for duplicate name
        with DB_ENGINE.connect() as conn:
            dup = conn.execute(text("SELECT id FROM suppliers WHERE user_id=:u AND name=:n"), 
                              {"u": user_id, "n": data['name']}).fetchone()
            if dup: return None # Indicate duplicate

        vendor_id = data.get('vendor_id') or f"VEN-{datetime.now().strftime('%y%m')}-{secrets.token_hex(2).upper()}"
        
        query = text('''
            INSERT INTO suppliers (user_id, vendor_id, name, contact_person, email, phone, address, tax_id, payment_terms, bank_details)
            VALUES (:user_id, :vendor_id, :name, :contact_person, :email, :phone, :address, :tax_id, :payment_terms, :bank_details)
        ''')
        with DB_ENGINE.begin() as conn:
            conn.execute(query, {**data, "user_id": user_id, "vendor_id": vendor_id})
            return True

    @staticmethod
    def update_supplier(user_id, supplier_id, data):
        query = text('''
            UPDATE suppliers SET name=:name, contact_person=:contact_person, email=:email, 
            phone=:phone, address=:address, tax_id=:tax_id, payment_terms=:payment_terms, bank_details=:bank_details
            WHERE id=:id AND user_id=:user_id
        ''')
        with DB_ENGINE.begin() as conn:
            conn.execute(query, {**data, "id": supplier_id, "user_id": user_id})
            return True

    @staticmethod
    def delete_supplier(user_id, supplier_id):
        with DB_ENGINE.begin() as conn:
            conn.execute(text("DELETE FROM suppliers WHERE id=:id AND user_id=:user_id"), 
                        {"id": supplier_id, "user_id": user_id})
            return True

    @staticmethod
    def update_volume(user_id, supplier_id, amount):
        """Updates Total Volume and Order Count after a successful PO"""
        query = text('''
            UPDATE suppliers 
            SET total_purchased = total_purchased + :amount,
                order_count = order_count + 1
            WHERE id = :id AND user_id = :user_id
        ''')
        with DB_ENGINE.begin() as conn:
            conn.execute(query, {"amount": amount, "id": supplier_id, "user_id": user_id})

    @staticmethod
    def get_suppliers(user_id):
        SupplierManager.ensure_table_exists()
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("SELECT * FROM suppliers WHERE user_id = :u ORDER BY name ASC"), {"u": user_id})
            return [dict(row._mapping) for row in result]
