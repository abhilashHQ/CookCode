set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("invoice.py"); s = p.read_text()
s = s.replace("from money import fmt", "from money import fmt, percent")
s = s.replace("    def total(self):\n        return self.subtotal()\n",
              "    def fee(self):\n        return percent(self.subtotal(), 5) if self.subtotal() > 10000 else 0\n\n"
              "    def total(self):\n        return self.subtotal() + self.fee()\n")
s = s.replace('        return "\\n".join([*rows, f"Total: {fmt(self.total())}"])',
              '        if self.fee():\n            rows.append(f"Service fee: {fmt(self.fee())}")\n'
              '        return "\\n".join([*rows, f"Total: {fmt(self.total())}"])')
p.write_text(s)
EOF
