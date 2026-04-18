# app/services/suppliers.py
"""
Supplier management service.

Fixes applied:
1. ensure_table_exists() was called on every get_suppliers() and add_supplier()
   call.  Each call executes a DDL CREATE TABLE IF NOT EXISTS which takes a
   schema lock and adds latency.  The suppliers table is confirmed present in
   the Railway schema, so these calls are pure overhead.  Removed from hot
   paths.  The method is kept but only called from an explicit migration step.

2. add_supplier had a race condition: it checked for duplicates with
   DB_ENGINE.connect() (one connection/transaction) and then inserted with
   DB_ENGINE.begin() (a second, separate transaction).  Between those two
   operations another request could insert the same supplier.  Fixed with a
   single atomic INSERT ... ON CONFLICT DO NOTHING transaction.

3. SELECT * replaced with explicit column list — avoids silent breakage when
   columns are added/removed and makes it clear what data is returned.
"""
import secrets
import logging
from datetime import datetime
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)

# Explicit column list — avoids SELECT * and documents the contract
_SUPPLIER_COLUMNS = """
    id, user_id, account_id, vendor_id, name, contact_person,
    email, phone, address, tax_id, payment_terms, bank_details,
    total_purchased, order_count, status, created_at
"""


class SupplierManager:

    @staticmethod
    def ensure_table_exists() -> None:
        """
        Create the suppliers table if it does not exist.
        DO NOT call this from get_suppliers() or add_supplier() — it runs DDL
        on every request.  Call once at startup or from a migration script.
        The table is confirmed present in production (see Railway schema).
        """
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
                        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (account_id, name)
                    )
                '''))
        except Exception as e:
            logger.error(f"suppliers table migration error: {e}")

    @staticmethod
    def get_suppliers(account_id: int) -> list:
        """Return all suppliers for the account, ordered by name."""
        # FIX: ensure_table_exists() removed — was running DDL on every page load
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
        Insert a new supplier.  Returns True on success, None if a supplier
        with the same name already exists for this account.

        FIX: Original code did a SELECT for duplicate check in one connection
        and then INSERT in a second connection — a classic TOCTOU race.
        Now uses a single INSERT ... ON CONFLICT DO NOTHING so the uniqueness
        check and insert are atomic.
        """
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
                    "user_id": user_id,
                    "aid": account_id,
                    "vendor_id": vendor_id,
                    "name": data.get('name'),
                    "contact_person": data.get('contact_person'),
                    "email": data.get('email'),
                    "phone": data.get('phone'),
                    "address": data.get('address'),
                    "tax_id": data.get('tax_id'),
                    "payment_terms": data.get('payment_terms'),
                    "bank_details": data.get('bank_details'),
                })
                row = result.fetchone()
                if row is None:
                    # ON CONFLICT fired — duplicate name
                    logger.info(f"Supplier '{data.get('name')}' already exists for account {account_id}")
                    return None
                return True
        except Exception as e:
            logger.error(f"Error adding supplier: {e}", exc_info=True)
            return None

    @staticmethod
    def update_supplier(account_id: int, supplier_id: int, data: dict) -> bool:
        """Update mutable supplier fields. Returns True if a row was updated."""
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    UPDATE suppliers
                    SET name             = :name,
                        contact_person   = :contact_person,
                        email            = :email,
                        phone            = :phone,
                        address          = :address,
                        tax_id           = :tax_id,
                        payment_terms    = :payment_terms,
                        bank_details     = :bank_details
                    WHERE id = :id AND account_id = :aid
                """), {
                    "name": data.get('name'),
                    "contact_person": data.get('contact_person'),
                    "email": data.get('email'),
                    "phone": data.get('phone'),
                    "address": data.get('address'),
                    "tax_id": data.get('tax_id'),
                    "payment_terms": data.get('payment_terms'),
                    "bank_details": data.get('bank_details'),
                    "id": supplier_id,
                    "aid": account_id,
                })
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating supplier {supplier_id}: {e}")
            return False

    @staticmethod
    def delete_supplier(account_id: int, supplier_id: int) -> bool:
        """Hard-delete a supplier. Returns True if deleted."""
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    DELETE FROM suppliers WHERE id = :id AND account_id = :aid
                """), {"id": supplier_id, "aid": account_id})
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting supplier {supplier_id}: {e}")
            return False

    @staticmethod
    def update_volume(account_id: int, supplier_id, amount: float) -> None:
        """Increment total_purchased and order_count for a supplier."""
        if not supplier_id:
            return
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    UPDATE suppliers
                    SET total_purchased = total_purchased + :amount,
                        order_count     = order_count + 1
                    WHERE id = :id AND account_id = :aid
                """), {"amount": amount, "id": int(supplier_id), "aid": account_id})
        except Exception as e:
            logger.error(f"Error updating supplier volume: {e}")
