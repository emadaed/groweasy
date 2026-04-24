# app/routes/api_v1.py
import hashlib
import logging
import functools
from collections import defaultdict
from decimal import Decimal

from flask import Blueprint, request, jsonify, session, g
from flask_limiter.util import get_remote_address
from sqlalchemy import text

from app.services.db import DB_ENGINE
from app.services.api_keys import validate_api_key
from app.services.inventory import InventoryManager
from app.decorators import role_required
from app.extensions import limiter, csrf

logger = logging.getLogger(__name__)
api_v1_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# ---------------------------------------------------------------------------
# Rate limit key function
# ---------------------------------------------------------------------------

def get_api_rate_limit_key():
    """
    Rate-limit by hashed API token for Bearer auth, or by IP for session auth.

    This gives each API key its own independent bucket.  Without this, all
    API key users sharing the same IP (e.g. a shared office NAT) would exhaust
    each other's limits — and a single abusive key couldn't be isolated.
    """
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        # Hash the key so the raw token never appears in Redis
        token_hash = hashlib.sha256(auth[7:].encode()).hexdigest()[:16]
        return f"apikey:{token_hash}"
    return get_remote_address()


# ---------------------------------------------------------------------------
# API logging
# ---------------------------------------------------------------------------

def log_api_call(account_id, endpoint, method, status_code, ip):
    try:
        with DB_ENGINE.begin() as conn:
            conn.execute(text("""
                INSERT INTO api_logs (account_id, endpoint, method, status_code, ip_address)
                VALUES (:aid, :endpoint, :method, :status, :ip)
            """), {"aid": account_id, "endpoint": endpoint,
                   "method": method, "status": status_code, "ip": ip})
    except Exception as e:
        logger.warning(f"API log insert failed: {e}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def error_response(message, code, status_code=400):
    return jsonify({"error": message, "code": code}), status_code


def get_account_id():
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        raw_key = auth_header[7:]
        account_id, error = validate_api_key(raw_key)
        if error:
            return None, error
        return account_id, None
    elif 'user_id' in session:
        return session.get('account_id'), None
    return None, "Unauthorized"


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        account_id, error = get_account_id()
        if not account_id:
            return error_response(error or "Unauthorized", "UNAUTHORIZED", 401)
        g.api_account_id = account_id
        return f(*args, **kwargs)
    return decorated


@api_v1_bp.after_request
def log_request(response):
    if hasattr(g, 'api_account_id') and g.api_account_id:
        log_api_call(
            g.api_account_id, request.path,
            request.method, response.status_code, request.remote_addr
        )
    return response


# ---------------------------------------------------------------------------
# INVENTORY
# ---------------------------------------------------------------------------

@api_v1_bp.route('/inventory', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_inventory_paginated():
    """List inventory items with pagination, search, and location breakdown."""
    account_id = g.api_account_id
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 50, type=int), 200)  # cap at 200
    search = request.args.get('search', '').strip()

    with DB_ENGINE.connect() as conn:
        # Build base filter
        where = "WHERE i.account_id = :aid AND i.is_active = TRUE"
        params = {"aid": account_id}
        if search:
            where += " AND (i.name ILIKE :search OR i.sku ILIKE :search)"
            params["search"] = f"%{search}%"

        # Count (without location join to keep it fast)
        total = conn.execute(text(
            f"SELECT COUNT(*) FROM inventory_items i {where}"
        ), params).scalar() or 0
        total_pages = (total + per_page - 1) // per_page

        offset = (page - 1) * per_page

        # FIX: Single JOIN query replaces the N+1 loop that called
        # get_product_location_breakdown() once per product.
        rows = conn.execute(text(f"""
            SELECT
                i.id, i.name, i.sku, i.category, i.current_stock,
                i.min_stock_level, i.cost_price, i.selling_price,
                i.supplier, i.location,
                l.id          AS loc_id,
                l.location_name,
                l.location_code,
                l.location_type,
                pl.quantity   AS loc_qty,
                pl.reserved_quantity
            FROM inventory_items i
            LEFT JOIN product_locations pl ON pl.product_id = i.id
            LEFT JOIN locations l ON l.id = pl.location_id AND l.is_active = TRUE
            {where}
            ORDER BY i.name
            LIMIT :limit OFFSET :offset
        """), {**params, "limit": per_page, "offset": offset}).fetchall()

    # Assemble products, grouping location rows per product_id
    product_map = {}
    loc_map = defaultdict(list)

    for r in rows:
        pid = r[0]
        if pid not in product_map:
            product_map[pid] = {
                'id': pid, 'name': r[1], 'sku': r[2], 'category': r[3],
                'current_stock': float(r[4]) if r[4] else 0,
                'min_stock_level': r[5] or 10,
                'cost_price': float(r[6]) if r[6] else 0,
                'selling_price': float(r[7]) if r[7] else 0,
                'supplier': r[8] or '', 'location': r[9] or 'Main',
                'location_breakdown': None
            }
        if r[10] is not None:  # loc_id
            loc_map[pid].append({
                'location_id': r[10],
                'location_name': r[11],
                'location_code': r[12],
                'quantity': float(r[14]) if r[14] else 0,
                'reserved': float(r[15]) if r[15] else 0,
                'type': r[13]
            })

    for pid, locs in loc_map.items():
        if pid in product_map:
            product_map[pid]['location_breakdown'] = {
                'total_stock': sum(l['quantity'] for l in locs),
                'locations': locs
            }

    return jsonify({
        'products': list(product_map.values()),
        'total': total,
        'total_pages': total_pages,
        'current_page': page
    })


@api_v1_bp.route('/inventory/<int:product_id>', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_inventory_item(product_id):
    account_id = g.api_account_id
    product = InventoryManager.get_product_details(account_id, product_id)
    if not product:
        return error_response("Product not found", "NOT_FOUND", 404)
    return jsonify(product)


@api_v1_bp.route('/inventory', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def create_inventory_item():
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('name') or not data.get('sku'):
        return error_response("name and sku are required", "MISSING_FIELD", 400)

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found for account", "INTERNAL_ERROR", 400)
        user_id = row[0]

    product_data = {
        'name': data['name'], 'sku': data['sku'],
        'barcode': data.get('barcode'), 'category': data.get('category'),
        'description': data.get('description'),
        'current_stock': float(data.get('current_stock', 0)),
        'min_stock_level': int(data.get('min_stock_level', 5)),
        'cost_price': float(data.get('cost_price', 0)),
        'selling_price': float(data.get('selling_price', 0)),
        'supplier': data.get('supplier'), 'location': data.get('location'),
        'unit_type': data.get('unit_type', 'piece'),
        'is_perishable': data.get('is_perishable', False),
        'expiry_date': data.get('expiry_date'), 'batch_number': data.get('batch_number'),
        'pack_size': float(data.get('pack_size', 1.0)),
        'weight_kg': float(data.get('weight_kg')) if data.get('weight_kg') else None,
    }

    product_id = InventoryManager.add_product(user_id, account_id, product_data)
    if product_id:
        return jsonify({"id": product_id, "message": "Product created"}), 201
    return error_response("Could not create product (duplicate SKU?)", "DUPLICATE_SKU", 400)


@api_v1_bp.route('/inventory/<string:sku>/stock', methods=['PATCH'])
@csrf.exempt
@limiter.limit("30 per minute", key_func=get_api_rate_limit_key)
@require_auth
def update_stock_by_sku(sku):
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    delta = data.get('delta')
    if delta is None:
        return error_response("delta is required", "MISSING_FIELD", 400)
    try:
        delta = float(delta)
    except (ValueError, TypeError):
        return error_response("delta must be a number", "INVALID_DATA", 400)

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, current_stock FROM inventory_items
            WHERE sku = :sku AND account_id = :aid AND is_active = TRUE
        """), {"sku": sku, "aid": account_id}).first()
        if not row:
            return error_response("Product not found", "NOT_FOUND", 404)
        product_id, product_name, current_stock = row

        owner = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not owner:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = owner[0]

    success = InventoryManager.update_stock_delta(
        user_id, account_id, product_id, delta,
        'api_adjustment', notes=f"API stock adjustment (delta={delta})"
    )
    if success:
        new_stock = float(Decimal(str(current_stock)) + Decimal(str(delta)))
        return jsonify({"message": "Stock updated", "new_stock": new_stock}), 200
    return error_response("Stock update failed (negative stock?)", "STOCK_ERROR", 400)


@api_v1_bp.route('/inventory/<int:product_id>/stock', methods=['PATCH'])
@csrf.exempt
@limiter.limit("30 per minute", key_func=get_api_rate_limit_key)
@require_auth
def update_stock_via_patch(product_id):
    account_id = g.api_account_id
    data = request.get_json()
    delta = data.get('delta')
    reason = data.get('reason', 'API adjustment')
    if delta is None:
        return error_response("delta required", "MISSING_FIELD", 400)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1"
        ), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]
    success = InventoryManager.update_stock_delta(
        user_id, account_id, product_id, delta, 'api_adjustment', notes=reason
    )
    if success:
        return jsonify({"message": "Stock updated"}), 200
    return error_response("Stock update failed (negative stock?)", "STOCK_ERROR", 400)


# ---------------------------------------------------------------------------
# CUSTOMERS
# ---------------------------------------------------------------------------

@api_v1_bp.route('/customers', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_customers():
    from app.services.auth import get_customers
    return jsonify(get_customers(g.api_account_id))


@api_v1_bp.route('/customers/<int:customer_id>', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_customer(customer_id):
    from app.services.auth import get_customer
    customer = get_customer(g.api_account_id, customer_id)
    if not customer:
        return error_response("Customer not found", "NOT_FOUND", 404)
    return jsonify(customer)


@api_v1_bp.route('/customers', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def create_customer():
    account_id = g.api_account_id
    data = request.get_json()
    if not data or not data.get('name'):
        return error_response("name is required", "MISSING_FIELD", 400)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1"
        ), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]
    from app.services.auth import save_customer
    customer_id = save_customer(user_id, account_id, data)
    if customer_id:
        return jsonify({"id": customer_id, "message": "Customer created"}), 201
    return error_response("Could not create customer", "DATABASE_ERROR", 400)


@api_v1_bp.route('/customers/<int:customer_id>', methods=['PUT'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def update_customer(customer_id):
    data = request.get_json()
    if not data or not data.get('name'):
        return error_response("name is required", "MISSING_FIELD", 400)
    from app.services.auth import update_customer
    success = update_customer(g.api_account_id, customer_id, data)
    if success:
        return jsonify({"message": "Customer updated"}), 200
    return error_response("Customer not found", "NOT_FOUND", 404)


@api_v1_bp.route('/customers/<int:customer_id>', methods=['DELETE'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def delete_customer(customer_id):
    from app.services.auth import delete_customer
    success = delete_customer(g.api_account_id, customer_id)
    if success:
        return jsonify({"message": "Customer deleted"}), 200
    return error_response("Customer not found", "NOT_FOUND", 404)


# ---------------------------------------------------------------------------
# INVOICES
# ---------------------------------------------------------------------------

@api_v1_bp.route('/invoices', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_invoices():
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.auth import get_invoices
    return jsonify(get_invoices(g.api_account_id, limit=limit, offset=offset))


@api_v1_bp.route('/invoices/<string:invoice_number>', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_invoice(invoice_number):
    from app.services.auth import get_invoice_by_number
    invoice = get_invoice_by_number(g.api_account_id, invoice_number)
    if not invoice:
        return error_response("Invoice not found", "NOT_FOUND", 404)
    return jsonify(invoice)


@api_v1_bp.route('/invoices/<string:invoice_number>/status', methods=['PATCH'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def update_invoice_status(invoice_number):
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    new_status = data.get('status')
    if not new_status:
        return error_response("status is required", "MISSING_FIELD", 400)
    allowed = ['paid', 'pending', 'cancelled', 'unpaid']
    if new_status not in allowed:
        return error_response(f"Invalid status. Allowed: {allowed}", "INVALID_STATUS", 400)
    from app.services.auth import update_invoice_status_by_number
    success = update_invoice_status_by_number(g.api_account_id, invoice_number, new_status)
    if success:
        return jsonify({"message": "Invoice status updated"}), 200
    return error_response("Invoice not found or no change", "NOT_FOUND", 404)


@api_v1_bp.route('/invoices', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def create_invoice():
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('client_name'):
        return error_response("client_name is required", "MISSING_FIELD", 400)
    if not data.get('items') or not isinstance(data['items'], list) or len(data['items']) == 0:
        return error_response("items list is required with at least one item", "MISSING_FIELD", 400)

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1"
        ), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]

    from werkzeug.datastructures import MultiDict
    from datetime import datetime as dt
    form_data = MultiDict({
        'client_name': data['client_name'],
        'client_email': data.get('client_email', ''),
        'client_phone': data.get('client_phone', ''),
        'client_address': data.get('client_address', ''),
        'invoice_date': data.get('invoice_date', dt.now().strftime('%Y-%m-%d')),
        'due_date': data.get('due_date', ''),
        'tax_rate': str(data.get('tax_rate', 0)),
        'discount_rate': str(data.get('discount_rate', 0)),
        'delivery_charge': str(data.get('delivery_charge', 0)),
        'invoice_type': 'S'
    })
    for item in data['items']:
        form_data.add('item_name[]', item.get('name', f"Product {item['product_id']}"))
        form_data.add('item_qty[]', str(item['qty']))
        form_data.add('item_price[]', str(item['price']))
        form_data.add('item_id[]', str(item['product_id']))
        form_data.add('item_unit_type[]', item.get('unit_type', 'piece'))

    from app.services.invoice_service import InvoiceService
    service = InvoiceService(user_id)
    invoice_data, errors = service.create_invoice(form_data, files=None)
    if errors:
        return error_response(f"Invoice creation failed: {errors}", "INVOICE_ERROR", 400)
    return jsonify({"message": "Invoice created",
                    "invoice_number": invoice_data['invoice_number']}), 201


# ---------------------------------------------------------------------------
# EXPENSES
# ---------------------------------------------------------------------------

@api_v1_bp.route('/expenses', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_expenses():
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.auth import get_expenses_api
    return jsonify(get_expenses_api(g.api_account_id, limit, offset))


@api_v1_bp.route('/expenses', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def create_expense():
    account_id = g.api_account_id
    data = request.get_json()
    if not data or not data.get('description') or data.get('amount') is None:
        return error_response("description and amount are required", "MISSING_FIELD", 400)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1"
        ), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]
    from app.services.auth import create_expense_api
    expense_id = create_expense_api(account_id, user_id, data)
    if expense_id:
        return jsonify({"id": expense_id, "message": "Expense created"}), 201
    return error_response("Could not create expense", "DATABASE_ERROR", 400)


# ---------------------------------------------------------------------------
# PURCHASE ORDERS
# ---------------------------------------------------------------------------

@api_v1_bp.route('/purchase_orders', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_purchase_orders():
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.purchases import get_purchase_orders_api
    return jsonify(get_purchase_orders_api(g.api_account_id, limit, offset))


@api_v1_bp.route('/purchase_orders/<string:po_number>', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_purchase_order(po_number):
    from app.services.purchases import get_purchase_order_by_number_api
    order = get_purchase_order_by_number_api(g.api_account_id, po_number)
    if not order:
        return error_response("Purchase order not found", "NOT_FOUND", 404)
    return jsonify(order)


# ---------------------------------------------------------------------------
# STOCK MOVEMENTS
# ---------------------------------------------------------------------------

@api_v1_bp.route('/stock_movements', methods=['GET'])
@limiter.limit("100 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_stock_movements():
    product_id = request.args.get('product_id', type=int)
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    movements = InventoryManager.get_stock_movements(g.api_account_id, product_id, limit, offset)
    return jsonify(movements)


# ---------------------------------------------------------------------------
# LOCATIONS
# ---------------------------------------------------------------------------

@api_v1_bp.route('/locations', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def list_locations():
    from app.services.location_inventory import LocationInventoryManager
    return jsonify(LocationInventoryManager.get_account_locations(g.api_account_id))


@api_v1_bp.route('/locations', methods=['POST'])
@csrf.exempt
@limiter.limit("20 per minute", key_func=get_api_rate_limit_key)
@require_auth
def create_location():
    data = request.get_json()
    if not data or not data.get('location_code') or not data.get('location_name'):
        return error_response("location_code and location_name required", "MISSING_FIELD", 400)
    from app.services.location_inventory import LocationInventoryManager
    location_id = LocationInventoryManager.create_location(g.api_account_id, data)
    if location_id:
        return jsonify({"id": location_id, "message": "Location created"}), 201
    return error_response("Failed to create location", "DATABASE_ERROR", 400)


@api_v1_bp.route('/locations/<int:location_id>', methods=['PUT'])
@csrf.exempt
@limiter.limit("20 per minute", key_func=get_api_rate_limit_key)
@require_auth
def update_location(location_id):
    account_id = g.api_account_id
    data = request.get_json()
    with DB_ENGINE.begin() as conn:
        loc = conn.execute(text(
            "SELECT id FROM locations WHERE id=:lid AND account_id=:aid"
        ), {"lid": location_id, "aid": account_id}).first()
        if not loc:
            return error_response("Location not found", "NOT_FOUND", 404)
        conn.execute(text("""
            UPDATE locations
            SET location_name=:name, location_code=:code,
                location_type=:type, address=:address, updated_at=NOW()
            WHERE id=:lid
        """), {
            "name": data.get('location_name'), "code": data.get('location_code'),
            "type": data.get('location_type'), "address": data.get('address'),
            "lid": location_id
        })
    return jsonify({"message": "Location updated"})


@api_v1_bp.route('/locations/<int:location_id>', methods=['DELETE'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def delete_location(location_id):
    account_id = g.api_account_id
    with DB_ENGINE.begin() as conn:
        loc = conn.execute(text(
            "SELECT id FROM locations WHERE id=:lid AND account_id=:aid"
        ), {"lid": location_id, "aid": account_id}).first()
        if not loc:
            return error_response("Location not found", "NOT_FOUND", 404)
        conn.execute(text("DELETE FROM product_locations WHERE location_id=:lid"), {"lid": location_id})
        conn.execute(text("DELETE FROM locations WHERE id=:lid"), {"lid": location_id})
    return jsonify({"message": "Location deleted"})


@api_v1_bp.route('/locations/stats', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_location_stats():
    account_id = g.api_account_id
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT l.id, l.location_name, l.location_code, l.location_type,
                   COUNT(DISTINCT CASE WHEN i.id IS NOT NULL THEN pl.product_id END) as product_count,
                   COALESCE(SUM(CASE WHEN i.id IS NOT NULL THEN pl.quantity ELSE 0 END), 0) as total_units,
                   COALESCE(SUM(CASE WHEN i.id IS NOT NULL THEN pl.quantity * i.cost_price ELSE 0 END), 0) as total_value
            FROM locations l
            LEFT JOIN product_locations pl ON l.id = pl.location_id
            LEFT JOIN inventory_items i ON pl.product_id = i.id AND i.is_active = TRUE
            WHERE l.account_id = :aid AND l.is_active = TRUE
            GROUP BY l.id, l.location_name, l.location_code, l.location_type
            ORDER BY total_value DESC
        """), {"aid": account_id}).fetchall()
    return jsonify([{
        'id': r[0], 'location_name': r[1], 'location_code': r[2], 'type': r[3],
        'product_count': r[4],
        'total_units': float(r[5]) if r[5] else 0,
        'total_value': float(r[6]) if r[6] else 0
    } for r in rows])


@api_v1_bp.route('/products/<int:product_id>/locations', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_product_locations(product_id):
    account_id = g.api_account_id
    product = InventoryManager.get_product_details(account_id, product_id)
    if not product:
        return error_response("Product not found", "NOT_FOUND", 404)
    from app.services.location_inventory import LocationInventoryManager
    return jsonify(LocationInventoryManager.get_product_location_breakdown(product_id))


@api_v1_bp.route('/locations/<int:location_id>/products', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_products_by_location_paginated(location_id):
    account_id = g.api_account_id
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 200)
    search = request.args.get('search', '').strip()

    with DB_ENGINE.connect() as conn:
        loc_check = conn.execute(text("""
            SELECT id FROM locations WHERE id=:lid AND account_id=:aid AND is_active=TRUE
        """), {"lid": location_id, "aid": account_id}).first()
        if not loc_check:
            return error_response("Location not found", "NOT_FOUND", 404)

        base = """
            SELECT i.id, i.name, i.sku, i.category, i.supplier,
                   pl.quantity as stock_at_location,
                   i.min_stock_level, i.cost_price, i.selling_price,
                   i.location as default_location, i.unit_type
            FROM product_locations pl
            JOIN inventory_items i ON pl.product_id = i.id AND i.is_active = TRUE
            WHERE pl.location_id = :lid
        """
        params = {"lid": location_id}
        if search:
            base += " AND (i.name ILIKE :search OR i.sku ILIKE :search)"
            params["search"] = f"%{search}%"

        total = conn.execute(text(f"SELECT COUNT(*) FROM ({base}) sub"), params).scalar() or 0
        offset = (page - 1) * per_page
        rows = conn.execute(text(base + " ORDER BY i.name LIMIT :limit OFFSET :offset"),
                            {**params, "limit": per_page, "offset": offset}).fetchall()

    return jsonify({
        'products': [{
            'id': r[0], 'name': r[1], 'sku': r[2], 'category': r[3], 'supplier': r[4],
            'stock_at_location': float(r[5]) if r[5] else 0,
            'min_stock_level': r[6] or 0, 'cost_price': float(r[7]) if r[7] else 0,
            'selling_price': float(r[8]) if r[8] else 0,
            'default_location': r[9], 'unit_type': r[10] or 'piece'
        } for r in rows],
        'total': total,
        'total_pages': (total + per_page - 1) // per_page,
        'current_page': page
    })


@api_v1_bp.route('/transfer', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute", key_func=get_api_rate_limit_key)
@require_auth
def transfer_stock():
    account_id = g.api_account_id
    data = request.get_json()
    for field in ['product_id', 'from_location_id', 'to_location_id', 'quantity']:
        if field not in data:
            return error_response(f"{field} is required", "MISSING_FIELD", 400)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text(
            "SELECT id FROM users WHERE account_id=:aid AND role='owner' LIMIT 1"
        ), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]
    from app.services.location_inventory import LocationInventoryManager
    transfer_number = LocationInventoryManager.transfer_between_locations(
        account_id=account_id,
        product_id=data['product_id'],
        from_location_id=data['from_location_id'],
        to_location_id=data['to_location_id'],
        quantity=Decimal(str(data['quantity'])),
        user_id=user_id,
        notes=data.get('notes')
    )
    if transfer_number:
        return jsonify({"message": "Transfer completed", "transfer_number": transfer_number}), 200
    return error_response("Transfer failed (insufficient stock?)", "TRANSFER_ERROR", 400)


@api_v1_bp.route('/inventory/low-stock', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_low_stock_api():
    return jsonify(InventoryManager.get_low_stock_alerts(g.api_account_id))


@api_v1_bp.route('/stock-movements/recent', methods=['GET'])
@limiter.limit("60 per minute", key_func=get_api_rate_limit_key)
@require_auth
def get_recent_movements():
    account_id = g.api_account_id
    limit = min(request.args.get('limit', default=20, type=int), 100)
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT lt.created_at, i.name, lt.from_location_id, lt.to_location_id,
                   lt.quantity, lt.status,
                   fl.location_name as from_location, tl.location_name as to_location
            FROM location_transfers lt
            JOIN inventory_items i ON lt.product_id = i.id
            LEFT JOIN locations fl ON lt.from_location_id = fl.id
            LEFT JOIN locations tl ON lt.to_location_id = tl.id
            WHERE lt.account_id = :aid AND i.is_active = TRUE
            ORDER BY lt.created_at DESC
            LIMIT :limit
        """), {"aid": account_id, "limit": limit}).fetchall()
    return jsonify([{
        'created_at': r[0].isoformat() if r[0] else None,
        'product_name': r[1], 'from_location_id': r[2], 'to_location_id': r[3],
        'quantity': float(r[4]) if r[4] else 0, 'status': r[5],
        'from_location': r[6], 'to_location': r[7]
    } for r in rows])
