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

from flask import request, abort



@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    
    # 1. BOT PROTECTION: Only run AI for real browsers
    user_agent = request.headers.get('User-Agent', '').lower()
    is_bot = any(bot in user_agent for bot in [
        'bot', 'spider', 'uptime', 'python-requests', 'health', 'crawler', 'sentry'
    ])

    # 2. HANDLE SESSION DATA SAFELY
    now = datetime.now()
    last_run = session.get('ai_timestamp')

    # Convert last_run from string back to datetime if necessary
    if isinstance(last_run, str):
        try:
            # Flask's default session serializer might store this as an ISO string
            last_run = datetime.fromisoformat(last_run)
        except (ValueError, TypeError):
            last_run = None

    # 3. LOGIC FOR AI INSIGHTS
    ai_advice = None
    if is_bot:
        ai_advice = "AI analysis is reserved for active users."
    # Now that last_run is a datetime object, the subtraction will work
    elif 'ai_advice' in session and last_run and (now - last_run) < timedelta(minutes=10):
        ai_advice = session['ai_advice']
    else:
        data = ReportService.get_financial_summary(user_id)
        ai_advice = get_gemini_insights(data)
        
        session['ai_advice'] = ai_advice
        # Store as ISO string to ensure it's handled correctly by the session cookie
        session['ai_timestamp'] = now.isoformat()

    # 4. FINAL UI SETUP
    data = ReportService.get_financial_summary(user_id)
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

from weasyprint import HTML
from io import BytesIO
from flask import send_file

@reports_bp.route('/reports/download/pdf')
def download_pdf():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    data = ReportService.get_financial_summary(user_id)
    
    # 1. Reuse your existing dashboard template or a special 'print' version
    # Note: Use a simplified template for PDF if the dashboard has too many buttons
    rendered_html = render_template('reports_dashboard.html', 
                                    data=data, 
                                    ai_advice=session.get('ai_advice', "No analysis available."),
                                    is_pdf=True) # Flag to hide buttons in PDF

    # 2. Convert HTML to PDF in memory
    pdf_buffer = BytesIO()
    HTML(string=rendered_html, base_url=request.base_url).write_pdf(pdf_buffer)
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Finance_Report_{datetime.now().strftime('%Y%m%d')}.pdf"
    )
