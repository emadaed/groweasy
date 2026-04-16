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
from app.extensions import limiter, compress
from app.context_processors import register_context_processors
from app.services.cache import init_cache
from app.services.middleware import init_middleware
from config import Config
from flask import request, abort


def block_automation():
    BLOCKED_BOTS = ['sentry', 'uptime', 'bot', 'spider', 'crawler', 'python-requests']
    user_agent = request.headers.get('User-Agent', '').lower()
    if any(bot in user_agent for bot in BLOCKED_BOTS):
        if request.path.startswith('/reports/'):
            abort(403)


mail = Mail()


def create_app():
    load_dotenv()

    if os.getenv('SENTRY_DSN'):
        sentry_sdk.init(
            dsn=os.getenv('SENTRY_DSN'),
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0
        )

    app = Flask(__name__)
    app.config.from_object(Config)
    app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
    app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
    app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
    app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
    mail.init_app(app)

    csrf.init_app(app)

    app_root = Path(__file__).parent
    app.template_folder = str(app_root / "templates")
    app.static_folder = str(app_root / "static")

    init_cache(app)
    app.before_request(block_automation)

    if app.config['SESSION_TYPE'] == 'redis':
        import redis
        app.config['SESSION_REDIS'] = redis.from_url(app.config['REDIS_URL'])
    Session(app)

    limiter.init_app(app)
    compress.init_app(app)
    register_context_processors(app)
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
    from app.routes.users import users_bp
    app.register_blueprint(users_bp)
##    from app.routes.ai import ai_bp
##    app.register_blueprint(ai_bp)
    from app.routes.reports import reports_bp
    app.register_blueprint(reports_bp)
    from app.routes.api_v1 import api_v1_bp
    app.register_blueprint(api_v1_bp)

    # --- Logging Noise Reduction ---
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # --- Custom Jinja Filters ---
    @app.template_filter('escapejs')
    def escapejs_filter(s):
        if not s:
            return ""
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
