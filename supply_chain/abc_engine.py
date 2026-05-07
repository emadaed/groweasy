"""
supply_chain/abc_engine.py  — v2.0  (GrowEasy Edition)
═══════════════════════════════════════════════════════
ABC Inventory Intelligence Engine wired to GrowEasy's raw-SQL stack.

Pipeline:  Classify → Control → Compute → Decide → Act

Reads from:   inventory_items, stock_movements
Writes to:    scm_item_classifications, scm_suggested_orders, scm_engine_run_logs

Requires:  migration_scm_v3.sql run before deployment.
           numpy + scipy in requirements.txt.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import numpy as np
from scipy import stats
from sqlalchemy import text

from app.services.db import DB_ENGINE

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Enums & Constants
# ═══════════════════════════════════════════════════════════════

class ABCClass(str, Enum):
    A            = "A"
    B            = "B"
    C            = "C"
    UNCLASSIFIED = "U"   # CHAR(1) column — "U" for unclassified


DEFAULT_ABC_THRESHOLDS = {"A": 0.70, "B": 0.90}

DEFAULT_ABC_POLICIES: dict[ABCClass, dict] = {
    ABCClass.A: {
        "service_level":       0.95,
        "lead_time_days":      7,
        "review_cycle_days":   7,
        "safety_stock_method": "dynamic_z",
        "eoq_enabled":         True,
        "min_order_qty":       1,
    },
    ABCClass.B: {
        "service_level":       0.90,
        "lead_time_days":      15,
        "review_cycle_days":   15,
        "safety_stock_method": "dynamic_z",
        "eoq_enabled":         True,
        "min_order_qty":       1,
    },
    ABCClass.C: {
        "service_level":       0.85,
        "lead_time_days":      30,
        "review_cycle_days":   30,
        "safety_stock_method": "fixed_days",
        "fixed_safety_days":   7,
        "eoq_enabled":         False,
        "min_order_qty":       1,
    },
    ABCClass.UNCLASSIFIED: {
        "service_level":       0.85,
        "lead_time_days":      14,
        "review_cycle_days":   30,
        "safety_stock_method": "fixed_days",
        "fixed_safety_days":   14,
        "eoq_enabled":         False,
        "min_order_qty":       1,
    },
}

# Cost defaults applied when inventory_items has no linked scm_inventory_items row
DEFAULT_ORDERING_COST    = 500.0   # PKR per PO (sensible Pakistani SME default)
DEFAULT_HOLDING_COST_PCT = 0.25    # 25% annual holding cost as fraction


# ═══════════════════════════════════════════════════════════════
# Data Shapes
# ═══════════════════════════════════════════════════════════════

@dataclass
class InventoryItem:
    item_id:                 int
    user_id:                 int
    sku:                     str
    description:             str
    cost_price:              float
    current_stock:           float
    ordering_cost:           float = DEFAULT_ORDERING_COST
    holding_cost_pct:        float = DEFAULT_HOLDING_COST_PCT


@dataclass
class DemandSignal:
    item_id:    int
    date:       datetime
    units_sold: float


@dataclass
class ItemClassification:
    item_id:            int
    user_id:            int
    abc_class:          ABCClass
    annual_value:       float
    avg_daily_demand:   float
    demand_std:         float
    demand_trend:       float
    seasonality_index:  float
    forecast_mape:      float | None
    model_version:      str


@dataclass
class SuggestedOrder:
    item_id:          int
    user_id:          int
    abc_class:        ABCClass
    current_stock:    float
    rop:              float
    eoq:              float
    suggested_qty:    float
    safety_stock:     float
    days_of_stock:    float
    reason_code:      str    # ROP_BREACH | SEASONAL_SPIKE | TREND_ALERT
    confidence_score: float


@dataclass
class EngineRunLog:
    run_id:              str
    user_id:             int
    triggered_by:        str
    started_at:          datetime
    finished_at:         datetime | None = None
    items_processed:     int             = 0
    suggestions_created: int             = 0
    errors:              list[str]       = field(default_factory=list)
    model_version:       str             = "2.0.0"


# ═══════════════════════════════════════════════════════════════
# Adaptive Demand Learner
# ═══════════════════════════════════════════════════════════════

class AdaptiveDemandLearner:
    """
    Robust demand signals from raw stock_movements history.

    Techniques:
      - EWMA (28-day span) for smoothed daily average
      - IQR outlier scrubbing before std computation
      - Month-over-month seasonality index
      - 14-day hold-out MAPE for forecast accuracy tracking
    """

    EWMA_SPAN = 28

    def compute_demand_profile(self, signals: list[DemandSignal], today: datetime) -> dict:
        if not signals:
            return self._cold_start_profile()

        signals = sorted(signals, key=lambda s: s.date)
        daily   = self._aggregate_to_daily(signals)

        if len(daily) < 60:
            return self._cold_start_profile(partial=daily)

        values = np.array([v for _, v in daily])

        # IQR outlier scrub
        q1, q3 = np.percentile(values, [25, 75])
        iqr    = q3 - q1
        clean  = values[(values >= q1 - 1.5 * iqr) & (values <= q3 + 1.5 * iqr)]

        # EWMA average
        alpha = 2.0 / (self.EWMA_SPAN + 1)
        ewma  = self._ewma(clean, alpha)
        avg   = float(ewma[-1])

        std = float(np.std(clean, ddof=1)) if len(clean) > 1 else avg * 0.2

        # Trend: recent 30d vs prior 30d
        recent = clean[-30:].mean() if len(clean) >= 30 else clean.mean()
        prior  = clean[-60:-30].mean() if len(clean) >= 60 else clean.mean()
        trend  = float((recent - prior) / prior * 100) if prior > 0 else 0.0

        return {
            "avg_daily_demand":  max(avg, 0.0),
            "demand_std":        max(std, 0.0),
            "demand_trend":      trend,
            "seasonality_index": self._compute_seasonality(daily, today),
            "mape":              self._compute_mape(clean),
            "is_cold_start":     False,
        }

    def _aggregate_to_daily(self, signals):
        buckets: dict[str, float] = {}
        for s in signals:
            key = s.date.date().isoformat()
            buckets[key] = buckets.get(key, 0.0) + s.units_sold
        return [(datetime.fromisoformat(k), v) for k, v in sorted(buckets.items())]

    def _ewma(self, arr, alpha):
        result    = np.empty_like(arr, dtype=float)
        result[0] = arr[0]
        for i in range(1, len(arr)):
            result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
        return result

    def _compute_seasonality(self, daily, today):
        by_month: dict[int, list[float]] = {}
        for dt, v in daily:
            by_month.setdefault(dt.month, []).append(v)
        if not by_month:
            return 1.0
        monthly_avgs = {m: np.mean(vs) for m, vs in by_month.items()}
        global_avg   = np.mean(list(monthly_avgs.values()))
        current_avg  = monthly_avgs.get(today.month, global_avg)
        return float(current_avg / global_avg) if global_avg > 0 else 1.0

    def _compute_mape(self, clean):
        if len(clean) < 15:
            return 0.0
        errors = [
            abs(a - f) / a
            for a, f in zip(clean[-14:], clean[-15:-1])
            if a > 0
        ]
        return float(np.mean(errors) * 100) if errors else 0.0

    def _cold_start_profile(self, partial=None):
        base = partial[-30:] if partial else []
        avg  = float(np.mean([v for _, v in base])) if base else 0.0
        return {
            "avg_daily_demand":  avg,
            "demand_std":        avg * 0.3,
            "demand_trend":      0.0,
            "seasonality_index": 1.0,
            "mape":              None,
            "is_cold_start":     True,
        }


# ═══════════════════════════════════════════════════════════════
# Policy Repository
# ═══════════════════════════════════════════════════════════════

class PolicyRepository:
    """Falls back to system defaults. Future: SELECT from scm_abc_policies."""

    def __init__(self):
        self._cache: dict[int, dict] = {}

    def get_policies(self, user_id: int) -> dict[ABCClass, dict]:
        if user_id not in self._cache:
            self._cache[user_id] = {k: dict(v) for k, v in DEFAULT_ABC_POLICIES.items()}
        return self._cache[user_id]


# ═══════════════════════════════════════════════════════════════
# ABC Classifier
# ═══════════════════════════════════════════════════════════════

class ABCClassifier:
    def __init__(self, thresholds: dict | None = None):
        self.thresholds = thresholds or DEFAULT_ABC_THRESHOLDS

    def classify(self, items: list[InventoryItem], profiles: dict[int, dict]) -> dict[int, ABCClass]:
        scored: list[tuple[int, float]] = []
        for item in items:
            p = profiles.get(item.item_id, {})
            if p.get("is_cold_start") or p.get("avg_daily_demand", 0) == 0:
                continue
            annual_value = p["avg_daily_demand"] * 365 * item.cost_price
            scored.append((item.item_id, annual_value))

        if not scored:
            return {item.item_id: ABCClass.UNCLASSIFIED for item in items}

        scored.sort(key=lambda x: x[1], reverse=True)
        total = sum(v for _, v in scored)

        result: dict[int, ABCClass] = {}
        if total > 0:
            cumulative = 0.0
            a_cut = self.thresholds["A"] * total
            b_cut = self.thresholds["B"] * total
            for item_id, value in scored:
                cumulative += value
                if   cumulative <= a_cut: result[item_id] = ABCClass.A
                elif cumulative <= b_cut: result[item_id] = ABCClass.B
                else:                     result[item_id] = ABCClass.C
        else:
            result = {iid: ABCClass.UNCLASSIFIED for iid, _ in scored}

        classified_ids = set(result.keys())
        for item in items:
            if item.item_id not in classified_ids:
                result[item.item_id] = ABCClass.UNCLASSIFIED

        return result


# ═══════════════════════════════════════════════════════════════
# Replenishment Calculator
# ═══════════════════════════════════════════════════════════════

class ReplenishmentCalculator:

    def compute_rop(self, profile: dict, policy: dict, item: InventoryItem) -> dict:
        lead_time  = policy["lead_time_days"]
        sl         = policy["service_level"]
        raw_demand = profile["avg_daily_demand"]
        demand_std = profile["demand_std"]
        season_idx = profile["seasonality_index"]
        trend_pct  = profile["demand_trend"]

        adj_demand   = raw_demand * season_idx * (1 + max(0.0, trend_pct / 100 / 12))

        if policy["safety_stock_method"] == "dynamic_z":
            z            = float(stats.norm.ppf(sl))
            safety_stock = z * math.sqrt(lead_time) * (demand_std * season_idx)
        else:
            safety_stock = policy.get("fixed_safety_days", 7) * adj_demand

        return {
            "rop":             max(adj_demand * lead_time + safety_stock, 0.0),
            "safety_stock":    max(safety_stock, 0.0),
            "adjusted_demand": max(adj_demand, 0.0),
        }

    def compute_eoq(self, profile: dict, item: InventoryItem, policy: dict) -> float:
        if not policy.get("eoq_enabled", False):
            return float(policy.get("min_order_qty", 1))
        annual_demand = profile["avg_daily_demand"] * 365
        H = item.cost_price * item.holding_cost_pct
        if annual_demand <= 0 or H <= 0:
            return float(policy.get("min_order_qty", 1))
        return max(math.sqrt(2 * annual_demand * item.ordering_cost / H),
                   float(policy["min_order_qty"]))


# ═══════════════════════════════════════════════════════════════
# Main Decision Engine
# ═══════════════════════════════════════════════════════════════

class ABCDecisionEngine:
    """
    Orchestrates the full ABC intelligence pipeline.
    All DB calls use GrowEasy's DB_ENGINE + text() pattern.
    """

    MODEL_VERSION = "2.0.0"

    def __init__(
        self,
        policy_repo: PolicyRepository | None        = None,
        learner:     AdaptiveDemandLearner | None   = None,
        classifier:  ABCClassifier | None           = None,
        calc:        ReplenishmentCalculator | None = None,
    ):
        self.policy_repo = policy_repo or PolicyRepository()
        self.learner     = learner     or AdaptiveDemandLearner()
        self.classifier  = classifier  or ABCClassifier()
        self.calc        = calc        or ReplenishmentCalculator()

    # ─── Public entry point ────────────────────────────────────

    def run(self, user_id: int, triggered_by: str = "manual", force: bool = False) -> EngineRunLog:
        """
        Full pipeline in one call.
        force=True → re-evaluates all items, skips duplicate-suggestion guard.
        """
        run_id  = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        log     = EngineRunLog(run_id=run_id, user_id=user_id,
                               triggered_by=triggered_by, started_at=started)

        logger.info("[Engine] START run_id=%s user=%s trigger=%s force=%s",
                    run_id, user_id, triggered_by, force)
        try:
            items    = self._load_active_items(user_id)
            signals  = self._load_demand_signals(user_id, days=365)
            policies = self.policy_repo.get_policies(user_id)

            if not items:
                logger.warning("[Engine] No active items for user_id=%s", user_id)
                return log

            today    = datetime.now(timezone.utc)
            profiles = {
                item.item_id: self.learner.compute_demand_profile(
                    [s for s in signals if s.item_id == item.item_id], today
                )
                for item in items
            }

            abc_map         = self.classifier.classify(items, profiles)
            classifications = [
                ItemClassification(
                    item_id           = item.item_id,
                    user_id           = user_id,
                    abc_class         = abc_map[item.item_id],
                    annual_value      = profiles[item.item_id]["avg_daily_demand"] * 365 * item.cost_price,
                    avg_daily_demand  = profiles[item.item_id]["avg_daily_demand"],
                    demand_std        = profiles[item.item_id]["demand_std"],
                    demand_trend      = profiles[item.item_id]["demand_trend"],
                    seasonality_index = profiles[item.item_id]["seasonality_index"],
                    forecast_mape     = profiles[item.item_id].get("mape"),
                    model_version     = self.MODEL_VERSION,
                )
                for item in items
            ]
            self._persist_classifications(classifications)

            # Evaluate A → B → C → U priority order
            priority   = [ABCClass.A, ABCClass.B, ABCClass.C, ABCClass.UNCLASSIFIED]
            by_class   = {c: [] for c in priority}
            for item in items:
                by_class[abc_map[item.item_id]].append(item)

            pending_ids  = set() if force else self._load_pending_item_ids(user_id)
            suggestions: list[SuggestedOrder] = []

            for abc_class in priority:
                for item in by_class[abc_class]:
                    log.items_processed += 1
                    try:
                        sug = self._evaluate_item(
                            item, abc_class,
                            profiles[item.item_id],
                            policies[abc_class],
                            pending_ids,
                        )
                        if sug:
                            suggestions.append(sug)
                    except Exception as exc:
                        msg = f"item_id={item.item_id}: {exc}"
                        log.errors.append(msg)
                        logger.exception("[Engine] %s", msg)

            if suggestions:
                self._persist_suggestions(suggestions)
                log.suggestions_created = len(suggestions)

        except Exception as exc:
            log.errors.append(f"fatal: {exc}")
            logger.exception("[Engine] Fatal run_id=%s", run_id)
        finally:
            log.finished_at = datetime.now(timezone.utc)
            self._persist_run_log(log)

        logger.info("[Engine] DONE run_id=%s processed=%d suggestions=%d errors=%d",
                    run_id, log.items_processed, log.suggestions_created, len(log.errors))
        return log

    # ─── Item evaluation ───────────────────────────────────────

    def _evaluate_item(
        self,
        item:        InventoryItem,
        abc_class:   ABCClass,
        profile:     dict,
        policy:      dict,
        pending_ids: set[int],
    ) -> SuggestedOrder | None:

        if item.item_id in pending_ids:
            return None

        avg_demand = profile["avg_daily_demand"]
        if avg_demand <= 0:
            return None

        rop_data      = self.calc.compute_rop(profile, policy, item)
        rop           = rop_data["rop"]
        ss            = rop_data["safety_stock"]
        eoq           = self.calc.compute_eoq(profile, item, policy)
        days_of_stock = item.current_stock / avg_demand if avg_demand > 0 else 999.0

        reason_code: str | None = None
        confidence = 0.85

        if item.current_stock <= rop:
            reason_code = "ROP_BREACH"
        elif profile["seasonality_index"] >= 1.2 and days_of_stock < 30:
            reason_code = "SEASONAL_SPIKE"
            confidence  = 0.80
        elif profile["demand_trend"] > 20 and days_of_stock < 21:
            reason_code = "TREND_ALERT"
            confidence  = 0.75

        if reason_code is None:
            return None

        if profile.get("mape") and profile["mape"] > 30:
            confidence *= 0.85
        if profile.get("is_cold_start"):
            confidence *= 0.70

        return SuggestedOrder(
            item_id          = item.item_id,
            user_id          = item.user_id,
            abc_class        = abc_class,
            current_stock    = item.current_stock,
            rop              = rop,
            eoq              = eoq,
            suggested_qty    = max(eoq, float(policy["min_order_qty"])),
            safety_stock     = ss,
            days_of_stock    = round(days_of_stock, 1),
            reason_code      = reason_code,
            confidence_score = round(confidence, 4),
        )

    # ─── DB layer ─────────────────────────────────────────────

    def _load_active_items(self, user_id: int) -> list[InventoryItem]:
        """
        Read inventory_items directly — the 1000 real items live here.
        LEFT JOIN scm_inventory_items to pick up any manually entered
        ordering_cost / holding_cost_pct if the user has linked a record.
        """
        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    i.id,
                    i.user_id,
                    COALESCE(i.sku, '')            AS sku,
                    i.name,
                    COALESCE(i.cost_price, 0)      AS cost_price,
                    COALESCE(i.current_stock, 0)   AS current_stock,
                    COALESCE(s.ordering_cost,    :def_oc) AS ordering_cost,
                    COALESCE(s.holding_cost_pct, :def_hc) AS holding_cost_pct
                FROM inventory_items i
                LEFT JOIN scm_inventory_items s
                       ON s.inventory_item_id = i.id
                      AND s.user_id = i.user_id
                WHERE i.user_id = :uid
                  AND i.is_active = TRUE
            """), {"uid": user_id, "def_oc": DEFAULT_ORDERING_COST,
                   "def_hc": DEFAULT_HOLDING_COST_PCT}).mappings().all()

        return [
            InventoryItem(
                item_id          = r["id"],
                user_id          = r["user_id"],
                sku              = r["sku"],
                description      = r["name"],
                cost_price       = float(r["cost_price"]),
                current_stock    = float(r["current_stock"]),
                ordering_cost    = float(r["ordering_cost"]),
                holding_cost_pct = float(r["holding_cost_pct"]),
            )
            for r in rows
        ]

    def _load_demand_signals(self, user_id: int, days: int = 365) -> list[DemandSignal]:
        """
        Sales from stock_movements.  quantity stored negative (outgoing) → ABS().
        """
        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT product_id,
                       ABS(quantity) AS units_sold,
                       created_at
                FROM   stock_movements
                WHERE  user_id       = :uid
                  AND  movement_type = 'sale'
                  AND  created_at   >= NOW() - INTERVAL '1 day' * :days
                ORDER  BY created_at
            """), {"uid": user_id, "days": days}).mappings().all()

        return [
            DemandSignal(
                item_id    = r["product_id"],
                date       = r["created_at"],
                units_sold = float(r["units_sold"]),
            )
            for r in rows
        ]

    def _load_pending_item_ids(self, user_id: int) -> set[int]:
        """Items that already have an open suggestion — skip to avoid duplicates."""
        with DB_ENGINE.connect() as conn:
            rows = conn.execute(text("""
                SELECT DISTINCT item_id
                FROM   scm_suggested_orders
                WHERE  user_id = :uid AND status = 'pending'
            """), {"uid": user_id}).fetchall()
        return {r[0] for r in rows}

    def _persist_classifications(self, rows: list[ItemClassification]) -> None:
        if not rows:
            return
        with DB_ENGINE.begin() as conn:
            for c in rows:
                conn.execute(text("""
                    INSERT INTO scm_item_classifications
                        (inventory_item_id, user_id, abc_class, annual_value,
                         avg_daily_demand, demand_std, demand_trend,
                         seasonality_index, forecast_mape, model_version, updated_at)
                    VALUES
                        (:iid, :uid, :abc, :av,
                         :ad, :ds, :dt,
                         :si, :mape, :mv, CURRENT_TIMESTAMP)
                    ON CONFLICT (inventory_item_id, user_id) DO UPDATE SET
                        abc_class         = EXCLUDED.abc_class,
                        annual_value      = EXCLUDED.annual_value,
                        avg_daily_demand  = EXCLUDED.avg_daily_demand,
                        demand_std        = EXCLUDED.demand_std,
                        demand_trend      = EXCLUDED.demand_trend,
                        seasonality_index = EXCLUDED.seasonality_index,
                        forecast_mape     = EXCLUDED.forecast_mape,
                        model_version     = EXCLUDED.model_version,
                        updated_at        = CURRENT_TIMESTAMP
                """), {
                    "iid":  c.item_id, "uid": c.user_id,
                    "abc":  c.abc_class.value,
                    "av":   round(c.annual_value, 2),
                    "ad":   round(c.avg_daily_demand, 4),
                    "ds":   round(c.demand_std, 4),
                    "dt":   round(c.demand_trend, 2),
                    "si":   round(c.seasonality_index, 4),
                    "mape": round(c.forecast_mape, 2) if c.forecast_mape else None,
                    "mv":   c.model_version,
                })

    def _persist_suggestions(self, rows: list[SuggestedOrder]) -> None:
        if not rows:
            return
        with DB_ENGINE.begin() as conn:
            for s in rows:
                reason_text = (
                    f"Stock {s.current_stock:.0f} ≤ ROP {s.rop:.0f}"
                    if s.reason_code == "ROP_BREACH"
                    else f"{s.reason_code}: {s.days_of_stock:.0f} days of stock"
                )
                conn.execute(text("""
                    INSERT INTO scm_suggested_orders (
                        item_id, user_id, suggested_quantity,
                        reason, reason_code, abc_class,
                        confidence_score, days_of_stock,
                        rop, eoq, safety_stock,
                        current_stock_at_suggestion, status
                    ) VALUES (
                        :iid, :uid, :qty,
                        :reason, :rcode, :abc,
                        :conf, :days,
                        :rop, :eoq, :ss,
                        :stock, 'pending'
                    )
                """), {
                    "iid":   s.item_id,  "uid":   s.user_id,
                    "qty":   int(s.suggested_qty),
                    "reason": reason_text,
                    "rcode": s.reason_code,
                    "abc":   s.abc_class.value,
                    "conf":  s.confidence_score,
                    "days":  s.days_of_stock,
                    "rop":   round(s.rop, 2),
                    "eoq":   round(s.eoq, 2),
                    "ss":    round(s.safety_stock, 2),
                    "stock": s.current_stock,
                })

    def _persist_run_log(self, log: EngineRunLog) -> None:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO scm_engine_run_logs (
                    run_id, user_id, triggered_by, started_at, finished_at,
                    items_processed, suggestions_created, errors, model_version
                ) VALUES (
                    :rid, :uid, :trigger, :started, :finished,
                    :processed, :created, :errors::jsonb, :model
                )
                ON CONFLICT (run_id) DO NOTHING
            """), {
                "rid":       log.run_id,       "uid":      log.user_id,
                "trigger":   log.triggered_by, "started":  log.started_at,
                "finished":  log.finished_at,
                "processed": log.items_processed,
                "created":   log.suggestions_created,
                "errors":    json.dumps(log.errors),
                "model":     log.model_version,
            })


# ═══════════════════════════════════════════════════════════════
# Public factory — the only import routes.py needs
# ═══════════════════════════════════════════════════════════════

def build_decision_engine() -> ABCDecisionEngine:
    """
    Wire up the full engine. Lightweight — no DB calls at init.

    Usage in routes.py:
        from .abc_engine import build_decision_engine
        log = build_decision_engine().run(user_id=uid, triggered_by="manual")
    """
    return ABCDecisionEngine()
