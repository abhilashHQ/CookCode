from decimal import Decimal

from app.routes import route

_REFUNDS = []


def create_refund(payment_id, amount, reason=""):
    refund = {"id": len(_REFUNDS) + 1, "payment_id": payment_id, "amount": str(Decimal(amount)), "reason": reason}
    _REFUNDS.append(refund)
    return refund


@route("GET", "/refunds")
def list_refunds(request):
    return 200, {"refunds": list(_REFUNDS)}


@route("POST", "/refunds")
def handle_refund(request):
    body = request.get("json") or {}
    if "payment_id" not in body or "amount" not in body:
        return 400, {"error": "payment_id and amount are required"}
    return 201, create_refund(body["payment_id"], body["amount"], body.get("reason", ""))
