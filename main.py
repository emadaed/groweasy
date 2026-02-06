# ============================================================================
# main.py - COMPLETE FIXED VERSION 29--01-2026 12:23 AM
# ============================================================================
from flask import render_template, session, redirect, url_for, request, flash, jsonify, g, send_file, make_response, current_app
##from sqlalchemy import text

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
##from app.services.services import InvoiceService

#config.py later

##CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}

app = create_app()


### PO API-1
##@app.route('/api/purchase_order/<po_number>/complete', methods=['POST'])
##def complete_purchase_order(po_number):
##    """Mark PO as completed - API endpoint"""
##    if 'user_id' not in session:
##        return jsonify({'error': 'Unauthorized'}), 401
##
##    try:
##        with DB_ENGINE.begin() as conn:
##            conn.execute(text("""
##                UPDATE purchase_orders
##                SET status = 'completed'
##                WHERE user_id = :user_id AND po_number = :po_number
##            """), {"user_id": session['user_id'], "po_number": po_number})
##
##        return jsonify({'success': True, 'message': f'PO {po_number} marked as completed'}), 200
##    except Exception as e:
##        return jsonify({'error': str(e)}), 500
##
### PO cancel API endpoint -API 2
##@app.route('/api/purchase_order/<po_number>/cancel', methods=['POST'])
##def cancel_purchase_order(po_number):
##    """Cancel purchase order"""
##    if 'user_id' not in session:
##        return jsonify({'error': 'Unauthorized'}), 401
##
##    try:
##        data = request.get_json()
##        reason = data.get('reason', 'No reason provided')
##
##        with DB_ENGINE.begin() as conn:
##            # Update order data with cancellation
##            result = conn.execute(text("""
##                SELECT order_data FROM purchase_orders
##                WHERE user_id = :user_id AND po_number = :po_number
##            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()
##
##            if result:
##                order_data = json.loads(result[0])
##                order_data['cancellation_reason'] = reason
##                order_data['cancelled_at'] = datetime.now().isoformat()
##
##                conn.execute(text("""
##                    UPDATE purchase_orders
##                    SET status = 'cancelled', order_data = :order_data
##                    WHERE user_id = :user_id AND po_number = :po_number
##                """), {
##                    "user_id": session['user_id'],
##                    "po_number": po_number,
##                    "order_data": json.dumps(order_data)
##                })
##
##        return jsonify({'success': True, 'message': f'PO {po_number} cancelled'}), 200
##    except Exception as e:
##        return jsonify({'error': str(e)}), 500
##
##
###API endpoints for better UX API-3
##@app.route("/api/purchase_order/<po_number>")
##@limiter.limit("30 per minute")
##def get_purchase_order_details(po_number):
##    """API endpoint to get PO details"""
##    if 'user_id' not in session:
##        return jsonify({'error': 'Unauthorized'}), 401
##
##    try:
##        with DB_ENGINE.connect() as conn:
##            result = conn.execute(text("""
##                SELECT order_data, status, created_at
##                FROM purchase_orders
##                WHERE user_id = :user_id AND po_number = :po_number
##                ORDER BY created_at DESC LIMIT 1
##            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()
##
##        if not result:
##            return jsonify({'error': 'Purchase order not found'}), 404
##
##        order_data = json.loads(result[0])
##        order_data['status'] = result[1]
##        order_data['created_at'] = result[2].isoformat() if result[2] else None
##
##        return jsonify(order_data), 200
##
##    except Exception as e:
##        current_app.logger.error(f"PO details error: {str(e)}")
##        return jsonify({'error': 'Internal server error'}), 500
##


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
