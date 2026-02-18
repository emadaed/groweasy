from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for, g, Response
from app.services.db import DB_ENGINE  
from sqlalchemy import text
import csv
import io

reports_bp = Blueprint('reports', __name__)

def get_live_business_data(user_id):
    """Calculates live stats using the correct 'tax' column name."""
    with DB_ENGINE.connect() as conn:
        # 1. Revenue & Tax from Invoices (Using 'tax' as verified)
        inv_stats = conn.execute(text("""
            SELECT SUM(grand_total) as rev, SUM(tax) as tax_val 
            FROM user_invoices WHERE user_id = :uid
        """), {'uid': user_id}).fetchone()
        
        revenue = inv_stats.rev or 0.0
        tax_liability = inv_stats.tax_val or 0.0

        # 2. Stock Value
        inv_res = conn.execute(text("SELECT SUM(current_stock * cost_price) FROM inventory_items WHERE user_id = :uid"), {'uid': user_id})
        inventory_value = inv_res.scalar() or 0.0

        # 3. Expenses
        exp_res = conn.execute(text("SELECT SUM(amount) FROM expenses WHERE user_id = :uid"), {'uid': user_id})
        total_expenses = exp_res.scalar() or 0.0

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
        f"Current Stats: Revenue {data['revenue']}, Tax {data['tax_liability']}, Profit {data['net_profit']}. "
        f"Provide professional advice using bullet points."
    )

    try:
        from app.utils.ai_handler import call_gemini 
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        return jsonify({"answer": response})
    except Exception as e:
        # Returning the actual error so we can stop the "Auditing" loop
        return jsonify({"answer": f"👔 <strong>Manager's Note:</strong> Connection issue: {str(e)[:100]}"})

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
                    headers={"Content-disposition": "attachment; filename=warehouse_report.csv"})
