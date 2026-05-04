"""
supply_chain/routes.py
───────────────────────
Raw SQL via DB_ENGINE + text() — same pattern as the rest of GrowEasy.
No Flask-SQLAlchemy. No ORM. No .query.filter_by().
user_id comes from session['user_id'] — same as other GrowEasy routes.
"""

from flask import (
    render_template, redirect, url_for, request,
    flash, jsonify, session, make_response
)
import json
from weasyprint import HTML
from functools import wraps
from sqlalchemy import text

from app.services.db import DB_ENGINE
from .abc_engine import classify_abc_items, assign_default_policies, run_decision_engine, get_current_stock, evaluate_item
from . import supply_chain_bp
from .forms import InventoryItemForm, SupplierKPIForm, LandedCostForm
from .utils import (
    run_inventory_calculation,
    calc_supplier_score,
    calc_landed_cost,
)


# ─────────────────────────────────────────────
# Auth guard — matches GrowEasy session pattern
# (replace with your actual login_required if you have one)
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in to continue.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def get_uid():
    """Current user's ID from session."""
    return session['user_id']


# ═══════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════

@supply_chain_bp.route("/")
@login_required
def dashboard():
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        inv_count = conn.execute(
            text("SELECT COUNT(*) FROM scm_inventory_items WHERE user_id = :uid"),
            {"uid": uid}
        ).scalar()

        supplier_count = conn.execute(
            text("SELECT COUNT(*) FROM supplier_kpis WHERE user_id = :uid"),
            {"uid": uid}
        ).scalar()

        lc_count = conn.execute(
            text("SELECT COUNT(*) FROM landed_costs WHERE user_id = :uid"),
            {"uid": uid}
        ).scalar()

        top_suppliers = conn.execute(
            text("""
                SELECT supplier_name, period, composite_score
                FROM supplier_kpis
                WHERE user_id = :uid
                ORDER BY composite_score DESC NULLS LAST
                LIMIT 5
            """),
            {"uid": uid}
        ).mappings().all()

        recent_items = conn.execute(
            text("""
                SELECT name, sku, eoq, rop, safety_stock, total_cost
                FROM scm_inventory_items
                WHERE user_id = :uid
                ORDER BY updated_at DESC
                LIMIT 5
            """),
            {"uid": uid}
        ).mappings().all()

    return render_template(
        "supply_chain/dashboard.html",
        inv_count=inv_count,
        supplier_count=supplier_count,
        lc_count=lc_count,
        top_suppliers=top_suppliers,
        recent_items=recent_items,
    )


# ═══════════════════════════════════════════════════════
# MODULE 1 — INVENTORY TOOLS (EOQ / ROP / Safety Stock)
# ═══════════════════════════════════════════════════════

@supply_chain_bp.route("/inventory")
@login_required
def inventory_list():
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        items = conn.execute(
            text("""
                SELECT id, name, sku, unit, eoq, rop, safety_stock, total_cost, updated_at
                FROM scm_inventory_items
                WHERE user_id = :uid
                ORDER BY updated_at DESC
            """),
            {"uid": uid}
        ).mappings().all()

    return render_template("supply_chain/inventory_tools.html",
                           items=items, form=InventoryItemForm())


@supply_chain_bp.route("/inventory/calculate", methods=["GET", "POST"])
@supply_chain_bp.route("/inventory/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def inventory_calculate(item_id=None):
    uid = get_uid()
    existing = None
    result = None

    if item_id:
        with DB_ENGINE.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM scm_inventory_items WHERE id = :id AND user_id = :uid"),
                {"id": item_id, "uid": uid}
            ).mappings().first()
        if not row:
            flash("Item not found.", "danger")
            return redirect(url_for("supply_chain.inventory_list"))
        existing = dict(row)

    form = InventoryItemForm()

    # Pre-fill form for edit
    if request.method == "GET" and existing:
        form.name.data             = existing["name"]
        form.sku.data              = existing.get("sku", "")
        form.unit.data             = existing.get("unit", "pcs")
        form.annual_demand.data    = float(existing["annual_demand"])
        form.daily_demand_avg.data = float(existing.get("daily_demand_avg") or 0)
        form.daily_demand_std.data = float(existing.get("daily_demand_std") or 0)
        form.ordering_cost.data    = float(existing["ordering_cost"])
        form.unit_cost.data        = float(existing["unit_cost"])
        # stored as fraction, form shows percentage
        form.holding_cost_pct.data = float(existing["holding_cost_pct"]) * 100
        form.lead_time_days_avg.data = float(existing["lead_time_days_avg"])
        form.lead_time_days_std.data = float(existing.get("lead_time_days_std") or 0)
        form.service_level_z.data  = str(existing.get("service_level_z", "1.645"))

    if form.validate_on_submit():
        z    = float(form.service_level_z.data)
        hpct = form.holding_cost_pct.data / 100.0

        try:
            result = run_inventory_calculation(
                annual_demand      = form.annual_demand.data,
                ordering_cost      = form.ordering_cost.data,
                unit_cost          = form.unit_cost.data,
                holding_cost_pct   = hpct,
                daily_demand_avg   = form.daily_demand_avg.data,
                lead_time_days_avg = form.lead_time_days_avg.data,
                z                  = z,
                daily_demand_std   = form.daily_demand_std.data or 0,
                lead_time_days_std = form.lead_time_days_std.data or 0,
            )
        except ValueError as e:
            flash(str(e), "danger")
            return render_template("supply_chain/inventory_calculate.html",
                                   form=form, result=None, existing=existing)

        with DB_ENGINE.begin() as conn:
            if existing:
                conn.execute(text("""
                    UPDATE scm_inventory_items SET
                        name = :name, sku = :sku, unit = :unit,
                        annual_demand = :annual_demand,
                        daily_demand_avg = :daily_demand_avg,
                        daily_demand_std = :daily_demand_std,
                        ordering_cost = :ordering_cost,
                        unit_cost = :unit_cost,
                        holding_cost_pct = :holding_cost_pct,
                        lead_time_days_avg = :lead_time_days_avg,
                        lead_time_days_std = :lead_time_days_std,
                        service_level_z = :service_level_z,
                        eoq = :eoq, rop = :rop, safety_stock = :safety_stock,
                        annual_order_cost = :annual_order_cost,
                        annual_hold_cost = :annual_hold_cost,
                        total_cost = :total_cost,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND user_id = :uid
                """), {
                    "name": form.name.data, "sku": form.sku.data,
                    "unit": form.unit.data,
                    "annual_demand": form.annual_demand.data,
                    "daily_demand_avg": form.daily_demand_avg.data,
                    "daily_demand_std": form.daily_demand_std.data or 0,
                    "ordering_cost": form.ordering_cost.data,
                    "unit_cost": form.unit_cost.data,
                    "holding_cost_pct": hpct, "service_level_z": z,
                    "lead_time_days_avg": form.lead_time_days_avg.data,
                    "lead_time_days_std": form.lead_time_days_std.data or 0,
                    "eoq": result.eoq, "rop": result.rop,
                    "safety_stock": result.safety_stock,
                    "annual_order_cost": result.annual_order_cost,
                    "annual_hold_cost": result.annual_hold_cost,
                    "total_cost": result.total_cost,
                    "id": item_id, "uid": uid,
                })
            else:
                conn.execute(text("""
                    INSERT INTO scm_inventory_items (
                        user_id, name, sku, unit,
                        annual_demand, daily_demand_avg, daily_demand_std,
                        ordering_cost, unit_cost, holding_cost_pct,
                        lead_time_days_avg, lead_time_days_std, service_level_z,
                        eoq, rop, safety_stock,
                        annual_order_cost, annual_hold_cost, total_cost
                    ) VALUES (
                        :uid, :name, :sku, :unit,
                        :annual_demand, :daily_demand_avg, :daily_demand_std,
                        :ordering_cost, :unit_cost, :holding_cost_pct,
                        :lead_time_days_avg, :lead_time_days_std, :service_level_z,
                        :eoq, :rop, :safety_stock,
                        :annual_order_cost, :annual_hold_cost, :total_cost
                    )
                """), {
                    "uid": uid,
                    "name": form.name.data, "sku": form.sku.data,
                    "unit": form.unit.data,
                    "annual_demand": form.annual_demand.data,
                    "daily_demand_avg": form.daily_demand_avg.data,
                    "daily_demand_std": form.daily_demand_std.data or 0,
                    "ordering_cost": form.ordering_cost.data,
                    "unit_cost": form.unit_cost.data,
                    "holding_cost_pct": hpct, "service_level_z": z,
                    "lead_time_days_avg": form.lead_time_days_avg.data,
                    "lead_time_days_std": form.lead_time_days_std.data or 0,
                    "eoq": result.eoq, "rop": result.rop,
                    "safety_stock": result.safety_stock,
                    "annual_order_cost": result.annual_order_cost,
                    "annual_hold_cost": result.annual_hold_cost,
                    "total_cost": result.total_cost,
                })

        flash(f"'{form.name.data}' saved — EOQ: {result.eoq:,.0f} units", "success")

    return render_template(
        "supply_chain/inventory_calculate.html",
        form=form, result=result, existing=existing,
    )


@supply_chain_bp.route("/inventory/<int:item_id>/delete", methods=["POST"])
@login_required
def inventory_delete(item_id):
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("DELETE FROM scm_inventory_items WHERE id = :id AND user_id = :uid"),
            {"id": item_id, "uid": get_uid()}
        )
    flash("Item deleted.", "info")
    return redirect(url_for("supply_chain.inventory_list"))


# ═══════════════════════════════════════════════════════
# MODULE 2 — SUPPLIER KPI DASHBOARD
# ═══════════════════════════════════════════════════════

@supply_chain_bp.route("/suppliers")
@login_required
def supplier_list():
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        kpis = conn.execute(
            text("""
                SELECT * FROM supplier_kpis
                WHERE user_id = :uid
                ORDER BY composite_score DESC NULLS LAST
            """),
            {"uid": uid}
        ).mappings().all()

    grade_dist = {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    for k in kpis:
        s = float(k["composite_score"] or 0)
        if s >= 90:   grade_dist["A+"] += 1
        elif s >= 80: grade_dist["A"]  += 1
        elif s >= 70: grade_dist["B"]  += 1
        elif s >= 60: grade_dist["C"]  += 1
        else:         grade_dist["D"]  += 1

    return render_template(
        "supply_chain/supplier_kpi.html",
        kpis=kpis,
        grade_dist=grade_dist,
        form=SupplierKPIForm(),
    )

import csv
from io import StringIO
from flask import Response

@supply_chain_bp.route('/suppliers/export/csv')
@login_required
def supplier_export_csv():
    """Export all supplier KPI records to CSV using raw SQL."""
    uid = get_uid()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Supplier Name', 'Supplier Code', 'Period', 'Category', 'Composite Score', 'Grade'])

    with DB_ENGINE.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT supplier_name, supplier_code, period, category, composite_score
                FROM supplier_kpis
                WHERE user_id = :uid
                ORDER BY supplier_name
            """),
            {"uid": uid}
        ).mappings().all()

    for row in rows:
        score = float(row["composite_score"] or 0)
        if score >= 90:   grade = 'A+'
        elif score >= 80: grade = 'A'
        elif score >= 70: grade = 'B'
        elif score >= 60: grade = 'C'
        else:             grade = 'D'

        writer.writerow([
            row["supplier_name"],
            row["supplier_code"] or '',
            row["period"],
            row["category"] or '',
            f"{score:.1f}",
            grade
        ])

    csv_output = output.getvalue()
    output.close()
    return Response(
        csv_output,
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=supplier_kpis.csv'}
    )

@supply_chain_bp.route("/suppliers/add", methods=["GET", "POST"])
@supply_chain_bp.route("/suppliers/<int:kpi_id>/edit", methods=["GET", "POST"])
@login_required
def supplier_save(kpi_id=None):
    uid = get_uid()
    existing = None

    if kpi_id:
        with DB_ENGINE.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM supplier_kpis WHERE id = :id AND user_id = :uid"),
                {"id": kpi_id, "uid": uid}
            ).mappings().first()
        if not row:
            flash("Record not found.", "danger")
            return redirect(url_for("supply_chain.supplier_list"))
        existing = dict(row)

    form = SupplierKPIForm()

    if request.method == "GET" and existing:
        for field in form:
            if field.name in existing and field.name != "csrf_token":
                field.data = existing[field.name]

    if form.validate_on_submit():
        score = calc_supplier_score(
            on_time_delivery    = form.on_time_delivery_pct.data,
            quality_acceptance  = form.quality_acceptance_pct.data,
            invoice_accuracy    = form.invoice_accuracy_pct.data,
            lead_time_adherence = form.lead_time_adherence_pct.data,
            responsiveness      = form.responsiveness_score.data,
            compliance          = form.compliance_score.data,
        )

        params = {
            "supplier_name":          form.supplier_name.data,
            "supplier_code":          form.supplier_code.data,
            "category":               form.category.data,
            "period":                 form.period.data,
            "on_time_delivery_pct":   form.on_time_delivery_pct.data,
            "quality_acceptance_pct": form.quality_acceptance_pct.data,
            "invoice_accuracy_pct":   form.invoice_accuracy_pct.data,
            "lead_time_adherence_pct":form.lead_time_adherence_pct.data,
            "responsiveness_score":   form.responsiveness_score.data,
            "compliance_score":       form.compliance_score.data,
            "composite_score":        score.composite,
            "notes":                  form.notes.data,
            "uid":                    uid,
        }

        with DB_ENGINE.begin() as conn:
            if existing:
                params["id"] = kpi_id
                conn.execute(text("""
                    UPDATE supplier_kpis SET
                        supplier_name = :supplier_name,
                        supplier_code = :supplier_code,
                        category = :category, period = :period,
                        on_time_delivery_pct = :on_time_delivery_pct,
                        quality_acceptance_pct = :quality_acceptance_pct,
                        invoice_accuracy_pct = :invoice_accuracy_pct,
                        lead_time_adherence_pct = :lead_time_adherence_pct,
                        responsiveness_score = :responsiveness_score,
                        compliance_score = :compliance_score,
                        composite_score = :composite_score,
                        notes = :notes,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND user_id = :uid
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO supplier_kpis (
                        user_id, supplier_name, supplier_code, category, period,
                        on_time_delivery_pct, quality_acceptance_pct,
                        invoice_accuracy_pct, lead_time_adherence_pct,
                        responsiveness_score, compliance_score,
                        composite_score, notes
                    ) VALUES (
                        :uid, :supplier_name, :supplier_code, :category, :period,
                        :on_time_delivery_pct, :quality_acceptance_pct,
                        :invoice_accuracy_pct, :lead_time_adherence_pct,
                        :responsiveness_score, :compliance_score,
                        :composite_score, :notes
                    )
                """), params)

        flash(
            f"'{form.supplier_name.data}' saved — Score: {score.composite} ({score.grade})",
            "success"
        )
        return redirect(url_for("supply_chain.supplier_list"))

    return render_template("supply_chain/supplier_form.html",
                           form=form, existing=existing)


@supply_chain_bp.route("/suppliers/<int:kpi_id>/delete", methods=["POST"])
@login_required
def supplier_delete(kpi_id):
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("DELETE FROM supplier_kpis WHERE id = :id AND user_id = :uid"),
            {"id": kpi_id, "uid": get_uid()}
        )
    flash("Supplier record deleted.", "info")
    return redirect(url_for("supply_chain.supplier_list"))


@supply_chain_bp.route("/suppliers/<int:kpi_id>/breakdown.json")
@login_required
def supplier_breakdown_json(kpi_id):
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM supplier_kpis WHERE id = :id AND user_id = :uid"),
            {"id": kpi_id, "uid": uid}
        ).mappings().first()

    if not row:
        return jsonify({"error": "not found"}), 404

    score = calc_supplier_score(
        on_time_delivery    = float(row["on_time_delivery_pct"] or 0),
        quality_acceptance  = float(row["quality_acceptance_pct"] or 0),
        invoice_accuracy    = float(row["invoice_accuracy_pct"] or 0),
        lead_time_adherence = float(row["lead_time_adherence_pct"] or 0),
        responsiveness      = float(row["responsiveness_score"] or 0),
        compliance          = float(row["compliance_score"] or 0),
    )
    return jsonify({
        "supplier":  row["supplier_name"],
        "period":    row["period"],
        "composite": score.composite,
        "grade":     score.grade,
        "labels":    list(score.breakdown.keys()),
        "data":      list(score.breakdown.values()),
        "raw": {
            "On-Time Delivery":    float(row["on_time_delivery_pct"] or 0),
            "Quality Acceptance":  float(row["quality_acceptance_pct"] or 0),
            "Invoice Accuracy":    float(row["invoice_accuracy_pct"] or 0),
            "Lead Time Adherence": float(row["lead_time_adherence_pct"] or 0),
            "Responsiveness":      float(row["responsiveness_score"] or 0) * 10,
            "Compliance":          float(row["compliance_score"] or 0) * 10,
        }
    })


# ═══════════════════════════════════════════════════════
# MODULE 3 — LANDED COST CALCULATOR
# ═══════════════════════════════════════════════════════

@supply_chain_bp.route("/landed-cost")
@login_required
def landed_cost_list():
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        records = conn.execute(
            text("""
                SELECT id, reference_no, description, currency,
                       product_cost, total_landed_cost, landed_cost_per_unit, created_at
                FROM landed_costs
                WHERE user_id = :uid
                ORDER BY created_at DESC
            """),
            {"uid": uid}
        ).mappings().all()

    return render_template("supply_chain/landed_cost_list.html", records=records)


@supply_chain_bp.route("/landed-cost/calculate", methods=["GET", "POST"])
@supply_chain_bp.route("/landed-cost/<int:lc_id>/edit", methods=["GET", "POST"])
@login_required
def landed_cost_calculate(lc_id=None):
    uid = get_uid()
    existing = None
    result = None
    lc_id_passed = lc_id   # will be used in template

    # Load existing record if editing
    if lc_id:
        with DB_ENGINE.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM landed_costs WHERE id = :id AND user_id = :uid"),
                {"id": lc_id, "uid": uid}
            ).mappings().first()
        if not row:
            flash("Record not found.", "danger")
            return redirect(url_for("supply_chain_bp.landed_cost_list"))
        existing = dict(row)

    form = LandedCostForm()

    # Pre‑fill form for edit (GET request with existing)
    if request.method == "GET" and existing:
        for field in form:
            if field.name in existing and field.name != "csrf_token":
                v = existing[field.name]
                # stored as fraction, form shows percentage
                if field.name in ("customs_duty_pct", "additional_duty_pct",
                                  "sales_tax_pct", "withholding_tax_pct"):
                    field.data = float(v or 0) * 100
                else:
                    field.data = v

    # Handle form submission (POST)
    if form.validate_on_submit():
        result = calc_landed_cost(
            product_cost        = form.product_cost.data,
            quantity            = form.quantity.data,
            exchange_rate       = form.exchange_rate.data,
            freight_cost        = form.freight_cost.data or 0,
            insurance_cost      = form.insurance_cost.data or 0,
            customs_duty_pct    = (form.customs_duty_pct.data or 0) / 100,
            additional_duty_pct = (form.additional_duty_pct.data or 0) / 100,
            sales_tax_pct       = (form.sales_tax_pct.data or 17) / 100,
            withholding_tax_pct = (form.withholding_tax_pct.data or 0) / 100,
            clearing_charges    = form.clearing_charges.data or 0,
            port_handling       = form.port_handling.data or 0,
            inland_freight      = form.inland_freight.data or 0,
            other_charges       = form.other_charges.data or 0,
        )

        params = {
            "uid":               uid,
            "reference_no":      form.reference_no.data,
            "description":       form.description.data,
            "currency":          form.currency.data,
            "exchange_rate":     form.exchange_rate.data,
            "product_cost":      form.product_cost.data,
            "quantity":          form.quantity.data,
            "freight_cost":      form.freight_cost.data or 0,
            "insurance_cost":    form.insurance_cost.data or 0,
            "customs_duty_pct":  (form.customs_duty_pct.data or 0) / 100,
            "additional_duty_pct": (form.additional_duty_pct.data or 0) / 100,
            "sales_tax_pct":     (form.sales_tax_pct.data or 17) / 100,
            "withholding_tax_pct": (form.withholding_tax_pct.data or 0) / 100,
            "clearing_charges":  form.clearing_charges.data or 0,
            "port_handling":     form.port_handling.data or 0,
            "inland_freight":    form.inland_freight.data or 0,
            "other_charges":     form.other_charges.data or 0,
            "total_landed_cost":    result.total_landed_cost,
            "landed_cost_per_unit": result.landed_cost_per_unit,
            "duty_amount":   result.customs_duty + result.additional_duty,
            "tax_amount":    result.sales_tax + result.withholding_tax,
        }

        new_id = None
        with DB_ENGINE.begin() as conn:
            if existing:
                params["id"] = lc_id
                conn.execute(text("""
                    UPDATE landed_costs SET
                        reference_no = :reference_no, description = :description,
                        currency = :currency, exchange_rate = :exchange_rate,
                        product_cost = :product_cost, quantity = :quantity,
                        freight_cost = :freight_cost, insurance_cost = :insurance_cost,
                        customs_duty_pct = :customs_duty_pct,
                        additional_duty_pct = :additional_duty_pct,
                        sales_tax_pct = :sales_tax_pct,
                        withholding_tax_pct = :withholding_tax_pct,
                        clearing_charges = :clearing_charges,
                        port_handling = :port_handling,
                        inland_freight = :inland_freight,
                        other_charges = :other_charges,
                        total_landed_cost = :total_landed_cost,
                        landed_cost_per_unit = :landed_cost_per_unit,
                        duty_amount = :duty_amount, tax_amount = :tax_amount,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :id AND user_id = :uid
                """), params)
                new_id = lc_id
            else:
                res = conn.execute(text("""
                    INSERT INTO landed_costs (
                        user_id, reference_no, description, currency, exchange_rate,
                        product_cost, quantity, freight_cost, insurance_cost,
                        customs_duty_pct, additional_duty_pct, sales_tax_pct,
                        withholding_tax_pct, clearing_charges, port_handling,
                        inland_freight, other_charges,
                        total_landed_cost, landed_cost_per_unit, duty_amount, tax_amount
                    ) VALUES (
                        :uid, :reference_no, :description, :currency, :exchange_rate,
                        :product_cost, :quantity, :freight_cost, :insurance_cost,
                        :customs_duty_pct, :additional_duty_pct, :sales_tax_pct,
                        :withholding_tax_pct, :clearing_charges, :port_handling,
                        :inland_freight, :other_charges,
                        :total_landed_cost, :landed_cost_per_unit, :duty_amount, :tax_amount
                    ) RETURNING id
                """), params)
                new_id = res.fetchone()[0]

        flash(
            f"Saved — Total: PKR {result.total_landed_cost:,.2f} | "
            f"Per unit: PKR {result.landed_cost_per_unit:,.4f}",
            "success"
        )
        return render_template(
            "supply_chain/landed_cost_calculate.html",
            form=form, result=result, existing=existing, lc_id=new_id
        )

    # For GET (and also if form not submitted), render the page
    return render_template(
        "supply_chain/landed_cost_calculate.html",
        form=form, result=result, existing=existing, lc_id=lc_id_passed
    )


@supply_chain_bp.route("/landed-cost/<int:lc_id>/delete", methods=["POST"])
@login_required
def landed_cost_delete(lc_id):
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("DELETE FROM landed_costs WHERE id = :id AND user_id = :uid"),
            {"id": lc_id, "uid": get_uid()}
        )
    flash("Record deleted.", "info")
    return redirect(url_for("supply_chain.landed_cost_list"))


@supply_chain_bp.route("/landed-cost/<int:lc_id>/breakdown.json")
@login_required
def landed_cost_breakdown_json(lc_id):
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM landed_costs WHERE id = :id AND user_id = :uid"),
            {"id": lc_id, "uid": uid}
        ).mappings().first()

    if not row:
        return jsonify({"error": "not found"}), 404

    result = calc_landed_cost(
        product_cost        = float(row["product_cost"]),
        quantity            = float(row["quantity"]),
        exchange_rate       = float(row["exchange_rate"] or 1),
        freight_cost        = float(row["freight_cost"] or 0),
        insurance_cost      = float(row["insurance_cost"] or 0),
        customs_duty_pct    = float(row["customs_duty_pct"] or 0),
        additional_duty_pct = float(row["additional_duty_pct"] or 0),
        sales_tax_pct       = float(row["sales_tax_pct"] or 0.17),
        withholding_tax_pct = float(row["withholding_tax_pct"] or 0),
        clearing_charges    = float(row["clearing_charges"] or 0),
        port_handling       = float(row["port_handling"] or 0),
        inland_freight      = float(row["inland_freight"] or 0),
        other_charges       = float(row["other_charges"] or 0),
    )
    return jsonify({
        "labels":  list(result.cost_breakdown.keys()),
        "data":    list(result.cost_breakdown.values()),
        "total":   result.total_landed_cost,
        "per_unit": result.landed_cost_per_unit,
        "effective_duty_pct": result.effective_duty_pct,
    })


@supply_chain_bp.route('/landed-cost/<int:lc_id>/pdf')
@login_required
def landed_cost_pdf(lc_id):
    """Generate PDF for a landed cost calculation using raw SQL."""
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM landed_costs WHERE id = :id AND user_id = :uid"),
            {"id": lc_id, "uid": uid}
        ).mappings().first()
        if not row:
            flash("Record not found.", "danger")
            return redirect(url_for("supply_chain_bp.landed_cost_list"))

    # Re‑calculate to get the cost_breakdown dict
    calc = calc_landed_cost(
        product_cost        = float(row["product_cost"]),
        quantity            = float(row["quantity"]),
        exchange_rate       = float(row["exchange_rate"] or 1),
        freight_cost        = float(row["freight_cost"] or 0),
        insurance_cost      = float(row["insurance_cost"] or 0),
        customs_duty_pct    = float(row["customs_duty_pct"] or 0),
        additional_duty_pct = float(row["additional_duty_pct"] or 0),
        sales_tax_pct       = float(row["sales_tax_pct"] or 0.17),
        withholding_tax_pct = float(row["withholding_tax_pct"] or 0),
        clearing_charges    = float(row["clearing_charges"] or 0),
        port_handling       = float(row["port_handling"] or 0),
        inland_freight      = float(row["inland_freight"] or 0),
        other_charges       = float(row["other_charges"] or 0),
    )
    cost_breakdown = calc.cost_breakdown

    # Get company details (adjust to your actual function)
    from app.services.cache import get_user_profile_cached
    company_data = get_user_profile_cached(uid)
    company = {
        "name": company_data.get("company_name", "GrowEasy"),
        "address": company_data.get("company_address", ""),
        "tax_id": company_data.get("company_tax_id", ""),
    }

    rendered = render_template(
        'supply_chain/landed_cost_pdf.html',
        lc=row,               # row is a Mapping, accessible as dict
        cost_breakdown=cost_breakdown,
        company=company
    )
    pdf = HTML(string=rendered).write_pdf()
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=landed_cost_{row["reference_no"] or lc_id}.pdf'
    return response

# ─────────────────────────────────────────────────────
# ABC Analysis & Decision Engine Endpoints
# ─────────────────────────────────────────────────────

@supply_chain_bp.route("/abc/run", methods=["POST"])
@login_required
def run_abc():
    """Manually trigger ABC classification and policy assignment."""
    uid = get_uid()
    classify_abc_items(uid)
    assign_default_policies(uid, override_existing=True)
    flash("ABC classification and policies updated.", "success")
    return redirect(url_for("supply_chain.decision_dashboard"))


@supply_chain_bp.route("/decision/run", methods=["POST"])
@login_required
def run_decision():
    """Manually trigger the decision engine to generate purchase suggestions."""
    uid = get_uid()
    results = run_decision_engine(uid)
    flash(f"Decision engine finished. {len(results)} suggested orders created.", "info")
    return redirect(url_for("supply_chain.decision_dashboard"))


@supply_chain_bp.route("/decision/dashboard")
@login_required
def decision_dashboard():
    """Show pending suggestions, current stock status, and control panel."""
    uid = get_uid()
    with DB_ENGINE.connect() as conn:
        # Pending suggestions
        suggestions = conn.execute(text("""
            SELECT s.*, i.name as item_name, i.sku
            FROM scm_suggested_orders s
            JOIN scm_inventory_items i ON s.item_id = i.id
            WHERE s.status = 'pending' AND i.user_id = :uid
            ORDER BY s.created_at DESC
        """), {"uid": uid}).mappings().all()

        # Items below ROP (for monitoring)
        # We compute ROP on the fly using the engine – but for simplicity, we'll list items with stock <= ROP
        # We'll call a helper per item; for dashboard we may precompute.
        # Instead, we fetch all items and compute quickly.
        items = conn.execute(text("""
            SELECT id, name, sku, auto_reorder, abc_class
            FROM scm_inventory_items WHERE user_id = :uid
        """), {"uid": uid}).mappings().all()

    low_stock = []
    for it in items:
        stock = get_current_stock(it["id"], uid)
        # Compute ROP using engine (simplify: fetch ROP from stored value if we had it, but we can compute)
        # We'll compute ROP quickly via compute_reorder_params
        from .abc_engine import compute_reorder_params
        _, rop, _ = compute_reorder_params(it["id"], uid)
        if rop and stock <= rop:
            low_stock.append({
                "name": it["name"],
                "sku": it["sku"],
                "stock": stock,
                "rop": int(rop),
                "abc": it["abc_class"]
            })

    return render_template(
        "supply_chain/decision_dashboard.html",
        suggestions=suggestions,
        low_stock=low_stock
    )


@supply_chain_bp.route("/decision/suggestion/<int:sug_id>/approve", methods=["POST"])
@login_required
def approve_suggestion(sug_id):
    """Approve a suggested order, create a real purchase order (draft)."""
    uid = get_uid()
    with DB_ENGINE.begin() as conn:
        sug = conn.execute(text("""
            SELECT s.*, i.name, i.sku, i.user_id
            FROM scm_suggested_orders s
            JOIN scm_inventory_items i ON s.item_id = i.id
            WHERE s.id = :sug_id AND i.user_id = :uid
        """), {"sug_id": sug_id, "uid": uid}).mappings().first()
        if not sug:
            flash("Suggestion not found.", "danger")
            return redirect(url_for("supply_chain_bp.decision_dashboard"))

        # Create a purchase order (draft)
        # You may have a purchase_orders table in your schema. We'll insert a draft.
        conn.execute(text("""
            INSERT INTO purchase_orders (user_id, supplier_id, supplier_name, order_date, expected_delivery_date, status)
            VALUES (:uid, :sup_id, :sup_name, CURRENT_DATE, CURRENT_DATE + INTERVAL '7 days', 'draft')
        """), {
            "uid": uid,
            "sup_id": sug["supplier_id"],
            "sup_name": sug["supplier_name"]
        })
        # Update suggestion status
        conn.execute(text("""
            UPDATE scm_suggested_orders SET status = 'approved' WHERE id = :id
        """), {"id": sug_id})

    flash(f"Purchase order created for {sug['name']} (quantity {sug['suggested_quantity']}).", "success")
    return redirect(url_for("supply_chain_bp.decision_dashboard"))


@supply_chain_bp.route("/decision/suggestion/<int:sug_id>/reject", methods=["POST"])
@login_required
def reject_suggestion(sug_id):
    """Reject a suggested order."""
    uid = get_uid()
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            UPDATE scm_suggested_orders
            SET status = 'rejected'
            WHERE id = :id AND item_id IN (SELECT id FROM scm_inventory_items WHERE user_id = :uid)
        """), {"id": sug_id, "uid": uid})
    flash("Suggestion rejected.", "info")
    return redirect(url_for("supply_chain_bp.decision_dashboard"))
