# app/services/suppliers.py
"""
Supplier management service.

Schema history:
- Original table (db.py): id, user_id, name, email, phone, address, tax_id,
  total_purchased, order_count, created_at, updated_at
- Extended columns added later via ensure_table_exists():
  account_id, vendor_id, contact_person, payment_terms, bank_details, status

The _migrate_columns() function below adds any missing columns at startup so
the code and the live schema stay in sync automatically.
"""
import secrets
import logging
from datetime import datetime
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)

# Columns guaranteed to exist after _migrate_columns() runs at startup.
# If you add a new column to the table, add it here AND in _migrate_columns().
_SUPPLIER_COLUMNS = """
    id, user_id, account_id, vendor_id, name, contact_person,
    email, phone, address, tax_id, payment_terms, bank_details,
    total_purchased, order_count, status, created_at
"""

_migrated = False  # module-level flag so migration only runs once per process


def _migrate_columns() -> None:
    """
    Add any missing columns to the suppliers table.
    Uses ADD COLUMN IF NOT EXISTS so it is fully idempotent.
    Runs once per worker process on first use.
    """
    global _migrated
    if _migrated:
        return
    try:
        with DB_ENGINE.begin() as conn:
            migrations = [
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS account_id INTEGER",
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS vendor_id VARCHAR(50)",
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS contact_person VARCHAR(255)",
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS payment_terms VARCHAR(100)",
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS bank_details TEXT",
                "ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'Active'",
            ]
            for sql in migrations:
                conn.execute(text(sql))
        _migrated = True
        logger.info("Suppliers schema migration complete")
    except Exception as e:
        logger.warning(f"Suppliers migration warning: {e}")


class SupplierManager:

    @staticmethod
    def ensure_table_exists() -> None:
        """
        Create the suppliers table if it does not exist and migrate any
        missing columns.  Safe to call multiple times.
        """
        _migrate_columns()
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text('''
                    CREATE TABLE IF NOT EXISTS suppliers (
                        id               SERIAL PRIMARY KEY,
                        user_id          INTEGER NOT NULL,
                        account_id       INTEGER NOT NULL,
                        name             VARCHAR(255) NOT NULL,
                        vendor_id        VARCHAR(50),
                        contact_person   VARCHAR(255),
                        email            VARCHAR(255),
                        phone            VARCHAR(50),
                        address          TEXT,
                        tax_id           VARCHAR(100),
                        payment_terms    VARCHAR(100),
                        bank_details     TEXT,
                        total_purchased  DECIMAL(15, 2) DEFAULT 0,
                        order_count      INTEGER DEFAULT 0,
                        status           VARCHAR(20) DEFAULT 'Active',
                        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                '''))
        except Exception as e:
            logger.error(f"Suppliers table creation error: {e}")

    @staticmethod
    def get_suppliers(account_id: int) -> list:
        """Return all suppliers for the account, ordered by name."""
        _migrate_columns()
        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT {_SUPPLIER_COLUMNS}
                FROM suppliers
                WHERE account_id = :aid
                ORDER BY name ASC
            """), {"aid": account_id}).fetchall()
        return [dict(row._mapping) for row in rows]

    @staticmethod
    def add_supplier(user_id: int, account_id: int, data: dict):
        """
        Insert a new supplier atomically.
        Returns True on success, None if name already exists for this account.
        """
        _migrate_columns()
        vendor_id = (
            data.get('vendor_id')
            or f"VEN-{datetime.now().strftime('%y%m')}-{secrets.token_hex(2).upper()}"
        )
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    INSERT INTO suppliers
                        (user_id, account_id, vendor_id, name, contact_person,
                         email, phone, address, tax_id, payment_terms, bank_details)
                    VALUES
                        (:user_id, :aid, :vendor_id, :name, :contact_person,
                         :email, :phone, :address, :tax_id, :payment_terms, :bank_details)
                    ON CONFLICT (account_id, name) DO NOTHING
                    RETURNING id
                """), {
                    "user_id": user_id, "aid": account_id, "vendor_id": vendor_id,
                    "name": data.get('name'), "contact_person": data.get('contact_person'),
                    "email": data.get('email'), "phone": data.get('phone'),
                    "address": data.get('address'), "tax_id": data.get('tax_id'),
                    "payment_terms": data.get('payment_terms'),
                    "bank_details": data.get('bank_details'),
                })
                row = result.fetchone()
                if row is None:
                    logger.info(f"Supplier '{data.get('name')}' already exists for account {account_id}")
                    return None
                return True
        except Exception as e:
            logger.error(f"Error adding supplier: {e}", exc_info=True)
            return None

    @staticmethod
    def update_supplier(account_id: int, supplier_id: int, data: dict) -> bool:
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    UPDATE suppliers
                    SET name=:name, contact_person=:contact_person, email=:email,
                        phone=:phone, address=:address, tax_id=:tax_id,
                        payment_terms=:payment_terms, bank_details=:bank_details
                    WHERE id=:id AND account_id=:aid
                """), {
                    "name": data.get('name'), "contact_person": data.get('contact_person'),
                    "email": data.get('email'), "phone": data.get('phone'),
                    "address": data.get('address'), "tax_id": data.get('tax_id'),
                    "payment_terms": data.get('payment_terms'),
                    "bank_details": data.get('bank_details'),
                    "id": supplier_id, "aid": account_id,
                })
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating supplier {supplier_id}: {e}")
            return False

    @staticmethod
    def delete_supplier(account_id: int, supplier_id: int) -> bool:
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text(
                    "DELETE FROM suppliers WHERE id=:id AND account_id=:aid"
                ), {"id": supplier_id, "aid": account_id})
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting supplier {supplier_id}: {e}")
            return False

    @staticmethod
    def update_volume(account_id: int, supplier_id, amount: float) -> None:
        if not supplier_id:
            return
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    UPDATE suppliers
                    SET total_purchased = total_purchased + :amount,
                        order_count = order_count + 1
                    WHERE id=:id AND account_id=:aid
                """), {"amount": amount, "id": int(supplier_id), "aid": account_id})
        except Exception as e:
            logger.error(f"Error updating supplier volume: {e}")
