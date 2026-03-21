from flask import send_file, make_response, g
from flask import redirect, url_for
from app.services.pdf_engine import generate_pdf  # if not already imported
from app.context_processors import CURRENCY_SYMBOLS
from app.services.cache import get_user_profile_cached
from flask import Blueprint, render_template, session, request, jsonify
from app.services.db import DB_ENGINE
from sqlalchemy import text
from weasyprint import HTML
import io
from datetime import datetime
from app.decorators import role_required

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

@reports_bp.route('/tax/certificate', methods=['GET', 'POST'])
def tax_certificate():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    
    if request.method == 'POST':
        from_date = request.form.get('from_date')
        to_date = request.form.get('to_date')
        include_details = request.form.get('include_details') == 'yes'
        
        # Validate dates (optional)
        
        # Query totals and optionally invoice details
        with DB_ENGINE.connect() as conn:
            # Get totals
            result = conn.execute(text("""
                SELECT 
                    SUM(grand_total) as total_sales,
                    SUM(COALESCE((invoice_data::json->>'tax_amount')::numeric, 0)) as total_tax
                FROM user_invoices
                WHERE user_id = :user_id AND invoice_date BETWEEN :from_date AND :to_date
            """), {"user_id": user_id, "from_date": from_date, "to_date": to_date}).fetchone()
            total_sales = result[0] or 0.0
            total_tax = result[1] or 0.0
            
            # If detailed list requested, fetch invoices
            invoices = []
            if include_details:
                inv_result = conn.execute(text("""
                    SELECT 
                        invoice_number,
                        invoice_date,
                        client_name,
                        grand_total,
                        COALESCE((invoice_data::json->>'tax_amount')::numeric, 0) as tax_amount
                    FROM user_invoices
                    WHERE user_id = :user_id AND invoice_date BETWEEN :from_date AND :to_date
                    ORDER BY invoice_date DESC
                """), {"user_id": user_id, "from_date": from_date, "to_date": to_date}).fetchall()
                
                for row in inv_result:
                    invoices.append({
                        'number': row[0],
                        'date': row[1].strftime('%Y-%m-%d') if row[1] else '',
                        'client': row[2],
                        'total': float(row[3]),
                        'tax': float(row[4])
                    })
        
        # Get user profile for company details
        user_profile = get_user_profile_cached(user_id)
        company_name = user_profile.get('company_name', 'Your Company')
        company_address = user_profile.get('company_address', '')
        company_tax_id = user_profile.get('company_tax_id', '')
        company_ntn = user_profile.get('seller_ntn', '')
        currency_symbol = CURRENCY_SYMBOLS.get(user_profile.get('preferred_currency', 'PKR'), 'Rs.')
        
        # Generate a certificate number
        certificate_number = f"TAX-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        issue_date = datetime.now().strftime('%d-%b-%Y')
        
        # Render the template
        html = render_template('tax_certificate_summary.html',
                               company_name=company_name,
                               company_address=company_address,
                               company_tax_id=company_tax_id,
                               company_ntn=company_ntn,
                               certificate_number=certificate_number,
                               issue_date=issue_date,
                               from_date=from_date,
                               to_date=to_date,
                               total_sales=total_sales,
                               total_tax=total_tax,
                               currency_symbol=currency_symbol,
                               tax_law_reference="Income Tax Ordinance, 2001",  # Make configurable later
                               include_details=include_details,
                               invoices=invoices)
        
        # Generate PDF
        from app.services.pdf_engine import generate_pdf
        pdf_bytes = generate_pdf(html)
        
        # Return as download
        response = make_response(send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=f"Tax_Certificate_{from_date}_to_{to_date}.pdf",
            mimetype='application/pdf'
        ))
        return response
    
    # GET: show form
    return render_template('tax_certificate_form.html', nonce=g.nonce)

@reports_bp.route('/sales/csv', methods=['GET'])
def sales_csv():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    from_date = request.args.get('from')
    to_date = request.args.get('to')

    query = """
        SELECT invoice_number, invoice_date, client_name, grand_total
        FROM user_invoices
        WHERE user_id = :uid
    """
    params = {"uid": user_id}
    if from_date and to_date:
        query += " AND invoice_date BETWEEN :from AND :to"
        params["from"] = from_date
        params["to"] = to_date
    query += " ORDER BY invoice_date DESC"

    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Invoice Number', 'Date', 'Client', 'Total'])
    for r in rows:
        writer.writerow([r[0], r[1], r[2], float(r[3])])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv'
    response.headers['Content-Disposition'] = 'attachment; filename=sales_report.csv'
    return response

    

@reports_bp.route('/stock/movements')
def stock_movements():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']

    from_date = request.args.get('from')
    to_date = request.args.get('to')
    product_id = request.args.get('product_id')

    query = """
        SELECT sm.id, i.name as product_name, i.sku, sm.movement_type, sm.quantity,
               sm.reference_id, sm.notes, sm.created_at
        FROM stock_movements sm
        JOIN inventory_items i ON sm.product_id = i.id
        WHERE sm.user_id = :uid
    """
    params = {"uid": user_id}
    if from_date and to_date:
        query += " AND sm.created_at::date BETWEEN :from AND :to"
        params["from"] = from_date
        params["to"] = to_date
    if product_id:
        query += " AND sm.product_id = :pid"
        params["pid"] = product_id
    query += " ORDER BY sm.created_at DESC"

    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()

        # Get products for filter dropdown
        products = conn.execute(text("""
            SELECT id, name FROM inventory_items 
            WHERE user_id = :uid AND is_active = TRUE 
            ORDER BY name
        """), {"uid": user_id}).fetchall()

    if 'csv' in request.args:
        import csv
        from io import StringIO
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Product', 'SKU', 'Type', 'Quantity', 'Reference', 'Notes', 'Date'])
        for r in rows:
            writer.writerow([r[0], r[1], r[2] or '—', r[3], r[4], r[5], r[6], r[7]])
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=stock_movements.csv'
        return response

    movements = []
    for r in rows:
        movements.append({
            'id': r[0],
            'product_name': r[1],
            'sku': r[2] or '—',
            'movement_type': r[3],
            'quantity': r[4],
            'reference_id': r[5],
            'notes': r[6],
            'created_at': r[7]
        })

    return render_template('stock_movements.html',
                           movements=movements,
                           products=products,
                           nonce=g.nonce)

# new PEL Enhanced
@reports_bp.route('/profit_loss', methods=['GET', 'POST'])
@role_required('owner', 'accountant')
def profit_loss():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']
    account_id = session['account_id']

    if request.method == 'POST':
        from_date = request.form.get('from_date')
        to_date = request.form.get('to_date')
        include_details = request.form.get('include_details') == 'yes'

        with DB_ENGINE.connect() as conn:
            # --- SALES TOTALS ---
            sales_result = conn.execute(text("""
                SELECT 
                    COUNT(*) as invoice_count,
                    SUM(grand_total) as total_sales,
                    SUM(COALESCE((invoice_data::json->>'tax_amount')::numeric, 0)) as total_tax_collected
                FROM user_invoices
                WHERE account_id = :aid AND invoice_date BETWEEN :from_date AND :to_date
            """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).fetchone()
            invoice_count = sales_result[0] or 0
            total_sales = float(sales_result[1] or 0.0)
            total_tax_collected = float(sales_result[2] or 0.0)

            # --- EXPENSE TOTALS ---
            expense_result = conn.execute(text("""
                SELECT 
                    COUNT(*) as expense_count,
                    SUM(amount) as total_net_expenses,
                    COALESCE(SUM(tax_amount), 0) as total_input_tax
                FROM expenses
                WHERE account_id = :aid AND expense_date BETWEEN :from_date AND :to_date
            """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).fetchone()
            expense_count = expense_result[0] or 0
            total_net_expenses = float(expense_result[1] or 0.0)
            total_input_tax = float(expense_result[2] or 0.0)

            # --- COGS (Cost of Goods Sold) from paid invoices ---
            cogs = float(conn.execute(text("""
                SELECT COALESCE(SUM(ii.quantity * i.cost_price), 0)
                FROM invoice_items ii
                JOIN user_invoices ui ON ii.invoice_id = ui.id
                JOIN inventory_items i ON ii.product_id = i.id
                WHERE ui.account_id = :aid 
                    AND ui.status = 'paid'
                    AND ui.invoice_date BETWEEN :from_date AND :to_date
            """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).scalar() or 0)

            # --- OPENING INVENTORY (value before from_date) ---
            opening_inventory = float(conn.execute(text("""
                WITH inventory_snapshot AS (
                    SELECT 
                        i.id,
                        i.cost_price,
                        COALESCE((
                            SELECT SUM(ii.quantity)
                            FROM invoice_items ii
                            JOIN user_invoices ui ON ii.invoice_id = ui.id
                            WHERE ii.product_id = i.id 
                                AND ui.status = 'paid'
                                AND ui.invoice_date < :from_date
                        ), 0) as sold_before,
                        COALESCE((
                            SELECT SUM(pr.received_qty)
                            FROM po_receipts pr
                            WHERE pr.product_id = i.id 
                                AND pr.received_date < :from_date
                        ), 0) as purchased_before
                    FROM inventory_items i
                    WHERE i.account_id = :aid
                )
                SELECT COALESCE(SUM(cost_price * (purchased_before - sold_before)), 0)
                FROM inventory_snapshot
            """), {"aid": account_id, "from_date": from_date}).scalar() or 0)

            # --- PURCHASES DURING PERIOD (from receipts) ---
            purchases = float(conn.execute(text("""
                SELECT COALESCE(SUM(pr.received_qty * i.cost_price), 0)
                FROM po_receipts pr
                JOIN inventory_items i ON pr.product_id = i.id
                WHERE i.account_id = :aid 
                    AND pr.received_date BETWEEN :from_date AND :to_date
            """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).scalar() or 0)

            # --- CLOSING INVENTORY (current value) ---
            closing_inventory = float(conn.execute(text("""
                SELECT COALESCE(SUM(current_stock * cost_price), 0)
                FROM inventory_items
                WHERE account_id = :aid
            """), {"aid": account_id}).scalar() or 0)

            # --- RECEIVABLES AGING ---
            receivables = conn.execute(text("""
                SELECT 
                    COUNT(*) as total_invoices,
                    COALESCE(SUM(grand_total), 0) as total_due,
                    COALESCE(SUM(CASE 
                        WHEN invoice_date < NOW() - INTERVAL '60 days' 
                        THEN grand_total ELSE 0 
                    END), 0) as over_60_days,
                    COALESCE(SUM(CASE 
                        WHEN invoice_date BETWEEN NOW() - INTERVAL '60 days' AND NOW() - INTERVAL '30 days'
                        THEN grand_total ELSE 0 
                    END), 0) as between_30_60,
                    COALESCE(SUM(CASE 
                        WHEN invoice_date > NOW() - INTERVAL '30 days'
                        THEN grand_total ELSE 0 
                    END), 0) as under_30_days
                FROM user_invoices
                WHERE account_id = :aid AND status = 'unpaid'
            """), {"aid": account_id}).first()

            # --- PAYABLES (unpaid purchase orders) ---
            payables = conn.execute(text("""
                SELECT 
                    COUNT(*) as total_pos,
                    COALESCE(SUM(grand_total), 0) as total_payable
                FROM purchase_orders
                WHERE account_id = :aid AND status = 'pending'
            """), {"aid": account_id}).first()

            # --- DETAILED LISTS (if requested) ---
            invoices = []
            expenses = []
            if include_details:
                inv_rows = conn.execute(text("""
                    SELECT invoice_number, invoice_date, client_name, grand_total,
                           COALESCE((invoice_data::json->>'tax_amount')::numeric, 0) as tax_amount
                    FROM user_invoices
                    WHERE account_id = :aid AND invoice_date BETWEEN :from_date AND :to_date
                    ORDER BY invoice_date DESC
                """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).fetchall()
                for row in inv_rows:
                    invoices.append({
                        'number': row[0],
                        'date': row[1].strftime('%Y-%m-%d') if row[1] else '',
                        'client': row[2],
                        'total': float(row[3]),
                        'tax': float(row[4])
                    })

                exp_rows = conn.execute(text("""
                    SELECT expense_date, description, category, amount, notes
                    FROM expenses
                    WHERE account_id = :aid AND expense_date BETWEEN :from_date AND :to_date
                    ORDER BY expense_date DESC
                """), {"aid": account_id, "from_date": from_date, "to_date": to_date}).fetchall()
                for row in exp_rows:
                    expenses.append({
                        'date': row[0].strftime('%Y-%m-%d') if row[0] else '',
                        'description': row[1],
                        'category': row[2],
                        'amount': float(row[3]),
                        'notes': row[4]
                    })

        gross_profit = total_sales - cogs
        net_profit = gross_profit - total_net_expenses

        user_profile = get_user_profile_cached(user_id)
        company_name = user_profile.get('company_name', 'Your Company')
        currency_symbol = CURRENCY_SYMBOLS.get(user_profile.get('preferred_currency', 'PKR'), 'Rs.')

        report_number = f"PL-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        issue_date = datetime.now().strftime('%d-%b-%Y')

        html = render_template('profit_loss_report.html',
                               company_name=company_name,
                               currency_symbol=currency_symbol,
                               report_number=report_number,
                               issue_date=issue_date,
                               from_date=from_date,
                               to_date=to_date,
                               invoice_count=invoice_count,
                               total_sales=total_sales,
                               total_tax_collected=total_tax_collected,
                               expense_count=expense_count,
                               total_expenses=total_net_expenses,
                               total_input_tax=total_input_tax,
                               cogs=cogs,
                               opening_inventory=opening_inventory,
                               purchases=purchases,
                               closing_inventory=closing_inventory,
                               gross_profit=gross_profit,
                               net_profit=net_profit,
                               receivables=receivables,
                               payables=payables,
                               include_details=include_details,
                               invoices=invoices,
                               expenses=expenses)

        from app.services.pdf_engine import generate_pdf
        pdf_bytes = generate_pdf(html)
        return make_response(send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=f"Profit_Loss_{from_date}_to_{to_date}.pdf",
            mimetype='application/pdf'
        ))

    return render_template('profit_loss_form.html', nonce=g.nonce)
