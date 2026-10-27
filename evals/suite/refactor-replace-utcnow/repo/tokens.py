"""Minimal signed, expiring tokens for internal service-to-service calls."""
import base64
import calendar
import hashlib
import hmac
import json
from datetime import datetime, timedelta

SECRET = b"dev-secret-change-me"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
    return _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def issue(subject, ttl=timedelta(minutes=15), now=None):
    now = now or datetime.utcnow()
    exp = calendar.timegm((now + ttl).utctimetuple())
    body = _b64(json.dumps({"sub": subject, "exp": exp}, sort_keys=True).encode())
    return f"{body}.{_sign(body)}"


def _claims(token):
    body, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(body)):
        raise ValueError("bad signature")
    return json.loads(_unb64(body))


def verify(token, now=None):
    claims = _claims(token)
    now = now or datetime.utcnow()
    if datetime.utcfromtimestamp(claims["exp"]) <= now:
        raise ValueError("token expired")
    return claims


def expires_at(token):
    return datetime.utcfromtimestamp(_claims(token)["exp"])
