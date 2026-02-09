# app/services/supplier.py
from app.services.db import DB_ENGINE
from sqlalchemy import text
import logging
import secrets 
from datetime import datetime

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
                tax_id VARCHAR(100), -- NTN/STRN
                payment_terms VARCHAR(100), -- e.g., Net 30, Due on Receipt
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
        SupplierManager.ensure_table_exists() # Safety check
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text('''
                    SELECT id, vendor_id, name, email, phone, address, 
                           tax_id, total_purchased, order_count, payment_terms
                    FROM suppliers 
                    WHERE user_id = :user_id AND is_active = TRUE 
                    ORDER BY name
                '''), {"user_id": user_id})
                
                return [dict(row._mapping) for row in result]
        except Exception as e:
            logger.error(f"Error fetching suppliers: {e}")
            return []

    @staticmethod
    def add_supplier(user_id, data):
        SupplierManager.ensure_table_exists()
        # Generate a Vendor ID if not provided (SAP style)
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
                    "contact_person": data.get('contact_person'),
                    "email": data.get('email'),
                    "phone": data.get('phone'),
                    "address": data.get('address'),
                    "tax_id": data.get('tax_id'),
                    "payment_terms": data.get('payment_terms'),
                    "bank_details": data.get('bank_details')
                })
                return result.fetchone()[0]
        except Exception as e:
            logger.error(f"Error adding supplier: {e}")
            return None
