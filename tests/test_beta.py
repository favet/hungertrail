import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hungertrail.app import app
from hungertrail.beta import engine, lore
from hungertrail.beta import minigames as mg
from hungertrail.beta.bots import BOTS, hands, play
from hungertrail.beta.engine import (DRAFT, DUEL, EDGE, PLACE_KEYS, PLACES, SIFT, SIFT_BIG, SIFT_SMALL, SIFT_STOP, SWAP, USE,
                                     Game, cards, new_setup, replay)

SETUP = new_setup(12, "Jake", ["Gingi"])
KNOWN_EFFECTS = {"health", "supplies", "sponsor", "add", "remove", "kill", "ally", "die", "cause", "next", "gift", "win", "partner_dies", "target_dies"}
KNOWN_NEEDS = {"item", "any", "perk", "ally", "supplies"}


def _run(seed, salt, choices_from=None):
    g = Game(seed, salt, SETUP)
    r = random.Random(seed)
    picks = []
    while not g.over:
        idx = BOTS["random"](g, r)
        param = hands(g, idx, 0.5, r)
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
    assert g._chips({"health": -20}) == [f"−{round(20 * engine.DAMAGE)} health"]
    g.health = 30
    assert engine.DEADLY in g._chips({"health": -20})


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


def test_striking_first_kills_the_ally_and_mercy_is_not_a_kill():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.allies = ["Rue"]
    strangers = list(g.pool)
    g._apply({"ally": "dies", "kill": 1}, cards()["betrayal"])
    assert g.allies == [] and g.new_kills[-1] == "Rue" and g.fallen_today[-1] == "Rue" and g.pool == strangers
    card = cards()["lone_sleeper"]
    g.target = g._pick_target(card)
    kills = g.kills
    g._apply({"target_dies": True}, card)
    assert g.kills == kills and g.fallen_today[-1] not in g.pool and g.fallen_today[-1] not in g.killed


def test_the_medkit_makes_room_in_a_full_pack():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["knife", "medkit", "rope"]
    g.health, g.supplies = 50, 3
    g.resume = ("night",)
    g._gain("bow")
    g._advance(("night",))
    assert g.card == SWAP
    use = [o for o in g.options() if o["index"] == USE]
    assert use and use[0]["label"] == "Use the medkit, then take the bow"
    g.choose(USE)
    assert g.health >= 90 and sorted(g.kit) == ["bow", "knife", "rope"] and g.card != SWAP


def test_a_close_call_at_the_horn_keeps_you_grabbing():
    for salt in range(400):
        g = Game(5, salt, SETUP)
        edge = next(o for o in g.options() if o.get("ring") == "edge")
        g.choose(edge["index"])
        roll = next(e for e in g.log if e["kind"] == "roll")
        if roll["near"]:
            assert g.card == DRAFT and g.grabs == 1 and g.health < 100
            return
    raise AssertionError("no close call in 400 runs")


def test_no_close_call_when_half_the_damage_still_kills():
    g = Game(5, 9, SETUP)
    g.health = 10
    opts = [o for o in g.options() if o.get("ring") == "horn"]
    assert opts and not any(o["wheel"]["near"] for o in opts)


def test_hunger_escalates_and_shows_before_night():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))  # run: day 2
    g.district = 7  # District 12 would take half
    g.arena.twist = "fat_fish"  # not the cold
    g.supplies = 0
    costs = []
    for _ in range(3):
        assert g.status()["hunger"] == g.hunger_cost()
        costs.append(g.hunger_cost())
        g.health = 100
        g._night()
    assert costs == [10, 20, 30]
    g.supplies = 1
    g._night()
    assert g.hungry == 0 and g.status()["hunger"] == 10


def test_the_death_recap_tells_the_chain():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.district = 7
    g.arena.twist = "fat_fish"
    g._hurt(45, "a Career's knife")
    g.supplies = 0
    g.health = 25
    g._night()
    g._night()
    assert g.over and g.cause == "starvation"
    assert g.recap == "Day 2: a Career's knife took 45. Two hungry nights finished it."


def test_stars_buy_a_parachute_that_drops_tonight():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))  # run for the trees: day 2, the place menu
    g.arena.twist = "fat_fish"  # no twist on prices or hunger
    g.stars, g.supplies = 4, 0
    menu = {m["key"]: m for m in g.status()["menu"]}
    assert menu["food"]["can"] and menu["food"]["price"] == 2 and not menu["bow"]["can"]
    g.choose(menu["food"]["index"])
    assert g.stars == 2 and g.order == "food" and g.status()["order"] == "Food for 2 days"
    assert not any(m["can"] for m in g.status()["menu"])  # one parachute a night
    g._night()
    assert g.order is None and g.supplies == 1 and g.hungry == 0  # it dropped before supper
    assert {m["key"]: m["price"] for m in g.status()["menu"]}["food"] == 3  # each order raises prices
    g.over, g.placement = True, 5
    two = g.score()
    g.stars = 0
    assert two == round(g.score() * 1.2)


def test_walking_away_costs_a_star():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.stars = 2
    for cid, c in cards().items():
        walk = [i for i, o in enumerate(c.get("options", [])) if o.get("tag") == "hide" and "roll" not in o and not o.get("need")]
        if c["kind"] == "event" and walk:
            g.card, g.target = cid, None
            assert "−1 star" in next(o for o in g.options() if o["index"] == walk[0])["lose"]
            g.choose(walk[0])
            assert g.stars == 1
            return
    raise AssertionError("no walk-away card")


def test_final_fight_runs_on_health():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["bow", "rope"]
    g.allies = []
    g._start_duel()
    assert g.card == DUEL and g.opponent_id == g.nemesis and g.remaining == 2 and g.opp_health == 100
    press, hold = g.options()
    assert press["minigame"]["game"] == "fight" and "Your bow" in press["hint"] and "Rope" in hold["hint"]
    # Rope widens the block; the same exchange has the same tell whichever stance you pick.
    assert press["minigame"]["side"] == hold["minigame"]["side"]
    assert hold["minigame"]["block"][1] - hold["minigame"]["block"][0] > press["minigame"]["block"][1] - press["minigame"]["block"][0]
    p = press["minigame"]
    g.choose(0, [p["block"][0], p["side"], p["open"][0]])  # a clean block, then a strike
    assert g.opp_health == 100 - g._fight_deal("aggressive") + 0 or g.over
    assert g.log[-2]["kind"] in ("roll", "result")
    health = g.health
    p = g.options()[1]["minigame"]
    g.choose(1, [p["block"][0], 1 - p["side"], -1])  # blocked the wrong side
    assert g.health == health - g._fight_take("careful")
    while not g.over:
        p = g.options()[0]["minigame"]
        g.choose(0, [p["block"][0], p["side"], p["open"][0]])
    assert g.won and g.opp_health == 0


def test_hunting_is_replayed_from_the_taps():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["bow"]
    g.arena.offers[2] = ["ridge", "stream", "horn"]
    ridge = next(o for o in g.options() if o["place"] == "ridge")
    hunt = ridge["minigame"]
    assert hunt["game"] == "hunt" and len(hunt["shots"]) == 3
    # Aim every arrow at the deer's centre on its first tick... the first one lands, the rest find rabbits or miss.
    s0 = hunt["shots"][0]
    deer = next(a for a in s0["animals"] if a["kind"] == "deer")
    tap = next(t for t in range(s0["duration"]) if abs(mg.tri(s0["speed"] * t) - mg.path_at(deer["path"], t)) < 100)
    food, hits = mg.hunt_run(hunt, [tap, -1, -1])
    assert hits == ["deer", None, None] and food == 2
    before = g.supplies
    g.choose(ridge["index"], [tap, -1, -1])
    assert g.supplies == before + 2
    g2 = Game(5, 9, SETUP)
    g2.choose(len(g2.arena.layout))
    g2.kit = ["bow"]
    g2.arena.offers[2] = ["ridge", "stream", "horn"]
    with pytest.raises(ValueError):
        g2.choose(ridge["index"], [1, 2, 3, 4])  # four taps for three arrows


def test_fishing_and_hiding_inputs_are_checked():
    p = mg.fish_params(random.Random(1), "minnow", 2200)
    with pytest.raises(ValueError):
        mg.clean_toggles([5, 3], p["duration"])
    with pytest.raises(ValueError):
        mg.clean_toggles([p["duration"]], p["duration"])
    with pytest.raises(ValueError):
        mg.clean_toggles(list(range(mg.MAX_INPUTS + 1)), 10_000)
    assert mg.fish_run(p, [])[0] in (True, False)
    for i in range(100):  # letting a leech go means just not reeling
        assert not mg.fish_run(mg.fish_params(random.Random(i), "leech", (2200, 3000)[i % 2]), [])[0]
    h = mg.hide_params(random.Random(2), 70, 0, 0, False)
    assert mg.hide_run(h, [])[0] == "spotted"  # never ducking means the light finds you
    assert mg.hide_run(h, [0])[0] == "gasp"  # never breathing means you gasp
    assert mg.hide_run(h, mg.bot_hide(h, 1.0, random.Random(3)))[0] == "hidden"


def test_hide_is_fair():
    """Every hold-still layout leaves time to breathe between passes."""
    for i in range(200):
        for base in (60, 65, 70):
            p = mg.hide_params(random.Random(i), base, 0, 200, i % 2 == 0)
            cover = mg._cover(p["path"], p["me"], p["half"], p["duration"])
            assert cover and all(b - a < p["breath"] for a, b in cover)


def test_sift_at_the_horn():
    """The Crystal Sphere: treasures cover several tiles and are yours only once fully uncovered."""
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.arena.offers[2] = ["stream", "horn", "open"]
    g.supplies = 3
    g.kit = []
    horn = next(o for o in g.options() if o["place"] == "horn")
    g.choose(horn["index"])
    assert g.card == SIFT and [o["label"] for o in g.options()] == ["Dig quietly", "Dig loudly"]
    g.choose(0)
    assert g.supplies == 2 and g.sift["digs"] == 4
    view = g.sift_view()
    assert len(view["tiles"]) == 81 and not any(t["open"] for t in view["tiles"])
    assert "cells" not in str(view) and "career" not in str(view)  # nothing under the rubble leaks
    assert any(t["name"].startswith("a crate") and (t["w"], t["h"]) == (3, 3) for t in view["legend"])
    crate = next(s for s in g.sift["shapes"] if s["kind"] == "crate")
    corner, middle = crate["cells"][0], crate["cells"][4]
    g.choose(SIFT_SMALL + corner)  # one piece: not yours yet, and its edges show it's a corner
    piece = g.sift_view()["tiles"][corner]
    assert piece["open"] and not piece["done"] and set(piece["edge"]) == {"t", "l"}
    assert crate["item"] not in g.kit
    g.choose(SIFT_BIG + middle)  # the 3x3 on its middle opens the rest of it
    assert crate["done"] and crate["item"] in g.kit
    assert g.sift["digs"] == 2
    while not g.over and g.card == SIFT:
        g.choose(SIFT_STOP)
    assert g.over or g.card != SIFT


def test_python_and_browser_minigames_agree():
    """The browser's minigames.js must reach the same result as the server on the same inputs."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = random.Random(7)
    cases = []
    for i in range(30):
        f = mg.fish_params(random.Random(i), ("minnow", "trout", "leech")[i % 3], 2200 + 800 * (i % 2))
        ft = mg.bot_fish(f, r.random(), r)
        cases.append(["fish", f, ft, list(mg.fish_run(f, ft))])
        h = mg.hunt_params(random.Random(i), ("bow", "spear")[i % 2], i % 3 == 0)
        ht = mg.bot_hunt(h, r.random(), r)
        food, hits = mg.hunt_run(h, ht)
        cases.append(["hunt", h, ht, [food, hits]])
        d = mg.hide_params(random.Random(i), 60 + i % 15, 0, 0, i % 2 == 0)
        dt = mg.bot_hide(d, r.random(), r)
        cases.append(["hide", d, dt, list(mg.hide_run(d, dt))])
        x = mg.fight_params(random.Random(i), ("careful", "aggressive")[i % 2], i % 2 == 0, i % 3 == 0, 12, i % 5)
        xt = mg.bot_fight(x, r.random(), r)
        cases.append(["fight", x, xt, list(mg.fight_run(x, xt))])
    js = Path(__file__).parents[1] / "src/hungertrail/static/beta/minigames.js"
    script = (
        f"const Sim = require({json.dumps(str(js))});"
        "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "const run = {fish: Sim.fishRun, hunt: Sim.huntRun, hide: Sim.hideRun, fight: Sim.fightRun};"
        "console.log(JSON.stringify(cases.map(([k, p, inp]) => run[k](p, inp))));"
    )
    out = subprocess.run([node, "-e", script], input=json.dumps(cases), capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [c[3] for c in cases]


def test_places_are_seeded_and_the_edge_closes():
    g = Game(5, 9, SETUP)
    a = g.arena
    assert all(len(a.offers[d]) == 3 for d in range(2, 8))
    assert a.offers == Game(5, 77, new_setup(3, "X", [])).arena.offers
    # After the edge closes, only the finale's refuge is left of it, and the menu changes day to day.
    assert all(set(a.offers[d]) & set(EDGE) <= {a.refuge} for d in range(6, 8))
    assert sorted(a.offers[6]) != sorted(a.offers[7]) or a.feast_day in (6, 7)
    assert "feast" in a.offers[a.feast_day]


def test_the_day_plan_varies_by_seed():
    arenas = [Game(seed, 1, SETUP).arena for seed in range(60)]
    assert {a.sight_day for a in arenas} == {2, 3, 4, 5}
    assert {a.finale for a in arenas} == set(engine.FINALES)
    assert {a.twist for a in arenas} == set(engine.TWISTS)
    assert len({tuple(a.offers[6]) for a in arenas}) > 1
    for a in arenas:
        assert a.sight_day < a.clash_day <= 7
        assert engine.cards()[a.finale]["kind"] == "finale"


def test_the_nemesis_keeps_to_the_rumored_places():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    a = g.arena
    g.day = a.sight_day
    away = next(p for p in ("stream", "ridge", "open", "horn") if p not in a.sight_places + a.clash_places)
    assert g._place_card(a.sight_places[0]) == "nemesis_sighting"
    assert g._place_card(away) != "nemesis_sighting"
    g.day = a.sight_day - 1
    assert g._place_card(a.sight_places[0]) != "nemesis_sighting"
    rumor = [line for line in g._caesar(False) if "seen near" in line]
    assert rumor and all(engine.PLACE_NAMES[p] in rumor[0] for p in a.sight_places)


def test_edge_brings_the_gamemakers():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    for _ in range(4):
        if g.card != PLACES:
            break
        edge = [o for o in g.options() if o["place"] in EDGE]
        pick = (edge or g.options())[0]["index"]
        g.choose(pick, hands(g, pick, 0.5, random.Random(1)))
        while not g.over and g.card not in (PLACES,) and not g.card.startswith("gm_"):
            pick = g.options()[-1 if g.card == SIFT else 0]["index"]
            g.choose(pick, hands(g, pick, 0.5, random.Random(1)))
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
    bought = False
    for _ in range(150):
        if v["over"]:
            break
        o = r.choice(v["options"])
        buy = [m for m in v["status"]["menu"] if m["can"]]
        if buy:  # spend stars through the API too: the token replays the order
            o = {"index": buy[0]["index"]}
            bought = True
        game = o.get("minigame")
        param = {"fish": lambda p: mg.bot_fish(p, 0.5, r), "hunt": lambda p: mg.bot_hunt(p, 0.5, r),
                 "hide": lambda p: mg.bot_hide(p, 0.5, r), "fight": lambda p: mg.bot_fight(p, 0.5, r)}[game["game"]](game) if game else None
        res = client.post("/api/beta/run/choose", json={"token": v["token"], "index": o["index"], "param": param})
        assert res.status_code == 200, res.text
        v = res.json()
    assert v["over"] and "beta.hungertrail.com" in v["result"]["share"] and v["result"]["multiplier"] == 2
    assert bought
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
