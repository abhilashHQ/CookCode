set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("inventory.py"); s = p.read_text()
s = s.replace("    def restock(self, item, qty):\n", "    def restock(self, item, qty):\n        if qty <= 0:\n            raise ValueError(\"quantity must be positive\")\n")
p.write_text(s)
EOF
