#app/__init__.py
import time
import json
import base64
import os
import io
from pathlib import Path
from datetime import datetime, timedelta
import secrets
import logging

# Third-party
from sqlalchemy import text
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import Flask, render_template, request, g, send_file, session, redirect, url_for, send_from_directory, flash, jsonify, Response, make_response, current_app
from flask_compress import Compress
from flask_session import Session
from dotenv import load_dotenv
import redis
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
# Local application imports
from app.services.db import DB_ENGINE
from app.services.middleware import security_headers
from app.services.cache import init_cache, get_user_profile_cached
from app.services.purchases import save_purchase_order, get_purchase_orders
from app.services.suppliers import SupplierManager
# Global Limiter instance
limiter = Limiter(key_func=get_remote_address)

def create_app():
    load_dotenv()

    # --- Sentry Setup ---
    if os.getenv('SENTRY_DSN'):
        sentry_sdk.init(
            dsn=os.getenv('SENTRY_DSN'),
            integrations=[FlaskIntegration()],
            traces_sample_rate=1.0,
            environment='production' if os.getenv('RAILWAY_ENVIRONMENT') else 'development'
        )

    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY')

    # --- Path Configuration (Inside /app folder) ---
    app_root = Path(__file__).parent
    app.template_folder = str(app_root / "templates")
    app.static_folder = str(app_root / "static")

    # --- Extensions ---
    init_cache(app)
    setup_redis_sessions(app)
    
    # --- Rate Limiting ---
    storage_uri = os.getenv('REDIS_URL', 'memory://')
    if storage_uri and '://' not in storage_uri:
        if '@' in storage_uri:
            password, host = storage_uri.split('@', 1)
            storage_uri = f"redis://default:{password}@{host}:6379"
        else:
            storage_uri = f"redis://default:{storage_uri}@redis.railway.internal:6379"
    
    limiter.init_app(app)
    app.config["RATELIMIT_STORAGE_URI"] = storage_uri

    # --- Middleware ---
    Compress(app)
    security_headers(app)

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
##    from app.routes.common import common_bp
##    app.register_blueprint(common_bp)

    # --- Logging Noise Reduction ---
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    # --- Global Context Processor ---
    CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}

    #context processor
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

    
    @app.before_request
    def set_nonce():
        g.nonce = secrets.token_hex(16)

    return app

def setup_redis_sessions(app):
    REDIS_URL = os.getenv('REDIS_URL', '').strip()
    if not REDIS_URL or REDIS_URL == 'memory://':
        app.config.update(SESSION_TYPE='filesystem', SESSION_FILE_DIR='/tmp/flask_sessions')
        Session(app)
        return
    try:
        if '://' not in REDIS_URL:
            REDIS_URL = f"redis://default:{REDIS_URL}@redis.railway.internal:6379"
        redis_client = redis.from_url(REDIS_URL, socket_connect_timeout=5)
        app.config.update(
            SESSION_TYPE='redis',
            SESSION_REDIS=redis_client,
            SESSION_PERMANENT=True,
            SESSION_USE_SIGNER=True,
            SESSION_KEY_PREFIX='invoice_sess:',
            PERMANENT_SESSION_LIFETIME=86400
        )
        Session(app)
    except:
        app.config.update(SESSION_TYPE='filesystem', SESSION_FILE_DIR='/tmp/flask_sessions')
        Session(app)


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

# update stock
def update_stock_on_invoice(user_id, invoice_items, invoice_type='S', invoice_number=None):
    """Update stock with invoice reference number"""
    try:
        for item in invoice_items:
            if item.get('product_id'):
                product_id = item['product_id']
                quantity = int(item.get('qty', 1))

                with DB_ENGINE.connect() as conn:  # Changed to connect for read-only
                    result = conn.execute(text("""
                        SELECT current_stock FROM inventory_items
                        WHERE id = :product_id AND user_id = :user_id
                    """), {"product_id": product_id, "user_id": user_id}).fetchone()

                if result:
                    current_stock = result[0]

                    if invoice_type == 'P':
                        new_stock = current_stock + quantity
                        movement_type = 'purchase'
                        notes = f"Purchased {quantity} units via PO: {invoice_number}" if invoice_number else f"Purchased {quantity} units"
                    else:
                        new_stock = current_stock - quantity
                        movement_type = 'sale'
                        notes = f"Sold {quantity} units via Invoice: {invoice_number}" if invoice_number else f"Sold {quantity} units"

                    success = InventoryManager.update_stock_delta(
                        user_id, product_id, new_stock, movement_type, invoice_number, notes
                    )

                    if success:
                        print(f"✅ Stock updated: {item.get('name')} ({movement_type})")
                    else:
                        print(f"⚠️ Stock update failed for {item.get('name')}")

    except Exception as e:
        print(f"Stock update error: {e}")


# --- Helper functions (Outside the create_app function) ---

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
