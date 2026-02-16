# app/services/report_service.py
from sqlalchemy import text
from app.services.db import DB_ENGINE
from datetime import datetime, timedelta
from flask_caching import Cache

# Setup a cache that all workers can see
cache = Cache(config={'CACHE_TYPE': 'FileSystemCache', 'CACHE_DIR': '/tmp/flask_cache'})


class ReportService:
    @staticmethod
    def get_financial_summary(user_id):
        with DB_ENGINE.connect() as conn:
            # Use the CAST fix we discussed for Postgres TEXT columns
            sales_query = conn.execute(text("""
                SELECT 
                    SUM(CAST(CAST(invoice_data AS JSONB)->>'total_amount' AS FLOAT)) as total_revenue,
                    SUM(CAST(CAST(invoice_data AS JSONB)->>'tax_amount' AS FLOAT)) as tax_collected
                FROM user_invoices 
                WHERE user_id = :uid 
                  AND created_at >= CURRENT_DATE - INTERVAL '30 days'
            """), {"uid": user_id}).mappings().first()
            
            # Align keys to what the UI/AI expects: 'revenue' instead of 'total_revenue'
            return {
                "revenue": sales_query['total_revenue'] or 0,
                "tax_collected": sales_query['tax_collected'] or 0,
                "costs": 0,  # Placeholder until you add Purchase Order logic
                "tax_paid": 0,
                "inventory_value": 0,
                "net_profit": (sales_query['total_revenue'] or 0) - 0,
                "tax_liability": (sales_query['tax_collected'] or 0) - 0
            }
        
            # 2. Purchases & Tax Paid
            purchases_query = conn.execute(text("""
                SELECT 
                    SUM(grand_total) as total_costs,
                    SUM(CAST(json_extract(order_data, '$.tax_amount') AS FLOAT)) as tax_paid
                FROM purchase_orders 
                WHERE user_id = :uid AND status = 'Received'
                AND created_at >= date('now', '-30 days')
            """), {"uid": user_id}).fetchone()

            # 3. Inventory Valuation
            inventory = conn.execute(text("""
                SELECT SUM(stock_level * cost_price) FROM inventory WHERE user_id = :uid
            """), {"uid": user_id}).scalar() or 0

            return {
                "revenue": sales_query[0] or 0,
                "tax_collected": sales_query[1] or 0,
                "costs": purchases_query[0] or 0,
                "tax_paid": purchases_query[1] or 0,
                "inventory_value": inventory,
                "net_profit": (sales_query[0] or 0) - (purchases_query[0] or 0),
                "tax_liability": (sales_query[1] or 0) - (purchases_query[1] or 0)
            }

def get_cached_insights(user_id, data):
    cache_key = f"ai_insights_{user_id}"
    existing = cache.get(cache_key)
    if existing:
        return existing
    
    # Only call the API if the cache is empty
    new_insights = get_gemini_insights(data)
    cache.set(cache_key, new_insights, timeout=3600) # Cache for 1 hour
    return new_insights
