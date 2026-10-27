import pytest

from timetrack.report import daily_totals, render
from timetrack.sessions import load_sessions, total_minutes


def test_minutes_in_utc():
    sessions = load_sessions([{"user": "ana", "start": "2024-03-01T08:00:00Z", "end": "2024-03-01T09:15:00Z"}])
    assert total_minutes(sessions) == 75


def test_rejects_backwards_session():
    with pytest.raises(ValueError):
        load_sessions([{"user": "ana", "start": "2024-03-01T10:00:00Z", "end": "2024-03-01T09:00:00Z"}])


def test_render():
    out = render(["ana; 2024-03-01T08:00:00Z; 2024-03-01T10:30:00Z"])
    assert out == "2024-03-01  ana           2h30"


def test_daily_totals_per_user():
    sessions = load_sessions([
        {"user": "ana", "start": "2024-03-01T08:00:00Z", "end": "2024-03-01T09:00:00Z"},
        {"user": "ben", "start": "2024-03-01T08:00:00Z", "end": "2024-03-01T08:30:00Z"},
    ])
    assert daily_totals(sessions) == {("ana", "2024-03-01"): 60, ("ben", "2024-03-01"): 30}
