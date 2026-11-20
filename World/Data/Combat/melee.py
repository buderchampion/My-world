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

Magic (Data.Magic.magic): casters cast their day's spells instead of
swinging -- healing, harm, sleep and holding, blessings; bards sing their side
into battle. Sleeping and held fighters do not fight and are hit by every
blow; a routed one has left the field (alive, but out of the fight).

Special attacks (Data.Combat.specials): breath weapons, gazes, poison,
paralysis, petrification, energy drain, swallowing, hugs and rakes and the
rest of the book. Stunned fighters lose their turn but are not helpless; the
swallowed are out of the fight until their monster falls.
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
    "Adventurer": (2, 6, (1, 8)),  # studded leather and shield, a sword (Data.Quests.adventure)
}
COMMONER = (0, 10, (1, 4))         # no armor, a club or a dagger
ARMORED_AC = 4                     # an Armor suit from the stores: chain mail and shield
HP_COMMONER = 4                    # about a d6
SKILL_LEVELS_PER_FIGHTING_LEVEL = 2  # Swordsmanship: every two levels beyond the first add a fighting level...
MAX_FIGHTING_LEVEL = 12              # ...up to this (the greatest heroes of the world)


def fighting_bonus(skills):
    """Fighting levels earned by training: (Swordsmanship - 1) // 2."""
    return max(0, ((skills or {}).get("Swordsmanship") or 1) - 1) // SKILL_LEVELS_PER_FIGHTING_LEVEL


def fighting_level(trade_level, skills):
    return min(MAX_FIGHTING_LEVEL, trade_level + fighting_bonus(skills))
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
    hit_dice: float = 1            # for spells: sleep and the like reach the weaker first
    nature: str = "person"         # person, humanoid (orcs and their like), beast or monster: what a hold spell can grip
    to_hit: int = 0                # blessings and songs (and curses)
    held: int = 0                  # rounds still asleep or held
    out: bool = False              # routed: fled the field
    damage_scale: float = 1.0      # weakened
    spells: list = field(default_factory=list)
    caster_level: int = 0
    inspires: int = 0              # a bard's song
    guarded: bool = False          # has cast their shield (or their club) already
    cast: int = 0                  # spells cast this fight
    race: str = ""                 # a person's people (Data.NPCS.races): the book's edge in a fight; "" for monsters
    specials: list = field(default_factory=list)   # a monster's special attacks (Data.Combat.specials)
    stunned: int = 0               # rounds that lose their turn (surprised, confused, blinded) without being helpless
    swallowed_by: object = None    # the Fighter it is inside of
    cause: str = ""                # how it died, when a special attack killed it
    fx: dict = field(default_factory=dict)         # what specials have left on it: drained levels, grips, sickness...
    max_engaged: int = 0           # at most this many foes can strike it in a round (a dragon on the wing); 0: no limit
    damage_bonus: int = 0          # Strength's damage bonus (Data.NPCS.abilities), on every blow
    wis_save: int = 0              # Wisdom's saving throw bonus against fear, charm and the like

    def __post_init__(self):
        self.max_hp = self.max_hp or self.hp

    @property
    def alive(self):
        return self.hp > 0 and not self.out and self.swallowed_by is None

    @property
    def fallen(self):
        return self.hp <= 0


def monster_fighters(monster, count, hp_each, side="monsters", ref=None):
    """`count` of a bestiary monster, each with `hp_each` hit points."""
    stats = monster["stats"]
    n = max(1, stats.get("attacks") or 1)
    ranges = stats.get("damage") or [[1, 8]]                      # "By weapon" and the like: a weapon's 1-8
    attacks = [tuple(ranges[min(i, len(ranges) - 1)]) for i in range(n)]
    ac = stats.get("ac") if stats.get("ac") is not None else 10
    thac0 = monster_thac0(stats.get("hit_dice"), stats.get("hit_dice_bonus") or 0)
    mindless = monster.get("intelligence_level", 1) == 0
    hd = stats.get("hit_dice") or 1
    kind = monster.get("kind")
    nature = "beast" if kind in ("beast", "mundane") else "humanoid" if kind in ("raider", "people") and hd <= 4 else "monster"
    from Data.Combat.specials import arm
    return [arm(Fighter(monster["name"], side, hp_each, ac, thac0, attacks, mindless, ref, hit_dice=hd, nature=nature), monster)
            for _ in range(count)]


def person_fighter(npc, side="people", armored=False, skills=None):
    """An NPC as a fighter: their trade's level, gear and weapon, raised by Swordsmanship and Shield."""
    level, ac, weapon = TRADES.get(npc.get("profession"), COMMONER)
    skills = skills if skills is not None else {k: v.get("level", 1) if isinstance(v, dict) else v
                                                for k, v in (npc.get("skills") or {}).items()}
    from Data.NPCS import races
    race = npc.get("race") or races.HUMAN
    level = fighting_level(level, skills)                          # training: two Swordsmanship levels per fighting level
    limit = races.fighting_limit(race)                             # ...up to their people's limit (a halfling's is 6)
    if limit is not None:
        level = min(level, limit)
    if armored:
        ac = min(ac, ARMORED_AC)
    if skills.get("Shield"):
        ac -= 1
    hp = HP_COMMONER if level <= 0 else HP_PER_LEVEL * level
    fighter = Fighter(npc.get("name", "?"), side, hp, ac, person_thac0(level), [weapon], False, npc.get("id"),
                      hit_dice=max(0, level), race=race)
    from Data.Magic.magic import casting_level, arm_caster       # casters fight with their spells
    casting = casting_level(npc.get("class"), npc.get("profession"), skills, race)
    if casting:
        arm_caster(fighter, npc.get("class"), casting, npc.get("id"), npc.get("wisdom"))
    fighter.spells = fighter.spells + races.racial_powers(race)    # a dragonborn's breath, a tiefling's rebuke
    from Data.NPCS import abilities                                 # the book's Strength, Dexterity, Constitution, Wisdom
    scores = abilities.of(npc)
    if scores:
        hit, damage = abilities.strength_bonus(scores)
        fighter.to_hit += hit
        fighter.damage_bonus = damage
        fighter.ac += abilities.dexterity_ac(scores)
        per_level = abilities.constitution_hp(scores, npc.get("class"))
        fighter.hp = fighter.max_hp = max(1.0, fighter.hp + per_level * max(1, level, fighter.caster_level))
        fighter.wis_save = abilities.wisdom_save(scores)
    return fighter


def roll(lo, hi):
    return random.randint(min(lo, hi), max(lo, hi))


def attack_all(attacker, target, rnd=0):
    """All of one fighter's attacks on one target this round. Returns the damage done."""
    done = 0
    from Data.NPCS.races import to_hit_bonus
    from Data.Combat.specials import on_hit, after_attacks
    edge = to_hit_bonus(attacker, target)          # an elf's sword arm, a dwarf against orcs, a giant swinging at a gnome
    hits = []
    for i, (lo, hi) in enumerate(attacker.attacks):
        die = random.randint(1, 20)
        if target.held or die == 20 or (die != 1 and die + attacker.to_hit + edge >= attacker.thac0 - target.ac):
            blow = max(1, roll(lo, hi) + attacker.damage_bonus) * attacker.damage_scale
            done += blow
            hits.append(i)
            if attacker.specials:
                done += on_hit(attacker, target, i, die, blow, rnd)
    if attacker.specials and hits:
        done += after_attacks(attacker, target, hits, len(attacker.attacks)) * attacker.damage_scale
    return done


def battle(side_a, side_b, morale=MORALE, max_rounds=MAX_ROUNDS):
    """Fights it out. Returns {"winner": "a" | "b" | None, "rounds", "fled": "a" | "b" | None, "a": survivors, "b": survivors,
    "dead_a": [...], "dead_b": [...]}."""
    from Data.Magic.magic import cast_in_battle, inspire
    from Data.Combat import specials
    start = {"a": len(side_a), "b": len(side_b)}
    sides = {"a": side_a, "b": side_b}
    state = {k: {"morale": morale, "blessed": set()} for k in sides}
    for k in sides:                                                # bards sing their side into the fight
        inspire(sides[k], state[k])
    specials.opening(side_a, side_b)                               # ambushes, terrors, stenches
    start = {k: max(1, sum(1 for f in v if not f.out)) for k, v in sides.items()}   # who ran at the sight never stood in the line
    fled = None
    rounds = 0
    for rounds in range(1, max_rounds + 1):
        harm = {}
        engaged = {}                                               # blows aimed at each fighter this round
        for me, them in (("a", "b"), ("b", "a")):
            foes = [f for f in sides[them] if f.alive]
            if not foes:
                break
            for fighter in sides[me]:
                if fighter.alive and not fighter.held and not fighter.stunned:
                    if fighter.specials and specials.act(fighter, sides[me], sides[them], rounds):
                        continue                                   # a breath, a volley, a wail: instead of its blows
                    if fighter.spells and cast_in_battle(fighter, sides[me], sides[them], state[me], state[them]):
                        continue                                   # a spell, this round, instead of a blow
                    targets = [f for f in foes if f.alive and not (f.max_engaged and engaged.get(id(f), 0) >= f.max_engaged)]
                    if not targets:
                        continue                                   # no foe it can reach this round
                    target = random.choice(targets)
                    engaged[id(target)] = engaged.get(id(target), 0) + 1
                    harm[id(target)] = harm.get(id(target), 0) + attack_all(fighter, target, rounds)
        for fighter in side_a + side_b:                            # blows land together
            fighter.hp -= harm.get(id(fighter), 0)
            if fighter.held:
                fighter.held -= 1
        specials.round_end(side_a + side_b)                        # the gripped bleed, the swallowed are digested
        alive = {k: [f for f in v if f.alive] for k, v in sides.items()}
        if not alive["a"] or not alive["b"]:
            break
        for k in ("a", "b"):                                       # a side that has lost half its number breaks
            lost = 1 - len(alive[k]) / max(1, start[k])
            if lost >= state[k]["morale"] and not all(f.mindless for f in alive[k]):
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
    specials.finish(side_a, side_b, winner)                        # the swallowed are lost, the slimed may be saved
    alive = {k: [f for f in v if f.alive] for k, v in sides.items()}
    return {"winner": winner, "rounds": rounds, "fled": fled, "a": alive["a"], "b": alive["b"],
            "dead_a": [f for f in side_a if f.fallen], "dead_b": [f for f in side_b if f.fallen]}
