# app/routes/auth.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g
from app import limiter
from app.services.auth import verify_user, get_user_profile
from app.services.utils import random_success_message
from app.services.cache import get_user_profile_cached

# Initialize Blueprint
auth_bp = Blueprint('auth', __name__)

# 2. @app.route('/login')
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
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials', nonce=g.nonce)

    # GET request - show login form
    return render_template('login.html', nonce=g.nonce)


# 3. @auth_bp.route('/logout')
@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))  # Changed from 'home' to 'login'

