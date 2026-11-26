import random

import pytest
from fastapi.testclient import TestClient

from hungertrail.app import app
from hungertrail.beta import engine, lore
from hungertrail.beta.bots import BOTS, play
from hungertrail.beta.engine import DRAFT, DUEL, EDGE, PLACE_KEYS, PLACES, SWAP, USE, Game, cards, new_setup, replay

SETUP = new_setup(12, "Jake", ["Gingi"])
KNOWN_EFFECTS = {"mercy", "wound", "health", "supplies", "sponsor", "add", "remove", "kill", "ally", "die", "cause", "next", "gift", "win", "partner_dies"}
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
            assert c.get("place") and set(c["place"]) <= set(PLACE_KEYS[:5]), cid
            free = [o for o in c["options"] if not o.get("need")]
            assert len(free) >= 2 or (len(free) == 1 and len(c["options"]) >= 2), cid
        if c.get("target"):
            want = ("{Nemesis}", "{nemesis}") if c["target"] == "nemesis" else ("{Target}", "{target}")
            assert any(w in c["text"] for w in want), cid
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
    assert g.card == PLACES and g.day == 2 and g.kit == [] and "🌲" in g.days[1]


def test_full_kit_asks_what_to_drop():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["knife", "rope", "medkit"]
    snap = g._snap()
    g._gain("bow")
    g._delta(snap)
    g._advance(("night",))
    assert g.card == SWAP and g.day == 2
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


def test_final_fight_runs_on_health_with_edges():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["bow", "rope"]
    g.allies = []
    g.nemesis_health = 60  # wounded earlier in the run
    g._start_duel()
    assert g.card == DUEL and g.opponent_id == g.nemesis and g.remaining == 2
    text = g.log[-1]["text"]
    assert "your bow" in text and "the high ground" in text and "They have 60" in text
    press, hold = g.options()
    assert "Your bow +15" in press["hint"] and "The high ground +15" in hold["hint"]
    assert press["chance"] == engine.FIGHT["press"][0] + 15
    for _ in range(40):
        if g.over:
            break
        g.choose(0)
    assert g.over and (g.won or g.cause.endswith("in the final duel"))
    assert g.won == (g.opp_health == 0)


def test_mercy_is_not_a_kill():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.target = next(t for t in g.pool if t.startswith("the District"))
    g._apply({"mercy": True}, {})
    assert g.kills == 0 and g.target is None and g.fallen_today


def test_walking_away_costs_a_day_of_supplies():
    g = Game(5, 9, SETUP)
    g.card = "smoke_east"
    walk = next(o for o in g.options() if o["label"] == "Go the other way")
    assert walk["lose"] == [f"🎒−{engine.WALK_COST}"]


def test_one_card_miss_never_kills_from_full_health():
    g = Game(5, 9, SETUP)
    for c in cards().values():
        for o in c["options"]:
            if "roll" in o and not o["fail"].get("effects", {}).get("die"):
                assert "☠️" not in g._chips(o["fail"].get("effects", {})), (c["id"], o["label"])


def test_places_are_seeded_and_the_edge_closes():
    g = Game(5, 9, SETUP)
    a = g.arena
    assert all(len(a.offers[d]) == 3 for d in range(2, 8))
    assert a.offers == Game(5, 77, new_setup(3, "X", [])).arena.offers
    assert all(not set(a.offers[d]) & set(EDGE) for d in range(6, 8))
    assert "feast" in a.offers[a.feast_day]


def test_edge_brings_the_gamemakers():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    for _ in range(4):
        if g.card != PLACES:
            break
        edge = [o for o in g.options() if o["place"] in EDGE]
        if not edge:
            g.choose(g.options()[0]["index"], 1)
        else:
            g.choose(edge[0]["index"], 1)
        while not g.over and g.card not in (PLACES,) and not g.card.startswith("gm_"):
            g.choose(g.options()[0]["index"], 1)
        if g.card.startswith("gm_"):
            break
    if not g.over:
        assert g.card.startswith("gm_") or g.edge_streak <= 1
    assert any("Gamemakers" in e["text"] for e in g.log if e["kind"] == "caesar")


def test_rolls_are_logged_for_the_reveal():
    g = Game(5, 9, SETUP)
    g.choose(0)  # a grab
    roll = next(e for e in g.log if e["kind"] == "roll")
    assert roll["chance"] and 1 <= roll["roll"] <= 100 and roll["ok"] == (roll["roll"] <= roll["chance"])


def test_medkit_by_tapping_it():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    with pytest.raises(ValueError):
        g.choose(USE)
    g.kit = ["medkit"]
    g.health = 30
    g.choose(USE)
    assert g.health == 70 and "medkit" not in g.kit and g.card == PLACES


def test_nemesis_survives_to_the_end_unless_you_kill_them():
    for seed in range(40):
        g = Game(seed, 3, SETUP)
        assert g.nemesis not in g.arena.death_order and g.nemesis in g.pool
        for d in range(1, 8):
            g._scheduled_deaths(d)
        assert g.nemesis in g.pool


def test_death_recap():
    g = Game(5, 9, SETUP)
    g.health = 1
    g.choose(5)  # a horn grab at 1 health
    if not g.won and g.over:
        assert g.recap and "1 health" in g.recap

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
