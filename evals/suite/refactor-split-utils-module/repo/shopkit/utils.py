import re
import unicodedata
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def slug(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def truncate(text, width, ellipsis="…"):
    if len(text) <= width:
        return text
    return text[: max(0, width - len(ellipsis))].rstrip() + ellipsis


def to_cents(amount):
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(cents, currency="EUR"):
    sign = "-" if cents < 0 else ""
    units, rest = divmod(abs(cents), 100)
    return f"{sign}{_SYMBOLS.get(currency, currency + ' ')}{units:,}.{rest:02d}"


def parse_money(text):
    cleaned = re.sub(r"[^\d.,-]", "", text).replace(",", "")
    if not cleaned or cleaned in {"-", "."}:
        raise ValueError(f"not an amount: {text!r}")
    return to_cents(cleaned)


def is_business_day(day):
    return day.weekday() < 5


def add_business_days(start, n):
    day = start
    step = 1 if n >= 0 else -1
    remaining = abs(n)
    while remaining:
        day += timedelta(days=step)
        if is_business_day(day):
            remaining -= 1
    return day


def business_days_between(start: date, end: date):
    if end < start:
        return -business_days_between(end, start)
    return sum(1 for i in range((end - start).days) if is_business_day(start + timedelta(days=i + 1)))
