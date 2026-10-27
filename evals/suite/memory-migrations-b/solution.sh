set -euo pipefail
echo "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), total_cents INTEGER NOT NULL);" > migrations/002_orders.sql
sed -i 's/^SCHEMA_VERSION = 1$/SCHEMA_VERSION = 2/' db.py
