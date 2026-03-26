#app/__init__.py
import os
import logging
from pathlib import Path
from flask import Flask
from flask_session import Session
from dotenv import load_dotenv
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from app.extensions import csrf
from flask_mail import Mail
# Local Imports
#from app.assets import init_assets
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

mail = Mail()
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
    app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com') #  SMTP server
    app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
    app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
    mail.init_app(app)       
    
    # --- Security: Initialize CSRF Protection ---
    csrf.init_app(app)

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
    ##register_commands(app)
    init_middleware(app)
    ##init_assets(app)

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
    from app.routes.users import users_bp
    app.register_blueprint(users_bp)
##    from app.routes.ai import ai_bp
##    app.register_blueprint(ai_bp)
    from app.routes.reports import reports_bp
    app.register_blueprint(reports_bp)
    from app.routes.api_v1 import api_v1_bp
    app.register_blueprint(api_v1_bp)
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
    
    from flasgger import Swagger
    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": 'apispec_1',
                "route": '/apispec_1.json',
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/apidocs/",
        "title": "Groweasy API",
        "description": "API for Groweasy ERP system",
        "version": "1.0.0",
        "termsOfService": "",
        "contact": {},
        "license": {},
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "Enter your API key in the format: Bearer <key>"
            }
        },
        "definitions": {
            "InventoryItem": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "sku": {"type": "string"},
                    "current_stock": {"type": "number"},
                    "cost_price": {"type": "number"},
                    "selling_price": {"type": "number"},
                    "category": {"type": "string"},
                    "supplier": {"type": "string"},
                    "location": {"type": "string"}
                }
            },
            "Customer": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "address": {"type": "string"},
                    "tax_id": {"type": "string"},
                    "total_spent": {"type": "number"},
                    "invoice_count": {"type": "integer"}
                }
            },
            "Invoice": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "invoice_number": {"type": "string"},
                    "client_name": {"type": "string"},
                    "invoice_date": {"type": "string", "format": "date"},
                    "due_date": {"type": "string", "format": "date"},
                    "grand_total": {"type": "number"},
                    "status": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            }
        }
    }

    swagger = Swagger(app, config=swagger_config)
    return app

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
