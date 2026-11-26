"""Beta names and numbers: district perks, kit items, multipliers.

Franchise names still come from the live lore file, so there is one place to rename them.
"""

from ..game.lore import CAPITOL, DEFAULT_NAMES, DISTRICTS, GAMES, HORN, HOST  # noqa: F401

# One small perk per district, from its industry. Each one names the roll or rule it changes.
PERKS = {
    1: ("Pampered", "The Capitol already loves you. +20 on sponsor gifts out in the open."),
    2: ("Trained", "+10 in fights along the way."),
    3: ("Tinkerer", "You can set snares on the ridge without rope, and they catch more."),
    4: ("Fisher", "+30 in the water, and a taller bar when you fish."),
    5: ("Quick", "+10 when running or climbing."),
    6: ("Sprinter", "+10 on every grab at the bloodbath."),
    7: ("Woodsman", "Hiding heals twice as much."),
    8: ("Unseen", "+15 when sneaking. Hold your breath longer when you hide."),
    9: ("Stocked", "Start with 2 extra days of supplies."),
    10: ("Herder", "A third animal turns up every time you hunt."),
    11: ("Plant lore", "+30 when foraging. You know which berries kill."),
    12: ("Hardened", "Hunger hurts half as much."),
}

CAREERS = (1, 2, 4)


# Oregon Trail's banker, carpenter and farmer: the poorer start scores more. Careers 1x, the
# middle districts 2x, the two poorest 3x (and they start with less food, see engine.Game).
def multiplier(district: int) -> int:
    if district in CAREERS:
        return 1
    return 3 if district in (11, 12) else 2


# name, emoji, what it does for you (shown at the bloodbath and in your kit)
ITEMS = {
    "knife": ("knife", "🔪", "Cut yourself free of nets and snares. A small edge in a fight."),
    "spear": ("spear", "🗡️", "Hunt with one big throw. A taller bar when you fish. A good edge in a fight."),
    "axe": ("axe", "🪓", "Cut yourself free of nets and snares. A good edge in a fight."),
    "bow": ("bow", "🏹", "Hunt with three arrows. A strong edge in a fight."),
    "trident": ("trident", "🔱", "Hunt with one big throw. A taller bar when you fish. The best edge in a fight."),
    "rope": ("rope", "🪢", "Climb, run, haul yourself out. Set snares on the ridge. A wider block in the final fight."),
    "sleeping_bag": ("sleeping bag", "🛏️", "Sleep warm through cold nights. Hiding heals 15 more."),
    "medkit": ("medkit", "🩹", "Tap it to heal 40, once."),
    "iodine": ("iodine", "🧪", "Drink any water safely. Better odds when you forage."),
}
WEAPONS = {"knife": 8, "spear": 12, "axe": 12, "bow": 15, "trident": 20}
HUNTERS = ("bow", "spear", "trident")
KIT_SLOTS = 3

# Packs at the Cornucopia are supplies, not kit: they don't take a slot.
PACKS = {"pack": ("backpack", "🎒", 3), "crate": ("supply crate", "📦", 5)}


def item_name(i: str) -> str:
    return PACKS[i][0] if i in PACKS else ITEMS[i][0]


def item_emoji(i: str) -> str:
    return PACKS[i][1] if i in PACKS else ITEMS[i][1]
