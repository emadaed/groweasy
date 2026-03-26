# app/routes/api_v1.py
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
def list_inventory():
    """
    List all inventory items for the account
    ---
    tags:
      - Inventory
    security:
      - Bearer: []
    responses:
      200:
        description: A list of inventory items
        schema:
          type: array
          items:
            $ref: '#/definitions/InventoryItem'
    """
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


# Swagger definitions
api_v1_bp.swag = {
    'definitions': {
        'InventoryItem': {
            'type': 'object',
            'properties': {
                'id': {'type': 'integer'},
                'name': {'type': 'string'},
                'sku': {'type': 'string'},
                'current_stock': {'type': 'number'},
                'cost_price': {'type': 'number'},
                'selling_price': {'type': 'number'},
                'category': {'type': 'string'},
                'supplier': {'type': 'string'},
                'location': {'type': 'string'}
            }
        },
        'Customer': {
            'type': 'object',
            'properties': {
                'id': {'type': 'integer'},
                'name': {'type': 'string'},
                'email': {'type': 'string'},
                'phone': {'type': 'string'},
                'address': {'type': 'string'},
                'tax_id': {'type': 'string'},
                'total_spent': {'type': 'number'},
                'invoice_count': {'type': 'integer'}
            }
        },
        'Invoice': {
            'type': 'object',
            'properties': {
                'id': {'type': 'integer'},
                'invoice_number': {'type': 'string'},
                'client_name': {'type': 'string'},
                'invoice_date': {'type': 'string', 'format': 'date'},
                'due_date': {'type': 'string', 'format': 'date'},
                'grand_total': {'type': 'number'},
                'status': {'type': 'string'},
                'created_at': {'type': 'string', 'format': 'date-time'}
            }
        }
    }
}
