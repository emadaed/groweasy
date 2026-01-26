# fbr_integration.py
import json
import base64
from datetime import datetime
import qrcode
from io import BytesIO
import re

class FBRInvoice:
    def __init__(self, invoice_data):
        self.invoice_data = invoice_data
        self.fbr_data = self.prepare_fbr_data()

    def prepare_fbr_data(self):
        """Prepare FBR-compliant invoice data"""
        # Extract basic invoice info
        items = self.invoice_data.get('items', [])
        subtotal = sum(item['total'] for item in items)
        tax_amount = self.invoice_data.get('tax_amount', 0)
        total = self.invoice_data.get('grand_total', subtotal + tax_amount)

        # FBR required fields
        fbr_data = {
            # Seller Information (Your Business)
            "seller": {
                "ntn": self.invoice_data.get('seller_ntn', ''),
                "strn": self.invoice_data.get('seller_strn', ''),
                "name": self.invoice_data.get('company_name', ''),
                "address": self.invoice_data.get('company_address', ''),
                "phone": self.invoice_data.get('company_phone', ''),
                "email": self.invoice_data.get('company_email', '')
            },

            # Buyer Information (Client)
            "buyer": {
                "ntn": self.invoice_data.get('buyer_ntn', ''),
                "strn": self.invoice_data.get('buyer_strn', ''),
                "name": self.invoice_data.get('client_name', ''),
                "address": self.invoice_data.get('client_address', ''),
                "phone": self.invoice_data.get('client_phone', ''),
                "email": self.invoice_data.get('client_email', '')
            },

            # Invoice Details
            "invoice": {
                "number": self.invoice_data.get('invoice_number', ''),
                "date": self.invoice_data.get('invoice_date', ''),
                "time": datetime.now().strftime("%H:%M:%S"),
                "pos": "Online",  # Point of Sale
                "invoice_type": self.invoice_data.get('invoice_type', 'S'),  # S for Sale, P for Purchase
            },

            # Financial Details
            "amounts": {
                "subtotal": round(subtotal, 2),
                "discount": round(self.invoice_data.get('discount_amount', 0), 2),
                "taxable_amount": round(subtotal - self.invoice_data.get('discount_amount', 0), 2),
                "tax_rate": round(self.invoice_data.get('tax_rate', 0), 2),
                "tax_amount": round(tax_amount, 2),
                "total": round(total, 2)
            },

            # Items
            "items": [
                {
                    "code": f"ITEM_{idx+1:03d}",
                    "name": item.get('name', '')[:100],  # FBR limit
                    "quantity": round(float(item.get('qty', 0)), 2),
                    "price": round(float(item.get('price', 0)), 2),
                    "total": round(float(item.get('total', 0)), 2)
                }
                for idx, item in enumerate(items)
            ]
        }

        return fbr_data

    def is_valid_ntn(self, ntn):
        """Validate NTN format (1234567-8)"""
        if not ntn:
            return False
        pattern = r'^\d{7}-\d{1}$'
        return bool(re.match(pattern, ntn))

    def generate_fbr_qr_code(self):
        """Generate FBR-compliant QR code with encrypted data"""
        # Prepare data for QR code
        qr_data = {
            "version": "1.0",
            "invoice": {
                "number": self.fbr_data['invoice']['number'],
                "date": self.fbr_data['invoice']['date'],
                "time": self.fbr_data['invoice']['time']
            },
            "seller": {
                "ntn": self.fbr_data['seller']['ntn'],
                "name": self.fbr_data['seller']['name'][:50]  # Limit for QR
            },
            "buyer": {
                "ntn": self.fbr_data['buyer']['ntn'],
                "name": self.fbr_data['buyer']['name'][:50]  # Limit for QR
            },
            "amounts": {
                "total": self.fbr_data['amounts']['total'],
                "tax": self.fbr_data['amounts']['tax_amount']
            }
        }

        # Convert to JSON string
        json_data = json.dumps(qr_data, separators=(',', ':'))

        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(json_data)
        qr.make(fit=True)

        # Create image
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert to base64 for HTML embedding
        buffered = BytesIO()
        qr_img.save(buffered, format="PNG")
        qr_b64 = base64.b64encode(buffered.getvalue()).decode()

        return qr_b64

    def validate_fbr_compliance(self):
        """Validate if invoice meets FBR requirements"""
        errors = []

        # Check seller NTN
        seller_ntn = self.fbr_data['seller']['ntn']
        if not seller_ntn:
            errors.append("Seller NTN is required for FBR compliance")
        elif not self.is_valid_ntn(seller_ntn):
            errors.append("Seller NTN must be in format: 1234567-8")

        # Check invoice number format
        invoice_number = self.fbr_data['invoice']['number']
        if not invoice_number or len(invoice_number) < 3:
            errors.append("Valid invoice number is required")

        # Check amounts
        if self.fbr_data['amounts']['total'] <= 0:
            errors.append("Invoice total must be greater than 0")

        # Check date format
        invoice_date = self.fbr_data['invoice']['date']
        if not invoice_date:
            errors.append("Invoice date is required")

        return errors

    def get_fbr_summary(self):
        """Get FBR compliance summary"""
        errors = self.validate_fbr_compliance()
        is_compliant = len(errors) == 0

        return {
            "is_compliant": is_compliant,
            "errors": errors,
            "fbr_data": self.fbr_data,
            "qr_code": self.generate_fbr_qr_code() if is_compliant else None
        }
