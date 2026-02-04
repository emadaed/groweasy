# ============================================================================
# main.py - COMPLETE FIXED VERSION 29--01-2026 12:23 AM
# ============================================================================
import os
import time
import io
import json
import datetime as dt_module
from datetime import datetime, date, timedelta
from flask import render_template, session, redirect, url_for, request, flash, jsonify, g, send_file, make_response, current_app
from sqlalchemy import text

# Import the Factory and Global Extensions
from app import create_app, limiter, generate_simple_qr
from app.services.db import DB_ENGINE

# Business Logic Services
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached
from app.services.inventory import InventoryManager
from app.services.invoice_logic import prepare_invoice_data
from app.services.invoice_logic_po import prepare_po_data
from app.services.qr_engine import generate_qr_base64
from app.services.pdf_engine import generate_pdf, HAS_WEASYPRINT
from app.services.auth import (
    create_user, verify_user, get_user_profile, 
    update_user_profile, change_user_password, save_user_invoice
)
from app.services.purchases import save_purchase_order, get_purchase_orders, get_suppliers

# Local application
from fbr_integration import FBRInvoice
#config.py later

CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}

app = create_app()


#create po = 1
@app.route("/create_purchase_order")
def create_purchase_order():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    from app.services.inventory import InventoryManager

    # Get inventory items for dropdown/modal
    inventory_items = InventoryManager.get_inventory_items(user_id)

    # Get suppliers (adjust if you have a supplier manager)
    suppliers = get_suppliers(user_id)

    # Today date
    today_str = datetime.today().strftime('%Y-%m-%d')

    return render_template("create_po.html",
                         inventory_items=inventory_items,
                         suppliers=suppliers,
                         today=today_str,
                         nonce=g.nonce)

#create po process ==2 
@app.route('/create_po_process', methods=['POST'])
@limiter.limit("10 per minute")
def create_po_process():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    try:
        from app.services.invoice_service import InvoiceService

        print("DEBUG: Starting PO creation for user", user_id)
        print("DEBUG: Form keys:", list(request.form.keys()))
        print("DEBUG: File keys:", list(request.files.keys()))

        service = InvoiceService(user_id)

        print("DEBUG: Calling service.create_purchase_order...")
        po_data, errors = service.create_purchase_order(request.form, request.files)

        print("DEBUG: Service returned po_data keys:", list(po_data.keys()) if po_data else "None")
        print("DEBUG: Service returned items count:", len(po_data.get('items', [])) if po_data else 0)
        print("DEBUG: Service returned errors:", errors)

        if errors:
            for error in errors:
                flash(f"❌ {error}", "error")
                print("DEBUG: Flashed error:", error)
            return redirect(url_for('create_purchase_order'))

        if po_data:
            print("DEBUG: PO data before save:", po_data)
            print("DEBUG: Items in po_data:", po_data.get('items', []))

            from app.services.session_storage import SessionStorage
            session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
            session['last_po_ref'] = session_ref

            flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
            print("DEBUG: Redirecting to preview for", po_data['po_number'])
            return redirect(url_for('po_preview', po_number=po_data['po_number']))

        flash("❌ Failed to create purchase order", "error")
        print("DEBUG: Failed - no po_data")
        return redirect(url_for('create_purchase_order'))

    except Exception as e:
        current_app.logger.error(f"PO creation error: {str(e)}", exc_info=True)
        print("DEBUG: Exception in PO creation:", str(e))
        flash("❌ An unexpected error occurred", "error")
        return redirect(url_for('create_purchase_order'))

# po preview =3 
@app.route('/po/preview/<po_number>')
def po_preview(po_number):
    """Final Preview & Print - with full product enrichment"""
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
            return redirect(url_for('purchase_orders'))

        po_data = json.loads(result[0])

        po_data['po_number'] = po_number
        po_data['invoice_number'] = po_number

        # === FULL ENRICHMENT ===
        from app.services.inventory import InventoryManager
        inventory_items = InventoryManager.get_inventory_items(user_id)

        product_lookup = {str(p['id']): p for p in inventory_items}
        product_lookup.update({int(k): v for k, v in product_lookup.items() if k.isdigit()})

        for item in po_data.get('items', []):
            pid = item.get('product_id')
            if pid and pid in product_lookup:
                p = product_lookup[pid]
                item['sku'] = p.get('sku', 'N/A')
                item['name'] = p.get('name', item.get('name', 'Unknown'))
                item['supplier'] = p.get('supplier', po_data.get('supplier_name', 'Unknown Supplier'))

        # DEBUG LOGS
        print("✅ PO PREVIEW ENRICHED DATA:")
        for i, item in enumerate(po_data.get('items', []), 1):
            print(f"  Item {i}: name='{item.get('name')}', sku='{item.get('sku')}', supplier='{item.get('supplier')}'")

        qr_b64 = generate_simple_qr(po_data)

        # Use same enriched data for both preview and PDF
        html = render_template('purchase_order_pdf.html',
                               data=po_data,
                               preview=True,
                               custom_qr_b64=qr_b64,
                               currency_symbol=g.get('currency_symbol', 'Rs.'))

        return render_template('po_preview.html',
                               html=html,
                               data=po_data,
                               po_number=po_number,
                               nonce=g.nonce)

    except Exception as e:
        current_app.logger.error(f"PO preview error: {str(e)}", exc_info=True)
        flash("Error loading purchase order", "error")
        return redirect(url_for('purchase_orders'))

# GRN - Goods Received Note (Receive Purchase Order) 4
@app.route("/po/mark_received/<po_number>", methods=['GET', 'POST'])
def mark_po_received(po_number):
    """Handle receiving goods for an existing Purchase Order"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']

    try:
        from app.services.purchases import get_purchase_order
        from app.services.inventory import InventoryManager

        # Load the existing PO data
        po_data = get_purchase_order(user_id, po_number)
        if not po_data:
            flash("❌ Purchase Order not found", "error")
            return redirect(url_for('purchase_orders'))

        # Prevent double-receiving
        if po_data.get('status', '').lower() == 'received':
            flash("⚠️ This Purchase Order has already been received", "warning")
            return redirect(url_for('purchase_orders'))

        # GET request → Show confirmation page
        if request.method == 'GET':
            return render_template("po_receive_confirm.html",
                                   po_data=po_data,
                                   po_number=po_number,
                                   nonce=g.nonce)

        # POST request → User confirmed "Yes, Receive Goods"
        if request.method == 'POST':
            added_units = 0

            # Step 1: Add items to inventory stock
            for item in po_data.get('items', []):
                if item.get('product_id'):
                    qty = int(item.get('qty', 0))
                    if qty > 0:
                        if InventoryManager.update_stock_delta(
                            user_id,
                            item['product_id'],
                            qty,  # positive = increase stock
                            'purchase_receive',
                            po_number,
                            f"Goods received via PO {po_number}"
                        ):
                            added_units += qty

            # Step 2: Update status only — updated_at will be set automatically to NOW()
            try:
                with DB_ENGINE.begin() as conn:
                    conn.execute(text("""
                        UPDATE purchase_orders
                        SET status = 'Received'
                        WHERE user_id = :user_id
                          AND po_number = :po_number
                    """), {"user_id": user_id, "po_number": po_number})

                flash(f"✅ PO {po_number} successfully marked as Received! "
                      f"{added_units} units added to stock.", "success")
            except Exception as e:
                current_app.logger.error(f"Error updating PO status: {e}")
                flash("⚠️ Stock added, but status update failed. Please contact support.", "warning")

            return redirect(url_for('purchase_orders'))

    except Exception as e:
        current_app.logger.error(f"Error receiving PO {po_number}: {e}", exc_info=True)
        flash("❌ An error occurred while receiving goods. Please try again.", "error")
        return redirect(url_for('purchase_orders'))

# Email to supplier 5
@app.route('/po/email/<po_number>', methods=['POST'])
def email_po_to_supplier(po_number):
    """Send PO to supplier via email"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # TODO: Implement email sending
    flash(f'PO {po_number} email functionality coming soon!', 'info')
    return jsonify({'success': True, 'message': 'Email queued'})


# Print preview -6
@app.route('/po/print/<po_number>')
def print_po_preview(po_number):
    """Print preview for PO"""
    return redirect(url_for('po_preview', po_number=po_number))

# purchase order - History FIXED VERSION 7
@app.route("/purchase_orders")
def purchase_orders():
    """Purchase order history with download options - FIXED"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    try:
        from app.services.purchases import get_purchase_orders

        page = request.args.get('page', 1, type=int)
        limit = 10
        offset = (page - 1) * limit

        # Get orders with error handling
        orders = []
        try:
            orders = get_purchase_orders(session['user_id'], limit=limit, offset=offset)

            # Fix date formats for template
            for order in orders:
                if 'order_date' in order and order['order_date']:
                    if isinstance(order['order_date'], str):
                        try:
                            from datetime import datetime
                            order['order_date'] = datetime.strptime(order['order_date'], '%Y-%m-%d')
                        except:
                            pass
        except Exception as e:
            current_app.logger.error(f"Error loading purchase orders: {e}")
            flash("Could not load purchase orders", "warning")

        # Get user's currency for display
        user_profile = get_user_profile_cached(session['user_id'])
        currency_code = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        currency_symbol = CURRENCY_SYMBOLS.get(currency_code, 'Rs.')

        return render_template("purchase_orders.html",
                             orders=orders,
                             current_page=page,
                             currency_symbol=currency_symbol,
                             nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Purchase orders route error: {e}")
        flash("Error loading purchase orders", "error")
        return redirect(url_for('dashboard'))


# PO API-1
@app.route('/api/purchase_order/<po_number>/complete', methods=['POST'])
def complete_purchase_order(po_number):
    """Mark PO as completed - API endpoint"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE purchase_orders
                SET status = 'completed'
                WHERE user_id = :user_id AND po_number = :po_number
            """), {"user_id": session['user_id'], "po_number": po_number})

        return jsonify({'success': True, 'message': f'PO {po_number} marked as completed'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# PO cancel API endpoint -API 2
@app.route('/api/purchase_order/<po_number>/cancel', methods=['POST'])
def cancel_purchase_order(po_number):
    """Cancel purchase order"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        reason = data.get('reason', 'No reason provided')

        with DB_ENGINE.begin() as conn:
            # Update order data with cancellation
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()

            if result:
                order_data = json.loads(result[0])
                order_data['cancellation_reason'] = reason
                order_data['cancelled_at'] = datetime.now().isoformat()

                conn.execute(text("""
                    UPDATE purchase_orders
                    SET status = 'cancelled', order_data = :order_data
                    WHERE user_id = :user_id AND po_number = :po_number
                """), {
                    "user_id": session['user_id'],
                    "po_number": po_number,
                    "order_data": json.dumps(order_data)
                })

        return jsonify({'success': True, 'message': f'PO {po_number} cancelled'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


#API endpoints for better UX API-3
@app.route("/api/purchase_order/<po_number>")
@limiter.limit("30 per minute")
def get_purchase_order_details(po_number):
    """API endpoint to get PO details"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data, status, created_at
                FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()

        if not result:
            return jsonify({'error': 'Purchase order not found'}), 404

        order_data = json.loads(result[0])
        order_data['status'] = result[1]
        order_data['created_at'] = result[2].isoformat() if result[2] else None

        return jsonify(order_data), 200

    except Exception as e:
        current_app.logger.error(f"PO details error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# poll route API-4
@app.route('/invoice/status/<user_id>')
def status(user_id):
    try:
        from app.services.services import InvoiceService
        service = InvoiceService(int(user_id))
        result = service.redis_client.get(f"preview:{user_id}")
        if result:
            return jsonify({'ready': True, 'data': json.loads(result)})
        return jsonify({'ready': False})
    except:
        return jsonify({'ready': False})

#clean up API-5
@app.route('/cancel_invoice')
def cancel_invoice():
    """Cancel pending invoice"""
    if 'user_id' in session:
        clear_pending_invoice(session['user_id'])
        session.pop('invoice_finalized', None)
        flash('Invoice cancelled', 'info')
    return redirect(url_for('create_invoice'))



# 1 create invoice
@app.route('/create_invoice')
def create_invoice():
    """Dedicated route for creating sales invoices ONLY"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    prefill_data = {}
    user_profile = get_user_profile_cached(session['user_id'])

    if user_profile:
        prefill_data = {
            'company_name': user_profile.get('company_name', ''),
            'company_address': user_profile.get('company_address', ''),
            'company_phone': user_profile.get('company_phone', ''),
            'company_email': user_profile.get('email', ''),
            'company_tax_id': user_profile.get('company_tax_id', ''),
            'seller_ntn': user_profile.get('seller_ntn', ''),
            'seller_strn': user_profile.get('seller_strn', ''),
        }

    return render_template('form.html',
                         prefill_data=prefill_data,
                         nonce=g.nonce)


# 2 preview and download 
from flask.views import MethodView
from app.services.services import InvoiceService
from app.services.number_generator import NumberGenerator
from app.services.purchases import save_purchase_order

class InvoiceView(MethodView):
    """Handles invoice creation and preview - RESTful design"""

    def get(self):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

        if 'last_invoice_ref' in session and request.args.get('preview'):
            from app.services.session_storage import SessionStorage
            invoice_data = SessionStorage.get_data(session['user_id'], session['last_invoice_ref'])
            if not invoice_data:
                flash("Invoice preview expired or not found", "error")
                return redirect(url_for('create_invoice'))

            # Generate QR
            qr_b64 = generate_simple_qr(invoice_data)  # or generate_qr_base64 if you have it

            # Render the PDF template directly for preview
            html = render_template('invoice_pdf.html',
                                 data=invoice_data,
                                 custom_qr_b64=qr_b64,
                                 fbr_qr_code=None,  # add if you have
                                 fbr_compliant=True,
                                 currency_symbol="Rs.",
                                 preview=True)  # optional flag if you want preview buttons

            return render_template('invoice_preview.html',
                                 html=html,
                                 data=invoice_data,
                                 nonce=g.nonce)

        return redirect(url_for('create_invoice'))

    def post(self):
        """
        POST /invoice/process - Create invoice or purchase order using service layer
        """
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))

        user_id = session['user_id']
        invoice_type = request.form.get('invoice_type', 'S')

        try:
            from app.services.invoice_service import InvoiceService
            service = InvoiceService(user_id)

            if invoice_type == 'P':
                # Create purchase order
                po_data, errors = service.create_purchase_order(request.form, request.files)

                if errors:
                    for error in errors:
                        flash(f"❌ {error}", 'error')
                    return redirect(url_for('create_purchase_order'))

                if po_data:
                    # Store for preview
                    from app.services.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
                    session['last_po_ref'] = session_ref

                    flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
                    return redirect(url_for('po_preview', po_number=po_data['po_number']))
            else:
                # Create sales invoice
                invoice_data, errors = service.create_invoice(request.form, request.files)

                if errors:
                    for error in errors:
                        flash(f"❌ {error}", 'error')
                    return redirect(url_for('create_invoice'))

                if invoice_data:
                    # Store for preview
                    from app.services.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_invoice', invoice_data)
                    session['last_invoice_ref'] = session_ref

                    flash(f"✅ Invoice {invoice_data['invoice_number']} created successfully!", "success")
                    return redirect(url_for('invoice_process', preview='true'))

            flash("⚠️ Failed to create document", 'error')
            return redirect(url_for('create_invoice'))

        except Exception as e:
            current_app.logger.error(f"Invoice creation error: {str(e)}",
                                   exc_info=True,
                                   extra={'user_id': user_id})
            flash("⚠️ An unexpected error occurred. Please try again.", 'error')
            return redirect(url_for('create_invoice'))



# 3 invoice/download/<document_number>')
@app.route('/invoice/download/<document_number>')
@limiter.limit("10 per minute")
def download_document(document_number):
    """
    Dedicated endpoint for document downloads - FIXED VERSION
    """
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_id = session['user_id']
    document_type = request.args.get('type', 'invoice')  # 'invoice' or 'purchase_order'

    try:
        # Fetch document data
        if document_type == 'purchase_order':
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT order_data, created_at, status, po_number
                    FROM purchase_orders
                    WHERE user_id = :user_id AND po_number = :doc_number
                    ORDER BY created_at DESC LIMIT 1
                """), {"user_id": user_id, "doc_number": document_number}).fetchone()

            if not result:
                flash("❌ Purchase order not found or access denied.", "error")
                return redirect(url_for('purchase_orders'))

            service_data = json.loads(result[0])
            created_at = result[1]
            status = result[2] or 'PENDING'
            po_number = result[3] or document_number

            # Add metadata
            service_data['po_number'] = po_number
            service_data['status'] = status
            service_data['created_at'] = created_at

            # Get user profile for company info
            user_profile = get_user_profile_cached(user_id) or {}
            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
            service_data['company_address'] = user_profile.get('company_address', '')
            service_data['company_phone'] = user_profile.get('company_phone', '')
            service_data['company_email'] = user_profile.get('email', '')

            document_type_name = "Purchase Order"

            # === ENRICH PO ITEMS WITH REAL PRODUCT DATA (same as preview) ===
            from app.services.inventory import InventoryManager
            inventory_items = InventoryManager.get_inventory_items(user_id)

            product_lookup = {}
            for product in inventory_items:
                pid = product.get('id')
                if pid is not None:
                    product_lookup[str(pid)] = product
                    product_lookup[int(pid)] = product

            for item in service_data.get('items', []):
                pid = item.get('product_id')
                if pid is not None and pid in product_lookup:
                    real = product_lookup[pid]
                    item['sku'] = real.get('sku', 'N/A')
                    item['name'] = real.get('name', item.get('name', 'Unknown Product'))
                    item['supplier'] = real.get('supplier', service_data.get('supplier_name', 'Unknown Supplier'))


            # Generate PDF
            from app.services.pdf_generator import generate_purchase_order_pdf
            pdf_bytes = generate_purchase_order_pdf(service_data)

        else:  # Sales Invoice
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT invoice_data, created_at, invoice_number, status
                    FROM user_invoices
                    WHERE user_id = :user_id AND invoice_number = :doc_number
                    ORDER BY created_at DESC LIMIT 1
                """), {"user_id": user_id, "doc_number": document_number}).fetchone()

            if not result:
                flash("❌ Invoice not found or access denied.", "error")
                return redirect(url_for('invoice_history'))

            service_data = json.loads(result[0])
            created_at = result[1]
            invoice_number = result[2] or document_number
            status = result[3] or 'PAID'

            # Add metadata
            service_data['invoice_number'] = invoice_number
            service_data['status'] = status
            service_data['created_at'] = created_at

            # Get user profile for company info
            user_profile = get_user_profile_cached(user_id) or {}
            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
            service_data['company_address'] = user_profile.get('company_address', '')
            service_data['company_phone'] = user_profile.get('company_phone', '')
            service_data['company_email'] = user_profile.get('email', '')

            document_type_name = "Invoice"

            # Generate PDF
            from app.services.pdf_generator import generate_invoice_pdf
            pdf_bytes = generate_invoice_pdf(service_data)

        # Create filename
        import re
        safe_doc_number = re.sub(r'[^\w\-]', '_', document_number)
        timestamp = created_at.strftime('%Y%m%d_%H%M') if created_at else datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{document_type_name.replace(' ', '_')}_{safe_doc_number}_{timestamp}.pdf"

        # Create response
        response = make_response(send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        ))

        # Security headers
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'

        return response

    except Exception as e:
        current_app.logger.error(f"Download error: {str(e)}", exc_info=True)
        flash("❌ Download failed. Please try again.", 'error')
        return redirect(url_for('invoice_history' if document_type != 'purchase_order' else 'purchase_orders'))

# 4 invoice history
@app.route("/invoice_history")
def invoice_history():
    """Invoice history and management page"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    limit = 20  # Invoices per page
    offset = (page - 1) * limit

    user_id = session['user_id']

    with DB_ENGINE.connect() as conn:
        # Base query
        base_sql = '''
            SELECT id, invoice_number, client_name, invoice_date, due_date, grand_total, status, created_at
            FROM user_invoices
            WHERE user_id = :user_id
        '''
        params = {"user_id": user_id}

        # Add search if provided
        if search:
            base_sql += ' AND (invoice_number ILIKE :search OR client_name ILIKE :search)'
            params["search"] = f"%{search}%"

        # Get total count for pagination
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS count_query"
        total_invoices = conn.execute(text(count_sql), params).scalar()

        # Get paginated invoices
        invoices_sql = base_sql + '''
            ORDER BY invoice_date DESC, created_at DESC
            LIMIT :limit OFFSET :offset
        '''
        params.update({"limit": limit, "offset": offset})
        invoices_result = conn.execute(text(invoices_sql), params).fetchall()

    # Convert to list of dicts for template
    invoices = []
    for row in invoices_result:
        invoices.append({
            'id': row[0],
            'invoice_number': row[1],
            'client_name': row[2],
            'invoice_date': row[3],
            'due_date': row[4],
            'grand_total': float(row[5]) if row[5] else 0.0,
            'status': row[6],
            'created_at': row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else ''
        })

    total_pages = (total_invoices + limit - 1) // limit  # Ceiling division

    return render_template(
        "invoice_history.html",
        invoices=invoices,
        current_page=page,
        total_pages=total_pages,
        search_query=search,
        total_invoices=total_invoices,
        nonce=g.nonce
    )

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
    #app.run(host="0.0.0.0", port=8080, debug=False)
