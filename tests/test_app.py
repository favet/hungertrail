from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from hungertrail.app import app
from hungertrail.config import settings
from hungertrail.daily import daily_for, seed_for, today


def test_seed_is_deterministic_and_daily():
    d = date(2026, 12, 1)
    assert seed_for(settings, d) == seed_for(settings, d)
    assert seed_for(settings, d) != seed_for(settings, date(2026, 12, 2))


def test_day_number_starts_at_one():
    assert daily_for(settings, settings.launch_date).number == 1


def test_day_rolls_over_in_configured_timezone():
    # 07:30 UTC is still the previous evening in Pacific time.
    now = datetime(2026, 12, 2, 7, 30, tzinfo=ZoneInfo("UTC"))
    assert today(settings, now) == date(2026, 12, 1)


def test_pages_and_api_do_not_leak_seed():
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert client.get("/").status_code == 200
        body = client.get("/api/daily").json()
        assert set(body) == {"date", "number"}
