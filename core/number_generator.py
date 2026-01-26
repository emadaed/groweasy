# core/number_generator.py
import time
from sqlalchemy import text
from core.db import DB_ENGINE

class NumberGenerator:
    @staticmethod
    def generate_invoice_number(user_id):
        """Generate unique invoice number: INV-00001, INV-00002, etc."""
        return NumberGenerator._generate_number(
            user_id, 'INV-', 'user_invoices', 'invoice_number'
        )

    @staticmethod
    def generate_po_number(user_id):
        """Generate unique purchase order number: PO-00001, PO-00002, etc."""
        return NumberGenerator._generate_number(
            user_id, 'PO-', 'purchase_orders', 'po_number'
        )

    # _generate_number method:
    @staticmethod
    def _generate_number(user_id, prefix, table, column):
        """Generic number generator"""
        try:
            from core.db import DB_ENGINE
            with DB_ENGINE.begin() as conn:
                # PostgreSQL-safe query
                result = conn.execute(text(f"""
                    SELECT {column} FROM {table}
                    WHERE user_id = :user_id AND {column} LIKE :prefix
                    ORDER BY LENGTH({column}) DESC, {column} DESC
                    LIMIT 1
                """), {
                    "user_id": user_id,
                    "prefix": f"{prefix}%"
                }).fetchone()

                if result:
                    last_number = result[0]
                    try:
                        # Extract the numeric part
                        last_num = int(last_number.split('-')[1])
                        return f"{prefix}{last_num + 1:05d}"
                    except (ValueError, IndexError):
                        # If parsing fails, start from 1
                        return f"{prefix}00001"

                # No existing numbers, start from 1
                return f"{prefix}00001"

        except Exception as e:
            print(f"⚠️ Number generation error for {prefix}: {e}")
            import time
            # Fallback: timestamp-based number
            timestamp = int(time.time() % 100000)
            return f"{prefix}{timestamp:05d}"
