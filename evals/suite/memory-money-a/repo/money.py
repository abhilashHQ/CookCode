"""Amounts are integer cents everywhere. Percentages round half up to the cent."""


def fmt(cents):
    return f"${cents // 100}.{cents % 100:02d}"


def percent(cents, pct):
    return (cents * pct * 2 + 100) // 200
