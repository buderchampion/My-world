"""
Magic, after the AD&D Player's Handbook. Spells.json holds the spell lists by
class and level and the spells a caster can cast each day.

Who casts
    Wizards, Sorcerers and Warlocks cast from the Wizard list, Clerics from the
    Cleric list, Druids and Bards from the Druid list. Their casting level
    comes from their magic skill (KEY_SKILL): every two skill levels make a
    casting level, up to MAX_CASTING_LEVEL. Magic is a trade that takes study:
    people of the magic trades (MAGIC_TRADES) cast at that level; anyone else
    with a casting class (a farmer who is a druid, an innkeeper who is a
    warlock) is a hedge caster, at half of it.
    Sorcerers cast by nature: one more spell of each level a day than a
    wizard, but they know only SORCERER_KINDS kinds of each level.

What magic does
    In fights   casters cast their day's spells instead of swinging a weapon:
                healing the worst wounded on their side, then their strongest
                harmful spell, then blessings, then guarding themselves
                (cast_in_battle, used by Data.Combat.melee.battle). Bards
                inspire their side with song: to hit and courage.
    In wars     the older battles count a caster's casting level as strength
                (Data.Combat.combat.get_combat_power).
    Healing     each day, the casters of a town spend their healing spells on
                its worst wounded (heal_the_wounded); on campaign, an army's
                own casters heal its wounded after a battle.
    Bards       a town with a bard is a merrier place (Data.NPCS.contentment).
    Druids      tend a village's fields: they yield more (Data.World.harvest), and
                beasts near a druid's home are calmer (Data.Monsters.travelers).
"""

from __future__ import annotations

import json
import math
import random
from functools import lru_cache
from pathlib import Path

SPELLS_FILE = Path(__file__).with_name("Spells.json")

# casting class -> (spell list, the skill its magic grows from)
CASTERS = {
    "Wizard":   ("Wizard", "Arcane Knowledge"),
    "Warlock":  ("Wizard", "Arcane Knowledge"),
    "Sorcerer": ("Wizard", "Fire Magic"),
    "Cleric":   ("Cleric", "Religion"),
    "Druid":    ("Druid", "Healing"),
    "Bard":     ("Druid", "Charisma"),
}
MAGIC_TRADES = {"Mage", "Priest", "Healer", "Scholar", "Alchemist"}
SKILL_LEVELS_PER_CASTING_LEVEL = 2
HEDGE_SHARE = 0.5                # a hedge caster casts at this share of their level
MAX_CASTING_LEVEL = 12
SORCERER_KINDS = 2               # kinds of spell a sorcerer knows of each level
HIT_DIE = {"Wizard": 2.5, "Cleric": 4.5, "Druid": 4.5}   # a caster's hit points a level, on average (d4, d8)
HEALTH_PER_HIT_POINT = 5         # a cure spell's hit points, as health (0-100): cure light wounds heals 5-40
INSPIRE_TO_HIT = 1               # a bard's song: their side hits more often...
INSPIRE_MORALE = 0.1             # ...and holds longer before breaking (master bards, level 7+, twice as much)
MASTER_BARD = 7
MAX_MORALE = 0.9

OFFENSIVE = ("slay", "slay_weak", "damage", "sleep", "hold", "rout", "calm", "weaken")


@lru_cache(maxsize=1)
def spells():
    with open(SPELLS_FILE, encoding="utf-8") as f:
        return json.load(f)


def spell_list(name):
    """{spell level: {spell name: spell}} of one list."""
    return {int(k): v for k, v in spells()[name].items() if not k.startswith("_")}


def casting_level(npc_class, profession, skills):
    """0 for someone who casts no spells."""
    caster = CASTERS.get(npc_class)
    if not caster:
        return 0
    skill = (skills or {}).get(caster[1]) or 0
    if isinstance(skill, dict):
        skill = skill.get("level", 0)
    if skill <= 0:
        return 0
    level = (skill + 1) // SKILL_LEVELS_PER_CASTING_LEVEL
    if profession not in MAGIC_TRADES:
        level = int(level * HEDGE_SHARE)
    return min(MAX_CASTING_LEVEL, level)


def per_day(npc_class, level):
    """Spells a day of each spell level: [first, second, ...]."""
    if level <= 0 or npc_class not in CASTERS:
        return []
    table = spells()["Spells per day"]["Bard" if npc_class == "Bard" else
                                       "Druid" if npc_class == "Druid" else
                                       "Cleric" if npc_class == "Cleric" else "Wizard"]
    slots = list(table[str(min(level, MAX_CASTING_LEVEL))])
    if npc_class == "Sorcerer":
        slots = [n + 1 for n in slots]
    return slots


def known(npc_class, ident=None):
    """{spell level: [spell names]} a caster of this class can cast. Sorcerers know a few, always the same ones."""
    book = spell_list(CASTERS[npc_class][0])
    if npc_class != "Sorcerer":
        return {lvl: list(v) for lvl, v in book.items()}
    rng = random.Random(ident)
    return {lvl: rng.sample(sorted(v), min(SORCERER_KINDS, len(v))) for lvl, v in book.items()}


def prepare(npc_class, level, ident=None, wanted=None):
    """The day's spells: [(spell level, name, spell)]. `wanted` (a set of effects) prefers those kinds:
    a caster heading into a fight prepares harm and healing, not plant growth."""
    slots = per_day(npc_class, level)
    if not slots:
        return []
    book = spell_list(CASTERS[npc_class][0])
    kinds = known(npc_class, ident)
    out = []
    for i, n in enumerate(slots):
        lvl = i + 1
        names = kinds.get(lvl) or []
        if wanted:
            names = [x for x in names if book[lvl][x]["effect"] in wanted] or names
        for _ in range(n):
            if names:
                name = random.choice(names)
                out.append((lvl, name, book[lvl][name]))
    return out


# ---------------------------------------------------------------- fights

def roll(lo, hi):
    return random.randint(min(lo, hi), max(lo, hi))


def saves(f):
    """A saving throw against a spell: d20 against a number by level or hit dice (as fighters)."""
    level = f.hit_dice or 0
    target = 19 if level < 1 else max(4, 17 - int((level - 1) // 2))
    return random.randint(1, 20) >= target


def _damage(spell, level):
    lo, hi = spell.get("dice", (0, 0))
    if spell.get("missiles_per_two_levels"):
        return None                                    # magic missile: handled one missile at a time
    if spell.get("flat_per_level"):
        return spell["flat_per_level"] * level
    if spell.get("per_level"):
        n = min(level, spell.get("max_level") or level)
        return sum(roll(lo, hi) + spell.get("bonus", 0) for _ in range(n))
    amount = roll(lo, hi) + spell.get("bonus", 0) + spell.get("bonus_per_level", 0) * level
    if spell.get("bonus_dice_per_level"):
        blo, bhi = spell["bonus_dice_per_level"]
        amount += sum(roll(blo, bhi) for _ in range(level))
    if spell.get("per_round"):
        amount += sum(roll(lo, hi) + spell.get("bonus", 0) for _ in range(spell.get("rounds", 1) - 1))
    return amount


def _eligible(spell, foes):
    """The foes this spell can touch."""
    out = [f for f in foes if f.alive]
    if spell.get("people_only"):
        out = [f for f in out if f.nature in ("person", "humanoid")]
    if spell.get("beasts_only") or spell["effect"] == "calm":
        out = [f for f in out if f.nature == "beast"]
    if spell.get("max_hit_dice_each"):
        out = [f for f in out if (f.hit_dice or 0) <= spell["max_hit_dice_each"]]
    if spell["effect"] in ("hold", "sleep"):
        out = [f for f in out if not f.held]
    if spell["effect"] in ("rout", "calm"):
        out = [f for f in out if not f.mindless]
    if spell["effect"] == "sleep":
        out = [f for f in out if not f.mindless]
    return out


def _useful(spell, caster, allies, foes, mine):
    effect = spell["effect"]
    if effect in OFFENSIVE:
        return bool(_eligible(spell, foes))
    if effect == "heal":
        return any(a.alive and a.hp < a.max_hp / 2 for a in allies)
    if effect == "bless":
        return spell_key(spell) not in mine["blessed"]
    if effect in ("shield", "weapon"):
        return not caster.guarded
    return False


def spell_key(spell):
    return json.dumps(spell, sort_keys=True)


def cast(caster, name, spell, allies, foes, mine, theirs):
    """Casts one spell. Returns a short note of what it did."""
    effect, level = spell["effect"], caster.caster_level
    if effect == "heal":
        hurt = min((a for a in allies if a.alive), key=lambda a: a.hp / a.max_hp)
        hurt.hp = min(hurt.max_hp, hurt.hp + roll(*spell["dice"]) + spell.get("bonus", 0))
        return f"{name} on {hurt.name}"
    if effect == "bless":
        mine["blessed"].add(spell_key(spell))
        for a in allies:
            a.to_hit += spell.get("to_hit", 0)
        for f in foes:
            f.to_hit -= spell.get("enemy_penalty", 0)
        mine["morale"] = min(MAX_MORALE, mine["morale"] + spell.get("morale", 0))
        return name
    if effect == "shield":
        caster.guarded = True
        caster.ac = min(caster.ac, spell["armor_class"]) if "armor_class" in spell else caster.ac - spell.get("armor_class_bonus", 1)
        return name
    if effect == "weapon":
        caster.guarded = True
        caster.attacks = [tuple(spell["dice"])]
        return name

    targets = _eligible(spell, foes)
    random.shuffle(targets)
    if effect == "damage":
        if spell.get("missiles_per_two_levels"):
            for _ in range(min(spell["max_missiles"], (level + 1) // 2)):
                live = [f for f in targets if f.alive]
                if not live:
                    break
                random.choice(live).hp -= roll(*spell["dice"]) + spell.get("bonus", 0)
            return name
        for f in targets[:spell.get("targets", 1)]:
            amount = _damage(spell, level)
            if spell.get("save_half") and saves(f):
                amount = amount / 2
            f.hp -= amount
        return name
    if effect in ("sleep", "slay_weak"):
        budget = roll(*spell["hit_dice"])
        for f in sorted(targets, key=lambda f: f.hit_dice or 0):
            cost = max(1, math.ceil(f.hit_dice or 0))
            if cost > budget:
                break
            budget -= cost
            if effect == "sleep":
                f.held = 99
            else:
                f.hp = 0
        return name
    if effect == "hold":
        for f in targets[:spell.get("targets", 1)]:
            if not saves(f):
                f.held = spell.get("rounds", 1)
        return name
    if effect in ("rout", "calm"):
        for f in targets[:spell.get("targets", 1)]:
            if not spell.get("save", effect == "calm") or not saves(f):
                f.out = True
        return name
    if effect == "slay":
        for f in targets[:spell.get("targets", 1)]:
            if not saves(f):
                f.hp = 0
        return name
    if effect == "weaken":
        for f in targets[:spell.get("targets", 1)]:
            if spell.get("damage_penalty"):
                if not saves(f):
                    f.damage_scale *= 1 - spell["damage_penalty"]
            else:
                f.ac += spell.get("armor_class", 0)
        return name
    return name


def cast_in_battle(caster, allies, foes, mine, theirs):
    """A caster's turn: heal, harm, bless, guard -- the first that helps, strongest spell first. False if nothing to cast."""
    if not caster.spells:
        return False
    order = (("heal",), OFFENSIVE, ("bless",), ("shield", "weapon"))
    for group in order:
        options = [(lvl, name, sp) for lvl, name, sp in caster.spells if sp["effect"] in group]
        for lvl, name, sp in sorted(options, key=lambda s: -s[0]):
            if _useful(sp, caster, allies, foes, mine):
                caster.spells.remove((lvl, name, sp))
                cast(caster, name, sp, allies, foes, mine, theirs)
                caster.cast += 1
                return True
    return False


def inspire(side, state):
    """Bards sing their side into battle: the best bard's song counts."""
    best = max((f.inspires for f in side), default=0)
    if best:
        for f in side:
            f.to_hit += best * INSPIRE_TO_HIT
        state["morale"] = min(MAX_MORALE, state["morale"] + best * INSPIRE_MORALE)
    return best


def inspiration(npc_class, level):
    """How much a bard's song lifts a side: 0, 1 or (a master) 2."""
    if npc_class != "Bard" or level <= 0:
        return 0
    return 2 if level >= MASTER_BARD else 1


BATTLE_SPELLS = set(OFFENSIVE) | {"heal", "bless", "shield", "weapon"}


def arm_caster(fighter, npc_class, level, ident=None):
    """Gives a fighter their casting: the day's battle spells, a caster's hit points, a bard's song."""
    fighter.caster_level = level
    if level <= 0:
        return fighter
    fighter.spells = prepare(npc_class, level, ident, wanted=BATTLE_SPELLS)
    fighter.inspires = inspiration(npc_class, level)
    hp = HIT_DIE.get(CASTERS[npc_class][0], 2.5) * level
    if hp > fighter.hp:
        fighter.hp = fighter.max_hp = hp
    fighter.hit_dice = max(fighter.hit_dice or 0, level)
    return fighter


# ---------------------------------------------------------------- the world

def casters(conn, world_id, classes=None, city_ids=None):
    """Every living caster in the world: [{"id", "name", "class", "profession", "city_id", "health", "level"}]."""
    classes = list(classes or CASTERS)
    marks = ",".join("?" * len(classes))
    rows = conn.execute(
        f"""SELECT n.id, n.name, n.class, n.profession, n.city_id, n.health, s.skill_name, s.level
            FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
            JOIN npc_skills s ON s.npc_id = n.id
            WHERE k.world_id = ? AND n.alive = 1 AND n.age >= 16 AND n.class IN ({marks})""",
        (world_id, *classes)).fetchall()
    found = {}
    for r in rows:
        if r["skill_name"] != CASTERS[r["class"]][1]:
            continue
        level = casting_level(r["class"], r["profession"], {r["skill_name"]: r["level"]})
        if level > 0 and (city_ids is None or r["city_id"] in city_ids):
            found[r["id"]] = {"id": r["id"], "name": r["name"], "class": r["class"], "profession": r["profession"],
                              "city_id": r["city_id"], "health": r["health"], "level": level}
    return list(found.values())


def healing_spells(npc_class, level, ident=None):
    """The cure spells a caster has today, as (low, high, bonus)."""
    return [(sp["dice"][0], sp["dice"][1], sp.get("bonus", 0))
            for _, _, sp in prepare(npc_class, level, ident, wanted={"heal"}) if sp["effect"] == "heal"]


def heal(conn, healers, wounded_ids):
    """`healers` spend their cure spells on the worst of `wounded_ids`. Returns the health restored."""
    if not healers or not wounded_ids:
        return 0
    marks = ",".join("?" * len(wounded_ids))
    hurt = {r[0]: r[1] for r in conn.execute(
        f"SELECT id, health FROM npcs WHERE alive = 1 AND health < 100 AND id IN ({marks})", list(wounded_ids))}
    restored = 0
    for h in healers:
        for lo, hi, bonus in healing_spells(h["class"], h["level"], h["id"]):
            if not hurt:
                break
            worst = min(hurt, key=hurt.get)
            gain = min(100 - hurt[worst], (roll(lo, hi) + bonus) * HEALTH_PER_HIT_POINT)
            hurt[worst] += gain
            restored += gain
            if hurt[worst] >= 100:
                conn.execute("UPDATE npcs SET health = 100 WHERE id = ?", (worst,))
                del hurt[worst]
            else:
                conn.execute("UPDATE npcs SET health = ? WHERE id = ?", (round(hurt[worst], 2), worst))
    return restored


def heal_the_wounded(conn, world_id):
    """Daily: each town's casters heal its worst wounded with their cure spells. Returns {city id: health restored}."""
    from Data.Infrastructure.travel import get_traveling_npc_ids
    from Data.Government.armies import get_army_npc_ids
    away = set(get_traveling_npc_ids(conn, world_id)) | set(get_army_npc_ids(conn, world_id))
    healers = {}
    for c in casters(conn, world_id, ("Cleric", "Druid", "Bard")):
        if c["id"] not in away:
            healers.setdefault(c["city_id"], []).append(c)
    if not healers:
        return {}
    marks = ",".join("?" * len(healers))
    wounded = {}
    for r in conn.execute(f"SELECT id, city_id FROM npcs WHERE alive = 1 AND health < 100 AND city_id IN ({marks})", list(healers)):
        if r[0] not in away:
            wounded.setdefault(r[1], []).append(r[0])
    done = {}
    for city_id, ids in wounded.items():
        done[city_id] = heal(conn, sorted(healers[city_id], key=lambda h: -h["level"]), ids)
    conn.commit()
    return done


def bard_towns(conn, world_id):
    """Towns with a bard who sings: {city id: the best bard's inspiration}."""
    out = {}
    for c in casters(conn, world_id, ("Bard",)):
        out[c["city_id"]] = max(out.get(c["city_id"], 0), inspiration("Bard", c["level"]))
    return out


PLANT_GROWTH_LEVEL = 3           # a druid of this level can cast Plant Growth (a third-level spell)
TENDED_BY_LEVEL = 4              # a druid tends this many households' fields for each casting level...
TENDED_HARVEST = 0.05            # ...which yield this much more...
PLANT_GROWTH_HARVEST = 0.10      # ...or this much where Plant Growth is cast on them (never more, however many druids)


def druid_harvest_bonus(conn, world_id):
    """{village id: the share more its fields yield} where druids tend the land: each druid tends some
    households' fields, so a village's bonus is the share of its fields they reach."""
    households = {r[0]: r[1] for r in conn.execute(
        """SELECT c.id, c.households FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND c.households > 0""", (world_id,))}
    gain = {}
    for c in casters(conn, world_id, ("Druid",)):
        if c["city_id"] in households:
            rate = PLANT_GROWTH_HARVEST if c["level"] >= PLANT_GROWTH_LEVEL else TENDED_HARVEST
            gain[c["city_id"]] = gain.get(c["city_id"], 0) + TENDED_BY_LEVEL * c["level"] * rate
    return {v: min(PLANT_GROWTH_HARVEST, g / households[v]) for v, g in gain.items()}


CALM_REACH = 300                 # beasts within this many map units of a druid's home...
CALM_SHARE = 0.5                 # ...attack travelers this much as often


def druid_homes(conn, world_id):
    """Map positions of the towns and villages where a druid lives."""
    ids = {c["city_id"] for c in casters(conn, world_id, ("Druid",))}
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return [(r[0], r[1]) for r in conn.execute(f"SELECT map_x, map_y FROM cities WHERE id IN ({marks})", list(ids))
            if r[0] is not None]


def calmed(lair, homes):
    return any((lair["map_x"] - x) ** 2 + (lair["map_y"] - y) ** 2 <= CALM_REACH ** 2 for x, y in homes)