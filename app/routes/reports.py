# app/routes/reports.py
from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.db import get_db_connection  # Correct import for your project
import csv
import io

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats using raw SQL to match your db.py tables"""
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Revenue from Invoices
    cur.execute("SELECT SUM(grand_total) FROM user_invoices WHERE user_id = %s", (user_id,))
    revenue = cur.fetchone()[0] or 0.0

    # 2. Stock Value from Inventory
    cur.execute("SELECT SUM(current_stock * cost_price) FROM inventory_items WHERE user_id = %s", (user_id,))
    inventory_value = cur.fetchone()[0] or 0.0

    # 3. Tax and Expenses
    cur.execute("SELECT SUM(amount), SUM(tax_amount) FROM expenses WHERE user_id = %s", (user_id,))
    exp_data = cur.fetchone()
    total_expenses = exp_data[0] or 0.0
    tax_liability = exp_data[1] or 0.0

    cur.close()
    conn.close()

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
    data = get_live_business_data(user_id)
    
    system_instruction = f"Manager Persona. Stats: Revenue {data['revenue']}, Stock {data['inventory_value']}, Profit {data['net_profit']}."

    try:
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        return jsonify({"answer": "👔 <strong>Manager's Note:</strong> I'm busy. Please try again in 60s."})

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
                    headers={"Content-disposition": "attachment; filename=report.csv"})
