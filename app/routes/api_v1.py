# app/routes/api_v1.py
from flask import Blueprint, request, jsonify, session, g
from sqlalchemy import text
from app.services.db import DB_ENGINE
from app.services.api_keys import validate_api_key
from app.services.inventory import InventoryManager
from app.decorators import role_required
from app.extensions import limiter

api_v1_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')

# Helper to get account_id from API key or session
def get_account_id():
    """Try to get account_id from Bearer token, or fall back to session (for web)."""
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        raw_key = auth_header[7:]
        account_id, error = validate_api_key(raw_key)
        if error:
            return None, error
        return account_id, None
    elif 'user_id' in session:
        return session.get('account_id'), None
    else:
        return None, "Unauthorized"

# Decorator to enforce API key or session
def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        account_id, error = get_account_id()
        if not account_id:
            return jsonify({"error": error or "Unauthorized"}), 401
        # Attach account_id to request so route can use it
        g.api_account_id = account_id
        return f(*args, **kwargs)
    return decorated

# --- Inventory Endpoints ---
@api_v1_bp.route('/inventory', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_inventory():
    """List all inventory items for the account."""
    account_id = g.api_account_id
    items = InventoryManager.get_inventory_items(account_id)
    return jsonify(items)

@api_v1_bp.route('/inventory/<int:product_id>', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def get_inventory_item(product_id):
    """Get a single inventory item."""
    account_id = g.api_account_id
    product = InventoryManager.get_product_details(account_id, product_id)
    if not product:
        return jsonify({"error": "Product not found"}), 404
    return jsonify(product)

@api_v1_bp.route('/inventory', methods=['POST'])
@limiter.limit("10 per minute")
@require_auth
def create_inventory_item():
    """Create a new product (requires product data in JSON)."""
    if 'user_id' not in session:  # Only authenticated web users can create via API? Or we could use API key only?
        # We'll require that the request also comes from a logged‑in user or the API key must be associated with an account.
        # Since we already have account_id, we can create, but we need user_id for audit (stock_movements).
        # For API, we can use a placeholder user (e.g., the owner of the account).
        # Let's fetch the first owner user_id for the account.
        with DB_ENGINE.connect() as conn:
            row = conn.execute(text("""
                SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
            """), {"aid": g.api_account_id}).first()
            if not row:
                return jsonify({"error": "No owner found for account"}), 400
            user_id = row[0]
    else:
        user_id = session['user_id']

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON data"}), 400

    # Minimal validation
    if not data.get('name') or not data.get('sku'):
        return jsonify({"error": "name and sku are required"}), 400

    product_data = {
        'name': data['name'],
        'sku': data['sku'],
        'barcode': data.get('barcode'),
        'category': data.get('category'),
        'description': data.get('description'),
        'current_stock': float(data.get('current_stock', 0)),
        'min_stock_level': int(data.get('min_stock_level', 5)),
        'cost_price': float(data.get('cost_price', 0)),
        'selling_price': float(data.get('selling_price', 0)),
        'supplier': data.get('supplier'),
        'location': data.get('location'),
        'unit_type': data.get('unit_type', 'piece'),
        'is_perishable': data.get('is_perishable', False),
        'expiry_date': data.get('expiry_date'),
        'batch_number': data.get('batch_number'),
        'pack_size': float(data.get('pack_size', 1.0)),
        'weight_kg': float(data.get('weight_kg')) if data.get('weight_kg') else None,
    }

    product_id = InventoryManager.add_product(user_id, g.api_account_id, product_data)
    if product_id:
        return jsonify({"id": product_id, "message": "Product created"}), 201
    else:
        return jsonify({"error": "Product could not be created (duplicate SKU?)"}), 400

# Add similar endpoints for other resources (invoices, customers, etc.)
