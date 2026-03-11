import csv
import random
import string

NUM_ROWS = 1000
FILENAME = "test_products_1000.csv"

def random_sku(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def random_name():
    adjectives = ['Pro', 'Max', 'Ultra', 'Basic', 'Premium', 'Eco', 'Smart', 'Quick', 'Durable', 'Compact']
    nouns = ['Widget', 'Gadget', 'Tool', 'Device', 'Component', 'Supply', 'Material', 'Part', 'Accessory', 'Unit']
    return f"{random.choice(adjectives)} {random.choice(nouns)}"

def random_category():
    return random.choice(['Electronics', 'Office Supplies', 'Furniture', 'Hardware', 'Software', 'Services', 'Consumables'])

def random_description():
    return random.choice(['', 'High quality', 'Customer favorite', 'Best seller', 'New arrival', 'Limited edition', ''])

def random_supplier():
    return random.choice(['Acme Corp', 'Global Supplies', 'Tech Distributors', 'Local Vendor', 'Import Direct', ''])

def random_location():
    return random.choice(['Warehouse A', 'Warehouse B', 'Shelf 1', 'Shelf 2', 'Backroom', ''])

with open(FILENAME, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow(['name', 'sku', 'category', 'description', 'current_stock',
                     'min_stock_level', 'cost_price', 'selling_price', 'supplier', 'location'])

    for i in range(NUM_ROWS):
        sku = random_sku(6) + f"{i:04d}"
        name = random_name()
        category = random_category()
        description = random_description()
        current_stock = random.randint(0, 500)
        min_stock = random.randint(5, 50)
        cost = round(random.uniform(5, 200), 2)
        price = round(cost * random.uniform(1.1, 2.5), 2)
        supplier = random_supplier()
        location = random_location()

        writer.writerow([name, sku, category, description, current_stock,
                         min_stock, cost, price, supplier, location])

print(f"✅ Generated {NUM_ROWS} rows in {FILENAME}")
