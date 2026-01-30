from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from datetime import datetime
from app.services.auth import get_expenses, get_expense_summary, save_expense

finance_bp = Blueprint('finance', __name__)

@finance_bp.route("/expenses")
def expenses():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    expense_list = get_expenses(session['user_id'])
    expense_summary = get_expense_summary(session['user_id'])
    today_date = datetime.now().strftime('%Y-%m-%d')

    return render_template("expenses.html",
                         expenses=expense_list,
                         expense_summary=expense_summary,
                         today_date=today_date,
                         nonce=g.nonce)

@finance_bp.route("/add_expense", methods=['POST'])
def add_expense():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    expense_data = {
        'description': request.form.get('description'),
        'amount': float(request.form.get('amount', 0)),
        'category': request.form.get('category'),
        'expense_date': request.form.get('expense_date'),
        'notes': request.form.get('notes', '')
    }

    if save_expense(session['user_id'], expense_data):
        flash('✅ Expense added successfully!', 'success')
    else:
        flash('❌ Error adding expense', 'error')

    return redirect(url_for('finance.expenses'))
