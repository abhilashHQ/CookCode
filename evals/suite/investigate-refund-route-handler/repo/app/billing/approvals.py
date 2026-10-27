from decimal import Decimal

from app.routes import route

from .refunds import create_refund

APPROVAL_THRESHOLD = Decimal("500")
_PENDING = []


@route("POST", "/refunds")
def issue_refund(request):
    """Refunds above the threshold need a second pair of eyes before money moves."""
    body = request.get("json") or {}
    if "payment_id" not in body or "amount" not in body:
        return 400, {"error": "payment_id and amount are required"}
    if Decimal(body["amount"]) > APPROVAL_THRESHOLD:
        _PENDING.append(body)
        return 202, {"status": "pending_approval"}
    return 201, create_refund(body["payment_id"], body["amount"], body.get("reason", ""))
