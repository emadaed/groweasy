"""
supply_chain/forms.py
──────────────────────
WTForms for all three SCM modules.
Uses Flask-WTF so CSRF is free.
"""

from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, FloatField, TextAreaField,
    IntegerField, BooleanField
)
from wtforms.validators import DataRequired, Optional, NumberRange


# ─────────────────────────────────────────────
# Inventory / EOQ / ROP / Safety Stock
# ─────────────────────────────────────────────
SERVICE_LEVEL_CHOICES = [
    ("0.842",  "80%"),
    ("1.036",  "85%"),
    ("1.282",  "90%"),
    ("1.645",  "95%"),
    ("1.881",  "97%"),
    ("2.326",  "99%"),
    ("3.090",  "99.9%"),
]


class InventoryItemForm(FlaskForm):
    name             = StringField("Item Name",           validators=[DataRequired()])
    sku              = StringField("SKU",                  validators=[Optional()])
    unit             = StringField("Unit (e.g. kg, pcs)", default="pcs")

    # ── Live stock (used by decision engine) ──
    current_stock    = FloatField("Current Stock (units on hand)",
                                  default=0,
                                  validators=[Optional(), NumberRange(min=0)])

    # ── Demand ──
    annual_demand        = FloatField("Annual Demand (units/year)",
                                      validators=[DataRequired(), NumberRange(min=0.01)])
    daily_demand_avg     = FloatField("Avg Daily Demand (units/day)",
                                      validators=[DataRequired(), NumberRange(min=0)])
    daily_demand_std     = FloatField("Daily Demand Std Dev",
                                      default=0,
                                      validators=[Optional(), NumberRange(min=0)])

    # ── Cost ──
    ordering_cost        = FloatField("Ordering Cost (PKR/order)",
                                      validators=[DataRequired(), NumberRange(min=0)])
    unit_cost            = FloatField("Unit Cost (PKR)",
                                      validators=[DataRequired(), NumberRange(min=0.01)])
    holding_cost_pct     = FloatField(
        "Annual Holding Cost % (e.g. 20 for 20%)",
        validators=[DataRequired(), NumberRange(min=0.1, max=100)]
    )

    # ── Lead time ──
    lead_time_days_avg   = FloatField("Avg Lead Time (days)",
                                      validators=[DataRequired(), NumberRange(min=0)])
    lead_time_days_std   = FloatField("Lead Time Std Dev (days)",
                                      default=0,
                                      validators=[Optional(), NumberRange(min=0)])

    service_level_z      = SelectField("Service Level",
                                       choices=SERVICE_LEVEL_CHOICES,
                                       default="1.645")


# ─────────────────────────────────────────────
# Supplier KPI
# ─────────────────────────────────────────────
class SupplierKPIForm(FlaskForm):
    supplier_name  = StringField("Supplier Name",        validators=[DataRequired()])
    supplier_code  = StringField("Supplier Code",        validators=[Optional()])
    category       = StringField("Category",              validators=[Optional()])
    period         = StringField("Period (e.g. 2025-Q2)", validators=[DataRequired()])

    on_time_delivery_pct    = FloatField("On-Time Delivery %",
                                         validators=[DataRequired(), NumberRange(0, 100)])
    quality_acceptance_pct  = FloatField("Quality Acceptance %",
                                         validators=[DataRequired(), NumberRange(0, 100)])
    invoice_accuracy_pct    = FloatField("Invoice Accuracy %",
                                         validators=[DataRequired(), NumberRange(0, 100)])
    lead_time_adherence_pct = FloatField("Lead Time Adherence %",
                                         validators=[DataRequired(), NumberRange(0, 100)])
    responsiveness_score    = FloatField("Responsiveness (1–10)",
                                         validators=[DataRequired(), NumberRange(1, 10)])
    compliance_score        = FloatField("Compliance (1–10)",
                                         validators=[DataRequired(), NumberRange(1, 10)])

    notes = TextAreaField("Notes", validators=[Optional()])


# ─────────────────────────────────────────────
# Landed Cost
# ─────────────────────────────────────────────
CURRENCY_CHOICES = [
    ("PKR", "PKR – Pakistani Rupee"),
    ("USD", "USD – US Dollar"),
    ("EUR", "EUR – Euro"),
    ("GBP", "GBP – British Pound"),
    ("CNY", "CNY – Chinese Yuan"),
    ("AED", "AED – UAE Dirham"),
    ("SAR", "SAR – Saudi Riyal"),
]


class LandedCostForm(FlaskForm):
    reference_no  = StringField("Reference / PO No.",   validators=[Optional()])
    description   = StringField("Description",           validators=[Optional()])

    currency      = SelectField("Purchase Currency",     choices=CURRENCY_CHOICES, default="USD")
    exchange_rate = FloatField("Exchange Rate to PKR",
                               validators=[DataRequired(), NumberRange(min=0.0001)],
                               default=278.0)

    product_cost  = FloatField("Product / FOB Cost (in selected currency)",
                               validators=[DataRequired(), NumberRange(min=0)])
    quantity      = FloatField("Quantity",
                               validators=[DataRequired(), NumberRange(min=0.0001)])

    freight_cost      = FloatField("Freight Cost (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])
    insurance_cost    = FloatField("Insurance (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])

    customs_duty_pct      = FloatField("Customs Duty %",
                                       default=0,  validators=[Optional(), NumberRange(0, 100)])
    additional_duty_pct   = FloatField("Additional Duty % (CESS / RD)",
                                       default=0,  validators=[Optional(), NumberRange(0, 100)])
    sales_tax_pct         = FloatField("Sales Tax / GST %",
                                       default=17, validators=[Optional(), NumberRange(0, 100)])
    withholding_tax_pct   = FloatField("Withholding Tax %",
                                       default=0,  validators=[Optional(), NumberRange(0, 100)])

    clearing_charges  = FloatField("Clearing Charges (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])
    port_handling     = FloatField("Port Handling (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])
    inland_freight    = FloatField("Inland Freight (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])
    other_charges     = FloatField("Other Charges (PKR)",
                                   default=0, validators=[Optional(), NumberRange(min=0)])
