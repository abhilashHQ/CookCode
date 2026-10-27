set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("invoice.py"); s = p.read_text()
s = s.replace("from money import fmt", "from money import fmt, percent")
s = s.replace("        self.lines = []\n", "        self.lines = []\n        self.discount = 0\n", 1)
s = s.replace("    def total(self):\n        return self.subtotal()\n",
              "    def apply_code(self, code):\n        if code != \"SAVE10\":\n            raise ValueError(code)\n"
              "        self.discount = percent(self.subtotal(), 10)\n\n"
              "    def total(self):\n        return self.subtotal() - self.discount\n")
s = s.replace('        return "\\n".join([*rows, f"Total: {fmt(self.total())}"])',
              '        if self.discount:\n            rows.append(f"Discount: -{fmt(self.discount)}")\n'
              '        return "\\n".join([*rows, f"Total: {fmt(self.total())}"])')
p.write_text(s)
EOF
