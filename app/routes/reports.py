from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.services.db import DB_ENGINE  
from sqlalchemy import text
import csv
import io

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats with column-safety to prevent crashes."""
    with DB_ENGINE.connect() as conn:
        # 1. Revenue from Invoices
        rev_res = conn.execute(text("SELECT SUM(grand_total) FROM user_invoices WHERE user_id = :uid"), {'uid': user_id})
        revenue = rev_res.scalar() or 0.0

        # 2. Stock Value from Inventory
        inv_res = conn.execute(text("SELECT SUM(current_stock * cost_price) FROM inventory_items WHERE user_id = :uid"), {'uid': user_id})
        inventory_value = inv_res.scalar() or 0.0

        # 3. Expenses (Safe: No tax_amount column to avoid UndefinedColumn error)
        exp_res = conn.execute(text("SELECT SUM(amount) FROM expenses WHERE user_id = :uid"), {'uid': user_id})
        total_expenses = exp_res.scalar() or 0.0
        
        # Default tax to 0.0 until column name is verified
        tax_liability = 0.0 

    return {
        "revenue": float(revenue),
        "inventory_value": float(inventory_value),
        "tax_liability": float(tax_liability),
        "costs": float(total_expenses),
        "net_profit": float(revenue - total_expenses)
    }

@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    user_id = session['user_id']
    data = get_live_business_data(user_id)
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
    
    # Get live stats to feed into the Manager's brain
    data = get_live_business_data(user_id)
    
    system_instruction = (
        f"You are a World-Class Warehouse Manager. "
        f"Current Stats: Revenue {data['revenue']}, Stock Value {data['inventory_value']}, Profit {data['net_profit']}. "
        f"Provide professional advice using bullet points."
    )

    try:
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        return jsonify({"answer": "👔 <strong>Manager's Note:</strong> I'm currently auditing the records. Please try again in 60 seconds."})

@reports_bp.route('/reports/clear_ai', methods=['POST'])
def clear_ai():
    session.pop('ai_advice', None)
    return jsonify({"status": "cleared"})

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
    
    return Response(
        output.getvalue(), 
        mimetype="text/csv", 
        headers={"Content-disposition": "attachment; filename=warehouse_report.csv"}
    )
