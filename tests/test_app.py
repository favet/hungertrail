from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from hungertrail.app import app
from hungertrail.config import settings
from hungertrail.daily import daily_for, seed_for, today


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_seed_is_deterministic_and_daily():
    d = date(2026, 12, 1)
    assert seed_for(settings, d) == seed_for(settings, d)
    assert seed_for(settings, d) != seed_for(settings, date(2026, 12, 2))


def test_day_number_starts_at_one():
    assert daily_for(settings, settings.launch_date).number == 1


def test_day_rolls_over_in_configured_timezone():
    now = datetime(2026, 12, 2, 7, 30, tzinfo=ZoneInfo("UTC"))
    assert today(settings, now) == date(2026, 12, 1)


def test_pages_and_api_do_not_leak_seed(client):
    assert client.get("/healthz").json()["ok"] is True
    page = client.get("/")
    assert page.status_code == 200 and "HUNGER" in page.text
    assert set(client.get("/api/daily").json()) == {"date", "number", "next_in"}
    assert str(seed_for(settings, today(settings))) not in page.text


def _play(client, mode="daily"):
    v = client.post("/api/run", json={"mode": mode, "district": 12, "trait": "fighter", "partner": "Jake", "allies": ["Rye"]}).json()
    first = v
    while not v["over"]:
        o = v["options"][0]
        r = client.post("/api/run/choose", json={"token": v["token"], "index": o["index"], "param": 2})
        assert r.status_code == 200, r.text
        v = r.json()
    return first, v


def test_full_run_and_leaderboard(client):
    first, v = _play(client)
    assert v["result"]["share"].startswith("HUNGER TRAIL #")
    assert "hungertrail.com" in v["result"]["share"]
    # Old tokens can't be replayed to reroll the dice.
    r = client.post("/api/run/choose", json={"token": first["token"], "index": first["options"][0]["index"]})
    assert r.status_code == 400
    ok = client.post("/api/leaderboard", json={"token": v["token"], "nickname": "Tester"})
    assert ok.status_code == 200, ok.text
    again = client.post("/api/leaderboard", json={"token": v["token"], "nickname": "Other"})
    assert again.status_code == 409
    rows = client.get("/api/leaderboard").json()["rows"]
    assert rows[0]["nickname"] == "Tester"


def test_tampered_token_rejected(client):
    v = client.post("/api/run", json={"mode": "practice", "district": 1, "trait": "charm"}).json()
    bad = v["token"][:-1] + ("0" if v["token"][-1] != "0" else "1")
    assert client.post("/api/run/view", json={"token": bad}).status_code == 400


def test_practice_is_not_ranked(client):
    _, v = _play(client, "practice")
    r = client.post("/api/leaderboard", json={"token": v["token"], "nickname": "Practice"})
    assert r.status_code == 400


def test_bad_nickname(client):
    _, v = _play(client)
    assert client.post("/api/leaderboard", json={"token": v["token"], "nickname": "x"}).status_code == 400
