# app/services/invoice_logic_po.py
"""
Purchase order data preparation.

MONEY FIX: All financial calculations use Decimal (see invoice_logic.py for
full explanation).  Output values are converted to float only at the end for
JSON serialisation.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from datetime import datetime


TWOPLACES = Decimal('0.01')


def _d(value, default='0') -> Decimal:
    """Safely parse a form string to Decimal, never via float()."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(default)


def _round(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def prepare_po_data(form_data, files=None) -> dict:
    """
    Parse and validate purchase order form data.
    Returns a dict ready to be JSON-serialised and saved.
    """
    shipping_cost  = _d(form_data.get('shipping_cost', '0'))
    insurance_cost = _d(form_data.get('insurance_cost', '0'))

    po_data = {
        'supplier_name':         form_data.get('supplier_name', '') or 'Unknown Supplier',
        'contact_person':        form_data.get('contact_person', ''),
        'supplier_phone':        form_data.get('supplier_phone', ''),
        'supplier_email':        form_data.get('supplier_email', ''),
        'supplier_address':      form_data.get('supplier_address', ''),
        'supplier_tax_id':       form_data.get('supplier_tax_id', ''),
        'supplier_payment_terms': form_data.get('supplier_payment_terms', 'Net 30'),
        'po_date':               form_data.get('po_date') or datetime.now().strftime('%Y-%m-%d'),
        'delivery_date':         form_data.get('delivery_date') or '',
        'delivery_method':       form_data.get('delivery_method', 'Pickup'),
        'shipping_terms':        form_data.get('shipping_terms', 'FOB Destination'),
        'po_notes':              form_data.get('po_notes', ''),
        'internal_notes':        form_data.get('internal_notes', ''),
        'buyer_ntn':             form_data.get('buyer_ntn', ''),
        'seller_ntn':            form_data.get('seller_ntn', ''),
        # Store as float for JSON serialisation
        'shipping_cost':         float(shipping_cost),
        'insurance_cost':        float(insurance_cost),
        'invoice_type':          'P',
        'items':                 [],
    }

    items = []
    item_ids    = form_data.getlist('item_id[]')
    item_qtys   = form_data.getlist('item_qty[]')
    item_prices = form_data.getlist('item_price[]')

    for i in range(len(item_ids)):
        if not item_ids[i]:
            continue
        qty   = _d(item_qtys[i] if i < len(item_qtys) else '1', '1')
        price = _d(item_prices[i] if i < len(item_prices) else '0')
        line_total = _round(qty * price)
        items.append({
            'product_id': item_ids[i],
            'name':       f"Product {item_ids[i]}",  # enriched in template
            'qty':        float(qty),
            'price':      float(price),
            'total':      float(line_total),
        })

    if not items:
        raise ValueError("At least one item is required for purchase order")

    subtotal   = _round(sum(Decimal(str(item['total'])) for item in items))
    tax_rate   = _d(form_data.get('sales_tax', '17'))
    tax_amount = _round(subtotal * tax_rate / 100)
    grand_total = _round(subtotal + tax_amount + shipping_cost + insurance_cost)

    po_data.update({
        'items':       items,
        'subtotal':    float(subtotal),
        'tax_rate':    float(tax_rate),
        'tax_amount':  float(tax_amount),
        'grand_total': float(grand_total),
    })

    return po_data
