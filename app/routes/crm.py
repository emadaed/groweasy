from flask import Blueprint, render_template, redirect, url_for, session, g
from app.services.auth import get_customers
from app.services.suppliers import SupplierManager

crm_bp = Blueprint('crm', __name__)

@crm_bp.route("/customers")
def customers():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    account_id = session['account_id']
    customer_list = get_customers(account_id)
    return render_template("customers.html", customers=customer_list, nonce=g.nonce)

@crm_bp.route("/suppliers")
def suppliers():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    account_id = session['account_id']
    suppliers = SupplierManager.get_suppliers(account_id)
    return render_template("suppliers.html", suppliers=suppliers, nonce=g.nonce)
