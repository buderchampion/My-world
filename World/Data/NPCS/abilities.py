"""
Ability scores, after the AD&D 1st edition Player's Handbook (Abilities.json).

Every person in a new world has six scores, 3 to 18 (a few peoples reach 19):
Strength, Intelligence, Wisdom, Dexterity, Constitution and Charisma, and a
fighter with 18 Strength rolls exceptional Strength (18/01 to 18/00, kept as
strength_pct; 100 is 18/00).

Rolling     ordinary folk roll 3d6 for each score, in order. Anyone with a
            class rolls as the book's Method I: 4d6 dropping the lowest die,
            six times, the rolls arranged to suit the class (the best where its
            minimums are highest, then its prime requisites). Their people's
            adjustments are added and the result is kept within the people's
            limits (a dwarf's Constitution is at least 12). (Measured: rolled
            in order, a Paladin's minimums were met 1 time in 70 and a world's
            174 Paladins became 1; arranged, about 1 in 4.)
Children    take half from their parents' average and half from their own
            roll (3d6, adjusted for their people), within their people's limits.
Classes     a class has minimum scores (a Paladin needs Charisma 17). Someone
            whose scores miss the minimums of the class their trade would give
            them takes another class their trade allows that they meet -- or
            none. Among the classes they meet, their personality tilts the
            choice (Data.NPCS.personality.CLASS_TILT: the pious lean to Cleric).
At generation a person's traits come first, then their scores, then their
trade (tilted by their traits) and class (within its minimums).

What the scores do in the world (fights, health, learning, decisions) comes in
later steps. A world made before ability scores (Data.NPCS.personality
.PEOPLE_VERSION below 2) has none: its people choose classes the old way.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

ABILITIES = ("strength", "intelligence", "wisdom", "dexterity", "constitution", "charisma")
ABILITIES_FILE = Path(__file__).resolve().parent / "Abilities.json"
LOWEST, HIGHEST = 3, 18
PRIME_AT = 16                  # a prime requisite this high (all of them) gives PRIME_BONUS more experience
PRIME_BONUS = 0.10


@lru_cache(maxsize=1)
def table():
    with open(ABILITIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def people(race):
    return table()["peoples"].get(race) or table()["peoples"]["Human"]


def limits(race, ability):
    return people(race)["limits"].get(ability, [LOWEST, HIGHEST])


def roll(heroic=False):
    """One score: 3d6, or for a hero 4d6 dropping the lowest."""
    if heroic:
        dice = sorted(random.randint(1, 6) for _ in range(4))
        return sum(dice[1:])
    return sum(random.randint(1, 6) for _ in range(3))


def heroic_rolls():
    """Method I: six rolls of 4d6 dropping the lowest, best first, to be arranged."""
    return sorted((roll(heroic=True) for _ in range(6)), reverse=True)


def arrange(race, rolls, npc_class):
    """Places six rolls (best first) where the class needs them: the highest minimums first, then its prime
    requisites, then the rest; a score the class caps (the Barbarian's Wisdom) takes the lowest roll. Then the
    people's adjustments and limits."""
    rule = table()["classes"].get(npc_class) or {}
    minimums, maximums, prime = rule.get("minimums", {}), rule.get("maximums", {}), rule.get("prime", [])
    order = sorted(ABILITIES, key=lambda a: (a in maximums, -minimums.get(a, 0), a not in prime))
    adjust = people(race)["adjust"]
    return dict(fit(race, {a: r + adjust.get(a, 0) for a, r in zip(order, rolls)}), strength_pct=0)


def fit(race, scores):
    """Adjusts nothing; keeps each score within the people's limits."""
    out = dict(scores)
    for a in ABILITIES:
        low, high = limits(race, a)
        out[a] = max(low, min(high, int(round(out[a]))))
    return out


def new_scores(race, heroic=False):
    """A person's own scores: rolled, adjusted for their people, within its limits."""
    adjust = people(race)["adjust"]
    return dict(fit(race, {a: roll(heroic) + adjust.get(a, 0) for a in ABILITIES}), strength_pct=0)


def inherited_scores(parent_a, parent_b, race):
    """A child's scores: half their parents' average, half their own roll (adjusted for their people). A score
    neither parent has (an older parent) is the child's own roll."""
    own = new_scores(race)
    out = {}
    for a in ABILITIES:
        known = [p.get(a) for p in (parent_a, parent_b) if p and p.get(a) is not None]
        out[a] = (sum(known) / len(known) + own[a]) / 2 if known else own[a]
    return dict(fit(race, out), strength_pct=0)


def of(npc):
    """The scores of a person (a dict or row), or None if they have none (an older world)."""
    try:
        if npc["strength"] is None:
            return None
        return {a: npc[a] for a in ABILITIES} | {"strength_pct": npc["strength_pct"] or 0}
    except (KeyError, IndexError):
        return None


def qualifies(scores, npc_class):
    """Do these scores meet the class's minimums (and maximums)? A class with no entry, or no class, always does;
    so does anyone without scores (an older world)."""
    rule = table()["classes"].get(npc_class)
    if not rule or scores is None:
        return True
    if any(scores.get(a, 0) < v for a, v in rule.get("minimums", {}).items()):
        return False
    return not any(scores.get(a, 0) > v for a, v in rule.get("maximums", {}).items())


def prime_bonus(npc_class, scores):
    """+10% experience if every prime requisite of the class is 16 or more (0 if the class has none)."""
    rule = table()["classes"].get(npc_class)
    if not rule or not rule.get("prime") or scores is None:
        return 0.0
    return PRIME_BONUS if all(scores.get(a, 0) >= PRIME_AT for a in rule["prime"]) else 0.0


def exceptional(scores, npc_class, race):
    """A fighter with 18 Strength rolls d100 for exceptional Strength, up to their people's limit. Returns the scores."""
    if scores is None or npc_class not in table()["exceptional"] or scores["strength"] != HIGHEST or scores.get("strength_pct"):
        return scores
    cap = people(race).get("strength_pct", 0)
    return dict(scores, strength_pct=min(random.randint(1, 100), cap)) if cap else scores


def class_options(race, profession):
    """The classes a trade allows to someone of this people (ProfessionClasses.json, the people's own limits)."""
    from Data.NPCS.npc_generator import load_json, PROFESSION_CLASSES
    from Data.NPCS.races import level_limit
    entry = next((v for k, v in load_json(PROFESSION_CLASSES).items() if k.lower() == (profession or "").lower()), None)
    options = (entry or {}).get("classes") or ["None"]
    return [c for c in options if c == "None" or level_limit(race, c) != 0]


def choose_class(race, profession, traits=None, scores=None):
    """Of the classes the trade allows this person and their scores meet, one -- their personality tilting it.
    'None' if none fits."""
    from Data.NPCS.personality import calling
    fitting = [c for c in class_options(race, profession) if qualifies(scores, c)]
    if not fitting:
        return "None"
    weights = [1.0 if c == "None" else calling(traits, c, "class") for c in fitting]
    return random.choices(fitting, weights=weights)[0]


def person(race, profession, traits=None, preferred=None):
    """A new grown person's class and scores. If their trade offers a class, they roll as heroes (Method I) and
    take a class whose minimums their arranged rolls meet -- `preferred` if it is one, otherwise one tilted by
    their traits; one who takes none rolls again as ordinary folk. Returns (class, scores)."""
    from Data.NPCS.personality import calling
    options = class_options(race, profession)
    if not any(c != "None" for c in options):
        return "None", new_scores(race)
    rolls = heroic_rolls()
    met = {}
    for c in options:
        if c != "None":
            scores = arrange(race, rolls, c)
            if qualifies(scores, c):
                met[c] = scores
    if preferred in met:
        npc_class = preferred
    else:
        pool = list(met) + (["None"] if "None" in options else [])
        if not pool:
            return "None", new_scores(race)
        npc_class = random.choices(pool, weights=[1.0 if c == "None" else calling(traits, c, "class") for c in pool])[0]
    if npc_class == "None":
        return "None", new_scores(race)
    return npc_class, exceptional(met[npc_class], npc_class, race)


def describe(scores):
    """'Str 18/76 Int 9 Wis 12 Dex 14 Con 16 Cha 8' -- for the tools."""
    if not scores:
        return "no scores"
    s = scores["strength"]
    pct = scores.get("strength_pct") or 0
    strength = f"18/{'00' if pct >= 100 else f'{pct:02d}'}" if s == HIGHEST and pct else str(s)
    return (f"Str {strength} Int {scores['intelligence']} Wis {scores['wisdom']} Dex {scores['dexterity']} "
            f"Con {scores['constitution']} Cha {scores['charisma']}")