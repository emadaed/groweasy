from flask import Blueprint, render_template, session, redirect, url_for, request, flash, g, current_app, jsonify
from sqlalchemy import text
from datetime import datetime
import json

# Internal imports from main.py
from app.services.utils import random_success_message
from app.services.db import DB_ENGINE
from app.services.inventory import InventoryManager
from app.services.invoice_logic_po import prepare_po_data
from app.services.qr_engine import generate_qr_base64
from app.services.pdf_engine import generate_pdf, HAS_WEASYPRINT
from app.services.purchases import get_purchase_orders, get_purchase_order,save_purchase_order,get_suppliers
#from app.services.suppliers import SupplierManager

from app.services.cache import get_user_profile_cached
from app import limiter, generate_simple_qr

purchases_bp = Blueprint('purchases', __name__)

CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}
#create po 1
@purchases_bp.route("/create_purchase_order")
def create_purchase_order():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    inventory_items = InventoryManager.get_inventory_items(user_id)
    suppliers = get_suppliers(user_id)
    #suppliers = SupplierManager.get_suppliers(user_id)
    today_str = datetime.today().strftime('%Y-%m-%d')
    return render_template("create_po.html", inventory_items=inventory_items, suppliers=suppliers, today=today_str, nonce=g.nonce)

# create po process ==2 
@purchases_bp.route('/create_po_process', methods=['POST'])
@limiter.limit("10 per minute")
def create_po_process():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    
    try:
        
        from app.services.invoice_service import InvoiceService
        service = InvoiceService(user_id)
        
        # Pass our
        po_data, errors = service.create_purchase_order(request.form, request.files)
        
        if errors:
            for error in errors:
                flash(f"❌ {error}", "error")
            return redirect(url_for('purchases.create_purchase_order'))
            
        if po_data:
            from app.services.session_storage import SessionStorage
            session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
            session['last_po_ref'] = session_ref
            flash(f"✅ PO {po_data['po_number']} created for {form_data.get('supplier_name')}!", "success")
            return redirect(url_for('purchases.po_preview', po_number=po_data['po_number']))
            
        flash("❌ Failed to create purchase order", "error")
        return redirect(url_for('purchases.create_purchase_order'))
        
    except Exception as e:
        current_app.logger.error(f"PO creation error: {str(e)}", exc_info=True)
        flash("❌ An unexpected error occurred", "error")
        return redirect(url_for('purchases.create_purchase_order'))
# po preview =3 
@purchases_bp.route('/po/preview/<po_number>')
def po_preview(po_number):
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"user_id": user_id, "po_number": po_number}).fetchone()
        if not result:
            flash("Purchase order not found", "error")
            return redirect(url_for('purchases.purchase_orders'))
        po_data = json.loads(result[0])
        po_data['po_number'] = po_number
        po_data['invoice_number'] = po_number
        inventory_items = InventoryManager.get_inventory_items(user_id)
        product_lookup = {str(p['id']): p for p in inventory_items}
        product_lookup.update({int(k): v for k, v in product_lookup.items() if str(k).isdigit()})
        for item in po_data.get('items', []):
            pid = item.get('product_id')
            if pid and pid in product_lookup:
                p = product_lookup[pid]
                item['sku'] = p.get('sku', 'N/A')
                item['name'] = p.get('name', item.get('name', 'Unknown'))
                item['supplier'] = p.get('supplier', po_data.get('supplier_name', 'Unknown Supplier'))
        qr_b64 = generate_simple_qr(po_data)
        html = render_template('purchase_order_pdf.html', data=po_data, preview=True, custom_qr_b64=qr_b64, currency_symbol=g.get('currency_symbol', 'Rs.'))
        return render_template('po_preview.html', html=html, data=po_data, po_number=po_number, nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"PO preview error: {str(e)}", exc_info=True)
        flash("Error loading purchase order", "error")
        return redirect(url_for('purchases.purchase_orders'))

# GRN - Goods Received Note (Receive Purchase Order) 4
@purchases_bp.route("/po/mark_received/<po_number>", methods=['GET', 'POST'])
def mark_po_received(po_number):
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    try:
        po_data = get_purchase_order(user_id, po_number)
        if not po_data:
            flash("❌ Purchase Order not found", "error")
            return redirect(url_for('purchases.purchase_orders'))
        if po_data.get('status', '').lower() == 'received':
            flash("⚠️ This Purchase Order has already been received", "warning")
            return redirect(url_for('purchases.purchase_orders'))
        if request.method == 'GET':
            return render_template("po_receive_confirm.html", po_data=po_data, po_number=po_number, nonce=g.nonce)
        if request.method == 'POST':
            added_units = 0
            for item in po_data.get('items', []):
                if item.get('product_id'):
                    qty = int(item.get('qty', 0))
                    if qty > 0:
                        if InventoryManager.update_stock_delta(user_id, item['product_id'], qty, 'purchase_receive', po_number, f"Goods received via PO {po_number}"):
                            added_units += qty
            try:
                with DB_ENGINE.begin() as conn:
                    conn.execute(text("""
                        UPDATE purchase_orders SET status = 'Received'
                        WHERE user_id = :user_id AND po_number = :po_number
                    """), {"user_id": user_id, "po_number": po_number})
                flash(f"✅ PO {po_number} successfully marked as Received! {added_units} units added to stock.", "success")
            except Exception as e:
                current_app.logger.error(f"Error updating PO status: {e}")
                flash("⚠️ Stock added, but status update failed.", "warning")
            return redirect(url_for('purchases.purchase_orders'))
    except Exception as e:
        current_app.logger.error(f"Error receiving PO {po_number}: {e}", exc_info=True)
        flash("❌ An error occurred while receiving goods.", "error")
        return redirect(url_for('purchases.purchase_orders'))

# Email to supplier 5
@purchases_bp.route('/po/email/<po_number>', methods=['POST'])
def email_po_to_supplier(po_number):
    """Send PO to supplier via email"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # TODO: Implement email sending
    flash(f'PO {po_number} email functionality coming soon!', 'info')
    return jsonify({'success': True, 'message': 'Email queued'})


# Print preview -6
@purchases_bp.route('/po/print/<po_number>')
def print_po_preview(po_number):
    """Print preview for PO"""
    return redirect(url_for('purchases.po_preview', po_number=po_number))

#History 7
@purchases_bp.route("/purchase_orders")
def purchase_orders():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    try:
        from app.services.purchases import get_purchase_orders
        page = request.args.get('page', 1, type=int)
        limit = 10
        offset = (page - 1) * limit
        orders = []
        try:
            orders = get_purchase_orders(session['user_id'], limit=limit, offset=offset)
            for order in orders:
                if 'order_date' in order and order['order_date']:
                    if isinstance(order['order_date'], str):
                        try:
                            order['order_date'] = datetime.strptime(order['order_date'], '%Y-%m-%d')
                        except: pass
        except Exception as e:
            current_app.logger.error(f"Error loading purchase orders: {e}")
            flash("Could not load purchase orders", "warning")
        user_profile = get_user_profile_cached(session['user_id'])
        currency_code = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        currency_symbol = CURRENCY_SYMBOLS.get(currency_code, 'Rs.')
        return render_template("purchase_orders.html", orders=orders, current_page=page, currency_symbol=currency_symbol, nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Purchase orders route error: {e}")
        flash("Error loading purchase orders", "error")
        return redirect(url_for('main.dashboard'))
