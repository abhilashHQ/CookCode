from app.routes import route

from .refunds import create_refund


@route("POST", "/payments/refund")
def refund_payment(request):
    body = request.get("json") or {}
    return 201, create_refund(body["payment_id"], body["amount"], "full refund")
