from . import utils


def render_receipt(cart, width=32):
    lines = []
    for key, (cents, qty) in sorted(cart.lines.items()):
        label = utils.truncate(f"{qty}x {key}", width - 12)
        lines.append(f"{label:<{width - 12}}{utils.format_money(cents * qty, cart.currency):>12}")
    lines.append("-" * width)
    lines.append(f"{'TOTAL':<{width - 12}}{cart.total():>12}")
    return "\n".join(lines)


def refund_amount(text, cart):
    cents = utils.parse_money(text)
    if cents > cart.total_cents:
        raise ValueError("refund exceeds order total")
    return cents
