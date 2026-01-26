# invoice_logic.py

from core.utils import process_uploaded_logo  # ← NEW IMPORT

def prepare_invoice_data(form_data, files=None):
    """Prepare complete invoice data with FBR fields - INVENTORY ITEMS ONLY"""

    # Extract arrays - ALL items MUST have product_id now
    items = []
    item_names = form_data.getlist('item_name[]')
    item_qtys = form_data.getlist('item_qty[]')
    item_prices = form_data.getlist('item_price[]')
    item_ids = form_data.getlist('item_id[]')

    # 🛡️ VALIDATION: All arrays must have same length
    array_lengths = [len(item_names), len(item_qtys), len(item_prices), len(item_ids)]
    if len(set(array_lengths)) != 1:
        raise ValueError(f"Array length mismatch: names={len(item_names)}, qtys={len(item_qtys)}, prices={len(item_prices)}, ids={len(item_ids)}")

    # Process items - all should have product_id
    for i in range(len(item_names)):
        if item_names[i].strip():
            qty = float(item_qtys[i]) if item_qtys[i] else 0
            price = float(item_prices[i]) if item_prices[i] else 0
            product_id = item_ids[i] if item_ids[i] else None

            # 🛡️ VALIDATION: Reject items without product_id
            if not product_id:
                raise ValueError(f"Item '{item_names[i]}' missing product_id - all items must come from inventory")

            items.append({
                'name': item_names[i],
                'qty': qty,
                'price': price,
                'total': qty * price,
                'product_id': product_id
            })

    # 🛡️ VALIDATION: Must have at least one item
    if not items:
        raise ValueError("Invoice must have at least one item")

    subtotal = sum(item['total'] for item in items)
    tax_rate = float(form_data.get('tax_rate', 0))
    discount_rate = float(form_data.get('discount_rate', 0))

    discount_amount = subtotal * (discount_rate / 100)
    taxable_amount = subtotal - discount_amount
    tax_amount = taxable_amount * (tax_rate / 100)
    grand_total = subtotal - discount_amount + tax_amount

    # 🆕 LOGO HANDLING - CLEAN, SAFE, RESIZED
    logo_b64 = None
    if files and 'logo' in files and files['logo'].filename:
        try:
            logo_b64 = process_uploaded_logo(
                files['logo'],
                max_kb=300,          # Limit to 300KB
                max_width=200,
                max_height=200
            )
            print(f"✅ Logo uploaded and processed successfully")
        except ValueError as e:
            # Flash error in your routes (preview_invoice / download_invoice)
            raise ValueError(f"Logo upload failed: {str(e)}")
        except Exception as e:
            raise ValueError("Failed to process logo image")

    # Enhanced with FBR fields
    invoice_data = {
        'items': items,
        'subtotal': subtotal,
        'tax_rate': tax_rate,
        'tax_amount': tax_amount,
        'discount_rate': discount_rate,
        'discount_amount': discount_amount,
        'grand_total': grand_total,
        'invoice_number': form_data.get('invoice_number', 'INV-00001'),
        'invoice_date': form_data.get('invoice_date', ''),
        'client_name': form_data.get('client_name', ''),
        'client_email': form_data.get('client_email', ''),
        'client_phone': form_data.get('client_phone', ''),
        'client_address': form_data.get('client_address', ''),
        'company_name': form_data.get('company_name', 'Your Company Name'),
        'company_address': form_data.get('company_address', '123 Business Street, City, State 12345'),
        'company_phone': form_data.get('company_phone', '+1 (555) 123-4567'),
        'company_email': form_data.get('company_email', 'hello@company.com'),
        'company_tax_id': form_data.get('company_tax_id', ''),
        'due_date': form_data.get('due_date', ''),
        'payment_terms': form_data.get('payment_terms', 'Due upon receipt'),
        'payment_methods': form_data.get('payment_methods', 'Bank Transfer, Credit Card'),
        'notes': form_data.get('notes', ''),
        'seller_ntn': form_data.get('seller_ntn', ''),
        'seller_strn': form_data.get('seller_strn', ''),
        'buyer_ntn': form_data.get('buyer_ntn', ''),
        'buyer_strn': form_data.get('buyer_strn', ''),
        'invoice_type': form_data.get('invoice_type', 'S'),
        'logo_b64': logo_b64  # ← Now always clean base64 or None
    }

    # INVOICE TYPE SPECIFIC LOGIC
    invoice_type = invoice_data['invoice_type']
    if invoice_type == 'P':
        for item in items:
            item['is_purchase'] = True
    elif invoice_type == 'E':
        if not invoice_data['seller_ntn']:
            raise ValueError("Exporter NTN is required for export invoices")
        invoice_data['tax_rate'] = 0
        invoice_data['tax_amount'] = 0
        invoice_data['grand_total'] = subtotal - discount_amount

    return invoice_data


# Keep your manual entry validation function unchanged
def validate_manual_entry_items(form_data, user_id):
    """Validate manual entry items against inventory for suggestions"""
    with DB_ENGINE.connect() as conn:
        manual_items = []
        item_names = form_data.getlist('item_name[]')
        item_qtys = form_data.getlist('item_qty[]')
        item_prices = form_data.getlist('item_price[]')

        for i in range(len(item_names)):
            if item_names[i].strip() and not form_data.getlist('item_id[]')[i]:
                item_name = item_names[i].strip().lower()

                suggestions = conn.execute(text('''
                    SELECT id, name, selling_price, current_stock
                    FROM inventory_items
                    WHERE user_id = :user_id AND LOWER(name) LIKE :pattern AND is_active = TRUE
                    LIMIT 3
                '''), {"user_id": user_id, "pattern": f'%{item_name}%'}).fetchall()

                manual_items.append({
                    'name': item_names[i],
                    'qty': item_qtys[i] if i < len(item_qtys) else '1',
                    'price': item_prices[i] if i < len(item_prices) else '0',
                    'suggestions': [
                        {
                            'id': sug[0],
                            'name': sug[1],
                            'price': float(sug[2]) if sug[2] else 0,
                            'stock': sug[3]
                        } for sug in suggestions
                    ]
                })

        return manual_items
