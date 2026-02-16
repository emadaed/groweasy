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
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    
    # Check if we already have AI advice from the last 10 mins
    if 'ai_advice' in session and session.get('ai_timestamp'):
        # (Optional: Check time delta here)
        ai_advice = session['ai_advice']
    else:
        data = ReportService.get_financial_summary(user_id)
        ai_advice = get_gemini_insights(data)
        session['ai_advice'] = ai_advice
        session['ai_timestamp'] = datetime.now()
    
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

@reports_bp.route('/reports/download/<type>')
def download_report(type):
    user_id = session.get('user_id')
    # Fetch  data as a list of dicts or objects
    # Example: summary_data = ReportService.get_financial_details(user_id)
    
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
