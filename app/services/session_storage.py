# app/services/session_storage.py
"""
Database-backed storage for large session payloads (PO data, invoice previews).

Fixes applied:
- print() statements replaced with logger
- Added cleanup_expired() to purge stale rows — call from a scheduled job
  or on login (like session_manager.cleanup_expired_sessions)
"""
import time
import json
import logging
from datetime import datetime
from app.services.db import DB_ENGINE
from sqlalchemy import text

logger = logging.getLogger(__name__)


class SessionStorage:

    @staticmethod
    def store_large_data(user_id: int, data_type: str, data: dict) -> str:
        """
        Persist large data to the session_storage table.
        Returns the session_key needed to retrieve it.
        """
        session_key = f"{data_type}_{int(time.time())}"
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    INSERT INTO session_storage
                        (user_id, session_key, data_type, data, expires_at)
                    VALUES
                        (:user_id, :session_key, :data_type, :data,
                         NOW() + INTERVAL '24 hours')
                """), {
                    "user_id": user_id,
                    "session_key": session_key,
                    "data_type": data_type,
                    "data": json.dumps(data),
                })
            return session_key
        except Exception as e:
            logger.error(f"SessionStorage.store_large_data failed: {e}", exc_info=True)
            # Return key anyway — caller may store it in Flask session; retrieval
            # will gracefully return None if the DB insert failed.
            return session_key

    @staticmethod
    def get_data(user_id: int, session_key: str):
        """
        Retrieve stored data by key.  Returns None if not found or expired.
        """
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT data FROM session_storage
                    WHERE user_id = :user_id
                      AND session_key = :session_key
                      AND expires_at > NOW()
                """), {
                    "user_id": user_id,
                    "session_key": session_key,
                }).fetchone()

                if result:
                    return json.loads(result[0])
        except Exception as e:
            logger.error(f"SessionStorage.get_data failed: {e}", exc_info=True)

        return None

    @staticmethod
    def clear_data(user_id: int, data_type: str) -> None:
        """
        Delete rows for this user+data_type OR any expired rows for this user.
        Parentheses around the OR are intentional and correct.
        """
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    DELETE FROM session_storage
                    WHERE user_id = :user_id
                      AND (data_type = :data_type OR expires_at <= NOW())
                """), {
                    "user_id": user_id,
                    "data_type": data_type,
                })
        except Exception as e:
            logger.error(f"SessionStorage.clear_data failed: {e}")

    @staticmethod
    def cleanup_expired() -> int:
        """
        Hard-delete all expired rows across all users.

        Call this from a scheduled job or on each login (it runs in
        milliseconds for typical table sizes).  Without this, the table
        grows indefinitely.

        Returns the number of rows deleted.
        """
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text("""
                    DELETE FROM session_storage WHERE expires_at <= NOW()
                """))
                deleted = result.rowcount
            if deleted:
                logger.info(f"SessionStorage: purged {deleted} expired row(s)")
            return deleted
        except Exception as e:
            logger.error(f"SessionStorage.cleanup_expired failed: {e}")
            return 0
