# app/routes/api_v1.py
from decimal import Decimal
from flask import Blueprint, request, jsonify, session, g
from sqlalchemy import text
from app.services.db import DB_ENGINE
from app.services.api_keys import validate_api_key
from app.services.inventory import InventoryManager
from app.decorators import role_required
from app.extensions import limiter, csrf
import functools

api_v1_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')

#=============API Logs============

def log_api_call(account_id, endpoint, method, status_code, ip):
    with DB_ENGINE.begin() as conn:
        conn.execute(text("""
            INSERT INTO api_logs (account_id, endpoint, method, status_code, ip_address)
            VALUES (:aid, :endpoint, :method, :status, :ip)
        """), {"aid": account_id, "endpoint": endpoint, "method": method, "status": status_code, "ip": ip})

# Helper for consistent error responses
def error_response(message, code, status_code=400):
    """Return a standard error response."""
    return jsonify({"error": message, "code": code}), status_code

# Helper to get account_id from API key or session
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
    else:
        return None, "Unauthorized"

# Authentication decorator
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
        account_id = g.api_account_id
        # Skip logging for static files? not needed
        log_api_call(account_id, request.path, request.method, response.status_code, request.remote_addr)
    return response

# ========== INVENTORY ==========
@api_v1_bp.route('/inventory', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_inventory_paginated():
    """List inventory items with pagination and search."""
    account_id = g.api_account_id
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    search = request.args.get('search', '').strip()
    
    with DB_ENGINE.connect() as conn:
        base_query = """
            SELECT id, name, sku, category, current_stock, min_stock_level,
                   cost_price, selling_price, supplier, location
            FROM inventory_items
            WHERE account_id = :aid AND is_active = TRUE
        """
        params = {"aid": account_id}
        if search:
            base_query += " AND (name ILIKE :search OR sku ILIKE :search)"
            params["search"] = f"%{search}%"
        
        count_result = conn.execute(text(f"SELECT COUNT(*) FROM ({base_query}) AS sub"), params).scalar()
        total = count_result or 0
        total_pages = (total + per_page - 1) // per_page
        
        offset = (page - 1) * per_page
        query = base_query + " ORDER BY name LIMIT :limit OFFSET :offset"
        params["limit"] = per_page
        params["offset"] = offset
        rows = conn.execute(text(query), params).fetchall()
    
        products = []
        for r in rows:
            product = {
                'id': r[0], 'name': r[1], 'sku': r[2], 'category': r[3],
                'current_stock': float(r[4]) if r[4] else 0,
                'min_stock_level': r[5] or 10,
                'cost_price': float(r[6]) if r[6] else 0,
                'selling_price': float(r[7]) if r[7] else 0,
                'supplier': r[8] or '', 'location': r[9] or 'Main'
            }
            # Add location breakdown
            try:
                from app.services.location_inventory import LocationInventoryManager
                breakdown = LocationInventoryManager.get_product_location_breakdown(r[0])
                if breakdown and breakdown.get('locations'):
                    product['location_breakdown'] = breakdown
            except Exception as e:
                pass
            products.append(product)
    return jsonify({'products': products, 'total': total, 'total_pages': total_pages, 'current_page': page})

@api_v1_bp.route('/inventory/<int:product_id>', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def get_inventory_item(product_id):
    """Get a single inventory item."""
    account_id = g.api_account_id
    product = InventoryManager.get_product_details(account_id, product_id)
    if not product:
        return error_response("Product not found", "NOT_FOUND", 404)
    return jsonify(product)

@api_v1_bp.route('/inventory', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def create_inventory_item():
    """Create a new product."""
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('name') or not data.get('sku'):
        return error_response("name and sku are required", "MISSING_FIELD", 400)

    # Get an owner user_id for the account
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found for account", "INTERNAL_ERROR", 400)
        user_id = row[0]

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

    product_id = InventoryManager.add_product(user_id, account_id, product_data)
    if product_id:
        return jsonify({"id": product_id, "message": "Product created"}), 201
    else:
        return error_response("Product could not be created (duplicate SKU?)", "DUPLICATE_SKU", 400)

@api_v1_bp.route('/inventory/<string:sku>/stock', methods=['PATCH'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def update_stock_by_sku(sku):
    """
    Update stock quantity by delta (positive or negative).
    ---
    tags:
      - Inventory
    security:
      - Bearer: []
    parameters:
      - name: sku
        in: path
        type: string
        required: true
        description: Product SKU
      - name: body
        in: body
        required: true
        schema:
          type: object
          properties:
            delta:
              type: number
              description: Quantity to add (positive) or remove (negative)
    responses:
      200:
        description: Stock updated
      400:
        description: Bad request
      404:
        description: Product not found
    """
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    delta = data.get('delta')
    if delta is None:
        return error_response("delta is required", "MISSING_FIELD", 400)
    try:
        delta = float(delta)
    except ValueError:
        return error_response("delta must be a number", "INVALID_DATA", 400)

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id, name, current_stock, min_stock_level FROM inventory_items
            WHERE sku = :sku AND account_id = :aid AND is_active = TRUE
        """), {"sku": sku, "aid": account_id}).first()
        if not row:
            return error_response("Product not found", "NOT_FOUND", 404)
        product_id, product_name, current_stock, min_stock_level = row

    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]

    success = InventoryManager.update_stock_delta(user_id, account_id, product_id, delta, 'api_adjustment', notes=f"Stock adjustment via API (delta={delta})")
    if success:
        from decimal import Decimal
        new_stock = Decimal(str(current_stock)) + Decimal(str(delta))
        return jsonify({"message": "Stock updated", "new_stock": float(new_stock)}), 200
    else:
        return error_response("Stock update failed (negative stock?)", "STOCK_ERROR", 400)
    
@api_v1_bp.route('/inventory/<int:product_id>/stock', methods=['PATCH'])
@csrf.exempt
@require_auth
def update_stock_via_patch(product_id):
    account_id = g.api_account_id
    data = request.get_json()
    delta = data.get('delta')
    reason = data.get('reason', 'API adjustment')
    if delta is None:
        return error_response("delta required", "MISSING_FIELD", 400)
    with DB_ENGINE.connect() as conn:
        # Get user_id
        row = conn.execute(text("SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1"), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]
    success = InventoryManager.update_stock_delta(user_id, account_id, product_id, delta, 'api_adjustment', notes=reason)
    if success:
        return jsonify({"message": "Stock updated"}), 200
    else:
        return error_response("Stock update failed (negative stock?)", "STOCK_ERROR", 400)

# ========== CUSTOMERS ==========
@api_v1_bp.route('/customers', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_customers():
    account_id = g.api_account_id
    from app.services.auth import get_customers
    customers = get_customers(account_id)
    return jsonify(customers)

@api_v1_bp.route('/customers/<int:customer_id>', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def get_customer(customer_id):
    account_id = g.api_account_id
    from app.services.auth import get_customer
    customer = get_customer(account_id, customer_id)
    if not customer:
        return error_response("Customer not found", "NOT_FOUND", 404)
    return jsonify(customer)

@api_v1_bp.route('/customers', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def create_customer():
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('name'):
        return error_response("name is required", "MISSING_FIELD", 400)

    # Get an owner user_id
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found for account", "INTERNAL_ERROR", 400)
        user_id = row[0]

    from app.services.auth import save_customer
    customer_id = save_customer(user_id, account_id, data)
    if customer_id:
        return jsonify({"id": customer_id, "message": "Customer created"}), 201
    else:
        return error_response("Could not create customer", "DATABASE_ERROR", 400)

@api_v1_bp.route('/customers/<int:customer_id>', methods=['PUT'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def update_customer(customer_id):
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('name'):
        return error_response("name is required", "MISSING_FIELD", 400)

    from app.services.auth import update_customer
    success = update_customer(account_id, customer_id, data)
    if success:
        return jsonify({"message": "Customer updated"}), 200
    else:
        return error_response("Customer not found", "NOT_FOUND", 404)

@api_v1_bp.route('/customers/<int:customer_id>', methods=['DELETE'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def delete_customer(customer_id):
    account_id = g.api_account_id
    from app.services.auth import delete_customer
    success = delete_customer(account_id, customer_id)
    if success:
        return jsonify({"message": "Customer deleted"}), 200
    else:
        return error_response("Customer not found", "NOT_FOUND", 404)

# ========== INVOICES ==========
@api_v1_bp.route('/invoices', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_invoices():
    account_id = g.api_account_id
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.auth import get_invoices
    invoices = get_invoices(account_id, limit=limit, offset=offset)
    return jsonify(invoices)

@api_v1_bp.route('/invoices/<string:invoice_number>', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def get_invoice(invoice_number):
    account_id = g.api_account_id
    from app.services.auth import get_invoice_by_number
    invoice = get_invoice_by_number(account_id, invoice_number)
    if not invoice:
        return error_response("Invoice not found", "NOT_FOUND", 404)
    return jsonify(invoice)

@api_v1_bp.route('/invoices/<string:invoice_number>/status', methods=['PATCH'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def update_invoice_status(invoice_number):
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    new_status = data.get('status')
    if not new_status:
        return error_response("status is required", "MISSING_FIELD", 400)
    allowed_statuses = ['paid', 'pending', 'cancelled', 'unpaid']
    if new_status not in allowed_statuses:
        return error_response(f"Invalid status. Allowed: {allowed_statuses}", "INVALID_STATUS", 400)

    from app.services.auth import update_invoice_status_by_number
    success = update_invoice_status_by_number(account_id, invoice_number, new_status)
    if success:
        return jsonify({"message": "Invoice status updated"}), 200
    else:
        return error_response("Invoice not found or no change", "NOT_FOUND", 404)

@api_v1_bp.route('/invoices', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def create_invoice():
    """
    Create a new invoice.
    ---
    tags:
      - Invoices
    security:
      - Bearer: []
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required:
            - client_name
            - items
          properties:
            client_name:
              type: string
            client_email:
              type: string
            client_phone:
              type: string
            client_address:
              type: string
            invoice_date:
              type: string
              format: date
            due_date:
              type: string
              format: date
            tax_rate:
              type: number
              default: 0
            discount_rate:
              type: number
              default: 0
            delivery_charge:
              type: number
              default: 0
            items:
              type: array
              items:
                type: object
                properties:
                  product_id:
                    type: integer
                  qty:
                    type: number
                  price:
                    type: number
                  name:
                    type: string
                  unit_type:
                    type: string
    responses:
      201:
        description: Invoice created
      400:
        description: Bad request
    """
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)

    if not data.get('client_name'):
        return error_response("client_name is required", "MISSING_FIELD", 400)
    if not data.get('items') or not isinstance(data['items'], list) or len(data['items']) == 0:
        return error_response("items list is required with at least one item", "MISSING_FIELD", 400)

    # Get an owner user_id for the account
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found", "INTERNAL_ERROR", 400)
        user_id = row[0]

    # Build form data
    from werkzeug.datastructures import MultiDict
    from datetime import datetime

    form_data = MultiDict({
        'client_name': data['client_name'],
        'client_email': data.get('client_email', ''),
        'client_phone': data.get('client_phone', ''),
        'client_address': data.get('client_address', ''),
        'invoice_date': data.get('invoice_date', datetime.now().strftime('%Y-%m-%d')),
        'due_date': data.get('due_date', ''),
        'tax_rate': str(data.get('tax_rate', 0)),
        'discount_rate': str(data.get('discount_rate', 0)),
        'delivery_charge': str(data.get('delivery_charge', 0)),
        'invoice_type': 'S'
    })

    for i, item in enumerate(data['items']):
        form_data.add(f'item_name[]', item.get('name', f'Product {item["product_id"]}'))
        form_data.add(f'item_qty[]', str(item['qty']))
        form_data.add(f'item_price[]', str(item['price']))
        form_data.add(f'item_id[]', str(item['product_id']))
        form_data.add(f'item_unit_type[]', item.get('unit_type', 'piece'))

    from app.services.invoice_service import InvoiceService
    service = InvoiceService(user_id)
    invoice_data, errors = service.create_invoice(form_data, files=None)

    if errors:
        return error_response(f"Invoice creation failed: {errors}", "INVOICE_ERROR", 400)
    else:
        return jsonify({"message": "Invoice created", "invoice_number": invoice_data['invoice_number']}), 201
    
# ========== EXPENSES ==========
@api_v1_bp.route('/expenses', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_expenses():
    account_id = g.api_account_id
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.auth import get_expenses_api
    expenses = get_expenses_api(account_id, limit, offset)
    return jsonify(expenses)

@api_v1_bp.route('/expenses', methods=['POST'])
@csrf.exempt
@limiter.limit("10 per minute")
@require_auth
def create_expense():
    account_id = g.api_account_id
    data = request.get_json()
    if not data:
        return error_response("Missing JSON data", "BAD_REQUEST", 400)
    if not data.get('description') or data.get('amount') is None:
        return error_response("description and amount are required", "MISSING_FIELD", 400)

    # Get an owner user_id
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
        if not row:
            return error_response("No owner found for account", "INTERNAL_ERROR", 400)
        user_id = row[0]

    from app.services.auth import create_expense_api
    expense_id = create_expense_api(account_id, user_id, data)
    if expense_id:
        return jsonify({"id": expense_id, "message": "Expense created"}), 201
    else:
        return error_response("Could not create expense", "DATABASE_ERROR", 400)

# ========== PURCHASE ORDERS ==========
@api_v1_bp.route('/purchase_orders', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_purchase_orders():
    account_id = g.api_account_id
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.purchases import get_purchase_orders_api
    orders = get_purchase_orders_api(account_id, limit, offset)
    return jsonify(orders)

@api_v1_bp.route('/purchase_orders/<string:po_number>', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def get_purchase_order(po_number):
    account_id = g.api_account_id
    from app.services.purchases import get_purchase_order_by_number_api
    order = get_purchase_order_by_number_api(account_id, po_number)
    if not order:
        return error_response("Purchase order not found", "NOT_FOUND", 404)
    return jsonify(order)

# ========== STOCK MOVEMENTS ==========
@api_v1_bp.route('/stock_movements', methods=['GET'])
@limiter.limit("100 per minute")
@require_auth
def list_stock_movements():
    account_id = g.api_account_id
    product_id = request.args.get('product_id', type=int)
    limit = request.args.get('limit', default=100, type=int)
    offset = request.args.get('offset', default=0, type=int)
    from app.services.inventory import InventoryManager
    movements = InventoryManager.get_stock_movements(account_id, product_id, limit, offset)
    return jsonify(movements)


# ========== LOCATION MANAGEMENT ==========

@api_v1_bp.route('/locations', methods=['GET'])
@require_auth
def list_locations():
    """Get all locations for the account"""
    account_id = g.api_account_id
    from app.services.location_inventory import LocationInventoryManager
    locations = LocationInventoryManager.get_account_locations(account_id)
    return jsonify(locations)

@api_v1_bp.route('/locations', methods=['POST'])
@csrf.exempt
@require_auth
def create_location():
    """Create a new location"""
    account_id = g.api_account_id
    data = request.get_json()
    
    if not data.get('location_code') or not data.get('location_name'):
        return error_response("location_code and location_name required", "MISSING_FIELD", 400)
    
    from app.services.location_inventory import LocationInventoryManager
    location_id = LocationInventoryManager.create_location(account_id, data)
    
    if location_id:
        return jsonify({"id": location_id, "message": "Location created"}), 201
    else:
        return error_response("Failed to create location", "DATABASE_ERROR", 400)

@api_v1_bp.route('/products/<int:product_id>/locations', methods=['GET'])
@require_auth
def get_product_locations(product_id):
    """Get stock breakdown by location for a product"""
    account_id = g.api_account_id
    
    # Verify product belongs to account
    product = InventoryManager.get_product_details(account_id, product_id)
    if not product:
        return error_response("Product not found", "NOT_FOUND", 404)
    
    from app.services.location_inventory import LocationInventoryManager
    breakdown = LocationInventoryManager.get_product_location_breakdown(product_id)
    return jsonify(breakdown)

@api_v1_bp.route('/locations/<int:location_id>/products', methods=['GET'])
@require_auth
def get_products_by_location_paginated(location_id):
    """Get products in a specific location with pagination and search."""
    account_id = g.api_account_id
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    search = request.args.get('search', '').strip()
    
    with DB_ENGINE.connect() as conn:
        # Verify location belongs to account
        loc_check = conn.execute(text("""
            SELECT id FROM locations WHERE id=:lid AND account_id=:aid AND is_active=TRUE
        """), {"lid": location_id, "aid": account_id}).first()
        if not loc_check:
            return error_response("Location not found", "NOT_FOUND", 404)
        
        base_query = """
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
            base_query += " AND (i.name ILIKE :search OR i.sku ILIKE :search)"
            params["search"] = f"%{search}%"
        
        count_result = conn.execute(text(f"SELECT COUNT(*) FROM ({base_query}) AS sub"), params).scalar()
        total = count_result or 0
        total_pages = (total + per_page - 1) // per_page
        
        offset = (page - 1) * per_page
        query = base_query + " ORDER BY i.name LIMIT :limit OFFSET :offset"
        params["limit"] = per_page
        params["offset"] = offset
        rows = conn.execute(text(query), params).fetchall()
    
    products = []
    for r in rows:
        products.append({
            'id': r[0], 'name': r[1], 'sku': r[2], 'category': r[3],
            'supplier': r[4], 'stock_at_location': float(r[5]) if r[5] else 0,
            'min_stock_level': r[6] or 0, 'cost_price': float(r[7]) if r[7] else 0,
            'selling_price': float(r[8]) if r[8] else 0,
            'default_location': r[9], 'unit_type': r[10] or 'piece'
        })
    return jsonify({'products': products, 'total': total, 'total_pages': total_pages, 'current_page': page})

@api_v1_bp.route('/transfer', methods=['POST'])
@csrf.exempt
@require_auth
def transfer_stock():
    """Transfer stock between locations"""
    account_id = g.api_account_id
    data = request.get_json()
    
    required = ['product_id', 'from_location_id', 'to_location_id', 'quantity']
    for field in required:
        if field not in data:
            return error_response(f"{field} is required", "MISSING_FIELD", 400)
    
    # Get user_id (owner)
    with DB_ENGINE.connect() as conn:
        row = conn.execute(text("""
            SELECT id FROM users WHERE account_id = :aid AND role = 'owner' LIMIT 1
        """), {"aid": account_id}).first()
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
        return jsonify({
            "message": "Transfer completed",
            "transfer_number": transfer_number
        }), 200
    else:
        return error_response("Transfer failed (insufficient stock?)", "TRANSFER_ERROR", 400)
# ========== LOCATION STATISTICS ==========

@api_v1_bp.route('/locations/stats', methods=['GET'])
@require_auth
def get_location_stats():
    """Get stock value and count by location"""
    account_id = g.api_account_id
    
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT 
                l.id,
                l.location_name,
                l.location_code,
                l.location_type,
                COUNT(DISTINCT pl.product_id) as product_count,
                COALESCE(SUM(pl.quantity), 0) as total_units,
                COALESCE(SUM(pl.quantity * i.cost_price), 0) as total_value
            FROM locations l
            LEFT JOIN product_locations pl ON l.id = pl.location_id
            LEFT JOIN inventory_items i ON pl.product_id = i.id AND i.is_active = TRUE
            WHERE l.account_id = :aid AND l.is_active = TRUE
            GROUP BY l.id, l.location_name, l.location_code, l.location_type
            ORDER BY total_value DESC
        """), {"aid": account_id}).fetchall()
    
    result = []
    for r in rows:
        result.append({
            'id': r[0],
            'location_name': r[1],
            'location_code': r[2],
            'type': r[3],
            'product_count': r[4],
            'total_units': float(r[5]) if r[5] else 0,
            'total_value': float(r[6]) if r[6] else 0
        })
    return jsonify(result)

@api_v1_bp.route('/inventory/low-stock', methods=['GET'])
@require_auth
def get_low_stock_api():
    """Get low stock alerts"""
    account_id = g.api_account_id
    items = InventoryManager.get_low_stock_alerts(account_id)
    return jsonify(items)

@api_v1_bp.route('/stock-movements/recent', methods=['GET'])
@require_auth
def get_recent_movements():
    """Get recent stock movements from location_transfers"""
    account_id = g.api_account_id
    limit = request.args.get('limit', default=20, type=int)
    
    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT 
                lt.created_at,
                i.name as product_name,
                lt.from_location_id,
                lt.to_location_id,
                lt.quantity,
                lt.status,
                fl.location_name as from_location,
                tl.location_name as to_location
            FROM location_transfers lt
            JOIN inventory_items i ON lt.product_id = i.id
            LEFT JOIN locations fl ON lt.from_location_id = fl.id
            LEFT JOIN locations tl ON lt.to_location_id = tl.id
            WHERE lt.account_id = :aid AND i.is_active = TRUE
            ORDER BY lt.created_at DESC
            LIMIT :limit
        """), {"aid": account_id, "limit": limit}).fetchall()
    
    result = []
    for r in rows:
        result.append({
            'created_at': r[0].isoformat() if r[0] else None,
            'product_name': r[1],
            'from_location_id': r[2],
            'to_location_id': r[3],
            'quantity': float(r[4]) if r[4] else 0,
            'status': r[5],
            'from_location': r[6],
            'to_location': r[7]
        })
    return jsonify(result)

@api_v1_bp.route('/locations/<int:location_id>', methods=['PUT'])
@csrf.exempt
@require_auth
def update_location(location_id):
    """Update a location (name, code, type, address)."""
    account_id = g.api_account_id
    data = request.get_json()
    with DB_ENGINE.begin() as conn:
        loc = conn.execute(text("SELECT id FROM locations WHERE id=:lid AND account_id=:aid"), 
                           {"lid": location_id, "aid": account_id}).first()
        if not loc:
            return error_response("Location not found", "NOT_FOUND", 404)
        conn.execute(text("""
            UPDATE locations 
            SET location_name = :name, location_code = :code, location_type = :type, 
                address = :address, updated_at = NOW()
            WHERE id = :lid
        """), {
            "name": data.get('location_name'),
            "code": data.get('location_code'),
            "type": data.get('location_type'),
            "address": data.get('address'),
            "lid": location_id
        })
    return jsonify({"message": "Location updated"})

@api_v1_bp.route('/locations/<int:location_id>', methods=['DELETE'])
@csrf.exempt
@require_auth
def delete_location(location_id):
    """Delete a location and all its product assignments."""
    account_id = g.api_account_id
    with DB_ENGINE.begin() as conn:
        loc = conn.execute(text("SELECT id FROM locations WHERE id=:lid AND account_id=:aid"), 
                           {"lid": location_id, "aid": account_id}).first()
        if not loc:
            return error_response("Location not found", "NOT_FOUND", 404)
        # Delete product_locations entries first
        conn.execute(text("DELETE FROM product_locations WHERE location_id = :lid"), {"lid": location_id})
        conn.execute(text("DELETE FROM locations WHERE id = :lid"), {"lid": location_id})
    return jsonify({"message": "Location deleted"})
