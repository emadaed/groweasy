# list_routes.py
from app import create_app  # Adjust import based on your app factory
from flask import url_for

app = create_app()  # This creates your app instance

with app.app_context():
    print("\n=== All Registered Routes ===")
    for rule in app.url_map.iter_rules():
        # Filter out static routes and OPTIONS methods if you want
        if "static" in rule.endpoint:
            continue
        methods = ','.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
        print(f"URL: {rule} | Methods: {methods} | Endpoint: {rule.endpoint}")
    print("=============================\n")
