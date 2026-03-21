# app/services/ai_context.py
import re
from sqlalchemy import text
from app.services.db import DB_ENGINE

def fetch_top_products(account_id, limit=5):
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT i.name, SUM(ii.quantity) as total_sold, SUM(ii.total) as revenue
            FROM invoice_items ii
            JOIN inventory_items i ON ii.product_id = i.id
            WHERE i.account_id = :aid
            GROUP BY i.id, i.name
            ORDER BY total_sold DESC
            LIMIT :limit
        """), {"aid": account_id, "limit": limit}).fetchall()
    return [{"name": r.name, "sold": r.total_sold, "revenue": float(r.revenue)} for r in rows]

def fetch_supplier_data(account_id, supplier_name=None):
    with DB_ENGINE.connect() as conn:
        if supplier_name:
            rows = conn.execute(text("""
                SELECT name, total_purchased, order_count
                FROM suppliers
                WHERE account_id = :aid AND name ILIKE :name
            """), {"aid": account_id, "name": f"%{supplier_name}%"}).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT name, total_purchased, order_count
                FROM suppliers
                WHERE account_id = :aid
                ORDER BY total_purchased DESC
                LIMIT 5
            """), {"aid": account_id}).fetchall()
    return [{"name": r.name, "total_purchased": float(r.total_purchased), "order_count": r.order_count} for r in rows]

def fetch_invoice_summary(account_id):
    with DB_ENGINE.connect() as conn:
        total = conn.execute(text("SELECT COALESCE(SUM(grand_total),0) FROM user_invoices WHERE account_id = :aid"), {"aid": account_id}).scalar()
        count = conn.execute(text("SELECT COUNT(*) FROM user_invoices WHERE account_id = :aid"), {"aid": account_id}).scalar()
        monthly = conn.execute(text("""
            SELECT DATE_TRUNC('month', invoice_date) AS month, SUM(grand_total)
            FROM user_invoices
            WHERE account_id = :aid
            GROUP BY month
            ORDER BY month DESC
            LIMIT 3
        """), {"aid": account_id}).fetchall()
    return {
        "total_revenue": float(total),
        "invoice_count": count,
        "last_3_months": [{"month": r[0].strftime("%Y-%m"), "revenue": float(r[1])} for r in monthly]
    }

def fetch_inventory_summary(account_id):
    with DB_ENGINE.connect() as conn:
        total_value = conn.execute(text("SELECT COALESCE(SUM(current_stock * cost_price),0) FROM inventory_items WHERE account_id = :aid"), {"aid": account_id}).scalar()
        low_stock = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE account_id = :aid AND current_stock <= min_stock_level
        """), {"aid": account_id}).scalar()
        top_items = conn.execute(text("""
            SELECT name, current_stock, selling_price
            FROM inventory_items
            WHERE account_id = :aid
            ORDER BY current_stock * selling_price DESC
            LIMIT 5
        """), {"aid": account_id}).fetchall()
    return {
        "total_value": float(total_value),
        "low_stock_count": low_stock,
        "top_items": [{"name": r.name, "stock": r.current_stock, "price": float(r.selling_price)} for r in top_items]
    }

def fetch_general_metrics(account_id):
    with DB_ENGINE.connect() as conn:
        revenue = conn.execute(text("SELECT COALESCE(SUM(grand_total),0) FROM user_invoices WHERE account_id = :aid"), {"aid": account_id}).scalar()
        expenses = conn.execute(text("SELECT COALESCE(SUM(amount),0) FROM expenses WHERE account_id = :aid"), {"aid": account_id}).scalar()
        inventory = conn.execute(text("SELECT COALESCE(SUM(current_stock * cost_price),0) FROM inventory_items WHERE account_id = :aid"), {"aid": account_id}).scalar()
    return {
        "revenue": float(revenue),
        "expenses": float(expenses),
        "profit": float(revenue - expenses),
        "inventory_value": float(inventory)
    }

def fetch_context(account_id, user_question):
    context = {}
    extra_system = ""

    if re.search(r'\bsupplier\b', user_question, re.IGNORECASE):
        supplier_match = re.search(r'supplier\s+(\w+)', user_question, re.IGNORECASE)
        supplier_name = supplier_match.group(1) if supplier_match else None
        supplier_data = fetch_supplier_data(account_id, supplier_name)
        context["supplier"] = supplier_data
        extra_system = "The user is asking about a specific supplier. Focus on supplier performance."

    elif re.search(r'\btop.?selling\b|\bbest.?seller\b|\bproduct\b|\bitem\b', user_question, re.IGNORECASE):
        context["top_products"] = fetch_top_products(account_id)
        extra_system = "The user is asking about product sales. Provide insights on best-selling items."

    elif re.search(r'\binvoice\b|\btransaction\b|\bsales\b', user_question, re.IGNORECASE):
        invoice_data = fetch_invoice_summary(account_id)
        context["invoices"] = invoice_data
        extra_system = "The user is asking about invoices/sales. Provide insights on revenue trends."

    elif re.search(r'\binventory\b|\bstock\b|\breorder\b', user_question, re.IGNORECASE):
        inventory_data = fetch_inventory_summary(account_id)
        context["inventory"] = inventory_data
        extra_system = "The user is asking about inventory. Focus on stock levels, turnover, and reorder needs."

    else:
        general_data = fetch_general_metrics(account_id)
        context["general"] = general_data
        extra_system = "The user is asking a general business question. Use all available data."

    return extra_system, context
