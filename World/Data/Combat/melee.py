"""
Fighting by the book: AD&D (first edition) melee, round by round.

    To hit   an attack rolls a d20 and needs at least the attacker's "to hit
             armor class 0" number (THAC0) minus the defender's armor class;
             a natural 20 always hits, a natural 1 always misses.
    THAC0    monsters by their hit dice (the Dungeon Master's Guide table),
             people by their fighting level (as fighters).
    Damage   monsters roll their own ranges (Monsters.json); people by weapon.
    Rounds   every living fighter attacks a random living enemy; blows land
             together, then the dead fall. A side that has lost MORALE of its
             number breaks and flees -- unless it is mindless (intelligence 0:
             undead, oozes, golems), which fight to the end.

Kept for later: special attacks (breath weapons, poison, paralysis) are text
in the book and need their own rules; monsters fight with their attacks only.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

MORALE = 0.5            # a side breaks when it has lost this share of its number
MAX_ROUNDS = 100

# Monsters: THAC0 by hit dice (DMG, monster attack matrix). Keys: (whole dice, has a bonus).
def monster_thac0(hit_dice, bonus=0):
    hd = hit_dice or 0
    if hd < 1:
        return 20
    if hd == 1:
        return 18 if bonus > 0 else 19
    for top, thac0 in ((3, 16), (5, 15), (7, 13), (9, 12), (11, 10), (13, 9), (15, 8)):
        if hd <= top:
            return thac0
    return 7


# People, as fighters: THAC0 by level (a normal man, level 0, is 21).
def person_thac0(level):
    if level <= 0:
        return 21
    return max(4, 20 - 2 * ((level - 1) // 2))


# A trade's fighting level, armor class and weapon (damage range).
TRADES = {
    "Knight":  (3, 3, (1, 8)),     # plate and shield, a sword
    "Captain": (4, 3, (1, 8)),
    "Soldier": (1, 7, (1, 8)),     # leather and shield, a sword
    "Guard":   (1, 7, (1, 6)),     # leather and shield, a spear
    "Bandit":  (1, 8, (1, 6)),     # leather, a short sword
}
COMMONER = (0, 10, (1, 4))         # no armor, a club or a dagger
ARMORED_AC = 4                     # an Armor suit from the stores: chain mail and shield
HP_COMMONER = 4                    # about a d6
HP_PER_LEVEL = 5.5                 # a fighter's d10, on average


@dataclass
class Fighter:
    name: str
    side: str
    hp: float
    ac: int
    thac0: int
    attacks: list                  # damage ranges, one per attack
    mindless: bool = False
    ref: object = None             # the NPC id or the lair id it stands for
    max_hp: float = 0.0

    def __post_init__(self):
        self.max_hp = self.max_hp or self.hp

    @property
    def alive(self):
        return self.hp > 0


def monster_fighters(monster, count, hp_each, side="monsters", ref=None):
    """`count` of a bestiary monster, each with `hp_each` hit points."""
    stats = monster["stats"]
    n = max(1, stats.get("attacks") or 1)
    ranges = stats.get("damage") or [[1, 8]]                      # "By weapon" and the like: a weapon's 1-8
    attacks = [tuple(ranges[min(i, len(ranges) - 1)]) for i in range(n)]
    ac = stats.get("ac") if stats.get("ac") is not None else 10
    thac0 = monster_thac0(stats.get("hit_dice"), stats.get("hit_dice_bonus") or 0)
    mindless = monster.get("intelligence_level", 1) == 0
    return [Fighter(monster["name"], side, hp_each, ac, thac0, attacks, mindless, ref) for _ in range(count)]


def person_fighter(npc, side="people", armored=False, skills=None):
    """An NPC as a fighter: their trade's level, gear and weapon, raised by Swordsmanship and Shield."""
    level, ac, weapon = TRADES.get(npc.get("profession"), COMMONER)
    skills = skills if skills is not None else {k: v.get("level", 1) if isinstance(v, dict) else v
                                                for k, v in (npc.get("skills") or {}).items()}
    level += max(0, (skills.get("Swordsmanship") or 1) - 1)        # every level of training beyond the first
    if armored:
        ac = min(ac, ARMORED_AC)
    if skills.get("Shield"):
        ac -= 1
    hp = HP_COMMONER if level <= 0 else HP_PER_LEVEL * level
    return Fighter(npc.get("name", "?"), side, hp, ac, person_thac0(level), [weapon], False, npc.get("id"))


def roll(lo, hi):
    return random.randint(min(lo, hi), max(lo, hi))


def attack_all(attacker, target):
    """All of one fighter's attacks on one target this round. Returns the damage done."""
    done = 0
    for lo, hi in attacker.attacks:
        die = random.randint(1, 20)
        if die == 20 or (die != 1 and die >= attacker.thac0 - target.ac):
            done += roll(lo, hi)
    return done


def battle(side_a, side_b, morale=MORALE, max_rounds=MAX_ROUNDS):
    """Fights it out. Returns {"winner": "a" | "b" | None, "rounds", "fled": "a" | "b" | None, "a": survivors, "b": survivors,
    "dead_a": [...], "dead_b": [...]}."""
    start = {"a": len(side_a), "b": len(side_b)}
    sides = {"a": side_a, "b": side_b}
    fled = None
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        harm = {}
        for me, them in (("a", "b"), ("b", "a")):
            foes = [f for f in sides[them] if f.alive]
            if not foes:
                break
            for fighter in sides[me]:
                if fighter.alive:
                    target = random.choice(foes)
                    harm[id(target)] = harm.get(id(target), 0) + attack_all(fighter, target)
        for fighter in side_a + side_b:                            # blows land together
            fighter.hp -= harm.get(id(fighter), 0)
        alive = {k: [f for f in v if f.alive] for k, v in sides.items()}
        if not alive["a"] or not alive["b"]:
            break
        for k in ("a", "b"):                                       # a side that has lost half its number breaks
            lost = 1 - len(alive[k]) / max(1, start[k])
            if lost >= morale and not all(f.mindless for f in alive[k]):
                fled = k
                break
        if fled:
            break
    alive = {k: [f for f in v if f.alive] for k, v in sides.items()}
    if fled:
        winner = "b" if fled == "a" else "a"
    elif alive["a"] and not alive["b"]:
        winner = "a"
    elif alive["b"] and not alive["a"]:
        winner = "b"
    else:
        winner = None
    return {"winner": winner, "rounds": rounds, "fled": fled, "a": alive["a"], "b": alive["b"],
            "dead_a": [f for f in side_a if not f.alive], "dead_b": [f for f in side_b if not f.alive]}