from flask import Blueprint, render_template, session, redirect, url_for, request, flash, g, current_app, jsonify, make_response, send_file
from flask.views import MethodView
from sqlalchemy import text
import json
import io
from app.services.db import DB_ENGINE
from app import limiter, generate_simple_qr
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached
from app.services.inventory import InventoryManager
from app.services.invoice_logic import prepare_invoice_data
from app.services.invoice_logic_po import prepare_po_data
from app.services.qr_engine import generate_qr_base64
from app.services.pdf_engine import generate_pdf, HAS_WEASYPRINT
from app.services.services import InvoiceService
from app.services.number_generator import NumberGenerator
from app.services.purchases import save_purchase_order


sales_bp = Blueprint('sales', __name__)


# 1 create invoice 1
@sales_bp.route('/create_invoice')
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

# 2 preview and download 2
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
                return redirect(url_for('sales.create_invoice'))

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

        return redirect(url_for('sales.create_invoice'))

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
                    return redirect(url_for('purchases.create_purchase_order'))

                if po_data:
                    # Store for preview
                    from app.services.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
                    session['last_po_ref'] = session_ref

                    flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
                    return redirect(url_for('purchases.po_preview', po_number=po_data['po_number']))
            else:
                # Create sales invoice
                invoice_data, errors = service.create_invoice(request.form, request.files)

                if errors:
                    for error in errors:
                        flash(f"❌ {error}", 'error')
                    return redirect(url_for('sales.create_invoice'))

                if invoice_data:
                    # Store for preview
                    from app.services.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_invoice', invoice_data)
                    session['last_invoice_ref'] = session_ref

                    flash(f"✅ Invoice {invoice_data['invoice_number']} created successfully!", "success")
                    return redirect(url_for('sales.invoice_process', preview='true'))

            flash("⚠️ Failed to create document", 'error')
            return redirect(url_for('sales.create_invoice'))

        except Exception as e:
            current_app.logger.error(f"Invoice creation error: {str(e)}",
                                   exc_info=True,
                                   extra={'user_id': user_id})
            flash("⚠️ An unexpected error occurred. Please try again.", 'error')
            return redirect(url_for('sales.create_invoice'))

# Register route
sales_bp.add_url_rule('/invoice/process', view_func=InvoiceView.as_view('invoice_process'), methods=['GET', 'POST'])

# 3 invoice/download/<document_number>') 3
@sales_bp.route('/invoice/download/<document_number>')
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
                return redirect(url_for('purchases.purchase_orders'))

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
                return redirect(url_for('sales.invoice_history'))

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
        return redirect(url_for('sales.invoice_history' if document_type != 'purchase_order' else 'purchase_orders'))

# 4 invoice history 4
@sales_bp.route('/invoice_history')
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

# poll route 5
@sales_bp.route('/invoice/status/<user_id>')
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


#clean up API-6 
@sales_bp.route('/cancel_invoice')
def cancel_invoice():
    """Cancel pending invoice"""
    if 'user_id' in session:
        clear_pending_invoice(session['user_id'])
        session.pop('invoice_finalized', None)
        flash('Invoice cancelled', 'info')
    return redirect(url_for('sales.create_invoice'))

# for view invoice detail button in history
@sales_bp.route('/invoice/preview/<invoice_number>')
def get_invoice_preview(invoice_number):
    if 'user_id' not in session:
        return "Unauthorized", 401

    try:
        from app.services.invoice_service import InvoiceService
        from app.services.utils import generate_simple_qr
        
        service = InvoiceService(session['user_id'])
        # Fetch existing invoice data from your database/storage
        invoice_data = service.get_invoice_by_number(invoice_number)

        if not invoice_data:
            return "Invoice not found", 404

        # Generate QR Code for the specific invoice
        qr_b64 = generate_simple_qr(invoice_data)

        # Render the template
        # 'preview=True' ensures it uses your professional layout logic
        return render_template('invoice_pdf.html',
                             data=invoice_data,
                             custom_qr_b64=qr_b64,
                             currency_symbol="Rs.",
                             fbr_compliant=True,
                             preview=True)
    except Exception as e:
        current_app.logger.error(f"Error fetching invoice preview: {str(e)}")
        return "Error loading preview", 500
