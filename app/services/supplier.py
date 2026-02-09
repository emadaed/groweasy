# app/services/supplier.py
import secrets  # <--- Added this critical import
import logging
from datetime import datetime
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)

class SupplierManager:
    @staticmethod
    def ensure_table_exists():
        """Ensures the suppliers table exists with professional ERP fields"""
        query = text('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                vendor_id VARCHAR(50) UNIQUE,
                name VARCHAR(255) NOT NULL,
                contact_person VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(50),
                address TEXT,
                tax_id VARCHAR(100),
                payment_terms VARCHAR(100),
                bank_details TEXT,
                total_purchased DECIMAL(15, 2) DEFAULT 0,
                order_count INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(query)
        except Exception as e:
            logger.error(f"Table Creation Error: {e}")

    @staticmethod
    def get_suppliers(user_id):
        SupplierManager.ensure_table_exists()
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, vendor_id, name, email, phone, address, 
                           tax_id, total_purchased, order_count, payment_terms
                    FROM suppliers 
                    WHERE user_id = :user_id AND is_active = TRUE 
                    ORDER BY name
                '''), {"user_id": user_id})
                
                # Convert rows to dictionaries properly
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Error fetching suppliers: {e}")
            return []

    @staticmethod
    def add_supplier(user_id, data):
        SupplierManager.ensure_table_exists()
        
        # Professional Vendor ID generation: VEN-2602-A1B2
        vendor_id = data.get('vendor_id') or f"VEN-{datetime.now().strftime('%y%m')}-{secrets.token_hex(2).upper()}"
        
        query = text('''
            INSERT INTO suppliers (
                user_id, vendor_id, name, contact_person, email, phone, 
                address, tax_id, payment_terms, bank_details
            ) VALUES (
                :user_id, :vendor_id, :name, :contact_person, :email, :phone, 
                :address, :tax_id, :payment_terms, :bank_details
            ) RETURNING id
        ''')
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(query, {
                    "user_id": user_id,
                    "vendor_id": vendor_id,
                    "name": data['name'],
                    "contact_person": data.get('contact_person') or '',
                    "email": data.get('email') or '',
                    "phone": data.get('phone') or '',
                    "address": data.get('address') or '',
                    "tax_id": data.get('tax_id') or '',
                    "payment_terms": data.get('payment_terms') or 'Due on Receipt',
                    "bank_details": data.get('bank_details') or ''
                })
                row = result.fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.error(f"Error adding supplier: {e}")
            return None
