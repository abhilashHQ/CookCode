set -euo pipefail
echo "ALTER TABLE customers ADD COLUMN phone TEXT;" > migrations/002_customer_phone.sql
sed -i 's/^SCHEMA_VERSION = 1$/SCHEMA_VERSION = 2/' db.py
