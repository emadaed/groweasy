# app/services/ai_service.py
from google import genai
from flask import current_app

def get_gemini_insights(data):
    """Feeds ERP data to Gemini 2.0 and returns professional advice"""
    try:
        # Initialize the new Client
        client = genai.Client(api_key=current_app.config['GEMINI_API_KEY'])
        
        prompt = f"""
        You are the Groweasy AI Business Consultant. Analyze this monthly data for a business owner:
        - Revenue: {data['revenue']}
        - Costs: {data['costs']}
        - Net Profit: {data['net_profit']}
        - Tax Liability: {data['tax_liability']}
        - Inventory Value: {data['inventory_value']}
        
        Provide 3 short, actionable bullet points to improve the business. 
        Focus on cash flow, tax management, and inventory.
        Keep the tone professional and brief.
        """
        
        # Use the latest model
        response = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=prompt
        )
        return response.text
    except Exception as e:
        current_app.logger.error(f"AI Service Error: {str(e)}")
        return "Gemini is currently analyzing your market data. Please check back in a moment!"
