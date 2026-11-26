import random

import pytest
from fastapi.testclient import TestClient

from hungertrail.app import app
from hungertrail.beta import engine, lore
from hungertrail.beta.bots import BOTS, play
from hungertrail.beta.engine import CAMP, DRAFT, DUEL, SWAP, Game, cards, new_setup, replay

SETUP = new_setup(12, "Jake", ["Gingi"])
KNOWN_EFFECTS = {"health", "supplies", "crowd", "add", "remove", "kill", "ally", "die", "cause", "next", "gift", "win", "partner_dies"}
KNOWN_NEEDS = {"item", "any", "perk", "ally", "supplies"}


def _run(seed, salt, choices_from=None):
    g = Game(seed, salt, SETUP)
    r = random.Random(seed)
    picks = []
    while not g.over:
        idx, param = BOTS["random"](g, r)
        picks.append([idx, param])
        g.choose(idx, param)
    return g, picks


def test_replay_is_deterministic():
    g, picks = _run(5, 9)
    again = replay(5, 9, SETUP, picks)
    assert again.log == g.log and again.placement == g.placement


def test_same_arena_for_everyone_different_dice():
    a, b = Game(5, 1, SETUP), Game(5, 2, SETUP)
    assert a.arena.layout == b.arena.layout and a.arena.deck == b.arena.deck
    assert Game(6, 1, SETUP).arena.layout != a.arena.layout or Game(7, 1, SETUP).arena.layout != a.arena.layout


@pytest.mark.parametrize("bot", list(BOTS))
def test_every_run_ends(bot):
    for seed in range(150):
        g = play(BOTS[bot], seed)
        assert g.over and g.placement is not None and 1 <= g.placement <= 24


def test_cards_are_valid():
    for cid, c in cards().items():
        assert c["kind"] in ("event", "gamemaker", "feast", "betrayal", "finale"), cid
        if c["kind"] == "event":
            free = [o for o in c["options"] if not o.get("need")]
            assert len(free) >= 2 or (len(free) == 1 and len(c["options"]) >= 2), cid
        if c.get("target"):
            assert "{Target}" in c["text"] or "{target}" in c["text"], cid
        for o in c["options"]:
            assert set(o.get("need") or {}) <= KNOWN_NEEDS, cid
            branches = [o.get("success"), o.get("fail")] if "roll" in o else [o]
            for b in branches:
                assert b is not None, (cid, o["label"])
                assert set(b.get("effects") or {}) <= KNOWN_EFFECTS, (cid, o["label"])
                for item in Game._list((b.get("effects") or {}).get("add")):
                    assert item in lore.ITEMS, (cid, item)


def test_options_show_stakes_the_engine_applies():
    g = Game(5, 9, SETUP)
    for o in g.options():
        if o["chance"] is not None:
            assert o["win"] and o["lose"]
    # Card damage on screen is the scaled damage the engine deals.
    assert g._chips({"health": -20}) == [f"❤️−{round(20 * engine.DAMAGE)}"]
    g.health = 30
    assert "☠️" in g._chips({"health": -20})


def test_draft_gives_kit_and_ends_bloodbath():
    g = Game(5, 9, SETUP)
    assert g.card == DRAFT
    run = len(g.arena.layout)
    g.choose(run)  # run for the trees
    assert g.card == CAMP and g.kit == [] and "🌲" in g.days[1]


def test_full_kit_asks_what_to_drop():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["knife", "rope", "medkit"]
    snap = g._snap()
    g._gain("bow")
    g._delta(snap)
    g._advance(("night",))
    assert g.card == SWAP
    labels = [o["label"] for o in g.options()]
    assert labels[-1] == "Leave the bow" and "Drop the knife" in labels
    g.choose(0)  # drop the knife
    assert "bow" in g.kit and "knife" not in g.kit and g.card != SWAP


def test_kills_take_the_named_tribute():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    card = cards()["lone_sleeper"]
    g.target = g._pick_target(card)
    assert g.target in g.pool
    g._apply({"kill": 1}, card)
    assert g.target is None and g.fallen_today[-1].startswith("the District")
    assert g.fallen_today[-1] not in g.pool


def test_duel_lists_edges_and_ends_the_run():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["bow", "rope"]
    g.pool = g.pool[:1]
    g.allies = []
    g._start_duel()
    assert g.card == DUEL
    text = g.log[-1]["text"]
    assert "your bow" in text and "the high ground" in text
    fight, wait = g.options()
    assert fight["chance"] > engine.DUEL_BASE and "Your bow +15" in fight["hint"]
    g.choose(0)
    assert g.over


def test_bored_crowd_brings_the_gamemakers():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.crowd = engine.RESTLESS - 30
    g.choose(0)  # rest: no bold move, Caesar warns
    assert g.warned and any(e["kind"] == "caesar" and "restless" in e["text"].lower() for e in g.log)


client = TestClient(app)


def test_beta_page_on_beta_host_only():
    live = client.get("/")
    assert "Hunger Trail β" not in live.text
    beta = client.get("/", headers={"host": "beta.hungertrail.com"})
    assert "Hunger Trail β" in beta.text
    assert "Hunger Trail β" in client.get("/", headers={"x-ht-site": "beta"}).text
    assert "Hunger Trail β" in client.get("/beta").text


def test_beta_full_run_and_board():
    v = client.post("/api/beta/run", json={"mode": "daily", "district": 7, "partner": "Jake", "allies": ["Gingi"]}).json()
    assert v["layout"] and v["card"] == "draft" and "seed" not in str(v)
    r = random.Random(1)
    for _ in range(80):
        if v["over"]:
            break
        o = r.choice(v["options"])
        res = client.post("/api/beta/run/choose", json={"token": v["token"], "index": o["index"], "param": 2})
        assert res.status_code == 200, res.text
        v = res.json()
    assert v["over"] and "beta.hungertrail.com" in v["result"]["share"]
    sub = client.post("/api/beta/leaderboard", json={"token": v["token"], "nickname": "BetaTester"})
    assert sub.status_code == 200
    rows = client.get("/api/beta/leaderboard").json()["rows"]
    assert rows and rows[0]["nickname"] == "BetaTester"
    assert all(r["nickname"] != "BetaTester" for r in client.get("/api/leaderboard").json()["rows"])


def test_live_and_beta_tokens_dont_mix():
    beta = client.post("/api/beta/run", json={"mode": "practice", "district": 3}).json()
    live = client.post("/api/run", json={"mode": "practice", "district": 3, "trait": "fighter"}).json()
    assert client.post("/api/run/view", json={"token": beta["token"]}).status_code == 400
    assert client.post("/api/beta/run/view", json={"token": live["token"]}).status_code == 400
