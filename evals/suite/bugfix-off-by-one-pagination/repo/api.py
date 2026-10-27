from pagination import paginate

PRODUCTS = [{"sku": f"SKU-{n:04d}", "name": f"Product {n}"} for n in range(1, 58)]


def list_products(query):
    page = int(query.get("page", 1))
    per_page = int(query.get("per_page", 20))
    result = paginate(PRODUCTS, page=page, per_page=per_page)
    return {
        "items": result.items,
        "page": result.number,
        "pages": result.pages,
        "next": result.number + 1 if result.has_next else None,
        "prev": result.number - 1 if result.has_prev else None,
    }
