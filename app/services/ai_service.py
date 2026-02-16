# app/services/ai_service.py
import google.generativeai as genai
from flask import current_app

def get_gemini_insights(data):
    """Feeds ERP data to Gemini and returns professional advice"""
    try:
        # Configure the API (Key stored in Config)
        genai.configure(api_key=current_app.config['GEMINI_API_KEY'])
        model = genai.GenerativeModel('gemini-pro')
        
        prompt = f"""
        You are the Groweasy AI Business Consultant. Analyze this monthly data for a business owner:
        - Revenue: {data['revenue']}
        - Costs: {data['costs']}
        - Net Profit: {data['net_profit']}
        - Tax Liability: {data['tax_liability']}
        - Inventory Value: {data['inventory_value']}
        
        Provide 3 short, actionable bullet points to improve the business. 
        Keep the tone professional, encouraging, and brief.
        """
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return "Gemini is resting right now. Check back in a moment for your insights!"
