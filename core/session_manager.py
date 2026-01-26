# core/session_manager.py - PostgreSQL compatible
import secrets
from datetime import datetime, timedelta
from core.db import DB_ENGINE
from sqlalchemy import text

class SessionManager:

    @staticmethod
    def create_session(user_id, request):
        """Create new session with device info"""
        session_token = secrets.token_urlsafe(32)
        user_agent = request.headers.get('User-Agent', 'Unknown')
        ip_address = request.remote_addr

        device_type = 'mobile' if 'Mobile' in user_agent else 'desktop'
        device_name = user_agent[:50] if user_agent else 'Unknown Device'
        location = 'Local' if ip_address.startswith('127.') or ip_address.startswith('192.168.') else ip_address

        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                INSERT INTO user_sessions
                (user_id, session_token, device_name, device_type, ip_address, user_agent, location)
                VALUES (:user_id, :session_token, :device_name, :device_type, :ip_address, :user_agent, :location)
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
    def validate_session(session_token):
        """Validate session and return user_id"""
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text('''
                SELECT user_id, last_active FROM user_sessions
                WHERE session_token = :session_token AND is_active = TRUE
            '''), {"session_token": session_token}).fetchone()

            if result:
                user_id, last_active = result

                # Check if session expired (24 hours)
                if last_active and (datetime.now() - last_active) > timedelta(hours=24):
                    SessionManager.revoke_session(session_token)
                    return None

                # Update last active
                conn.execute(text('''
                    UPDATE user_sessions
                    SET last_active = CURRENT_TIMESTAMP
                    WHERE session_token = :session_token
                '''), {"session_token": session_token})

                return user_id

        return None

    @staticmethod
    def revoke_session(session_token):
        """Revoke a specific session"""
        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                UPDATE user_sessions
                SET is_active = FALSE
                WHERE session_token = :session_token
            '''), {"session_token": session_token})

    @staticmethod
    def revoke_all_sessions(user_id, except_token=None):
        """Revoke all sessions for a user except current"""
        with DB_ENGINE.begin() as conn:
            if except_token:
                conn.execute(text('''
                    UPDATE user_sessions
                    SET is_active = FALSE
                    WHERE user_id = :user_id AND session_token != :except_token
                '''), {"user_id": user_id, "except_token": except_token})
            else:
                conn.execute(text('''
                    UPDATE user_sessions
                    SET is_active = FALSE
                    WHERE user_id = :user_id
                '''), {"user_id": user_id})

    @staticmethod
    def get_active_sessions(user_id):
        """Get all active sessions for user"""
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
    def check_location_restrictions(user_id, ip_address):
        """Check if user's location is allowed - simplified"""
        return True  # No restrictions by default
