from decimal import Decimal

RATES = {
    "DE": Decimal("0.19"),
    "FR": Decimal("0.20"),
    "GB": Decimal("0.20"),
    "US-CA": Decimal("0.0725"),
    "US-OR": Decimal("0"),
}
DEFAULT_RATE = Decimal("0.10")


def rate_for(region: str) -> Decimal:
    return RATES.get(region.upper(), DEFAULT_RATE)
