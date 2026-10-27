from collections import defaultdict
from decimal import Decimal


def revenue_by_sku(orders):
    revenue = defaultdict(lambda: Decimal("0"))
    for order in orders:
        revenue[order[1]] += order[2] * order[3]
    return dict(sorted(revenue.items()))


def total_units(orders):
    return sum(order[2] for order in orders)


def top_skus(orders, n=3):
    units = defaultdict(int)
    for order in orders:
        units[order[1]] += order[2]
    return sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def largest_order(orders):
    if not orders:
        return None
    best = max(orders, key=lambda o: o[2] * o[3])
    return best[0]
