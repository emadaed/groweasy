# core/auth.py - Fully Postgres Ready
from core.db import DB_ENGINE
from sqlalchemy import text
import hashlib
import os
import json
from datetime import datetime



def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(email, password, company_name=""):
    with DB_ENGINE.begin() as conn:
        try:
            conn.execute(text('''
                INSERT INTO users (email, password_hash, company_name)
                VALUES (:email, :password_hash, :company_name)
            '''), {
                "email": email,
                "password_hash": hash_password(password),
                "company_name": company_name
            })
            return True
        except Exception:  # IntegrityError for duplicate email
            return False

def verify_user(email, password):
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT id, password_hash FROM users WHERE email = :email
        '''), {"email": email}).fetchone()

    if result and result[1] == hash_password(password):
        return result[0]
    return None

def update_user_profile(user_id, company_name=None, company_address=None, company_phone=None,
                       company_tax_id=None, seller_ntn=None, seller_strn=None, preferred_currency=None):
    """Update user profile information"""
    with DB_ENGINE.begin() as conn:
        updates = []
        params = {"user_id": user_id}

        if company_name is not None:
            updates.append("company_name = :company_name")
            params["company_name"] = company_name
        if company_address is not None:
            updates.append("company_address = :company_address")
            params["company_address"] = company_address
        if company_phone is not None:
            updates.append("company_phone = :company_phone")
            params["company_phone"] = company_phone
        if company_tax_id is not None:
            updates.append("company_tax_id = :company_tax_id")
            params["company_tax_id"] = company_tax_id
        if seller_ntn is not None:
            updates.append("seller_ntn = :seller_ntn")
            params["seller_ntn"] = seller_ntn
        if seller_strn is not None:
            updates.append("seller_strn = :seller_strn")
            params["seller_strn"] = seller_strn
        if preferred_currency is not None:
            updates.append("preferred_currency = :preferred_currency")
            params["preferred_currency"] = preferred_currency

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = :user_id"
            conn.execute(text(sql), params)

def get_user_profile(user_id):
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT company_name, company_address, company_phone, company_email,
                   company_tax_id, seller_ntn, seller_strn, preferred_currency,
                   created_at, id, email
            FROM users WHERE id = :user_id
        '''), {"user_id": user_id}).fetchone()

    if result:
        return {
            'company_name': result[0],
            'company_address': result[1],
            'company_phone': result[2],
            'company_email': result[3],
            'company_tax_id': result[4],
            'seller_ntn': result[5],
            'seller_strn': result[6],
            'preferred_currency': result[7] or 'PKR',
            'created_at': result[8].strftime('%Y-%m-%d') if result[8] else None,
            'id': result[9],
            'email': result[10]
        }
    return {}

def get_business_summary(user_id):
    """Get overall business summary"""
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT
                COUNT(*) as total_invoices,
                COALESCE(SUM(grand_total), 0) as total_revenue,
                COALESCE(AVG(grand_total), 0) as avg_invoice,
                MIN(invoice_date) as first_invoice,
                MAX(invoice_date) as last_invoice
            FROM user_invoices
            WHERE user_id = :user_id
        '''), {"user_id": user_id}).fetchone()

    if result and result[0] > 0:
        return {
            'total_invoices': result[0],
            'total_revenue': float(result[1]),
            'avg_invoice': float(result[2]),
            'first_invoice': result[3].isoformat() if result[3] else None,
            'last_invoice': result[4].isoformat() if result[4] else None
        }
    return {
        'total_invoices': 0,
        'total_revenue': 0,
        'avg_invoice': 0,
        'first_invoice': None,
        'last_invoice': None
    }

def get_client_analytics(user_id):
    """Get top clients by revenue"""
    with DB_ENGINE.connect() as conn:
        results = conn.execute(text('''
            SELECT
                client_name,
                COUNT(*) as invoice_count,
                COALESCE(SUM(grand_total), 0) as total_spent,
                COALESCE(AVG(grand_total), 0) as avg_invoice
            FROM user_invoices
            WHERE user_id = :user_id
            GROUP BY client_name
            ORDER BY total_spent DESC
            LIMIT 10
        '''), {"user_id": user_id}).fetchall()

    clients = []
    for row in results:
        clients.append({
            'client_name': row[0],
            'invoice_count': row[1],
            'total_spent': float(row[2]),
            'avg_invoice': float(row[3])
        })

    return clients

from datetime import datetime

def save_user_invoice(user_id, invoice_data):
    """Save invoice data with metadata"""
    with DB_ENGINE.begin() as conn:
        invoice_number = invoice_data.get('invoice_number', 'Unknown')
        client_name = invoice_data.get('client_name', 'Unknown Client')
        invoice_date_str = invoice_data.get('invoice_date', '')
        due_date_str = invoice_data.get('due_date', '')
        grand_total = float(invoice_data.get('grand_total', 0))
        invoice_json = json.dumps(invoice_data)

        # Convert date strings to date objects or None
        invoice_date = None
        if invoice_date_str:
            try:
                invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
            except ValueError:
                invoice_date = None

        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                due_date = None

        conn.execute(text('''
            INSERT INTO user_invoices
            (user_id, invoice_number, client_name, invoice_date, due_date, grand_total, invoice_data)
            VALUES (:user_id, :invoice_number, :client_name, :invoice_date, :due_date, :grand_total, :invoice_json)
        '''), {
            "user_id": user_id,
            "invoice_number": invoice_number,
            "client_name": client_name,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "grand_total": grand_total,
            "invoice_json": invoice_json
        })



        # Auto-save customer
        customer_data = {
            'name': client_name,
            'email': invoice_data.get('client_email', ''),
            'phone': invoice_data.get('client_phone', ''),
            'address': invoice_data.get('client_address', ''),
            'tax_id': invoice_data.get('buyer_ntn', '')
        }

        result = conn.execute(text("SELECT id FROM customers WHERE user_id = :user_id AND name = :name"),
                             {"user_id": user_id, "name": customer_data['name']}).fetchone()

        if result:
            conn.execute(text('''
                UPDATE customers SET
                email=:email, phone=:phone, address=:address, tax_id=:tax_id,
                invoice_count = invoice_count + 1,
                total_spent = total_spent + :grand_total,
                updated_at=CURRENT_TIMESTAMP
                WHERE id=:id
            '''), {
                "email": customer_data['email'], "phone": customer_data['phone'],
                "address": customer_data['address'], "tax_id": customer_data['tax_id'],
                "grand_total": grand_total, "id": result[0]
            })
        else:
            conn.execute(text('''
                INSERT INTO customers
                (user_id, name, email, phone, address, tax_id, total_spent, invoice_count)
                VALUES (:user_id, :name, :email, :phone, :address, :tax_id, :grand_total, 1)
            '''), {
                "user_id": user_id, "name": customer_data['name'], "email": customer_data['email'],
                "phone": customer_data['phone'], "address": customer_data['address'],
                "tax_id": customer_data['tax_id'], "grand_total": grand_total
            })

    return True

def get_customers(user_id):
    """Get all customers"""
    with DB_ENGINE.connect() as conn:
        customers = conn.execute(text('''
            SELECT id, name, email, phone, address, tax_id, total_spent, invoice_count
            FROM customers WHERE user_id = :user_id ORDER BY name
        '''), {"user_id": user_id}).fetchall()

    result = []
    for customer in customers:
        result.append({
            'id': customer[0],
            'name': customer[1],
            'email': customer[2],
            'phone': customer[3],
            'address': customer[4],
            'tax_id': customer[5],
            'total_spent': float(customer[6]) if customer[6] else 0,
            'invoice_count': customer[7]
        })
    return result

def save_expense(user_id, expense_data):
    """Save business expense"""
    with DB_ENGINE.begin() as conn:
        conn.execute(text('''
            INSERT INTO expenses (user_id, description, amount, category, expense_date, notes)
            VALUES (:user_id, :description, :amount, :category, :expense_date, :notes)
        '''), {
            "user_id": user_id, "description": expense_data['description'], "amount": expense_data['amount'],
            "category": expense_data['category'], "expense_date": expense_data['expense_date'],
            "notes": expense_data.get('notes', '')
        })
    return True

def get_expenses(user_id, limit=50):
    """Get expenses for a user"""
    with DB_ENGINE.connect() as conn:
        expenses = conn.execute(text('''
            SELECT id, description, amount, category, expense_date, notes, created_at
            FROM expenses WHERE user_id = :user_id
            ORDER BY expense_date DESC, created_at DESC
            LIMIT :limit
        '''), {"user_id": user_id, "limit": limit}).fetchall()

    result = []
    for expense in expenses:
        result.append({
            'id': expense[0],
            'description': expense[1],
            'amount': float(expense[2]),
            'category': expense[3],
            'expense_date': expense[4],
            'notes': expense[5],
            'created_at': expense[6]
        })
    return result

def get_expense_summary(user_id):
    """Get expense summary by category"""
    with DB_ENGINE.connect() as conn:
        summary = conn.execute(text('''
            SELECT category, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
            FROM expenses WHERE user_id = :user_id
            GROUP BY category ORDER BY total DESC
        '''), {"user_id": user_id}).fetchall()

    result = []
    for item in summary:
        result.append({
            'category': item[0],
            'total': float(item[1]),
            'count': item[2]
        })
    return result

def change_user_password(user_id, new_password):
    """Change user password"""
    with DB_ENGINE.begin() as conn:
        conn.execute(text("UPDATE users SET password_hash = :hash WHERE id = :id"),
                     {"id": user_id, "hash": hash_password(new_password)})
    return True
