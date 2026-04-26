# app/services/invoice_logic.py
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from app.services.utils import process_uploaded_logo

# Consistent rounding for all monetary values (2 decimal places)
TWOPLACES = Decimal('0.01')


def _d(value, default='0') -> Decimal:
    """
    Safely convert a form string value to Decimal.
    Avoids float() entirely — Decimal(float(x)) inherits float rounding errors.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


def _round(value: Decimal) -> Decimal:
    """Round to 2 decimal places using standard half-up rounding."""
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def prepare_invoice_data(form_data, files=None):
    """
    Parse and validate invoice form data.
    Returns a dict with all monetary values as plain floats for JSON
    serialisation (Decimal is not JSON-serialisable), but all *arithmetic*
    is done in Decimal before conversion.
    """
    items = []
    item_names  = form_data.getlist('item_name[]')
    item_qtys   = form_data.getlist('item_qty[]')
    item_prices = form_data.getlist('item_price[]')
    item_ids    = form_data.getlist('item_id[]')
    item_units  = form_data.getlist('item_unit_type[]')

    # All four arrays must be the same length
    lengths = {len(item_names), len(item_qtys), len(item_prices), len(item_ids), len(item_units)}
    if len(lengths) != 1:
        raise ValueError(
            f"Array length mismatch: names={len(item_names)}, qtys={len(item_qtys)}, "
            f"prices={len(item_prices)}, ids={len(item_ids)}, units={len(item_units)}"
        )

    for i in range(len(item_names)):
        if not item_names[i].strip():
            continue

        qty   = _d(item_qtys[i])
        price = _d(item_prices[i])
        product_id = item_ids[i] if item_ids[i] else None
        unit_type  = item_units[i] if i < len(item_units) else 'piece'

        if not product_id:
            raise ValueError(
                f"Item '{item_names[i]}' has no product_id — all items must come from inventory"
            )
        if qty <= 0:
            raise ValueError(f"Quantity for '{item_names[i]}' must be greater than 0")

        line_total = _round(qty * price)
        items.append({
            'name':       item_names[i],
            'qty':        float(qty),        # JSON-safe
            'price':      float(price),
            'total':      float(line_total),
            'product_id': product_id,
            'unit_type':  unit_type,
        })

    if not items:
        raise ValueError("Invoice must have at least one item")

    # All arithmetic in Decimal
    subtotal        = _round(sum(Decimal(str(item['total'])) for item in items))
    tax_rate        = _d(form_data.get('tax_rate', '0'))
    discount_rate   = _d(form_data.get('discount_rate', '0'))
    delivery_charge = _d(form_data.get('delivery_charge', '0'))

    discount_amount = _round(subtotal * discount_rate / 100)
    taxable_amount  = subtotal - discount_amount
    tax_amount      = _round(taxable_amount * tax_rate / 100)
    grand_total     = _round(subtotal - discount_amount + tax_amount + delivery_charge)

    # Logo handling — unchanged
    logo_b64 = None
    if files and 'logo' in files and files['logo'].filename:
        try:
            logo_b64 = process_uploaded_logo(
                files['logo'], max_kb=300, max_width=200, max_height=200
            )
        except ValueError as e:
            raise ValueError(f"Logo upload failed: {e}")
        except Exception:
            raise ValueError("Failed to process logo image")

    invoice_data = {
        'items':           items,
        'subtotal':        float(subtotal),
        'tax_rate':        float(tax_rate),
        'tax_amount':      float(tax_amount),
        'discount_rate':   float(discount_rate),
        'discount_amount': float(discount_amount),
        'delivery_charge': float(delivery_charge),
        'grand_total':     float(grand_total),
        'invoice_number':  form_data.get('invoice_number', 'INV-00001'),
        'invoice_date':    form_data.get('invoice_date', ''),
        'client_name':     form_data.get('client_name', ''),
        'client_email':    form_data.get('client_email', ''),
        'client_phone':    form_data.get('client_phone', ''),
        'client_address':  form_data.get('client_address', ''),
        'company_name':    form_data.get('company_name', 'Your Company Name'),
        'company_address': form_data.get('company_address', '123 Business Street'),
        'company_phone':   form_data.get('company_phone', ''),
        'company_email':   form_data.get('company_email', ''),
        'company_tax_id':  form_data.get('company_tax_id', ''),
        'due_date':        form_data.get('due_date', ''),
        'payment_terms':   form_data.get('payment_terms', 'Due upon receipt'),
        'payment_methods': form_data.get('payment_methods', 'Bank Transfer'),
        'notes':           form_data.get('notes', ''),
        'seller_ntn':      form_data.get('seller_ntn', ''),
        'seller_strn':     form_data.get('seller_strn', ''),
        'buyer_ntn':       form_data.get('buyer_ntn', ''),
        'buyer_strn':      form_data.get('buyer_strn', ''),
        'invoice_type':    form_data.get('invoice_type', 'S'),
        'logo_b64':        logo_b64,
    }

    # Invoice-type-specific overrides
    invoice_type = invoice_data['invoice_type']
    if invoice_type == 'P':
        for item in items:
            item['is_purchase'] = True
    elif invoice_type == 'E':
        if not invoice_data['seller_ntn']:
            raise ValueError("Exporter NTN is required for export invoices")
        export_total = _round(subtotal - discount_amount + delivery_charge)
        invoice_data['tax_rate']    = 0.0
        invoice_data['tax_amount']  = 0.0
        invoice_data['grand_total'] = float(export_total)

    return invoice_data
