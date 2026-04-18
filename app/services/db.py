# app/services/db.py
import os
import logging
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///users.db')
DB_ENGINE = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=300,
)

logger.info(f"Database connected: {DATABASE_URL[:50]}...")


def init_database():
    """Dead code — never called. Kept for reference only."""
    pass


def create_all_tables():
    """Create all required tables with correct schema."""
    with DB_ENGINE.begin() as conn:
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                company_name TEXT,
                company_address TEXT,
                company_phone TEXT,
                company_email TEXT,
                company_tax_id TEXT,
                seller_ntn TEXT,
                seller_strn TEXT,
                mobile_number TEXT,
                preferred_currency TEXT DEFAULT 'PKR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS user_invoices (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                invoice_number TEXT NOT NULL,
                client_name TEXT NOT NULL,
                invoice_date DATE NOT NULL,
                due_date DATE,
                grand_total DECIMAL(10,2) NOT NULL,
                status TEXT DEFAULT 'paid',
                invoice_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS inventory_items (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sku TEXT,
                category TEXT,
                description TEXT,
                current_stock INTEGER DEFAULT 0,
                min_stock_level INTEGER DEFAULT 5,
                cost_price DECIMAL(10,2),
                selling_price DECIMAL(10,2),
                supplier TEXT,
                location TEXT,
                barcode TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_user_sku UNIQUE (user_id, sku)
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS stock_movements (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                movement_type TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                reference_id TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                po_number TEXT NOT NULL,
                supplier_name TEXT NOT NULL,
                order_date DATE NOT NULL,
                delivery_date DATE,
                grand_total DECIMAL(10,2) NOT NULL,
                status TEXT DEFAULT 'pending',
                order_data TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS suppliers (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                address TEXT,
                tax_id TEXT,
                total_purchased DECIMAL(10,2) DEFAULT 0,
                order_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS user_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                session_token TEXT UNIQUE NOT NULL,
                device_name TEXT,
                device_type TEXT,
                ip_address TEXT,
                user_agent TEXT,
                location TEXT,
                is_active BOOLEAN DEFAULT TRUE,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS download_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                document_type TEXT NOT NULL,
                document_number TEXT NOT NULL,
                downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS ai_insights (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                task_id TEXT,
                insight_type TEXT DEFAULT 'summary',
                content TEXT,
                status TEXT DEFAULT 'pending',
                currency_code TEXT DEFAULT 'PKR',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))
        logger.info("AI Insights table verified")

        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS session_storage (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                session_key TEXT NOT NULL,
                data_type TEXT NOT NULL,
                data TEXT NOT NULL,
                expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '24 hours',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        '''))

        logger.info("All tables created/verified successfully")


def create_missing_tables():
    """Create any auxiliary tables that might be missing."""
    with DB_ENGINE.begin() as conn:
        tables = [
            ('customers', '''
                CREATE TABLE IF NOT EXISTS customers (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    address TEXT,
                    tax_id TEXT,
                    total_spent DECIMAL(10,2) DEFAULT 0,
                    invoice_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            '''),
            ('expenses', '''
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    amount DECIMAL(10,2) NOT NULL,
                    category TEXT NOT NULL,
                    expense_date DATE NOT NULL,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            '''),
            ('stock_alerts', '''
                CREATE TABLE IF NOT EXISTS stock_alerts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    alert_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    is_resolved BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        ]

        for table_name, create_sql in tables:
            try:
                conn.execute(text(create_sql))
                logger.debug(f"Verified/created table: {table_name}")
            except Exception as e:
                logger.warning(f"Table {table_name} setup issue: {e}")

        # po_receipts handled separately due to historical concurrency issues
        try:
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'po_receipts'
                )
            """)).scalar()
            if not result:
                conn.execute(text("""
                    CREATE TABLE po_receipts (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        po_number TEXT NOT NULL,
                        product_id INTEGER NOT NULL,
                        received_qty INTEGER NOT NULL,
                        received_date DATE NOT NULL,
                        notes TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                logger.info("Created table: po_receipts")
            else:
                logger.debug("Table po_receipts already exists")
        except Exception as e:
            logger.warning(f"po_receipts table issue: {e}")


def apply_inventory_constraints():
    """Apply inventory uniqueness constraints."""
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'unique_user_sku'
                    ) THEN
                        ALTER TABLE inventory_items
                        ADD CONSTRAINT unique_user_sku UNIQUE (user_id, sku);
                    END IF;
                END $$;
            '''))
            logger.info("Inventory constraints verified")
    except Exception as e:
        logger.warning(f"Constraint check issue: {e}")


def fix_reference_id_column():
    """Ensure reference_id in stock_movements is TEXT type."""
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text('''
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'stock_movements'
                        AND column_name = 'reference_id'
                        AND data_type != 'text'
                    ) THEN
                        ALTER TABLE stock_movements
                        ALTER COLUMN reference_id TYPE TEXT;
                    END IF;
                END $$;
            '''))
            logger.info("Reference ID column verified")
    except Exception as e:
        logger.warning(f"Column fix issue: {e}")


# Run on import
try:
    create_all_tables()
    create_missing_tables()
    apply_inventory_constraints()
    fix_reference_id_column()
except Exception as e:
    logger.error(f"Initial database setup failed: {e}", exc_info=True)
