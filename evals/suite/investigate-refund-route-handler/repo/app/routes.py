ROUTES = {}


def route(method, path):
    """Register the decorated function as the handler for ``method path``.

    Registering the same method/path twice replaces the earlier handler.
    """
    def register(func):
        ROUTES[(method.upper(), path)] = func
        return func
    return register


def dispatch(method, path, request):
    handler = ROUTES.get((method.upper(), path))
    if handler is None:
        return 404, {"error": "not found"}
    return handler(request)
