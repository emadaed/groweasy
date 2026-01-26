# database_migration.py
from core.db import DB_ENGINE
from sqlalchemy import text, inspect
import sqlalchemy as sa

def fix_database():
    inspector = inspect(DB_ENGINE)
    is_sqlite = DB_ENGINE.dialect.name == 'sqlite'
    is_postgresql = DB_ENGINE.dialect.name == 'postgresql'

    print(f"🔍 Detected database: {DB_ENGINE.dialect.name.upper()}")
    print("🔧 Starting database schema migration...")

    with DB_ENGINE.begin() as conn:
        # 1. stock_movements table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                movement_type VARCHAR(50) NOT NULL,
                reference_id VARCHAR(100),
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        print("✅ stock_movements table verified")

        # 2. purchase_orders table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                po_number VARCHAR(50) UNIQUE NOT NULL,
                order_data TEXT NOT NULL,
                status VARCHAR(50) DEFAULT 'draft',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        print("✅ purchase_orders table verified")

        # 3. ADD received_at column — COMPATIBLE WITH BOTH SQLite AND PostgreSQL
        if 'received_at' not in inspector.get_columns('purchase_orders'):
            if is_sqlite:
                # SQLite way: Add column without IF NOT EXISTS
                conn.execute(text("""
                    ALTER TABLE purchase_orders
                    ADD COLUMN received_at TIMESTAMP
                """))
                print("✅ Added received_at column (SQLite mode)")
            elif is_postgresql:
                # PostgreSQL way: Safe with IF NOT EXISTS
                conn.execute(text("""
                    ALTER TABLE purchase_orders
                    ADD COLUMN IF NOT EXISTS received_at TIMESTAMP DEFAULT NULL
                """))
                print("✅ Added received_at column (PostgreSQL mode)")
        else:
            print("ℹ️ received_at column already exists — skipping")

        # Optional: Add index only on PostgreSQL (SQLite handles it differently)
        if is_postgresql:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_purchase_orders_received_at
                ON purchase_orders(received_at)
            """))
            print("✅ Performance index added on received_at")

        print("🎉 Database migration completed successfully!")
        print("   → Safe to use the full mark_po_received route with received_at = NOW()")

if __name__ == "__main__":
    fix_database()
