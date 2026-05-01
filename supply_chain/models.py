"""
supply_chain/models.py
───────────────────────
NO Flask-SQLAlchemy ORM here — GrowEasy uses raw SQL with DB_ENGINE.

This file contains:
  1. The CREATE TABLE SQL strings (called from db.py)
  2. Python dataclasses as typed return containers (no DB dependency)
  3. CRUD helper functions that use DB_ENGINE + text()

Import pattern matches the rest of GrowEasy exactly.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime


# ─────────────────────────────────────────────
# Table creation SQL — paste these into
# app/services/db.py → create_all_tables()
# ─────────────────────────────────────────────

CREATE_SCM_INVENTORY_ITEMS = """
    CREATE TABLE IF NOT EXISTS scm_inventory_items (
        id               SERIAL PRIMARY KEY,
        user_id          INTEGER NOT NULL,
        name             TEXT NOT NULL,
        sku              TEXT,
        unit             TEXT DEFAULT 'pcs',
        annual_demand    NUMERIC(14,4) NOT NULL,
        daily_demand_avg NUMERIC(14,4),
        daily_demand_std NUMERIC(14,4) DEFAULT 0,
        ordering_cost    NUMERIC(14,2) NOT NULL,
        holding_cost_pct NUMERIC(6,4)  NOT NULL,
        unit_cost        NUMERIC(14,2) NOT NULL,
        lead_time_days_avg  NUMERIC(10,2) NOT NULL,
        lead_time_days_std  NUMERIC(10,2) DEFAULT 0,
        service_level_z  NUMERIC(6,4)  DEFAULT 1.645,
        eoq              NUMERIC(14,4),
        rop              NUMERIC(14,4),
        safety_stock     NUMERIC(14,4),
        annual_order_cost NUMERIC(14,2),
        annual_hold_cost  NUMERIC(14,2),
        total_cost        NUMERIC(14,2),
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
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


# ─────────────────────────────────────────────
# Typed row containers (replaces ORM model objects)
# Routes return these instead of ORM instances
# ─────────────────────────────────────────────

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
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @classmethod
    def from_row(cls, row):
        """Build from a SQLAlchemy Core Row object."""
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
