# ============================================================================
# main.py - COMPLETE FIXED VERSION 29--01-2026 12:23 AM
# ============================================================================
from flask import render_template, session, redirect, url_for, request, flash, jsonify, g, send_file, make_response, current_app

# Import the Factory and Global Extensions
import os
from app import create_app

# Local application
from fbr_integration import FBRInvoice


#config.py need to transfer it. currently its in app/__init__.py and app/routes.purchases.py

##CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}
#also limiter need to be transfered to extention.py from app/__init__.py

app = create_app()



if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
    
    #app.run(host="0.0.0.0", port=8080, debug=False)
