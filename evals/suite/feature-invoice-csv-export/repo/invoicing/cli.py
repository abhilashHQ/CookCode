import argparse
import json
import sys
from datetime import date
from decimal import Decimal

from .models import Customer, Invoice, LineItem
from .report import render_text


def load_invoices(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    invoices = []
    for entry in raw:
        customer = Customer(entry["customer"]["name"], entry["customer"]["region"])
        items = [
            LineItem(i["description"], int(i["quantity"]), Decimal(str(i["unit_price"])), i.get("taxable", True))
            for i in entry["items"]
        ]
        invoices.append(Invoice(
            entry["number"], customer, date.fromisoformat(entry["issued"]), items,
            Decimal(str(entry.get("discount", "0"))),
        ))
    return invoices


def main(argv=None):
    parser = argparse.ArgumentParser(prog="invoicing", description="Print an invoice summary.")
    parser.add_argument("path", help="JSON file with invoices")
    args = parser.parse_args(argv)
    sys.stdout.write(render_text(load_invoices(args.path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
