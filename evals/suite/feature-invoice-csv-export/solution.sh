set -euo pipefail
python3 - <<'EOF'
from pathlib import Path
p = Path("invoicing/report.py")
s = p.read_text()
s = s.replace("from . import totals\n", "import csv\nimport io\n\nfrom . import totals\n")
s += '''

def to_csv(invoices) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\\n")
    writer.writerow(["number", "customer", "issued", "subtotal", "tax", "total"])
    for inv in invoices:
        writer.writerow([
            inv.number, inv.customer.name, inv.issued.isoformat(),
            f"{totals.subtotal(inv):.2f}", f"{totals.tax(inv):.2f}", f"{totals.total(inv):.2f}",
        ])
    return buf.getvalue()
'''
p.write_text(s)

p = Path("invoicing/cli.py")
s = p.read_text()
s = s.replace("from .report import render_text\n", "from .report import render_text, to_csv\n")
s = s.replace(
    '    parser.add_argument("path", help="JSON file with invoices")\n',
    '    parser.add_argument("path", help="JSON file with invoices")\n'
    '    parser.add_argument("--format", choices=("text", "csv"), default="text")\n',
)
s = s.replace(
    "    sys.stdout.write(render_text(load_invoices(args.path)))\n",
    "    invoices = load_invoices(args.path)\n"
    '    sys.stdout.write(to_csv(invoices) if args.format == "csv" else render_text(invoices))\n',
)
p.write_text(s)
EOF
