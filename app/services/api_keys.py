# app/services/api_keys.py
import secrets
import hashlib
from sqlalchemy import text
from app.services.db import DB_ENGINE
from datetime import datetime, timedelta

def hash_key(key):
    """Hash a raw API key (sha256) for storage."""
    return hashlib.sha256(key.encode()).hexdigest()

def generate_api_key():
    """Generate a new raw API key."""
    return secrets.token_urlsafe(32)

def create_api_key(account_id, name):
    raw_key = generate_api_key()
    key_hash = hash_key(raw_key)
    expires_at = datetime.now() + timedelta(days=365)  # 1 year
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            INSERT INTO api_keys (account_id, name, key_hash, expires_at)
            VALUES (:account_id, :name, :key_hash, :expires_at)
        """), {"account_id": account_id, "name": name, "key_hash": key_hash, "expires_at": expires_at})
    return raw_key

def get_api_keys(account_id):
    """List all active API keys for an account."""
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, name, created_at, last_used_at, is_active
            FROM api_keys
            WHERE account_id = :account_id AND is_active = TRUE
            ORDER BY created_at DESC
        """), {"account_id": account_id}).fetchall()
    return [dict(row._mapping) for row in rows]

def revoke_api_key(account_id, key_id):
    """Soft delete an API key (set is_active = FALSE)."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE api_keys SET is_active = FALSE
            WHERE id = :key_id AND account_id = :account_id
        """), {"key_id": key_id, "account_id": account_id})

from datetime import datetime

def validate_api_key(raw_key):
    key_hash = hash_key(raw_key)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT account_id, is_active, expires_at FROM api_keys
            WHERE key_hash = :key_hash
        """), {"key_hash": key_hash}).first()
        if not row:
            return None, "Invalid API key"
        if not row.is_active:
            return None, "API key revoked"
        if row.expires_at and row.expires_at < datetime.now():
            return None, "API key expired"
        # Update last_used_at
        conn.execute(text("""
            UPDATE api_keys SET last_used_at = NOW()
            WHERE key_hash = :key_hash
        """), {"key_hash": key_hash})
        return row.account_id, None
    
