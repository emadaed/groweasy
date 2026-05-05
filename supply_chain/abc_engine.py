#groweasy.supply_chain.abc_engine.py
from sqlalchemy import text
from app.services.db import DB_ENGINE
from decimal import Decimal
from statistics import stdev
import logging

logger = logging.getLogger(__name__)

def classify_abc_items(user_id: int, alpha: float = 1.0, beta: float = 0.0):
    """Classify using revenue from invoice_items (linked via inventory_items.id)."""
    with DB_ENGINE.begin() as conn:
        rows = conn.execute(text("""
            SELECT i.id AS item_id,
                   SUM(ii.quantity * ii.unit_price) AS revenue
            FROM inventory_items i
            JOIN invoice_items ii ON i.id = ii.product_id
            JOIN user_invoices ui ON ii.invoice_id = ui.id
            WHERE i.user_id = :user_id
              AND ui.created_at >= NOW() - INTERVAL '12 months'
            GROUP BY i.id
        """), {"user_id": user_id}).mappings().all()

        if not rows:
            return

        total = sum(r["revenue"] for r in rows)
        rows.sort(key=lambda x: x["revenue"], reverse=True)

        cum = Decimal('0')
        for row in rows:
            cum += row["revenue"] / total * 100
            abc = 'A' if cum <= 70 else ('B' if cum <= 90 else 'C')
            # Update scm_inventory_items via the linked inventory_item_id
            conn.execute(text("""
                UPDATE scm_inventory_items
                SET abc_class = :abc
                WHERE inventory_item_id = :item_id AND user_id = :user_id
            """), {"abc": abc, "item_id": row["item_id"], "user_id": user_id})

def assign_default_policies(user_id: int, override_existing: bool = False):
    policies = {
        'A': (0.95, 1.0, 7, True, True),
        'B': (0.90, 1.2, 15, True, False),
        'C': (0.85, 1.5, 30, False, False)
    }
    with DB_ENGINE.begin() as conn:
        for abc, (sl, ssm, rfd, ar, apr) in policies.items():
            if override_existing:
                conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET policy_service_level = :sl,
                        policy_safety_stock_multiplier = :ssm,
                        review_frequency_days = :rfd,
                        auto_reorder = :ar,
                        approval_required = :apr
                    WHERE user_id = :uid AND abc_class = :abc
                """), {"sl": sl, "ssm": ssm, "rfd": rfd, "ar": ar, "apr": apr, "uid": user_id, "abc": abc})
            else:
                conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET policy_service_level = COALESCE(policy_service_level, :sl),
                        policy_safety_stock_multiplier = COALESCE(policy_safety_stock_multiplier, :ssm),
                        review_frequency_days = COALESCE(review_frequency_days, :rfd),
                        auto_reorder = COALESCE(auto_reorder, :ar),
                        approval_required = COALESCE(approval_required, :apr)
                    WHERE user_id = :uid AND abc_class = :abc
                """), {"sl": sl, "ssm": ssm, "rfd": rfd, "ar": ar, "apr": apr, "uid": user_id, "abc": abc})

def get_current_stock(item_id: int, user_id: int) -> int:
    """Get current stock from inventory_items (linked via inventory_item_id)."""
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT i.current_stock
            FROM inventory_items i
            JOIN scm_inventory_items s ON i.id = s.inventory_item_id
            WHERE s.id = :item_id AND i.user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).first()
        return int(row[0]) if row else 0

def get_demand_stats(item_id: int, user_id: int, days: int = 90):
    """Get avg daily demand and std dev from invoice_items."""
    with DB_ENGINE.connect() as conn:
        # Get lead time from scm_inventory_items
        lead_row = conn.execute(text("""
            SELECT lead_time_days_avg FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).first()
        lead_time = float(lead_row[0]) if lead_row else 5.0

        # Get the linked inventory_item_id
        inv_item_id = conn.execute(text("""
            SELECT inventory_item_id FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).scalar()
        if not inv_item_id:
            return 0.0, 0.0, lead_time

        # Demand from invoice_items
        demand_rows = conn.execute(text("""
            SELECT ii.quantity, ui.created_at
            FROM invoice_items ii
            JOIN user_invoices ui ON ii.invoice_id = ui.id
            WHERE ii.product_id = :inv_item_id
              AND ui.user_id = :user_id
              AND ui.created_at >= NOW() - INTERVAL '1 day' * :days
        """), {"inv_item_id": inv_item_id, "user_id": user_id, "days": days}).mappings().all()

        if not demand_rows:
            return 0.0, 0.0, lead_time

        total_qty = sum(r["quantity"] for r in demand_rows)
        avg_daily = float(total_qty) / days
        quantities = [float(r["quantity"]) for r in demand_rows]
        std_daily = stdev(quantities) if len(quantities) > 1 else 0.0
        return avg_daily, std_daily, lead_time

def compute_reorder_params(item_id: int, user_id: int):
    """Use scm_inventory_items fields + compute EOQ/ROP with policy."""
    with DB_ENGINE.connect() as conn:
        s = conn.execute(text("""
            SELECT annual_demand, ordering_cost, unit_cost, holding_cost_pct,
                   policy_service_level, policy_safety_stock_multiplier
            FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).mappings().first()
        if not s:
            return None, None, None

    D = s["annual_demand"]
    S = s["ordering_cost"]
    H = s["unit_cost"] * s["holding_cost_pct"]  
    eoq = int((2 * D * S / H) ** Decimal('0.5')) if H > 0 else 0

    avg_demand, std_demand, lead_time = get_demand_stats(item_id, user_id)
    if avg_demand == 0:
        return eoq, None, None

    if s["policy_service_level"] is None or s["policy_safety_stock_multiplier"] is None:
        return eoq, None, None

    z_map = {0.85:1.036, 0.90:1.282, 0.95:1.645, 0.98:2.054}
    z = z_map.get(float(s["policy_service_level"]), 1.645)
    ss = z * (float(lead_time) ** 0.5) * std_demand * float(s["policy_safety_stock_multiplier"])
    rop = int(float(avg_demand) * lead_time + ss)
    safety_stock = int(ss)
    return eoq, rop, safety_stock

def select_best_supplier(item_id: int, user_id: int):
    """Simplified: get highest KPI supplier with lowest landed cost."""
    with DB_ENGINE.connect() as conn:
        # Get all suppliers with KPI score (latest) and average landed cost
        rows = conn.execute(text("""
            SELECT s.id, s.name,
                   COALESCE(sk.composite_score, 50) AS kpi,
                   COALESCE(lc.avg_landed_cost, 100) AS landed_cost
            FROM suppliers s
            LEFT JOIN (
                SELECT DISTINCT ON (supplier_name) supplier_name, composite_score
                FROM supplier_kpis WHERE user_id = :uid
                ORDER BY supplier_name, created_at DESC
            ) sk ON sk.supplier_name = s.name
            LEFT JOIN (
                SELECT user_id, AVG(landed_cost_per_unit) AS avg_landed_cost
                FROM landed_costs GROUP BY user_id
            ) lc ON lc.user_id = s.user_id
            WHERE s.user_id = :uid
            ORDER BY COALESCE(sk.composite_score, 50) / NULLIF(COALESCE(lc.avg_landed_cost, 100), 0) DESC
            LIMIT 1
        """), {"uid": user_id}).first()
        if rows:
            return (rows[0], rows[1])
    return (None, None)

def evaluate_item(item_id: int, user_id: int, force: bool = False):
    """Decision for a single scm_inventory_items record."""
    with DB_ENGINE.connect() as conn:
        s = conn.execute(text("""
            SELECT auto_reorder, approval_required
            FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :uid
        """), {"item_id": item_id, "uid": user_id}).first()
        if not s:
            return {"error": "Item not found"}
    if not force and not s[0]:
        return {"should_reorder": False, "reason": "Auto-reorder disabled"}

    stock = get_current_stock(item_id, user_id)
    eoq, rop, ss = compute_reorder_params(item_id, user_id)
    if rop is None:
        return {"should_reorder": False, "reason": "Missing demand data"}

    if stock <= rop:
        suggested_qty = max(eoq, rop - stock + 1)
        sup_id, sup_name = select_best_supplier(item_id, user_id)
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO scm_suggested_orders (item_id, suggested_quantity, supplier_id, supplier_name, reason, status)
                VALUES (:item_id, :qty, :sup_id, :sup_name, :reason, 'pending')
            """), {"item_id": item_id, "qty": suggested_qty, "sup_id": sup_id, "sup_name": sup_name,
                  "reason": f"Stock {stock} ≤ ROP {rop}"})
        return {"should_reorder": True, "suggested_quantity": suggested_qty, "supplier_id": sup_id, "supplier_name": sup_name}
    return {"should_reorder": False, "reason": f"Stock {stock} > ROP {rop}"}

def run_decision_engine(user_id: int):
    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("SELECT id FROM scm_inventory_items WHERE user_id = :uid"), {"uid": user_id}).mappings().all()
    results = []
    for it in items:
        res = evaluate_item(it["id"], user_id)
        if res.get("should_reorder"):
            results.append(res)
    return results
