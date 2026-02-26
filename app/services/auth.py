# app/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app.extensions import limiter
from app.services.db import DB_ENGINE
#from app.services.auth import verify_user, get_user_profile, create_user
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached

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

            # Check location restrictions
            if not SessionManager.check_location_restrictions(user_id, request.remote_addr):
                flash('❌ Login not allowed from this location', 'error')
                return render_template('login.html', nonce=g.nonce)

            # Create secure session
            session_token = SessionManager.create_session(user_id, request)

            session['user_id'] = user_id
            session['user_email'] = email
            session['session_token'] = session_token

            flash(random_success_message('login'), 'success')
            return redirect(url_for('main.dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials', nonce=g.nonce)

    # GET request - show login form
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
        # Validate terms acceptance
        if not request.form.get('agree_terms'):
            flash('❌ You must agree to Terms of Service to register', 'error')
            return render_template('register.html', nonce=g.nonce)

        email = request.form.get('email')
        password = request.form.get('password')
        company_name = request.form.get('company_name', '')

        # 🆕 ADD DEBUG LOGGING
        print(f"📝 Attempting to register user: {email}")
        print(f"🔑 Password length: {len(password) if password else 0}")

        user_created = create_user(email, password, company_name)
        print(f"✅ User creation result: {user_created}")

        if user_created:
            flash('✅ Account created! Please login.', 'success')
            return redirect(url_for('auth.login'))
        else:
            flash('❌ User already exists or registration failed', 'error')
            return render_template('register.html', nonce=g.nonce)

    # GET request - show form
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





