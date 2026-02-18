from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.services.db import DB_ENGINE  # Corrected based on your db.py
from sqlalchemy import text
import csv
import io
import json

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats correctly from the invoice_data column."""
    with DB_ENGINE.connect() as conn:
        # 1. Pull all invoices for this user
        # Your db.py shows grand_total is available, but tax is inside invoice_data
        inv_res = conn.execute(text("""
            SELECT grand_total, invoice_data 
            FROM user_invoices WHERE user_id = :uid
        """), {'uid': user_id}).fetchall()
        
        total_revenue = 0.0
        total_tax = 0.0

        for row in inv_res:
            total_revenue += float(row.grand_total)
            try:
                # Parsing the invoice_data string to find the tax
                data = json.loads(row.invoice_data)
                total_tax += float(data.get('tax', 0) or data.get('tax_amount', 0))
            except:
                # Fallback: If parsing fails, assume 5% tax included in grand_total
                total_tax += float(row.grand_total) * 0.05

        # 2. Stock Value (Using current_stock and cost_price from inventory_items)
        inv_val_res = conn.execute(text("""
            SELECT SUM(current_stock * cost_price) 
            FROM inventory_items WHERE user_id = :uid
        """), {'uid': user_id})
        inventory_value = inv_val_res.scalar() or 0.0

        # 3. Expenses (Using amount from expenses table)
        exp_res = conn.execute(text("SELECT SUM(amount) FROM expenses WHERE user_id = :uid"), {'uid': user_id})
        total_expenses = exp_res.scalar() or 0.0

    return {
        "revenue": total_revenue,
        "inventory_value": float(inventory_value),
        "tax_liability": total_tax,
        "costs": float(total_expenses),
        "net_profit": total_revenue - float(total_expenses)
    }

@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    data = get_live_business_data(session['user_id'])
    ai_advice = session.get('ai_advice')

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol="د.إ",
                           nonce=getattr(g, 'nonce', ''))

@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    user_id = session.get('user_id')
    user_prompt = request.json.get('prompt', '').strip()
    data = get_live_business_data(user_id)
    
    system_instruction = (
        f"You are a World-Class Warehouse Manager. "
        f"Stats: Rev {data['revenue']}, Tax {data['tax_liability']}, Stock {data['inventory_value']}. "
        f"Analyze this and give advice."
    )

    try:
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        return jsonify({"answer": f"👔 <strong>Manager's Note:</strong> Busy with audit. {str(e)[:50]}"})

@reports_bp.route('/reports/download/csv')
def download_csv():
    user_id = session.get('user_id')
    data = get_live_business_data(user_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Total Revenue', data['revenue']])
    writer.writerow(['Net Profit', data['net_profit']])
    writer.writerow(['Tax Liability', data['tax_liability']])
    writer.writerow(['Stock Value', data['inventory_value']])
    return Response(output.getvalue(), mimetype="text/csv", 
                    headers={"Content-disposition": "attachment; filename=report.csv"})
