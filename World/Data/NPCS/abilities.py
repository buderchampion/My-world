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


# ---------------------------------------------------------------- what the scores do (the book's tables)

def _band(value, table):
    """The entry of a [(highest score, value), ...] table for this score."""
    for top, v in table:
        if value <= top:
            return v
    return table[-1][1]


STRENGTH_TABLE = [(3, (-3, -1)), (5, (-2, -1)), (7, (-1, 0)), (15, (0, 0)), (16, (0, 1)), (17, (1, 1)), (18, (1, 2))]
EXCEPTIONAL_TABLE = [(50, (1, 3)), (75, (2, 3)), (90, (2, 4)), (99, (2, 5)), (100, (3, 6))]
DEXTERITY_AC = [(3, 4), (4, 3), (5, 2), (6, 1), (14, 0), (15, -1), (16, -2), (17, -3), (19, -4)]
CONSTITUTION_HP = [(3, -2), (6, -1), (14, 0), (15, 1), (16, 2), (19, 2)]
FIGHTER_CONSTITUTION_HP = {17: 3, 18: 4, 19: 5}            # fighters alone gain more at 17 and up
WISDOM_SAVE = [(3, -3), (4, -2), (7, -1), (14, 0), (15, 1), (16, 2), (17, 3), (19, 4)]
AGING = [(41, {"strength": -1, "constitution": -1, "intelligence": 1, "wisdom": 1}),               # middle age
         (61, {"strength": -2, "dexterity": -2, "constitution": -1, "wisdom": 1}),                 # old
         (91, {"strength": -1, "dexterity": -1, "constitution": -1, "intelligence": 1, "wisdom": 1})]  # venerable
SICKNESS_PER_POINT = 0.10      # each point of Constitution's hit-point bonus takes this share off a sickness's daily harm
RESEARCH_PER_POINT = 0.05      # each point of Intelligence above 10 adds this to a scholar's research (below 10, takes it)


def strength_bonus(scores):
    """(to hit, damage) for this Strength (exceptional Strength for those who have it)."""
    if not scores:
        return 0, 0
    if scores["strength"] >= HIGHEST and scores.get("strength_pct"):
        return _band(scores["strength_pct"], EXCEPTIONAL_TABLE)
    return _band(scores["strength"], STRENGTH_TABLE)


def dexterity_ac(scores):
    """Armor class adjustment (negative is better)."""
    return _band(scores["dexterity"], DEXTERITY_AC) if scores else 0


def constitution_hp(scores, npc_class=None):
    """Hit points added (or taken) per level."""
    if not scores:
        return 0
    con = scores["constitution"]
    if npc_class in table()["exceptional"] and con in FIGHTER_CONSTITUTION_HP:
        return FIGHTER_CONSTITUTION_HP[con]
    return _band(con, CONSTITUTION_HP)


def wisdom_save(scores):
    """Saving throw adjustment against charm, fear and other attacks on the mind."""
    return _band(scores["wisdom"], WISDOM_SAVE) if scores else 0


def sickness_factor(scores):
    """How much of a sickness's daily harm this Constitution takes (Con 16: 0.8, Con 3: 1.2)."""
    return 1.0 - SICKNESS_PER_POINT * constitution_hp(scores) if scores else 1.0


def research_factor(scores):
    """A scholar's research by Intelligence (Int 18: 1.4, Int 10: 1.0, Int 6: 0.8)."""
    return max(0.5, 1.0 + RESEARCH_PER_POINT * (scores["intelligence"] - 10)) if scores else 1.0


def bonus_spells(scores, npc_class):
    """A cleric's or druid's extra first- and second-level spells a day for high Wisdom (the book: 13 and 14 give
    first-level spells, 15 and 16 second-level). Returns [extra first, extra second]."""
    if not scores or npc_class not in ("Cleric", "Druid"):
        return [0, 0]
    wis = scores["wisdom"]
    return [(wis >= 13) + (wis >= 14), (wis >= 15) + (wis >= 16)]


# ---------------------------------------------------------------- scores that change: training, deeds, hardship, age

TRAIN_UNTIL = 25               # the young train: life stage 16 to this
TRAIN_FROM = 16
TRAIN_CHANCE = 0.12            # a year, for someone of middling diligence (doubled for the most diligent, none for the idlest;
                               # half again for the boldest)
TRAIN_CAP = 2                  # training raises any one score at most this much in a lifetime
DEED_CHANCE = 0.03             # surviving a fight: this often +1 Strength or Constitution...
HARDSHIP_CHANCE = 0.05         # ...pulling through a sickness: this often +1 Constitution
DEED_CAP = 1                   # deeds and hardship raise any one score at most this much in a lifetime
TRADE_SCORE = {                # the score a trade trains
    "Soldier": "strength", "Guard": "strength", "Captain": "strength", "Knight": "strength", "Blacksmith": "strength",
    "Armorer": "strength", "Miner": "strength", "Quarrier": "strength", "Smelter": "strength", "Farmer": "strength",
    "Carpenter": "strength", "Baker": "constitution", "Miller": "constitution",
    "Thief": "dexterity", "Smuggler": "dexterity", "Hunter": "dexterity", "Fisherman": "dexterity", "Tailor": "dexterity",
    "Scholar": "intelligence", "Mage": "intelligence", "Alchemist": "intelligence",
    "Priest": "wisdom", "Healer": "wisdom",
    "Merchant": "charisma", "innkeeper": "charisma", "Bandit": "strength",
}


def _gains(row):
    try:
        return json.loads(row["ability_gains"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


def grow(conn, npc_id, ability, how="train"):
    """+1 to a score -- by training (how 'train', at most TRAIN_CAP a lifetime) or by deeds and hardship ('deed',
    at most DEED_CAP) -- never past their people's maximum. Returns True if it rose."""
    row = conn.execute(f"SELECT race, {ability}, ability_gains FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not row or row[ability] is None:
        return False
    gains = _gains(row)
    key = f"{how}:{ability}"
    if gains.get(key, 0) >= (TRAIN_CAP if how == "train" else DEED_CAP) or row[ability] >= limits(row["race"], ability)[1]:
        return False
    gains[key] = gains.get(key, 0) + 1
    conn.execute(f"UPDATE npcs SET {ability} = {ability} + 1, ability_gains = ? WHERE id = ?", (json.dumps(gains), npc_id))
    return True


def deeds(conn, fighters):
    """After a fight: each named survivor may come out of it stronger or hardier."""
    for f in fighters:
        if f.ref is None or not getattr(f, "race", "") or f.hp <= 0:
            continue
        if random.random() < DEED_CHANCE:
            grow(conn, f.ref, random.choice(("strength", "constitution")), "deed")


def hardship(conn, npc_id):
    if random.random() < HARDSHIP_CHANCE:
        grow(conn, npc_id, "constitution", "deed")


def trains(row):
    """The score this person's trade (or, for an adventurer, their class's first prime requisite) trains."""
    if row["profession"] == "Adventurer":
        prime = (table()["classes"].get(row["class"]) or {}).get("prime") or ["strength"]
        return prime[0]
    return TRADE_SCORE.get(row["profession"])


def age_shift(conn, row):
    """The book's aging: on reaching middle age, old age and venerable age, scores shift (within their people's
    limits). Returns how many stages were applied."""
    reached = sum(1 for at, _ in AGING if row["age"] >= at)
    done = row["ability_age"] or 0
    if reached <= done or row["strength"] is None:
        return 0
    scores = {a: row[a] for a in ABILITIES}
    for _, shift in AGING[done:reached]:
        for a, d in shift.items():
            scores[a] += d
    scores = fit(row["race"], scores)
    pct = row["strength_pct"] if scores["strength"] >= HIGHEST else 0
    conn.execute(f"UPDATE npcs SET {', '.join(a + ' = ?' for a in ABILITIES)}, strength_pct = ?, ability_age = ? WHERE id = ?",
                 (*[scores[a] for a in ABILITIES], pct, reached, row["id"]))
    return reached - done


def abilities_year(conn, world_id):
    """Yearly: the young train (by diligence); the aging feel their years. Returns {"trained", "aged"}."""
    trained = aged = 0
    for row in conn.execute(
            """SELECT n.* FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND n.alive = 1 AND n.strength IS NOT NULL""", (world_id,)).fetchall():
        aged += age_shift(conn, row) > 0
        if TRAIN_FROM <= row["age"] <= TRAIN_UNTIL and row["jailed_until"] is None:
            ability = trains(row)
            drive = (1 + (row["diligence"] or 0) / 100) * (1 + max(0, row["boldness"] or 0) / 200)   # the diligent and bold train harder
            if ability and random.random() < TRAIN_CHANCE * drive and grow(conn, row["id"], ability):
                trained += 1
    conn.commit()
    return {"trained": trained, "aged": aged}
