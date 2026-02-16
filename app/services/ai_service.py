#app/services/ai_service.py
from google import genai
from flask import current_app

def get_gemini_insights(data, custom_prompt=None):
    """Feeds ERP data to Gemini 2.0 and returns professional advice"""
    try:
        client = genai.Client(api_key=current_app.config['GEMINI_API_KEY'])
        
        # Determine if we are doing a general summary or answering a specific question
        if custom_prompt:
            context = f"""
            Context: The business has {data.get('revenue', 0)} revenue, 
            {data.get('net_profit', 0)} profit, and {data.get('inventory_value', 0)} in stock.
            User Question: {custom_prompt}
            """
        else:
            context = f"""
            Analyze this monthly data for a business owner:
            - Revenue: {data.get('revenue', 0)}
            - Net Profit: {data.get('net_profit', 0)}
            - Tax Liability: {data.get('tax_liability', 0)}
            - Inventory Value: {data.get('inventory_value', 0)}
            
            Provide 3 short, actionable bullet points to improve the business. 
            Focus on cash flow, tax management, and inventory.
            """

        full_prompt = f"You are the Groweasy AI Business Consultant. {context} Keep the tone professional and brief."
        
        response = client.models.generate_content(
            model="gemini-2.0-flash", 
            contents=full_prompt
        )
        return response.text
    except Exception as e:
        current_app.logger.error(f"AI Service Error: {str(e)}")
        return "Gemini is currently analyzing your market data. Please check back in a moment!"
