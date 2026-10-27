def to_rows(orders):
    return [
        {
            "order_id": o[0],
            "sku": o[1],
            "quantity": o[2],
            "unit_price": f"{o[3]:.2f}",
            "total": f"{o[2] * o[3]:.2f}",
        }
        for o in orders
    ]


def to_tsv(orders):
    header = "order_id\tsku\tquantity\tunit_price\ttotal"
    lines = [header]
    for row in to_rows(orders):
        lines.append("\t".join(str(row[k]) for k in ("order_id", "sku", "quantity", "unit_price", "total")))
    return "\n".join(lines) + "\n"
