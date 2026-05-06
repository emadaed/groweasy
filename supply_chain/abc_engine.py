"""
supply_chain/abc_engine.py
───────────────────────────
Inventory Intelligence Engine — Classify → Control → Compute → Decide → Act

ARCHITECTURE NOTE:
  scm_inventory_items is a STANDALONE table (data entered via EOQ form).
  It has NO FK to inventory_items.  This engine works entirely from
  scm_inventory_items columns — no JOINs to other modules.

STAGES:
  1. classify_abc_items()      → ABC class by inventory value (Pareto)
  2. assign_default_policies() → set service-level / reorder policy per class
  3. compute_reorder_params()  → EOQ / ROP / SS using utils.py math + policy
  4. evaluate_item()           → compare stock vs ROP, create suggestion
  5. run_decision_engine()     → batch run for all user items (A→B→C order)
"""

import logging
from decimal import Decimal
from sqlalchemy import text
from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
# STAGE 1 — CLASSIFY  (ABC Layer)
# ═══════════════════════════════════════════════════════

def classify_abc_items(user_id: int) -> int:
    """
    Classify all user items by inventory value = annual_demand × unit_cost.
    Sorted descending → cumulative share → A (top 70%), B (next 20%), C (last 10%).

    Uses a simple per-row UPDATE loop — reliable, readable, debuggable.
    Returns count of items classified.
    """
    with DB_ENGINE.begin() as conn:
        rows = conn.execute(text("""
            SELECT id,
                   COALESCE(annual_demand, 0) * COALESCE(unit_cost, 0) AS inv_value
            FROM   scm_inventory_items
            WHERE  user_id = :uid
        """), {"uid": user_id}).mappings().all()

        if not rows:
            logger.warning("classify_abc: no items for user_id=%s", user_id)
            return 0

        total = sum(Decimal(str(r["inv_value"])) for r in rows)

        if total == 0:
            # No value data yet — tag everything C so policies still apply
            for row in rows:
                conn.execute(text("""
                    UPDATE scm_inventory_items SET abc_class = 'C'
                    WHERE  id = :id AND user_id = :uid
                """), {"id": row["id"], "uid": user_id})
            logger.warning("classify_abc: all items have 0 value — tagged C for user_id=%s", user_id)
            return len(rows)

        sorted_rows = sorted(rows, key=lambda x: x["inv_value"], reverse=True)

        cum = Decimal("0")
        for row in sorted_rows:
            cum += Decimal(str(row["inv_value"])) / total * 100
            abc = "A" if cum <= 70 else ("B" if cum <= 90 else "C")
            conn.execute(text("""
                UPDATE scm_inventory_items
                SET    abc_class = :abc
                WHERE  id = :id AND user_id = :uid
            """), {"abc": abc, "id": row["id"], "uid": user_id})

    logger.info("classify_abc: classified %d items for user_id=%s", len(sorted_rows), user_id)
    return len(sorted_rows)


# ═══════════════════════════════════════════════════════
# STAGE 2 — CONTROL BEHAVIOR  (Policy Layer)
# ═══════════════════════════════════════════════════════

_ABC_POLICIES = {
    "A": {
        "service_level":           0.95,   # z ≈ 1.645
        "safety_stock_multiplier": 1.0,
        "review_frequency_days":   7,
        "auto_reorder":            True,
        "approval_required":       True,   # high-value: human approves PO
    },
    "B": {
        "service_level":           0.90,   # z ≈ 1.282
        "safety_stock_multiplier": 1.2,
        "review_frequency_days":   15,
        "auto_reorder":            True,
        "approval_required":       False,
    },
    "C": {
        "service_level":           0.85,   # z ≈ 1.036
        "safety_stock_multiplier": 1.5,    # bulk buffer, low monitoring cost
        "review_frequency_days":   30,
        "auto_reorder":            False,  # manual decision for low-value items
        "approval_required":       False,
    },
}


def assign_default_policies(user_id: int, override_existing: bool = False) -> int:
    """
    Write policy defaults to scm_inventory_items based on abc_class.
    override_existing=False  → safe: fills NULLs only (production default).
    override_existing=True   → replaces all policies (run after fresh re-classify).
    Returns total rows updated.
    """
    updated = 0
    with DB_ENGINE.begin() as conn:
        for abc, p in _ABC_POLICIES.items():
            params = {
                "sl":  p["service_level"],
                "ssm": p["safety_stock_multiplier"],
                "rfd": p["review_frequency_days"],
                "ar":  p["auto_reorder"],
                "apr": p["approval_required"],
                "uid": user_id,
                "abc": abc,
            }
            if override_existing:
                result = conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET    policy_service_level           = :sl,
                           policy_safety_stock_multiplier = :ssm,
                           review_frequency_days          = :rfd,
                           auto_reorder                   = :ar,
                           approval_required              = :apr
                    WHERE  user_id = :uid AND abc_class = :abc
                """), params)
            else:
                result = conn.execute(text("""
                    UPDATE scm_inventory_items
                    SET    policy_service_level           = COALESCE(policy_service_level, :sl),
                           policy_safety_stock_multiplier = COALESCE(policy_safety_stock_multiplier, :ssm),
                           review_frequency_days          = COALESCE(review_frequency_days, :rfd),
                           auto_reorder                   = COALESCE(auto_reorder, :ar),
                           approval_required              = COALESCE(approval_required, :apr)
                    WHERE  user_id = :uid AND abc_class = :abc
                """), params)
            updated += result.rowcount

    logger.info("assign_policies: %d rows updated for user_id=%s", updated, user_id)
    return updated


# ═══════════════════════════════════════════════════════
# STAGE 3 — COMPUTE  (Math Layer)
# ═══════════════════════════════════════════════════════

def get_current_stock(item_id: int, user_id: int) -> float:
    """Read current_stock from scm_inventory_items directly."""
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT COALESCE(current_stock, 0)
            FROM   scm_inventory_items
            WHERE  id = :id AND user_id = :uid
        """), {"id": item_id, "uid": user_id}).first()
    return float(row[0]) if row else 0.0


def compute_reorder_params(item_id: int, user_id: int):
    """
    Compute (eoq, rop, safety_stock) from stored scm_inventory_items columns.

    Policy columns take precedence over manual z-score:
      effective_z  = policy_service_level  → else service_level_z → else 1.645
      ss_mult      = policy_safety_stock_multiplier → else 1.0

    Returns (eoq, rop, ss)  or  (None, None, None) if data is insufficient.
    """
    from .utils import calc_eoq, calc_safety_stock, calc_rop

    with DB_ENGINE.connect() as conn:
        s = conn.execute(text("""
            SELECT
                COALESCE(annual_demand, 0)          AS annual_demand,
                COALESCE(ordering_cost, 0)          AS ordering_cost,
                COALESCE(unit_cost, 0)              AS unit_cost,
                COALESCE(holding_cost_pct, 0)       AS holding_cost_pct,
                COALESCE(daily_demand_avg, 0)       AS daily_demand_avg,
                COALESCE(daily_demand_std, 0)       AS daily_demand_std,
                COALESCE(lead_time_days_avg, 0)     AS lead_time_days_avg,
                COALESCE(lead_time_days_std, 0)     AS lead_time_days_std,
                COALESCE(policy_service_level,
                         service_level_z,
                         1.645)                     AS effective_z,
                COALESCE(policy_safety_stock_multiplier, 1.0) AS ss_mult
            FROM scm_inventory_items
            WHERE id = :id AND user_id = :uid
        """), {"id": item_id, "uid": user_id}).mappings().first()

    if not s:
        return None, None, None

    D = float(s["annual_demand"])
    S = float(s["ordering_cost"])
    C = float(s["unit_cost"])
    H = float(s["holding_cost_pct"])

    if D <= 0 or H <= 0 or C <= 0:
        return None, None, None

    try:
        eoq = calc_eoq(D, S, C, H)
    except ValueError as e:
        logger.debug("calc_eoq failed item_id=%s: %s", item_id, e)
        return None, None, None

    d_avg  = float(s["daily_demand_avg"])
    d_std  = float(s["daily_demand_std"])
    lt_avg = float(s["lead_time_days_avg"])
    lt_std = float(s["lead_time_days_std"])
    z      = float(s["effective_z"])
    mult   = float(s["ss_mult"])

    if d_avg <= 0 or lt_avg <= 0:
        # EOQ calculable but ROP/SS not — return partial result
        return int(eoq), None, None

    ss  = calc_safety_stock(z, lt_avg, lt_std, d_avg, d_std) * mult
    rop = calc_rop(d_avg, lt_avg, ss)

    return int(eoq), round(rop, 2), round(ss, 2)


# ═══════════════════════════════════════════════════════
# STAGE 4 — DECIDE  (Decision Layer)
# ═══════════════════════════════════════════════════════

def select_best_supplier(user_id: int) -> tuple:
    """
    Return (supplier_id=None, supplier_name) of the highest-KPI supplier.
    Uses supplier_kpis table — native to this SCM module.
    Returns (None, None) if no KPI data exists.
    """
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT DISTINCT ON (supplier_name)
                supplier_name,
                composite_score
            FROM   supplier_kpis
            WHERE  user_id = :uid
            ORDER  BY supplier_name, composite_score DESC NULLS LAST
        """), {"uid": user_id}).first()

    if row:
        return (None, row[0])
    return (None, None)


def evaluate_item(item_id: int, user_id: int, force: bool = False) -> dict:
    """
    Evaluate one item.  Creates a pending suggestion if stock ≤ ROP.

    force=True  → ignores auto_reorder flag (used for manual dashboard runs).
    Duplicate guard: won't insert if a 'pending' suggestion already exists.

    Returns a result dict with should_reorder bool + details.
    """
    with DB_ENGINE.connect() as conn:
        s = conn.execute(text("""
            SELECT name, sku,
                   COALESCE(auto_reorder, TRUE)      AS auto_reorder,
                   COALESCE(approval_required, FALSE) AS approval_required
            FROM   scm_inventory_items
            WHERE  id = :id AND user_id = :uid
        """), {"id": item_id, "uid": user_id}).mappings().first()

    if not s:
        return {"should_reorder": False, "reason": "Item not found"}

    if not force and not s["auto_reorder"]:
        return {"should_reorder": False, "reason": "Auto-reorder disabled"}

    stock = get_current_stock(item_id, user_id)
    eoq, rop, ss = compute_reorder_params(item_id, user_id)

    if rop is None:
        return {
            "should_reorder": False,
            "reason": "Insufficient data — set daily_demand_avg and lead_time_days_avg",
        }

    if stock <= rop:
        sup_id, sup_name = select_best_supplier(user_id)

        # Duplicate guard — avoid spamming suggestions
        with DB_ENGINE.connect() as conn:
            already_pending = conn.execute(text("""
                SELECT id FROM scm_suggested_orders
                WHERE  item_id = :item_id AND status = 'pending'
                LIMIT  1
            """), {"item_id": item_id}).first()

        if not already_pending:
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    INSERT INTO scm_suggested_orders
                        (item_id, suggested_quantity, supplier_id, supplier_name, reason, status)
                    VALUES
                        (:item_id, :qty, :sup_id, :sup_name, :reason, 'pending')
                """), {
                    "item_id":  item_id,
                    "qty":      eoq,
                    "sup_id":   sup_id,
                    "sup_name": sup_name,
                    "reason":   f"Stock {stock:.0f} ≤ ROP {rop:.0f}",
                })

        return {
            "should_reorder":     True,
            "item_id":            item_id,
            "item_name":          s["name"],
            "current_stock":      stock,
            "rop":                rop,
            "suggested_quantity": eoq,
            "supplier_name":      sup_name,
            "approval_required":  s["approval_required"],
        }

    return {
        "should_reorder": False,
        "reason": f"Stock {stock:.0f} > ROP {rop:.0f}",
    }


# ═══════════════════════════════════════════════════════
# STAGE 5 — ACT  (Batch Run)
# ═══════════════════════════════════════════════════════

def run_decision_engine(user_id: int, force: bool = False) -> list:
    """
    Run the full decision cycle for all items belonging to user_id.
    Items are processed in ABC priority order (A first — highest value risk).

    force=True  → evaluates all items regardless of auto_reorder flag.
                  Use for manual dashboard runs.

    Returns list of dicts for items that triggered a reorder suggestion.
    """
    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id FROM scm_inventory_items
            WHERE  user_id = :uid
            ORDER  BY
                CASE abc_class WHEN 'A' THEN 1 WHEN 'B' THEN 2 ELSE 3 END,
                id
        """), {"uid": user_id}).mappings().all()

    if not items:
        logger.warning("run_decision_engine: no items for user_id=%s", user_id)
        return []

    results = []
    errors  = 0
    for it in items:
        try:
            res = evaluate_item(it["id"], user_id, force=force)
            if res.get("should_reorder"):
                results.append(res)
        except Exception as exc:
            errors += 1
            logger.error("evaluate_item failed item_id=%s user_id=%s: %s",
                         it["id"], user_id, exc, exc_info=True)

    logger.info(
        "run_decision_engine: user_id=%s | items=%d | suggestions=%d | errors=%d",
        user_id, len(items), len(results), errors
    )
    return results
