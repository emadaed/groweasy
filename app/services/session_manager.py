# app/services/session_manager.py
"""
Session management for GrowEasy.
"""
import secrets
import logging
from datetime import datetime, timedelta
from app.services.db import DB_ENGINE
from sqlalchemy import text

logger = logging.getLogger(__name__)


class SessionManager:

    @staticmethod
    def create_session(user_id: int, request) -> str:
        """Create new session record and return the session token."""
        session_token = secrets.token_urlsafe(32)
        user_agent = request.headers.get('User-Agent', 'Unknown')
        ip_address = request.remote_addr

        device_type = 'mobile' if 'Mobile' in user_agent else 'desktop'
        device_name = user_agent[:50] if user_agent else 'Unknown Device'
        location = (
            'Local'
            if ip_address.startswith('127.') or ip_address.startswith('192.168.')
            else ip_address
        )

        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                INSERT INTO user_sessions
                (user_id, session_token, device_name, device_type, ip_address, user_agent, location)
                VALUES (:user_id, :session_token, :device_name, :device_type,
                        :ip_address, :user_agent, :location)
            '''), {
                "user_id": user_id,
                "session_token": session_token,
                "device_name": device_name,
                "device_type": device_type,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "location": location
            })

        return session_token

    @staticmethod
    def validate_session(session_token: str):
        """
        Validate a session token and return user_id, or None if invalid/expired.
        """
        # Step 1: read-only check
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text('''
                SELECT user_id, last_active FROM user_sessions
                WHERE session_token = :token AND is_active = TRUE
            '''), {"token": session_token}).fetchone()

        if not result:
            return None

        user_id, last_active = result

        # Check 24-hour expiry
        if last_active and (datetime.now() - last_active) > timedelta(hours=24):
            SessionManager.revoke_session(session_token)
            return None

        # Step 2: write transaction to update last_active (actually committed)
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text('''
                    UPDATE user_sessions
                    SET last_active = CURRENT_TIMESTAMP
                    WHERE session_token = :token
                '''), {"token": session_token})
        except Exception as e:
            # Non-fatal — session is still valid even if we can't update timestamp
            logger.warning(f"Failed to update session last_active: {e}")

        return user_id

    @staticmethod
    def revoke_session(session_token: str) -> None:
        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                UPDATE user_sessions SET is_active = FALSE
                WHERE session_token = :token
            '''), {"token": session_token})

    @staticmethod
    def revoke_all_sessions(user_id: int, except_token: str = None) -> None:
        with DB_ENGINE.begin() as conn:
            if except_token:
                conn.execute(text('''
                    UPDATE user_sessions SET is_active = FALSE
                    WHERE user_id = :user_id AND session_token != :except_token
                '''), {"user_id": user_id, "except_token": except_token})
            else:
                conn.execute(text('''
                    UPDATE user_sessions SET is_active = FALSE
                    WHERE user_id = :user_id
                '''), {"user_id": user_id})

    @staticmethod
    def get_active_sessions(user_id: int) -> list:
        with DB_ENGINE.connect() as conn:
            sessions = conn.execute(text('''
                SELECT session_token, device_name, device_type, ip_address,
                       location, last_active, created_at
                FROM user_sessions
                WHERE user_id = :user_id AND is_active = TRUE
                ORDER BY last_active DESC
            '''), {"user_id": user_id}).fetchall()

        return [{
            'token': s[0],
            'device_name': s[1],
            'device_type': s[2],
            'ip_address': s[3],
            'location': s[4],
            'last_active': s[5].isoformat() if s[5] else None,
            'created_at': s[6].isoformat() if s[6] else None
        } for s in sessions]

    @staticmethod
    def cleanup_expired_sessions(older_than_hours: int = 48) -> int:
        """
        Hard-delete inactive or expired session rows to keep the table small.

        Call this from a scheduled task or on every login (acceptable since
        logins are infrequent compared to page views).

        Returns the number of rows deleted.
        """
        cutoff = datetime.now() - timedelta(hours=older_than_hours)
        try:
            with DB_ENGINE.begin() as conn:
                result = conn.execute(text('''
                    DELETE FROM user_sessions
                    WHERE is_active = FALSE
                       OR last_active < :cutoff
                '''), {"cutoff": cutoff})
                deleted = result.rowcount
            if deleted:
                logger.info(f"Cleaned up {deleted} expired session(s)")
            return deleted
        except Exception as e:
            logger.error(f"Session cleanup failed: {e}")
            return 0

    @staticmethod
    def check_location_restrictions(user_id: int, ip_address: str) -> bool:
        """No location restrictions by default."""
        return True
