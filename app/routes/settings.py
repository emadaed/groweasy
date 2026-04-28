from flask import Blueprint, render_template, request, redirect, url_for, flash, session, g, make_response
from app.services.auth import get_user_profile, update_user_profile, change_user_password, verify_user
from app.services.cache import get_user_profile_cached, invalidate_user_profile_cache  # FIX: added invalidate import
from app.services.session_manager import SessionManager

settings_bp = Blueprint('settings', __name__)

# SETTINGS - 1
@settings_bp.route("/settings", methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    user_profile = get_user_profile_cached(session['user_id'])

    if request.method == 'POST':
        # Handle profile update
        if 'update_profile' in request.form:
            # FIX: explicitly convert the checkbox value to a Python bool.
            # When unchecked, request.form.get('show_fbr_fields') returns None,
            # which update_user_profile() silently skips — so FALSE was never
            # written to the DB.  Mapping to bool here fixes that.
            show_fbr = request.form.get('show_fbr_fields') == 'on'

            update_user_profile(
                session['user_id'],
                company_name=request.form.get('company_name'),
                company_address=request.form.get('company_address'),
                company_phone=request.form.get('company_phone'),
                company_email=request.form.get('company_email'),
                company_tax_id=request.form.get('company_tax_id'),
                seller_ntn=request.form.get('seller_ntn'),
                seller_strn=request.form.get('seller_strn'),
                preferred_currency=request.form.get('preferred_currency'),
                show_fbr_fields=show_fbr,  # FIX: always a bool, never None
            )

            # FIX: bust the memoized cache so the redirect GET reads fresh DB data.
            # Without this the page re-renders with the old (stale) profile and
            # the toggle appears to revert even though the DB was updated correctly.
            invalidate_user_profile_cache(session['user_id'])

            flash('✅ Settings updated successfully!', 'success')
            response = make_response(redirect(url_for('settings.settings')))
            # Security headers to prevent back-button showing sensitive data
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            return response

        # Handle password change
        elif 'change_password' in request.form:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not verify_user(user_profile['email'], current_password):
                flash('❌ Current password is incorrect', 'error')
            elif new_password != confirm_password:
                flash('❌ New passwords do not match', 'error')
            elif len(new_password) < 6:
                flash('❌ Password must be at least 6 characters', 'error')
            else:
                change_user_password(session['user_id'], new_password)
                flash('✅ Password changed successfully!', 'success')

            return redirect(url_for('settings.settings'))

    return render_template("settings.html", user_profile=user_profile, nonce=g.nonce)

# DEVICE MANAGEMENT - 2
@settings_bp.route("/devices")
def devices():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    active_sessions = SessionManager.get_active_sessions(session['user_id'])
    return render_template("devices.html",
                         sessions=active_sessions,
                         current_token=session.get('session_token'),
                         nonce=g.nonce)

# REVOKE TOKEN - 3
@settings_bp.route("/revoke_device/<token>", methods=['GET', 'POST'])
def revoke_device(token):
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if token == session.get('session_token'):
        flash('❌ Cannot revoke current session', 'error')
    else:
        SessionManager.revoke_session(token)
        flash('✅ Device session revoked', 'success')

    return redirect(url_for('settings.devices'))

# REVOKE ALL DEVICES - 4
@settings_bp.route("/revoke_all_devices", methods=['GET', 'POST'])
def revoke_all_devices():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    SessionManager.revoke_all_sessions(session['user_id'], except_token=session.get('session_token'))
    flash('✅ All other devices logged out', 'success')
    return redirect(url_for('settings.devices'))
