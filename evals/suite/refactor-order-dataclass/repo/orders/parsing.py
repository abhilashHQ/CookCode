from decimal import Decimal, InvalidOperation


def parse_order(line):
    """Parse ``ORDER_ID,SKU,QTY,UNIT_PRICE`` into (order_id, sku, quantity, unit_price)."""
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 4 or not parts[0] or not parts[1]:
        raise ValueError(f"malformed order line: {line!r}")
    order_id, sku, qty, price = parts
    try:
        return (order_id, sku.upper(), int(qty), Decimal(price))
    except (ValueError, InvalidOperation):
        raise ValueError(f"malformed order line: {line!r}") from None


def parse_orders(text):
    return [
        parse_order(line)
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
