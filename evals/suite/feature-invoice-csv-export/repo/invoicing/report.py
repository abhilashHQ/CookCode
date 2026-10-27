from . import totals


def render_text(invoices) -> str:
    header = f"{'Invoice':<10} {'Customer':<24} {'Issued':<10} {'Subtotal':>10} {'Tax':>8} {'Total':>10}"
    lines = [header, "-" * len(header)]
    grand = 0
    for inv in invoices:
        sub, tx, tot = totals.subtotal(inv), totals.tax(inv), totals.total(inv)
        grand += tot
        lines.append(
            f"{inv.number:<10} {inv.customer.name[:24]:<24} {inv.issued.isoformat():<10} "
            f"{sub:>10} {tx:>8} {tot:>10}"
        )
    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':<46} {grand:>31}")
    return "\n".join(lines) + "\n"
