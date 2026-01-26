
# ============================================================================
# app.py - COMPLETE FIXED VERSION 19--01-2026 07:25 AM
# ============================================================================
import time
import json
import base64
import os
import io
from pathlib import Path
from datetime import datetime, timedelta
import secrets

# Third-party
from sqlalchemy import text
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask import Flask, render_template, request, g, send_file, session, redirect, url_for, send_from_directory, flash, jsonify, Response, make_response, current_app
from flask_compress import Compress
from flask_session import Session
from dotenv import load_dotenv
import redis
# Local application
from fbr_integration import FBRInvoice
from core.inventory import InventoryManager
from core.invoice_logic import prepare_invoice_data
from core.invoice_logic_po import prepare_po_data
from core.qr_engine import generate_qr_base64
from core.pdf_engine import generate_pdf, HAS_WEASYPRINT
from core.auth import create_user, verify_user, get_user_profile, update_user_profile, change_user_password, save_user_invoice
from core.purchases import save_purchase_order, get_purchase_orders, get_suppliers
from core.middleware import security_headers
from core.db import DB_ENGINE
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# Environment setup
load_dotenv()

# Initialize Sentry for error monitoring
if os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=os.getenv('SENTRY_DSN'),
        integrations=[FlaskIntegration()],
        traces_sample_rate=1.0,  # Capture all for MVP monitoring
        environment='production' if os.getenv('RAILWAY_ENVIRONMENT') else 'development',
        send_default_pii=True
    )
    # Breadcrumbs for invoices (example — add more as needed)
    sentry_sdk.add_breadcrumb(category="invoice", message="app_started", level="info")
    print("✅ Sentry monitoring enabled")

# Fun success messages
SUCCESS_MESSAGES = {
    'invoice_created': [
        "🎉 Invoice created! You're a billing boss!",
        "💰 Cha-ching! Another invoice done!",
        "✨ Invoice magic complete!",
        "🚀 Invoice sent to the moon!",
        "🎊 You're on fire! Invoice created!"
    ],
    'stock_updated': [
        "📦 Stock updated! Inventory ninja at work!",
        "✅ Stock levels looking good!",
        "🎯 Bullseye! Stock updated perfectly!",
        "💪 Stock management on point!"
    ],
    'login': [
        "🎉 Welcome back, superstar!",
        "👋 Great to see you again!",
        "✨ You're logged in! Let's make money!",
        "🚀 Ready to conquer the day?"
    ],
    'product_added': [
        "📦 Product added! Your inventory grows!",
        "✨ New product in the house!",
        "🎉 Inventory expanded successfully!",
        "💪 Another product conquered!"
    ]
}

def random_success_message(category='default'):
    import random
    messages = SUCCESS_MESSAGES.get(category, SUCCESS_MESSAGES['invoice_created'])
    return random.choice(messages)

# App creation
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
# Fix template/static path for Railway
app_root = Path(__file__).parent
app.template_folder = str(app_root / "templates")
app.static_folder = str(app_root / "static")
print(f"✅ Templates folder: {app.template_folder}")
print(f"✅ Static folder: {app.static_folder}")

from core.cache import init_cache, get_user_profile_cached
init_cache(app)

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Redis session configuration
def setup_redis_sessions(app):
    """Configure Redis-based sessions with proper fallback"""
    REDIS_URL = os.getenv('REDIS_URL', '').strip()

    # Validate Redis URL
    if not REDIS_URL or REDIS_URL == 'memory://':
        print("⚠️ No Redis URL provided, using filesystem sessions")
        app.config.update(
            SESSION_TYPE='filesystem',
            SESSION_FILE_DIR='/tmp/flask_sessions',
            SESSION_FILE_THRESHOLD=100,
            SESSION_PERMANENT=True,
            PERMANENT_SESSION_LIFETIME=86400,
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax'
        )
        Session(app)
        return

    # Fix common Railway Redis URL issues
    # Railway provides passwords without full URL sometimes
    if '://' not in REDIS_URL:
        # Try to construct proper URL
        if '@' in REDIS_URL:
            # Looks like password@host format
            password, host = REDIS_URL.split('@', 1)
            REDIS_URL = f"redis://default:{password}@{host}:6379"
        else:
            # Just a password, use default Railway Redis
            REDIS_URL = f"redis://default:{REDIS_URL}@redis.railway.internal:6379"
        print(f"🔧 Fixed Redis URL: {REDIS_URL.split('@')[0]}@...")

    # Validate Redis URL format
    if not REDIS_URL.startswith(('redis://', 'rediss://', 'unix://')):
        print(f"⚠️ Invalid Redis URL format: {REDIS_URL[:50]}...")
        print("⚠️ Using filesystem sessions as fallback")
        app.config.update(
            SESSION_TYPE='filesystem',
            SESSION_FILE_DIR='/tmp/flask_sessions',
            SESSION_FILE_THRESHOLD=100
        )
        Session(app)
        return

    try:
        # Test Redis connection
        redis_client = redis.from_url(REDIS_URL, socket_connect_timeout=5, socket_keepalive=True)
        redis_client.ping()
        print(f"✅ Redis connected: {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")

        app.config.update(
            SESSION_TYPE='redis',
            SESSION_REDIS=redis_client,
            SESSION_PERMANENT=True,
            SESSION_USE_SIGNER=True,
            SESSION_KEY_PREFIX='invoice_sess:',
            PERMANENT_SESSION_LIFETIME=86400,
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax'
        )

        Session(app)
        print("✅ Redis sessions configured")

    except Exception as e:
        print(f"⚠️ Redis connection failed: {e}")
        app.config.update(
            SESSION_TYPE='filesystem',
            SESSION_FILE_DIR='/tmp/flask_sessions',
            SESSION_FILE_THRESHOLD=100
        )
        Session(app)
        print("✅ Fallback to filesystem sessions")

# Setup Redis sessions
setup_redis_sessions(app)

# Rate Limiting
REDIS_URL = os.getenv('REDIS_URL', 'memory://')
app.config['REDIS_URL'] = REDIS_URL

# Fix Redis URL for rate limiting
if REDIS_URL and '://' not in REDIS_URL:
    if '@' in REDIS_URL:
        password, host = REDIS_URL.split('@', 1)
        storage_uri = f"redis://default:{password}@{host}:6379"
    else:
        storage_uri = f"redis://default:{REDIS_URL}@redis.railway.internal:6379"
else:
    storage_uri = REDIS_URL if REDIS_URL else 'memory://'

# If memory storage or invalid URL, use memory
if not storage_uri or storage_uri == 'memory://' or not storage_uri.startswith(('redis://', 'rediss://')):
    storage_uri = 'memory://'
    print("⚠️ Using memory storage for rate limiting")

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=storage_uri,
    strategy="fixed-window",
    on_breach=lambda req_limit: print(f"Rate limit exceeded: {req_limit}")
)

# Middleware
Compress(app)
# Exclude PDFs from compression to prevent corruption
app.config['COMPRESS_MIMETYPES'] = [
    'text/html',
    'text/css',
    'text/xml',
    'application/json',
    'application/javascript'
]
security_headers(app)

# REDUCE LOG NOISE
import logging
# Set root logger to INFO (your own prints stay)
logging.getLogger().setLevel(logging.WARNING)
# Silence the very noisy third-party libraries
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('weasyprint').setLevel(logging.ERROR)
logging.getLogger('fontTools').setLevel(logging.ERROR)
logging.getLogger('PIL').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)

# Initialize purchase tables
from core.purchases import init_purchase_tables
try:
    init_purchase_tables()
    print("✅ Purchase tables initialized successfully")
except Exception as e:
    print(f"⚠️ Warning: Could not initialize purchase tables: {e}")
    if os.getenv('SENTRY_DSN'):
        sentry_sdk.capture_exception(e)

# Currency symbols
CURRENCY_SYMBOLS = {
    'PKR': 'Rs.',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'AED': 'د.إ',
    'SAR': '﷼'
}

# Helper functions
def generate_simple_qr(data):
    """Generate a simple QR code for document data"""
    try:
        import qrcode
        from io import BytesIO
        import base64

        # Create minimal data for QR
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
        # This function should be in services module
        # For now, implementing a simple version
        from core.session_storage import SessionStorage
        SessionStorage.clear_data(user_id, 'last_invoice')
        print(f"Cleared pending invoice for user {user_id}")
        return True
    except Exception as e:
        print(f"Error clearing pending invoice: {e}")
        return False

def template_exists(template_name):
    """Check if a template exists"""
    try:
        app.jinja_env.get_template(template_name)
        return True
    except Exception:
        return False

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

from flask import g
import secrets

@app.before_request
def before_request():
    """Set nonce for CSP"""
    g.nonce = secrets.token_hex(16)

# password reset
@app.route("/forgot_password", methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def forgot_password():
    """Simple password reset request with email simulation"""
    if request.method == 'POST':
        email = request.form.get('email')
        # Check if email exists in database
        with DB_ENGINE.connect() as conn:  # Read-only
            result = conn.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email}).fetchone()

        if result:
            flash('📧 Password reset instructions have been sent to your email.', 'success')
            flash('🔐 Development Note: In production, you would receive an email with reset link.', 'info')
            return render_template('reset_instructions.html', email=email, nonce=g.nonce)
        else:
            flash('❌ No account found with this email address.', 'error')
    return render_template('forgot_password.html', nonce=g.nonce)

#PW token
@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_password(token):
    """Password reset page (placeholder)"""
    # In production, you'd verify the token
    flash('Password reset functionality coming soon!', 'info')
    return redirect(url_for('login'))

# home
@app.route('/')
def home():
    """Home page - redirect to login or dashboard"""
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    else:
        return redirect(url_for('login'))

# create invoice
@app.route('/create_invoice')
def create_invoice():
    """Dedicated route for creating sales invoices ONLY"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    prefill_data = {}
    user_profile = get_user_profile_cached(session['user_id'])

    if user_profile:
        prefill_data = {
            'company_name': user_profile.get('company_name', ''),
            'company_address': user_profile.get('company_address', ''),
            'company_phone': user_profile.get('company_phone', ''),
            'company_email': user_profile.get('email', ''),
            'company_tax_id': user_profile.get('company_tax_id', ''),
            'seller_ntn': user_profile.get('seller_ntn', ''),
            'seller_strn': user_profile.get('seller_strn', ''),
        }

    return render_template('form.html',
                         prefill_data=prefill_data,
                         nonce=g.nonce)

#create po
@app.route("/create_purchase_order")
def create_purchase_order():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    from core.inventory import InventoryManager

    # Get inventory items for dropdown/modal
    inventory_items = InventoryManager.get_inventory_items(user_id)

    # Get suppliers (adjust if you have a supplier manager)
    suppliers = get_suppliers(user_id)

    # Today date
    today_str = datetime.today().strftime('%Y-%m-%d')

    return render_template("create_po.html",
                         inventory_items=inventory_items,
                         suppliers=suppliers,
                         today=today_str,
                         nonce=g.nonce)

#create po process
@app.route('/create_po_process', methods=['POST'])
@limiter.limit("10 per minute")
def create_po_process():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    try:
        from core.invoice_service import InvoiceService

        print("DEBUG: Starting PO creation for user", user_id)
        print("DEBUG: Form keys:", list(request.form.keys()))
        print("DEBUG: File keys:", list(request.files.keys()))

        service = InvoiceService(user_id)

        print("DEBUG: Calling service.create_purchase_order...")
        po_data, errors = service.create_purchase_order(request.form, request.files)

        print("DEBUG: Service returned po_data keys:", list(po_data.keys()) if po_data else "None")
        print("DEBUG: Service returned items count:", len(po_data.get('items', [])) if po_data else 0)
        print("DEBUG: Service returned errors:", errors)

        if errors:
            for error in errors:
                flash(f"❌ {error}", "error")
                print("DEBUG: Flashed error:", error)
            return redirect(url_for('create_purchase_order'))

        if po_data:
            print("DEBUG: PO data before save:", po_data)
            print("DEBUG: Items in po_data:", po_data.get('items', []))

            from core.session_storage import SessionStorage
            session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
            session['last_po_ref'] = session_ref

            flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
            print("DEBUG: Redirecting to preview for", po_data['po_number'])
            return redirect(url_for('po_preview', po_number=po_data['po_number']))

        flash("❌ Failed to create purchase order", "error")
        print("DEBUG: Failed - no po_data")
        return redirect(url_for('create_purchase_order'))

    except Exception as e:
        current_app.logger.error(f"PO creation error: {str(e)}", exc_info=True)
        print("DEBUG: Exception in PO creation:", str(e))
        flash("❌ An unexpected error occurred", "error")
        return redirect(url_for('create_purchase_order'))

# po preview
@app.route('/po/preview/<po_number>')
def po_preview(po_number):
    """Final Preview & Print - with full product enrichment"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"user_id": user_id, "po_number": po_number}).fetchone()

        if not result:
            flash("Purchase order not found", "error")
            return redirect(url_for('purchase_orders'))

        po_data = json.loads(result[0])

        po_data['po_number'] = po_number
        po_data['invoice_number'] = po_number

        # === FULL ENRICHMENT ===
        from core.inventory import InventoryManager
        inventory_items = InventoryManager.get_inventory_items(user_id)

        product_lookup = {str(p['id']): p for p in inventory_items}
        product_lookup.update({int(k): v for k, v in product_lookup.items() if k.isdigit()})

        for item in po_data.get('items', []):
            pid = item.get('product_id')
            if pid and pid in product_lookup:
                p = product_lookup[pid]
                item['sku'] = p.get('sku', 'N/A')
                item['name'] = p.get('name', item.get('name', 'Unknown'))
                item['supplier'] = p.get('supplier', po_data.get('supplier_name', 'Unknown Supplier'))

        # DEBUG LOGS
        print("✅ PO PREVIEW ENRICHED DATA:")
        for i, item in enumerate(po_data.get('items', []), 1):
            print(f"  Item {i}: name='{item.get('name')}', sku='{item.get('sku')}', supplier='{item.get('supplier')}'")

        qr_b64 = generate_simple_qr(po_data)

        # Use same enriched data for both preview and PDF
        html = render_template('purchase_order_pdf.html',
                               data=po_data,
                               preview=True,
                               custom_qr_b64=qr_b64,
                               currency_symbol=g.get('currency_symbol', 'Rs.'))

        return render_template('po_preview.html',
                               html=html,
                               data=po_data,
                               po_number=po_number,
                               nonce=g.nonce)

    except Exception as e:
        current_app.logger.error(f"PO preview error: {str(e)}", exc_info=True)
        flash("Error loading purchase order", "error")
        return redirect(url_for('purchase_orders'))

# GRN - Goods Received Note (Receive Purchase Order)
@app.route("/po/mark_received/<po_number>", methods=['GET', 'POST'])
def mark_po_received(po_number):
    """Handle receiving goods for an existing Purchase Order"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    try:
        from core.purchases import get_purchase_order
        from core.inventory import InventoryManager

        # Load the existing PO data
        po_data = get_purchase_order(user_id, po_number)
        if not po_data:
            flash("❌ Purchase Order not found", "error")
            return redirect(url_for('purchase_orders'))

        # Prevent double-receiving
        if po_data.get('status', '').lower() == 'received':
            flash("⚠️ This Purchase Order has already been received", "warning")
            return redirect(url_for('purchase_orders'))

        # GET request → Show confirmation page
        if request.method == 'GET':
            return render_template("po_receive_confirm.html",
                                   po_data=po_data,
                                   po_number=po_number,
                                   nonce=g.nonce)

        # POST request → User confirmed "Yes, Receive Goods"
        if request.method == 'POST':
            added_units = 0

            # Step 1: Add items to inventory stock
            for item in po_data.get('items', []):
                if item.get('product_id'):
                    qty = int(item.get('qty', 0))
                    if qty > 0:
                        if InventoryManager.update_stock_delta(
                            user_id,
                            item['product_id'],
                            qty,  # positive = increase stock
                            'purchase_receive',
                            po_number,
                            f"Goods received via PO {po_number}"
                        ):
                            added_units += qty

            # Step 2: Update status only — updated_at will be set automatically to NOW()
            try:
                with DB_ENGINE.begin() as conn:
                    conn.execute(text("""
                        UPDATE purchase_orders
                        SET status = 'Received'
                        WHERE user_id = :user_id
                          AND po_number = :po_number
                    """), {"user_id": user_id, "po_number": po_number})

                flash(f"✅ PO {po_number} successfully marked as Received! "
                      f"{added_units} units added to stock.", "success")
            except Exception as e:
                current_app.logger.error(f"Error updating PO status: {e}")
                flash("⚠️ Stock added, but status update failed. Please contact support.", "warning")

            return redirect(url_for('purchase_orders'))

    except Exception as e:
        current_app.logger.error(f"Error receiving PO {po_number}: {e}", exc_info=True)
        flash("❌ An error occurred while receiving goods. Please try again.", "error")
        return redirect(url_for('purchase_orders'))

# Email to supplier
@app.route('/po/email/<po_number>', methods=['POST'])
def email_po_to_supplier(po_number):
    """Send PO to supplier via email"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # TODO: Implement email sending
    flash(f'PO {po_number} email functionality coming soon!', 'info')
    return jsonify({'success': True, 'message': 'Email queued'})



# Print preview
@app.route('/po/print/<po_number>')
def print_po_preview(po_number):
    """Print preview for PO"""
    return redirect(url_for('po_preview', po_number=po_number))

# po api
@app.route('/api/purchase_order/<po_number>/complete', methods=['POST'])
def complete_purchase_order(po_number):
    """Mark PO as completed - API endpoint"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                UPDATE purchase_orders
                SET status = 'completed'
                WHERE user_id = :user_id AND po_number = :po_number
            """), {"user_id": session['user_id'], "po_number": po_number})

        return jsonify({'success': True, 'message': f'PO {po_number} marked as completed'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# PO cancel endpoint
@app.route('/api/purchase_order/<po_number>/cancel', methods=['POST'])
def cancel_purchase_order(po_number):
    """Cancel purchase order"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        data = request.get_json()
        reason = data.get('reason', 'No reason provided')

        with DB_ENGINE.begin() as conn:
            # Update order data with cancellation
            result = conn.execute(text("""
                SELECT order_data FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()

            if result:
                order_data = json.loads(result[0])
                order_data['cancellation_reason'] = reason
                order_data['cancelled_at'] = datetime.now().isoformat()

                conn.execute(text("""
                    UPDATE purchase_orders
                    SET status = 'cancelled', order_data = :order_data
                    WHERE user_id = :user_id AND po_number = :po_number
                """), {
                    "user_id": session['user_id'],
                    "po_number": po_number,
                    "order_data": json.dumps(order_data)
                })

        return jsonify({'success': True, 'message': f'PO {po_number} cancelled'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/debug')
def debug():
    """Debug route to check what's working"""
    debug_info = {
        'session': dict(session),
        'routes': [str(rule) for rule in app.url_map.iter_rules()],
        'user_authenticated': bool(session.get('user_id'))
    }
    return jsonify(debug_info)

# INVENTORY
@app.route("/inventory")
def inventory():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, sku, category, current_stock, min_stock_level,
                   cost_price, selling_price, supplier, location
            FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE
            ORDER BY name
        """), {"user_id": user_id}).fetchall()

    inventory_items = [dict(row._mapping) for row in items]

    low_stock_alerts = InventoryManager.get_low_stock_alerts(user_id)

    return render_template("inventory.html",
                         inventory_items=inventory_items,
                         low_stock_alerts=low_stock_alerts,
                         nonce=g.nonce)

# inventory reports - SIMPLIFIED TO AVOID ERRORS
@app.route("/inventory_reports")
def inventory_reports():
    """Inventory analytics and reports dashboard - SIMPLIFIED"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        from core.reports import InventoryReports
        # Try to get reports, but don't crash if they fail
        bcg_matrix = []
        turnover = []
        profitability = []
        slow_movers = []

        try:
            bcg_matrix = InventoryReports.get_bcg_matrix(session['user_id'])
        except:
            pass

        try:
            turnover = InventoryReports.get_stock_turnover(session['user_id'], days=30)
        except:
            pass

        try:
            profitability = InventoryReports.get_profitability_analysis(session['user_id'])
        except:
            pass

        try:
            slow_movers = InventoryReports.get_slow_movers(session['user_id'], days_threshold=90)
        except:
            pass

        return render_template("inventory_reports.html",
                             bcg_matrix=bcg_matrix,
                             turnover=turnover[:10],  # Top 10
                             profitability=profitability[:10],  # Top 10
                             slow_movers=slow_movers,
                             nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Inventory reports error: {e}")
        flash("Reports temporarily unavailable", "info")
        return redirect(url_for('inventory'))

@app.route("/add_product", methods=['POST'])
def add_product():
    """Add new product to inventory"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.inventory import InventoryManager

    product_data = {
        'name': request.form.get('name'),
        'sku': request.form.get('sku'),
        'category': request.form.get('category'),
        'description': request.form.get('description'),
        'current_stock': int(request.form.get('current_stock', 0)),
        'min_stock_level': int(request.form.get('min_stock_level', 5)),
        'cost_price': float(request.form.get('cost_price', 0)),
        'selling_price': float(request.form.get('selling_price', 0)),
        'supplier': request.form.get('supplier'),
        'location': request.form.get('location')
    }

    product_id = InventoryManager.add_product(session['user_id'], product_data)

    if product_id:
        # If initial stock > 0, use delta to log it (already logged in add_product, but safe)
        initial_stock = int(product_data.get('current_stock', 0))
        if initial_stock > 0:
            InventoryManager.update_stock_delta(
                session['user_id'],
                product_id,
                initial_stock,
                'initial',
                notes='Initial stock on product creation'
            )
        flash(random_success_message('product_added'), 'success')
    else:
        flash('Error adding product. SKU might already exist.', 'error')

    return redirect(url_for('inventory'))

#delete
@app.route("/delete_product", methods=['POST'])
def delete_product():
    """Remove product from inventory with audit trail"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.inventory import InventoryManager

    product_id = request.form.get('product_id')
    reason = request.form.get('reason')
    notes = request.form.get('notes', '')

    full_reason = f"{reason}. {notes}".strip()

    success = InventoryManager.delete_product(session['user_id'], product_id, full_reason)

    if success:
        flash('✅ Product removed successfully', 'success')
    else:
        flash('❌ Error removing product', 'error')

    return redirect(url_for('inventory'))

# API inventory items
@app.route("/api/inventory_items")
def get_inventory_items_api():
    """API endpoint for inventory items (for invoice form)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401

    with DB_ENGINE.connect() as conn:
        items = conn.execute(text("""
            SELECT id, name, selling_price, current_stock
            FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE AND current_stock > 0
            ORDER BY name
        """), {"user_id": session['user_id']}).fetchall()

    inventory_data = [{
        'id': item[0],
        'name': item[1],
        'price': float(item[2]) if item[2] else 0,
        'stock': item[3]
    } for item in items]

    return jsonify(inventory_data)

# stock adjustment - FINAL WORKING VERSION
@app.route("/adjust_stock_audit", methods=['POST'])
@limiter.limit("10 per minute")
def adjust_stock_audit():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    product_id = request.form.get('product_id')
    adjustment_type = request.form.get('adjustment_type')
    quantity = int(request.form.get('quantity', 0))
    new_cost_price = request.form.get('new_cost_price')
    new_selling_price = request.form.get('new_selling_price')
    reason = request.form.get('reason', 'Stock adjustment')
    notes = request.form.get('notes', '')

    try:
        from core.inventory import InventoryManager
        from flask import current_app as app  # ← Fix logger

        # Get product - use your existing method
        product = InventoryManager.get_product_details(user_id, product_id)
        if not product:
            flash('❌ Product not found', 'error')
            return redirect(url_for('inventory'))

        current_stock = product['current_stock']
        product_name = product['name']

        # Calculate delta
        if adjustment_type == 'add_stock':
            delta = +quantity
            movement_type = 'stock_in'
        elif adjustment_type == 'remove_stock':
            delta = -quantity
            movement_type = 'stock_out'
        elif adjustment_type == 'damaged':
            delta = -quantity
            movement_type = 'damaged'
        elif adjustment_type == 'found_stock':
            delta = +quantity
            movement_type = 'found'
        elif adjustment_type == 'set_stock':
            delta = quantity - current_stock
            movement_type = 'adjustment'
        else:
            flash('❌ Invalid adjustment type', 'error')
            return redirect(url_for('inventory'))

        # Update stock using delta
        success = InventoryManager.update_stock_delta(
            user_id=user_id,
            product_id=product_id,
            quantity_delta=delta,
            movement_type=movement_type,
            reference_id=f"ADJ-{int(time.time())}",
            notes=f"{reason}: {notes}".strip()
        )

        # Update prices
        if success and (new_cost_price or new_selling_price):
            updates = {}
            if new_cost_price and new_cost_price.strip():
                updates['cost_price'] = float(new_cost_price)
            if new_selling_price and new_selling_price.strip():
                updates['selling_price'] = float(new_selling_price)

            if updates:
                with DB_ENGINE.begin() as conn:
                    set_clause = ', '.join(f"{k} = :{k}" for k in updates)
                    params = updates.copy()
                    params.update({"product_id": product_id, "user_id": user_id})
                    conn.execute(text(f"UPDATE inventory_items SET {set_clause} WHERE id = :product_id AND user_id = :user_id"), params)

        if success:
            new_stock = current_stock + delta
            flash(f'✅ {product_name} adjusted! Stock: {current_stock} → {new_stock}', 'success')
        else:
            flash('❌ Failed to update stock (negative not allowed)', 'error')

        return redirect(url_for('inventory'))

    except Exception as e:
        app.logger.error(f"Stock adjustment error: {e}", exc_info=True)
        flash('❌ Error updating product', 'error')
        return redirect(url_for('inventory'))

# inventory report
@app.route("/download_inventory_report")
def download_inventory_report():
    """Download inventory as CSV"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.inventory import InventoryManager
    import csv
    import io

    inventory_data = InventoryManager.get_inventory_report(session['user_id'])

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(['Product Name', 'SKU', 'Category', 'Current Stock', 'Min Stock',
                    'Cost Price', 'Selling Price', 'Supplier', 'Location'])

    # Write data
    for item in inventory_data:
        writer.writerow([
            item['name'], item['sku'], item['category'], item['current_stock'],
            item['min_stock'], item['cost_price'], item['selling_price'],
            item['supplier'], item['location']
        ])

    # Return CSV file
    output.seek(0)
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=inventory_report.csv"}
    )

#SETTINGS
@app.route("/settings", methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.auth import get_user_profile, update_user_profile, change_user_password, verify_user

    user_profile = get_user_profile_cached(session['user_id'])

    if request.method == 'POST':
        # Handle profile update
        if 'update_profile' in request.form:
            company_name = request.form.get('company_name')
            company_address = request.form.get('company_address')
            company_phone = request.form.get('company_phone')
            company_tax_id = request.form.get('company_tax_id')
            seller_ntn = request.form.get('seller_ntn')  # 🆕 FBR field
            seller_strn = request.form.get('seller_strn')  # 🆕 FBR field
            preferred_currency = request.form.get('preferred_currency')

            update_user_profile(
                session['user_id'],
                company_name=company_name,
                company_address=company_address,
                company_phone=company_phone,
                company_tax_id=company_tax_id,
                seller_ntn=seller_ntn,  # 🆕 Pass to function
                seller_strn=seller_strn,  # 🆕 Pass to function
                preferred_currency=preferred_currency
            )

            flash('Settings updated successfully!', 'success')
            response = make_response(redirect(url_for('settings')))
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response

        # Handle password change
        elif 'change_password' in request.form:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            # Verify current password
            if not verify_user(user_profile['email'], current_password):
                flash('Current password is incorrect', 'error')
            elif new_password != confirm_password:
                flash('New passwords do not match', 'error')
            elif len(new_password) < 6:
                flash('New password must be at least 6 characters', 'error')
            else:
                change_user_password(session['user_id'], new_password)
                flash('Password changed successfully!', 'success')

            return redirect(url_for('settings'))

    return render_template("settings.html", user_profile=user_profile, nonce=g.nonce)

#device management
@app.route("/devices")
def devices():
    """Manage active devices"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.session_manager import SessionManager
    active_sessions = SessionManager.get_active_sessions(session['user_id'])

    return render_template("devices.html",
                         sessions=active_sessions,
                         current_token=session.get('session_token'),
                         nonce=g.nonce)

# revoke tokens
@app.route("/revoke_device/<token>")
def revoke_device(token):
    """Revoke specific device session"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.session_manager import SessionManager

    # Don't allow revoking current session
    if token == session.get('session_token'):
        flash('❌ Cannot revoke current session', 'error')
    else:
        SessionManager.revoke_session(token)
        flash('✅ Device session revoked', 'success')

    return redirect(url_for('devices'))

# revoke devices
@app.route("/revoke_all_devices")
def revoke_all_devices():
    """Revoke all other sessions"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.session_manager import SessionManager
    SessionManager.revoke_all_sessions(session['user_id'], except_token=session.get('session_token'))

    flash('✅ All other devices logged out', 'success')
    return redirect(url_for('devices'))

# Login
@app.route("/login", methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user_id = verify_user(email, password)
        if user_id:
            from core.session_manager import SessionManager

            # Check location restrictions
            if not SessionManager.check_location_restrictions(user_id, request.remote_addr):
                flash('❌ Login not allowed from this location', 'error')
                return render_template('login.html', nonce=g.nonce)

            # Create secure session
            session_token = SessionManager.create_session(user_id, request)

            session['user_id'] = user_id
            session['user_email'] = email
            session['session_token'] = session_token

            flash(random_success_message('login'), 'success')
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials', nonce=g.nonce)

    # GET request - show login form
    return render_template('login.html', nonce=g.nonce)

# leagal pages
@app.route("/terms")
def terms():
    return render_template("terms.html", nonce=g.nonce)

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", nonce=g.nonce)

@app.route("/about")
def about():
    return render_template("about.html", nonce=g.nonce)

# register
@app.route("/register", methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def register():
    if request.method == 'POST':
        # Validate terms acceptance
        if not request.form.get('agree_terms'):
            flash('❌ You must agree to Terms of Service to register', 'error')
            return render_template('register.html', nonce=g.nonce)

        email = request.form.get('email')
        password = request.form.get('password')
        company_name = request.form.get('company_name', '')

        # 🆕 ADD DEBUG LOGGING
        print(f"📝 Attempting to register user: {email}")
        print(f"🔑 Password length: {len(password) if password else 0}")

        user_created = create_user(email, password, company_name)
        print(f"✅ User creation result: {user_created}")

        if user_created:
            flash('✅ Account created! Please login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('❌ User already exists or registration failed', 'error')
            return render_template('register.html', nonce=g.nonce)

    # GET request - show form
    return render_template('register.html', nonce=g.nonce)

# dashboard
@app.route("/dashboard")
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.auth import get_business_summary, get_client_analytics

    with DB_ENGINE.connect() as conn:
        total_products = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND is_active = TRUE
        """), {"user_id": session['user_id']}).scalar()

        low_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND current_stock <= min_stock_level AND current_stock > 0
        """), {"user_id": session['user_id']}).scalar()

        out_of_stock_items = conn.execute(text("""
            SELECT COUNT(*) FROM inventory_items
            WHERE user_id = :user_id AND current_stock = 0
        """), {"user_id": session['user_id']}).scalar()

    return render_template(
        "dashboard.html",
        user_email=session['user_email'],
        get_business_summary=get_business_summary,
        get_client_analytics=get_client_analytics,
        total_products=total_products,
        low_stock_items=low_stock_items,
        out_of_stock_items=out_of_stock_items,
        nonce=g.nonce
    )

# logout
@app.route("/logout")
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'info')
    return redirect(url_for('login'))  # Changed from 'home' to 'login'

# donate
@app.route("/donate")
def donate():
    return render_template("donate.html", nonce=g.nonce)

# preview and download
from flask.views import MethodView
from core.services import InvoiceService
from core.number_generator import NumberGenerator
from core.purchases import save_purchase_order

class InvoiceView(MethodView):
    """Handles invoice creation and preview - RESTful design"""

    def get(self):
        if 'user_id' not in session:
            return redirect(url_for('login'))

        if 'last_invoice_ref' in session and request.args.get('preview'):
            from core.session_storage import SessionStorage
            invoice_data = SessionStorage.get_data(session['user_id'], session['last_invoice_ref'])
            if not invoice_data:
                flash("Invoice preview expired or not found", "error")
                return redirect(url_for('create_invoice'))

            # Generate QR
            qr_b64 = generate_simple_qr(invoice_data)  # or generate_qr_base64 if you have it

            # Render the PDF template directly for preview
            html = render_template('invoice_pdf.html',
                                 data=invoice_data,
                                 custom_qr_b64=qr_b64,
                                 fbr_qr_code=None,  # add if you have
                                 fbr_compliant=True,
                                 currency_symbol="Rs.",
                                 preview=True)  # optional flag if you want preview buttons

            return render_template('invoice_preview.html',
                                 html=html,
                                 data=invoice_data,
                                 nonce=g.nonce)

        return redirect(url_for('create_invoice'))

    def post(self):
        """
        POST /invoice/process - Create invoice or purchase order using service layer
        """
        if 'user_id' not in session:
            return redirect(url_for('login'))

        user_id = session['user_id']
        invoice_type = request.form.get('invoice_type', 'S')

        try:
            from core.invoice_service import InvoiceService
            service = InvoiceService(user_id)

            if invoice_type == 'P':
                # Create purchase order
                po_data, errors = service.create_purchase_order(request.form, request.files)

                if errors:
                    for error in errors:
                        flash(f"❌ {error}", 'error')
                    return redirect(url_for('create_purchase_order'))

                if po_data:
                    # Store for preview
                    from core.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_po', po_data)
                    session['last_po_ref'] = session_ref

                    flash(f"✅ Purchase Order {po_data['po_number']} created successfully!", "success")
                    return redirect(url_for('po_preview', po_number=po_data['po_number']))
            else:
                # Create sales invoice
                invoice_data, errors = service.create_invoice(request.form, request.files)

                if errors:
                    for error in errors:
                        flash(f"❌ {error}", 'error')
                    return redirect(url_for('create_invoice'))

                if invoice_data:
                    # Store for preview
                    from core.session_storage import SessionStorage
                    session_ref = SessionStorage.store_large_data(user_id, 'last_invoice', invoice_data)
                    session['last_invoice_ref'] = session_ref

                    flash(f"✅ Invoice {invoice_data['invoice_number']} created successfully!", "success")
                    return redirect(url_for('invoice_process', preview='true'))

            flash("⚠️ Failed to create document", 'error')
            return redirect(url_for('create_invoice'))

        except Exception as e:
            current_app.logger.error(f"Invoice creation error: {str(e)}",
                                   exc_info=True,
                                   extra={'user_id': user_id})
            flash("⚠️ An unexpected error occurred. Please try again.", 'error')
            return redirect(url_for('create_invoice'))



#invoice/download/<document_number>')
@app.route('/invoice/download/<document_number>')
@limiter.limit("10 per minute")
def download_document(document_number):
    """
    Dedicated endpoint for document downloads - FIXED VERSION
    """
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    document_type = request.args.get('type', 'invoice')  # 'invoice' or 'purchase_order'

    try:
        # Fetch document data
        if document_type == 'purchase_order':
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT order_data, created_at, status, po_number
                    FROM purchase_orders
                    WHERE user_id = :user_id AND po_number = :doc_number
                    ORDER BY created_at DESC LIMIT 1
                """), {"user_id": user_id, "doc_number": document_number}).fetchone()

            if not result:
                flash("❌ Purchase order not found or access denied.", "error")
                return redirect(url_for('purchase_orders'))

            service_data = json.loads(result[0])
            created_at = result[1]
            status = result[2] or 'PENDING'
            po_number = result[3] or document_number

            # Add metadata
            service_data['po_number'] = po_number
            service_data['status'] = status
            service_data['created_at'] = created_at

            # Get user profile for company info
            user_profile = get_user_profile_cached(user_id) or {}
            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
            service_data['company_address'] = user_profile.get('company_address', '')
            service_data['company_phone'] = user_profile.get('company_phone', '')
            service_data['company_email'] = user_profile.get('email', '')

            document_type_name = "Purchase Order"

            # === ENRICH PO ITEMS WITH REAL PRODUCT DATA (same as preview) ===
            from core.inventory import InventoryManager
            inventory_items = InventoryManager.get_inventory_items(user_id)

            product_lookup = {}
            for product in inventory_items:
                pid = product.get('id')
                if pid is not None:
                    product_lookup[str(pid)] = product
                    product_lookup[int(pid)] = product

            for item in service_data.get('items', []):
                pid = item.get('product_id')
                if pid is not None and pid in product_lookup:
                    real = product_lookup[pid]
                    item['sku'] = real.get('sku', 'N/A')
                    item['name'] = real.get('name', item.get('name', 'Unknown Product'))
                    item['supplier'] = real.get('supplier', service_data.get('supplier_name', 'Unknown Supplier'))


            # Generate PDF
            from core.pdf_generator import generate_purchase_order_pdf
            pdf_bytes = generate_purchase_order_pdf(service_data)

        else:  # Sales Invoice
            with DB_ENGINE.connect() as conn:
                result = conn.execute(text("""
                    SELECT invoice_data, created_at, invoice_number, status
                    FROM user_invoices
                    WHERE user_id = :user_id AND invoice_number = :doc_number
                    ORDER BY created_at DESC LIMIT 1
                """), {"user_id": user_id, "doc_number": document_number}).fetchone()

            if not result:
                flash("❌ Invoice not found or access denied.", "error")
                return redirect(url_for('invoice_history'))

            service_data = json.loads(result[0])
            created_at = result[1]
            invoice_number = result[2] or document_number
            status = result[3] or 'PAID'

            # Add metadata
            service_data['invoice_number'] = invoice_number
            service_data['status'] = status
            service_data['created_at'] = created_at

            # Get user profile for company info
            user_profile = get_user_profile_cached(user_id) or {}
            service_data['company_name'] = user_profile.get('company_name', 'Your Company')
            service_data['company_address'] = user_profile.get('company_address', '')
            service_data['company_phone'] = user_profile.get('company_phone', '')
            service_data['company_email'] = user_profile.get('email', '')

            document_type_name = "Invoice"

            # Generate PDF
            from core.pdf_generator import generate_invoice_pdf
            pdf_bytes = generate_invoice_pdf(service_data)

        # Create filename
        import re
        safe_doc_number = re.sub(r'[^\w\-]', '_', document_number)
        timestamp = created_at.strftime('%Y%m%d_%H%M') if created_at else datetime.now().strftime('%Y%m%d_%H%M')
        filename = f"{document_type_name.replace(' ', '_')}_{safe_doc_number}_{timestamp}.pdf"

        # Create response
        response = make_response(send_file(
            io.BytesIO(pdf_bytes),
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        ))

        # Security headers
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'

        return response

    except Exception as e:
        current_app.logger.error(f"Download error: {str(e)}", exc_info=True)
        flash("❌ Download failed. Please try again.", 'error')
        return redirect(url_for('invoice_history' if document_type != 'purchase_order' else 'purchase_orders'))

# NEW: Direct PDF Creation Functions
def create_purchase_order_pdf_direct(data):
    """Create purchase order PDF directly from data"""
    buffer = io.BytesIO()

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch, cm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'POTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#0d6efd'),
        alignment=1,  # Center
        spaceAfter=12
    )

    header_style = ParagraphStyle(
        'POHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#0d6efd'),
        spaceAfter=6
    )

    normal_style = ParagraphStyle(
        'PONormal',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    )

    bold_style = ParagraphStyle(
        'POBold',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica-Bold'
    )

    # Title
    story.append(Paragraph(data['title'], title_style))
    story.append(Paragraph(f"PO #: {data['document_number']}", header_style))
    story.append(Spacer(1, 0.2*inch))

    # Company Info
    story.append(Paragraph(f"<b>FROM:</b> {data['company_name']}", bold_style))
    if data['company_address']:
        story.append(Paragraph(data['company_address'], normal_style))
    if data['company_phone']:
        story.append(Paragraph(f"Phone: {data['company_phone']}", normal_style))
    if data['company_email']:
        story.append(Paragraph(f"Email: {data['company_email']}", normal_style))

    story.append(Spacer(1, 0.2*inch))

    # Supplier Info Box
    supplier_info = [
        [Paragraph("<b>TO:</b>", bold_style), ""],
        [Paragraph(f"{data['supplier_name']}", normal_style),
         Paragraph(f"<b>PO Date:</b> {data['po_date']}", normal_style)],
        [Paragraph(f"{data['supplier_address']}", normal_style),
         Paragraph(f"<b>Delivery Date:</b> {data['delivery_date']}", normal_style)],
        [Paragraph(f"Phone: {data['supplier_phone']}", normal_style),
         Paragraph(f"<b>Status:</b> {data['status']}", normal_style)],
        [Paragraph(f"Email: {data['supplier_email']}", normal_style), ""]
    ]

    supplier_table = Table(supplier_info, colWidths=[3.5*inch, 3.5*inch])
    supplier_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#e8f4fd')),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#f8f9fa')),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    story.append(supplier_table)
    story.append(Spacer(1, 0.3*inch))

    # Items Table
    if data['items']:
        table_data = [['#', 'Description', 'SKU', 'Supplier', 'Qty', 'Unit Price', 'Total']]

        for idx, item in enumerate(data['items'], 1):
            table_data.append([
                str(idx),
                item.get('name', ''),
                item.get('sku', ''),
                item.get('supplier', ''),
                str(item.get('qty', 1)),
                f"{data['currency_symbol']}{item.get('price', 0):.2f}",
                f"{data['currency_symbol']}{item.get('total', 0):.2f}"
            ])

        # Add totals
        table_data.append(['', '', '', '', '',
                         Paragraph('<b>Subtotal:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['subtotal']:.2f}</b>", bold_style)])

        if data['tax_amount'] > 0:
            table_data.append(['', '', '', '', '',
                             Paragraph(f'<b>Tax ({data["sales_tax"]}%):</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['tax_amount']:.2f}</b>", bold_style)])

        if data.get('shipping_cost', 0) > 0:
            table_data.append(['', '', '', '', '',
                             Paragraph('<b>Shipping:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['shipping_cost']:.2f}</b>", bold_style)])

        table_data.append(['', '', '', '', '',
                         Paragraph('<b>GRAND TOTAL:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['grand_total']:.2f}</b>", bold_style)])

        items_table = Table(table_data, colWidths=[0.4*inch, 2*inch, 1*inch, 1.2*inch, 0.5*inch, 1*inch, 1*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, len(data['items']) + 1), 1, colors.grey),
            ('ALIGN', (4, 1), (6, len(data['items']) + 1), 'RIGHT'),
            ('BACKGROUND', (0, -4), (-1, -1), colors.HexColor('#f8f9fa')),
            ('LINEABOVE', (0, -4), (-1, -4), 2, colors.black),
        ]))

        story.append(items_table)
        story.append(Spacer(1, 0.3*inch))

    # Terms & Conditions Box
    story.append(Paragraph("TERMS & CONDITIONS", header_style))

    terms_data = [
        [Paragraph(f"<b>Payment Terms:</b> {data['payment_terms']}", normal_style)],
        [Paragraph(f"<b>Shipping Terms:</b> {data['shipping_terms']}", normal_style)],
        [Paragraph(f"<b>Delivery Method:</b> {data['delivery_method']}", normal_style)]
    ]

    if data.get('notes'):
        terms_data.append([Paragraph(f"<b>Notes:</b> {data['notes']}", normal_style)])

    terms_table = Table(terms_data, colWidths=[7*inch])
    terms_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#6c757d')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))

    story.append(terms_table)
    story.append(Spacer(1, 0.5*inch))

    # Signatures
    sig_data = [
        [
            Paragraph("_________________________<br/><b>Authorized Signature</b>", normal_style),
            Paragraph("_________________________<br/><b>Supplier Acknowledgment</b>", normal_style)
        ]
    ]

    sig_table = Table(sig_data, colWidths=[3.5*inch, 3.5*inch])
    story.append(sig_table)

    # Footer
    story.append(Spacer(1, 0.5*inch))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | PO #: {data['document_number']}",
                          ParagraphStyle('Footer', parent=styles['Italic'], fontSize=8, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def create_invoice_pdf_direct(data):
    """Create invoice PDF directly from data"""
    buffer = io.BytesIO()

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.lib.units import inch, cm

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1*cm,
        leftMargin=1*cm,
        topMargin=1.5*cm,
        bottomMargin=1.5*cm
    )

    story = []
    styles = getSampleStyleSheet()

    # Custom styles for Invoice
    title_style = ParagraphStyle(
        'InvoiceTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor('#28a745'),  # Green for invoices
        alignment=1,
        spaceAfter=12
    )

    header_style = ParagraphStyle(
        'InvoiceHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.HexColor('#28a745'),
        spaceAfter=6
    )

    normal_style = ParagraphStyle(
        'InvoiceNormal',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6
    )

    bold_style = ParagraphStyle(
        'InvoiceBold',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica-Bold'
    )

    # Title with Tax Invoice
    story.append(Paragraph("TAX INVOICE", title_style))
    story.append(Paragraph(f"Invoice #: {data['document_number']}", header_style))
    story.append(Spacer(1, 0.2*inch))

    # Seller/Buyer info in two columns
    seller_info = [
        [Paragraph("<b>SELLER:</b>", bold_style), Paragraph("<b>BUYER:</b>", bold_style)],
        [Paragraph(data['company_name'], normal_style),
         Paragraph(data['client_name'], normal_style)],
        [Paragraph(data['company_address'], normal_style),
         Paragraph(data['client_address'], normal_style)],
        [Paragraph(f"Phone: {data['company_phone']}", normal_style),
         Paragraph(f"Phone: {data['client_phone']}", normal_style)],
        [Paragraph(f"Email: {data['company_email']}", normal_style),
         Paragraph(f"Email: {data['client_email']}", normal_style)]
    ]

    # Add tax IDs if available
    if data.get('seller_ntn') or data.get('company_tax_id'):
        seller_info.append([
            Paragraph(f"Tax ID: {data.get('seller_ntn') or data.get('company_tax_id')}", normal_style),
            Paragraph(f"Tax ID: {data.get('client_tax_id', '')}", normal_style)
        ])

    seller_table = Table(seller_info, colWidths=[3.5*inch, 3.5*inch])
    seller_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#e8f4fd')),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor('#f8f9fa')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))

    story.append(seller_table)
    story.append(Spacer(1, 0.2*inch))

    # Invoice details
    details_data = [
        [Paragraph(f"<b>Invoice Date:</b> {data['invoice_date']}", normal_style),
         Paragraph(f"<b>Due Date:</b> {data['due_date']}", normal_style),
         Paragraph(f"<b>Status:</b> {data['status']}", normal_style)]
    ]

    details_table = Table(details_data, colWidths=[2.3*inch, 2.3*inch, 2.3*inch])
    details_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('PADDING', (0, 0), (-1, -1), 8),
    ]))

    story.append(details_table)
    story.append(Spacer(1, 0.3*inch))

    # Items Table for Invoice
    if data['items']:
        table_data = [['#', 'Description', 'Qty', 'Unit Price', 'Total']]

        for idx, item in enumerate(data['items'], 1):
            table_data.append([
                str(idx),
                item.get('name', ''),
                str(item.get('qty', 1)),
                f"{data['currency_symbol']}{item.get('price', 0):.2f}",
                f"{data['currency_symbol']}{item.get('total', 0):.2f}"
            ])

        # Add totals
        table_data.append(['', '', '',
                         Paragraph('<b>Subtotal:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['subtotal']:.2f}</b>", bold_style)])

        if data.get('tax_amount', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph('<b>Tax:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['tax_amount']:.2f}</b>", bold_style)])

        if data.get('discount', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph(f'<b>Discount:</b>', bold_style),
                             Paragraph(f"<b>-{data['currency_symbol']}{data['discount']:.2f}</b>", bold_style)])

        if data.get('shipping', 0) > 0:
            table_data.append(['', '', '',
                             Paragraph('<b>Shipping:</b>', bold_style),
                             Paragraph(f"<b>{data['currency_symbol']}{data['shipping']:.2f}</b>", bold_style)])

        table_data.append(['', '', '',
                         Paragraph('<b>GRAND TOTAL:</b>', bold_style),
                         Paragraph(f"<b>{data['currency_symbol']}{data['grand_total']:.2f}</b>", bold_style)])

        items_table = Table(table_data, colWidths=[0.4*inch, 3*inch, 0.6*inch, 1.2*inch, 1.2*inch])
        items_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#28a745')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('GRID', (0, 0), (-1, len(data['items']) + 1), 1, colors.grey),
            ('ALIGN', (2, 1), (4, len(data['items']) + 1), 'RIGHT'),
            ('BACKGROUND', (0, -4), (-1, -1), colors.HexColor('#f8f9fa')),
            ('LINEABOVE', (0, -4), (-1, -4), 2, colors.black),
        ]))

        story.append(items_table)
        story.append(Spacer(1, 0.3*inch))

    # Payment details and notes
    if data.get('notes') or data.get('terms'):
        notes_data = []
        if data.get('notes'):
            notes_data.append([Paragraph(f"<b>Notes:</b> {data['notes']}", normal_style)])
        if data.get('terms'):
            notes_data.append([Paragraph(f"<b>Terms:</b> {data['terms']}", normal_style)])

        notes_table = Table(notes_data, colWidths=[7*inch])
        notes_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#6c757d')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f9fa')),
            ('PADDING', (0, 0), (-1, -1), 8),
        ]))

        story.append(notes_table)
        story.append(Spacer(1, 0.3*inch))

    # Thank you message and footer
    story.append(Paragraph("Thank you for your business!",
                          ParagraphStyle('Thanks', parent=styles['Italic'], fontSize=11, alignment=1)))
    story.append(Spacer(1, 0.3*inch))

    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Invoice #: {data['document_number']}",
                          ParagraphStyle('Footer', parent=styles['Italic'], fontSize=8, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

# Register route
app.add_url_rule('/invoice/process', view_func=InvoiceView.as_view('invoice_process'), methods=['GET', 'POST'])

# poll route
@app.route('/invoice/status/<user_id>')
def status(user_id):
    try:
        from core.services import InvoiceService
        service = InvoiceService(int(user_id))
        result = service.redis_client.get(f"preview:{user_id}")
        if result:
            return jsonify({'ready': True, 'data': json.loads(result)})
        return jsonify({'ready': False})
    except:
        return jsonify({'ready': False})

#clean up
@app.route('/cancel_invoice')
def cancel_invoice():
    """Cancel pending invoice"""
    if 'user_id' in session:
        clear_pending_invoice(session['user_id'])
        session.pop('invoice_finalized', None)
        flash('Invoice cancelled', 'info')
    return redirect(url_for('create_invoice'))

# invoice history
@app.route("/invoice_history")
def invoice_history():
    """Invoice history and management page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # Get pagination parameters
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()
    limit = 20  # Invoices per page
    offset = (page - 1) * limit

    user_id = session['user_id']

    with DB_ENGINE.connect() as conn:
        # Base query
        base_sql = '''
            SELECT id, invoice_number, client_name, invoice_date, due_date, grand_total, status, created_at
            FROM user_invoices
            WHERE user_id = :user_id
        '''
        params = {"user_id": user_id}

        # Add search if provided
        if search:
            base_sql += ' AND (invoice_number ILIKE :search OR client_name ILIKE :search)'
            params["search"] = f"%{search}%"

        # Get total count for pagination
        count_sql = f"SELECT COUNT(*) FROM ({base_sql}) AS count_query"
        total_invoices = conn.execute(text(count_sql), params).scalar()

        # Get paginated invoices
        invoices_sql = base_sql + '''
            ORDER BY invoice_date DESC, created_at DESC
            LIMIT :limit OFFSET :offset
        '''
        params.update({"limit": limit, "offset": offset})
        invoices_result = conn.execute(text(invoices_sql), params).fetchall()

    # Convert to list of dicts for template
    invoices = []
    for row in invoices_result:
        invoices.append({
            'id': row[0],
            'invoice_number': row[1],
            'client_name': row[2],
            'invoice_date': row[3],
            'due_date': row[4],
            'grand_total': float(row[5]) if row[5] else 0.0,
            'status': row[6],
            'created_at': row[7].strftime('%Y-%m-%d %H:%M:%S') if row[7] else ''
        })

    total_pages = (total_invoices + limit - 1) // limit  # Ceiling division

    return render_template(
        "invoice_history.html",
        invoices=invoices,
        current_page=page,
        total_pages=total_pages,
        search_query=search,
        total_invoices=total_invoices,
        nonce=g.nonce
    )

# purchase order - FIXED VERSION
@app.route("/purchase_orders")
def purchase_orders():
    """Purchase order history with download options - FIXED"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        from core.purchases import get_purchase_orders

        page = request.args.get('page', 1, type=int)
        limit = 10
        offset = (page - 1) * limit

        # Get orders with error handling
        orders = []
        try:
            orders = get_purchase_orders(session['user_id'], limit=limit, offset=offset)

            # Fix date formats for template
            for order in orders:
                if 'order_date' in order and order['order_date']:
                    if isinstance(order['order_date'], str):
                        try:
                            from datetime import datetime
                            order['order_date'] = datetime.strptime(order['order_date'], '%Y-%m-%d')
                        except:
                            pass
        except Exception as e:
            current_app.logger.error(f"Error loading purchase orders: {e}")
            flash("Could not load purchase orders", "warning")

        # Get user's currency for display
        user_profile = get_user_profile_cached(session['user_id'])
        currency_code = user_profile.get('preferred_currency', 'PKR') if user_profile else 'PKR'
        currency_symbol = CURRENCY_SYMBOLS.get(currency_code, 'Rs.')

        return render_template("purchase_orders.html",
                             orders=orders,
                             current_page=page,
                             currency_symbol=currency_symbol,
                             nonce=g.nonce)
    except Exception as e:
        current_app.logger.error(f"Purchase orders route error: {e}")
        flash("Error loading purchase orders", "error")
        return redirect(url_for('dashboard'))

#API endpoints for better UX
@app.route("/api/purchase_order/<po_number>")
@limiter.limit("30 per minute")
def get_purchase_order_details(po_number):
    """API endpoint to get PO details"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with DB_ENGINE.connect() as conn:
            result = conn.execute(text("""
                SELECT order_data, status, created_at
                FROM purchase_orders
                WHERE user_id = :user_id AND po_number = :po_number
                ORDER BY created_at DESC LIMIT 1
            """), {"user_id": session['user_id'], "po_number": po_number}).fetchone()

        if not result:
            return jsonify({'error': 'Purchase order not found'}), 404

        order_data = json.loads(result[0])
        order_data['status'] = result[1]
        order_data['created_at'] = result[2].isoformat() if result[2] else None

        return jsonify(order_data), 200

    except Exception as e:
        current_app.logger.error(f"PO details error: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

# supplier management
@app.route("/suppliers")
def suppliers():
    """Supplier management"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.purchases import get_suppliers
    supplier_list = get_suppliers(session['user_id'])

    return render_template("suppliers.html",
                         suppliers=supplier_list,
                         nonce=g.nonce)

# CUSTOMER MANAGEMENT ROUTES
@app.route("/customers")
def customers():
    """Customer management page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.auth import get_customers
    customer_list = get_customers(session['user_id'])

    return render_template("customers.html", customers=customer_list, nonce=g.nonce)

# EXPENSE TRACKING ROUTES
@app.route("/expenses")
def expenses():
    """Expense tracking page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.auth import get_expenses, get_expense_summary
    from datetime import datetime

    expense_list = get_expenses(session['user_id'])
    expense_summary = get_expense_summary(session['user_id'])
    today_date = datetime.now().strftime('%Y-%m-%d')

    return render_template("expenses.html",
                         expenses=expense_list,
                         expense_summary=expense_summary,
                         today_date=today_date,
                         nonce=g.nonce)

#add expense
@app.route("/add_expense", methods=['POST'])
def add_expense():
    """Add new expense"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    from core.auth import save_expense

    expense_data = {
        'description': request.form.get('description'),
        'amount': float(request.form.get('amount', 0)),
        'category': request.form.get('category'),
        'expense_date': request.form.get('expense_date'),
        'notes': request.form.get('notes', '')
    }

    if save_expense(session['user_id'], expense_data):
        flash('Expense added successfully!', 'success')
    else:
        flash('Error adding expense', 'error')

    return redirect(url_for('expenses'))

#Backup Route (Manual Trigger)
@app.route('/admin/backup')
def admin_backup():
    """Manual database backup trigger (admin only)"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    # Simple admin check (first user is admin)
    if session['user_id'] != 1:
        return jsonify({'error': 'Admin only'}), 403

    try:
        import subprocess
        result = subprocess.run(['python', 'backup_db.py'],
                              capture_output=True,
                              text=True,
                              timeout=30)

        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'Backup created successfully',
                'output': result.stdout
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': result.stderr
            }), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Health status
@app.route('/health')
def health_check():
    try:
        with DB_ENGINE.connect() as conn:
            user_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            invoice_count = conn.execute(text("SELECT COUNT(*) FROM user_invoices")).scalar()
            product_count = conn.execute(text("SELECT COUNT(*) FROM inventory_items WHERE is_active = TRUE")).scalar()

        import shutil
        total, used, free = shutil.disk_usage(".")
        disk_free_gb = free // (2**30)

        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'database': 'connected',
            'users': user_count,
            'invoices': invoice_count,
            'products': product_count,
            'disk_free_gb': disk_free_gb,
            'version': '1.0.0'
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

#API status
@app.route('/api/status')
def system_status():
    """Detailed system status"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        with DB_ENGINE.connect() as conn:
            total_users = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            total_invoices = conn.execute(text("SELECT COUNT(*) FROM user_invoices")).scalar()
            total_products = conn.execute(text("SELECT COUNT(*) FROM inventory_items WHERE is_active = TRUE")).scalar()

        return jsonify({
            'status': 'operational',
            'stats': {
                'total_users': total_users or 0,
                'total_invoices': total_invoices or 0,
                'total_products': total_products or 0
            },
            'timestamp': datetime.now().isoformat()
        }), 200

    except Exception as e:
        print(f"System status error: {e}")
        return jsonify({'error': 'Database error'}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
