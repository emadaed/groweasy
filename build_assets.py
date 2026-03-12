import os
import cssmin
import jsmin

# Define input files (same as your Flask-Assets bundles)
CSS_FILES = [
    'static/css/bootstrap.min.css',
    'static/css/custom.css',
    'static/css/invoice.css',
    'static/css/theme.css',
    'static/css/base.css'
]

JS_FILES = [
    'static/js/bootstrap.bundle.min.js',
    'static/js/form.js',
    'static/js/form_items.js',
    'static/js/groweasy_toast.js',
    'static/js/invoice.js'
]

OUTPUT_CSS = 'static/dist/css/all.min.css'
OUTPUT_JS = 'static/dist/js/all.min.js'

def ensure_dir(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

def build_css():
    print("Building CSS bundle...")
    combined = ""
    for f in CSS_FILES:
        if not os.path.exists(f):
            print(f"⚠️ Warning: {f} not found, skipping")
            continue
        with open(f, 'r', encoding='utf-8') as file:
            combined += file.read() + "\n"
    minified = cssmin.cssmin(combined)
    ensure_dir(OUTPUT_CSS)
    with open(OUTPUT_CSS, 'w', encoding='utf-8') as out:
        out.write(minified)
    print(f"✅ CSS bundle created at {OUTPUT_CSS}")

def build_js():
    print("Building JS bundle...")
    combined = ""
    for f in JS_FILES:
        if not os.path.exists(f):
            print(f"⚠️ Warning: {f} not found, skipping")
            continue
        with open(f, 'r', encoding='utf-8') as file:
            combined += file.read() + "\n"
    minified = jsmin.jsmin(combined)
    ensure_dir(OUTPUT_JS)
    with open(OUTPUT_JS, 'w', encoding='utf-8') as out:
        out.write(minified)
    print(f"✅ JS bundle created at {OUTPUT_JS}")

if __name__ == "__main__":
    build_css()
    build_js()
    print("🎉 Asset build complete!")
