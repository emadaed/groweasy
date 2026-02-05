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
##from app.services.db import DB_ENGINE

# Business Logic Services
##from app.services.utils import random_success_message
##from app.services.cache import get_user_profile_cached
##from app.services.inventory import InventoryManager
##from app.services.invoice_logic import prepare_invoice_data
##from app.services.invoice_logic_po import prepare_po_data
##from app.services.qr_engine import generate_qr_base64
##from app.services.pdf_engine import generate_pdf, HAS_WEASYPRINT
##from app.services.auth import create_user, verify_user, get_user_profile, update_user_profile, change_user_password, save_user_invoice

##from app.services.purchases import save_purchase_order, get_purchase_orders, get_suppliers

# Local application
from fbr_integration import FBRInvoice
from app.services.services import InvoiceService

#config.py later

CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}

app = create_app()


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



### 1 create invoice 1
##@app.route('/create_invoice')
##def create_invoice():
##    """Dedicated route for creating sales invoices ONLY"""
##    if 'user_id' not in session:
##        return redirect(url_for('auth.login'))
##
##    prefill_data = {}
##    user_profile = get_user_profile_cached(session['user_id'])
##
##    if user_profile:
##        prefill_data = {
##            'company_name': user_profile.get('company_name', ''),
##            'company_address': user_profile.get('company_address', ''),
##            'company_phone': user_profile.get('company_phone', ''),
##            'company_email': user_profile.get('email', ''),
##            'company_tax_id': user_profile.get('company_tax_id', ''),
##            'seller_ntn': user_profile.get('seller_ntn', ''),
##            'seller_strn': user_profile.get('seller_strn', ''),
##        }
##
##    return render_template('form.html',
##                         prefill_data=prefill_data,
##                         nonce=g.nonce)
##

### 2 preview and download 2
##from flask.views import MethodView
##from app.services.services import InvoiceService
##from app.services.number_generator import NumberGenerator
##from app.services.purchases import save_purchase_order
##
##class InvoiceView(MethodView):
##    """Handles invoice creation and preview - RESTful design"""
##
##    def get(self):
##        if 'user_id' not in session:
##            return redirect(url_for('auth.login'))
##
##        if 'last_invoice_ref' in session and request.args.get('preview'):
##            from app.services.session_storage import SessionStorage
##            invoice_data = SessionStorage.get_data(session['user_id'], session['last_invoice_ref'])
##            if not invoice_data:
##                flash("Invoice preview expired or not found", "error")
##                return redirect(url_for('create_invoice'))
##
##            # Generate QR
##            qr_b64 = generate_simple_qr(invoice_data)  # or generate_qr_base64 if you have it
##
##            # Render the PDF template directly for preview
##            html = render_template('invoice_pdf.html',
##                                 data=invoice_data,
##                                 custom_qr_b64=qr_b64,
##                                 fbr_qr_code=None,  # add if you have
##                                 fbr_compliant=True,
##                                 currency_symbol="Rs.",
##                                 preview=True)  # optional flag if you want preview buttons
##
##            return render_template('invoice_preview.html',
##                                 html=html,
##                                 data=invoice_data,
##                                 nonce=g.nonce)
##
##        return redirect(url_for('create_invoice'))
##
##    def post(self):
##        """
##        POST /invoice/process - Create invoice or purchase order using service layer
##        """
##        if 'user_id' not in session:
##            return redirect(url_for('auth.login'))
##
##        user_id = session['user_id']
##        invoice_type = request.form.get('invoice_type', 'S')
##
##        try:
##            from app.services.invoice_service import InvoiceService
##            service = InvoiceService(user_id)
##
##            if invoice_type == 'P':
##                # Create purchase order
##                po_data, errors = service.create_purchase_order(request.form, request.files)
##
##                if errors:
##                    for error in errors:
##                        flash(f"❌ {error}", 'error')
##                    return redirect(url_for('create_purchase_order'))
##
##                if po_data:
##                    # Store for preview
##                    from app.services.session_storage import SessionStorage
##                    session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
##                    session['last_po_ref'] = session_ref
##
##                    flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
##                    return redirect(url_for('po_preview', po_number=po_data['po_number']))
##            else:
##                # Create sales invoice
##                invoice_data, errors = service.create_invoice(request.form, request.files)
##
##                if errors:
##                    for error in errors:
##                        flash(f"❌ {error}", 'error')
##                    return redirect(url_for('create_invoice'))
##
##                if invoice_data:
##                    # Store for preview
##                    from app.services.session_storage import SessionStorage
##                    session_ref = SessionStorage.store_large_data(user_id, 'last_invoice', invoice_data)
##                    session['last_invoice_ref'] = session_ref
##
##                    flash(f"✅ Invoice {invoice_data['invoice_number']} created successfully!", "success")
##                    return redirect(url_for('invoice_process', preview='true'))
##
##            flash("⚠️ Failed to create document", 'error')
##            return redirect(url_for('create_invoice'))
##
##        except Exception as e:
##            current_app.logger.error(f"Invoice creation error: {str(e)}",
##                                   exc_info=True,
##                                   extra={'user_id': user_id})
##            flash("⚠️ An unexpected error occurred. Please try again.", 'error')
##            return redirect(url_for('create_invoice'))
##
##
##
### 3 invoice/download/<document_number>') 3
##@app.route('/invoice/download/<document_number>')
##@limiter.limit("10 per minute")
##def download_document(document_number):
##    """
##    Dedicated endpoint for document downloads - FIXED VERSION
##    """
##    if 'user_id' not in session:
##        return redirect(url_for('auth.login'))
##
##    user_id = session['user_id']
##    document_type = request.args.get('type', 'invoice')  # 'invoice' or 'purchase_order'
##
##    try:
##        # Fetch document data
##        if document_type == 'purchase_order':
##            with DB_ENGINE.connect() as conn:
##                result = conn.execute(text("""
##                    SELECT order_data, created_at, status, po_number
##                    FROM purchase_orders
##                    WHERE user_id = :user_id AND po_number = :doc_number
##                    ORDER BY created_at DESC LIMIT 1
##                """), {"user_id": user_id, "doc_number": document_number}).fetchone()
##
##            if not result:
##                flash("❌ Purchase order not found or access denied.", "error")
##                return redirect(url_for('purchase_orders'))
##
##            service_data = json.loads(result[0])
##            created_at = result[1]
##            status = result[2] or 'PENDING'
##            po_number = result[3] or document_number
##
##            # Add metadata
##            service_data['po_number'] = po_number
##            service_data['status'] = status
##            service_data['created_at'] = created_at
##
##            # Get user profile for company info
##            user_profile = get_user_profile_cached(user_id) or {}
##            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
##            service_data['company_address'] = user_profile.get('company_address', '')
##            service_data['company_phone'] = user_profile.get('company_phone', '')
##            service_data['company_email'] = user_profile.get('email', '')
##
##            document_type_name = "Purchase Order"
##
##            # === ENRICH PO ITEMS WITH REAL PRODUCT DATA (same as preview) ===
##            from app.services.inventory import InventoryManager
##            inventory_items = InventoryManager.get_inventory_items(user_id)
##
##            product_lookup = {}
##            for product in inventory_items:
##                pid = product.get('id')
##                if pid is not None:
##                    product_lookup[str(pid)] = product
##                    product_lookup[int(pid)] = product
##
##            for item in service_data.get('items', []):
##                pid = item.get('product_id')
##                if pid is not None and pid in product_lookup:
##                    real = product_lookup[pid]
##                    item['sku'] = real.get('sku', 'N/A')
##                    item['name'] = real.get('name', item.get('name', 'Unknown Product'))
##                    item['supplier'] = real.get('supplier', service_data.get('supplier_name', 'Unknown Supplier'))
##
##
##            # Generate PDF
##            from app.services.pdf_generator import generate_purchase_order_pdf
##            pdf_bytes = generate_purchase_order_pdf(service_data)
##
##        else:  # Sales Invoice
##            with DB_ENGINE.connect() as conn:
##                result = conn.execute(text("""
##                    SELECT invoice_data, created_at, invoice_number, status
##                    FROM user_invoices
##                    WHERE user_id = :user_id AND invoice_number = :doc_number
##                    ORDER BY created_at DESC LIMIT 1
##                """), {"user_id": user_id, "doc_number": document_number}).fetchone()
##
##            if not result:
##                flash("❌ Invoice not found or access denied.", "error")
##                return redirect(url_for('invoice_history'))
##
##            service_data = json.loads(result[0])
##            created_at = result[1]
##            invoice_number = result[2] or document_number
##            status = result[3] or 'PAID'
##
##            # Add metadata
##            service_data['invoice_number'] = invoice_number
##            service_data['status'] = status
##            service_data['created_at'] = created_at
##
##            # Get user profile for company info
##            user_profile = get_user_profile_cached(user_id) or {}
##            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
##            service_data['company_address'] = user_profile.get('company_address', '')
##            service_data['company_phone'] = user_profile.get('company_phone', '')
##            service_data['company_email'] = user_profile.get('email', '')
##
##            document_type_name = "Invoice"
##
##            # Generate PDF
##            from app.services.pdf_generator import generate_invoice_pdf
##            pdf_bytes = generate_invoice_pdf(service_data)
##
##        # Create filename
##        import re
##        safe_doc_number = re.sub(r'[^\w\-]', '_', document_number)
##        timestamp = created_at.strftime('%Y%m%d_%H%M') if created_at else datetime.now().strftime('%Y%m%d_%H%M')
##        filename = f"{document_type_name.replace(' ', '_')}_{safe_doc_number}_{timestamp}.pdf"
##
##        # Create response
##        response = make_response(send_file(
##            io.BytesIO(pdf_bytes),
##            as_attachment=True,
##            download_name=filename,
##            mimetype='application/pdf'
##        ))
##
##        # Security headers
##        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
##        response.headers['Pragma'] = 'no-cache'
##        response.headers['Expires'] = '0'
##        response.headers['X-Content-Type-Options'] = 'nosniff'
##        response.headers['X-Frame-Options'] = 'DENY'
##
##        return response
##
##    except Exception as e:
##        current_app.logger.error(f"Download error: {str(e)}", exc_info=True)
##        flash("❌ Download failed. Please try again.", 'error')
##        return redirect(url_for('invoice_history' if document_type != 'purchase_order' else 'purchase_orders'))
##
### 4 invoice history 4
##@app.route("/invoice_history")
##def invoice_history():
##    """Invoice history and management page"""
##    if 'user_id' not in session:
##        return redirect(url_for('auth.login'))
##
##    # Get pagination parameters
##    page = request.args.get('page', 1, type=int)
##    search = request.args.get('search', '').strip()
##    limit = 20  # Invoices per page
##    offset = (page - 1) * limit
##
##    user_id = session['user_id']
##
##    with DB_ENGINE.connect() as conn:
##        # Base query
##        base_sql = '''
##            SELECT id, invoice_number, client_name, invoice_date, due_date, grand_total, status, created_at
##            FROM user_invoices
##            WHERE user_id = :user_id
##        '''
##        params = {"user_id": user_id}
##
##        # Add search if provided
##        if search:
##            base_sql += ' AND (invoice_number ILIKE :search OR client_name ILIKE :search)'
##            params["search"] = f"%{search}%"
##
##        # Get total count for pagination
##        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS count_query"
##        total_invoices = conn.execute(text(count_sql), params).scalar()
##
##        # Get paginated invoices
##        invoices_sql = base_sql + '''
##            ORDER BY invoice_date DESC, created_at DESC
##            LIMIT :limit OFFSET :offset
##        '''
##        params.update({"limit": limit, "offset": offset})
##        invoices_result = conn.execute(text(invoices_sql), params).fetchall()
##
##    # Convert to list of dicts for template
##    invoices = []
##    for row in invoices_result:
##        invoices.append({
##            'id': row[0],
##            'invoice_number': row[1],
##            'client_name': row[2],
##            'invoice_date': row[3],
##            'due_date': row[4],
##            'grand_total': float(row[5]) if row[5] else 0.0,
##            'status': row[6],
##            'created_at': row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else ''
##        })
##
##    total_pages = (total_invoices + limit - 1) // limit  # Ceiling division
##
##    return render_template(
##        "invoice_history.html",
##        invoices=invoices,
##        current_page=page,
##        total_pages=total_pages,
##        search_query=search,
##        total_invoices=total_invoices,
##        nonce=g.nonce
##    )


# NEW: Direct PDF Creation Functions = app/services/pdf_generator.py
def create_purchase_order_pdf_direct(data):
    """Create purchase order PDF directly from data"""
    buffer = io.BytesIO()

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch, cm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'POTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#0d6efd'),
        alignment=1,  # Center
        spaceAfter=12
    )

    header_style = ParagraphStyle(
        'POHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0d6efd'),
        spaceAfter=6
    )

    normal_style = ParagraphStyle(
        'PONormal',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    )

    bold_style = ParagraphStyle(
        'POBold',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica-Bold'
    )

    # Title
    story.append(Paragraph(data['title'], title_style))
    story.append(Paragraph(f"PO #: {data['document_number']}", header_style))
    story.append(Spacer(1, 0.2*inch))

    # Company Info
    story.append(Paragraph(f"<b>FROM:</b> {data['company_name']}", bold_style))
    if data['company_address']:
        story.append(Paragraph(data['company_address'], normal_style))
    if data['company_phone']:
        story.append(Paragraph(f"Phone: {data['company_phone']}", normal_style))
    if data['company_email']:
        story.append(Paragraph(f"Email: {data['company_email']}", normal_style))

    story.append(Spacer(1, 0.2*inch))

    # Supplier Info Box
    supplier_info = [
        [Paragraph("<b>TO:</b>", bold_style), ""],
        [Paragraph(f"{data['supplier_name']}", normal_style),
         Paragraph(f"<b>PO Date:</b> {data['po_date']}", normal_style)],
        [Paragraph(f"{data['supplier_address']}", normal_style),
         Paragraph(f"<b>Delivery Date:</b> {data['delivery_date']}", normal_style)],
        [Paragraph(f"Phone: {data['supplier_phone']}", normal_style),
         Paragraph(f"<b>Status:</b> {data['status']}", normal_style)],
        [Paragraph(f"Email: {data['supplier_email']}", normal_style), ""]
    ]

    supplier_table = Table(supplier_info, colWidths=[3.5*inch, 3.5*inch])
    supplier_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#e8f4fd')),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    story.append(supplier_table)
    story.append(Spacer(1, 0.3*inch))

    # Items Table
    if data['items']:
        table_data = [['#', 'Description', 'SKU', 'Supplier', 'Qty', 'Unit Price', 'Total']]

        for idx, item in enumerate(data['items'], 1):
            table_data.append([
                str(idx),
                item.get('name', ''),
                item.get('sku', ''),
                item.get('supplier', ''),
                str(item.get('qty', 1)),
                f"{data['currency_symbol']}{item.get('price', 0):.2f}",
                f"{data['currency_symbol']}{item.get('total', 0):.2f}"
            ])

        # Add totals
        table_data.append(['', '', '', '', '',
                         Paragraph('<b>Subtotal:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['subtotal']:.2f}</b>", bold_style)])

        if data['tax_amount'] > 0:
            table_data.append(['', '', '', '', '',
                             Paragraph(f'<b>Tax ({data["sales_tax"]}%):</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['tax_amount']:.2f}</b>", bold_style)])

        if data.get('shipping_cost', 0) > 0:
            table_data.append(['', '', '', '', '',
                             Paragraph('<b>Shipping:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['shipping_cost']:.2f}</b>", bold_style)])

        table_data.append(['', '', '', '', '',
                         Paragraph('<b>GRAND TOTAL:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['grand_total']:.2f}</b>", bold_style)])

        items_table = Table(table_data, colWidths=[0.4*inch, 2*inch, 1*inch, 1.2*inch, 0.5*inch, 1*inch, 1*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, len(data['items']) + 1), 1, colors.grey),
            ('ALIGN', (4, 1), (6, len(data['items']) + 1), 'RIGHT'),
            ('BACKGROUND', (0, -4), (-1, -1), colors.HexColor('#f8f9fa')),
            ('LINEABOVE', (0, -4), (-1, -4), 2, colors.black),
        ]))

        story.append(items_table)
        story.append(Spacer(1, 0.3*inch))

    # Terms & Conditions Box
    story.append(Paragraph("TERMS & CONDITIONS", header_style))

    terms_data = [
        [Paragraph(f"<b>Payment Terms:</b> {data['payment_terms']}", normal_style)],
        [Paragraph(f"<b>Shipping Terms:</b> {data['shipping_terms']}", normal_style)],
        [Paragraph(f"<b>Delivery Method:</b> {data['delivery_method']}", normal_style)]
    ]

    if data.get('notes'):
        terms_data.append([Paragraph(f"<b>Notes:</b> {data['notes']}", normal_style)])

    terms_table = Table(terms_data, colWidths=[7*inch])
    terms_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#6c757d')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))

    story.append(terms_table)
    story.append(Spacer(1, 0.5*inch))

    # Signatures
    sig_data = [
        [
            Paragraph("_________________________<br/><b>Authorized Signature</b>", normal_style),
            Paragraph("_________________________<br/><b>Supplier Acknowledgment</b>", normal_style)
        ]
    ]

    sig_table = Table(sig_data, colWidths=[3.5*inch, 3.5*inch])
    story.append(sig_table)

    # Footer
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | PO #: {data['document_number']}",
                          ParagraphStyle('Footer', parent=styles['Italic'], fontSize=8, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# app/services/pdf_generator.py
def create_invoice_pdf_direct(data):
    """Create invoice PDF directly from data"""
    buffer = io.BytesIO()

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch, cm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1*cm,
        bottomMargin=1*cm
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom styles for Invoice
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#28a745'),  # Green for invoices
        alignment=1,
        spaceAfter=4
    )

    header_style = ParagraphStyle(
        'InvoiceHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#28a745'),
        spaceAfter=4
    )

    normal_style = ParagraphStyle(
        'InvoiceNormal',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=4
    )

    bold_style = ParagraphStyle(
        'InvoiceBold',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica-Bold'
    )

    # Title with Tax Invoice
    story.append(Paragraph("TAX INVOICE", title_style))
    story.append(Paragraph(f"Invoice #: {data['document_number']}", header_style))
    story.append(Spacer(1, 0.2*inch))

    # Seller/Buyer info in two columns
    seller_info = [
        [Paragraph("<b>SELLER:</b>", bold_style), Paragraph("<b>BUYER:</b>", bold_style)],
        [Paragraph(data['company_name'], normal_style),
         Paragraph(data['client_name'], normal_style)],
        [Paragraph(data['company_address'], normal_style),
         Paragraph(data['client_address'], normal_style)],
        [Paragraph(f"Phone: {data['company_phone']}", normal_style),
         Paragraph(f"Phone: {data['client_phone']}", normal_style)],
        [Paragraph(f"Email: {data['company_email']}", normal_style),
         Paragraph(f"Email: {data['client_email']}", normal_style)]
    ]

    # Add tax IDs if available
    if data.get('seller_ntn') or data.get('company_tax_id'):
        seller_info.append([
            Paragraph(f"Tax ID: {data.get('seller_ntn') or data.get('company_tax_id')}", normal_style),
            Paragraph(f"Tax ID: {data.get('client_tax_id', '')}", normal_style)
        ])

    seller_table = Table(seller_info, colWidths=[3.5*inch, 3.5*inch])
    seller_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#e8f4fd')),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#f8f9fa')),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))

    story.append(seller_table)
    story.append(Spacer(1, 0.2*inch))

    # Invoice details
    details_data = [
        [Paragraph(f"<b>Invoice Date:</b> {data['invoice_date']}", normal_style),
         Paragraph(f"<b>Due Date:</b> {data['due_date']}", normal_style),
         Paragraph(f"<b>Status:</b> {data['status']}", normal_style)]
    ]

    details_table = Table(details_data, colWidths=[2.3*inch, 2.3*inch, 2.3*inch])
    details_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))

    story.append(details_table)
    story.append(Spacer(1, 0.3*inch))

    # Items Table for Invoice
    if data['items']:
        table_data = [['#', 'Description', 'Qty', 'Unit Price', 'Total']]

        for idx, item in enumerate(data['items'], 1):
            table_data.append([
                str(idx),
                item.get('name', ''),
                str(item.get('qty', 1)),
                f"{data['currency_symbol']}{item.get('price', 0):.2f}",
                f"{data['currency_symbol']}{item.get('total', 0):.2f}"
            ])

        # Add totals
        table_data.append(['', '', '',
                         Paragraph('<b>Subtotal:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['subtotal']:.2f}</b>", bold_style)])

        if data.get('tax_amount', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph('<b>Tax:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['tax_amount']:.2f}</b>", bold_style)])

        if data.get('discount', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph(f'<b>Discount:</b>', bold_style),
                             Paragraph(f"<b>-{data['currency_symbol']}{data['discount']:.2f}</b>", bold_style)])

        if data.get('shipping', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph('<b>Shipping:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['shipping']:.2f}</b>", bold_style)])

        table_data.append(['', '', '',
                         Paragraph('<b>GRAND TOTAL:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['grand_total']:.2f}</b>", bold_style)])

        items_table = Table(table_data, colWidths=[0.4*inch, 3*inch, 0.6*inch, 1.2*inch, 1.2*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#28a745')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, len(data['items']) + 1), 1, colors.grey),
            ('ALIGN', (2, 1), (4, len(data['items']) + 1), 'RIGHT'),
            ('BACKGROUND', (0, -4), (-1, -1), colors.HexColor('#f8f9fa')),
            ('LINEABOVE', (0, -4), (-1, -4), 2, colors.black),
        ]))

        story.append(items_table)
        story.append(Spacer(1, 0.3*inch))

    # Payment details and notes
    if data.get('notes') or data.get('terms'):
        notes_data = []
        if data.get('notes'):
            notes_data.append([Paragraph(f"<b>Notes:</b> {data['notes']}", normal_style)])
        if data.get('terms'):
            notes_data.append([Paragraph(f"<b>Terms:</b> {data['terms']}", normal_style)])

        notes_table = Table(notes_data, colWidths=[7*inch])
        notes_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#6c757d')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
            ('PADDING', (0, 0), (-1, -1), 4),
        ]))

        story.append(notes_table)
        story.append(Spacer(1, 0.3*inch))

    # Thank you message and footer
    story.append(Paragraph("Thank you for your business!",
                          ParagraphStyle('Thanks', parent=styles['Italic'], fontSize=11, alignment=1)))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Invoice #: {data['document_number']}",
                          ParagraphStyle('Footer', parent=styles['Italic'], fontSize=8, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
    #app.run(host="0.0.0.0", port=8080, debug=False)
