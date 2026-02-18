# app/routes/reports.py
from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.services.db import DB_ENGINE  # Using verified import
from sqlalchemy import text
import csv
import io

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats with column-safety to prevent 500 errors."""
    with DB_ENGINE.connect() as conn:
        # 1. Revenue from Invoices
        rev_res = conn.execute(text("SELECT SUM(grand_total) FROM user_invoices WHERE user_id = :uid"), {'uid': user_id})
        revenue = rev_res.scalar() or 0.0

        # 2. Stock Value from Inventory
        inv_res = conn.execute(text("SELECT SUM(current_stock * cost_price) FROM inventory_items WHERE user_id = :uid"), {'uid': user_id})
        inventory_value = inv_res.scalar() or 0.0

        # 3. Safe Expenses & Tax Calculation
        # We only pull 'amount'. If you have a tax column, we can add it later.
        # For now, this prevents the 'UndefinedColumn' crash.
        exp_res = conn.execute(text("SELECT SUM(amount) FROM expenses WHERE user_id = :uid"), {'uid': user_id})
        total_expenses = exp_res.scalar() or 0.0
        
        # Default tax to 0.0 to prevent crash until you confirm the column name
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
    if 'user_id' not in session: return redirect(url_for('auth.login'))
    data = get_live_business_data(session['user_id'])
    return render_template('reports_dashboard.html', data=data, ai_advice=session.get('ai_advice'), currency_symbol="د.إ")

@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    user_id = session.get('user_id')
    prompt = request.json.get('prompt')
    data = get_live_business_data(user_id)
    
    # Passing the live data to Gemini so it knows your real numbers
    msg = f"Manager Analysis: Revenue {data['revenue']}, Profit {data['net_profit']}, Stock {data['inventory_value']}."
    try:
        from app.utils.ai_handler import call_gemini
        response = call_gemini(msg, prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except:
        return jsonify({"answer": "👔 Manager is busy. Try again in 60s."})
    
@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    user_id = session.get('user_id')
    user_prompt = request.json.get('prompt', '').strip()
    data = get_live_business_data(user_id)
    
    system_instruction = f"You are a World-Class Warehouse Manager. Live Stats: Revenue {data['revenue']}, Stock {data['inventory_value']}, Profit {data['net_profit']}."

    try:
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        # Return the specific error to help us debug if it's not a rate limit
        return jsonify({"answer": f"👔 <strong>Manager's Note:</strong> I hit an issue: {str(e)[:50]}"})

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
    return Response(output.getvalue(), mimetype="text/csv", 
                    headers={"Content-disposition": "attachment; filename=live_report.csv"})
