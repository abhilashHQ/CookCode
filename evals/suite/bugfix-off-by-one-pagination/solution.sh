set -euo pipefail
sed -i 's/start = page \* per_page/start = (page - 1) * per_page/' pagination.py
