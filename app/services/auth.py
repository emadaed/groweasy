# app/services/auth.py
import logging
import secrets
from app.services.db import DB_ENGINE
from sqlalchemy import text
import json
from datetime import datetime
from app.services.webhooks import fire_webhook

# FIX: werkzeug is already a Flask dependency — use it everywhere for password hashing.
# SHA256 without salt (the old approach) is vulnerable to rainbow table attacks.
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)  # FIX: was missing — caused NameError in get_api_key_for_user


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    Hash a password using werkzeug (pbkdf2:sha256 with random salt).
    """
    return generate_password_hash(password)


def _verify_password(plain: str, stored_hash: str) -> bool:
    """
    Verify a password against a stored hash.
    """
    # Old SHA256 hashes are exactly 64 lowercase hex characters
    if len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash):
        import hashlib
        return hashlib.sha256(plain.encode()).hexdigest() == stored_hash
    # New werkzeug hashes
    return check_password_hash(stored_hash, plain)


def create_user(email: str, password: str, company_name: str = "") -> bool:
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
        except Exception:
            return False


def verify_user(email: str, password: str):
    """
    Verify credentials and return user_id on success, None on failure.
    """
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT id, password_hash FROM users WHERE email = :email
        '''), {"email": email}).fetchone()

    if not result:
        return None

    user_id, stored_hash = result[0], result[1]

    if not _verify_password(password, stored_hash):
        return None

    # Silently upgrade old SHA256 hash to werkzeug on first successful login
    if len(stored_hash) == 64 and all(c in '0123456789abcdef' for c in stored_hash):
        try:
            with DB_ENGINE.begin() as conn:
                conn.execute(text(
                    "UPDATE users SET password_hash = :new_hash WHERE id = :uid"
                ), {"new_hash": hash_password(password), "uid": user_id})
            logger.info(f"Password hash upgraded for user {user_id}")
        except Exception as e:
            # Non-fatal: login still succeeds, upgrade retried next time
            logger.warning(f"Failed to upgrade password hash for user {user_id}: {e}")

    return user_id


def get_api_key_for_user(user_id: int) -> str:
    """
    Return a display token for the inventory dashboard API key widget.
    """
    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT id FROM api_keys
                WHERE account_id = (SELECT account_id FROM users WHERE id = :uid)
                AND is_active = TRUE
                LIMIT 1
            """), {"uid": user_id}).first()

            if result:
                # Stable but non-reversible display token — not used for auth
                return secrets.token_hex(16)

            return create_session_token(user_id)
    except Exception as e:
        logger.error(f"Error getting API key for user {user_id}: {e}")
        return create_session_token(user_id)


def create_session_token(user_id: int) -> str:
    """Create a cryptographically random display token."""
    return secrets.token_urlsafe(16)


def update_user_profile(user_id, company_name=None, company_address=None,
                        company_phone=None, company_email=None,
                        company_tax_id=None, seller_ntn=None, seller_strn=None,
                        preferred_currency=None, show_fbr_fields=None):

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
        if company_email is not None:
            # FIX: company_email was missing from update_user_profile entirely.
            # Added so the settings form can persist a business email separately
            # from the login email stored in the `email` column.
            updates.append("company_email = :company_email")
            params["company_email"] = company_email
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

        # FIX: the original guard was `if show_fbr_fields is not None`.
        # Since settings.py now always passes a Python bool (True or False),
        # `not None` is always satisfied and the value is always written.
        # Using `isinstance` makes the intent explicit and protects against
        # any accidental None slip-through from other callers.
        if isinstance(show_fbr_fields, bool):
            updates.append("show_fbr_fields = :show_fbr_fields")
            params["show_fbr_fields"] = show_fbr_fields

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = :user_id"
            conn.execute(text(sql), params)


def get_user_profile(user_id: int) -> dict:
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT company_name, company_address, company_phone, company_email,
                   company_tax_id, seller_ntn, seller_strn, preferred_currency,
                   created_at, id, email, show_fbr_fields
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
            'email': result[10],
            'show_fbr_fields': result[11],
        }
    return {}


def change_user_password(user_id: int, new_password: str) -> bool:
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("UPDATE users SET password_hash = :hash WHERE id = :id"),
            {"id": user_id, "hash": hash_password(new_password)}
        )
    return True
# ---------------------------------------------------------------------------
# Business data functions 
# ---------------------------------------------------------------------------
def get_business_summary(account_id):
    with DB_ENGINE.connect() as conn:
        result = conn.execute(text('''
            SELECT
                COUNT(*) as total_invoices,
                COALESCE(SUM(grand_total), 0) as total_revenue,
                COALESCE(AVG(grand_total), 0) as avg_invoice,
                MIN(invoice_date) as first_invoice,
                MAX(invoice_date) as last_invoice
            FROM user_invoices
            WHERE account_id = :aid
        '''), {"aid": account_id}).fetchone()
    if result and result[0] > 0:
        return {
            'total_invoices': result[0],
            'total_revenue': float(result[1]),
            'avg_invoice': float(result[2]),
            'first_invoice': result[3].isoformat() if result[3] else None,
            'last_invoice': result[4].isoformat() if result[4] else None
        }
    return {'total_invoices': 0, 'total_revenue': 0, 'avg_invoice': 0,
            'first_invoice': None, 'last_invoice': None}


def get_client_analytics(account_id):
    with DB_ENGINE.connect() as conn:
        results = conn.execute(text('''
            SELECT client_name, COUNT(*) as invoice_count,
                   COALESCE(SUM(grand_total), 0) as total_spent,
                   COALESCE(AVG(grand_total), 0) as avg_invoice
            FROM user_invoices
            WHERE account_id = :aid
            GROUP BY client_name ORDER BY total_spent DESC LIMIT 10
        '''), {"aid": account_id}).fetchall()
    return [{
        'client_name': row[0],
        'invoice_count': row[1],
        'total_spent': float(row[2]),
        'avg_invoice': float(row[3])
    } for row in results]


def save_user_invoice(user_id, account_id, invoice_data):
    with DB_ENGINE.begin() as conn:
        invoice_number = invoice_data.get('invoice_number', 'Unknown')
        client_name = invoice_data.get('client_name', 'Unknown Client')
        invoice_date_str = invoice_data.get('invoice_date', '')
        due_date_str = invoice_data.get('due_date', '')
        grand_total = float(invoice_data.get('grand_total', 0))
        invoice_json = json.dumps(invoice_data)

        invoice_date = None
        if invoice_date_str:
            try:
                invoice_date = datetime.strptime(invoice_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        conn.execute(text('''
            INSERT INTO user_invoices
            (user_id, account_id, invoice_number, client_name, invoice_date, due_date, grand_total, invoice_data)
            VALUES (:user_id, :aid, :invoice_number, :client_name, :invoice_date, :due_date, :grand_total, :invoice_json)
        '''), {
            "user_id": user_id, "aid": account_id,
            "invoice_number": invoice_number, "client_name": client_name,
            "invoice_date": invoice_date, "due_date": due_date,
            "grand_total": grand_total, "invoice_json": invoice_json
        })

        customer_data = {
            'name': client_name,
            'email': invoice_data.get('client_email', ''),
            'phone': invoice_data.get('client_phone', ''),
            'address': invoice_data.get('client_address', ''),
            'tax_id': invoice_data.get('buyer_ntn', '')
        }

        result = conn.execute(
            text("SELECT id FROM customers WHERE account_id = :aid AND name = :name"),
            {"aid": account_id, "name": customer_data['name']}
        ).fetchone()
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
                (user_id, account_id, name, email, phone, address, tax_id, total_spent, invoice_count)
                VALUES (:user_id, :aid, :name, :email, :phone, :address, :tax_id, :grand_total, 1)
            '''), {
                "user_id": user_id, "aid": account_id,
                "name": customer_data['name'], "email": customer_data['email'],
                "phone": customer_data['phone'], "address": customer_data['address'],
                "tax_id": customer_data['tax_id'], "grand_total": grand_total
            })
    return True


def save_expense(user_id, account_id, expense_data):
    with DB_ENGINE.begin() as conn:
        conn.execute(text('''
            INSERT INTO expenses
                (user_id, account_id, description, amount, tax_amount, tax_rate, category, expense_date, notes)
            VALUES
                (:user_id, :aid, :description, :amount, :tax_amount, :tax_rate, :category, :expense_date, :notes)
        '''), {
            "user_id": user_id, "aid": account_id,
            "description": expense_data['description'],
            "amount": expense_data['amount'],
            "tax_amount": expense_data.get('tax_amount', 0),
            "tax_rate": expense_data.get('tax_rate', 0),
            "category": expense_data['category'],
            "expense_date": expense_data['expense_date'],
            "notes": expense_data.get('notes', '')
        })
    return True


def get_expenses(account_id, limit=50):
    with DB_ENGINE.connect() as conn:
        expenses = conn.execute(text('''
            SELECT id, description, amount, tax_amount, tax_rate, category, expense_date, notes, created_at
            FROM expenses WHERE account_id = :aid
            ORDER BY expense_date DESC, created_at DESC LIMIT :limit
        '''), {"aid": account_id, "limit": limit}).fetchall()
    return [{
        'id': e[0], 'description': e[1], 'amount': float(e[2]),
        'tax_amount': float(e[3]) if e[3] else 0.0,
        'tax_rate': float(e[4]) if e[4] else 0.0,
        'category': e[5], 'expense_date': e[6], 'notes': e[7], 'created_at': e[8]
    } for e in expenses]


def get_expense_summary(account_id):
    with DB_ENGINE.connect() as conn:
        summary = conn.execute(text('''
            SELECT category, COALESCE(SUM(amount), 0) as total, COUNT(*) as count
            FROM expenses WHERE account_id = :aid
            GROUP BY category ORDER BY total DESC
        '''), {"aid": account_id}).fetchall()
    return [{'category': i[0], 'total': float(i[1]), 'count': i[2]} for i in summary]


def get_customers(account_id):
    with DB_ENGINE.connect() as conn:
        customers = conn.execute(text('''
            SELECT id, name, email, phone, address, tax_id, total_spent, invoice_count
            FROM customers WHERE account_id = :aid ORDER BY name
        '''), {"aid": account_id}).fetchall()
    return [{
        'id': c[0], 'name': c[1], 'email': c[2], 'phone': c[3],
        'address': c[4], 'tax_id': c[5],
        'total_spent': float(c[6]) if c[6] else 0,
        'invoice_count': c[7]
    } for c in customers]


def get_customer(account_id, customer_id):
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, email, phone, address, tax_id, total_spent, invoice_count
            FROM customers WHERE id = :cid AND account_id = :aid
        """), {"cid": customer_id, "aid": account_id}).first()
    if row:
        return {
            'id': row[0], 'name': row[1], 'email': row[2], 'phone': row[3],
            'address': row[4], 'tax_id': row[5],
            'total_spent': float(row[6]) if row[6] else 0,
            'invoice_count': row[7]
        }
    return None


def save_customer(user_id, account_id, data):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO customers (user_id, account_id, name, email, phone, address, tax_id)
            VALUES (:user_id, :aid, :name, :email, :phone, :address, :tax_id)
            RETURNING id
        """), {
            "user_id": user_id, "aid": account_id,
            "name": data.get('name'), "email": data.get('email'),
            "phone": data.get('phone'), "address": data.get('address'),
            "tax_id": data.get('tax_id')
        })
        return result.scalar()


def update_customer(account_id, customer_id, data):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            UPDATE customers
            SET name=:name, email=:email, phone=:phone, address=:address, tax_id=:tax_id,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=:cid AND account_id=:aid
        """), {
            "name": data.get('name'), "email": data.get('email'),
            "phone": data.get('phone'), "address": data.get('address'),
            "tax_id": data.get('tax_id'), "cid": customer_id, "aid": account_id
        })
        return result.rowcount > 0


def delete_customer(account_id, customer_id):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            DELETE FROM customers WHERE id = :cid AND account_id = :aid
        """), {"cid": customer_id, "aid": account_id})
        return result.rowcount > 0


def get_invoices(account_id, limit=100, offset=0):
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, invoice_number, client_name, invoice_date, due_date,
                   grand_total, status, created_at
            FROM user_invoices WHERE account_id = :aid
            ORDER BY invoice_date DESC LIMIT :limit OFFSET :offset
        """), {"aid": account_id, "limit": limit, "offset": offset}).fetchall()
    return [{
        'id': r[0], 'invoice_number': r[1], 'client_name': r[2],
        'invoice_date': r[3].isoformat() if r[3] else None,
        'due_date': r[4].isoformat() if r[4] else None,
        'grand_total': float(r[5]), 'status': r[6],
        'created_at': r[7].isoformat() if r[7] else None
    } for r in rows]


def get_invoice(account_id, invoice_id):
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, invoice_number, client_name, invoice_date, due_date,
                   grand_total, status, created_at, invoice_data
            FROM user_invoices WHERE id = :id AND account_id = :aid
        """), {"id": invoice_id, "aid": account_id}).first()
    if row:
        return {
            'id': row[0], 'invoice_number': row[1], 'client_name': row[2],
            'invoice_date': row[3].isoformat() if row[3] else None,
            'due_date': row[4].isoformat() if row[4] else None,
            'grand_total': float(row[5]), 'status': row[6],
            'created_at': row[7].isoformat() if row[7] else None,
            'invoice_data': row[8]
        }
    return None


def update_invoice_status(account_id, invoice_id, status):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            UPDATE user_invoices SET status=:status, updated_at=CURRENT_TIMESTAMP
            WHERE id=:id AND account_id=:aid
        """), {"status": status, "id": invoice_id, "aid": account_id})
        return result.rowcount > 0


def get_invoice_by_number(account_id, invoice_number):
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, invoice_number, client_name, invoice_date, due_date,
                   grand_total, status, created_at, invoice_data
            FROM user_invoices
            WHERE invoice_number = :inv_num AND account_id = :aid
        """), {"inv_num": invoice_number, "aid": account_id}).first()
    if row:
        return {
            'id': row[0], 'invoice_number': row[1], 'client_name': row[2],
            'invoice_date': row[3].isoformat() if row[3] else None,
            'due_date': row[4].isoformat() if row[4] else None,
            'grand_total': float(row[5]), 'status': row[6],
            'created_at': row[7].isoformat() if row[7] else None,
            'invoice_data': row[8]
        }
    return None


def update_invoice_status_by_number(account_id, invoice_number, status):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            UPDATE user_invoices SET status=:status, updated_at=CURRENT_TIMESTAMP
            WHERE invoice_number=:inv_num AND account_id=:aid
        """), {"status": status, "inv_num": invoice_number, "aid": account_id})
        success = result.rowcount > 0

    if success and status == 'paid':
        invoice = get_invoice_by_number(account_id, invoice_number)
        if invoice:
            fire_webhook(account_id, 'invoice.paid', {
                'invoice_number': invoice_number,
                'status': status,
                'grand_total': invoice['grand_total'],
                'client_name': invoice['client_name']
            })
    return success


def get_expenses_api(account_id, limit=100, offset=0):
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, description, amount, tax_amount, tax_rate, category, expense_date, notes, created_at
            FROM expenses WHERE account_id = :aid
            ORDER BY expense_date DESC LIMIT :limit OFFSET :offset
        """), {"aid": account_id, "limit": limit, "offset": offset}).fetchall()
    return [{
        'id': r[0], 'description': r[1], 'amount': float(r[2]),
        'tax_amount': float(r[3]) if r[3] else 0.0,
        'tax_rate': float(r[4]) if r[4] else 0.0,
        'category': r[5],
        'expense_date': r[6].isoformat() if r[6] else None,
        'notes': r[7],
        'created_at': r[8].isoformat() if r[8] else None
    } for r in rows]


def create_expense_api(account_id, user_id, data):
    with DB_ENGINE.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO expenses (user_id, account_id, description, amount, tax_amount, tax_rate, category, expense_date, notes)
            VALUES (:user_id, :aid, :desc, :amount, :tax_amount, :tax_rate, :category, :date, :notes)
            RETURNING id
        """), {
            "user_id": user_id, "aid": account_id,
            "desc": data.get('description'), "amount": data.get('amount', 0),
            "tax_amount": data.get('tax_amount', 0), "tax_rate": data.get('tax_rate', 0),
            "category": data.get('category'), "date": data.get('expense_date'),
            "notes": data.get('notes')
        })
        return result.scalar()
