from datetime import datetime

from jarvis.scheduler import in_quiet_hours, next_daily, parse_hhmm
from jarvis.telegram_bot import split_message


def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 1, 1, 23), "22-7")
    assert in_quiet_hours(datetime(2026, 1, 1, 3), "22-7")
    assert not in_quiet_hours(datetime(2026, 1, 1, 12), "22-7")
    assert in_quiet_hours(datetime(2026, 1, 1, 13), "12-14")


def test_next_daily():
    now = datetime(2026, 1, 1, 8, 0)
    assert next_daily(now, 7, 30) == datetime(2026, 1, 2, 7, 30)
    assert next_daily(now, 9, 0) == datetime(2026, 1, 1, 9, 0)
    assert parse_hhmm("07:30") == (7, 30) and parse_hhmm("x") is None


def test_split_message():
    parts = split_message("a" * 9000)
    assert len(parts) == 3 and "".join(parts) == "a" * 9000
