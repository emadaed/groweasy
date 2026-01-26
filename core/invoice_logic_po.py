# core/invoice_logic_po.py
def prepare_po_data(form_data, files=None):
    """Prepare PO data - supports item_id[], item_qty[], item_price[] format"""
    from datetime import datetime

    # Basic info
    po_data = {
        'supplier_name': form_data.get('supplier_name', '') or 'Unknown Supplier',
        'contact_person': form_data.get('contact_person', ''),
        'supplier_phone': form_data.get('supplier_phone', ''),
        'supplier_email': form_data.get('supplier_email', ''),
        'supplier_address': form_data.get('supplier_address', ''),
        'supplier_tax_id': form_data.get('supplier_tax_id', ''),
        'supplier_payment_terms': form_data.get('supplier_payment_terms', 'Net 30'),
        'po_date': form_data.get('po_date') or datetime.now().strftime('%Y-%m-%d'),
        'delivery_date': form_data.get('delivery_date') or '',
        'delivery_method': form_data.get('delivery_method', 'Pickup'),
        'shipping_terms': form_data.get('shipping_terms', 'FOB Destination'),
        'po_notes': form_data.get('po_notes', ''),
        'internal_notes': form_data.get('internal_notes', ''),
        'buyer_ntn': form_data.get('buyer_ntn', ''),
        'seller_ntn': form_data.get('seller_ntn', ''),
        'shipping_cost': float(form_data.get('shipping_cost', 0)),
        'insurance_cost': float(form_data.get('insurance_cost', 0)),
        'invoice_type': 'P',
        'items': []
    }

    # Extract items from new format
    items = []
    item_ids = form_data.getlist('item_id[]')
    item_qtys = form_data.getlist('item_qty[]')
    item_prices = form_data.getlist('item_price[]')

    for i in range(len(item_ids)):
        if item_ids[i]:  # Only if product selected
            qty = int(item_qtys[i]) if i < len(item_qtys) else 1
            price = float(item_prices[i]) if i < len(item_prices) else 0.0
            items.append({
                'product_id': item_ids[i],
                'name': f"Product {item_ids[i]}",  # Will be replaced in template if needed
                'qty': qty,
                'price': price,
                'total': qty * price
            })

    if not items:
        raise ValueError("At least one item is required for purchase order")

    subtotal = sum(item['total'] for item in items)
    tax_rate = float(form_data.get('sales_tax', 17))
    tax_amount = subtotal * (tax_rate / 100)
    grand_total = subtotal + tax_amount + po_data['shipping_cost'] + po_data['insurance_cost']

    po_data.update({
        'items': items,
        'subtotal': subtotal,
        'tax_rate': tax_rate,
        'tax_amount': tax_amount,
        'grand_total': grand_total
    })

    return po_data
