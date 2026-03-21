# app/services/number_generator.py
import time
from sqlalchemy import text
from app.services.db import DB_ENGINE

class NumberGenerator:
    @staticmethod
    def generate_invoice_number(account_id):
        return NumberGenerator._generate_number(account_id, 'INV-', 'user_invoices', 'invoice_number')

    @staticmethod
    def generate_po_number(account_id):
        return NumberGenerator._generate_number(account_id, 'PO-', 'purchase_orders', 'po_number')

    @staticmethod
    def _generate_number(account_id, prefix, table, column):
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text(f"""
                    SELECT {column} FROM {table}
                    WHERE account_id = :aid AND {column} LIKE :prefix
                    ORDER BY LENGTH({column}) DESC, {column} DESC
                    LIMIT 1
                """), {
                    "aid": account_id,
                    "prefix": f"{prefix}%"
                }).fetchone()

                if result:
                    last_number = result[0]
                    try:
                        last_num = int(last_number.split('-')[1])
                        return f"{prefix}{last_num + 1:05d}"
                    except (ValueError, IndexError):
                        return f"{prefix}00001"

                return f"{prefix}00001"

        except Exception as e:
            print(f"⚠️ Number generation error for {prefix}: {e}")
            timestamp = int(time.time() % 100000)
            return f"{prefix}{timestamp:05d}"
