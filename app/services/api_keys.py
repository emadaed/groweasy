# app/services/api_keys.py
import secrets
import hashlib
import logging
from sqlalchemy import text
from app.services.db import DB_ENGINE
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def hash_key(key: str) -> str:
    """Hash a raw API key (SHA-256) for storage. Keys are long random values so no salt needed."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new cryptographically random API key."""
    return secrets.token_urlsafe(32)


def create_api_key(account_id: int, name: str) -> str:
    """Create and store a new API key. Returns the raw key (shown once)."""
    raw_key = generate_api_key()
    key_hash = hash_key(raw_key)
    expires_at = datetime.now() + timedelta(days=365)
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            INSERT INTO api_keys (account_id, name, key_hash, expires_at)
            VALUES (:account_id, :name, :key_hash, :expires_at)
        """), {"account_id": account_id, "name": name,
               "key_hash": key_hash, "expires_at": expires_at})
    return raw_key


def get_api_keys(account_id: int) -> list:
    """List all active API keys for an account (hashes never returned)."""
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, name, created_at, last_used_at, expires_at, is_active
            FROM api_keys
            WHERE account_id = :account_id AND is_active = TRUE
            ORDER BY created_at DESC
        """), {"account_id": account_id}).fetchall()
    return [dict(row._mapping) for row in rows]


def revoke_api_key(account_id: int, key_id: int) -> None:
    """Soft-delete an API key (set is_active = FALSE)."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE api_keys SET is_active = FALSE
            WHERE id = :key_id AND account_id = :account_id
        """), {"key_id": key_id, "account_id": account_id})


def validate_api_key(raw_key: str):
    """
    Validate a raw API key and return (account_id, None) on success
    or (None, error_message) on failure.

    FIX: The original code used DB_ENGINE.connect() (read-only) and then
    attempted an UPDATE inside that context.  The UPDATE was never committed,
    so last_used_at was silently ignored on every single API call.
    Fixed by splitting into two explicit transactions:
      1. A read-only SELECT to validate.
      2. A separate DB_ENGINE.begin() to commit the last_used_at UPDATE.
    """
    key_hash = hash_key(raw_key)

    # Step 1: validate (read-only)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, account_id, is_active, expires_at
            FROM api_keys
            WHERE key_hash = :key_hash
        """), {"key_hash": key_hash}).first()

    if not row:
        return None, "Invalid API key"
    if not row.is_active:
        return None, "API key revoked"
    if row.expires_at and row.expires_at < datetime.now():
        return None, "API key expired"

    # Step 2: update last_used_at (write transaction — actually committed)
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE api_keys SET last_used_at = NOW()
                WHERE key_hash = :key_hash
            """), {"key_hash": key_hash})
    except Exception as e:
        # Non-fatal: the key is valid even if we can't update the timestamp
        logger.warning(f"Failed to update last_used_at for API key: {e}")

    return row.account_id, None
