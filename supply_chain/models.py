"""
supply_chain/models.py
───────────────────────
NO Flask-SQLAlchemy ORM here — GrowEasy uses raw SQL with DB_ENGINE.

This file contains:
  1. The CREATE TABLE SQL strings (called from db.py create_all_tables)
  2. Python dataclasses as typed return containers (no DB dependency)

IMPORTANT: If your tables already exist, run migration_scm_v2.sql instead
of dropping and recreating — it uses ALTER TABLE IF NOT EXISTS.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime


# ─────────────────────────────────────────────────────────
# Table creation SQL
# ─────────────────────────────────────────────────────────

CREATE_SCM_INVENTORY_ITEMS = """
    CREATE TABLE IF NOT EXISTS scm_inventory_items (
        id               SERIAL PRIMARY KEY,
        user_id          INTEGER NOT NULL,

        -- Item identity
        name             TEXT NOT NULL,
        sku              TEXT,
        unit             TEXT DEFAULT 'pcs',

        -- Demand inputs (entered via EOQ form)
        annual_demand    NUMERIC(14,4) NOT NULL,
        daily_demand_avg NUMERIC(14,4),
        daily_demand_std NUMERIC(14,4) DEFAULT 0,

        -- Cost inputs
        ordering_cost    NUMERIC(14,2) NOT NULL,
        holding_cost_pct NUMERIC(6,4)  NOT NULL,   -- stored as fraction e.g. 0.20
        unit_cost        NUMERIC(14,2) NOT NULL,

        -- Lead time inputs
        lead_time_days_avg  NUMERIC(10,2) NOT NULL,
        lead_time_days_std  NUMERIC(10,2) DEFAULT 0,

        -- Service level (z-score, e.g. 1.645 = 95%)
        service_level_z  NUMERIC(6,4)  DEFAULT 1.645,

        -- Computed results (stored after Calculate)
        eoq              NUMERIC(14,4),
        rop              NUMERIC(14,4),
        safety_stock     NUMERIC(14,4),
        annual_order_cost NUMERIC(14,2),
        annual_hold_cost  NUMERIC(14,2),
        total_cost        NUMERIC(14,2),

        -- Live stock (updated by user or future sync)
        current_stock    NUMERIC(14,4) DEFAULT 0,

        -- ABC classification (set by classify_abc_items)
        abc_class        TEXT,  -- 'A', 'B', or 'C'

        -- Policy (set by assign_default_policies, can be overridden)
        auto_reorder                   BOOLEAN DEFAULT TRUE,
        approval_required              BOOLEAN DEFAULT FALSE,
        policy_service_level           NUMERIC(6,4),
        policy_safety_stock_multiplier NUMERIC(6,4) DEFAULT 1.0,
        review_frequency_days          INTEGER,

        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_scm_inv_user_abc
        ON scm_inventory_items (user_id, abc_class);

    CREATE INDEX IF NOT EXISTS idx_scm_inv_user_stock
        ON scm_inventory_items (user_id, current_stock, rop);
"""

CREATE_SUPPLIER_KPIS = """
    CREATE TABLE IF NOT EXISTS supplier_kpis (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        supplier_name   TEXT NOT NULL,
        supplier_code   TEXT,
        category        TEXT,
        period          TEXT NOT NULL,
        on_time_delivery_pct    NUMERIC(6,2),
        quality_acceptance_pct  NUMERIC(6,2),
        invoice_accuracy_pct    NUMERIC(6,2),
        lead_time_adherence_pct NUMERIC(6,2),
        responsiveness_score    NUMERIC(4,2),
        compliance_score        NUMERIC(4,2),
        composite_score         NUMERIC(6,2),
        notes           TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""

CREATE_LANDED_COSTS = """
    CREATE TABLE IF NOT EXISTS landed_costs (
        id              SERIAL PRIMARY KEY,
        user_id         INTEGER NOT NULL,
        reference_no    TEXT,
        description     TEXT,
        currency        TEXT DEFAULT 'PKR',
        exchange_rate   NUMERIC(12,4) DEFAULT 1.0,
        product_cost    NUMERIC(16,2) NOT NULL,
        quantity        NUMERIC(14,4) NOT NULL,
        freight_cost    NUMERIC(14,2) DEFAULT 0,
        insurance_cost  NUMERIC(14,2) DEFAULT 0,
        customs_duty_pct    NUMERIC(6,4) DEFAULT 0,
        additional_duty_pct NUMERIC(6,4) DEFAULT 0,
        sales_tax_pct       NUMERIC(6,4) DEFAULT 0.17,
        withholding_tax_pct NUMERIC(6,4) DEFAULT 0,
        clearing_charges    NUMERIC(14,2) DEFAULT 0,
        port_handling       NUMERIC(14,2) DEFAULT 0,
        inland_freight      NUMERIC(14,2) DEFAULT 0,
        other_charges       NUMERIC(14,2) DEFAULT 0,
        total_landed_cost    NUMERIC(16,2),
        landed_cost_per_unit NUMERIC(14,4),
        duty_amount          NUMERIC(14,2),
        tax_amount           NUMERIC(14,2),
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
"""

CREATE_SCM_SUGGESTED_ORDERS = """
    CREATE TABLE IF NOT EXISTS scm_suggested_orders (
        id                  SERIAL PRIMARY KEY,
        item_id             INTEGER NOT NULL REFERENCES scm_inventory_items(id),
        suggested_quantity  INTEGER NOT NULL,
        supplier_id         INTEGER,
        supplier_name       TEXT,
        reason              TEXT,
        status              TEXT DEFAULT 'pending',  -- pending | approved | rejected
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_scm_sug_item_status
        ON scm_suggested_orders (item_id, status);
"""


# ─────────────────────────────────────────────────────────
# Typed row containers
# ─────────────────────────────────────────────────────────

@dataclass
class SCMInventoryRow:
    id: int
    user_id: int
    name: str
    sku: Optional[str]
    unit: str
    annual_demand: float
    daily_demand_avg: float
    daily_demand_std: float
    ordering_cost: float
    holding_cost_pct: float
    unit_cost: float
    lead_time_days_avg: float
    lead_time_days_std: float
    service_level_z: float
    eoq: Optional[float]
    rop: Optional[float]
    safety_stock: Optional[float]
    annual_order_cost: Optional[float]
    annual_hold_cost: Optional[float]
    total_cost: Optional[float]
    current_stock: Optional[float]
    abc_class: Optional[str]
    auto_reorder: Optional[bool]
    approval_required: Optional[bool]
    policy_service_level: Optional[float]
    policy_safety_stock_multiplier: Optional[float]
    review_frequency_days: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @classmethod
    def from_row(cls, row):
        return cls(**{k: row._mapping[k] for k in row._fields})


@dataclass
class SupplierKPIRow:
    id: int
    user_id: int
    supplier_name: str
    supplier_code: Optional[str]
    category: Optional[str]
    period: str
    on_time_delivery_pct: Optional[float]
    quality_acceptance_pct: Optional[float]
    invoice_accuracy_pct: Optional[float]
    lead_time_adherence_pct: Optional[float]
    responsiveness_score: Optional[float]
    compliance_score: Optional[float]
    composite_score: Optional[float]
    notes: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @classmethod
    def from_row(cls, row):
        return cls(**{k: row._mapping[k] for k in row._fields})


@dataclass
class LandedCostRow:
    id: int
    user_id: int
    reference_no: Optional[str]
    description: Optional[str]
    currency: str
    exchange_rate: float
    product_cost: float
    quantity: float
    freight_cost: float
    insurance_cost: float
    customs_duty_pct: float
    additional_duty_pct: float
    sales_tax_pct: float
    withholding_tax_pct: float
    clearing_charges: float
    port_handling: float
    inland_freight: float
    other_charges: float
    total_landed_cost: Optional[float]
    landed_cost_per_unit: Optional[float]
    duty_amount: Optional[float]
    tax_amount: Optional[float]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @classmethod
    def from_row(cls, row):
        return cls(**{k: row._mapping[k] for k in row._fields})
