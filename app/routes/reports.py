# app/routes/reports.py
from flask import Blueprint, render_template, session, redirect, url_for, g, current_app
from app.services.report_service import ReportService
from app.services.ai_service import get_gemini_insights
from app.services.cache import get_user_profile_cached
# You MUST import this to avoid a NameError
from app.context_processors import CURRENCY_SYMBOLS 

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    
    try:
        data = ReportService.get_financial_summary(user_id)
        ai_advice = get_gemini_insights(data)
        
        # Get user currency preference
        user_profile = get_user_profile_cached(user_id)
        user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')

        return render_template('reports_dashboard.html', 
                               data=data, 
                               ai_advice=ai_advice,
                               currency_symbol=user_symbol,
                               nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Dashboard Error: {str(e)}")
        # Fallback if AI or DB fails temporarily
        return render_template('reports_dashboard.html', 
                               data={'revenue':0, 'costs':0, 'net_profit':0, 'tax_liability':0, 'inventory_value':0}, 
                               ai_advice="Gemini is analyzing your data. Please refresh in a moment.",
                               currency_symbol="Rs.",
                               nonce=g.nonce)
