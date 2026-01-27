"""Storage layer (placeholder for future expansion)."""

def save_local(path, data_bytes):
    with open(path, "wb") as f:
        f.write(data_bytes)
