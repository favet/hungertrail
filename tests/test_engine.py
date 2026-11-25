import random

import pytest

from hungertrail.game import lore
from hungertrail.game.bots import BOTS, play
from hungertrail.game.engine import Game, cards, new_setup, replay

SETUP = new_setup(12, "snares", "Jake", ["Rye", "Wren"])


def _run(seed, salt, pick_seed):
    g = Game(seed, salt, SETUP)
    r = random.Random(pick_seed)
    choices = []
    while not g.over:
        o = r.choice(g.options())
        c = [o["index"], r.randint(0, 3)]
        choices.append(c)
        g.choose(*c)
    return g, choices


def test_replay_is_deterministic():
    g, choices = _run(7, 99, 3)
    again = replay(7, 99, SETUP, choices)
    assert again.log == g.log and again.score() == g.score()


def test_same_seed_same_arena_different_salt_different_dice():
    a, b = Game(5, 1, SETUP), Game(5, 2, SETUP)
    assert a.arena.deck == b.arena.deck and a.arena.schedule == b.arena.schedule
    outcomes = {tuple(e["text"] for e in _run(5, salt, 1)[0].log) for salt in range(20)}
    assert len(outcomes) > 1


@pytest.mark.parametrize("bot", list(BOTS))
def test_every_run_ends(bot):
    r = random.Random(bot)
    for i in range(300):
        g = play(BOTS[bot], i, i, r)
        assert g.over and g.placement is not None
        assert 1 <= g.placement <= 24
        assert g.won == (g.placement == 1)
        assert g.steps <= 30


def test_cards_are_well_formed():
    ids = set(cards())
    for cid, c in cards().items():
        assert c["kind"] in {"launch", "chain", "event", "gamemaker", "feast", "betrayal", "finale"}, cid
        assert c["options"], cid
        # Every card must have at least one option with no requirement, so nobody gets stuck.
        assert any(not o.get("need") for o in c["options"]), cid
        for o in c["options"]:
            branches = [o["success"], o["fail"]] if "roll" in o else [o]
            for br in branches:
                e = br.get("effects", {})
                if "next" in e:
                    assert e["next"] in ids, (cid, e["next"])
                for key in ("add", "remove"):
                    vals = e.get(key, [])
                    for item in [vals] if isinstance(vals, str) else vals:
                        assert item in lore.ITEMS, (cid, item)


def test_invalid_choice_rejected():
    g = Game(1, 1, SETUP)
    with pytest.raises(ValueError):
        g.choose(99)
