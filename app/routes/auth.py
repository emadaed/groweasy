# app/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app.extensions import limiter
from app.services.db import DB_ENGINE
from app.services.auth import verify_user, get_user_profile, create_user
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached
from app.services.account import create_account, check_user_limit

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
    if request.method == 'POST':
        if not request.form.get('agree_terms'):
            flash('❌ You must agree to Terms of Service to register', 'error')
            return render_template('register.html', nonce=g.nonce)

        email = request.form.get('email')
        password = request.form.get('password')
        company_name = request.form.get('company_name', '')
        plan = request.form.get('plan', 'starter')   # <-- new field from form

        # Validate plan
        if plan not in ['starter', 'growth', 'pro']:
            plan = 'starter'

        # 1. Create the user in the users table (existing create_user returns user_id)
        user_id = create_user(email, password, company_name)
        if not user_id:
            flash('❌ User already exists or registration failed', 'error')
            return render_template('register.html', nonce=g.nonce)

        # 2. Create an account for this user
        account_id = create_account(company_name, plan)

        # 3. Link user to account and set role = owner
        with DB_ENGINE.begin() as conn:
            conn.execute(
                text("UPDATE users SET account_id = :aid, role = 'owner' WHERE id = :uid"),
                {"aid": account_id, "uid": user_id}
            )

        flash('✅ Account created! Please login.', 'success')
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





