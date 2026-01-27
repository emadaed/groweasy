import os
from flask import Flask
from pathlib import Path

def create_app():
    # 1. Initialize the Flask object
    app = Flask(__name__)
    
    # 2. Setup Paths (The logic we just tested!)
    app_root = Path(__file__).parent
    app.template_folder = str(app_root / "templates")
    app.static_folder = str(app_root / "static")

    # 3. Load Configs (Secret Key, etc.)
    app.secret_key = os.getenv('SECRET_KEY', 'dev-key-for-local-only')

    # 4. Initialize Services (We will move these here one by one)
    # from app.services.cache import init_cache
    # init_cache(app)

    return app
