# core/session_storage.py - Store large session data in database
import time
import json
from datetime import datetime
from core.db import DB_ENGINE
from sqlalchemy import text

class SessionStorage:
    @staticmethod
    def store_large_data(user_id, data_type, data):
        """Store large data in database instead of session"""
        try:
            session_key = f"{data_type}_{int(time.time())}"

            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    INSERT INTO session_storage
                    (user_id, session_key, data_type, data, expires_at)
                    VALUES (:user_id, :session_key, :data_type, :data,
                            NOW() + INTERVAL '24 hours')
                """), {
                    "user_id": user_id,
                    "session_key": session_key,
                    "data_type": data_type,
                    "data": json.dumps(data)
                })

            return session_key
        except Exception as e:
            print(f"Session storage error: {e}")
            # Fallback to simple key
            return f"{data_type}_{int(time.time())}"

    @staticmethod
    def get_data(user_id, session_key):
        """Retrieve stored data"""
        try:
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT data FROM session_storage
                    WHERE user_id = :user_id AND session_key = :session_key
                    AND expires_at > NOW()
                """), {
                    "user_id": user_id,
                    "session_key": session_key
                }).fetchone()

                if result:
                    return json.loads(result[0])
        except Exception as e:
            print(f"Session retrieval error: {e}")

        return None

    @staticmethod
    def clear_data(user_id, data_type):
        """Clear expired data"""
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    DELETE FROM session_storage
                    WHERE user_id = :user_id AND data_type = :data_type
                    OR expires_at <= NOW()
                """), {
                    "user_id": user_id,
                    "data_type": data_type
                })
        except Exception as e:
            print(f"Session clear error: {e}")
