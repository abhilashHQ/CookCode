from collections import defaultdict

from .parsing import parse_row
from .sessions import load_sessions


def daily_totals(sessions):
    """Map (user, date) -> minutes worked, keyed by the session's start date."""
    totals = defaultdict(int)
    for s in sessions:
        totals[(s.user, s.start.date().isoformat())] += s.minutes
    return dict(totals)


def render(lines):
    sessions = load_sessions(parse_row(l) for l in lines if l.strip() and not l.startswith("#"))
    out = []
    for (user, day), minutes in sorted(daily_totals(sessions).items()):
        out.append(f"{day}  {user:<12} {minutes // 60:>2}h{minutes % 60:02d}")
    return "\n".join(out)
