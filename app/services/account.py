# app/services/account.py
from app.services.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime

# Hard‑coded plan limits (you can later move to a DB table)
PLAN_LIMITS = {
    'starter': {
        'invoice_limit': 100,
        'inventory_limit': None,
        'user_limit': 1,
        'has_purchase_orders': False,
        'has_ai_insights': False,
    },
    'growth': {
        'invoice_limit': None,          # None = unlimited
        'inventory_limit': None,
        'user_limit': 3,
        'has_purchase_orders': True,
        'has_ai_insights': False,
    },
    'pro': {
        'invoice_limit': None,
        'inventory_limit': None,
        'user_limit': None,             # unlimited
        'has_purchase_orders': True,
        'has_ai_insights': True,
    }
}

def create_account(name, plan='starter'):
    """Create a new account and return its id."""
    with DB_ENGINE.begin() as conn:
        result = conn.execute(
            text("INSERT INTO accounts (name, subscription_plan) VALUES (:name, :plan) RETURNING id"),
            {"name": name, "plan": plan}
        )
        account_id = result.scalar()
    return account_id

def get_account(account_id):
    """Return account details as dict."""
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM accounts WHERE id = :aid"),
            {"aid": account_id}
        ).first()
    return dict(row._mapping) if row else None

def get_plan_limits(plan_name):
    """Return limits dict for a given plan."""
    return PLAN_LIMITS.get(plan_name, PLAN_LIMITS['starter'])

def get_current_usage(account_id):
    """Get usage counts for current month (year, month)."""
    now = datetime.now()
    year = now.year
    month = now.month
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT invoice_count, inventory_count FROM monthly_usage WHERE account_id = :aid AND year = :year AND month = :month"),
            {"aid": account_id, "year": year, "month": month}
        ).first()
    if row:
        return {"invoices": row[0], "inventory": row[1]}
    return {"invoices": 0, "inventory": 0}

def increment_invoice_count(account_id):
    """Increment invoice count for current month (create row if needed)."""
    now = datetime.now()
    year = now.year
    month = now.month
    with DB_ENGINE.begin() as conn:
        # Upsert: try update, if no rows affected insert
        result = conn.execute(
            text("""
                UPDATE monthly_usage
                SET invoice_count = invoice_count + 1
                WHERE account_id = :aid AND year = :year AND month = :month
                RETURNING id
            """),
            {"aid": account_id, "year": year, "month": month}
        )
        if result.rowcount == 0:
            conn.execute(
                text("INSERT INTO monthly_usage (account_id, year, month, invoice_count) VALUES (:aid, :year, :month, 1)"),
                {"aid": account_id, "year": year, "month": month}
            )

def increment_inventory_count(account_id):
    """Increment active inventory count (when adding a new product)."""
    now = datetime.now()
    year = now.year
    month = now.month
    with DB_ENGINE.begin() as conn:
        result = conn.execute(
            text("""
                UPDATE monthly_usage
                SET inventory_count = inventory_count + 1
                WHERE account_id = :aid AND year = :year AND month = :month
                RETURNING id
            """),
            {"aid": account_id, "year": year, "month": month}
        )
        if result.rowcount == 0:
            conn.execute(
                text("INSERT INTO monthly_usage (account_id, year, month, inventory_count) VALUES (:aid, :year, :month, 1)"),
                {"aid": account_id, "year": year, "month": month}
            )

def decrement_inventory_count(account_id):
    """Decrement when a product is deleted (soft delete maybe)."""
    now = datetime.now()
    year = now.year
    month = now.month
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("""
                UPDATE monthly_usage
                SET inventory_count = GREATEST(inventory_count - 1, 0)
                WHERE account_id = :aid AND year = :year AND month = :month
            """),
            {"aid": account_id, "year": year, "month": month}
        )

def check_invoice_limit(account_id):
    """Return (allowed, message) tuple."""
    account = get_account(account_id)
    if not account:
        return False, "Account not found"
    limits = get_plan_limits(account['subscription_plan'])
    if limits['invoice_limit'] is None:
        return True, "Unlimited"
    usage = get_current_usage(account_id)
    if usage['invoices'] >= limits['invoice_limit']:
        return False, f"Invoice limit reached ({limits['invoice_limit']} per month)"
    return True, "OK"

def check_inventory_limit(account_id):
    """Return (allowed, message)."""
    account = get_account(account_id)
    if not account:
        return False, "Account not found"
    limits = get_plan_limits(account['subscription_plan'])
    if limits['inventory_limit'] is None:
        return True, "Unlimited"
    usage = get_current_usage(account_id)
    if usage['inventory'] >= limits['inventory_limit']:
        return False, f"Inventory item limit reached ({limits['inventory_limit']})"
    return True, "OK"

def check_user_limit(account_id, additional_users=1):
    """Check if adding additional_users would exceed plan limit."""
    account = get_account(account_id)
    if not account:
        return False, "Account not found"
    limits = get_plan_limits(account['subscription_plan'])
    if limits['user_limit'] is None:
        return True, "Unlimited"
    with DB_ENGINE.connect() as conn:
        current_users = conn.execute(
            text("SELECT COUNT(*) FROM users WHERE account_id = :aid"),
            {"aid": account_id}
        ).scalar() or 0
    if current_users + additional_users > limits['user_limit']:
        return False, f"User limit reached ({limits['user_limit']} users)"
    return True, "OK"

def has_feature(account_id, feature):
    """feature can be 'purchase_orders' or 'ai_insights'."""
    account = get_account(account_id)
    if not account:
        return False
    limits = get_plan_limits(account['subscription_plan'])
    return limits.get(feature, False)
