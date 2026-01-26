def validate_items(items):
    """Server-side validation for invoice item list."""
    errors = []
    for idx, item in enumerate(items):
        name = (item.get("name") or item.get("code") or "").strip()
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        if name and (qty <= 0 or price <= 0):
            errors.append(f"Row {idx+1}: quantity and price are required.")
        elif not name and (qty > 0 or price > 0):
            errors.append(f"Row {idx+1}: item name or code is required.")
    return errors
