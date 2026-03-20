# app/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app.extensions import limiter
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.services.auth import verify_user, get_user_profile, create_user
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached
from app.services.account import create_account, check_user_limit
from app.decorators import role_required
import threading
from flask_mail import Message
from app import mail
def send_welcome_email_async(user_email, plan):
    from app import create_app
    app = create_app()

    def _send():
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
                print(f"✅ Welcome email sent to {user_email}")
            except Exception as e:
                app.logger.error(f"❌ Failed to send welcome email to {user_email}: {e}")

    threading.Thread(target=_send, daemon=True).start()
    
# Initialize Blueprint
auth_bp = Blueprint('auth', __name__)

# 1. @app.route('/login')
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

            # Fetch user's role and account_id
            with DB_ENGINE.connect() as conn:
                row = conn.execute(
                    text("SELECT role, account_id FROM users WHERE id = :uid"),
                    {"uid": user_id}
                ).first()
                role = row[0] if row else 'assistant'
                account_id = row[1] if row else None

            # --- INSERT LOGIN RECORD ---
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
            # __Session management__etc

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


# 2. @auth_bp.route('/logout')
@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('auth.login'))  # Changed from 'home' to 'login'

# 3. Registration
@auth_bp.route("/register", methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def register():
    # --- GET: pass token to template (if present) ---
    token = request.args.get('token')
    if request.method == 'GET':
        return render_template('register.html', nonce=g.nonce, token=token)

    # --- POST: handle registration ---
    if not request.form.get('agree_terms'):
        flash('❌ You must agree to Terms of Service to register', 'error')
        return render_template('register.html', nonce=g.nonce)

    email = request.form.get('email')
    password = request.form.get('password')
    company_name = request.form.get('company_name', '')
    plan = request.form.get('plan', 'starter')
    token = request.form.get('token')  # hidden input

    if plan not in ['starter', 'growth', 'pro']:
        plan = 'starter'

    # 1. Create the user (no account yet)
    user_created = create_user(email, password, company_name)
    if not user_created:
        flash('❌ User already exists or registration failed', 'error')
        return render_template('register.html', nonce=g.nonce)

    # 2. Fetch the newly created user's ID
    with DB_ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": email}
        ).first()
        if not row:
            flash("❌ User created but ID not found – please contact support.", "error")
            return render_template('register.html', nonce=g.nonce)
        user_id = row[0]

    # 3. Handle invite token (if any)
    if token:
        with DB_ENGINE.begin() as conn:
            invite = conn.execute(
                text("SELECT * FROM user_invites WHERE token = :token AND expires_at > NOW() AND accepted_at IS NULL"),
                {"token": token}
            ).first()
            if invite:
                # Link to existing account and set role from invite
                conn.execute(
                    text("UPDATE users SET account_id = :aid, role = :role WHERE id = :uid"),
                    {"aid": invite.account_id, "role": invite.role, "uid": user_id}
                )
                # Mark invite as accepted
                conn.execute(
                    text("UPDATE user_invites SET accepted_at = NOW() WHERE id = :id"),
                    {"id": invite.id}
                )
                flash('✅ You have been added to the team! Please login.', 'success')
                return redirect(url_for('auth.login'))
            else:
                flash('❌ Invalid or expired invite token.', 'error')
                # Optionally delete the user? Better to let them retry without token.
                return redirect(url_for('auth.register'))
    else:
        # 4. No token – normal registration: create a new account and set as owner
        account_id = create_account(company_name, plan)
        with DB_ENGINE.begin() as conn:
            conn.execute(
                text("UPDATE users SET account_id = :aid, role = 'owner' WHERE id = :uid"),
                {"aid": account_id, "uid": user_id}
            )
        flash('✅ Account created! Please login.', 'success')

        # 5. Send welcome email (async)   
        send_welcome_email_async(email, plan)
        return redirect(url_for('auth.login'))

    return render_template('register.html', nonce=g.nonce)


# 4 Password Recovery
@auth_bp.route("/forgot_password", methods=['GET', 'POST'])
def forgot_password():
    """Simple password reset request with email simulation"""
    if request.method == 'POST':
        email = request.form.get('email')
        # Check if email exists in database
        with DB_ENGINE.connect() as conn:  # Read-only
            result = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email}).fetchone()

        if result:
            flash('📧 Password reset instructions have been sent to your email.', 'success')
            flash('🔐 Development Note: In production, you would receive an email with reset link.', 'info')
            return render_template('reset_instructions.html', email=email, nonce=g.nonce)
        else:
            flash('❌ No account found with this email address.', 'error')
    return render_template('forgot_password.html', nonce=g.nonce)





