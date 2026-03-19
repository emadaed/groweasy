# app/routes/users.py
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, g
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.decorators import role_required
from app.services.account import check_user_limit
from werkzeug.security import generate_password_hash

users_bp = Blueprint('users', __name__, url_prefix='/users')

@users_bp.route('/')
@role_required('owner')
def list_users():
    account_id = session['account_id']
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, role, created_at FROM users WHERE account_id = :aid ORDER BY created_at"),
            {"aid": account_id}
        ).fetchall()
    users = [dict(r) for r in rows]
    return render_template('users/list.html', users=users, nonce=g.nonce)

@users_bp.route('/add', methods=['GET', 'POST'])
@role_required('owner')
def add_user():
    account_id = session['account_id']
    if request.method == 'POST':
        email = request.form.get('email')
        role = request.form.get('role', 'assistant')
        password = request.form.get('password')  # simple; better to send invite email

        # Check user limit
        allowed, msg = check_user_limit(account_id, 1)
        if not allowed:
            flash(f"❌ {msg}", 'error')
            return redirect(url_for('users.add_user'))

        # Create user (we need to hash password)
        password_hash = generate_password_hash(password)

        with DB_ENGINE.begin() as conn:
            # Check if email already exists
            exists = conn.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email}
            ).first()
            if exists:
                flash("❌ Email already registered", 'error')
                return redirect(url_for('users.add_user'))

            conn.execute(
                text("""
                    INSERT INTO users (email, password_hash, account_id, role, company_name)
                    VALUES (:email, :pwd, :aid, :role, '')
                """),
                {"email": email, "pwd": password_hash, "aid": account_id, "role": role}
            )
        flash("✅ User added successfully", 'success')
        return redirect(url_for('users.list_users'))

    return render_template('users/add.html', nonce=g.nonce)

@users_bp.route('/edit/<int:user_id>', methods=['GET', 'POST'])
@role_required('owner')
def edit_user(user_id):
    account_id = session['account_id']
    if request.method == 'POST':
        role = request.form.get('role')
        with DB_ENGINE.begin() as conn:
            conn.execute(
                text("UPDATE users SET role = :role WHERE id = :uid AND account_id = :aid"),
                {"role": role, "uid": user_id, "aid": account_id}
            )
        flash("✅ User role updated", 'success')
        return redirect(url_for('users.list_users'))

    # GET: show current user data
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT id, email, role FROM users WHERE id = :uid AND account_id = :aid"),
            {"uid": user_id, "aid": account_id}
        ).first()
    if not row:
        flash("User not found", 'error')
        return redirect(url_for('users.list_users'))
    user = dict(row)
    return render_template('users/edit.html', user=user, nonce=g.nonce)

@users_bp.route('/delete/<int:user_id>', methods=['POST'])
@role_required('owner')
def delete_user(user_id):
    account_id = session['account_id']
    # Don't allow deleting yourself
    if user_id == session['user_id']:
        flash("❌ You cannot delete your own account", 'error')
        return redirect(url_for('users.list_users'))

    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("DELETE FROM users WHERE id = :uid AND account_id = :aid"),
            {"uid": user_id, "aid": account_id}
        )
    flash("✅ User deleted", 'success')
    return redirect(url_for('users.list_users'))
