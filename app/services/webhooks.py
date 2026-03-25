# app/services/webhooks.py
from sqlalchemy import text
from app.services.db import DB_ENGINE
import requests
import logging

logger = logging.getLogger(__name__)

def create_webhook(account_id, url, events):
    """Store a new webhook for the account."""
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO webhooks (account_id, url, events)
            VALUES (:aid, :url, :events)
            RETURNING id
        """), {"aid": account_id, "url": url, "events": events})
        return result.scalar()

def get_webhooks(account_id):
    """List all active webhooks for an account."""
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, url, events, is_active, created_at
            FROM webhooks
            WHERE account_id = :aid AND is_active = TRUE
            ORDER BY created_at DESC
        """), {"aid": account_id}).fetchall()
    return [dict(row._mapping) for row in rows]

def update_webhook(account_id, webhook_id, url, events):
    """Update an existing webhook."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE webhooks
            SET url = :url, events = :events, updated_at = NOW()
            WHERE id = :id AND account_id = :aid
        """), {"id": webhook_id, "aid": account_id, "url": url, "events": events})

def delete_webhook(account_id, webhook_id):
    """Soft delete a webhook (set is_active = FALSE)."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE webhooks SET is_active = FALSE WHERE id = :id AND account_id = :aid
        """), {"id": webhook_id, "aid": account_id})

def fire_webhook(account_id, event, payload):
    """
    Send a POST request to all active webhooks of the account that listen to the given event.
    Logs errors but does not block the main process.
    """
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, url FROM webhooks
            WHERE account_id = :aid AND is_active = TRUE AND :event = ANY(events)
        """), {"aid": account_id, "event": event}).fetchall()

    for webhook_id, url in rows:
        try:
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Webhook {webhook_id} failed: {e}")
