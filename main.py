# ============================================================================
# main.py - COMPLETE FIXED VERSION 29--01-2026 06-02-2026 Refactoring done.
# ============================================================================
from flask import render_template, session, redirect, url_for, request, flash, jsonify, g, send_file, make_response, current_app
import os
from app import create_app


#config.py need to transfer it. currently its in app/__init__.py and app/routes.purchases.py

##CURRENCY_SYMBOLS = {'PKR': 'Rs.', 'USD': '$', 'EUR': '€', 'GBP': '£', 'AED': 'د.إ', 'SAR': '﷼'}
#also limiter need to be transfered to extention.py from app/__init__.py

app = create_app()



if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))
    
