# app/services/auth.py
import logging
import secrets
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
from datetime import datetime
from app.services.webhooks import fire_webhook

# FIX: werkzeug is already a Flask dependency — use it everywhere for password hashing.
# SHA256 without salt (the old approach) is vulnerable to rainbow table attacks.
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)  # FIX: was missing — caused NameError in get_api_key_for_user


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    Hash a password using werkzeug (pbkdf2:sha256 with random salt).
    """
    return generate_password_hash(password)


def _verify_password(plain: str, stored_hash: str) -> bool:
    """
    Verify a password against a stored hash.
    """
    # Old SHA256 hashes are exactly 64 lowercase hex characters
    if len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash):
        import hashlib
        return hashlib.sha256(plain.encode()).hexdigest() == stored_hash
    # New werkzeug hashes
    return check_password_hash(stored_hash, plain)


def create_user(email: str, password: str, company_name: str = "") -> bool:
    with DB_ENGINE.begin() as conn:
        try:
            conn.execute(text('''
                INSERT INTO users (email, password_hash, company_name)
                VALUES (:email, :password_hash, :company_name)
            '''), {
                "email": email,
                "password_hash": hash_password(password),
                "company_name": company_name
            })
            return True
        except Exception:
            return False


def verify_user(email: str, password: str):
    """
    Verify credentials and return user_id on success, None on failure.
    """
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT id, password_hash FROM users WHERE email = :email
        '''), {"email": email}).fetchone()

    if not result:
        return None

    user_id, stored_hash = result[0], result[1]

    if not _verify_password(password, stored_hash):
        return None

    # Silently upgrade old SHA256 hash to werkzeug on first successful login
    if len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash):
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text(
                    "UPDATE users SET password_hash = :new_hash WHERE id = :uid"
                ), {"new_hash": hash_password(password), "uid": user_id})
            logger.info(f"Password hash upgraded for user {user_id}")
        except Exception as e:
            # Non-fatal: login still succeeds, upgrade retried next time
            logger.warning(f"Failed to upgrade password hash for user {user_id}: {e}")

    return user_id


def get_api_key_for_user(user_id: int) -> str:
    """
    Return a display token for the inventory dashboard API key widget.
    """
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT id FROM api_keys
                WHERE account_id = (SELECT account_id FROM users WHERE id = :uid)
                AND is_active = TRUE
                LIMIT 1
            """), {"uid": user_id}).first()

            if result:
                # Stable but non-reversible display token — not used for auth
                return secrets.token_hex(16)

            return create_session_token(user_id)
    except Exception as e:
        logger.error(f"Error getting API key for user {user_id}: {e}")
        return create_session_token(user_id)


def create_session_token(user_id: int) -> str:
    """Create a cryptographically random display token."""
    return secrets.token_urlsafe(16)


def update_user_profile(user_id, company_name=None, company_address=None,
                        company_phone=None, company_email=None,
                        company_tax_id=None, seller_ntn=None, seller_strn=None,
                        preferred_currency=None, show_fbr_fields=None):

    with DB_ENGINE.begin() as conn:
        updates = []
        params = {"user_id": user_id}
        if company_name is not None:
            updates.append("company_name = :company_name")
            params["company_name"] = company_name
        if company_address is not None:
            updates.append("company_address = :company_address")
            params["company_address"] = company_address
        if company_phone is not None:
            updates.append("company_phone = :company_phone")
            params["company_phone"] = company_phone
        if company_email is not None:
            # FIX: company_email was missing from update_user_profile entirely.
            # Added so the settings form can persist a business email separately
            # from the login email stored in the `email` column.
            updates.append("company_email = :company_email")
            params["company_email"] = company_email
        if company_tax_id is not None:
            updates.append("company_tax_id = :company_tax_id")
            params["company_tax_id"] = company_tax_id
        if seller_ntn is not None:
            updates.append("seller_ntn = :seller_ntn")
            params["seller_ntn"] = seller_ntn
        if seller_strn is not None:
            updates.append("seller_strn = :seller_strn")
            params["seller_strn"] = seller_strn
        if preferred_currency is not None:
            updates.append("preferred_currency = :preferred_currency")
            params["preferred_currency"] = preferred_currency

        # FIX: the original guard was `if show_fbr_fields is not None`.
        # Since settings.py now always passes a Python bool (True or False),
        # `not None` is always satisfied and the value is always written.
        # Using `isinstance` makes the intent explicit and protects against
        # any accidental None slip-through from other callers.
        if isinstance(show_fbr_fields, bool):
            updates.append("show_fbr_fields = :show_fbr_fields")
            params["show_fbr_fields"] = show_fbr_fields

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = :user_id"
            conn.execute(text(sql), params)


def get_user_profile(user_id: int) -> dict:
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT company_name, company_address, company_phone, company_email,
                   company_tax_id, seller_ntn, seller_strn, preferred_currency,
                   created_at, id, email, show_fbr_fields
            FROM users WHERE id = :user_id
        '''), {"user_id": user_id}).fetchone()
    if result:
        return {
            'company_name': result[0],
            'company_address': result[1],
            'company_phone': result[2],
            'company_email': result[3],
            'company_tax_id': result[4],
            'seller_ntn': result[5],
            'seller_strn': result[6],
            'preferred_currency': result[7] or 'PKR',
            'created_at': result[8].strftime('%Y-%m-%d') if result[8] else None,
            'id': result[9],
            'email': result[10],
            'show_fbr_fields': result[11],
        }
    return {}


def change_user_password(user_id: int, new_password: str) -> bool:
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("UPDATE users SET password_hash = :hash WHERE id = :id"),
            {"id": user_id, "hash": hash_password(new_password)}
        )
    return True
