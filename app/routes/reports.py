# app/routes/reports.py
import csv
from io import StringIO
from flask import Blueprint, render_template, Response, session, redirect, url_for, g, current_app, request, jsonify
from sqlalchemy import text
from datetime import datetime, timedelta
from app.services.report_service import ReportService
from app.services.ai_service import get_gemini_insights
from app.services.cache import get_user_profile_cached
from app.context_processors import CURRENCY_SYMBOLS 
from app.services.report_service import ReportService # Assumes you have this service
reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/reports/dashboard')
def dashboard():
    # 1. Access Control
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))        
    user_id = session['user_id']
    
    # 2. BOT PROTECTION: Only run AI for real browsers
    user_agent = request.headers.get('User-Agent', '').lower()
    # List of common bot strings to block from AI usage
    is_bot = any(bot in user_agent for bot in [
        'bot', 'spider', 'uptime', 'python-requests', 'health', 'crawler', 'sentry'
    ])

    # 3. Handle AI Insights with specific 10-minute expiry
    now = datetime.now()
    last_run = session.get('ai_timestamp')
    
    # Logic: If it's a bot, skip AI. If we have fresh advice (under 10 mins), use it.
    if is_bot:
        ai_advice = "AI analysis is reserved for active users."
    elif 'ai_advice' in session and last_run and (now - last_run) < timedelta(minutes=10):
        ai_advice = session['ai_advice']
    else:
        # Fetch fresh data and call Gemini
        data = ReportService.get_financial_summary(user_id)
        ai_advice = get_gemini_insights(data)
        
        # Save to session to prevent re-calls for 10 minutes
        session['ai_advice'] = ai_advice
        session['ai_timestamp'] = now

    # 4. Final UI Setup
    data = ReportService.get_financial_summary(user_id) # Ensure data is loaded for the cards
    user_profile = get_user_profile_cached(user_id)
    user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
    user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol=user_symbol,
                           nonce=g.nonce)

#ask AI
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

#PDF&CSV download
@reports_bp.route('/reports/download/<type>')
def download_report(type):
    user_id = session.get('user_id')
        
    if type == 'csv':
        # 1. Get the data (This is an example, replace with your real query)
        data_to_export = [
            {'Category': 'Total Revenue', 'Value': 5000.00},
            {'Category': 'Net Profit', 'Value': 1200.00},
            {'Category': 'Tax Liability', 'Value': 350.00},
            # Add more rows here from your DB
        ]
        
        # 2. Generate CSV in memory
        si = StringIO()
        cw = csv.DictWriter(si, fieldnames=data_to_export[0].keys())
        cw.writeheader()
        cw.writerows(data_to_export)
        
        # 3. Create response
        output = si.getvalue()
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=business_report.csv"}
        )
    
    elif type == 'pdf':
        # PDF logic usually requires WeasyPrint:
        # html = render_template('pdf/report_template.html', data=data)
        # return render_pdf(HTML(string=html))
        return "PDF generation triggered (Configure WeasyPrint template first)"
