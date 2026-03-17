#app/routes/inventory.py
import time
import csv
import io
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g, jsonify, Response, current_app
from sqlalchemy import text
from app.services.inventory import InventoryManager
from app.services.utils import random_success_message
from flask import Response
from app.services.db import DB_ENGINE # Ensure this import exists
from app.extensions import limiter

inventory_bp = Blueprint('inventory', __name__)

# INVENTORY =1
@inventory_bp.route("/inventory")
def inventory():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, sku, category, current_stock, min_stock_level,
                   cost_price, selling_price, supplier, location
            FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE
            ORDER BY name
        """), {"user_id": user_id}).fetchall()

    inventory_items = [dict(row._mapping) for row in items]

    low_stock_alerts = InventoryManager.get_low_stock_alerts(user_id)

    return render_template("inventory.html",
                         inventory_items=inventory_items,
                         low_stock_alerts=low_stock_alerts,
                         nonce=g.nonce)

# inventory reports - SIMPLIFIED TO AVOID ERRORS =2 currently unused.
@inventory_bp.route("/inventory_reports")
def inventory_reports():
    """Inventory analytics and reports dashboard - SIMPLIFIED"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    try:
        from app.services.reports import InventoryReports
        # Try to get reports, but don't crash if they fail
        bcg_matrix = []
        turnover = []
        profitability = []
        slow_movers = []

        try:
            bcg_matrix = InventoryReports.get_bcg_matrix(session['user_id'])
        except:
            pass

        try:
            turnover = InventoryReports.get_stock_turnover(session['user_id'], days=30)
        except:
            pass

        try:
            profitability = InventoryReports.get_profitability_analysis(session['user_id'])
        except:
            pass

        try:
            slow_movers = InventoryReports.get_slow_movers(session['user_id'], days_threshold=90)
        except:
            pass

        return render_template("inventory_reports.html",
                             bcg_matrix=bcg_matrix,
                             turnover=turnover[:10],  # Top 10
                             profitability=profitability[:10],  # Top 10
                             slow_movers=slow_movers,
                             nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Inventory reports error: {e}")
        flash("Reports temporarily unavailable", "info")
        return redirect(url_for('inventory.inventory'))

# app/routes/inventory.py add product 4
@inventory_bp.route("/add_product", methods=['POST'])
def add_product():
    """Add new product to inventory — NOW WITH ALL STANDARD FIELDS"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    from app.services.inventory import InventoryManager

    # Safe number converter (already had this)
    def safe_num(val, func, default=0):
        if val is None or str(val).strip() == '':
            return default
        try:
            return func(val)
        except (ValueError, TypeError):
            return default

    # NEW FIELDS PARSING
    product_data = {
        'name': request.form.get('name'),
        'sku': request.form.get('sku'),
        'category': request.form.get('category'),
        'description': request.form.get('description'),
        'current_stock': safe_num(request.form.get('current_stock'), int, 0),
        'min_stock_level': safe_num(request.form.get('min_stock_level'), int, 5),
        'cost_price': safe_num(request.form.get('cost_price'), float, 0.0),
        'selling_price': safe_num(request.form.get('selling_price'), float, 0.0),
        'supplier': request.form.get('supplier'),
        'location': request.form.get('location'),

        # === NEW STANDARD INVENTORY FIELDS ===
        'unit_type': request.form.get('unit_type', 'piece'),
        'is_perishable': 'is_perishable' in request.form,
        'expiry_date': request.form.get('expiry_date') or None,
        'batch_number': request.form.get('batch_number', '').strip() or None,
        'barcode': request.form.get('barcode', '').strip() or None,
        'pack_size': safe_num(request.form.get('pack_size'), float, 1.0),
        'weight_kg': safe_num(request.form.get('weight_kg'), float, None),
    }

    # Price validation (you already had this)
    if product_data['selling_price'] < product_data['cost_price']:
        flash('❌ Selling price cannot be less than cost price.', 'error')
        return redirect(url_for('inventory.inventory'))

    product_id = InventoryManager.add_product(session['user_id'], product_data)

    if product_id:
        flash('✅ Product added successfully!', 'success')
    else:
        flash('❌ Error adding product. SKU might already exist.', 'error')

    return redirect(url_for('inventory.inventory'))

#delete 4
@inventory_bp.route("/delete_product", methods=['POST'])
def delete_product():
    """Soft delete a product (mark inactive) with audit trail"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    from app.services.inventory import InventoryManager

    product_id = request.form.get('product_id')
    reason = request.form.get('reason')
    notes = request.form.get('notes', '')

    full_reason = f"{reason}. {notes}".strip()

    success = InventoryManager.delete_product(session['user_id'], product_id, full_reason)

    if success:
        flash('✅ Product removed successfully', 'success')
    else:
        flash('❌ Error removing product – it may already be deleted.', 'error')

    return redirect(url_for('inventory.inventory'))

# API inventory items 5
@inventory_bp.route("/api/inventory_items")
def get_inventory_items_api():
    """API endpoint for invoice form - now includes sku & unit_type"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, sku, selling_price, current_stock, unit_type
            FROM inventory_items
            WHERE user_id = :user_id 
              AND is_active = TRUE 
              AND current_stock > 0
            ORDER BY name
        """), {"user_id": session['user_id']}).fetchall()
    
    inventory_data = [{
        'id': str(item[0]),               # string to be safe with JS Set
        'name': item[1],
        'sku': item[2] or '',
        'price': float(item[3]) if item[3] else 0.0,
        'stock': int(item[4]),
        'unit_type': item[5] or 'piece'
    } for item in items]
    
    return jsonify(inventory_data)

# stock adjustment - FINAL WORKING VERSION 6
@inventory_bp.route("/adjust_stock_audit", methods=['POST'])
@limiter.limit("10 per minute")
def adjust_stock_audit():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    product_id = request.form.get('product_id')
    adjustment_type = request.form.get('adjustment_type')
    quantity = int(request.form.get('quantity', 0))
    new_cost_price = request.form.get('new_cost_price')
    new_selling_price = request.form.get('new_selling_price')
    reason = request.form.get('reason', 'Stock adjustment')
    notes = request.form.get('notes', '')

    try:
        from app.services.inventory import InventoryManager
        from flask import current_app as app  # ← Fix logger

        # Get product - use your existing method
        product = InventoryManager.get_product_details(user_id, product_id)
        if not product:
            flash('❌ Product not found', 'error')
            return redirect(url_for('inventory.inventory'))

        current_stock = product['current_stock']
        product_name = product['name']

        # Calculate delta
        if adjustment_type == 'add_stock':
            delta = +quantity
            movement_type = 'stock_in'
        elif adjustment_type == 'remove_stock':
            delta = -quantity
            movement_type = 'stock_out'
        elif adjustment_type == 'damaged':
            delta = -quantity
            movement_type = 'damaged'
        elif adjustment_type == 'found_stock':
            delta = +quantity
            movement_type = 'found'
        elif adjustment_type == 'set_stock':
            delta = quantity - current_stock
            movement_type = 'adjustment'
        else:
            flash('❌ Invalid adjustment type', 'error')
            return redirect(url_for('inventory.inventory'))

        # Update stock using delta
        success = InventoryManager.update_stock_delta(
            user_id=user_id,
            product_id=product_id,
            quantity_delta=delta,
            movement_type=movement_type,
            reference_id=f"ADJ-{int(time.time())}",
            notes=f"{reason}: {notes}".strip()
        )

        # Update prices
        if success and (new_cost_price or new_selling_price):
            updates = {}
            if new_cost_price and new_cost_price.strip():
                updates['cost_price'] = float(new_cost_price)
            if new_selling_price and new_selling_price.strip():
                updates['selling_price'] = float(new_selling_price)

            if updates:
                with DB_ENGINE.begin() as conn:
                    set_clause = ', '.join(f"{k} = :{k}" for k in updates)
                    params = updates.copy()
                    params.update({"product_id": product_id, "user_id": user_id})
                    conn.execute(text(f"UPDATE inventory_items SET {set_clause} WHERE id = :product_id AND user_id = :user_id"), params)

        if success:
            new_stock = current_stock + delta
            flash(f'✅ {product_name} adjusted! Stock: {current_stock} → {new_stock}', 'success')
        else:
            flash('❌ Failed to update stock (negative not allowed)', 'error')

        return redirect(url_for('inventory.inventory'))

    except Exception as e:
        app.logger.error(f"Stock adjustment error: {e}", exc_info=True)
        flash('❌ Error updating product', 'error')
        return redirect(url_for('inventory.inventory'))

# inventory report =7
@inventory_bp.route("/download_inventory_report")
def download_inventory_report():
    """Download inventory as CSV"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    from app.services.inventory import InventoryManager
    import csv
    import io

    inventory_data = InventoryManager.get_inventory_report(session['user_id'])

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(['Product Name', 'SKU', 'Category', 'Current Stock', 'Min Stock',
                    'Cost Price', 'Selling Price', 'Supplier', 'Location'])

    # Write data
    for item in inventory_data:
        writer.writerow([
            item['name'], item['sku'], item['category'], item['current_stock'],
            item['min_stock'], item['cost_price'], item['selling_price'],
            item['supplier'], item['location']
        ])

    # Return CSV file
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=inventory_report.csv"}
    )


# new function bulk upload

@inventory_bp.route('/bulk_upload', methods=['GET', 'POST'])
@limiter.limit("5 per hour")
def bulk_upload():
    """Bulk import products from a CSV file."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if request.method == 'GET':
        return render_template('bulk_upload.html', nonce=g.nonce)

    # Handle POST
    if 'file' not in request.files:
        flash('❌ No file selected.', 'error')
        return redirect(request.url)

    file = request.files['file']
    if file.filename == '':
        flash('❌ Empty file name.', 'error')
        return redirect(request.url)

    if not file.filename.lower().endswith('.csv'):
        flash('❌ Only CSV files are allowed.', 'error')
        return redirect(request.url)

    # Optional: size limit (2 MB)
    if file.content_length and file.content_length > 2 * 1024 * 1024:
        flash('❌ File too large (max 2 MB).', 'error')
        return redirect(request.url)

    # Read and process CSV
    stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
    reader = csv.DictReader(stream)

    # Validate headers
    required_headers = {'name', 'sku'}
    if not required_headers.issubset(reader.fieldnames or []):
        flash('❌ CSV must contain at least "name" and "sku" columns.', 'error')
        return redirect(request.url)

    results = {'success': 0, 'failure': 0, 'errors': []}
    user_id = session['user_id']

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        product_data = {
            'name': row.get('name', '').strip(),
            'sku': row.get('sku', '').strip(),
            'category': row.get('category', '').strip() or None,
            'description': row.get('description', '').strip() or None,
            'current_stock': _safe_int(row.get('current_stock'), 0),
            'min_stock_level': _safe_int(row.get('min_stock_level'), 5),
            'cost_price': _safe_float(row.get('cost_price'), 0.0),
            'selling_price': _safe_float(row.get('selling_price'), 0.0),
            'supplier': row.get('supplier', '').strip() or None,
            'location': row.get('location', '').strip() or None,
        }

        if not product_data['name'] or not product_data['sku']:
            results['failure'] += 1
            results['errors'].append(f"Row {row_num}: Missing name or SKU")
            continue

        product_id = InventoryManager.add_product(user_id, product_data)
        if product_id:
            results['success'] += 1
        else:
            results['failure'] += 1
            results['errors'].append(f"Row {row_num}: SKU '{product_data['sku']}' may already exist or invalid data")

    if results['success'] > 0:
        flash(f"✅ Successfully imported {results['success']} product(s).", 'success')
    if results['failure'] > 0:
        flash(f"⚠️ {results['failure']} product(s) failed. See details below.", 'warning')

    if results['errors']:
        session['bulk_upload_errors'] = results['errors']

    return redirect(url_for('inventory.bulk_upload_results'))

@inventory_bp.route('/bulk_upload_results')
def bulk_upload_results():
    """Show detailed results after bulk upload."""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    errors = session.pop('bulk_upload_errors', [])
    return render_template('bulk_upload_results.html', errors=errors, nonce=g.nonce)

@inventory_bp.route('/sample_products.csv')
def download_sample_csv():
    """Provide a sample CSV template for users."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['name', 'sku', 'category', 'description', 'current_stock',
                     'min_stock_level', 'cost_price', 'selling_price', 'supplier', 'location'])
    writer.writerow(['Example Product', 'EX-123', 'Electronics', 'High-quality item', '50',
                     '10', '25.00', '39.99', 'Acme Inc.', 'Warehouse A'])
    writer.writerow(['Another Product', 'AN-456', 'Office Supplies', '', '100',
                     '20', '5.50', '12.00', '', 'Shelf B'])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=sample_products.csv'}
    )

# Helper conversion functions (place at bottom of the file)
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
