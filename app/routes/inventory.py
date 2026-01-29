#app/routes/inventory.py
import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g, jsonify, Response, current_app
from sqlalchemy import text
from app.services.inventory import InventoryManager
from app.services.utils import random_success_message
from app.services.db import DB_ENGINE # Ensure this import exists
from app import limiter

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

    return render_template("inventory.inventory.html",
                         inventory_items=inventory_items,
                         low_stock_alerts=low_stock_alerts,
                         nonce=g.nonce)

# inventory reports - SIMPLIFIED TO AVOID ERRORS =2
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

#add products = 3
@inventory_bp.route("/add_product", methods=['POST'])
def add_product():
    """Add new product to inventory"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    from app.services.inventory import InventoryManager

    product_data = {
        'name': request.form.get('name'),
        'sku': request.form.get('sku'),
        'category': request.form.get('category'),
        'description': request.form.get('description'),
        'current_stock': int(request.form.get('current_stock', 0)),
        'min_stock_level': int(request.form.get('min_stock_level', 5)),
        'cost_price': float(request.form.get('cost_price', 0)),
        'selling_price': float(request.form.get('selling_price', 0)),
        'supplier': request.form.get('supplier'),
        'location': request.form.get('location')
    }

    product_id = InventoryManager.add_product(session['user_id'], product_data)

    if product_id:
        # If initial stock > 0, use delta to log it (already logged in add_product, but safe)
        initial_stock = int(product_data.get('current_stock', 0))
        if initial_stock > 0:
            InventoryManager.update_stock_delta(
                session['user_id'],
                product_id,
                initial_stock,
                'initial',
                notes='Initial stock on product creation'
            )
        flash(random_success_message('product_added'), 'success')
    else:
        flash('Error adding product. SKU might already exist.', 'error')

    return redirect(url_for('inventory.inventory'))

#delete 4
@inventory_bp.route("/delete_product", methods=['POST'])
def delete_product():
    """Remove product from inventory with audit trail"""
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
        flash('❌ Error removing product', 'error')

    return redirect(url_for('inventory.inventory'))

# API inventory items 5
@inventory_bp.route("/api/inventory_items")
def get_inventory_items_api():
    """API endpoint for inventory items (for invoice form)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, selling_price, current_stock
            FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE AND current_stock > 0
            ORDER BY name
        """), {"user_id": session['user_id']}).fetchall()

    inventory_data = [{
        'id': item[0],
        'name': item[1],
        'price': float(item[2]) if item[2] else 0,
        'stock': item[3]
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
