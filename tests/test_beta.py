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
from hungertrail.beta.engine import (DRAFT, DUEL, EDGE, PLACE_KEYS, PLACES, DIG, DIG_GO, DIG_LEAVE, DIG_CAUGHT, SWAP, USE,
                                     Game, cards, new_setup, replay)

SETUP = new_setup(12, "Jake", ["Gingi"])
KNOWN_EFFECTS = {"health", "supplies", "sponsor", "add", "remove", "kill", "ally", "die", "cause", "next", "gift", "win", "partner_dies", "target_dies", "wound", "study"}
KNOWN_NEEDS = {"item", "any", "without", "perk", "ally", "supplies"}


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


def test_full_kit_asks_what_to_keep():
    """Everything that doesn't fit goes on one screen with your kit: keep any three."""
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["knife", "rope", "medkit"]
    snap = g._snap()
    g._gain("bow")
    g._gain("axe")
    g._gain("bow")  # a second copy is left where it is
    g._delta(snap)
    g._advance(("night",))
    assert g.card == SWAP and g.day == 2
    pack = g.options()[0]["pack"]
    assert [i["id"] for i in pack["items"]] == ["knife", "rope", "medkit", "bow", "axe"] and pack["slots"] == 3
    notes = {i["id"]: i["note"] for i in pack["items"]}
    assert notes["bow"] == "" and "bow is better" in notes["knife"] and "bow is better" in notes["axe"]
    with pytest.raises(ValueError):
        g.choose(0, [0, 1, 2, 3])  # four won't fit
    g.choose(0, [1, 2, 3])  # rope, medkit, bow
    assert g.kit == ["rope", "medkit", "bow"] and g.card != SWAP and g.item_queue == []
    assert any("You leave the knife and the axe" in e["text"] for e in g.log)


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
    assert use and use[0]["label"] == "Use the medkit now"
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
    g.allies = []  # no ally bringing back a rabbit
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
    g.allies = []
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


def test_giving_up_ends_the_run_as_a_loss():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    left = g.remaining
    g.choose(engine.QUIT)
    assert g.over and not g.won and g.cause == engine.GAVE_UP and g.placement == left
    assert g.recap.startswith("You gave up on day 2") and g.score() > 0
    with pytest.raises(ValueError):
        g.choose(engine.QUIT)


def test_final_fight_runs_on_health():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["bow", "rope"]
    g.allies = []
    g._start_duel()
    assert g.card == DUEL and g.opponent_id == g.nemesis and g.remaining == 2 and g.opp_health == engine.OPPONENT_HEALTH["nemesis"]
    press, hold = g.options()
    assert press["minigame"]["game"] == "fight" and "Your bow" in press["hint"] and "Rope" in hold["hint"]
    # Rope widens the block; the same exchange has the same tell whichever stance you pick.
    assert press["minigame"]["side"] == hold["minigame"]["side"]
    assert hold["minigame"]["block"][1] - hold["minigame"]["block"][0] > press["minigame"]["block"][1] - press["minigame"]["block"][0]
    p = press["minigame"]
    g.choose(0, [p["block"][0], p["side"], p["open"][0]])  # a clean block, then a strike
    assert g.opp_health == engine.OPPONENT_HEALTH["nemesis"] - g._fight_deal("aggressive") or g.over
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


def test_dig_at_the_horn():
    """Push your luck: each dig spins a wheel of what's still buried. Every noise costs a little; the
    third brings a Career, and the haul is lost."""
    def at_horn(salt, seed=5):
        g = Game(seed, salt, SETUP)
        g.choose(len(g.arena.layout))
        g.arena.offers[2] = ["stream", "horn", "open"]
        g.kit, g.health, g.supplies = [], 100, 1
        g.choose(next(o for o in g.options() if o["place"] == "horn")["index"])
        return g

    g = at_horn(9)
    assert g.card == DIG and [o["label"] for o in g.options()] == ["Dig", "Leave it"]
    view = g.dig_view()
    noise = sum(1 for b in view["buried"] if b["icon"] == "👂")
    loot = len(view["buried"]) - noise
    assert engine.DIG_NOISES[0] <= noise <= engine.DIG_NOISES[1] and engine.DIG_LOOTS[0] <= loot <= engine.DIG_LOOTS[1]
    assert view["haul"] == [] and view["heard"] == 0
    dig = g.options()[0]
    # one way of stating the odds: the chance of loot, on the button and the wheel; and the cost, said
    assert dig["chance"] == round(100 * loot / (loot + noise)) and dig["lose"] == [f"noise: {engine.hp(-engine.DIG_NOISE_HIT)}"]
    before = len(g.log)
    g.choose(DIG_GO)
    new = g.log[before:]
    assert len(new) == 1 and new[0]["kind"] == "roll"  # one line per dig
    spin = new[0]
    # the wheel shows one slice per buried thing, and the roll lands in the slice that was dug
    assert len(spin["slices"]) == loot + noise and spin["slices"][-1]["b"] == 100
    hit = next(s for s in spin["slices"] if s["a"] < spin["roll"] <= s["b"])
    assert hit["ok"] == spin["ok"] and spin["win"] and spin["lose"]
    after = g.dig_view()
    assert len(after["buried"]) == loot + noise - 1
    assert (len(after["haul"]), after["heard"]) == ((1, 0) if spin["ok"] else (0, 1))
    assert g.health == (100 if spin["ok"] else 100 - engine.DIG_NOISE_HIT)
    # leaving keeps the haul
    food, kit = g.supplies, list(g.kit)
    got = list(g.dig["haul"])
    g.choose(DIG_LEAVE)
    assert g.card != DIG and g.dig is None
    gained = sum(2 if t["kind"] == "pack" else 1 for t in got if t["kind"] in ("pack", "can"))
    assert g.supplies == food + gained and all(t["item"] in g.kit for t in got if t["item"])

    # the pile changes shape from day to day
    shapes = set()
    for seed in range(12):
        g = at_horn(1, seed)
        v = g.dig_view()
        shapes.add((len(v["buried"]), sum(1 for b in v["buried"] if b["icon"] == "👂")))
    assert len(shapes) >= 4

    # digging on and on: either the pile runs dry or the third noise brings a Career, the haul is lost, and
    # the day is over: no card after it
    for salt in range(1, 40):
        g = at_horn(salt)
        food = g.supplies
        while g.card == DIG and not g.over:
            last = g.dig["heard"] == DIG_CAUGHT - 1
            if last:
                assert engine.hp(-engine.DIG_HIT) in g.options()[0]["lose"]
            g.choose(DIG_GO)
        caught = any("Footsteps" in e["text"] for e in g.log)
        if caught:
            costs = [n for _, n, c in g.hits if "dug" in c or "digging" in c]
            assert costs == [engine.DIG_NOISE_HIT] * (DIG_CAUGHT - 1) + [engine.DIG_HIT]
            assert not g.kit and g.day == 3
            break
    else:
        raise AssertionError("never caught")


def test_dig_dims_what_you_cant_use():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.arena.offers[2] = ["stream", "horn", "open"]
    g.kit, g.supplies = ["trident"], engine.SUPPLY_CAP
    g.choose(next(o for o in g.options() if o["place"] == "horn")["index"])
    for t, b in zip(g.dig["loot"], g.dig_view()["buried"]):
        if t["kind"] in ("pack", "can") or t["item"] in ("spear", "axe", "knife", "bow", "trident"):
            assert not b["use"], t


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
            pick = g.options()[-1 if g.card == DIG else 0]["index"]
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

# ---------------------------------------------------------------- beta 4 rules


def _value(e: dict) -> float:
    """A rough worth of an outcome, the same yardstick the card audit uses."""
    h = e.get("health", 0)
    v = h if h > 0 else -Game._card_damage(h)
    v += 12 * e.get("supplies", 0) + 20 * len(Game._list(e.get("add"))) - 20 * len(Game._list(e.get("remove")))
    v += 25 * bool(e.get("sponsor")) + 20 * bool(e.get("gift")) + 15 * e.get("kill", 0)
    v += 20 * (e.get("ally") == "joins_partner") - 25 * (e.get("ally") in ("dies", "lost"))
    v += e.get("wound", 0) + 15 * bool(e.get("study")) - 200 * bool(e.get("die"))
    return v


def test_every_roll_is_worth_taking():
    """A gamble's win beats every free option on the card, or nobody should take it."""
    for cid, c in cards().items():
        free = [_value(o.get("effects") or {}) for o in c["options"] if "roll" not in o and not o.get("need")]
        for o in c["options"]:
            if "roll" in o and free:
                assert _value(o["success"].get("effects") or {}) > max(free), (cid, o["label"])


def test_safe_means_safe():
    """A safe or hiding option that misses leaves a tribute at 60 health standing."""
    for cid, c in cards().items():
        for o in c["options"]:
            if o.get("tag") in ("safe", "hide") and "roll" in o:
                f = o["fail"].get("effects") or {}
                assert not f.get("die") and Game._card_damage(f.get("health", 0)) < 60, (cid, o["label"])


def test_no_single_hit_kills_a_healthy_tribute():
    assert engine.HIT_CAP < 70
    for cid, c in cards().items():
        for o in c["options"]:
            for b in ([o.get("success"), o.get("fail")] if "roll" in o else [o]):
                e = b.get("effects") or {}
                assert Game._card_damage(e.get("health", 0)) < 70 or e.get("die"), (cid, o["label"])


def test_food_has_a_cap_and_the_chips_say_so():
    g = Game(5, 9, SETUP)
    g.supplies = 10
    assert g.supplies == engine.SUPPLY_CAP
    g.supplies = -3
    assert g.supplies == 0
    g.supplies = engine.SUPPLY_CAP - 1
    share = "share what you can't carry: +1 star"
    assert g._chips({"supplies": 3}) == [engine.food(1), share]
    g.supplies = engine.SUPPLY_CAP
    assert g._chips({"supplies": 2}) == [share]
    assert g._chips({"supplies": -1}) == [engine.food(-1)]
    # What you can't carry, you share: a star, once a day.
    g.stars = 1
    g._food(2)
    assert g.stars == 2 and g.supplies == engine.SUPPLY_CAP
    assert g._chips({"supplies": 2}) == ["your pack is full of food"]
    g._food(2)
    assert g.stars == 2


def test_heal_chips_show_what_you_really_gain():
    g = Game(5, 9, SETUP)
    g.health = 90
    assert g._chips({"health": 30}) == [engine.hp(10)]
    g.health = 100
    assert g._chips({"health": 30}) == ["you're already healthy"]


def test_sponsors_send_what_you_are_missing():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit, g.health, g.supplies = ["medkit", "rope", "knife"], 100, engine.SUPPLY_CAP
    stars = g.stars
    g._gift()
    assert g.supplies == engine.SUPPLY_CAP and g.kit == ["medkit", "rope", "knife"] and g.stars >= stars
    g.health = 50
    g._gift()
    assert g.health == 50 + engine.MEDICINE


def test_a_second_copy_stays_where_it_is():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.kit = ["knife", "rope", "bow"]
    g._gain("knife")
    g._advance(("night",))
    assert g.card != SWAP and g.kit == ["knife", "rope", "bow"]
    assert any("already carry" in e["text"] for e in g.log)


def test_your_ally_takes_one_killing_blow_for_you():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.allies = ["Rue"]
    g.health = 30
    g._hurt(50, "a Career's spear")
    assert g.health == 30 and g.allies == [] and g.saved and "Rue" in g.fallen_today
    g.allies = ["Thresh"]
    g._hurt(50, "a Career's spear")
    assert g.health <= 0 and g.allies == ["Thresh"]  # only once a run
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.allies, g.health = ["Rue"], 10
    g._hurt(20, "starvation")  # nobody can eat for you
    assert g.allies == ["Rue"] and g.health <= 0


def test_wounds_carry_into_the_final_fight():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    chip = g._chips({"wound": 25})
    assert any("final fight" in c for c in chip)
    g._apply({"wound": 25}, cards()["nemesis_clash"])
    assert g.wounds == 25 and "🎯" in g.days[g.day]
    g.allies = []
    g._start_duel()
    assert g.opponent_id == g.nemesis and g.opp_health == engine.OPPONENT_HEALTH["nemesis"] - 25
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.wounds, g.allies = 500, []
    g._start_duel()
    assert g.opp_health == engine.NEMESIS_FLOOR  # wounds weaken them; only you finish them


def test_a_kill_that_can_only_fall_on_the_nemesis_wounds_them():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.pool = [g.nemesis]
    g.target = None
    g._kill(1)
    assert g.nemesis in g.pool and g.wounds == 30


def test_studying_the_nemesis_reads_their_feints():
    plain = sum(mg.fight_params(random.Random(i), "careful", True, False, 0)["feint"] >= 0 for i in range(2000))
    read = sum(mg.fight_params(random.Random(i), "careful", True, False, 0, read=True)["feint"] >= 0 for i in range(2000))
    assert read < plain * 0.65
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g._apply({"study": True}, cards()["nemesis_sighting"])
    assert g.study and not g._chips({"study": True})  # you only learn it once


def test_the_snare_needs_rope():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.card, g.target = "nemesis_clash", None
    snare = [o for o in g.options() if o["label"] == "Lead them into your snare"]
    g.kit = ["rope"]
    with_rope = [o for o in g.options() if o["label"] == "Lead them into your snare"]
    assert with_rope and (not snare or snare[0].get("locked"))


def test_every_gamble_says_what_you_win():
    """A roll or a minigame never shows an empty win side: getting away clean says so."""
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.health, g.supplies, g.kit, g.allies = 70, 3, [], ["Rue"]
    for cid, c in cards().items():
        if c["kind"] not in ("event", "gamemaker", "feast", "finale"):
            continue
        g.card = cid
        g.target = g._pick_target(c) if c.get("target") else None
        for o in g.options():
            if o["chance"] is not None or o.get("minigame"):
                assert o["win"], (cid, o["label"])


def test_scores_spread_out():
    """Two tributes who did different things rarely tie: no score covers 8% of runs."""
    from collections import Counter
    scores = Counter(play(BOTS["smart"], seed).score() for seed in range(400))
    assert scores.most_common(1)[0][1] < 0.08 * 400, scores.most_common(3)


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


# ---------------------------------------------------------------- beta 5 rules


def test_only_the_first_daily_run_is_ranked():
    first = client.post("/api/beta/run", json={"mode": "daily", "district": 7}).json()
    again = client.post("/api/beta/run", json={"mode": "daily", "district": 7, "ranked": False}).json()
    assert first["ranked"] and not again["ranked"]
    for v in (first, again):
        while not v["over"]:
            o = v["options"][-1] if v["card"] != "swap" else v["options"][0]
            game = o.get("minigame")
            param = {"fish": lambda p: mg.bot_fish(p, 0.5, random.Random(1)), "hunt": lambda p: mg.bot_hunt(p, 0.5, random.Random(1)),
                     "hide": lambda p: mg.bot_hide(p, 0.5, random.Random(1)), "fight": lambda p: mg.bot_fight(p, 0.5, random.Random(1))}[game["game"]](game) if game else None
            v = client.post("/api/beta/run/choose", json={"token": v["token"], "index": o["index"], "param": param}).json()
    assert v["result"]["ranked"] is False and "(replay)" in v["result"]["share"]
    res = client.post("/api/beta/leaderboard", json={"token": v["token"], "nickname": "Replayer"})
    assert res.status_code == 400 and "first run" in res.json()["detail"]


def test_a_gamemaker_morning_still_lets_you_choose_where_to_go():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.gm_due = True
    g._night()
    assert g.card.startswith("gm_")
    day = g.day
    safe = next(o for o in g.options() if not o.get("minigame"))
    g.choose(safe["index"])
    if not g.over:
        assert g.card == PLACES and g.day == day  # the promised places are still there


def test_the_refuge_is_open_the_day_caesar_says_so():
    for seed in range(30):
        a = Game(seed, 1, SETUP).arena
        assert a.refuge in a.offers[engine.EDGE_CLOSES]


def test_an_option_a_better_one_replaces_is_hidden():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.card, g.kit = "high_river", []
    assert "Ford the river" in [o["label"] for o in g.options()]
    g.kit = ["rope"]
    labels = [o["label"] for o in g.options()]
    assert "Ford the river" not in labels and "Float across on a rope line" in labels


def test_stars_buy_help_in_the_final_fight():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    g.allies = []
    g.stars, g.health = 5, 40
    g._start_duel()
    menu = {m["key"]: m for m in g.status()["menu"]}
    assert menu["medicine"]["can"] and not menu["food"]["can"] and menu["food"]["note"] == "no time to eat"
    g.choose(menu["medicine"]["index"])
    assert g.health == 40 + engine.MEDICINE and g.order is None and g.card == DUEL  # it landed at once


def test_allies_die_fighting_at_the_horn():
    g = Game(5, 9, new_setup(12, "Jake", ["Sam", "Lou"]))
    g.choose(len(g.arena.layout))
    assert g.allies == ["Sam", "Lou"] and g.nemesis in g.pool
    full = g._nemesis_health()
    g._start_duel()
    story = next(e["text"] for e in g.log if e["kind"] == "story" and "reached the horn" in e["text"])
    assert "Sam and Lou were gone" in story and g.opp_health == full - 2 * engine.ALLY_LAST_STAND
    assert not any("in a row" in e["text"] for e in g.log)


def test_killing_your_ally_shows_once():
    g = Game(5, 9, SETUP)
    g.choose(len(g.arena.layout))
    snap = g._snap()
    g._apply({"ally": "dies", "kill": 1}, cards()["betrayal"])
    g._delta(snap)
    assert g.log[-1]["text"] == "⚔️ Gingi"


def test_the_score_says_how_it_was_built():
    g = play(BOTS["smart"], 3)
    p = g.score_parts()
    assert p["total"] == g.score() == round(p["points"] * p["district"] * (1 + 0.1 * p["stars"]))


def test_a_lost_final_fight_says_how_close_it_was():
    for seed in range(200):
        g = play(BOTS["casual"], seed)
        if g.cause and "final fight" in g.cause:
            assert "you fell with" in g.recap and f"on {g.opp_health} health" in g.recap
            return
    raise AssertionError("no final fight lost")
