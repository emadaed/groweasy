from flask import Blueprint, render_template, session, request, jsonify, make_response
from app.services.db import DB_ENGINE
from sqlalchemy import text
from weasyprint import HTML
import io
from datetime import datetime

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

@reports_bp.route('/tax/certificate', methods=['GET', 'POST'])
def tax_certificate():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    user_id = session['user_id']

    if request.method == 'POST':
        from_date = request.form.get('from_date')
        to_date = request.form.get('to_date')
        # Calculate tax sum within range
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT COALESCE(SUM(
                    COALESCE(
                        (invoice_data::jsonb->>'tax')::numeric,
                        (invoice_data::jsonb->>'tax_amount')::numeric,
                        0
                    )
                ), 0)
                FROM user_invoices
                WHERE user_id = :uid
                  AND invoice_date BETWEEN :from AND :to
            """), {"uid": user_id, "from": from_date, "to": to_date}).scalar()
        tax_total = float(result)

        # Generate PDF certificate
        html = render_template('tax_certificate.html',
                               user_id=user_id,
                               from_date=from_date,
                               to_date=to_date,
                               tax_total=tax_total,
                               generated_at=datetime.now().strftime('%Y-%m-%d %H:%M'))
        pdf = HTML(string=html).write_pdf()
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=tax_certificate_{from_date}_to_{to_date}.pdf'
        return response

    # GET: show form
    return render_template('tax_certificate_form.html')

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

    # Get filter parameters
    from_date = request.args.get('from')
    to_date = request.args.get('to')
    product_id = request.args.get('product_id')

    query = """
        SELECT sm.id, i.name as product_name, sm.movement_type, sm.quantity,
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

    if 'csv' in request.args:
        # Export CSV
        import csv
        from io import StringIO
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Product', 'Type', 'Quantity', 'Reference', 'Notes', 'Date'])
        for r in rows:
            writer.writerow(r)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv'
        response.headers['Content-Disposition'] = 'attachment; filename=stock_movements.csv'
        return response
    movements = []
    for r in rows:
        movements.append({
            'id': r[0],
            'product_name': r[1],
            'movement_type': r[2],
            'quantity': r[3],
            'reference_id': r[4],
            'notes': r[5],
            'created_at': r[6]
        })

    return render_template('stock_movements.html',
                           movements=movements,
                           products=products)

    
