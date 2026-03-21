#app/routes/purchase.py
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, g, current_app, jsonify
from sqlalchemy import text
from datetime import datetime
import json
from app.services.utils import random_success_message
from app.services.db import DB_ENGINE
from app.services.inventory import InventoryManager
from app.services.invoice_logic_po import prepare_po_data
from app.services.qr_engine import generate_qr_base64
from app.services.pdf_engine import generate_pdf, HAS_WEASYPRINT
from app.services.purchases import get_purchase_orders, get_purchase_order, save_purchase_order
from app.services.suppliers import SupplierManager
from app.services.cache import get_user_profile_cached
from app import generate_simple_qr
from app.extensions import limiter
from app.context_processors import CURRENCY_SYMBOLS
from app.decorators import role_required

purchases_bp = Blueprint('purchases', __name__)

# 1. Create PO form
@purchases_bp.route("/create_purchase_order")
@role_required('owner', 'assistant')
def create_purchase_order():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    account_id = session['account_id']
    inventory_items = InventoryManager.get_inventory_items(account_id)
    suppliers = SupplierManager.get_suppliers(account_id)
    today_str = datetime.today().strftime('%Y-%m-%d')
    return render_template("create_po.html", 
                         inventory_items=inventory_items, 
                         suppliers=suppliers, 
                         today=today_str, 
                         nonce=g.nonce)

# 2. Process PO creation
@purchases_bp.route('/create_po_process', methods=['POST'])
@limiter.limit("10 per minute")
def create_po_process():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    account_id = session['account_id']

    try:
        from app.services.invoice_service import InvoiceService
        service = InvoiceService(user_id)

        po_data, errors = service.create_purchase_order(request.form, request.files)

        if errors:
            for error in errors:
                flash(f"❌ {error}", "error")
            return redirect(url_for('purchases.create_purchase_order'))

        if po_data:
            from app.services.session_storage import SessionStorage
            # Update supplier volume – now using account_id
            SupplierManager.update_volume(account_id, request.form.get('supplier_id'), po_data['grand_total'])
            session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
            session['last_po_ref'] = session_ref
            return redirect(url_for('purchases.po_preview', po_number=po_data['po_number']))

        flash("❌ Failed to create purchase order", "error")
        return redirect(url_for('purchases.create_purchase_order'))

    except Exception as e:
        current_app.logger.error(f"PO creation error: {str(e)}", exc_info=True)
        flash("❌ An unexpected error occurred", "error")
        return redirect(url_for('purchases.create_purchase_order'))

# 3. PO Preview
@purchases_bp.route('/po/preview/<po_number>')
def po_preview(po_number):
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    account_id = session['account_id']
    user_id = session['user_id']

    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE account_id = :aid AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"aid": account_id, "po_number": po_number}).fetchone()

        if not result:
            flash("Purchase order not found", "error")
            return redirect(url_for('purchases.purchase_orders'))

        po_data = json.loads(result[0])
        po_data['po_number'] = po_number
        po_data['invoice_number'] = po_number

        # Enrich supplier details if missing
        if not po_data.get('supplier_email') or not po_data.get('supplier_phone'):
            suppliers = SupplierManager.get_suppliers(account_id)
            match = next((s for s in suppliers if s['name'] == po_data.get('supplier_name')), None)
            if match:
                po_data['supplier_email'] = match.get('email', '')
                po_data['supplier_phone'] = match.get('phone', '')

        # Enrich items with product details
        inventory_items = InventoryManager.get_inventory_items(account_id)
        product_lookup = {str(p['id']): p for p in inventory_items}
        product_lookup.update({int(k): v for k, v in product_lookup.items() if str(k).isdigit()})

        for item in po_data.get('items', []):
            pid = item.get('product_id')
            if pid and pid in product_lookup:
                p = product_lookup[pid]
                item['sku'] = p.get('sku', 'N/A')
                item['name'] = p.get('name', item.get('name', 'Unknown'))
                item['supplier'] = p.get('supplier') or po_data.get('supplier_name') or 'Unknown Supplier'

        user_profile = get_user_profile_cached(user_id)
        user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')
        qr_b64 = generate_simple_qr(po_data)
        html = render_template('purchase_order_pdf.html',
                               data=po_data,
                               preview=True,
                               custom_qr_b64=qr_b64,
                               currency_symbol=user_symbol)

        return render_template('po_preview.html', html=html, data=po_data, po_number=po_number, nonce=g.nonce)

    except Exception as e:
        current_app.logger.error(f"PO preview error: {str(e)}", exc_info=True)
        flash("Error loading purchase order", "error")
        return redirect(url_for('purchases.purchase_orders'))

# 4. Goods Received Note (Receive PO)
@purchases_bp.route("/po/mark_received/<po_number>", methods=['GET', 'POST'])
def mark_po_received(po_number):
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    account_id = session['account_id']

    try:
        po_data = get_purchase_order(account_id, po_number)
        if not po_data:
            flash("❌ Purchase Order not found", "error")
            return redirect(url_for('purchases.purchase_orders'))

        if po_data.get('status', '').lower() == 'received':
            flash("⚠️ This Purchase Order has already been fully received", "warning")
            return redirect(url_for('purchases.purchase_orders'))

        # Fetch already received quantities
        with DB_ENGINE.connect() as conn:
            receipts = conn.execute(text("""
                SELECT product_id, SUM(received_qty) as total_received
                FROM po_receipts
                WHERE user_id = :uid AND po_number = :po_number
                GROUP BY product_id
            """), {"uid": user_id, "po_number": po_number}).fetchall()
        received_map = {r[0]: r[1] for r in receipts}

        # Prepare items
        items = po_data.get('items', [])
        inventory_items = InventoryManager.get_inventory_items(account_id)
        product_map = {item['id']: item['name'] for item in inventory_items}

        for item in items:
            product_id = item['product_id']
            try:
                pid = int(product_id)
            except:
                pid = product_id
            item['ordered_qty'] = item.get('qty', 0)
            item['received_so_far'] = received_map.get(pid, 0)
            item['remaining'] = item['ordered_qty'] - item['received_so_far']
            item['name'] = product_map.get(pid, f"Product {product_id}")

        if request.method == 'GET':
            return render_template("po_receive_partial.html",
                                   po_data=po_data,
                                   po_number=po_number,
                                   items=items,
                                   nonce=g.nonce)

        # POST: process receipt
        added_units = 0
        receipts_to_insert = []
        today = datetime.now().date()

        for item in items:
            product_id = item['product_id']
            form_key = f'receive_qty_{product_id}'
            qty_to_receive = int(request.form.get(form_key, 0))

            if qty_to_receive < 0 or qty_to_receive > item['remaining']:
                flash(f"❌ Invalid quantity for {item['name']}. Max remaining: {item['remaining']}", "error")
                return redirect(url_for('purchases.mark_po_received', po_number=po_number))

            if qty_to_receive > 0:
                receipts_to_insert.append({
                    'product_id': int(product_id),
                    'qty': qty_to_receive
                })
                if InventoryManager.update_stock_delta(
                    user_id,
                    account_id,
                    int(product_id),
                    qty_to_receive,
                    'purchase_receive',
                    po_number,
                    f"Partial receipt for PO {po_number}"
                ):
                    added_units += qty_to_receive
                else:
                    flash(f"⚠️ Stock update failed for {item['name']}.", "warning")

        if receipts_to_insert:
            with DB_ENGINE.begin() as conn:
                for rec in receipts_to_insert:
                    conn.execute(text("""
                        INSERT INTO po_receipts (user_id, po_number, product_id, received_qty, received_date, notes)
                        VALUES (:user_id, :po_number, :product_id, :qty, :date, :notes)
                    """), {
                        "user_id": user_id,
                        "po_number": po_number,
                        "product_id": rec['product_id'],
                        "qty": rec['qty'],
                        "date": today,
                        "notes": f"Received via PO {po_number}"
                    })

        # Determine new status
        with DB_ENGINE.connect() as conn:
            new_receipts = conn.execute(text("""
                SELECT product_id, SUM(received_qty) as total_received
                FROM po_receipts
                WHERE user_id = :user_id AND po_number = :po_number
                GROUP BY product_id
            """), {"user_id": user_id, "po_number": po_number}).fetchall()
        new_received_map = {r[0]: r[1] for r in new_receipts}

        all_fully_received = True
        for item in items:
            pid = int(item['product_id'])
            total_received = new_received_map.get(pid, 0)
            if total_received < item['ordered_qty']:
                all_fully_received = False
                break

        new_status = 'Received' if all_fully_received else 'Partial'
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE purchase_orders SET status = :status
                WHERE user_id = :user_id AND po_number = :po_number
            """), {"user_id": user_id, "po_number": po_number, "status": new_status})

        if all_fully_received:
            flash(f"✅ PO {po_number} fully received! {added_units} units added to stock.", "success")
        else:
            flash(f"📦 PO {po_number} partially received. {added_units} units added to stock.", "info")

        return redirect(url_for('purchases.purchase_orders'))

    except Exception as e:
        current_app.logger.error(f"Error receiving PO {po_number}: {e}", exc_info=True)
        flash("❌ An error occurred while processing receipt.", "error")
        return redirect(url_for('purchases.purchase_orders'))

# 5. Email PO to supplier (placeholder)
@purchases_bp.route('/po/email/<po_number>', methods=['POST'])
def email_po_to_supplier(po_number):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    flash(f'PO {po_number} email functionality coming soon!', 'info')
    return jsonify({'success': True, 'message': 'Email queued'})

# 6. Print preview
@purchases_bp.route('/po/print/<po_number>')
def print_po_preview(po_number):
    return redirect(url_for('purchases.po_preview', po_number=po_number))

# 7. List purchase orders
@purchases_bp.route("/purchase_orders")
def purchase_orders():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    try:
        from app.services.purchases import get_purchase_orders
        account_id = session['account_id']
        page = request.args.get('page', 1, type=int)
        limit = 10
        offset = (page - 1) * limit

        orders = get_purchase_orders(account_id, limit=limit, offset=offset)

        with DB_ENGINE.connect() as conn:
            total_count = conn.execute(text("""
                SELECT COUNT(*) FROM purchase_orders WHERE account_id = :aid
            """), {"aid": account_id}).scalar() or 0

            total_sum = conn.execute(text("""
                SELECT COALESCE(SUM(grand_total), 0) FROM purchase_orders WHERE account_id = :aid
            """), {"aid": account_id}).scalar() or 0.0

            pending_count = conn.execute(text("""
                SELECT COUNT(*) FROM purchase_orders 
                WHERE account_id = :aid AND status = 'pending'
            """), {"aid": account_id}).scalar() or 0

            current_month = datetime.now().month
            current_year = datetime.now().year
            month_count = conn.execute(text("""
                SELECT COUNT(*) FROM purchase_orders 
                WHERE account_id = :aid 
                  AND EXTRACT(MONTH FROM order_date) = :month
                  AND EXTRACT(YEAR FROM order_date) = :year
            """), {"aid": account_id, "month": current_month, "year": current_year}).scalar() or 0

        user_profile = get_user_profile_cached(session['user_id'])
        user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')

        return render_template("purchase_orders.html",
                               orders=orders,
                               current_page=page,
                               limit=limit,
                               total_count=total_count,
                               total_sum=total_sum,
                               pending_count=pending_count,
                               month_count=month_count,
                               currency_symbol=user_symbol,
                               nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Purchase orders route error: {e}")
        flash("Error loading purchase orders", "error")
        return redirect(url_for('main.dashboard'))
