# app/routes/users.py
from flask import Blueprint, render_template, session, redirect, url_for, request, flash, g, current_app
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.decorators import role_required
from werkzeug.security import generate_password_hash
import secrets
from datetime import datetime, timedelta
import threading

users_bp = Blueprint('users', __name__, url_prefix='/users')

def send_invite_email_async(email, token, inviter_name):
    """Send invite email in a background thread to avoid slowing down the request."""
    def _send():
        from flask_mail import Message
        from app import mail
        with current_app.app_context():
            register_link = url_for('auth.register', token=token, _external=True)
            msg = Message(
                subject=f"{inviter_name} invited you to join Groweasy",
                recipients=[email]
            )
            msg.body = f"""
You've been invited to join {inviter_name}'s team on Groweasy.

Click the link below to create your account and get started:
{register_link}

This invite will expire in 7 days.
"""
            try:
                mail.send(msg)
            except Exception as e:
                current_app.logger.error(f"Failed to send invite email: {e}")
    threading.Thread(target=_send).start()

@users_bp.route('/')
@role_required('owner')
def list_users():
    account_id = session['account_id']
    with DB_ENGINE.connect() as conn:
        # Get current team members
        rows = conn.execute(
            text("SELECT id, email, role, created_at FROM users WHERE account_id = :aid ORDER BY created_at"),
            {"aid": account_id}
        ).fetchall()
        users = [dict(row._mapping) for row in rows]

        # Get pending invites
        invites = conn.execute(
            text("SELECT id, email, role, expires_at FROM user_invites WHERE account_id = :aid AND accepted_at IS NULL ORDER BY created_at DESC"),
            {"aid": account_id}
        ).fetchall()
        pending_invites = [dict(row._mapping) for row in invites]

    return render_template('users/list.html', users=users, pending_invites=pending_invites, nonce=g.nonce)

@users_bp.route('/add', methods=['GET', 'POST'])
@role_required('owner')
def add_user():
    account_id = session['account_id']
    if request.method == 'POST':
        email = request.form.get('email')
        role = request.form.get('role', 'assistant')
        password = request.form.get('password')

        # Check user limit
        from app.services.account import check_user_limit
        allowed, msg = check_user_limit(account_id, 1)
        if not allowed:
            flash(f"❌ {msg}", 'error')
            return redirect(url_for('users.add_user'))

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

            # Insert user (company_name empty, will be set later)
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
    user = dict(row._mapping)
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
        # Optionally, you might want to delete related data first if cascade is not set.
        # But we assume foreign keys are set to cascade.
        conn.execute(
            text("DELETE FROM users WHERE id = :uid AND account_id = :aid"),
            {"uid": user_id, "aid": account_id}
        )
    flash("✅ User deleted", 'success')
    return redirect(url_for('users.list_users'))

@users_bp.route('/send_invite', methods=['POST'])
@role_required('owner')
def send_invite():
    account_id = session['account_id']
    email = request.form.get('email')
    role = request.form.get('role', 'assistant')

    with DB_ENGINE.connect() as conn:
        # Check if user already exists in this account
        existing = conn.execute(
            text("SELECT id FROM users WHERE email = :email AND account_id = :aid"),
            {"email": email, "aid": account_id}
        ).first()
        if existing:
            flash("❌ This user is already part of your account.", 'error')
            return redirect(url_for('users.list_users'))

        # Check if there's already a pending invite for this email
        pending = conn.execute(
            text("SELECT id FROM user_invites WHERE email = :email AND account_id = :aid AND accepted_at IS NULL"),
            {"email": email, "aid": account_id}
        ).first()
        if pending:
            flash("⚠️ An invite has already been sent to this email.", 'warning')
            return redirect(url_for('users.list_users'))

        # Generate token and expiration (7 days)
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(days=7)

        # Get inviter name (owner's email)
        inviter_name = session.get('user_email', 'The owner')

        with DB_ENGINE.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO user_invites (account_id, email, role, token, expires_at, created_by)
                    VALUES (:aid, :email, :role, :token, :expires, :creator)
                """),
                {
                    "aid": account_id,
                    "email": email,
                    "role": role,
                    "token": token,
                    "expires": expires_at,
                    "creator": session['user_id']
                }
            )

    # Send email asynchronously
    send_invite_email_async(email, token, inviter_name)
    flash("✅ Invite sent! The user will receive an email with registration link.", 'success')
    return redirect(url_for('users.list_users'))

@users_bp.route('/revoke_invite/<int:invite_id>', methods=['POST'])
@role_required('owner')
def revoke_invite(invite_id):
    account_id = session['account_id']
    with DB_ENGINE.begin() as conn:
        conn.execute(
            text("DELETE FROM user_invites WHERE id = :id AND account_id = :aid"),
            {"id": invite_id, "aid": account_id}
        )
    flash("✅ Invite revoked.", 'success')
    return redirect(url_for('users.list_users'))
