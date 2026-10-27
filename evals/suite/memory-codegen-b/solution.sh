set -euo pipefail
python3 - <<'EOF'
import json; from pathlib import Path
p = Path("schema.json"); s = json.loads(p.read_text()); s["Product"] = ["sku", "price"]
p.write_text(json.dumps(s, indent=2) + "\n")
EOF
python3 gen.py
