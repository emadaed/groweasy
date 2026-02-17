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
from app.services.report_service import ReportService
from flask import request, abort

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/reports/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
        
    user_id = session['user_id']
    
    # 1. Bot Protection Middleware
    user_agent = request.headers.get('User-Agent', '').lower()
    is_bot = any(bot in user_agent for bot in ['bot', 'spider', 'uptime', 'sentry', 'python-requests'])
    
    # 2. Financial & Inventory Data for the cards/chart
    data = ReportService.get_financial_summary(user_id)
    
    # 3. Handle AI Insights (Manual Only)
    # We only show existing advice from the session. 
    # We NEVER call get_gemini_insights() here anymore.
    ai_advice = session.get('ai_advice')

    user_profile = get_user_profile_cached(user_id)
    user_currency = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
    user_symbol = CURRENCY_SYMBOLS.get(user_currency, 'Rs.')

    return render_template('reports_dashboard.html', 
                           data=data, 
                           ai_advice=ai_advice,
                           currency_symbol=user_symbol,
                           nonce=g.nonce)

#Ask AI
@reports_bp.route('/reports/ask_ai', methods=['POST'])
def ask_ai():
    if 'user_id' not in session:
        return jsonify({"answer": "Please log in."}), 401

    user_id = session['user_id']
    user_prompt = request.json.get('prompt', '').strip()
    
    if not user_prompt:
        return jsonify({"answer": "Please provide a question."})

    # 1. Fetch live data from all tables
    # Ensure your ReportService is actually summing grand_total from user_invoices
    data = ReportService.get_financial_summary(user_id)
    
    # 2. Fashionable Manager System Instruction
    system_instruction = f"""
    You are a World-Class Warehouse Manager. Respond with professional, actionable insights.
    CURRENT DATA:
    - Revenue: {data.get('revenue', 0)}
    - Profit: {data.get('net_profit', 0)}
    - Stock Value: {data.get('inventory_value', 0)}
    - Tax: {data.get('tax_liability', 0)}
    
    Use bold text for KPIs and bullet points for advice.
    """

    try:
        # Call Gemini and cache result in session for 1 hour
        response = call_gemini(system_instruction, user_prompt)
        session['ai_advice'] = response
        session.modified = True
        return jsonify({"answer": response})
    except Exception as e:
        # Check if it's specifically a rate limit
        if "429" in str(e):
            return jsonify({"answer": "👔 <strong>Manager's Note:</strong> I'm currently auditing the warehouse. Please wait 60 seconds before our next consultation."})
        return jsonify({"answer": "❌ Manager is currently unavailable. Please try again shortly."})
    
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

@reports_bp.route('/reports/clear_ai', methods=['POST'])
def clear_ai():
    # 'pop' removes the key from the session if it exists
    session.pop('ai_advice', None)
    return jsonify({"status": "cleared"})
