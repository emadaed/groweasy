# app/routes/reports.py
from flask import Blueprint, render_template, session, redirect, url_for, g, current_app, request, jsonify
from sqlalchemy import text
from app.services.report_service import ReportService
from app.services.ai_service import get_gemini_insights
from app.services.cache import get_user_profile_cached
from app.context_processors import CURRENCY_SYMBOLS 

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    
    # FIX: Updated query to use 'user_invoices' and PostgreSQL JSON syntax
    # We use ReportService but ensure its internal SQL matches your db.py
    data = ReportService.get_financial_summary(user_id)
    ai_advice = get_gemini_insights(data)
    
    user_profile = get_user_profile_cached(user_id)
    user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
    user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol=user_symbol,
                           nonce=g.nonce)

@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    """Handles custom prompts from the Gemini Corner"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    user_query = request.json.get('prompt')
    user_id = session['user_id']
    
    # Get current data so Gemini has context for the specific question
    data = ReportService.get_financial_summary(user_id)
    
    # We pass the custom prompt to our service
    answer = get_gemini_insights(data, custom_prompt=user_query)
    
    return jsonify({'answer': answer})
