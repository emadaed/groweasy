# app/services/number_generator.py
"""
Atomic document number generation.

The original implementation had two critical bugs:
1. SQL INJECTION: table and column names were passed directly into an f-string SQL
   query.  Any caller that controlled those values could execute arbitrary SQL.
2. RACE CONDITION: SELECT-max-then-increment is not atomic.  Under concurrent load
   (two users saving invoices at the same time) both transactions could read the
   same last number and generate duplicate invoice numbers.

Fix: use a dedicated `document_sequences` table with a PostgreSQL
INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING pattern.
This is a single atomic operation — no SELECT, no gap, no duplicate.

Required migration (run once on your Railway DB):
    CREATE TABLE IF NOT EXISTS document_sequences (
        account_id  INTEGER      NOT NULL,
        doc_type    TEXT         NOT NULL,
        last_value  INTEGER      NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, doc_type)
    );
"""

import logging
import time
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)

# Allowlist for document types — eliminates the SQL injection surface entirely.
# Add new types here when needed; they map to a prefix and are stored in
# document_sequences.doc_type.
_DOC_TYPES = {
    'invoice': 'INV-',
    'po':      'PO-',
}


class NumberGenerator:

    @staticmethod
    def generate_invoice_number(account_id: int) -> str:
        return NumberGenerator._next('invoice', account_id)

    @staticmethod
    def generate_po_number(account_id: int) -> str:
        return NumberGenerator._next('po', account_id)

    @staticmethod
    def _next(doc_type: str, account_id: int) -> str:
        """
        Atomically increment and return the next number for this account + doc_type.

        Uses PostgreSQL's INSERT ... ON CONFLICT DO UPDATE which is guaranteed
        atomic — no two concurrent transactions can read the same value.
        """
        if doc_type not in _DOC_TYPES:
            raise ValueError(f"Unknown doc_type '{doc_type}'. Allowed: {list(_DOC_TYPES)}")

        prefix = _DOC_TYPES[doc_type]

        try:
            with DB_ENGINE.begin() as conn:
                # Ensure the sequences table exists (idempotent)
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS document_sequences (
                        account_id  INTEGER NOT NULL,
                        doc_type    TEXT    NOT NULL,
                        last_value  INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (account_id, doc_type)
                    )
                """))

                # Atomic upsert: insert with value=1 on first use, increment on conflict.
                result = conn.execute(text("""
                    INSERT INTO document_sequences (account_id, doc_type, last_value)
                    VALUES (:aid, :doc_type, 1)
                    ON CONFLICT (account_id, doc_type)
                    DO UPDATE SET last_value = document_sequences.last_value + 1
                    RETURNING last_value
                """), {"aid": account_id, "doc_type": doc_type})

                new_value = result.scalar()

            return f"{prefix}{new_value:05d}"

        except Exception as e:
            # Fallback: timestamp-based to avoid a crash, but log loudly
            # because duplicates are possible in the fallback path.
            logger.error(
                f"NumberGenerator failed for {doc_type} account={account_id}: {e}",
                exc_info=True
            )
            fallback = f"{prefix}{int(time.time() % 100000):05d}"
            logger.warning(f"Using fallback number {fallback} — check DB urgently")
            return fallback
