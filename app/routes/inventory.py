# app/routes/inventory.py
import time
import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g, jsonify, Response, current_app
from sqlalchemy import text
from app.services.inventory import InventoryManager
from app.services.location_inventory import LocationInventoryManager
from app.services.utils import random_success_message
from app.services.db import DB_ENGINE
from app.extensions import limiter
from app.decorators import role_required
from app.context_processors import CURRENCY_SYMBOLS
from app.services.cache import get_user_profile_cached

inventory_bp = Blueprint('inventory', __name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_int(val, default):
    try:
        return int(float(val)) if val not in (None, '') else default
    except (ValueError, TypeError):
        return default

def _safe_float(val, default):
    try:
        return float(val) if val not in (None, '') else default
    except (ValueError, TypeError):
        return default

def _get_or_create_main_location(conn, account_id):
    """
    Return the ID of the 'Main' location for this account, creating it if it
    doesn't exist yet.

    Uses a single atomic PostgreSQL upsert so there is no race condition and no
    UniqueViolation regardless of is_active state or concurrent requests.

    The ON CONFLICT targets the unique constraint on (account_id, location_code).
    The DO UPDATE is a no-op touch (sets location_name to itself) so that
    RETURNING id is always populated — both on INSERT and on conflict.
    """
    row = conn.execute(text("""
        INSERT INTO locations (account_id, location_name, location_code, location_type, is_active)
        VALUES (:aid, 'Main', 'MAIN', 'warehouse', TRUE)
        ON CONFLICT (account_id, location_code)
        DO UPDATE SET location_name = EXCLUDED.location_name
        RETURNING id
    """), {"aid": account_id}).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@inventory_bp.route("/inventory")
@role_required('owner', 'assistant')
def inventory():
    user_profile = get_user_profile_cached(session['user_id'])
    currency_symbol = CURRENCY_SYMBOLS.get(user_profile.get('preferred_currency', 'PKR'), 'Rs.')
    account_id = session['account_id']

    low_stock_alerts = InventoryManager.get_low_stock_alerts(account_id)

    with DB_ENGINE.connect() as conn:
        total = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE account_id = :aid AND is_active = TRUE
        """), {"aid": account_id}).scalar() or 0

        in_stock = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE account_id = :aid AND is_active = TRUE
            AND current_stock > COALESCE(min_stock_level, 0)
            AND current_stock > 0
        """), {"aid": account_id}).scalar() or 0

        low_stock = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE account_id = :aid AND is_active = TRUE
            AND current_stock <= COALESCE(min_stock_level, 0)
            AND current_stock > 0
        """), {"aid": account_id}).scalar() or 0

        out_of_stock = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE account_id = :aid AND is_active = TRUE
            AND current_stock = 0
        """), {"aid": account_id}).scalar() or 0

    class Summary:
        pass
    inventory_summary = Summary()
    inventory_summary.total = total
    inventory_summary.in_stock = in_stock
    inventory_summary.low_stock = low_stock
    inventory_summary.out_of_stock = out_of_stock

    return render_template("inventory.html",
                           currency_symbol=currency_symbol,
                           low_stock_alerts=low_stock_alerts,
                           inventory_summary=inventory_summary,
                           nonce=g.nonce)


@inventory_bp.route("/inventory_reports")
@role_required('owner', 'accountant')
def inventory_reports():
    try:
        from app.services.reports import InventoryReports
        account_id = session['account_id']

        bcg_matrix = InventoryReports.get_bcg_matrix(account_id)
        turnover = InventoryReports.get_stock_turnover(account_id, days=30)
        profitability = InventoryReports.get_profitability_analysis(account_id)
        slow_movers = InventoryReports.get_slow_movers(account_id, days_threshold=90)

        return render_template("inventory_reports.html",
                               bcg_matrix=bcg_matrix,
                               turnover=turnover[:10],
                               profitability=profitability[:10],
                               slow_movers=slow_movers,
                               nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Inventory reports error: {e}")
        flash("Reports temporarily unavailable", "info")
        return redirect(url_for('inventory.inventory'))


@inventory_bp.route("/inventory/dashboard")
@role_required('owner', 'assistant')
def inventory_dashboard():
    from app.services.auth import get_api_key_for_user
    user_profile = get_user_profile_cached(session['user_id'])
    currency_symbol = CURRENCY_SYMBOLS.get(user_profile.get('preferred_currency', 'PKR'), 'Rs.')
    api_key = get_api_key_for_user(session['user_id'])
    return render_template("inventory_dashboard.html",
                           currency_symbol=currency_symbol,
                           api_key=api_key,
                           nonce=g.nonce)


@inventory_bp.route("/inventory/catalog")
@role_required('owner', 'assistant')
def inventory_catalog():
    from app.services.auth import get_api_key_for_user
    user_profile = get_user_profile_cached(session['user_id'])
    currency_symbol = CURRENCY_SYMBOLS.get(user_profile.get('preferred_currency', 'PKR'), 'Rs.')
    api_key = get_api_key_for_user(session['user_id'])
    return render_template("inventory_catalog.html",
                           currency_symbol=currency_symbol,
                           api_key=api_key,
                           nonce=g.nonce)


@inventory_bp.route("/add_product", methods=['POST'])
@role_required('owner', 'assistant')
def add_product():
    user_id = session['user_id']
    account_id = session['account_id']

    product_data = {
        'name': request.form.get('name'),
        'sku': request.form.get('sku'),
        'category': request.form.get('category'),
        'description': request.form.get('description'),
        'current_stock': _safe_float(request.form.get('current_stock'), 0),
        'min_stock_level': _safe_int(request.form.get('min_stock_level'), 5),
        'cost_price': _safe_float(request.form.get('cost_price'), 0.0),
        'selling_price': _safe_float(request.form.get('selling_price'), 0.0),
        'supplier': request.form.get('supplier'),
        'location': request.form.get('location'),
        'unit_type': request.form.get('unit_type', 'piece'),
        'is_perishable': 'is_perishable' in request.form,
        'expiry_date': request.form.get('expiry_date') or None,
        'batch_number': request.form.get('batch_number', '').strip() or None,
        'barcode': request.form.get('barcode', '').strip() or None,
        'pack_size': _safe_float(request.form.get('pack_size'), 1.0),
        'weight_kg': _safe_float(request.form.get('weight_kg'), None),
    }

    if product_data['selling_price'] < product_data['cost_price']:
        flash('❌ Selling price cannot be less than cost price.', 'error')
        return redirect(url_for('inventory.inventory'))

    product_id = InventoryManager.add_product(user_id, account_id, product_data)
    if product_id:
        try:
            with DB_ENGINE.begin() as conn:
                location_id = _get_or_create_main_location(conn, account_id)

            if location_id:
                LocationInventoryManager.add_product_to_location(
                    product_id, location_id, product_data['current_stock'], user_id
                )
        except Exception as e:
            # Non-fatal: product is saved, only location mapping failed
            current_app.logger.error(f"Failed to assign product {product_id} to location: {e}")

        flash('✅ Product added successfully!', 'success')
    else:
        flash('❌ Error adding product. SKU might already exist.', 'error')

    return redirect(url_for('inventory.inventory'))


@inventory_bp.route("/update_product", methods=['POST'])
@role_required('owner', 'assistant')
def update_product():
    user_id = session['user_id']
    account_id = session['account_id']
    product_id = request.form.get('product_id')

    if not product_id:
        flash("Product ID missing", "error")
        return redirect(url_for('inventory.inventory'))

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM inventory_items WHERE id = :pid AND account_id = :aid
        """), {"pid": product_id, "aid": account_id}).first()
        if not row:
            flash("Product not found", "error")
            return redirect(url_for('inventory.inventory'))

    update_data = {
        'name': request.form.get('name'),
        'sku': request.form.get('sku'),
        'barcode': request.form.get('barcode'),
        'category': request.form.get('category'),
        'supplier': request.form.get('supplier'),
        'min_stock_level': request.form.get('min_stock_level', 5),
        'location': request.form.get('location'),
        'unit_type': request.form.get('unit_type', 'piece'),
        'cost_price': request.form.get('cost_price', 0),
        'selling_price': request.form.get('selling_price', 0),
        'expiry_date': request.form.get('expiry_date') or None,
        'batch_number': request.form.get('batch_number'),
        'description': request.form.get('description'),
    }

    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE inventory_items SET
                    name = :name,
                    sku = :sku,
                    barcode = :barcode,
                    category = :category,
                    supplier = :supplier,
                    min_stock_level = :min_stock_level,
                    location = :location,
                    unit_type = :unit_type,
                    cost_price = :cost_price,
                    selling_price = :selling_price,
                    expiry_date = :expiry_date,
                    batch_number = :batch_number,
                    description = :description,
                    updated_at = NOW()
                WHERE id = :pid AND account_id = :aid
            """), {**update_data, 'pid': product_id, 'aid': account_id})
        flash("Product updated successfully", "success")
    except Exception as e:
        flash(f"Update failed: {str(e)}", "error")

    return redirect(url_for('inventory.inventory'))


@inventory_bp.route("/delete_product", methods=['POST'])
@role_required('owner', 'assistant')
def delete_product():
    user_id = session['user_id']
    account_id = session['account_id']
    product_id = request.form.get('product_id')
    reason = request.form.get('reason')
    notes = request.form.get('notes', '')
    full_reason = f"{reason}. {notes}".strip()
    success = InventoryManager.delete_product(user_id, account_id, product_id, full_reason)
    if success:
        flash('✅ Product removed successfully', 'success')
    else:
        flash('❌ Error removing product – it may already be deleted.', 'error')
    return redirect(url_for('inventory.inventory'))


@inventory_bp.route("/api/inventory_items")
@role_required('owner', 'assistant')
def get_inventory_items_api():
    account_id = session['account_id']
    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, sku, selling_price, current_stock, unit_type
            FROM inventory_items
            WHERE account_id = :aid
              AND is_active = TRUE
              AND current_stock > 0
            ORDER BY name
        """), {"aid": account_id}).fetchall()
    inventory_data = [{
        'id': str(item[0]),
        'name': item[1],
        'sku': item[2] or '',
        'price': float(item[3]) if item[3] else 0.0,
        'stock': int(item[4]),
        'unit_type': item[5] or 'piece'
    } for item in items]
    return jsonify(inventory_data)


@inventory_bp.route("/adjust_stock_audit", methods=['POST'])
@limiter.limit("10 per minute")
@role_required('owner', 'assistant')
def adjust_stock_audit():
    user_id = session['user_id']
    account_id = session['account_id']
    product_id = request.form.get('product_id')
    adjustment_type = request.form.get('adjustment_type')
    quantity = int(request.form.get('quantity', 0))
    new_cost_price = request.form.get('new_cost_price')
    new_selling_price = request.form.get('new_selling_price')
    reason = request.form.get('reason', 'Stock adjustment')
    notes = request.form.get('notes', '')

    try:
        product = InventoryManager.get_product_details(account_id, product_id)
        if not product:
            flash('❌ Product not found', 'error')
            return redirect(url_for('inventory.inventory'))

        current_stock = product['current_stock']
        product_name = product['name']

        if adjustment_type == 'add_stock':
            delta, movement_type = +quantity, 'stock_in'
        elif adjustment_type == 'remove_stock':
            delta, movement_type = -quantity, 'stock_out'
        elif adjustment_type == 'damaged':
            delta, movement_type = -quantity, 'damaged'
        elif adjustment_type == 'found_stock':
            delta, movement_type = +quantity, 'found'
        elif adjustment_type == 'set_stock':
            delta, movement_type = quantity - current_stock, 'adjustment'
        else:
            flash('❌ Invalid adjustment type', 'error')
            return redirect(url_for('inventory.inventory'))

        # Always adjust at Main location — get or create it
        with DB_ENGINE.begin() as conn:
            location_id = _get_or_create_main_location(conn, account_id)

        success = InventoryManager.update_stock_delta(
            user_id=user_id,
            account_id=account_id,
            product_id=product_id,
            quantity_delta=delta,
            movement_type=movement_type,
            reference_id=f"ADJ-{int(time.time())}",
            notes=f"{reason}: {notes}".strip(),
            location_id=location_id              # ← KEY FIX
        )

        if success and (new_cost_price or new_selling_price):
            updates = {}
            if new_cost_price and new_cost_price.strip():
                updates['cost_price'] = float(new_cost_price)
            if new_selling_price and new_selling_price.strip():
                updates['selling_price'] = float(new_selling_price)
            if updates:
                with DB_ENGINE.begin() as conn:
                    set_clause = ', '.join(f"{k} = :{k}" for k in updates)
                    params = {**updates, "product_id": product_id, "aid": account_id}
                    conn.execute(text(
                        f"UPDATE inventory_items SET {set_clause} WHERE id = :product_id AND account_id = :aid"
                    ), params)

        if success:
            flash(f'✅ {product_name} adjusted! Stock: {current_stock} → {current_stock + delta}', 'success')
        else:
            flash('❌ Failed to update stock (negative not allowed)', 'error')
        return redirect(url_for('inventory.inventory'))

    except Exception as e:
        current_app.logger.error(f"Stock adjustment error: {e}", exc_info=True)
        flash('❌ Error updating product', 'error')
        return redirect(url_for('inventory.inventory'))


@inventory_bp.route("/download_inventory_report")
@role_required('owner', 'accountant')
def download_inventory_report():
    account_id = session['account_id']
    inventory_data = InventoryManager.get_inventory_report(account_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Product Name', 'SKU', 'Barcode', 'Category', 'Current Stock', 'Unit Type',
        'Min Stock', 'Cost Price', 'Selling Price', 'Supplier', 'Location',
        'Perishable', 'Expiry Date', 'Batch'
    ])
    for item in inventory_data:
        writer.writerow([
            item['name'], item['sku'], item['barcode'], item['category'],
            item['current_stock'], item['unit_type'], item['min_stock'],
            item['cost_price'], item['selling_price'], item['supplier'],
            item['location'], item['is_perishable'], item['expiry_date'],
            item['batch_number']
        ])
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=inventory_report.csv"}
    )


@inventory_bp.route('/bulk_upload', methods=['GET', 'POST'])
@limiter.limit("15 per hour")
@role_required('owner', 'assistant')
def bulk_upload():
    if request.method == 'GET':
        return render_template('bulk_upload.html', nonce=g.nonce)

    action = request.form.get('action')
    if action == 'preview':
        file = request.files.get('file')
        if not file or file.filename == '':
            flash('❌ No file selected.', 'error')
            return redirect(url_for('inventory.bulk_upload'))

        if not file.filename.lower().endswith('.csv'):
            flash('❌ Only CSV files are allowed.', 'error')
            return redirect(url_for('inventory.bulk_upload'))

        file_content = file.stream.read().decode('utf-8-sig')

        from io import StringIO
        stream = StringIO(file_content)
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        if not fieldnames:
            flash('❌ CSV is empty or malformed.', 'error')
            return redirect(url_for('inventory.bulk_upload'))

        required_headers = {'name', 'sku'}
        if not required_headers.issubset(fieldnames):
            flash('❌ CSV must contain at least "name" and "sku" columns.', 'error')
            return redirect(url_for('inventory.bulk_upload'))

        preview_rows = []
        row_num = 1
        for row in reader:
            row_num += 1
            errors = []
            if not row.get('name', '').strip():
                errors.append('Missing name')
            if not row.get('sku', '').strip():
                errors.append('Missing SKU')
            existing_skus = [r.get('sku') for r in preview_rows if r.get('sku')]
            if row.get('sku') in existing_skus:
                errors.append('Duplicate SKU in file')
            with DB_ENGINE.connect() as conn:
                existing = conn.execute(
                    text("SELECT id FROM inventory_items WHERE account_id = :aid AND sku = :sku"),
                    {"aid": session['account_id'], "sku": row.get('sku')}
                ).first()
                if existing:
                    errors.append('SKU already exists in inventory')

            preview_rows.append({
                'row_num': row_num,
                'data': row,
                'errors': errors,
                'valid': len(errors) == 0
            })
            if len(preview_rows) >= 10:
                break

        session['bulk_upload_data'] = {
            'file_content': file_content,
            'row_count': row_num - 1
        }

        return render_template('bulk_upload_preview.html',
                               preview_rows=preview_rows,
                               total_rows=row_num - 1,
                               nonce=g.nonce)

    elif action == 'confirm':
        stored = session.get('bulk_upload_data')
        if not stored:
            flash('❌ No upload data found. Please upload again.', 'error')
            return redirect(url_for('inventory.bulk_upload'))

        from io import StringIO
        stream = StringIO(stored['file_content'])
        reader = csv.DictReader(stream)

        results = {'success': 0, 'failure': 0, 'errors': []}
        user_id = session['user_id']
        account_id = session['account_id']

        for row_num, row in enumerate(reader, start=2):
            cleaned_row = {k.strip(): v.strip() if isinstance(v, str) else v for k, v in row.items()}

            product_data = {
                'name': cleaned_row.get('name', ''),
                'sku': cleaned_row.get('sku', ''),
                'barcode': cleaned_row.get('barcode', '') or None,
                'category': cleaned_row.get('category', '') or None,
                'description': cleaned_row.get('description', '') or None,
                'current_stock': _safe_float(cleaned_row.get('current_stock'), 0.0),
                'min_stock_level': _safe_int(cleaned_row.get('min_stock_level'), 5),
                'cost_price': _safe_float(cleaned_row.get('cost_price'), 0.0),
                'selling_price': _safe_float(cleaned_row.get('selling_price'), 0.0),
                'supplier': cleaned_row.get('supplier', '') or None,
                'location': cleaned_row.get('location', '') or None,
                'unit_type': cleaned_row.get('unit_type', 'piece').strip(),
                'is_perishable': str(cleaned_row.get('is_perishable', '')).lower() in ('yes', 'true', '1'),
                'expiry_date': cleaned_row.get('expiry_date', '') or None,
                'batch_number': cleaned_row.get('batch_number', '') or None,
                'pack_size': _safe_float(cleaned_row.get('pack_size'), 1.0),
                'weight_kg': _safe_float(cleaned_row.get('weight_kg'), None),
            }

            if not product_data['name'] or not product_data['sku']:
                results['failure'] += 1
                results['errors'].append(f"Row {row_num}: Missing name or SKU")
                continue

            product_id = InventoryManager.add_product(user_id, account_id, product_data)
            if product_id:
                try:
                    with DB_ENGINE.begin() as conn:
                        location_id = _get_or_create_main_location(conn, account_id)
                    if location_id:
                        LocationInventoryManager.add_product_to_location(
                            product_id, location_id, product_data['current_stock'], user_id
                        )
                except Exception as e:
                    current_app.logger.error(f"Bulk upload location assignment failed for product {product_id}: {e}")
                results['success'] += 1
            else:
                results['failure'] += 1
                results['errors'].append(
                    f"Row {row_num}: Failed to add '{product_data['name']}' (SKU: {product_data['sku']})"
                )

        session.pop('bulk_upload_data', None)

        if results['success'] > 0:
            flash(f"✅ Successfully imported {results['success']} product(s).", 'success')
        if results['failure'] > 0:
            flash(f"⚠️ {results['failure']} product(s) failed. See details below.", 'warning')
        if results['errors']:
            session['bulk_upload_errors'] = results['errors']

        return redirect(url_for('inventory.bulk_upload_results'))

    else:
        flash('❌ Invalid action.', 'error')
        return redirect(url_for('inventory.bulk_upload'))


@inventory_bp.route('/bulk_upload_results')
def bulk_upload_results():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    errors = session.pop('bulk_upload_errors', [])
    return render_template('bulk_upload_results.html', errors=errors, nonce=g.nonce)


@inventory_bp.route('/sample_products.csv')
def download_sample_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'name', 'sku', 'barcode', 'category', 'description', 'current_stock',
        'min_stock_level', 'cost_price', 'selling_price', 'supplier', 'location',
        'unit_type', 'is_perishable', 'expiry_date', 'batch_number', 'pack_size', 'weight_kg'
    ])
    writer.writerow([
        'Fresh Milk', 'MILK-001', '7891234567890', 'Dairy', '1L full cream milk', '50',
        '10', '200.00', '250.00', 'Milk Corp', 'Cold Room A',
        'weight', 'Yes', '2026-04-15', 'BATCH-202603', '1.0', '1.0'
    ])
    output.seek(0)
    return Response(output.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment; filename=sample_products.csv'})
