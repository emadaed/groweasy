# app/services/reports.py - Fixed version with account_id and BCG categories
from app.services.db import DB_ENGINE
from sqlalchemy import text
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class InventoryReports:
    @staticmethod
    def get_stock_turnover(account_id, days=30):
        """Get stock turnover rate (units sold per product in last N days)."""
        date_threshold = datetime.now() - timedelta(days=days)

        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    i.id,
                    i.name,
                    i.current_stock,
                    COALESCE(SUM(
                        CASE WHEN sm.movement_type = 'sale'
                             AND sm.created_at >= :date_threshold
                             THEN ABS(sm.quantity)
                             ELSE 0
                        END
                    ), 0) as units_sold,
                    COALESCE(SUM(
                        CASE WHEN sm.movement_type = 'sale'
                             AND sm.created_at >= :date_threshold
                             THEN ABS(sm.quantity) * i.selling_price
                             ELSE 0
                        END
                    ), 0) as revenue
                FROM inventory_items i
                LEFT JOIN stock_movements sm ON i.id = sm.product_id
                WHERE i.account_id = :aid AND i.is_active = TRUE
                GROUP BY i.id
                ORDER BY units_sold DESC
            """), {"aid": account_id, "date_threshold": date_threshold}).fetchall()

        result = []
        for row in rows:
            units_sold = row.units_sold
            turnover_ratio = units_sold / row.current_stock if row.current_stock > 0 else 0
            days_to_sell = (days / turnover_ratio) if turnover_ratio > 0 else 0
            status = 'Fast' if turnover_ratio > 1 else 'Moderate' if turnover_ratio > 0.3 else 'Slow'
            result.append({
                'id': row.id,
                'name': row.name,
                'current_stock': float(row.current_stock),
                'units_sold': float(units_sold),
                'turnover_ratio': round(turnover_ratio, 2),
                'days_to_sell': round(days_to_sell, 1),
                'status': status,
                'revenue': float(row.revenue)
            })
        return result

    @staticmethod
    def get_bcg_matrix(account_id):
        """
        Returns BCG categories: stars, cash_cows, question_marks, dogs.
        Uses sales volume (units sold last 90 days) and profit margin.
        """
        date_threshold = datetime.now() - timedelta(days=90)

        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    i.id,
                    i.name,
                    i.current_stock,
                    i.cost_price,
                    i.selling_price,
                    COALESCE(SUM(
                        CASE WHEN sm.movement_type = 'sale'
                             AND sm.created_at >= :date_threshold
                             THEN ABS(sm.quantity)
                             ELSE 0
                        END
                    ), 0) as units_sold,
                    COALESCE(SUM(
                        CASE WHEN sm.movement_type = 'sale'
                             AND sm.created_at >= :date_threshold
                             THEN ABS(sm.quantity) * i.selling_price
                             ELSE 0
                        END
                    ), 0) as revenue
                FROM inventory_items i
                LEFT JOIN stock_movements sm ON i.id = sm.product_id
                WHERE i.account_id = :aid AND i.is_active = TRUE
                GROUP BY i.id
            """), {"aid": account_id, "date_threshold": date_threshold}).fetchall()

        if not rows:
            return {'stars': [], 'cash_cows': [], 'question_marks': [], 'dogs': []}

        # Compute profit margin for each product
        product_data = []
        for row in rows:
            units_sold = float(row.units_sold)
            revenue = float(row.revenue)
            cost = units_sold * float(row.cost_price)
            profit_margin = ((revenue - cost) / revenue * 100) if revenue > 0 else 0
            product_data.append({
                'id': row.id,
                'name': row.name,
                'units_sold': units_sold,
                'revenue': revenue,
                'profit_margin': profit_margin,
                'current_stock': float(row.current_stock)
            })

        # Determine thresholds (median for units sold, median for margin)
        units_sold_list = sorted([p['units_sold'] for p in product_data])
        margin_list = sorted([p['profit_margin'] for p in product_data])
        units_median = units_sold_list[len(units_sold_list)//2] if units_sold_list else 0
        margin_median = margin_list[len(margin_list)//2] if margin_list else 0

        categories = {
            'stars': [],
            'cash_cows': [],
            'question_marks': [],
            'dogs': []
        }

        for p in product_data:
            # High sales = above median units sold
            high_sales = p['units_sold'] > units_median
            high_margin = p['profit_margin'] > margin_median

            if high_sales and high_margin:
                categories['stars'].append(p)
            elif high_sales and not high_margin:
                categories['cash_cows'].append(p)
            elif not high_sales and high_margin:
                categories['question_marks'].append(p)
            else:
                categories['dogs'].append(p)

        return categories

    @staticmethod
    def get_profitability_analysis(account_id):
        """List products with profit margin."""
        date_threshold = datetime.now() - timedelta(days=90)

        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    i.id,
                    i.name,
                    i.cost_price,
                    i.selling_price,
                    COALESCE(SUM(
                        CASE WHEN sm.movement_type = 'sale'
                             AND sm.created_at >= :date_threshold
                             THEN ABS(sm.quantity)
                             ELSE 0
                        END
                    ), 0) as units_sold
                FROM inventory_items i
                LEFT JOIN stock_movements sm ON i.id = sm.product_id
                WHERE i.account_id = :aid AND i.is_active = TRUE
                GROUP BY i.id
                HAVING COALESCE(SUM(
                    CASE WHEN sm.movement_type = 'sale'
                         AND sm.created_at >= :date_threshold
                         THEN ABS(sm.quantity)
                         ELSE 0
                    END
                ), 0) > 0
                ORDER BY (i.selling_price - i.cost_price) DESC
            """), {"aid": account_id, "date_threshold": date_threshold}).fetchall()

        result = []
        for row in rows:
            profit_per_unit = float(row.selling_price) - float(row.cost_price)
            total_profit = profit_per_unit * float(row.units_sold)
            result.append({
                'id': row.id,
                'name': row.name,
                'units_sold': float(row.units_sold),
                'profit_per_unit': round(profit_per_unit, 2),
                'total_profit': round(total_profit, 2)
            })
        return result

    @staticmethod
    def get_slow_movers(account_id, days_threshold=90):
        """Products with no sales in the last N days."""
        date_threshold = datetime.now() - timedelta(days=days_threshold)

        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    i.id,
                    i.name,
                    i.current_stock,
                    i.cost_price,
                    i.selling_price,
                    MAX(sm.created_at) as last_sale_date
                FROM inventory_items i
                LEFT JOIN stock_movements sm ON i.id = sm.product_id
                    AND sm.movement_type = 'sale'
                WHERE i.account_id = :aid AND i.is_active = TRUE
                GROUP BY i.id
                HAVING MAX(sm.created_at) IS NULL OR MAX(sm.created_at) < :date_threshold
                ORDER BY last_sale_date ASC NULLS FIRST
            """), {"aid": account_id, "date_threshold": date_threshold}).fetchall()

        result = []
        for row in rows:
            days_inactive = (datetime.now() - row.last_sale_date).days if row.last_sale_date else days_threshold + 1
            result.append({
                'id': row.id,
                'name': row.name,
                'current_stock': float(row.current_stock),
                'cost_price': float(row.cost_price),
                'selling_price': float(row.selling_price),
                'last_sale_date': row.last_sale_date.strftime('%Y-%m-%d') if row.last_sale_date else 'Never',
                'days_inactive': days_inactive
            })
        return result
