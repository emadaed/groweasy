"""
ABC Analysis Policy Engine for GrowEasy
Raw SQL, no ORM. Follows existing patterns.
"""

from sqlalchemy import text
from app.services.db import DB_ENGINE
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def get_uid():
    """Helper – in real routes you pass user_id. Here we assume caller provides user_id."""
    pass  # will be passed as argument


# ─────────────────────────────────────────────────────
# 1. ABC Classification (runs on invoice_items)
# ─────────────────────────────────────────────────────
def classify_abc_items(user_id: int, alpha: float = 1.0, beta: float = 0.0):
    """
    Classify inventory items into A/B/C based on weighted score.
    alpha * revenue + beta * gross_profit (default alpha=1 → revenue only)
    """
    with DB_ENGINE.connect() as conn:
        # Get annual revenue per item from invoice_items
        # Assumes invoice_items has: inventory_item_id, quantity, unit_price, (profit? optional)
        # For gross profit we would need cost; if not available, use revenue only.
        rows = conn.execute(text("""
            SELECT ii.inventory_item_id AS item_id,
                   SUM(ii.quantity * ii.unit_price) AS revenue
            FROM invoice_items ii
            JOIN user_invoices ui ON ii.invoice_id = ui.id
            WHERE ui.user_id = :user_id
              AND ui.created_at >= NOW() - INTERVAL '12 months'
            GROUP BY ii.inventory_item_id
        """), {"user_id": user_id}).mappings().all()

        if not rows:
            logger.info("No sales history, skipping ABC classification")
            return

        total_value = sum(r["revenue"] for r in rows)
        if total_value == 0:
            return

        # Sort by revenue desc
        rows.sort(key=lambda x: x["revenue"], reverse=True)

        cumulative = 0.0
        for row in rows:
            cumulative += row["revenue"] / total_value * 100
            if cumulative <= 70:
                abc_class = 'A'
            elif cumulative <= 90:
                abc_class = 'B'
            else:
                abc_class = 'C'

            # Update scm_inventory_items
            conn.execute(text("""
                UPDATE scm_inventory_items
                SET abc_class = :abc_class
                WHERE id = :item_id AND user_id = :user_id
            """), {"abc_class": abc_class, "item_id": row["item_id"], "user_id": user_id})

    logger.info("ABC classification completed for user %s", user_id)


# ─────────────────────────────────────────────────────
# 2. Policy Assignment (based on ABC class)
# ─────────────────────────────────────────────────────
def assign_default_policies(user_id: int, override_existing: bool = False):
    """
    Assign default policy values to items based on their ABC class.
    If override_existing = False, only fill NULL values.
    """
    policies = {
        'A': {
            'service_level': 0.95,
            'safety_stock_multiplier': 1.0,
            'review_frequency_days': 7,
            'auto_reorder': True,
            'approval_required': True
        },
        'B': {
            'service_level': 0.90,
            'safety_stock_multiplier': 1.2,
            'review_frequency_days': 15,
            'auto_reorder': True,
            'approval_required': False
        },
        'C': {
            'service_level': 0.85,
            'safety_stock_multiplier': 1.5,
            'review_frequency_days': 30,
            'auto_reorder': False,
            'approval_required': False
        }
    }

    with DB_ENGINE.begin() as conn:
        for abc_class, policy in policies.items():
            if override_existing:
                # Update all items of this class
                conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET policy_service_level = :sl,
                        policy_safety_stock_multiplier = :ssm,
                        review_frequency_days = :rfd,
                        auto_reorder = :ar,
                        approval_required = :apr
                    WHERE user_id = :uid AND abc_class = :abc
                """), {
                    "sl": policy['service_level'],
                    "ssm": policy['safety_stock_multiplier'],
                    "rfd": policy['review_frequency_days'],
                    "ar": policy['auto_reorder'],
                    "apr": policy['approval_required'],
                    "uid": user_id,
                    "abc": abc_class
                })
            else:
                # Only update NULL values
                conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET policy_service_level = COALESCE(policy_service_level, :sl),
                        policy_safety_stock_multiplier = COALESCE(policy_safety_stock_multiplier, :ssm),
                        review_frequency_days = COALESCE(review_frequency_days, :rfd),
                        auto_reorder = COALESCE(auto_reorder, :ar),
                        approval_required = COALESCE(approval_required, :apr)
                    WHERE user_id = :uid AND abc_class = :abc
                """), {
                    "sl": policy['service_level'],
                    "ssm": policy['safety_stock_multiplier'],
                    "rfd": policy['review_frequency_days'],
                    "ar": policy['auto_reorder'],
                    "apr": policy['approval_required'],
                    "uid": user_id,
                    "abc": abc_class
                })

    logger.info("Policies assigned for user %s (override=%s)", user_id, override_existing)


# ─────────────────────────────────────────────────────
# 3. Get current stock level
# ─────────────────────────────────────────────────────
def get_current_stock(item_id: int, user_id: int) -> int:
    """Calculate current stock from stock_movements or fallback to manual."""
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text("""
            SELECT COALESCE(SUM(
                CASE WHEN movement_type = 'in' THEN quantity
                     WHEN movement_type = 'out' THEN -quantity
                     ELSE 0 END
            ), 0) AS stock
            FROM stock_movements
            WHERE item_id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).scalar()
    return int(result or 0)


# ─────────────────────────────────────────────────────
# 4. Demand forecasting (from invoice_items)
# ─────────────────────────────────────────────────────
def get_demand_stats(item_id: int, user_id: int, days: int = 90):
    """
    Returns avg_daily_demand, std_daily_demand from last N days.
    Also returns lead_time from scm_inventory_items.
    """
    with DB_ENGINE.connect() as conn:
        # Get lead time from item
        lead_row = conn.execute(text("""
            SELECT lead_time_days_avg FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).first()
        lead_time = float(lead_row[0]) if lead_row else 5.0

        # Demand history from invoice_items
        demand_rows = conn.execute(text("""
            SELECT ii.quantity, ui.created_at
            FROM invoice_items ii
            JOIN user_invoices ui ON ii.invoice_id = ui.id
            WHERE ii.inventory_item_id = :item_id
              AND ui.user_id = :user_id
              AND ui.created_at >= NOW() - INTERVAL ':days days'
        """), {"item_id": item_id, "user_id": user_id, "days": days}).mappings().all()

        if not demand_rows:
            return 0.0, 0.0, lead_time

        # Calculate daily demand (assume each sale is per day – we can aggregate by day)
        # Simple approach: total quantity / days
        total_qty = sum(r["quantity"] for r in demand_rows)
        avg_daily = total_qty / days

        # For std dev, we need daily aggregates. We'll keep it simple: use overall std of each transaction quantity.
        # More accurate: group by date.
        from statistics import stdev
        quantities = [r["quantity"] for r in demand_rows]
        std_daily = stdev(quantities) if len(quantities) > 1 else 0.0

        return avg_daily, std_daily, lead_time


# ─────────────────────────────────────────────────────
# 5. EOQ / ROP / Safety Stock with policy multipliers
# ─────────────────────────────────────────────────────
def compute_reorder_params(item_id: int, user_id: int):
    """Return (eoq, rop, safety_stock) using item's current policy."""
    with DB_ENGINE.connect() as conn:
        item = conn.execute(text("""
            SELECT annual_demand, ordering_cost, unit_cost, holding_cost_pct,
                   policy_service_level, policy_safety_stock_multiplier
            FROM scm_inventory_items
            WHERE id = :item_id AND user_id = :user_id
        """), {"item_id": item_id, "user_id": user_id}).mappings().first()
        if not item:
            return None, None, None

    avg_demand, std_demand, lead_time = get_demand_stats(item_id, user_id)
    if avg_demand == 0:
        return None, None, None

    # EOQ = sqrt(2 * D * S / H)
    D = item["annual_demand"]
    S = item["ordering_cost"]
    H = item["unit_cost"] * item["holding_cost_pct"]
    eoq = (2 * D * S / H) ** 0.5 if H > 0 else 0

    # Safety stock = z * sqrt(lead_time) * std_daily_demand * multiplier
    z_values = {0.85: 1.036, 0.90: 1.282, 0.95: 1.645, 0.98: 2.054}
    z = z_values.get(item["policy_service_level"], 1.645)
    safety_stock = z * (lead_time ** 0.5) * std_demand * item["policy_safety_stock_multiplier"]

    # ROP = avg_demand * lead_time + safety_stock
    rop = avg_demand * lead_time + safety_stock

    return int(eoq), int(rop), int(safety_stock)


# ─────────────────────────────────────────────────────
# 6. Best supplier selection (KPI + landed cost)
# ─────────────────────────────────────────────────────
def select_best_supplier(item_id: int, user_id: int):
    """
    For a given inventory item, find suppliers that supply this item.
    Since we don't have product‑supplier mapping, we'll assume any supplier can supply.
    Score = (KPI composite) * (1 / landed_cost_per_unit)  – normalized.
    """
    with DB_ENGINE.connect() as conn:
        # Get all suppliers with their latest composite score
        suppliers = conn.execute(text("""
            SELECT s.id, s.name, sk.composite_score
            FROM suppliers s
            LEFT JOIN (
                SELECT DISTINCT ON (supplier_name) supplier_name, composite_score
                FROM supplier_kpis
                WHERE user_id = :uid
                ORDER BY supplier_name, created_at DESC
            ) sk ON sk.supplier_name = s.name
            WHERE s.user_id = :uid
        """), {"uid": user_id}).mappings().all()

        if not suppliers:
            return None, None

        best = None
        best_score = -1
        for sup in suppliers:
            # Get latest landed cost for any product (or we could filter by product category)
            # For simplicity, use average landed cost per unit or 0
            lc = conn.execute(text("""
                SELECT landed_cost_per_unit FROM landed_costs
                WHERE user_id = :uid
                ORDER BY created_at DESC LIMIT 1
            """), {"uid": user_id}).scalar() or 100.0

            kpi = sup["composite_score"] or 50.0
            score = kpi * (1 / lc)  # lower landed cost -> higher score
            if score > best_score:
                best_score = score
                best = (sup["id"], sup["name"])

    return best  # (id, name)


# ─────────────────────────────────────────────────────
# 7. Main Decision Engine – single item
# ─────────────────────────────────────────────────────
def evaluate_item(item_id: int, user_id: int, force: bool = False):
    """
    Returns a dict with decision: should_reorder, quantity, supplier, etc.
    If force=True, ignore `auto_reorder` flag and still evaluate.
    """
    with DB_ENGINE.connect() as conn:
        item = conn.execute(text("""
            SELECT id, name, sku, auto_reorder, approval_required, abc_class,
                   lead_time_days_avg, ordering_cost, unit_cost, holding_cost_pct,
                   annual_demand, policy_service_level, policy_safety_stock_multiplier
            FROM scm_inventory_items
            WHERE id = :id AND user_id = :uid
        """), {"id": item_id, "uid": user_id}).mappings().first()
        if not item:
            return {"error": "Item not found"}

    if not force and not item["auto_reorder"]:
        return {"should_reorder": False, "reason": "Auto-reorder disabled"}

    # Get current stock
    stock = get_current_stock(item_id, user_id)

    # Compute demand stats & ROP
    avg_demand, std_demand, lead_time = get_demand_stats(item_id, user_id)
    if avg_demand == 0:
        return {"should_reorder": False, "reason": "No demand history"}

    eoq, rop, safety_stock = compute_reorder_params(item_id, user_id)
    if rop is None:
        return {"should_reorder": False, "reason": "Missing data"}

    if stock <= rop:
        # Trigger reorder
        # EOQ may be small, ensure minimum order quantity at least (rop - stock + 1)
        suggested_qty = max(eoq, rop - stock + 1)

        # Select best supplier
        supplier_id, supplier_name = select_best_supplier(item_id, user_id) or (None, None)

        # Create a suggestion in scm_suggested_orders
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO scm_suggested_orders (item_id, suggested_quantity, supplier_id, supplier_name, reason, status)
                VALUES (:item_id, :qty, :sup_id, :sup_name, :reason, :status)
            """), {
                "item_id": item_id,
                "qty": suggested_qty,
                "sup_id": supplier_id,
                "sup_name": supplier_name,
                "reason": f"Stock ({stock}) <= ROP ({rop:.0f})",
                "status": "pending"
            })
        return {
            "should_reorder": True,
            "suggested_quantity": suggested_qty,
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "reason": f"Stock {stock} ≤ ROP {rop:.0f}"
        }
    else:
        return {"should_reorder": False, "reason": f"Stock ({stock}) > ROP ({rop:.0f})"}


# ─────────────────────────────────────────────────────
# 8. Batch decision engine (for all items of a user)
# ─────────────────────────────────────────────────────
def run_decision_engine(user_id: int):
    """Evaluate all items for a user and create suggestions."""
    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id FROM scm_inventory_items
            WHERE user_id = :uid
        """), {"uid": user_id}).mappings().all()

    results = []
    for item in items:
        result = evaluate_item(item["id"], user_id)
        if result.get("should_reorder"):
            results.append(result)
    return results
