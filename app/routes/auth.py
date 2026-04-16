# app/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app.extensions import limiter
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.services.auth import verify_user, get_user_profile, create_user, change_user_password
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached
from app.services.account import create_account, check_user_limit
from app.decorators import role_required
import threading
from flask_mail import Message
from app import mail
from flask import current_app
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature


def _get_reset_serializer():
    """Return a serializer scoped to password-reset tokens."""
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='password-reset')


def send_welcome_email_async(user_email, plan):
    def _send(app):
        with app.app_context():
            msg = Message(
                subject="Welcome to Groweasy!",
                recipients=[user_email]
            )
            msg.body = f"""
Thank you for signing up for Groweasy!

You've selected the {plan.capitalize()} plan.
You can now log in and start managing your business.

Best regards,
The Groweasy Team
"""
            try:
                mail.send(msg)
            except Exception as e:
                app.logger.error(f"Failed to send welcome email: {e}")

    app = current_app._get_current_object()
    threading.Thread(target=_send, args=(app,)).start()


def send_reset_email_async(user_email, reset_link):
    """Send password reset email in a background thread."""
    def _send(app):
        with app.app_context():
            msg = Message(
                subject="Reset Your Groweasy Password",
                recipients=[user_email]
            )
            msg.body = f"""
You requested a password reset for your Groweasy account.

Click the link below to reset your password (valid for 1 hour):
{reset_link}

If you did not request this, you can safely ignore this email.
Your password will not change.

Best regards,
The Groweasy Team
"""
            try:
                mail.send(msg)
            except Exception as e:
                app.logger.error(f"Failed to send reset email to {user_email}: {e}")

    app = current_app._get_current_object()
    threading.Thread(target=_send, args=(app,)).start()


# Initialize Blueprint
auth_bp = Blueprint('auth', __name__)


# 1. Login
@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user_id = verify_user(email, password)
        if user_id:
            from app.services.session_manager import SessionManager

            if not SessionManager.check_location_restrictions(user_id, request.remote_addr):
                flash('❌ Login not allowed from this location', 'error')
                return render_template('login.html', nonce=g.nonce)

            with DB_ENGINE.connect() as conn:
                row = conn.execute(
                    text("SELECT role, account_id FROM users WHERE id = :uid"),
                    {"uid": user_id}
                ).first()
                role = row[0] if row else 'assistant'
                account_id = row[1] if row else None

            with DB_ENGINE.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO user_logins (user_id, ip_address, user_agent)
                        VALUES (:uid, :ip, :ua)
                    """),
                    {
                        "uid": user_id,
                        "ip": request.remote_addr,
                        "ua": request.user_agent.string
                    }
                )

            session_token = SessionManager.create_session(user_id, request)

            session['user_id'] = user_id
            session['user_email'] = email
            session['role'] = role
            session['account_id'] = account_id
            session['session_token'] = session_token

            flash(random_success_message('login'), 'success')
            return redirect(url_for('main.dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials', nonce=g.nonce)

    return render_template('login.html', nonce=g.nonce)


# 2. Logout
@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))


# 3. Registration
@auth_bp.route("/register", methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def register():
    token = request.args.get('token')
    if request.method == 'GET':
        return render_template('register.html', nonce=g.nonce, token=token)

    if not request.form.get('agree_terms'):
        flash('❌ You must agree to Terms of Service to register', 'error')
        return render_template('register.html', nonce=g.nonce)

    email = request.form.get('email')
    password = request.form.get('password')
    company_name = request.form.get('company_name', '')
    plan = request.form.get('plan', 'starter')
    token = request.form.get('token')

    if plan not in ['starter', 'growth', 'pro']:
        plan = 'starter'

    user_created = create_user(email, password, company_name)
    if not user_created:
        flash('❌ User already exists or registration failed', 'error')
        return render_template('register.html', nonce=g.nonce)

    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email}
        ).first()
        if not row:
            flash("❌ User created but ID not found – please contact support.", "error")
            return render_template('register.html', nonce=g.nonce)
        user_id = row[0]

    if token:
        with DB_ENGINE.begin() as conn:
            invite = conn.execute(
                text("SELECT * FROM user_invites WHERE token = :token AND expires_at > NOW() AND accepted_at IS NULL"),
                {"token": token}
            ).first()
            if invite:
                conn.execute(
                    text("UPDATE users SET account_id = :aid, role = :role WHERE id = :uid"),
                    {"aid": invite.account_id, "role": invite.role, "uid": user_id}
                )
                conn.execute(
                    text("UPDATE user_invites SET accepted_at = NOW() WHERE id = :id"),
                    {"id": invite.id}
                )
                flash('✅ You have been added to the team! Please login.', 'success')
                return redirect(url_for('auth.login'))
            else:
                flash('❌ This invite link is invalid or expired. Please ask the owner to send a new one.', 'error')
                return redirect(url_for('auth.register'))
    else:
        account_id = create_account(company_name, plan)
        with DB_ENGINE.begin() as conn:
            conn.execute(
                text("UPDATE users SET account_id = :aid, role = 'owner' WHERE id = :uid"),
                {"aid": account_id, "uid": user_id}
            )
        flash('✅ Account created! Please login.', 'success')
        send_welcome_email_async(email, plan)
        return redirect(url_for('auth.login'))

    return render_template('register.html', nonce=g.nonce)


# 4. Forgot Password — generates a signed token and emails a real reset link
@auth_bp.route("/forgot_password", methods=['GET', 'POST'])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()

        with DB_ENGINE.connect() as conn:
            result = conn.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email}
            ).fetchone()

        # Always show the same message regardless of whether the email exists.
        # This prevents user enumeration attacks.
        if result:
            s = _get_reset_serializer()
            token = s.dumps(email)
            reset_link = url_for('auth.reset_password', token=token, _external=True)
            send_reset_email_async(email, reset_link)
            current_app.logger.info(f"Password reset requested for {email}")

        flash(
            '📧 If an account with that email exists, reset instructions have been sent.',
            'success'
        )
        return redirect(url_for('auth.login'))

    return render_template('forgot_password.html', nonce=g.nonce)


# 5. Reset Password — validates the signed token and sets the new password
@auth_bp.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_password(token):
    """Token is valid for 1 hour (3600 seconds)."""
    try:
        s = _get_reset_serializer()
        email = s.loads(token, max_age=3600)
    except SignatureExpired:
        flash('❌ This reset link has expired. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))
    except BadSignature:
        flash('❌ Invalid reset link. Please request a new one.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        if len(new_password) < 6:
            flash('❌ Password must be at least 6 characters.', 'error')
            return render_template('reset_password.html', token=token, nonce=g.nonce)

        if new_password != confirm_password:
            flash('❌ Passwords do not match.', 'error')
            return render_template('reset_password.html', token=token, nonce=g.nonce)

        # Fetch user id for change_user_password
        with DB_ENGINE.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM users WHERE email = :email"),
                {"email": email}
            ).fetchone()

        if not row:
            flash('❌ Account not found.', 'error')
            return redirect(url_for('auth.login'))

        change_user_password(row[0], new_password)
        current_app.logger.info(f"Password reset completed for {email}")
        flash('✅ Password updated successfully. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html', token=token, nonce=g.nonce)
