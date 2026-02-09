# app/routes/suppliers.py
from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from app.services.suppliers import SupplierManager

suppliers_bp = Blueprint('suppliers', __name__)

@suppliers_bp.route('/suppliers')
def list_suppliers():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    suppliers = SupplierManager.get_suppliers(session['user_id'])
    return render_template('suppliers.html', suppliers=suppliers)

@suppliers_bp.route('/suppliers/add', methods=['POST'])
def add_supplier():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    supplier_data = {
        "name": request.form.get('name'),
        "contact_person": request.form.get('contact_person'),
        "email": request.form.get('email'),
        "phone": request.form.get('phone'),
        "address": request.form.get('address'),
        "tax_id": request.form.get('tax_id'),
        "payment_terms": request.form.get('payment_terms'),
        "bank_details": request.form.get('bank_details')
    }
    
    new_id = SupplierManager.add_supplier(session['user_id'], supplier_data)
    if new_id:
        flash('Supplier added successfully!', 'success')
    else:
        flash('Error adding supplier.', 'danger')
        
    return redirect(url_for('suppliers.list_suppliers'))
