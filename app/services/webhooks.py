# app/services/webhooks.py
"""
Webhook delivery service.

Fixes applied:
1. CRASH BUG: update_webhook was writing to an `updated_at` column that does
   not exist in the webhooks table (confirmed from \d webhooks schema output).
   Every call to update_webhook crashed with a column-not-found error.
   Fixed by removing the nonexistent column reference.

2. BLOCKING: fire_webhook made synchronous HTTP requests in the main request
   thread.  With a 5s timeout per webhook, creating an invoice with 3 active
   webhooks could block the response for up to 15 seconds.  Fixed by firing
   each webhook in a daemon thread — the invoice response returns immediately
   and deliveries happen in the background.  This matches the pattern already
   used by send_welcome_email_async and send_invite_email_async.
"""
import threading
import logging
from sqlalchemy import text
from app.services.db import DB_ENGINE
import requests

logger = logging.getLogger(__name__)


def create_webhook(account_id: int, url: str, events: list) -> int:
    """Store a new webhook and return its id."""
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO webhooks (account_id, url, events)
            VALUES (:aid, :url, :events)
            RETURNING id
        """), {"aid": account_id, "url": url, "events": events})
        return result.scalar()


def get_webhooks(account_id: int) -> list:
    """List all active webhooks for an account."""
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, url, events, is_active, created_at
            FROM webhooks
            WHERE account_id = :aid AND is_active = TRUE
            ORDER BY created_at DESC
        """), {"aid": account_id}).fetchall()
    return [dict(row._mapping) for row in rows]


def update_webhook(account_id: int, webhook_id: int, url: str, events: list) -> None:
    """
    Update an existing webhook's URL and events.

    FIX: Original query included `updated_at = NOW()` but the webhooks table
    has no updated_at column (confirmed via \\d webhooks).  That line caused
    every update call to raise ProgrammingError: column "updated_at" does not
    exist.  Removed.
    """
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE webhooks
            SET url = :url, events = :events
            WHERE id = :id AND account_id = :aid
        """), {"id": webhook_id, "aid": account_id, "url": url, "events": events})


def delete_webhook(account_id: int, webhook_id: int) -> None:
    """Soft-delete a webhook (set is_active = FALSE)."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE webhooks SET is_active = FALSE
            WHERE id = :id AND account_id = :aid
        """), {"id": webhook_id, "aid": account_id})


def _deliver_webhook(webhook_id: int, url: str, event: str, payload: dict) -> None:
    """
    Internal: send a single webhook POST request.
    Always runs in a background thread — never call directly from request handlers.
    """
    try:
        response = requests.post(
            url,
            json={"event": event, "data": payload},
            timeout=10,
            headers={"Content-Type": "application/json", "X-GrowEasy-Event": event}
        )
        response.raise_for_status()
        logger.info(f"Webhook {webhook_id} delivered: {event} → {url} ({response.status_code})")
    except requests.exceptions.Timeout:
        logger.warning(f"Webhook {webhook_id} timed out: {url}")
    except requests.exceptions.ConnectionError:
        logger.warning(f"Webhook {webhook_id} connection failed: {url}")
    except Exception as e:
        logger.error(f"Webhook {webhook_id} failed: {e}")


def fire_webhook(account_id: int, event: str, payload: dict) -> None:
    """
    Dispatch a webhook event to all matching active webhooks for the account.

    FIX: Was synchronous — blocked the main request thread for up to
    5s × number_of_webhooks on every invoice or product save.
    Now fires each delivery in a daemon thread so the caller returns
    immediately.  This matches the pattern used by Flask-Mail async sends.

    Thread is daemon=True so it won't prevent process shutdown.
    """
    try:
        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, url FROM webhooks
                WHERE account_id = :aid
                  AND is_active = TRUE
                  AND :event = ANY(events)
            """), {"aid": account_id, "event": event}).fetchall()
    except Exception as e:
        logger.error(f"Failed to fetch webhooks for account {account_id}: {e}")
        return

    for webhook_id, url in rows:
        t = threading.Thread(
            target=_deliver_webhook,
            args=(webhook_id, url, event, payload),
            daemon=True,
            name=f"webhook-{webhook_id}-{event}"
        )
        t.start()
