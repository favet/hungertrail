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
DAMAGE = 2.0  # global lethality dial for card damage, tuned with the bots
HIT_CAP = 65  # no single card hit is bigger than this, so one miss never kills a tribute at 66+ health
SUPPLY_CAP = 6  # days of food you can carry: past this, food is left behind
# The nemesis arc: every wound you give them comes off their health in the final fight, down to a floor.
NEMESIS_FLOOR = 40
NEAR = 10  # a failed roll this close to the target is a near miss
# The final fight. stance: (damage your strike deals, damage you take if you don't block)
FIGHT = {"aggressive": (26, 30), "careful": (14, 18)}
FIGHT_NEMESIS_HIT = 8  # the nemesis hits harder
FIGHT_PER_KILL, FIGHT_KILLS_CAP = 4, 12  # your kills make your strikes count
FIGHT_TIRED = 10  # from this exchange on, every blow counts double: nobody fights forever
OPPONENT_HEALTH = {"nemesis": 125, "other": 70}  # the nemesis is the last fight: wounds and study bring them down
OPEN_GIFT = 55  # odds a sponsor pays when you go out in the open
HIDE_HEAL = 25
EDGE_WATCHED = 5  # from this day, any day at the edge brings the Gamemakers
EDGE_CLOSES = 6  # from this day the edge places are gone
USE = 99  # choice index for tapping the medkit in your kit
BUY = 500  # choice index BUY + k: order SPONSOR_MENU[k] from the sponsors

# The crowd: stars from 0 to 5. Long shots that land and kills earn them, walking away costs one,
# and the sponsors take them as payment. Stars you never spend add 10% each to your score.
STARS_MAX = 5
LONG_SHOT = 40  # a wheel at this or less that lands earns a star
SPONSOR_MENU = [  # key, what it is, icon, price in stars (each order raises every price by one)
    ("food", "Food for 2 days", "🍞", 2),
    ("medicine", "Medicine: +30 health", "💊", 3),
    ("knife", "A knife", "🔪", 3),
    ("rope", "Rope", "🪢", 3),
    ("bow", "A bow", "🏹", 5),
]
SPONSORS = ("the Capitol's fashion set", "an old Victor from your district", "a jeweller in District 1",
            "a fan club in the Capitol", "someone who won money on you")
DRAFT, PLACES, SWAP, DUEL, SIFT = "draft", "places", "swap", "duel", "sift"

# The sift, after Slay the Spire's Crystal Sphere: a 9x9 grid of rubble at the horn hides treasures
# that each cover several tiles, and a few traps. A treasure is yours only once every tile of it is
# uncovered. A small dig opens one tile, a big dig a 3x3. Dig quietly (4 digs, costs a day of supplies)
# or loudly (6 digs, free, but Careers hear you and lie in wait in the rubble).
SIFT_SIZE = 9
SIFT_DIGS = {"quiet": 4, "loud": 6}
SIFT_SMALL, SIFT_BIG, SIFT_STOP = 100, 200, 300  # choice index = SIFT_SMALL + tile, SIFT_BIG + tile
# What's buried: (kind, width, height). Items are picked from the pools by the daily seed.
SIFT_TREASURES = [("crate", 3, 3), ("weapon", 3, 1), ("pack", 2, 2), ("pack", 2, 2), ("tool", 2, 1),
                  ("can", 1, 1), ("can", 1, 1), ("can", 1, 1)]
SIFT_POOLS = {"crate": ("bow", "trident", "medkit"), "weapon": ("spear", "axe", "knife"),
              "tool": ("rope", "iodine", "sleeping_bag")}
SIFT_TRAPS = [("mine", 1, 1), ("snare", 2, 1)]
SIFT_CAREERS = [("career", 2, 1), ("career", 1, 2)]  # only when you dig loudly
SIFT_HURT = {"snare": (12, "a snare in the rubble"), "mine": (30, "a mine left from the bloodbath"),
             "career": (25, "a Career who heard you digging")}
SIFT_TRAP_NAMES = {"mine": ("💥", "a mine"), "snare": ("🪤", "a snare"), "career": ("👁️", "a Career")}

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
PLACE_NAMES = {"stream": "the stream", "rocks": "the rocks", "ridge": "the ridge", "horn": "the horn", "open": "the open ground"}

# The end of the Games, one per day from the seed. refuge: the one edge place that stays open
# after the edge closes, because of what's coming. hint: what Caesar says the night before.
FINALES = {
    "flood": {"refuge": "ridge", "why": "the water won't reach that high",
              "hint": "Bring a swimsuit. That's all I'm allowed to say."},
    "fire_finale": {"refuge": "rocks", "why": "rock doesn't burn",
                    "hint": "Something warm is planned for the end. Very warm."},
    "mutt_finale": {"refuge": "ridge", "why": "nothing with four legs climbs that",
                    "hint": "Listen for howling, ladies and gentlemen."},
    "fog_finale": {"refuge": "rocks", "why": "the fog rolls over the rocks and on",
                   "hint": "Take a deep breath tonight. You'll want it later."},
}
# A twist of the day, the same for everyone, announced when the bloodbath ends.
TWISTS = {
    "generous": "The sponsors are generous today: every parachute costs one star less.",
    "cold": "A cold front is coming through: every hungry night hurts 5 more.",
    "fat_fish": "The stream is full of fish today: every catch is worth one more day of food.",
    "loud_crowd": "The Capitol is restless today: every tribute starts with two more stars.",
}
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


# Stakes are words, not emoji: what you gain or lose, said plainly.
DEADLY = "could kill you"


def hp(n: int) -> str:
    return f"{_sign(n)} health"


def food(n: int) -> str:
    return f"{_sign(n)} food"


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

        # Today's script: when and where the nemesis turns up, how the Games end, and the twist.
        plan = _rng(seed, "plan")
        self.sight_day = plan.choice([2, 3, 4, 5])
        self.sight_places = sorted(plan.sample(["stream", "ridge", "open", "horn"], 2), key=PLACE_KEYS.index)
        self.clash_day = min(7, self.sight_day + plan.choice([2, 3]))
        self.clash_places = sorted(plan.sample(["stream", "open", "horn"], 2), key=PLACE_KEYS.index)
        self.finale = plan.choice(sorted(FINALES))
        self.refuge = FINALES[self.finale]["refuge"]
        self.twist = plan.choice(sorted(TWISTS))

        # Where you can go each day: 3 of the 5 places until the edge closes. After that, 3 of the
        # stream, the horn, the open and the finale's refuge, never the same three two days running.
        self.offers: dict[int, list[str]] = {}
        late = ["stream", "horn", "open", self.refuge]
        for day in range(2, MAX_DAYS + 1):
            if day >= EDGE_CLOSES:
                offer = _rng(seed, "places", day).sample(late, 3)
                if sorted(offer) == sorted(self.offers.get(day - 1, [])):
                    offer = [p for p in late if p not in offer] + offer[:2]
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
        self.supplies = 1 if self.district in (11, 12) else 2 + (2 if self.district == 9 else 0)
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
        self.hungry = 0  # hungry nights in a row: each one hurts more
        self.stars = (2 if self.district == 1 else 1) + (2 if self.arena.twist == "loud_crowd" else 0)
        self.bought = 0
        self.order: str | None = None  # what the sponsors drop tonight
        self.cheer: str | None = None  # a star earned this choice, said after its result
        self.hits: list[tuple[int, int, str]] = []  # (day, damage, cause), for the death recap
        self.kills_today = 0
        self.killed: set[str] = set()
        self.wounds = 0  # what you've taken off the nemesis before the final fight
        self.study = False  # you've watched the nemesis fight: you see their feints coming
        self.saved = False  # your ally has taken a killing blow for you (once a run)
        self.clean = 0  # minigames played cleanly: a catch, a hit, holding still, a find, a block

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
    def supplies(self) -> int:
        return self._supplies

    @supplies.setter
    def supplies(self, n: int) -> None:
        self._supplies = max(0, min(SUPPLY_CAP, n))

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
                if not win and (o["fail"].get("effects") or {}).get("health", 0) < 0:
                    win = ["you get away unhurt"]  # getting out clean is the prize
                if not win and (o["fail"].get("effects") or {}).get("ally") == "dies" and self.allies:
                    win = [f"{self.allies[0]} pulls through"]
                if o.get("minigame") == "hide":
                    out.append(self._opt(i, o["label"], o.get("hint", "") or "Hold to stay down. Let go to breathe.", None,
                                         o.get("tag", "safe"), win, lose, minigame=self._hide_params(card["id"], o["roll"]["base"])))
                    continue
                odds = {**self._odds(o["roll"]), "near": self._card_near(o)}
                if odds["chance"] <= LONG_SHOT and self.stars < STARS_MAX:
                    win = win + ["+1 star"]
                out.append(self._opt(i, o["label"], o.get("hint", ""), odds["chance"], o.get("tag", "safe"), win, lose, wheel=odds))
            else:
                e = o.get("effects", {})
                walk = o.get("tag") == "hide" and self.stars > 0
                out.append(self._opt(i, o["label"], o.get("hint", ""), None, o.get("tag", "safe"), self._chips(e, card),
                                     ["−1 star"] if walk else []))
        return out

    def locked(self) -> list[dict]:
        """Options on this card that an item you don't have would unlock: shown greyed, so kit means something."""
        if self.over or self.card in (DRAFT, PLACES, SWAP, DUEL, SIFT) or self.card not in cards():
            return []
        out = []
        for o in cards()[self.card]["options"]:
            need = o.get("need") or {}
            items = [need["item"]] if "item" in need else list(need.get("any", []))
            if not items or self._has(o.get("need")):
                continue
            rest = {k: v for k, v in need.items() if k not in ("item", "any")}
            if not self._has(rest):
                continue
            names = [f"{'some' if i == 'iodine' else 'an' if i[0] in 'aeiou' else 'a'} {lore.item_name(i)}" for i in items]
            out.append({"label": self._fmt(o["label"]), "needs": " or ".join(names)})
        return out

    def can_use_medkit(self) -> bool:
        return not self.over and "medkit" in self.kit and self.health < 100

    def choose(self, index: int, param=None) -> None:
        if self.over:
            raise ValueError("run is over")
        if index == USE:
            return self._use_medkit()
        if BUY <= index < BUY + len(SPONSOR_MENU):
            return self._buy(index - BUY)
        valid = {o["index"]: o for o in self.options()}
        if index not in valid:
            raise ValueError("invalid option")
        self.steps += 1
        o = valid[index]
        self.last = (o["label"], o["chance"], self.health)
        snap = self._snap()
        if o["label"]:
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
        if self.cheer and not self.over:
            self._crowd(1, self.cheer)
        self.cheer = None
        self._delta(snap)
        if dest and dest[0] == "round":
            if dest[1]:
                self._say(dest[1], "round")  # the next round, after this round's result
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
        if self.card == SWAP and self.pending_item:
            # The medkit's slot is free now: the new thing goes in it.
            new = self.pending_item
            self.pending_item = None
            self._gain(new)
            self._say(f"That makes room for the {lore.item_name(new)}.", "result")
            while self.item_queue and self.pending_item is None:
                self._gain(self.item_queue.pop(0))
            self._delta(snap)
            self._advance(self.resume)
            return
        self._delta(snap)

    # ------------------------------------------------------------------ option helpers
    def _opt(self, index, label, hint, chance, tag, win, lose, **extra) -> dict:
        deadly = DEADLY in lose
        return {
            "index": index, "label": self._fmt(label), "hint": self._fmt(hint), "chance": chance,
            "tag": tag, "win": win, "lose": lose, "deadly": deadly, **extra,
        }

    def _chips(self, e: dict, card: dict | None = None) -> list[str]:
        out = []
        if "health" in e:
            if e["health"] > 0:  # a heal shows what you'd really gain
                n = min(e["health"], 100 - self.health)
                out.append(hp(n) if n else "you're already healthy")
            else:
                n = -self._card_damage(e["health"])
                if n > -self.health:  # a hit that would kill you says so instead, below
                    out.append(hp(n))
        if e.get("supplies"):
            n = e["supplies"] if e["supplies"] < 0 else min(e["supplies"], SUPPLY_CAP - self.supplies)
            out.append(food(n) if n else "your pack is full of food")
        for item in self._list(e.get("add")):
            out.append(f"a {lore.item_name(item)}" if item != "iodine" else "iodine")
        for item in self._list(e.get("remove")):
            if item in self.kit:
                out.append(f"lose your {lore.item_name(item)}")
        if e.get("sponsor"):
            out.append("a sponsor gift")
        if e.get("gift"):
            out.append("a find")
        if e.get("kill"):
            out.append("a kill")
        if e.get("wound") and self.nemesis in self.pool:
            out.append(f"wound {self.nemesis_name}: −{e['wound']} in the final fight")
        if e.get("study") and not self.study:
            out.append(f"learn {self.nemesis_name}'s feint")
        if e.get("ally") in ("dies", "lost") and self.allies:
            out.append(f"lose {self.allies[0]}")
        if e.get("ally") == "joins_partner":
            out.append(f"{self.partner} joins you")
        if e.get("die") or ("health" in e and e["health"] < 0 and self._card_damage(e["health"]) >= self.health):
            out.append(DEADLY)
        if not out and e.get("next") == DUEL:
            out.append("you reach the final fight unhurt")
        # "Nothing to gain here" only matters when it's the whole story.
        full = ("you're already healthy", "your pack is full of food")
        return [c for c in out if c not in full] or out

    def _near_ok(self, dmg: int) -> bool:
        """A close call halves the damage. It only counts when half the damage leaves you standing."""
        return 0 < dmg and dmg // 2 < self.health

    def _card_near(self, opt: dict) -> bool:
        fe = opt["fail"].get("effects", {})
        return fe.get("health", 0) < 0 and not fe.get("die") and self._near_ok(self._card_damage(fe["health"]))

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
            odds = {**self._odds({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]}),
                    "near": self._near_ok(dmg)}
            chance = odds["chance"]
            desc = f"{lore.PACKS[item][2]} days of supplies." if item in lore.PACKS else lore.ITEMS[item][2]
            lose = [hp(-dmg)] + ([DEADLY] if dmg >= self.health else []) + ["you run"]
            out.append(self._opt(i, f"Grab the {lore.item_name(item)}", f"{where}. {desc}", chance,
                                 "bold" if ring != "edge" else "safe",
                                 [f"{lore.item_emoji(item)} {lore.item_name(item)}"], lose, wheel=odds,
                                 ring=ring, where=where, desc=desc, dmg=dmg,
                                 near_text="you get it, but take half the damage"))
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
        odds = self._odds({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]})
        self.taken.add(index)
        self.grabs += 1
        name = lore.item_name(item)
        roll, ok, near = self._reveal(odds, f"Grab the {name}", f"you get the {name}", near_counts=self._near_ok(dmg))
        if ok:
            self._say(self._pick(f"You snatch the {name} and keep moving.", f"The {name} is yours. Nobody touched you."), "result")
            self._take(item)
            return ("draft",) if self.grabs < 2 else ("bloodbath",)
        if near:
            # A close call still gets you the item, and you can still go back for more.
            self._say(f"Close call. You get the {name}, and a blade across the arm for it.", "result")
            self._take(item)
            self._hurt(dmg // 2, "the bloodbath")
            if self.over:
                return ()
            return ("draft",) if self.grabs < 2 else ("bloodbath",)
        else:
            self._say(self._pick(
                f"Someone gets to the {name} first. You get a fist, then a knife. No more grabs: you run.",
                f"You never reach the {name}. You crawl away bleeding. The horn is done with you.",
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
        self._say(f"Today's twist: {TWISTS[self.arena.twist]}", "twist")
        if self.ally_names:
            names = " and ".join(self.ally_names)
            self.allies = list(self.ally_names)
            self.reserved_allies = 0
            self._say(f"At dusk, {names} find{'s' if len(self.ally_names) == 1 else ''} you. The alliance holds. For now.", "story")
        self._night()

    # ------------------------------------------------------------------ kit
    def _gain(self, item: str) -> None:
        if item in self.kit and item != "medkit":
            self._say(f"You already carry a {lore.item_name(item)}. You leave this one.", "result")
            return
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
                      [f"the {lore.item_name(new)}"], [])
            for i, k in enumerate(self.kit)
        ]
        out.append(self._opt(lore.KIT_SLOTS, f"Leave the {lore.item_name(new)}", lore.ITEMS[new][2], None, "safe", [], []))
        if self.can_use_medkit():
            heal = min(40, 100 - self.health)
            out.append(self._opt(USE, f"Use the medkit, then take the {lore.item_name(new)}", "Heal now and make room.",
                                 None, "safe", [hp(heal), f"the {lore.item_name(new)}"], []))
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
            _, ok, near = self._reveal(self._odds(opt["roll"]), self._fmt(opt["label"]), near_counts=self._card_near(opt))
            branch = opt["success" if ok else "fail"]
            text, effects = branch.get("text", ""), branch.get("effects", {})
        else:
            text, effects = opt.get("text", ""), opt.get("effects", {})
            if opt.get("tag") == "hide":
                self._crowd(-1, "The crowd groans. They wanted to see something.")
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
            return self._draw(self.arena.finale)
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
        self._say("Night falls.", "night")  # the screen starts a new scene here
        snap = self._snap()
        if self.order:
            self._drop()
        fed = self.supplies > 0
        if fed:
            self.supplies -= 1
            self.hungry = 0
        else:
            cost = self.hunger_cost()
            self.hungry += 1
            self._say(["You go to sleep with nothing to eat or drink.",
                       "A second night with nothing to eat. Your hands shake.",
                       "Another night without food. You can feel your body eating itself."][min(2, self.hungry - 1)], "status")
            self._hurt(cost, "starvation")
            if self.over:
                return
        if fed:
            self.health = min(100, self.health + 5)
        self._delta(snap, night=True)

        # The Gamemakers watch the edge of the arena.
        at_edge = self.place in EDGE and not self._refuge_open(self.place)
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
        nxt = self.arena.offers.get(self.day + 1)
        if nxt and self.day + 1 < MAX_DAYS:
            names = [PLACE_NAMES.get(p, "the feast") for p in nxt]
            self._say(f"Tomorrow you can reach {', '.join(names[:-1])} or {names[-1]}.", "plan")
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
            gone = "ridge" if self.arena.refuge == "rocks" else "rocks"
            out.append(f"{lore.HOST}: \"Tomorrow the arena closes in. The {gone} are gone. The {self.arena.refuge} "
                       f"stays open, and I'm told there's a reason.\"".replace("The ridge are", "The ridge is"))
        if self.day + 1 == MAX_DAYS - 1:
            out.append(f"{lore.HOST}: \"{FINALES[self.arena.finale]['hint']}\"")
        if self.nemesis in self.pool:
            name = f"{self.nemesis_name[0].upper()}{self.nemesis_name[1:]}"
            where = " and ".join(PLACE_NAMES[p] for p in self.arena.sight_places)
            if self.day + 1 == self.arena.sight_day and "nemesis_sighting" not in self.used:
                out.append(f"{lore.HOST}: \"Word is {self.nemesis_name} has been seen near {where}. Stay away, or go and look.\"")
            where = " and ".join(PLACE_NAMES[p] for p in self.arena.clash_places)
            if self.day + 1 == self.arena.clash_day and "nemesis_clash" not in self.used:
                out.append(f"{lore.HOST}: \"{name} is hunting now. Last seen at {where}.\"")
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

    def _ridge_odds(self) -> dict:
        if self.district == 3:
            return {"chance": 90, "base": 50, "edges": [{"icon": "3", "label": "District 3: snares", "delta": 40}]}
        if "rope" in self.kit:
            return {"chance": 70, "base": 50, "edges": [{"icon": lore.item_emoji("rope"), "label": "rope: snares", "delta": 20}]}
        return self._odds({"base": 50, "mods": ["forage"]})

    def _ridge_chance(self) -> int:
        return self._ridge_odds()["chance"]

    def _open_odds(self) -> dict:
        edges = [{"icon": "1", "label": "District 1", "delta": 20}] if self.district == 1 else []
        return {"chance": min(95, OPEN_GIFT + (20 if self.district == 1 else 0)), "base": OPEN_GIFT, "edges": edges}

    def _open_chance(self) -> int:
        return self._open_odds()["chance"]

    def _place_options(self) -> list[dict]:
        out = []
        for key in self.arena.offers[self.day]:
            emoji, label, hint, tag = PLACES_INFO[key]
            extra = {}
            if key == "stream":
                win, lose = (["fish: +2 or +3 food"] if self.arena.twist == "fat_fish" else ["fish: +1 or +2 food"]), ["others drink here too"]
                extra["minigame"] = self._fish_params()
                hint = "Fish for your supper. Water and food. Everyone else needs it too."
            elif key == "rocks":
                heal = min(self._hide_heal(), 100 - self.health)
                win, lose = [hp(heal) if heal else "you're already healthy"], [food(-1)]
            elif key == "ridge":
                if any(w in self.kit for w in lore.HUNTERS):
                    hunt = self._hunt_params()
                    shots = len(hunt["shots"])
                    win, lose = ["hunt: rabbit +1 food, deer +2" if shots > 1 else "hunt: rabbit +2 food, deer +3"], ["noise if you miss"]
                    extra["minigame"] = hunt
                    hint = (f"Hunt up high: {'three arrows' if shots > 1 else 'one throw'}. The edge of the arena.")
                else:
                    snares = self.district == 3 or "rope" in self.kit
                    win, lose = [f"{food(2 if self.district == 3 else 1 if snares else 2)}, likely"], ["some danger"]
                    hint = ("Set snares up high." if snares else "Forage up high.") + " The edge of the arena."
            elif key == "horn":
                win, lose = ["dig for loot"], ["fights likely"]
            elif key == "open":
                win, lose = ["+2 stars, likely"], ["you'll be seen"]
            else:  # feast
                win, lose = ["what you need most"], ["everyone will be there"]
            if key in ("stream", "ridge") and self.supplies >= SUPPLY_CAP:
                win = ["your pack is full of food"]
            if key in EDGE and self._refuge_open(key):
                lose = lose + [f"the last quiet place: {FINALES[self.arena.finale]['why']}"]
            elif key in EDGE:
                watched = self.edge_streak >= 1 or self.day >= EDGE_WATCHED
                lose = lose + (["the Gamemakers are watching"] if watched else ["the edge of the arena"])
            if self._nemesis_here(key):
                lose = [f"{self.nemesis_name[0].upper()}{self.nemesis_name[1:]} was seen here"] + lose
            out.append(self._opt(PLACE_KEYS.index(key), label, hint, None, tag, win, lose, place=key, icon=emoji, **extra))
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
                self.supplies += food + (1 if self.arena.twist == "fat_fish" else 0)
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
                    _, quiet, _ = self._reveal(self._flat(70), "Nobody hears you", near_counts=False)
                    if not quiet:
                        self._say("Someone heard.", "result")
                        self._hurt(20, "a tribute who heard you hunting")
            else:
                _, ok, _ = self._reveal(self._ridge_odds(), "Forage up high", near_counts=False)
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
            _, ok, _ = self._reveal(self._open_odds(), "Play to the cameras", near_counts=False)
            if ok:
                self._crowd(2, "The cameras find you, and the Capitol likes what it sees.")
            else:
                self._say("Nothing falls from the sky. And now everyone knows where you are.", "result")
        if self.over:
            return ()
        card = self._place_card(key)
        if card:
            return ("draw", card)
        self._say("The rest of the day passes quietly.", "result")
        return ("night",)

    def _refuge_open(self, key: str) -> bool:
        return self.day >= EDGE_CLOSES and key == self.arena.refuge

    def _nemesis_plan(self) -> list[tuple[str, int, list[str]]]:
        a = self.arena
        return [("nemesis_sighting", a.sight_day, a.sight_places), ("nemesis_clash", a.clash_day, a.clash_places)]

    def _nemesis_here(self, key: str) -> bool:
        """Has the nemesis been seen at this place, and not met there yet?"""
        if self.nemesis not in self.pool:
            return False
        return any(cid not in self.used and self.day >= day and key in places for cid, day, places in self._nemesis_plan())

    def _place_card(self, key: str) -> str | None:
        for cid, day, places in self._nemesis_plan():
            if cid not in self.used and self.day >= day and key in places and self._eligible(cards()[cid]):
                return cid
        for cid in self.arena.deck:
            c = cards()[cid]
            if cid not in self.used and key in c.get("place", ()) and self._eligible(c):
                return cid
        return None

    # ------------------------------------------------------------------ minigames
    def _played(self, game: str, ok: bool, label: str, note: str, near: bool = False) -> None:
        """Log a minigame's result where the roll reveal goes. near shows it amber: no harm done."""
        self.clean += 1 if ok else 0
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
        """Bury today's treasures and traps. The layout comes from the daily seed: the same for everyone."""
        r = _rng(self.seed, "sift", self.day)
        n = SIFT_SIZE

        def place(w: int, h: int, taken: set[int], gap: int) -> list[int] | None:
            if w != h and r.random() < 0.5:
                w, h = h, w
            for _ in range(200):
                x, y = r.randrange(n - w + 1), r.randrange(n - h + 1)
                cells = [(y + dy) * n + x + dx for dy in range(h) for dx in range(w)]
                # keep a gap around every shape, so two never touch and a piece's edges read true
                ring = {(y + dy) * n + x + dx for dy in range(-gap, h + gap) for dx in range(-gap, w + gap)
                        if 0 <= x + dx < n and 0 <= y + dy < n}
                if not ring & taken:
                    taken.update(cells)
                    return cells
            return None

        for attempt in range(100):
            gap = 1 if attempt < 90 else 0
            taken: set[int] = set()
            shapes = []
            for kind, w, h in SIFT_TREASURES:
                what = r.choice(SIFT_POOLS[kind]) if kind in SIFT_POOLS else ""
                shapes.append({"kind": kind, "item": what, "cells": place(w, h, taken, gap), "trap": False, "done": False})
            for kind, w, h in SIFT_TRAPS + SIFT_CAREERS:
                shapes.append({"kind": kind, "item": "", "cells": place(w, h, taken, gap), "trap": True, "done": False,
                               "loud_only": kind == "career"})
            if all(s["cells"] for s in shapes):
                break
        self.sift = {"shapes": shapes, "open": [], "digs": 0, "how": None}

    def _sift_shapes(self) -> list[dict]:
        """The shapes in play: Careers only hide in the rubble if you dig loudly."""
        sf = self.sift
        return [s for s in sf["shapes"] if not s.get("loud_only") or sf["how"] == "loud"]

    def _sift_at(self, i: int) -> dict | None:
        return next((s for s in self._sift_shapes() if i in s["cells"]), None)

    def _sift_name(self, s: dict) -> tuple[str, str]:
        if s["trap"]:
            return SIFT_TRAP_NAMES[s["kind"]]
        if s["kind"] == "pack":
            return "🎒", "a pack: 2 days of food"
        if s["kind"] == "can":
            return "🥫", "a can: a day of food"
        return lore.item_emoji(s["item"]), ("a crate with a " if s["kind"] == "crate" else "a ") + lore.item_name(s["item"])

    def _area(self, i: int, big: bool) -> list[int]:
        if not big:
            return [i]
        n = SIFT_SIZE
        x, y = i % n, i // n
        return [(y + dy) * n + x + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if 0 <= x + dx < n and 0 <= y + dy < n]

    def _sift_options(self) -> list[dict]:
        sf = self.sift
        if sf["how"] is None:
            out = []
            if self.supplies >= 1:
                out.append(self._opt(0, "Dig quietly", "Slow and careful. It takes all day.", None, "safe",
                                     [f"{SIFT_DIGS['quiet']} digs"], ["a day of food"]))
            out.append(self._opt(1, "Dig loudly", "Fast and noisy. Careers will come looking.", None, "bold",
                                 [f"{SIFT_DIGS['loud']} digs"], ["Careers hide in the rubble"]))
            return out
        out = []
        for i in range(SIFT_SIZE * SIFT_SIZE):
            if i in sf["open"]:
                continue
            out.append(self._opt(SIFT_SMALL + i, "", "", None, "safe", [], [], dig=i, big=False))
            out.append(self._opt(SIFT_BIG + i, "", "", None, "bold", [], [], dig=i, big=True))
        out.append(self._opt(SIFT_STOP, "Stop digging", "Keep what you've found and get clear.", None, "safe", [], []))
        return out

    def sift_view(self) -> dict | None:
        sf = self.sift
        if not sf or self.card != SIFT:
            return None
        n = SIFT_SIZE
        ids = {id(s): k for k, s in enumerate(sf["shapes"])}
        tiles = []
        for i in range(n * n):
            if i not in sf["open"]:
                tiles.append({"open": False})
                continue
            s = self._sift_at(i)
            if not s:
                tiles.append({"open": True})
                continue
            x, y = i % n, i // n
            same = lambda j: j in s["cells"]  # noqa: E731
            tiles.append({
                "open": True, "shape": ids[id(s)], "icon": self._sift_name(s)[0], "trap": s["trap"], "done": s["done"],
                # which sides are the treasure's outer edge, so you can read its size from a piece
                "edge": "".join(c for c, j, ok in (("t", i - n, y > 0), ("r", i + 1, x < n - 1),
                                                    ("b", i + n, y < n - 1), ("l", i - 1, x > 0))
                                if not ok or not same(j)),
            })
        legend = []
        for k, s in enumerate(self._sift_shapes()):
            icon, name = self._sift_name(s)
            xs = sorted({c % n for c in s["cells"]})
            ys = sorted({c // n for c in s["cells"]})
            # the size without its orientation: a piece's edges tell you which way it lies
            legend.append({"shape": ids[id(s)], "icon": icon, "name": name, "w": max(len(xs), len(ys)), "h": min(len(xs), len(ys)),
                           "trap": s["trap"], "done": s["done"],
                           "found": sum(c in sf["open"] for c in s["cells"])})
        return {"size": n, "digs": sf["digs"], "how": sf["how"], "tiles": tiles, "legend": legend}

    def _resolve_sift(self, index: int) -> tuple:
        sf = self.sift
        if sf["how"] is None:
            sf["how"] = ("quiet", "loud")[index]
            sf["digs"] = SIFT_DIGS[sf["how"]] + (1 if "axe" in self.kit else 0)
            if sf["how"] == "quiet":
                self.supplies -= 1
                self._say("You work slowly, lifting one plank at a time.", "result")
            else:
                self._say("You tear into the rubble with both hands. Anyone nearby can hear it.", "result")
            return ("round", None)
        if index == SIFT_STOP:
            return self._end_sift()
        big = index >= SIFT_BIG
        tile = index - (SIFT_BIG if big else SIFT_SMALL)
        sf["digs"] -= 1
        dug = [i for i in self._area(tile, big) if i not in sf["open"]]
        sf["open"].extend(dug)
        touched = []
        for i in dug:
            s = self._sift_at(i)
            if s and s not in touched:
                touched.append(s)
        got, hurt, pieces = [], [], []
        for s in touched:
            icon, name = self._sift_name(s)
            if s["trap"]:
                if s["done"]:
                    continue
                s["done"] = True
                sf["open"].extend(c for c in s["cells"] if c not in sf["open"])  # it goes off: all of it shows
                dmg, cause = SIFT_HURT[s["kind"]]
                hurt.append(icon)
                self._say({"snare": "A wire snaps tight around your wrist.",
                           "mine": "The ground under the plank clicks.",
                           "career": "A hand grabs your collar from behind the crate."}[s["kind"]], "result")
                self._hurt(dmg, cause)
                if self.over:
                    return ()
            elif all(c in sf["open"] for c in s["cells"]) and not s["done"]:
                s["done"] = True
                got.append(icon)
                if s["kind"] in ("pack", "can"):
                    self.supplies += 2 if s["kind"] == "pack" else 1
                else:
                    self._gain(s["item"])
            elif not s["done"]:
                pieces.append(icon)
        if hurt:
            label, ok, near = "HURT " + " ".join(hurt), False, False
        elif got:
            label, ok, near = "FOUND " + " ".join(got), True, False
        elif pieces:
            label, ok, near = "PART OF " + " ".join(pieces), False, True
        else:
            label, ok, near = "EMPTY", False, True
        self._played("sift", ok, label, "", near=near)
        if sf["digs"] <= 0:
            return self._end_sift()
        return ("round", None)

    def _end_sift(self, dug: bool = True) -> tuple:
        half = [self._sift_name(s)[1] for s in self._sift_shapes()
                if not s["trap"] and not s["done"] and any(c in self.sift["open"] for c in s["cells"])]
        self.sift = None
        self.card = ""
        if dug:
            self._say(f"You leave {' and '.join(half)} half dug." if half else "You leave the rubble behind.", "result")
        card = self._place_card("horn")
        if card:
            return ("draw", card)
        self._say("The rest of the day passes quietly.", "result")
        return ("night",)

    # ------------------------------------------------------------------ the end
    def _start_duel(self) -> None:
        # Everyone but you and one rival goes. The nemesis is the day's last fight if they're alive: an ally
        # who's still with you dies at their hands on the horn. Otherwise your ally (only one can win).
        if self.nemesis in self.pool:
            self.opponent_id = self.nemesis
            self.opponent = f"{self.nemesis_name} ({self.nemesis[4:]})"
        elif self.allies:
            self.opponent_id = self.opponent = self.allies[0]
        elif self.pool:
            self.opponent_id = self.pool[-1]
            self.opponent = self._name(self.pool[-1])
        else:
            self.opponent_id = self.opponent = "the last Career"
        boss = self.opponent_id == self.nemesis
        self.opp_max = OPPONENT_HEALTH["nemesis" if boss else "other"]
        self.opp_health = self._nemesis_health() if boss else self.opp_max
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
            if boss and rest_allies:
                name = " and ".join(rest_allies)
                self._say(f"{name} reached the horn with you. {self.nemesis_name[0].upper() + self.nemesis_name[1:]} "
                          f"got there first. {name} is gone, and it's just the two of you now.", "story")
        self.card = DUEL
        self.exchanges = 0
        self.days[self.day].append("⚔️")
        scars = ""
        if boss and self.wounds:
            scars = " Your work this week shows: they're still bleeding from the wounds you gave them."
        if boss and self.study:
            scars += " You've watched them fight. You know the feint."
        self._say(f"Two tributes left. You, and {self.opponent}, face to face on the horn. You have {self.health} "
                  f"health. They have {self.opp_health}.{scars} Watch which side they wind up on, and block it.", "event")

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
        boss = self.opponent_id == self.nemesis
        return mg.fight_params(_rng(self.salt, "fight", self.exchanges), stance, boss,
                               "rope" in self.kit, lore.WEAPONS[w] if w else 0, self.exchanges, read=boss and self.study)

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
            win = ["you win"] if deal >= self.opp_health else [f"them −{deal}"]
            lose = [hp(-take)] + ([DEADLY] if take >= self.health else [])
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
        ally = e.get("ally")
        kills = e.get("kill", 0)
        if ally == "dies" and kills and self.allies:
            # You killed your ally: they're the kill, not some stranger.
            self.pool.append(self.allies.pop(0))
            self.target = self.pool[-1]
            ally = None
        if kills:
            self._kill(kills)
        if e.get("wound"):
            self._wound(e["wound"])
        if e.get("study") and not self.study:
            self.study = True
            self._say(f"You know how {self.nemesis_name} fights now. In the final fight, their feints won't fool you as often.", "story")
        if e.get("target_dies"):
            self._kill(1, mine=False)
        if e.get("partner_dies") and "partner" in self.pool:
            self.pool.remove("partner")
            self.fallen_today.append("partner")
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

    def _kill(self, n: int, mine: bool = True) -> None:
        """n tributes die: the card's target first. mine=False when they die but it isn't your kill."""
        for _ in range(n):
            if self.target in self.pool:
                v = self.target
                self.target = None
            else:
                victims = [t for t in self.pool if t not in ("partner", self.nemesis)] or self.pool
                if not victims:
                    return
                v = victims[0]
                if v == self.nemesis and mine:
                    self._wound(30)
                    continue
            self.pool.remove(v)
            self.fallen_today.append(v)
            if mine:
                self.cheer = "A kill. The crowd sits up."
                self.killed.add(v)
                self.new_kills.append(self._name(v))
                self.kills += 1
                self.kills_today += 1
            for d in range(self.day, MAX_DAYS + 1):
                if self.schedule[d] > 0:
                    self.schedule[d] -= 1
                    break
        if mine and "⚔️" not in self.days[self.day]:
            self.days[self.day].append("⚔️")

    def _wound(self, n: int) -> None:
        if self.nemesis not in self.pool:
            return
        self.wounds += n
        name = self.nemesis_name[0].upper() + self.nemesis_name[1:]
        left = self._nemesis_health()
        self._say(f"{name} is wounded. If you meet in the final fight, they start with {left} health, not "
                  f"{OPPONENT_HEALTH['nemesis']}.", "story")
        if "🎯" not in self.days[self.day]:
            self.days[self.day].append("🎯")

    def _nemesis_health(self) -> int:
        return max(NEMESIS_FLOOR, OPPONENT_HEALTH["nemesis"] - self.wounds)

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
        elif self.health < 100:
            self.health = min(100, self.health + 30)
            what = "medicine"
        elif "rope" not in self.kit and len(self.kit) < lore.KIT_SLOTS:
            self.kit.append("rope")
            what = "a coil of rope"
        elif self.supplies < SUPPLY_CAP:
            self.supplies += 2
            what = "a hot meal"
        else:
            self._crowd(1, "The sponsors have nothing you need. They send a note instead: the Capitol loves you.")
            what = "a note: \"Keep going.\""
        self.days[self.day].append("🎁")
        self._say(f"A silver parachute drifts down: {what}.", "gift")

    # ------------------------------------------------------------------ the crowd and the sponsors
    def _crowd(self, n: int, why: str) -> None:
        was = self.stars
        self.stars = max(0, min(STARS_MAX, self.stars + n))
        if self.stars != was:
            self._say(why, "crowd")

    def sponsor_menu(self) -> list[dict]:
        open_ = not self.over and self.order is None and self.card not in (DUEL, SWAP, DRAFT)
        out = []
        for k, (key, label, icon, price) in enumerate(SPONSOR_MENU):
            price += self.bought - (1 if self.arena.twist == "generous" else 0)
            gear = key not in ("food", "medicine")
            note = ("you have one" if gear and key in self.kit else
                    "your pack is full" if gear and len(self.kit) >= lore.KIT_SLOTS else "")
            out.append({"index": BUY + k, "key": key, "label": label, "icon": icon, "price": price, "note": note,
                        "can": open_ and not note and self.stars >= price})
        return out

    def _buy(self, k: int) -> None:
        item = self.sponsor_menu()[k]
        if not item["can"]:
            raise ValueError("the sponsors won't send that now")
        self.steps += 1
        snap = self._snap()
        self._say(f"Ask the sponsors for {item['label'][0].lower()}{item['label'][1:]}", "choice")
        self.stars -= item["price"]
        self.bought += 1
        self.order = item["key"]
        self._say("You look up at the cameras and ask. It will drop tonight, if anyone's still watching.", "result")
        self._delta(snap)

    def _drop(self) -> None:
        """Tonight's parachute, the one you paid for."""
        key, self.order = self.order, None
        label = next(lb for k, lb, _, _ in SPONSOR_MENU if k == key)
        if key == "food":
            self.supplies += 2
        elif key == "medicine":
            self.health = min(100, self.health + 30)
        else:
            self._gain(key)
        who = SPONSORS[(self.day + self.bought) % len(SPONSORS)]
        self.days[self.day].append("🎁")
        self._say(f"A silver parachute drifts down: {label[0].lower()}{label[1:].split(':')[0]}. A gift from {who}.", "gift")

    def hunger_cost(self) -> int:
        """What tonight costs if you have nothing to eat: 10, then 20, then 30 in a row. District 12 takes half."""
        n = 10 * min(3, self.hungry + 1) + (5 if self.arena.twist == "cold" else 0)
        return n // 2 if self.district == 12 else n

    def _hurt(self, n: int, cause: str) -> None:
        if (n >= self.health and self.allies and not self.saved and cause != "starvation"
                and self.card != DUEL and self.health > 0):
            self.saved = True
            name = self.allies.pop(0)
            self.fallen_today.append(name)
            self._say(f"{name} throws themself between you and it. {name} takes the blow meant for you, and doesn't "
                      "get up. You're alive because of them.", "story")
            self.hits.append((self.day, 0, f"{name} died saving you"))
            return
        if n > 0:
            self.hits.append((self.day, n, cause))
        self.health -= n
        if self.health <= 0:
            self._die(cause)

    def _die(self, cause: str) -> None:
        if self.over:
            return
        self.recap = self._recap(cause)
        self.health = 0
        self.over = True
        self.cause = cause
        self.placement = self.remaining
        self.days[self.day].append("☠️")
        self._say(f"You have died. Cause of death: {cause}.", "death")
        self.card = ""

    def _recap(self, cause: str) -> str:
        """The chain that killed you, not just the last blow: the big hits, the hungry nights, the end."""
        label, chance, health = self.last
        hungry = sum(1 for _, _, c in self.hits if c == "starvation")
        big = [h for h in self.hits[:-1] if h[2] not in ("starvation", cause) and h[1] >= 20]
        big = sorted(sorted(big, key=lambda h: -h[1])[:2])
        parts = [f"Day {d}: {c} took {n}." for d, n, c in big]
        if cause == "starvation":
            nights = {1: "One hungry night", 2: "Two hungry nights", 3: "Three hungry nights"}.get(hungry, f"{hungry} hungry nights")
            parts.append(f"{nights} finished it." if parts else "You ran out of food.")
        else:
            if hungry:
                parts.append(f"{'One hungry night' if hungry == 1 else f'{hungry} hungry nights'} wore you down.")
            if label and chance is not None:
                parts.append(f"Then you took the {chance}% “{label}” with {health} health.")
            elif label:
                parts.append(f"Then you chose “{label}” with {health} health.")
        text = " ".join(parts)
        return text[0].upper() + text[1:] if text else ""

    def _win(self) -> None:
        self.over = True
        self.won = True
        self.placement = 1
        self.days[self.day].append("👑")
        self._say("Ladies and gentlemen, the victor of the Hunger Games!", "victory")
        self.card = ""

    # ------------------------------------------------------------------ odds
    def _chance(self, roll: dict) -> int:
        return self._odds(roll)["chance"]

    def _odds(self, roll: dict) -> dict:
        """The chance, and what built it: the card's base plus each edge you can see on the wheel."""
        h = self.health
        hurt = 0 if h >= 70 else -5 if h >= 45 else -12 if h >= 20 else -20
        edges: dict[str, dict] = {}

        def edge(icon: str, label: str, d: int) -> None:
            if d:
                e = edges.setdefault(label, {"icon": icon, "label": label, "delta": 0})
                e["delta"] += d

        for m in roll.get("mods", []):
            if m == "combat":
                best = max(self.kit, key=lambda i: lore.WEAPONS.get(i, 0), default=None)
                if best and lore.WEAPONS.get(best, 0):
                    edge(lore.item_emoji(best), lore.item_name(best), lore.WEAPONS[best])
                edge("2", "District 2", 10 if self.district == 2 else 0)
                edge("🤝", "your ally" if len(self.allies) == 1 else "your allies", 5 * len(self.allies))
                edge("🩸", "your wounds", hurt)
            elif m == "escape":
                edge(lore.item_emoji("rope"), "rope", 10 if "rope" in self.kit else 0)
                edge("5", "District 5", 10 if self.district == 5 else 0)
                edge("🩸", "your wounds", hurt)
            elif m == "stealth":
                edge("🤝", "your ally" if len(self.allies) == 1 else "your allies", -5 * len(self.allies))
                edge("8", "District 8", 15 if self.district == 8 else 0)
                edge("🩸", "your wounds", hurt)
            elif m == "forage":
                edge("11", "District 11", 30 if self.district == 11 else 0)
                edge(lore.item_emoji("iodine"), "iodine", 20 if "iodine" in self.kit else 0)
            elif m == "swim":
                edge("4", "District 4", 30 if self.district == 4 else 0)
            elif m == "grab":
                edge("6", "District 6", 10 if self.district == 6 else 0)
        out = [e for e in edges.values() if e["delta"]]
        c = roll["base"] + sum(e["delta"] for e in out)
        return {"chance": max(5, min(95, c)), "base": roll["base"], "edges": out}

    def _dice(self) -> random.Random:
        return _rng(self.salt, self.steps, len(self.log))

    def _reveal(self, odds: dict, label: str = "", got: str = "", near_counts: bool = True) -> tuple[int, bool, bool]:
        """Roll for an option the player chose, and log the roll so the wheel can land on it.
        got: what a near miss still gets you (the bloodbath), shown on the yellow banner.
        near_counts: False when a near miss changes nothing, so the wheel shows no yellow."""
        chance = odds["chance"]
        roll = self._dice().randint(1, 100)
        ok = roll <= chance
        near = near_counts and not ok and roll <= chance + NEAR
        self.log.append({"day": self.day, "kind": "roll", "text": f"{chance}% · rolled {roll}",
                         "chance": chance, "roll": roll, "ok": ok, "near": near, "label": label,
                         "base": odds["base"], "edges": odds["edges"], "got": got, "near_zone": near_counts})
        if ok and chance <= LONG_SHOT:
            self.cheer = "A long shot, and it landed. The crowd is on its feet."
        return roll, ok, near

    @staticmethod
    def _flat(chance: int) -> dict:
        return {"chance": chance, "base": chance, "edges": []}

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
        return (self.health, self.supplies, list(self.kit), list(self.allies), self.stars)

    def _delta(self, snap: tuple, night: bool = False) -> None:
        if self.over and not self.won:
            return
        health, supplies, kit, allies, stars = snap
        parts = []
        if self.health != health:
            parts.append(f"❤️ {_sign(self.health - health)}")
        if self.supplies != supplies:
            parts.append(f"🎒 {_sign(self.supplies - supplies)}")
        if self.stars != stars:
            parts.append(f"⭐ {_sign(self.stars - stars)}")
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
            "hunger": 0 if self.supplies else self.hunger_cost(),  # what tonight costs you, if you don't eat
            "stars": self.stars,
            "stars_max": STARS_MAX,
            "order": next((lb for k, lb, _, _ in SPONSOR_MENU if k == self.order), None),
            "menu": self.sponsor_menu(),
            "fight": {"them": self.opp_health, "max": self.opp_max, "name": self.opponent,
                      "boss": self.opponent_id == self.nemesis} if self.card == DUEL else None,
        }

    def emoji_row(self) -> str:
        priority = ["👑", "☠️", "⚔️", "🎯", "🎁"]
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
            pts += 500 + 3 * self.health  # how much of you made it home
        # What you did along the way, so two second places on day 8 don't tie.
        pts += 15 * self.clean  # minigames played cleanly
        pts += 2 * min(self.wounds, OPPONENT_HEALTH["nemesis"])  # what you took out of the nemesis
        pts += 150 if self.nemesis in self.killed or (self.won and self.opponent_id == self.nemesis) else 0
        # Your district's multiplier, then 10% for every star you never spent.
        return round(pts * lore.multiplier(self.district) * (1 + 0.1 * self.stars))


def new_setup(district: int, partner: str, allies: list[str]) -> dict:
    if district not in lore.DISTRICTS:
        raise ValueError("bad district")
    return {"district": district, "partner": partner, "allies": allies[:2]}


def replay(seed: int, salt: int, setup: dict, choices: list[list[int | None]]) -> Game:
    g = Game(seed, salt, setup)
    for idx, param in choices:
        g.choose(idx, param)
    return g
