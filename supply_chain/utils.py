"""
supply_chain/utils.py
──────────────────────
Pure calculation functions.  No Flask/SQLAlchemy imports here.
All values are plain Python floats so they're trivially unit-testable.
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════
# 1. INVENTORY TOOLS  (EOQ / ROP / Safety Stock)
# ═══════════════════════════════════════════════════════

@dataclass
class InventoryResult:
    eoq: float                  # Economic Order Quantity (units)
    safety_stock: float         # Safety Stock (units)
    rop: float                  # Reorder Point (units)
    annual_order_cost: float    # PKR
    annual_hold_cost: float     # PKR
    total_cost: float           # PKR
    orders_per_year: float
    cycle_time_days: float      # avg days between orders
    max_inventory: float        # SS + EOQ


def calc_eoq(
    annual_demand: float,       # D  units/year
    ordering_cost: float,       # S  PKR/order
    unit_cost: float,           # C  PKR/unit
    holding_cost_pct: float,    # i  fraction (e.g. 0.20)
) -> float:
    """Classic Wilson EOQ formula."""
    h = unit_cost * holding_cost_pct   # annual holding cost per unit
    if h <= 0 or annual_demand <= 0:
        raise ValueError("annual_demand and holding_cost must be > 0")
    return math.sqrt((2 * annual_demand * ordering_cost) / h)


def calc_safety_stock(
    z: float,                       # service-level z-score
    lead_time_days_avg: float,      # L̄
    lead_time_days_std: float,      # σL
    daily_demand_avg: float,        # d̄
    daily_demand_std: float,        # σd
) -> float:
    """
    Combined formula that accounts for uncertainty in BOTH demand and lead time.
    SS = z * sqrt(L̄ * σd² + d̄² * σL²)
    Falls back gracefully when std devs are zero.
    """
    variance = (
        lead_time_days_avg * (daily_demand_std ** 2)
        + (daily_demand_avg ** 2) * (lead_time_days_std ** 2)
    )
    return z * math.sqrt(variance)


def calc_rop(
    daily_demand_avg: float,
    lead_time_days_avg: float,
    safety_stock: float,
) -> float:
    """ROP = (average daily demand × average lead time) + safety stock"""
    return (daily_demand_avg * lead_time_days_avg) + safety_stock


def run_inventory_calculation(
    annual_demand: float,
    ordering_cost: float,
    unit_cost: float,
    holding_cost_pct: float,
    daily_demand_avg: float,
    lead_time_days_avg: float,
    z: float = 1.65,
    daily_demand_std: float = 0.0,
    lead_time_days_std: float = 0.0,
    working_days: int = 300,
) -> InventoryResult:
    """Single entry point — returns a fully populated InventoryResult."""

    eoq = calc_eoq(annual_demand, ordering_cost, unit_cost, holding_cost_pct)
    ss  = calc_safety_stock(z, lead_time_days_avg, lead_time_days_std,
                            daily_demand_avg, daily_demand_std)
    rop = calc_rop(daily_demand_avg, lead_time_days_avg, ss)

    h = unit_cost * holding_cost_pct
    annual_order_cost = (annual_demand / eoq) * ordering_cost
    annual_hold_cost  = (eoq / 2 + ss) * h
    total_cost        = annual_order_cost + annual_hold_cost

    return InventoryResult(
        eoq               = round(eoq, 2),
        safety_stock      = round(ss, 2),
        rop               = round(rop, 2),
        annual_order_cost = round(annual_order_cost, 2),
        annual_hold_cost  = round(annual_hold_cost, 2),
        total_cost        = round(total_cost, 2),
        orders_per_year   = round(annual_demand / eoq, 2),
        cycle_time_days   = round(working_days / (annual_demand / eoq), 1),
        max_inventory     = round(ss + eoq, 2),
    )


# Service-level → z-score lookup (extend as needed)
SERVICE_LEVEL_Z: dict[str, float] = {
    "80%": 0.842,
    "85%": 1.036,
    "90%": 1.282,
    "95%": 1.645,
    "97%": 1.881,
    "99%": 2.326,
    "99.9%": 3.090,
}


# ═══════════════════════════════════════════════════════
# 2. SUPPLIER KPI SCORING
# ═══════════════════════════════════════════════════════

@dataclass
class SupplierScore:
    composite: float
    grade: str
    breakdown: dict[str, float]   # {kpi_name: weighted_contribution}


# Grade thresholds
def _grade(score: float) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B"
    if score >= 60: return "C"
    return "D"


def calc_supplier_score(
    on_time_delivery: Optional[float],
    quality_acceptance: Optional[float],
    invoice_accuracy: Optional[float],
    lead_time_adherence: Optional[float],
    responsiveness: Optional[float],   # raw 1–10 → converted to 0–100
    compliance: Optional[float],       # raw 1–10 → converted to 0–100

    w_delivery:   float = 0.30,
    w_quality:    float = 0.25,
    w_invoice:    float = 0.15,
    w_lead_time:  float = 0.15,
    w_responsive: float = 0.075,
    w_compliance: float = 0.075,
) -> SupplierScore:
    """
    Weighted composite KPI score.
    Ratings already on 0–100 scale except responsiveness/compliance (1–10 → × 10).
    Missing values are skipped and weights re-normalised automatically.
    """

    def _safe(v, scale=1.0):
        return float(v) * scale if v is not None else None

    raw = {
        "On-Time Delivery":    (_safe(on_time_delivery),   w_delivery),
        "Quality Acceptance":  (_safe(quality_acceptance),  w_quality),
        "Invoice Accuracy":    (_safe(invoice_accuracy),    w_invoice),
        "Lead Time Adherence": (_safe(lead_time_adherence), w_lead_time),
        "Responsiveness":      (_safe(responsiveness, 10),  w_responsive),
        "Compliance":          (_safe(compliance, 10),       w_compliance),
    }

    present   = {k: v for k, v in raw.items() if v[0] is not None}
    total_w   = sum(v[1] for v in present.values())

    breakdown: dict[str, float] = {}
    composite = 0.0
    for name, (score, w) in present.items():
        norm_w      = w / total_w
        contribution = score * norm_w
        breakdown[name] = round(contribution, 2)
        composite   += contribution

    return SupplierScore(
        composite = round(composite, 2),
        grade     = _grade(composite),
        breakdown = breakdown,
    )


# ═══════════════════════════════════════════════════════
# 3. LANDED COST CALCULATOR
# ═══════════════════════════════════════════════════════

@dataclass
class LandedCostResult:
    product_cost_pkr:     float
    freight_insurance:    float
    cif_value:            float          # Cost + Insurance + Freight
    customs_duty:         float
    additional_duty:      float
    sales_tax:            float          # GST on assessable value
    withholding_tax:      float
    clearing_and_other:   float
    total_landed_cost:    float
    landed_cost_per_unit: float
    effective_duty_pct:   float          # total duties+taxes / product cost
    cost_breakdown: dict[str, float]     # for pie chart


def calc_landed_cost(
    product_cost: float,
    quantity: float,
    exchange_rate: float = 1.0,          # foreign → PKR

    freight_cost: float = 0,
    insurance_cost: float = 0,

    customs_duty_pct: float = 0,         # % of CIF
    additional_duty_pct: float = 0,      # % of CIF (CESS, RD, etc.)
    sales_tax_pct: float = 0.17,         # 17% GST default (Pakistan)
    withholding_tax_pct: float = 0,      # % of CIF

    clearing_charges: float = 0,
    port_handling: float = 0,
    inland_freight: float = 0,
    other_charges: float = 0,
) -> LandedCostResult:
    """
    Pakistan import landed cost model.
    All monetary inputs assumed in PKR (caller converts via exchange_rate first).
    GST is calculated on: CIF + customs duty + additional duty (assessable value).
    """

    product_cost_pkr = product_cost * exchange_rate
    cif              = product_cost_pkr + freight_cost + insurance_cost

    customs_duty     = cif * customs_duty_pct
    additional_duty  = cif * additional_duty_pct
    assessable_value = cif + customs_duty + additional_duty

    sales_tax        = assessable_value * sales_tax_pct
    withholding_tax  = cif * withholding_tax_pct

    clearing_and_other = clearing_charges + port_handling + inland_freight + other_charges

    total = (
        product_cost_pkr
        + freight_cost
        + insurance_cost
        + customs_duty
        + additional_duty
        + sales_tax
        + withholding_tax
        + clearing_and_other
    )

    per_unit = total / quantity if quantity > 0 else 0

    effective_duty_pct = (
        (customs_duty + additional_duty + sales_tax + withholding_tax)
        / product_cost_pkr * 100
        if product_cost_pkr > 0 else 0
    )

    breakdown = {
        "Product Cost":       round(product_cost_pkr, 2),
        "Freight & Insurance": round(freight_cost + insurance_cost, 2),
        "Customs Duty":       round(customs_duty, 2),
        "Additional Duty":    round(additional_duty, 2),
        "Sales Tax (GST)":    round(sales_tax, 2),
        "Withholding Tax":    round(withholding_tax, 2),
        "Clearing & Other":   round(clearing_and_other, 2),
    }

    return LandedCostResult(
        product_cost_pkr     = round(product_cost_pkr, 2),
        freight_insurance    = round(freight_cost + insurance_cost, 2),
        cif_value            = round(cif, 2),
        customs_duty         = round(customs_duty, 2),
        additional_duty      = round(additional_duty, 2),
        sales_tax            = round(sales_tax, 2),
        withholding_tax      = round(withholding_tax, 2),
        clearing_and_other   = round(clearing_and_other, 2),
        total_landed_cost    = round(total, 2),
        landed_cost_per_unit = round(per_unit, 4),
        effective_duty_pct   = round(effective_duty_pct, 2),
        cost_breakdown       = breakdown,
    )
