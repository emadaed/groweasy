#app/context_processors.py
from datetime import datetime, timedelta
import secrets
import base64
from flask import g, session
from app.services.cache import get_user_profile_cached


 # --- Global Context Processor ---
CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}

    
def register_context_processors(app):
    @app.context_processor
    def inject_currency():
        """Make currency available in all templates"""
        currency = 'PKR'
        symbol = 'Rs.'

        if 'user_id' in session:
            profile = get_user_profile_cached(session['user_id'])
            if profile:
                currency = profile.get('preferred_currency', 'PKR')
                symbol = CURRENCY_SYMBOLS.get(currency, 'Rs.')

        return dict(currency=currency, currency_symbol=symbol)

    @app.context_processor
    def inject_nonce():
        if not hasattr(g, 'nonce'):
            g.nonce = base64.b64encode(secrets.token_bytes(16)).decode('utf-8')
        return dict(nonce=g.nonce)

    @app.context_processor
    def utility_processor():
        """Add utility functions to all templates"""
        def now():
            return datetime.now()

        def today():
            return datetime.now().date()

        def month_equalto_filter(value, month):
            """Custom filter for month comparison - FIXED"""
            try:
                if hasattr(value, 'month'):
                    return value.month == month
                elif isinstance(value, str):
                    # Try to parse date string
                    from datetime import datetime as dt
                    # Handle different date formats
                    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                        try:
                            date_obj = dt.strptime(value, fmt)
                            return date_obj.month == month
                        except:
                            continue
                    return False
                elif hasattr(value, 'order_date'):
                    # Handle purchase order objects
                    return value.order_date.month == month if hasattr(value.order_date, 'month') else False
                return False
            except:
                return False

        return {
            'now': now,
            'today': today,
            'month_equalto': month_equalto_filter
        }
