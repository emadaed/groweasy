# core/reports.py - Fixed version
from core.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime, timedelta

class InventoryReports:
    @staticmethod
    def get_stock_turnover(user_id, days=30):
        """Get stock turnover rate - FIXED VERSION"""
        date_threshold = datetime.now() - timedelta(days=days)

        with DB_ENGINE.connect() as conn:
            # Use subquery instead of HAVING on calculated column
            result = conn.execute(text("""
                WITH sales_data AS (
                    SELECT
                        ii.id,
                        ii.name,
                        ii.current_stock,
                        ii.cost_price,
                        ii.selling_price,
                        COALESCE(SUM(CASE
                            WHEN sm.movement_type = 'sale'
                            AND sm.created_at >= :date_threshold
                            THEN ABS(sm.quantity)
                            ELSE 0
                        END), 0) as units_sold
                    FROM inventory_items ii
                    LEFT JOIN stock_movements sm ON ii.id = sm.product_id
                    WHERE ii.user_id = :user_id AND ii.is_active = TRUE
                    GROUP BY ii.id
                )
                SELECT * FROM sales_data
                WHERE units_sold > 0
                ORDER BY units_sold DESC
            """), {
                "date_threshold": date_threshold,
                "user_id": user_id
            }).fetchall()

        return [dict(row._mapping) for row in result]

    @staticmethod
    def get_bcg_matrix(user_id):
        """Boston Consulting Group Matrix - FIXED"""
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT
                    ii.id,
                    ii.name,
                    ii.current_stock as market_share,
                    COALESCE(SUM(CASE
                        WHEN sm.movement_type = 'sale'
                        THEN ABS(sm.quantity)
                        ELSE 0
                    END), 0) as growth_rate
                FROM inventory_items ii
                LEFT JOIN stock_movements sm ON ii.id = sm.product_id
                WHERE ii.user_id = :user_id AND ii.is_active = TRUE
                GROUP BY ii.id
                ORDER BY growth_rate DESC
            """), {"user_id": user_id}).fetchall()

        return [dict(row._mapping) for row in result]
