set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("pricing.py")
s = p.read_text()
s = s.replace(
    'CENTS = Decimal("0.01")\n',
    'CENTS = Decimal("0.01")\n\n\n'
    "def round_money(amount):\n"
    "    return Decimal(amount).quantize(CENTS, rounding=ROUND_HALF_UP)\n",
)
s = s.replace("    return total.quantize(CENTS, rounding=ROUND_HALF_UP)\n", "    return round_money(total)\n")
s = s.replace('    return discounted.quantize(Decimal("0.01"), ROUND_HALF_UP)\n', "    return round_money(discounted)\n")
s = s.replace("    return Decimal(taxed).quantize(CENTS, rounding=ROUND_HALF_UP)\n", "    return round_money(taxed)\n")
p.write_text(s)

p = Path("checkout.py")
s = p.read_text()
s = s.replace("from decimal import ROUND_HALF_UP, Decimal\n", "from decimal import Decimal\n")
s = s.replace("from pricing import add_tax, apply_discount, line_total\n",
              "from pricing import add_tax, apply_discount, line_total, round_money\n")
s = s.replace('    share = (total / people).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)\n',
              "    share = round_money(total / people)\n")
s = s.replace('    return (Decimal(amount) * Decimal(percent) / 100).quantize(Decimal("1.00"), ROUND_HALF_UP)\n',
              "    return round_money(Decimal(amount) * Decimal(percent) / 100)\n")
p.write_text(s)
EOF
