# app/routes/reports.py
from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.models import db  # Ensure this points to your database models
from datetime import datetime, timedelta
import csv
import io

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats from the database tables"""
    # 1. Calculate Revenue (Sum of grand_total from user_invoices)
    revenue = db.session.execute(
        "SELECT SUM(grand_total) FROM user_invoices WHERE user_id = :uid", 
        {'uid': user_id}
    ).scalar() or 0.0

    # 2. Calculate Stock Value (SUM of current_stock * cost_price)
    inventory_value = db.session.execute(
        "SELECT SUM(current_stock * cost_price) FROM inventory_items WHERE user_id = :uid",
        {'uid': user_id}
    ).scalar() or 0.0

    # 3. Calculate Tax & Expenses
    expenses_data = db.session.execute(
        "SELECT SUM(amount), SUM(tax_amount) FROM expenses WHERE user_id = :uid",
        {'uid': user_id}
    ).fetchone()
    total_expenses = expenses_data[0] or 0.0
    tax_liability = expenses_data[1] or 0.0

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

    # Currency setup
    user_symbol = "د.إ" # Default based on your logs

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol=user_symbol,
                           nonce=getattr(g, 'nonce', ''))

@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    user_id = session.get('user_id')
    user_prompt = request.json.get('prompt', '').strip()
    
    data = get_live_business_data(user_id)
    
    # Manager Persona
    system_instruction = f"You are a World-Class Warehouse Manager. Revenue: {data['revenue']}, Stock: {data['inventory_value']}, Tax: {data['tax_liability']}. Be professional and use bullet points."

    try:
        # Assuming call_gemini is your function to hit the API
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            return jsonify({"answer": "👔 <strong>Manager's Note:</strong> I'm busy with an audit. Please try again in 60 seconds."})
        return jsonify({"answer": f"❌ Manager is away: {error_msg[:50]}..."})

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
