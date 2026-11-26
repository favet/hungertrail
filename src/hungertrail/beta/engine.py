"""Hunger Trail BETA rules engine (beta 2).

Same contract as the live engine: pure and deterministic, a run is (seed, salt, setup, choices).

- Three stats on screen: health, supplies (food and water together, in days) and a 3-slot kit.
- Day 1 is the bloodbath draft: seeded loot around the Cornucopia, closer is better and deadlier.
- Every other day you choose where to go: 3 of 5 places, picked by the daily seed. Each place
  does one thing for you (water, rest, food, loot, sponsors), then deals an event from its own
  stack. Night runs itself.
- Nothing is hidden. Every option states its stakes as chips computed from the same effects the
  engine applies. Sponsors pay only through options that say so.
- The Gamemakers come for players who stay at the edge (two days running, or any day from day
  5), and the edge closes on day 6. Caesar says so out loud the night before.
- A failed roll within NEAR points is a near miss: the damage is halved.
- One named Career, the nemesis, is the same for everyone today: their kills show in the sky,
  they cross your path, and if they're alive at the end they're your final opponent.
- The final fight runs on health, yours and theirs. Each exchange you pick a stance, then read their
  wind-up and block it; a clean block opens a moment to strike back.
- Minigames (minigames.py) are played in the browser and replayed here from the player's inputs:
  fishing at the stream, hunting on the ridge, the sift at the horn, holding still when you hide,
  and every exchange of the final fight. Their layouts come from the daily seed (the same for
  everyone today); only the fight's tells come from the run's own dice.
- Rolls and minigames the player chose are logged as "roll" entries so the screen can show what happened.
"""

from __future__ import annotations

import hashlib
import random
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from . import lore
from . import minigames as mg

START_TRIBUTES = 24
MAX_DAYS = 8
DAMAGE = 3.2  # global lethality dial for card damage, tuned with the bots
HIT_CAP = 85  # no single card hit is bigger than this, so one miss never kills from full health
NEAR = 10  # a failed roll this close to the target is a near miss
# The final fight. stance: (damage your strike deals, damage you take if you don't block)
FIGHT = {"aggressive": (26, 30), "careful": (14, 18)}
FIGHT_NEMESIS_HIT = 8  # the nemesis hits harder
FIGHT_PER_KILL, FIGHT_KILLS_CAP = 4, 12  # your kills make your strikes count
FIGHT_TIRED = 10  # from this exchange on, every blow counts double: nobody fights forever
OPPONENT_HEALTH = {"nemesis": 100, "other": 70}
OPEN_GIFT = 55  # odds a sponsor pays when you go out in the open
HIDE_HEAL = 25
EDGE_WATCHED = 5  # from this day, any day at the edge brings the Gamemakers
EDGE_CLOSES = 6  # from this day the edge places are gone
USE = 99  # choice index for tapping the medkit in your kit
DRAFT, PLACES, SWAP, DUEL, SIFT = "draft", "places", "swap", "duel", "sift"

# The sift: a 5x5 grid of rubble at the horn. Dig quietly (costs a day of supplies) or loudly (free,
# more digs, but Careers hear it). A small dig turns one tile; a big dig turns a cross of five.
SIFT_SIZE = 5
SIFT_DIGS = {"quiet": 3, "loud": 6}
SIFT_SMALL, SIFT_BIG, SIFT_STOP = 10, 40, 70  # choice index = SIFT_SMALL + tile, SIFT_BIG + tile
SIFT_TILES = {
    # what: emoji, what the screen calls it
    "food": ("🥫", "food"),
    "clue": ("🔮", "a Capitol map"),
    "snare": ("🪤", "a snare"),
    "mine": ("💥", "a mine"),
    "career": ("👁️", "a Career"),
    "empty": ("·", "rubble"),
}
SIFT_HURT = {"snare": (12, "a snare in the rubble"), "mine": (30, "a mine left from the bloodbath"),
             "career": (25, "a Career who heard you digging")}
SIFT_ITEMS = ("knife", "rope", "medkit", "iodine", "sleeping_bag", "spear", "axe", "knife", "rope", "medkit", "bow")

# Cornucopia rings: base odds, damage on a miss, how it reads.
RINGS = {
    "edge": (85, 15, "At the edge"),
    "mid": (65, 30, "Halfway in"),
    "horn": (45, 45, "At the mouth of the horn"),
}

# Places, in a fixed order (the order is the choice index): emoji, label, hint, tag.
PLACE_KEYS = ("stream", "rocks", "ridge", "horn", "open", "feast")
PLACES_INFO = {
    "stream": ("🌊", "Go to the stream", "Water and food. Everyone else needs it too.", "safe"),
    "rocks": ("🪨", "Hide in the rocks", "Rest and heal. The edge of the arena, where it's quiet.", "hide"),
    "ridge": ("⛰️", "Climb the ridge", "Food up high, if you can get it. The edge of the arena.", "safe"),
    "horn": ("🔺", "Head for the horn", "Loot and kills. The Careers camp there.", "bold"),
    "open": ("🎥", "Go out in the open", "The cameras find you. So might everyone else.", "bold"),
    "feast": ("🍽️", "Go to the feast", "A pack with your district's number on it. Everyone left alive will be there.", "bold"),
}
EDGE = ("rocks", "ridge")
EPITHETS = ("the Butcher", "the Wolf", "Smiler", "the Hound", "the Knife")


@cache
def cards() -> dict[str, dict]:
    data = yaml.safe_load((Path(__file__).parent / "cards.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data}


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def health_word(h: int) -> str:
    if h >= 70:
        return "Good"
    if h >= 45:
        return "Fair"
    if h >= 20:
        return "Poor"
    return "Very poor"


def _sign(n: int) -> str:
    return f"+{n}" if n >= 0 else f"−{-n}"


class Arena:
    """Everything decided by the daily seed alone: the same for every player that day."""

    def __init__(self, seed: int, district: int):
        r = _rng(seed, "arena")
        self.seed = seed
        events = sorted(k for k, c in cards().items() if c["kind"] == "event")
        r.shuffle(events)
        self.deck = events
        gms = sorted(k for k, c in cards().items() if c["kind"] == "gamemaker")
        r.shuffle(gms)
        self.gamemakers = gms
        self.feast_day = r.choice([4, 5])

        # The loot around the horn. Same layout for everyone today: the bloodbath talking point.
        h = _rng(seed, "horn")
        edge = h.sample(["pack", "knife", "rope", "medkit"], 2)
        mid = h.sample([i for i in ("spear", "axe", "sleeping_bag", "iodine", "rope", "medkit") if i not in edge], 2)
        top = h.choice(["bow", "trident"])
        second = h.choice([i for i in ("crate", "bow", "trident", "axe") if i != top and i not in mid])
        self.layout = [(i, "edge") for i in edge] + [(i, "mid") for i in mid] + [(top, "horn"), (second, "horn")]

        # The nemesis: a Career from the seed. Same for everyone, except a player from that district.
        n = _rng(seed, "nemesis")
        careers = [f"the District {d} {g}" for d in (1, 2, 4) for g in ("boy", "girl")]
        self.nemesis = n.choice([c for c in careers if not c.startswith(f"the District {district} ")])
        self.epithet = n.choice(EPITHETS)

        others = [f"the District {d} {g}" for d in range(1, 13) for g in ("boy", "girl") if d != district]
        others.remove(self.nemesis)
        r.shuffle(others)
        self.death_order = others + ["partner"]
        r.shuffle(self.death_order)

        sched = [0] * (MAX_DAYS + 2)
        sched[1] = r.randint(8, 11)
        left = (START_TRIBUTES - 1 - sched[1]) - 2
        for _ in range(left):
            sched[r.randint(2, MAX_DAYS - 1)] += 1
        self.schedule = sched

        # Where you can go each day: 3 of the 5 places until the edge closes.
        self.offers: dict[int, list[str]] = {}
        for day in range(2, MAX_DAYS + 1):
            if day >= EDGE_CLOSES:
                offer = ["stream", "horn", "open"]
            else:
                offer = _rng(seed, "places", day).sample(PLACE_KEYS[:5], 3)
            if day == self.feast_day:
                offer = [p for p in offer if p != "horn"][:2] + ["feast"]
            self.offers[day] = sorted(offer, key=PLACE_KEYS.index)


class Game:
    def __init__(self, seed: int, salt: int, setup: dict):
        self.setup = setup
        self.district: int = setup["district"]
        self.partner: str = setup["partner"]
        self.arena = Arena(seed, self.district)
        self.seed = seed
        self.salt = salt
        self.schedule = list(self.arena.schedule)
        self.nemesis = self.arena.nemesis
        self.nemesis_kills = 2  # the bloodbath

        self.day = 1
        self.health = 100
        self.supplies = 2 + (2 if self.district == 9 else 0)
        self.kit: list[str] = []
        self.gm_due = False
        self.edge_streak = 0
        self.place: str | None = None
        self.kills = 0
        self.pool = list(self.arena.death_order) + [self.nemesis]
        self.allies: list[str] = []
        self.ally_names: list[str] = list(setup.get("allies", []))
        self.fallen_today: list[str] = []
        self.announced = 0
        self.used: set[str] = set()
        self.log: list[dict] = []
        self.days: list[list[str]] = [[] for _ in range(MAX_DAYS + 2)]
        self.over = False
        self.won = False
        self.cause: str | None = None
        self.recap: str | None = None
        self.placement: int | None = None
        self.steps = 0
        self.card = ""
        self.target: str | None = None
        self.opponent: str | None = None
        self.opponent_id: str | None = None
        self.opp_health = 0
        self.opp_max = 0
        self.exchanges = 0
        self.sift: dict | None = None
        self.item_queue: list[str] = []
        self.grabs = 0
        self.taken: set[int] = set()
        self.pending_item: str | None = None
        self.resume: tuple = ()
        self.new_kills: list[str] = []
        self.last: tuple = ("", None, 100)
        self.kills_today = 0
        self.killed: set[str] = set()

        r = _rng(seed, "allies", self.district)
        for _ in self.ally_names:
            self.pool.remove(r.choice([t for t in self.pool if t not in ("partner", self.nemesis)]))
        self.reserved_allies = len(self.ally_names)

        self._say(f"District {self.district} ({lore.DISTRICTS[self.district]}). {START_TRIBUTES} tributes rise into the arena.", "system")
        self.card = DRAFT
        self._say(
            "Sixty seconds on the plate. Then the gong. Supplies lie scattered around the Cornucopia: "
            "the closer to the horn, the better they are, and the more people will die for them.",
            "event",
        )

    # ------------------------------------------------------------------ public API
    @property
    def remaining(self) -> int:
        return 1 + len(self.pool) + len(self.allies) + self.reserved_allies

    @property
    def nemesis_name(self) -> str:
        return self.arena.epithet

    def options(self) -> list[dict]:
        if self.over:
            return []
        if self.card == DRAFT:
            return self._draft_options()
        if self.card == PLACES:
            return self._place_options()
        if self.card == SWAP:
            return self._swap_options()
        if self.card == DUEL:
            return self._duel_options()
        if self.card == SIFT:
            return self._sift_options()
        card = cards()[self.card]
        out = []
        for i, o in enumerate(card["options"]):
            if not self._has(o.get("need")):
                continue
            if "roll" in o:
                win = self._chips(o["success"].get("effects", {}))
                lose = self._chips(o["fail"].get("effects", {}), card)
                if o.get("minigame") == "hide":
                    out.append(self._opt(i, o["label"], o.get("hint", "") or "Hold to stay down. Let go to breathe.", None,
                                         o.get("tag", "safe"), win, lose, minigame=self._hide_params(card["id"], o["roll"]["base"])))
                    continue
                out.append(self._opt(i, o["label"], o.get("hint", ""), self._chance(o["roll"]), o.get("tag", "safe"), win, lose))
            else:
                e = o.get("effects", {})
                out.append(self._opt(i, o["label"], o.get("hint", ""), None, o.get("tag", "safe"), self._chips(e, card), []))
        return out

    def can_use_medkit(self) -> bool:
        return not self.over and "medkit" in self.kit and self.health < 100 and self.card != SWAP

    def choose(self, index: int, param=None) -> None:
        if self.over:
            raise ValueError("run is over")
        if index == USE:
            return self._use_medkit()
        valid = {o["index"]: o for o in self.options()}
        if index not in valid:
            raise ValueError("invalid option")
        self.steps += 1
        o = valid[index]
        self.last = (o["label"], o["chance"], self.health)
        snap = self._snap()
        self._say(o["label"], "choice")
        game = o.get("minigame")
        if self.card == DRAFT:
            dest = self._resolve_draft(index)
        elif self.card == PLACES:
            dest = self._resolve_place(index, param, game)
        elif self.card == SWAP:
            dest = self._resolve_swap(index)
        elif self.card == DUEL:
            dest = ("round", self._resolve_duel(index, param, game))
        elif self.card == SIFT:
            dest = self._resolve_sift(index)
        else:
            dest = self._resolve_card(index, param, game)
        self._delta(snap)
        if dest and dest[0] == "round":
            if dest[1]:
                self._say(dest[1], "prompt")  # the next round, after this round's result
            return
        self._advance(dest)

    def _use_medkit(self) -> None:
        if not self.can_use_medkit():
            raise ValueError("can't use the medkit now")
        self.steps += 1
        snap = self._snap()
        self._say("Use the medkit", "choice")
        self.kit.remove("medkit")
        self.health = min(100, self.health + 40)
        self._say("You clean and bind your wounds. It stings like nothing else.", "result")
        self._delta(snap)

    # ------------------------------------------------------------------ option helpers
    def _opt(self, index, label, hint, chance, tag, win, lose, **extra) -> dict:
        deadly = "☠️" in lose
        return {
            "index": index, "label": self._fmt(label), "hint": self._fmt(hint), "chance": chance,
            "tag": tag, "win": win, "lose": lose, "deadly": deadly, **extra,
        }

    def _chips(self, e: dict, card: dict | None = None) -> list[str]:
        out = []
        if "health" in e:
            n = e["health"] if e["health"] > 0 else -self._card_damage(e["health"])
            if n > -self.health:  # a hit that would kill you shows as ☠️ alone, below
                out.append(f"❤️{_sign(n)}")
        if e.get("supplies"):
            out.append(f"🎒{_sign(e['supplies'])}")
        for item in self._list(e.get("add")):
            out.append(f"{lore.item_emoji(item)} {lore.item_name(item)}")
        for item in self._list(e.get("remove")):
            if item in self.kit:
                out.append(f"−{lore.item_emoji(item)}")
        if e.get("sponsor"):
            out.append("🎁 sponsor")
        if e.get("gift"):
            out.append("🎁 a find")
        if e.get("kill"):
            out.append("⚔️ kill")
        if e.get("ally") in ("dies", "lost") and self.allies:
            out.append(f"−{self.allies[0]}")
        if e.get("ally") == "joins_partner":
            out.append(f"+{self.partner}")
        if e.get("die") or ("health" in e and e["health"] < 0 and self._card_damage(e["health"]) >= self.health):
            out.append("☠️")
        return out

    @staticmethod
    def _card_damage(health_effect: int) -> int:
        return min(HIT_CAP, round(-health_effect * DAMAGE))

    @staticmethod
    def _list(v) -> list:
        return [] if not v else [v] if isinstance(v, str) else list(v)

    # ------------------------------------------------------------------ the bloodbath draft
    def _draft_options(self) -> list[dict]:
        out = []
        for i, (item, ring) in enumerate(self.arena.layout):
            if i in self.taken:
                continue
            base, dmg, where = RINGS[ring]
            chance = self._chance({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]})
            desc = f"{lore.PACKS[item][2]} days of supplies." if item in lore.PACKS else lore.ITEMS[item][2]
            lose = [f"❤️−{dmg}"] + (["☠️"] if dmg >= self.health else [])
            out.append(self._opt(i, f"Grab the {lore.item_name(item)}", f"{where}. {desc}", chance,
                                 "bold" if ring != "edge" else "safe",
                                 [f"{lore.item_emoji(item)} {lore.item_name(item)}"], lose))
        label = "Run for the trees" if not self.grabs else "Run while you can"
        hint = "You'll arrive with nothing but your life." if not self.grabs else "Take what you have and go."
        out.append(self._opt(len(self.arena.layout), label, hint, None, "hide", [], []))
        return out

    def _resolve_draft(self, index: int) -> tuple:
        if index == len(self.arena.layout):
            self._say(self._pick(
                "You sprint into the trees and don't look back until the screaming stops.",
                "You run. Behind you, the cannons will wait until it's over.",
            ) if not self.grabs else "You run with what you've got.", "result")
            if not self.grabs:
                self.days[1].append("🌲")
            return ("bloodbath",)
        item, ring = self.arena.layout[index]
        base, dmg, _ = RINGS[ring]
        chance = self._chance({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]})
        self.taken.add(index)
        self.grabs += 1
        roll, ok, near = self._reveal(chance)
        name = lore.item_name(item)
        if ok:
            self._say(self._pick(f"You snatch the {name} and keep moving.", f"The {name} is yours. Nobody touched you."), "result")
            self._take(item)
            return ("draft",) if self.grabs < 2 else ("bloodbath",)
        if near:
            self._say(f"Close call. You get the {name}, and a blade across the arm for it. You run.", "result")
            self._take(item)
            self._hurt(dmg // 2, "the bloodbath")
        else:
            self._say(self._pick(
                f"Someone gets to the {name} first. You get a fist, then a knife, and you run.",
                f"You never reach the {name}. You crawl away bleeding.",
            ), "result")
            self._hurt(dmg, "the bloodbath")
        return ("bloodbath",)

    def _take(self, item: str) -> None:
        if item in lore.PACKS:
            self.supplies += lore.PACKS[item][2]
        else:
            self._gain(item)
        self.days[1].append(lore.item_emoji(item))

    def _end_bloodbath(self) -> None:
        self._scheduled_deaths(1)
        n = len(self.fallen_today)
        self.announced = n
        self._say(f"The bloodbath ends. The cannons fire all at once. You count {n}.", "cannon")
        self._say(f"{lore.HOST}: \"Two of those were {self.nemesis}. The Capitol already has a name for that one: "
                  f"{self.nemesis_name}.\"", "caesar")
        if self.ally_names:
            names = " and ".join(self.ally_names)
            self.allies = list(self.ally_names)
            self.reserved_allies = 0
            self._say(f"At dusk, {names} find{'s' if len(self.ally_names) == 1 else ''} you. The alliance holds. For now.", "story")
        self._night()

    # ------------------------------------------------------------------ kit
    def _gain(self, item: str) -> None:
        if len(self.kit) < lore.KIT_SLOTS:
            self.kit.append(item)
        elif self.pending_item is None:
            self.pending_item = item
        else:
            self.item_queue.append(item)  # one full-kit question at a time

    def _swap_options(self) -> list[dict]:
        new = self.pending_item
        out = [
            self._opt(i, f"Drop the {lore.item_name(k)}", lore.ITEMS[k][2], None, "safe",
                      [f"{lore.item_emoji(new)} {lore.item_name(new)}"], [])
            for i, k in enumerate(self.kit)
        ]
        out.append(self._opt(lore.KIT_SLOTS, f"Leave the {lore.item_name(new)}", lore.ITEMS[new][2], None, "safe", [], []))
        return out

    def _resolve_swap(self, index: int) -> tuple:
        new = self.pending_item
        self.pending_item = None
        if index < lore.KIT_SLOTS:
            old = self.kit[index]
            self.kit[index] = new
            self._say(f"You leave the {lore.item_name(old)} behind and take the {lore.item_name(new)}.", "result")
        else:
            self._say(f"You leave the {lore.item_name(new)} where it lies.", "result")
        while self.item_queue and self.pending_item is None:
            self._gain(self.item_queue.pop(0))
        return self.resume

    # ------------------------------------------------------------------ flow
    def _advance(self, dest: tuple) -> None:
        if self.over or not dest:
            return
        if self.pending_item:
            self.resume = dest
            self.card = SWAP
            self._say(f"Your pack is full. Something has to go if you want the {lore.item_name(self.pending_item)}.", "prompt")
            return
        kind = dest[0]
        if kind == "draw":
            self._draw(dest[1])
        elif kind == "night":
            self._night()
        elif kind == "bloodbath":
            self._end_bloodbath()
        elif kind == "draft":
            self.card = DRAFT
            self._say("The Careers are at the horn now. One more grab, or run?", "prompt")
        elif kind == "duel":
            self._start_duel()
        elif kind == "sift":
            self.card = SIFT
            self._say("The bloodbath left the ground around the horn a mess of crates and rubble. Some of it was "
                      "never picked over. Digging is slow, and it isn't quiet.", "event")

    def _draw(self, card_id: str) -> None:
        self.card = card_id
        card = cards()[card_id]
        self.used.add(card_id)
        self.target = self._pick_target(card) if card.get("target") else None
        self.days[self.day].append(card["emoji"])
        self._say(self._fmt(card["text"]), "event")

    def _pick_target(self, card: dict) -> str | None:
        want = card["target"]
        if want == "nemesis":
            return self.nemesis if self.nemesis in self.pool else None
        cands = [t for t in self.pool if t not in ("partner", self.nemesis) and (want == "any" or t.endswith(want))]
        if not cands:
            return None
        return _rng(self.seed, card["id"], self.district).choice(sorted(cands))

    def _resolve_card(self, index: int, param=None, game: dict | None = None) -> tuple:
        card = cards()[self.card]
        opt = card["options"][index]
        near = False
        if game and game["game"] == "hide":
            how, _ = mg.hide_run(game, mg.clean_toggles(param, game["duration"]))
            ok = how == "hidden"
            self._played("hide", ok, {"hidden": "HIDDEN", "spotted": "SPOTTED", "gasp": "YOU GASPED"}[how],
                         "held still" if ok else "they saw you" if how == "spotted" else "out of breath")
            branch = opt["success" if ok else "fail"]
            text, effects = branch.get("text", ""), branch.get("effects", {})
        elif "roll" in opt:
            _, ok, near = self._reveal(self._chance(opt["roll"]))
            branch = opt["success" if ok else "fail"]
            text, effects = branch.get("text", ""), branch.get("effects", {})
        else:
            text, effects = opt.get("text", ""), opt.get("effects", {})
        if near and effects.get("health", 0) < 0 and not effects.get("die"):
            text = "Close call. " + text
        if text:
            self._say(self._fmt(text), "result")
        nxt = self._apply(effects, card, near)
        if self.over:
            return ()
        if nxt == "duel":
            return ("duel",)
        if nxt:
            return ("draw", nxt)
        return ("night",)

    def _morning(self) -> None:
        self.fallen_today = []
        self.announced = 0
        self.kills_today = 0
        self.place = None
        self._say(f"Day {self.day}. {self.remaining} tributes remain.", "day")
        if self.remaining <= 2:
            return self._start_duel()
        if self.day >= MAX_DAYS:
            return self._draw("flood")
        if self.gm_due:
            self.gm_due = False
            gm = next((g for g in self.arena.gamemakers if g not in self.used), self.arena.gamemakers[0])
            return self._draw(gm)
        if "betrayal" not in self.used and self._eligible(cards()["betrayal"]):
            return self._draw("betrayal")
        self.card = PLACES
        self._say("Where do you go today?", "prompt")

    def _eligible(self, card: dict) -> bool:
        lo, hi = card.get("days", [1, MAX_DAYS + 1])
        if not lo <= self.day <= hi:
            return False
        if card.get("target") and not self._pick_target(card):
            return False
        req = card.get("requires", {})
        if req.get("ally") and not self.allies:
            return False
        if req.get("partner") and "partner" not in self.pool:
            return False
        if req.get("nemesis") and self.nemesis not in self.pool:
            return False
        if "min_remaining" in req and self.remaining < req["min_remaining"]:
            return False
        if "max_remaining" in req and self.remaining > req["max_remaining"]:
            return False
        if "max_health" in req and self.health > req["max_health"]:
            return False
        return True

    def _night(self) -> None:
        snap = self._snap()
        fed = self.supplies > 0
        if fed:
            self.supplies -= 1
        else:
            self._say("You go to sleep with nothing to eat or drink.", "status")
            self._hurt(10 if self.district == 12 else 20, "starvation")
            if self.over:
                return
        if fed:
            self.health = min(100, self.health + 5)
        self._delta(snap, night=True)

        # The Gamemakers watch the edge of the arena.
        at_edge = self.place in EDGE
        self.edge_streak = self.edge_streak + 1 if at_edge else 0
        if at_edge and (self.edge_streak >= 2 or self.day >= EDGE_WATCHED):
            self.gm_due = True
            self.edge_streak = 0
        for line in self._caesar(at_edge):
            self._say(line, "caesar")
        if self.day + 1 == self.arena.feast_day:
            self._say(f"{lore.HOST} announces a feast at the Cornucopia tomorrow at dawn.", "caesar")

        scheduled = self._scheduled_deaths(self.day)
        n = len(self.fallen_today) - self.announced
        if n:
            self._say(f"{'A cannon fires' if n == 1 else f'{n} cannons fire'} in the distance today.", "cannon")
        sky = "The anthem plays. " + self._faces()
        if scheduled and self.nemesis in self.pool and self.day > 1:
            self.nemesis_kills += 1 + (len(scheduled) >= 4)
            sky += f" {self.nemesis_name[0].upper()}{self.nemesis_name[1:]} is up to {self.nemesis_kills} kills."
        self._say(sky, "sky")
        self.day += 1
        self._morning()

    def _faces(self) -> str:
        """Tonight's dead: by name when they mattered to you, otherwise just counted."""
        fallen = self.fallen_today
        if not fallen:
            return "No faces in the sky tonight."
        if len(fallen) <= 3:
            return "Faces in the sky: " + ", ".join(self._name(t) for t in fallen) + "."
        known = [t for t in fallen if t in ("partner", self.nemesis) or t in self.ally_names or t in self.killed]
        if not known:
            return f"{len(fallen)} faces in the sky."
        rest = len(fallen) - len(known)
        return "Faces in the sky: " + ", ".join(self._name(t) for t in known) + f", and {rest} more."

    def _caesar(self, at_edge: bool) -> list[str]:
        d = f"District {self.district}"
        out = []
        if self.gm_due:
            out.append(f"{lore.HOST}: \"{d} has been hiding at the edge of the arena, and the Gamemakers have noticed. "
                       "Tomorrow should be exciting.\"")
        elif at_edge and self.day + 1 < EDGE_CLOSES:
            out.append(f"{lore.HOST}: \"{d} spent the day at the edge. One more, and the Gamemakers will come looking.\"")
        elif self.kills_today:
            out.append(self._pick(f"{lore.HOST}: \"What a day for {d}! The Capitol can't look away.\"",
                                  f"{lore.HOST}: \"{d} is out for blood, ladies and gentlemen.\""))
        elif self.day > 1:
            out.append(self._pick(f"{lore.HOST}: \"{d} is still with us. Quietly.\"",
                                  f"{lore.HOST}: \"A steady day for {d}. The arena won't stay this kind.\""))
        if self.day + 1 == EDGE_WATCHED:
            out.append(f"{lore.HOST}: \"From tomorrow the Gamemakers are watching the edge of the arena. Anyone hiding there "
                       "will be found.\"")
        elif self.day + 1 == EDGE_CLOSES:
            out.append(f"{lore.HOST}: \"Tomorrow the arena closes in. The rocks and the ridge are gone.\"")
        return out

    def _scheduled_deaths(self, day: int) -> list[str]:
        n = self.schedule[day]
        self.schedule[day] = 0
        cands = [t for t in self.pool if t != self.nemesis]
        alive_rival = self.allies or self.reserved_allies or self.nemesis in self.pool
        n = max(0, min(n, len(cands) - (0 if alive_rival else 1)))
        fallen = cands[:n]
        self.pool = [t for t in self.pool if t not in fallen]
        self.fallen_today.extend(fallen)
        return fallen

    # ------------------------------------------------------------------ places
    def _hide_heal(self) -> int:
        return HIDE_HEAL * (2 if self.district == 7 else 1) + (15 if "sleeping_bag" in self.kit else 0)

    def _ridge_chance(self) -> int:
        if self.district == 3:
            return 90
        if "rope" in self.kit:
            return 70
        return self._chance({"base": 50, "mods": ["forage"]})

    def _open_chance(self) -> int:
        return min(95, OPEN_GIFT + (20 if self.district == 1 else 0))

    def _place_options(self) -> list[dict]:
        out = []
        for key in self.arena.offers[self.day]:
            emoji, label, hint, tag = PLACES_INFO[key]
            extra = {}
            if key == "stream":
                win, lose = ["🎣 🎒+1 or +2"], ["⚔️ others drink here too"]
                extra["minigame"] = self._fish_params()
                hint = "Fish for your supper. Water and food. Everyone else needs it too."
            elif key == "rocks":
                win, lose = [f"❤️+{self._hide_heal()}"], ["🎒−1"]
            elif key == "ridge":
                if any(w in self.kit for w in lore.HUNTERS):
                    hunt = self._hunt_params()
                    shots = len(hunt["shots"])
                    win, lose = ["🏹 🐇🎒+1 🦌🎒+2" if shots > 1 else "🗡️ 🐇🎒+2 🦌🎒+3"], ["🐾 noise if you miss"]
                    extra["minigame"] = hunt
                    hint = (f"Hunt up high: {'three arrows' if shots > 1 else 'one throw'}. The edge of the arena.")
                else:
                    snares = self.district == 3 or "rope" in self.kit
                    win, lose = [f"🎒+{2 if self.district == 3 else 1 if snares else 2} likely"], ["🐾 some danger"]
                    hint = ("Set snares up high." if snares else "Forage up high.") + " The edge of the arena."
            elif key == "horn":
                win, lose = ["⛏️ dig for loot"], ["⚔️ fights likely"]
            elif key == "open":
                win, lose = ["🎁 sponsor gift likely"], ["👁️ you'll be seen"]
            else:  # feast
                win, lose = ["❤️ 🎒 what you need"], ["⚔️ everyone"]
            if key in EDGE:
                watched = self.edge_streak >= 1 or self.day >= EDGE_WATCHED
                lose = lose + (["⚠️ Gamemakers"] if watched else ["🌲 the edge"])
            out.append(self._opt(PLACE_KEYS.index(key), f"{emoji} {label}", hint, None, tag, win, lose, place=key, **extra))
        return out

    def _resolve_place(self, index: int, param=None, game: dict | None = None) -> tuple:
        key = PLACE_KEYS[index]
        self.place = key
        if key == "feast":
            return ("draw", "feast")
        self.days[self.day].append(PLACES_INFO[key][0])
        if key == "stream":
            caught, _ = mg.fish_run(game, mg.clean_toggles(param, game["duration"]))
            kind = game["kind"]
            emoji, food, hurt, _ = mg.FISH_KINDS[kind]
            if caught and kind == "leech":
                self._played("fish", False, "A LEECH", "you pulled it in")
                self._say("It isn't a fish. It's a leech the length of your hand, and it's on your wrist before you can drop the line.", "result")
                self._hurt(-hurt, "a leech from the stream")
            elif caught:
                self._played("fish", True, "CAUGHT", f"{emoji} a {kind}")
                self.supplies += food
                self._say(self._pick("You drink until your stomach hurts, then eat the fish raw on the bank.",
                                     f"A {kind}, fat and slippery. Cold water and a meal."), "result")
            else:
                self._played("fish", kind == "leech", "LET IT GO" if kind == "leech" else "GOT AWAY",
                             "a leech, not a fish" if kind == "leech" else f"{emoji} the {kind}")
                self._say("You drink your fill. That's all you get." if kind != "leech" else
                          "The dark shape on your line was a leech. You let it go and drink your fill.", "result")
        elif key == "rocks":
            self.health = min(100, self.health + self._hide_heal())
            self.supplies = max(0, self.supplies - 1)
            self._say(self._pick("You wedge yourself into a crack in the rocks and sleep most of the day. It costs you a day's food.",
                                 "A dry hollow under an overhang. You don't move until dark, and you eat more than you'd like."), "result")
        elif key == "ridge":
            if any(w in self.kit for w in lore.HUNTERS):
                food, hits = mg.hunt_run(game, mg.clean_taps(param, len(game["shots"])))
                got = [h for h in hits if h]
                if food:
                    names = ", ".join(mg.ANIMALS[h][0] for h in got)
                    self._played("hunt", True, f"{len(got)} HIT{'S' if len(got) > 1 else ''}", names)
                    self.supplies += food
                    self._say("A deer. You eat well for days." if "deer" in got else
                              f"{len(got)} hit{'s' if len(got) > 1 else ''}. You eat well tonight.", "result")
                else:
                    self._played("hunt", False, "MISSED", "everything")
                    self._say("You miss everything. The noise carries.", "result")
                    if not self._roll(70):
                        self._say("Someone heard.", "result")
                        self._hurt(20, "a tribute who heard you hunting")
            else:
                _, ok, _ = self._reveal(self._ridge_chance())
                if ok:
                    snares = self.district == 3 or "rope" in self.kit
                    self.supplies += 2 if self.district == 3 or not snares else 1
                    self._say("Something is caught by morning." if snares else
                              self._pick("Pine nuts, and rainwater pooled in a hollow stump.", "Wild onions and a seep of clean water between the rocks."), "result")
                else:
                    self._say(self._pick("You find nothing but blisters.", "Everything edible up here was eaten days ago."), "result")
        elif key == "horn":
            self._start_sift()
            return ("sift",)
        elif key == "open":
            _, ok, _ = self._reveal(self._open_chance())
            if ok:
                self._gift()
            else:
                self._say("Nothing falls from the sky. And now everyone knows where you are.", "result")
        if self.over:
            return ()
        card = self._place_card(key)
        if card:
            return ("draw", card)
        self._say("The rest of the day passes quietly.", "result")
        return ("night",)

    def _place_card(self, key: str) -> str | None:
        for cid in ("nemesis_sighting", "nemesis_clash"):
            c = cards()[cid]
            if cid not in self.used and key in c["place"] and self._eligible(c):
                return cid
        for cid in self.arena.deck:
            c = cards()[cid]
            if cid not in self.used and key in c.get("place", ()) and self._eligible(c):
                return cid
        return None

    # ------------------------------------------------------------------ minigames
    def _played(self, game: str, ok: bool, label: str, note: str, near: bool = False) -> None:
        """Log a minigame's result where the roll reveal goes. near shows it amber: no harm done."""
        self.log.append({"day": self.day, "kind": "roll", "game": game, "ok": ok, "near": near,
                         "label": label, "note": note, "chance": None, "text": f"{label} · {note}"})

    def _fish_params(self) -> dict:
        r = _rng(self.seed, "fish", self.day)
        kinds = ["minnow"] if self.day <= 2 else ["minnow", "trout"] if self.day == 3 else ["minnow", "trout", "trout", "leech"]
        kind = r.choice(kinds)
        big = self.district == 4 or any(w in self.kit for w in ("spear", "trident"))
        return mg.fish_params(r, kind, 3000 if big else 2200)

    def _hunter(self) -> str:
        return "bow" if "bow" in self.kit else next(w for w in ("spear", "trident") if w in self.kit)

    def _hunt_params(self) -> dict:
        w = self._hunter()
        style = "bow" if w == "bow" else "spear"
        return mg.hunt_params(_rng(self.seed, "hunt", self.day, style), w, self.district == 10)

    def _hide_params(self, card_id: str, base: int) -> dict:
        breath = (60 if self.district == 8 else 0) - (30 if self.health < 45 else 0)
        return mg.hide_params(_rng(self.seed, "hide", card_id, self.day), base, breath, 200 * len(self.allies),
                              card_id == "nemesis_sighting")

    # ------------------------------------------------------------------ the sift
    def _start_sift(self) -> None:
        r = _rng(self.seed, "sift", self.day)
        n = SIFT_SIZE * SIFT_SIZE
        items = r.sample(SIFT_ITEMS, 2)
        tiles = ["food"] * 5 + [f"item:{i}" for i in items] + ["clue", "snare", "snare", "mine"]
        tiles += ["empty"] * (n - len(tiles))
        r.shuffle(tiles)
        careers = r.sample([i for i, t in enumerate(tiles) if t == "empty"], 2)  # only there if you dig loudly
        self.sift = {"tiles": tiles, "careers": careers, "open": [], "digs": 0, "how": None, "clue": False, "found": []}

    def _sift_tile(self, i: int) -> str:
        if self.sift["how"] == "loud" and i in self.sift["careers"]:
            return "career"
        return self.sift["tiles"][i]

    @staticmethod
    def _cross(i: int) -> list[int]:
        x, y = i % SIFT_SIZE, i // SIFT_SIZE
        out = [i]
        for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            if 0 <= x + dx < SIFT_SIZE and 0 <= y + dy < SIFT_SIZE:
                out.append((y + dy) * SIFT_SIZE + x + dx)
        return out

    def _sift_options(self) -> list[dict]:
        sf = self.sift
        if sf["how"] is None:
            out = []
            if self.supplies >= 1:
                out.append(self._opt(0, "Dig quietly", "Three digs. Slow work, and it costs you a day's food.", None, "safe",
                                     [f"⛏️×{SIFT_DIGS['quiet']}"], ["🎒−1"]))
            out.append(self._opt(1, "Dig loudly", "Six digs, and fast. Careers will hear it.", None, "bold",
                                 [f"⛏️×{SIFT_DIGS['loud']}"], ["👁️ Careers in the rubble"]))
            out.append(self._opt(2, "Leave the rubble", "Look around the horn instead.", None, "safe", [], []))
            return out
        out = []
        for i in range(SIFT_SIZE * SIFT_SIZE):
            if i in sf["open"]:
                continue
            out.append(self._opt(SIFT_SMALL + i, "Dig here", "", None, "safe", [], [], dig=i, big=False))
            out.append(self._opt(SIFT_BIG + i, "Dig wide here", "", None, "bold", [], [], dig=i, big=True))
        out.append(self._opt(SIFT_STOP, "Stop digging", "Keep what you've found and get clear.", None, "safe", [], []))
        return out

    def sift_view(self) -> dict | None:
        sf = self.sift
        if not sf or self.card != SIFT:
            return None
        tiles = []
        for i in range(SIFT_SIZE * SIFT_SIZE):
            if i in sf["open"]:
                what = self._sift_tile(i)
                near = [j for j in self._cross(i)[1:] if j not in sf["open"]]
                kinds = [self._sift_tile(j) for j in near]
                tiles.append({
                    "open": True,
                    "emoji": lore.item_emoji(what[5:]) if what.startswith("item:") else SIFT_TILES[what][0],
                    "glint": any(k.startswith("item:") for k in kinds),
                    "scorch": any(k in SIFT_HURT for k in kinds),
                })
            else:
                tiles.append({"open": False, "marked": sf["clue"] and self._sift_tile(i).startswith("item:")})
        return {"size": SIFT_SIZE, "digs": sf["digs"], "how": sf["how"], "tiles": tiles}

    def _resolve_sift(self, index: int) -> tuple:
        sf = self.sift
        if sf["how"] is None:
            if index == 2:
                return self._end_sift(dug=False)
            sf["how"] = ("quiet", "loud")[index]
            sf["digs"] = SIFT_DIGS[sf["how"]]
            if sf["how"] == "quiet":
                self.supplies -= 1
                self._say("You work slowly, lifting one plank at a time. It takes all day.", "result")
            else:
                self._say("You tear into the rubble with both hands. Anyone nearby can hear it.", "result")
            self._say("Tap a tile to dig. Dig wide to turn five at once. ✨ means something good is next door; "
                      "🔥 means something isn't.", "prompt")
            return ("round", None)
        if index == SIFT_STOP:
            return self._end_sift()
        big = index >= SIFT_BIG
        tile = index - (SIFT_BIG if big else SIFT_SMALL)
        sf["digs"] -= 1
        found, ended = [], False
        for i in (self._cross(tile) if big else [tile]):
            if i in sf["open"]:
                continue
            sf["open"].append(i)
            what = self._sift_tile(i)
            if what == "food":
                self.supplies += 1
                found.append("🥫")
            elif what.startswith("item:"):
                item = what[5:]
                self._gain(item)
                found.append(lore.item_emoji(item))
            elif what == "clue":
                sf["clue"] = True
                found.append("🔮")
                nxt = self.arena.offers.get(self.day + 1)
                tomorrow = f" It shows where you can get to tomorrow: {', '.join('the ' + k for k in nxt)}." if nxt else ""
                self._say("A Capitol map, half burned. Someone marked where the good finds are." + tomorrow, "gift")
            elif what in SIFT_HURT:
                dmg, cause = SIFT_HURT[what]
                found.append(SIFT_TILES[what][0])
                self._say({"snare": "A wire snaps tight around your wrist.",
                           "mine": "The ground under the plank clicks.",
                           "career": "A hand grabs your collar from behind the crate."}[what], "result")
                self._hurt(dmg, cause)
                if self.over:
                    return ()
                ended = ended or what == "career"
        hurt = any(f in ("🪤", "💥", "👁️") for f in found)
        # Green for a find, red for a trap, amber for plain dirt.
        self._played("sift", bool(found) and not hurt, " ".join(found) if found else "EMPTY",
                     ("dig wide" if big else "dig") + ("" if found else " · just dirt"), near=not found)
        if ended:
            self._say("You tear free and run. The digging is over.", "result")
            return self._end_sift()
        if sf["digs"] <= 0:
            return self._end_sift()
        return ("round", None)

    def _end_sift(self, dug: bool = True) -> tuple:
        self.sift = None
        self.card = ""
        if dug:
            self._say("You leave the rubble behind.", "result")
        card = self._place_card("horn")
        if card:
            return ("draw", card)
        self._say("The rest of the day passes quietly.", "result")
        return ("night",)

    # ------------------------------------------------------------------ the end
    def _start_duel(self) -> None:
        # Everyone but you and one rival goes. Your ally first (only one can win), then the nemesis.
        if self.allies:
            self.opponent_id = self.opponent = self.allies[0]
        elif self.nemesis in self.pool:
            self.opponent_id = self.nemesis
            self.opponent = f"{self.nemesis_name} ({self.nemesis[4:]})"
        elif self.pool:
            self.opponent_id = self.pool[-1]
            self.opponent = self._name(self.pool[-1])
        else:
            self.opponent_id = self.opponent = "the last Career"
        self.opp_max = self.opp_health = OPPONENT_HEALTH["nemesis" if self.opponent_id == self.nemesis else "other"]
        rest = [t for t in self.pool if t != self.opponent_id]
        rest_allies = [a for a in self.allies if a != self.opponent_id]
        self.pool = [t for t in self.pool if t == self.opponent_id]
        self.allies = [a for a in self.allies if a == self.opponent_id]
        gone = rest + rest_allies
        if gone:
            self.fallen_today.extend(gone)
            line = f"{len(gone)} cannon{'s' if len(gone) != 1 else ''} in a row."
            if self.nemesis in gone:
                line += f" One of them is {self.nemesis_name}."
            self._say(line, "cannon")
        self.card = DUEL
        self.exchanges = 0
        self.days[self.day].append("⚔️")
        self._say(f"Two tributes left. You, and {self.opponent}, face to face on the horn. You have {self.health} "
                  f"health. They have {self.opp_health}. Watch which side they wind up on, and block it.", "event")

    def _weapon(self) -> str | None:
        weapons = [w for w in self.kit if w in lore.WEAPONS]
        return max(weapons, key=lambda k: lore.WEAPONS[k]) if weapons else None

    def _fight_deal(self, stance: str) -> int:
        w = self._weapon()
        bonus = (lore.WEAPONS[w] if stance == "aggressive" else lore.WEAPONS[w] // 2) if w else 0
        tired = 2 if self.exchanges >= FIGHT_TIRED else 1
        return (FIGHT[stance][0] + bonus + min(FIGHT_KILLS_CAP, FIGHT_PER_KILL * self.kills)) * tired

    def _fight_take(self, stance: str) -> int:
        tired = 2 if self.exchanges >= FIGHT_TIRED else 1
        return (FIGHT[stance][1] + (FIGHT_NEMESIS_HIT if self.opponent_id == self.nemesis else 0)) * tired

    def _fight_params(self, stance: str) -> dict:
        w = self._weapon()
        return mg.fight_params(_rng(self.salt, "fight", self.exchanges), stance, self.opponent_id == self.nemesis,
                               "rope" in self.kit, lore.WEAPONS[w] if w else 0, self.exchanges)

    def _duel_options(self) -> list[dict]:
        out = []
        w = self._weapon()
        for i, (stance, label, tag) in enumerate((("aggressive", "Press the attack", "bold"), ("careful", "Hold your ground", "safe"))):
            bits = ["Short block, heavy strike." if stance == "aggressive" else "Wide block, light strike."]
            if w:
                bits.append(f"Your {lore.item_name(w)}: a longer strike.")
            if "rope" in self.kit:
                bits.append("Rope: a wider block.")
            deal, take = self._fight_deal(stance), self._fight_take(stance)
            win = ["👑"] if deal >= self.opp_health else [f"🩸 them −{deal}"]
            lose = [f"❤️−{take}"] + (["☠️"] if take >= self.health else [])
            out.append(self._opt(i, label, " ".join(bits), None, tag, win, lose, minigame=self._fight_params(stance)))
        return out

    def _resolve_duel(self, index: int, param=None, game: dict | None = None) -> str | None:
        stance = ("aggressive", "careful")[index]
        blocked, struck = mg.fight_run(game, mg.clean_fight(param))
        deal, take = self._fight_deal(stance), self._fight_take(stance)
        self.exchanges += 1
        if struck:
            self._played("fight", True, "BLOCKED · HIT", f"them −{deal}")
            self.opp_health = max(0, self.opp_health - deal)
            self._say(self._pick("You turn their blade and drive yours in.", "You catch the blow and answer it. They stagger.",
                                 "Steel on steel, then your strike lands."), "result")
        elif blocked:
            self._played("fight", True, "BLOCKED", "no opening")
            self._say(self._pick("You turn the blow, but they're back on guard before you can answer.",
                                 "You catch it. Neither of you gives an inch."), "result")
        else:
            self._played("fight", False, "HIT", f"you −{take}")
            self._say(self._pick("They're faster than you.", "You guess wrong, and it costs you.", "You slip on the wet gold."), "result")
            self._hurt(take, f"{self.opponent}, in the final fight")
            if self.over:
                return None
        if self.opp_health <= 0:
            self._say(self._pick("It's over. The trumpets sound.", "They fall. You don't."), "result")
            if self.opponent_id in self.allies:
                self.allies.remove(self.opponent_id)
            self.pool = []
            self.kills += 1
            self.new_kills.append(self.opponent)
            self._win()
            return None
        tired = " You're both exhausted now: every blow counts double." if self.exchanges == FIGHT_TIRED else ""
        return f"{self.opponent}: {self.opp_health} health. You: {self.health}.{tired}"

    # ------------------------------------------------------------------ effects
    def _apply(self, e: dict, card: dict, near: bool = False) -> str | None:
        cause = self._fmt(e.get("cause", card.get("cause", "the arena")))
        if "supplies" in e:
            self.supplies = max(0, self.supplies + e["supplies"])
        for item in self._list(e.get("remove")):
            if item in self.kit:
                self.kit.remove(item)
        for item in self._list(e.get("add")):
            self._gain(item)
        if e.get("gift"):
            pool = [i for i in ("knife", "spear", "rope", "medkit", "iodine", "axe") if i not in self.kit]
            if pool:
                item = self._dice().choice(pool)
                article = "some" if item == "iodine" else "an" if item[0] in "aeiou" else "a"
                self._say(f"You found {article} {lore.item_name(item)}.", "gift")
                self._gain(item)
        if e.get("kill"):
            self._kill(e["kill"])
        if e.get("partner_dies") and "partner" in self.pool:
            self.pool.remove("partner")
            self.fallen_today.append("partner")
        ally = e.get("ally")
        if ally and ally != "joins_partner" and self.allies:
            name = self.allies.pop(0)
            if ally == "dies":
                self.fallen_today.append(name)
            elif ally == "lost":
                self.pool.append(name)
        if ally == "joins_partner" and "partner" in self.pool:
            self.pool.remove("partner")
            self.allies.append(self.partner)
        if "health" in e:
            if e["health"] < 0:
                dmg = self._card_damage(e["health"])
                self._hurt(dmg // 2 if near else dmg, cause)
            else:
                self.health = min(100, self.health + e["health"])
        if e.get("sponsor") and not self.over:
            self._gift()
        if e.get("die"):
            self._die(self._fmt(e["die"]))
        if e.get("win") or (not self.over and self.remaining == 1):
            self._win()
        return None if self.over else e.get("next")

    def _kill(self, n: int) -> None:
        for _ in range(n):
            if self.target in self.pool:
                v = self.target
                self.target = None
            else:
                victims = [t for t in self.pool if t not in ("partner", self.nemesis)] or self.pool
                if not victims:
                    return
                v = victims[0]
            self.pool.remove(v)
            self.fallen_today.append(v)
            self.killed.add(v)
            self.new_kills.append(self._name(v))
            self.kills += 1
            self.kills_today += 1
            for d in range(self.day, MAX_DAYS + 1):
                if self.schedule[d] > 0:
                    self.schedule[d] -= 1
                    break
        if "⚔️" not in self.days[self.day]:
            self.days[self.day].append("⚔️")

    def _gift(self) -> None:
        if self.health < 50:
            self.health = min(100, self.health + 30)
            what = "medicine"
        elif self.supplies <= 1:
            self.supplies += 3
            what = "bread and a canteen of water"
        elif not any(w in self.kit for w in lore.WEAPONS) and len(self.kit) < lore.KIT_SLOTS:
            self.kit.append("knife")
            what = "a knife"
        elif "medkit" not in self.kit and len(self.kit) < lore.KIT_SLOTS:
            self.kit.append("medkit")
            what = "a medkit"
        else:
            self.supplies += 2
            what = "a hot meal"
        self.days[self.day].append("🎁")
        self._say(f"A silver parachute drifts down: {what}.", "gift")

    def _hurt(self, n: int, cause: str) -> None:
        self.health -= n
        if self.health <= 0:
            self._die(cause)

    def _die(self, cause: str) -> None:
        if self.over:
            return
        label, chance, health = self.last
        if cause == "starvation":
            self.recap = "You ran out of supplies."
        elif label and chance is not None:
            self.recap = f"You took the {chance}% “{label}” with {health} health."
        elif label:
            self.recap = f"You chose “{label}” with {health} health."
        self.health = 0
        self.over = True
        self.cause = cause
        self.placement = self.remaining
        self.days[self.day].append("☠️")
        self._say(f"You have died. Cause of death: {cause}.", "death")
        self.card = ""

    def _win(self) -> None:
        self.over = True
        self.won = True
        self.placement = 1
        self.days[self.day].append("👑")
        self._say("Ladies and gentlemen, the victor of the Hunger Games!", "victory")
        self.card = ""

    # ------------------------------------------------------------------ odds
    def _chance(self, roll: dict) -> int:
        c = roll["base"]
        h = self.health
        penalty = 0 if h >= 70 else -5 if h >= 45 else -12 if h >= 20 else -20
        for m in roll.get("mods", []):
            if m == "combat":
                c += max([lore.WEAPONS.get(i, 0) for i in self.kit] + [0])
                c += 10 if self.district == 2 else 0
                c += 5 * len(self.allies)
                c += penalty
            elif m == "escape":
                c += 10 if "rope" in self.kit else 0
                c += 10 if self.district == 5 else 0
                c += penalty
            elif m == "stealth":
                c -= 5 * len(self.allies)
                c += 15 if self.district == 8 else 0
                c += penalty
            elif m == "forage":
                c += 30 if self.district == 11 else 0
                c += 20 if "iodine" in self.kit else 0
            elif m == "swim":
                c += 30 if self.district == 4 else 0
            elif m == "grab":
                c += 10 if self.district == 6 else 0
        return max(5, min(95, c))

    def _dice(self) -> random.Random:
        return _rng(self.salt, self.steps, len(self.log))

    def _roll(self, chance: int) -> bool:
        return self._dice().randint(1, 100) <= chance

    def _reveal(self, chance: int) -> tuple[int, bool, bool]:
        """Roll for an option the player chose, and log the roll so the screen can show it."""
        roll = self._dice().randint(1, 100)
        ok = roll <= chance
        near = not ok and roll <= chance + NEAR
        self.log.append({"day": self.day, "kind": "roll", "text": f"{chance}% · rolled {roll}",
                         "chance": chance, "roll": roll, "ok": ok, "near": near})
        return roll, ok, near

    def _has(self, need: dict | None) -> bool:
        if not need:
            return True
        if "item" in need and need["item"] not in self.kit:
            return False
        if "any" in need and not any(i in self.kit for i in need["any"]):
            return False
        if "perk" in need and self.district != need["perk"]:
            return False
        if need.get("ally") and not self.allies:
            return False
        if "supplies" in need and self.supplies < need["supplies"]:
            return False
        return True

    # ------------------------------------------------------------------ text
    def _snap(self) -> tuple:
        self.new_kills = []
        return (self.health, self.supplies, list(self.kit), list(self.allies))

    def _delta(self, snap: tuple, night: bool = False) -> None:
        if self.over and not self.won:
            return
        health, supplies, kit, allies = snap
        parts = []
        if self.health != health:
            parts.append(f"❤️ {_sign(self.health - health)}")
        if self.supplies != supplies:
            parts.append(f"🎒 {_sign(self.supplies - supplies)}")
        for k in self.kit:
            if k not in kit:
                parts.append(f"+{lore.item_emoji(k)} {lore.item_name(k)}")
        for k in kit:
            if k not in self.kit:
                parts.append(f"−{lore.item_emoji(k)} {lore.item_name(k)}")
        for name in self.new_kills:
            parts.append(f"⚔️ {name}")
        for a in self.allies:
            if a not in allies:
                parts.append(f"+{a}")
        for a in allies:
            if a not in self.allies:
                parts.append(f"−{a}")
        self.new_kills = []
        if parts:
            self._say("  ".join(parts), "delta")

    def _name(self, t: str) -> str:
        if t == "partner":
            return f"{self.partner}, your district partner"
        if t == self.nemesis:
            return f"{self.nemesis_name} ({t[4:]})"
        return t

    def _pick(self, *lines: str) -> str:
        return self._dice().choice(lines)

    def _fmt(self, s: str) -> str:
        if "{" not in s:
            return s
        t = self._name(self.target) if self.target else "a tribute"
        nem = self.nemesis_name
        return s.format(
            ally=self.allies[0] if self.allies else "your ally",
            partner=self.partner,
            target=t,
            Target=t[0].upper() + t[1:],
            nemesis=nem,
            Nemesis=nem[0].upper() + nem[1:],
            remaining=self.remaining,
            host=lore.HOST,
            opponent=self.opponent or "the last tribute",
            district=self.district,
        )

    def _say(self, text: str, kind: str) -> None:
        self.log.append({"day": self.day, "kind": kind, "text": text})

    # ------------------------------------------------------------------ views
    def status(self) -> dict:
        return {
            "day": self.day,
            "health": self.health,
            "health_word": health_word(self.health),
            "supplies": self.supplies,
            "kit": [{"id": k, "name": lore.ITEMS[k][0], "emoji": lore.ITEMS[k][1], "desc": lore.ITEMS[k][2]} for k in self.kit],
            "slots": lore.KIT_SLOTS,
            "kills": self.kills,
            "remaining": self.remaining,
            "allies": list(self.allies),
            "can_use": "medkit" if self.can_use_medkit() else None,
            "fight": {"them": self.opp_health, "max": self.opp_max, "name": self.opponent,
                      "boss": self.opponent_id == self.nemesis} if self.card == DUEL else None,
        }

    def emoji_row(self) -> str:
        priority = ["👑", "☠️", "⚔️", "🎁"]
        row = []
        for d in range(1, min(self.day, MAX_DAYS) + 1):
            marks = self.days[d]
            if not marks:
                continue
            if d == 1 and not any(p in marks for p in priority[:2]):
                row.append(marks[0])  # the bloodbath shows what you grabbed (or 🌲)
                continue
            row.append(next((p for p in priority if p in marks), marks[0]))  # else where you went
        return "".join(row)

    def score(self) -> int:
        if not self.over:
            return 0
        place = self.placement or START_TRIBUTES
        pts = (START_TRIBUTES + 1 - place) * 40 + self.kills * 60 + self.day * 25
        if self.won:
            pts += 500
        return round(pts * lore.multiplier(self.district))


def new_setup(district: int, partner: str, allies: list[str]) -> dict:
    if district not in lore.DISTRICTS:
        raise ValueError("bad district")
    return {"district": district, "partner": partner, "allies": allies[:2]}


def replay(seed: int, salt: int, setup: dict, choices: list[list[int | None]]) -> Game:
    g = Game(seed, salt, setup)
    for idx, param in choices:
        g.choose(idx, param)
    return g
