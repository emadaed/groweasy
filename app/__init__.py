#app/__init__.py
import os
import logging
from pathlib import Path
from flask import Flask
from flask_session import Session
from dotenv import load_dotenv
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask_wtf.csrf import CSRFProtect

# Local Imports
from app.extensions import limiter, compress
from app.context_processors import register_context_processors
from app.services.cache import init_cache
from app.services.middleware import init_middleware
from config import Config
from flask import request, abort

# 1. Define the function FIRST
def block_automation():
    # List of known bots from your logs
    BLOCKED_BOTS = ['sentry', 'uptime', 'bot', 'spider', 'crawler', 'python-requests']
    user_agent = request.headers.get('User-Agent', '').lower()
    
    # Block bots from reaching the reports routes to save your Gemini limit
    if any(bot in user_agent for bot in BLOCKED_BOTS):
        if request.path.startswith('/reports/'):
            abort(403)

def create_app():
    load_dotenv()

    # --- Sentry ---
    if os.getenv('SENTRY_DSN'):
        sentry_sdk.init(
            dsn=os.getenv('SENTRY_DSN'),
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0
        )

    app = Flask(__name__)
    app.config.from_object(Config)


    # --- Security: Initialize CSRF Protection ---
    csrf = CSRFProtect(app)

    # Path Config
    app_root = Path(__file__).parent
    app.template_folder = str(app_root / "templates")
    app.static_folder = str(app_root / "static")

    # --- Initialize Extensions ---
    init_cache(app)    
    app.before_request(block_automation)
    
    # Simple Session Setup (Logic is now in Config)
    if app.config['SESSION_TYPE'] == 'redis':
        import redis
        app.config['SESSION_REDIS'] = redis.from_url(app.config['REDIS_URL'])
    Session(app)

    limiter.init_app(app)
    compress.init_app(app)
    register_context_processors(app)
    #security_headers(app)
    init_middleware(app)

    # --- Blueprints ---
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp)
    from app.routes.inventory import inventory_bp
    app.register_blueprint(inventory_bp)
    from app.routes.main import main_bp
    app.register_blueprint(main_bp)
    from app.routes.crm import crm_bp
    app.register_blueprint(crm_bp)
    from app.routes.finance import finance_bp
    app.register_blueprint(finance_bp)
    from app.routes.settings import settings_bp
    app.register_blueprint(settings_bp)
    from app.routes.purchases import purchases_bp
    app.register_blueprint(purchases_bp)
    from app.routes.sales import sales_bp
    app.register_blueprint(sales_bp)
    from app.routes.api import api_bp
    app.register_blueprint(api_bp)
    from app.routes.suppliers import suppliers_bp
    app.register_blueprint(suppliers_bp)
    from app.routes.ai import ai_bp
    app.register_blueprint(ai_bp)
    from app.routes.reports import reports_bp
    app.register_blueprint(reports_bp)
##    from app.routes.common import common_bp
##    app.register_blueprint(common_bp)

    # --- Logging Noise Reduction ---
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

   
    # --- Custom Jinja Filters ---
    @app.template_filter('escapejs')
    def escapejs_filter(s):
        if not s:
            return ""
        # Simple escape logic for JS strings
        return (str(s).replace('\\', '\\\\')
                .replace("'", "\\'")
                .replace('"', '\\"')
                .replace('\n', '\\n')
                .replace('\r', '\\r'))
    
    
    return app

# STOCK VALIDATION
def validate_stock_availability(user_id, invoice_items, invoice_type='S'):
    """Validate stock availability BEFORE invoice processing"""
    if invoice_type == 'P':  # Purchase order - NO validation needed
        return {'success': True, 'message': 'Purchase order - no stock check needed'}
    try:
        with DB_ENGINE.begin() as conn:
            for item in invoice_items:
                if item.get('product_id'):
                    product_id = item['product_id']
                    requested_qty = int(item.get('qty', 1))

                    result = conn.execute(text("""
                        SELECT name, current_stock
                        FROM inventory_items
                        WHERE id = :product_id AND user_id = :user_id
                    """), {"product_id": product_id, "user_id": user_id}).fetchone()

                    if not result:
                        return {'success': False, 'message': "Product not found in inventory"}

                    product_name, current_stock = result
                    if current_stock < requested_qty:
                        return {
                            'success': False,
                            'message': f"Only {current_stock} units available for '{product_name}'"
                        }

            return {'success': True, 'message': 'Stock available'}

    except Exception as e:
        print(f"Stock validation error: {e}")
        return {'success': False, 'message': 'Stock validation failed'}


# --- Helper functions (Outside the create_app function) ---move it app/utils/qr.py

def generate_simple_qr(data):
    """Generate a simple QR code for document data"""
    try:
        import qrcode
        from io import BytesIO
        import base64
        import json

        qr_data = {
            'doc_number': data.get('invoice_number', ''),
            'date': data.get('invoice_date', ''),
            'total': data.get('grand_total', 0)
        }

        qr = qrcode.QRCode(version=1, box_size=5, border=2)
        qr.add_data(json.dumps(qr_data))
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")

        return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        print(f"QR generation error: {e}")
        return None

def clear_pending_invoice(user_id):
    """Clear pending invoice data"""
    try:
        from app.services.session_storage import SessionStorage
        SessionStorage.clear_data(user_id, 'last_invoice')
        return True
    except Exception as e:
        print(f"Error clearing pending invoice: {e}")
        return False

def template_exists(template_name):
    """Check if a template exists using current_app to avoid NameError"""
    try:
        from flask import current_app
        current_app.jinja_env.get_template(template_name)
        return True
    except Exception:
        return False
