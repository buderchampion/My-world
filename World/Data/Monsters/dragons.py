"""
Dragons: each dragon lair's eldest is a named dragon with a hoard.

Evil dragons    (black, blue, green, red, white) sleep on their hoards and do
(rampages)      not raid like orcs. Each year one may wake (WAKE_CHANCE, more
                the smaller its hoard) and fly against a town or village
                within RAMPAGE_REACH -- the richest it can see, nearer ones
                likelier. The townsfolk fight it by the book with everything
                they have (Data.Combat.melee and its special attacks: a red
                dragon's fire is its own hit points) for RAMPAGE_ROUNDS; then
                the dragon flies home -- unless slain, or hurt below
                DRIVEN_OFF of its hit points (driven off: it takes nothing,
                and less of the town burns). If the dragon wins, it
                burns a share of the town, kills, and carries off TAKEN_GOLD of
                the town's purse and TAKEN_FOOD of its stores to its hoard; the
                town is afraid. If it falls, its slayer is the Dragonslayer and
                the town takes the hoard (TOWN_SHARE, the rest to the crown).

Dragon hunts    each year a kingdom a dragon has burned in the last
                HUNT_MEMORY years (or, now and then, one whose ruler is greedy,
                HUNT_GREED, and a hoard is near) may send its best: knights,
                captains, soldiers and casters (HUNT_BAND of them) to the lair,
                to fight every dragon there. If they win, the crown takes
                HUNT_CROWN_SHARE of the hoard and the hunters share the rest;
                the slayer is the Dragonslayer. Either way it is chronicled.

Good dragons    (brass, bronze, copper, gold, silver) keep to their lairs,
                proud and old, and help mortals on their own terms (KINDS):
                  - when an evil dragon falls on a town within GUARD_REACH,
                    each may come to fight it (gold and silver most often,
                    brass seldom); a good dragon that slays an evil one
                    claims its hoard, and gives a share to the town
                    (GIFT_SHARE: generous gold and silver, little from brass);
                  - silver and gold dragons may take mortal form and live in
                    a town near their lair (GUISE_CHANCE). When that town is
                    attacked -- by a dragon or by raiders -- the stranger
                    throws off the disguise and fights as a dragon;
                  - bronze dragons hate tyranny: when an empire or a horde
                    assails a town within GUARD_REACH, a bronze dragon may
                    join its defence (BRONZE_AID times the defenders' strength,
                    Data.Government.campaigns);
                  - copper dragons play pranks and pose riddles on the
                    nearest town (a little mirth, now and then a little
                    gold gone missing); brass dragons love talk and news, and
                    gossiping between kingdoms warms them toward each other.
                No one hunts a good dragon unless their ruler is greedy
                beyond shame (GREEDY_HUNTS_GOOD).
"""

from __future__ import annotations

import math
import random

WAKE_CHANCE = 0.2              # a year, for an evil dragon with a middling hoard (more if poor, less if rich)
RICH_HOARD = 60000             # a hoard this big makes a dragon sleep half as often; half this, wake half again as often
RAMPAGE_REACH = 1600
RAMPAGE_ROUNDS = 4             # a rampage lasts this many rounds: the dragon burns, wheels, and flies home (long_run.py: fought to
                               # the death, dragons fell to every town's casters -- three of three in five years)
DRIVEN_OFF = 0.3               # a dragon brought this low flees with nothing
SWORDS_FEARED = 5              # a town's worth to a dragon falls by half for every this many soldiers, guards and casters in it
TAKEN_GOLD = 0.4               # of the town's purse, if the dragon wins
TAKEN_FOOD = 0.15              # of its stores (cattle and grain)
BURNED = (0.15, 0.45)          # the share of the town's buildings it burns...
BURN_DAMAGE = (30, 90)         # ...and how much condition each loses
TOWN_DEFENDERS = 40            # at most this many townsfolk stand beside the watch and the soldiers
TOWN_SHARE = 0.5               # of a slain dragon's hoard, to the town that slew it (the rest to the crown)
HOARD = {"Dragon, White": (5000, 20000), "Dragon, Black": (8000, 30000), "Dragon, Green": (10000, 40000),
         "Blue Dragon (Draco Electricus)": (15000, 50000), "Dragon, Red": (25000, 80000),
         "Dragon, Brass": (5000, 25000), "Dragon, Copper": (8000, 30000), "Dragon, Bronze": (12000, 45000),
         "Dragon, Silver": (20000, 60000), "Dragon, Gold": (30000, 90000)}
HP_PER_DIE = 8                 # a named dragon is ancient: the book's greatest, 8 hit points a die...
ELDER_DICE = 2                 # ...and the most dice its kind can have (the book gives a range; the hoard-keepers are the largest)
ENGAGED = 6                    # a dragon on the wing: at most this many can strike at it in a round

HUNT_MEMORY = 3                # years a kingdom remembers a dragon burning its towns
HUNT_CHANCE = 0.5              # a year, for a kingdom with a dragon to avenge
HUNT_GREED = 0.05              # a year, for a greedy ruler (generosity below GREEDY) with a hoard within HUNT_REACH
GREEDY = -40
GREEDY_HUNTS_GOOD = -80        # only a ruler this greedy sends hunters against a good dragon
HUNT_REACH = 2500
HUNT_BAND = 12
HUNT_MIN = 4
HUNT_CROWN_SHARE = 0.6
SLEEPING_ROUNDS = 2
DRAGONSLAYER_RENOWN = 20

NEW_DRAGON_CHANCE = 0.15       # a year, while fewer than MAX_EVIL evil dragons live: one comes from far away to den
MAX_EVIL = 3
NEW_DRAGON_CLEARANCE = 400     # ...in a wild region of its kind's land, at least this far from any settlement

GUARD_REACH = 1000
GIFT_SHARE = {"Dragon, Gold": 0.5, "Dragon, Silver": 0.5, "Dragon, Bronze": 0.3, "Dragon, Copper": 0.2, "Dragon, Brass": 0.1}
GUISE_REACH = 900
BRONZE_AID = 1.5
PRANK_CHANCE = 0.3             # a year, a copper dragon's pranks on its nearest town
PRANK_GOLD = 0.02              # ...now and then this share of the town's purse goes missing (and turns up in the hoard)
GOSSIP_CHANCE = 0.4            # a year, a brass dragon's talk warms two kingdoms toward each other...
GOSSIP_FAVOR = 3.0             # ...by this much each way

# What sets each kind apart: good or evil; the colour it is named by; how often it comes to a town's aid
# against an evil dragon; how often it takes mortal form (and the trade it takes up); and how often hunters find it
# asleep on its hoard (the book's own chance): they get SLEEPING_ROUNDS of blows before it wakes.
KINDS = {
    "Dragon, Black": {"good": False, "colour": "Black", "asleep": 0.4},
    "Blue Dragon (Draco Electricus)": {"good": False, "colour": "Blue", "asleep": 0.2},
    "Dragon, Green": {"good": False, "colour": "Green", "asleep": 0.3},
    "Dragon, Red": {"good": False, "colour": "Red", "asleep": 0.2},
    "Dragon, White": {"good": False, "colour": "White", "asleep": 0.5},
    "Dragon, Gold": {"good": True, "colour": "Gold", "asleep": 0.1, "guard": 0.6, "guise": 0.25, "trade": "Scholar"},
    "Dragon, Silver": {"good": True, "colour": "Silver", "asleep": 0.15, "guard": 0.6, "guise": 0.5, "trade": "Healer"},
    "Dragon, Bronze": {"good": True, "colour": "Bronze", "asleep": 0.25, "guard": 0.4},
    "Dragon, Copper": {"good": True, "colour": "Copper", "asleep": 0.4, "guard": 0.25},
    "Dragon, Brass": {"good": True, "colour": "Brass", "asleep": 0.5, "guard": 0.15},
}

FIRST = ["Vor", "Ash", "Kal", "Thra", "Ign", "Sarn", "Mor", "Zyr", "Fal", "Gor", "Vel", "Aur", "Arg", "Bryn", "Cyr", "Sil",
         "Ery", "Nyx", "Ral", "Ser", "Tiam", "Ul", "Xan", "Hal", "Dra", "Ost", "Pyr", "Quel"]
MIDDLE = ["a", "e", "i", "o", "u", "y", "ae", "or", "an", "ith"]
LAST = ["thax", "rax", "goth", "vyre", "mira", "drath", "zhar", "lyss", "noth", "garn", "vess", "ryn", "lagon", "shar", "dorn",
        "ix", "ammon", "thys"]


# ---------------------------------------------------------------- the dragons of the world

def is_dragon(monster_name):
    return monster_name in KINDS


def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def dragon_name(taken=()):
    for _ in range(50):
        name = random.choice(FIRST) + random.choice(MIDDLE) + random.choice(LAST)
        if name not in taken:
            return name
    return name


def full_name(d):
    return f"{d['name']} the {KINDS[d['monster']]['colour']}"


def ensure_dragons(conn, world_id):
    """Every living dragon lair gets its named dragon (older worlds too). Returns how many were named."""
    taken = {r[0] for r in conn.execute("SELECT name FROM dragons WHERE world_id = ?", (world_id,))}
    marks = ",".join("?" * len(KINDS))
    named = 0
    for lair in conn.execute(
            f"""SELECT * FROM monster_lairs l WHERE world_id = ? AND status = 'Active' AND count > 0 AND monster IN ({marks})
                AND NOT EXISTS (SELECT 1 FROM dragons d WHERE d.lair_id = l.id)""", (world_id, *KINDS)).fetchall():
        name = dragon_name(taken)
        taken.add(name)
        conn.execute("""INSERT INTO dragons (world_id, lair_id, name, monster, good, hoard, status)
                        VALUES (?, ?, ?, ?, ?, ?, 'Alive')""",
                     (world_id, lair["id"], name, lair["monster"], int(KINDS[lair["monster"]]["good"]),
                      round(random.uniform(*HOARD[lair["monster"]])), ))
        named += 1
    return named


def dragons(conn, world_id, good=None):
    rows = conn.execute(
        """SELECT d.*, l.map_x, l.map_y, l.count, l.hit_points_each FROM dragons d JOIN monster_lairs l ON l.id = d.lair_id
           WHERE d.world_id = ? AND d.status = 'Alive' AND l.status = 'Active' AND l.count > 0""", (world_id,)).fetchall()
    out = [dict(r) for r in rows]
    return out if good is None else [d for d in out if bool(d["good"]) == good]


def fighter_of(d, side):
    """The named dragon itself, as a fighter: ancient and great (ELDER_DICE more dice, HP_PER_DIE a die), with its
    breath (as strong as its hit points) and spells."""
    from Data.Combat import melee
    from Data.Monsters.bestiary import by_name
    monster = by_name(d["monster"])
    hp = ((monster["stats"].get("hit_dice") or 8) + ELDER_DICE) * HP_PER_DIE
    f = melee.monster_fighters(monster, 1, hp, side=side, ref=None)[0]
    f.name = d["monster"]
    f.fx["dragon"] = d["id"]
    f.max_engaged = ENGAGED
    return f


def _slain(conn, d, day, slayer_text):
    conn.execute("UPDATE dragons SET status = 'Slain', slain_day = ?, slain_by = ?, hoard = 0 WHERE id = ?",
                 (day, slayer_text, d["id"]))
    conn.execute("UPDATE monster_lairs SET count = MAX(0, count - 1) WHERE id = ?", (d["lair_id"],))
    conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE id = ? AND count <= 0", (d["lair_id"],))
    if d.get("guise_npc_id"):
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (d["guise_npc_id"],))


def _chronicle(conn, world_id, kingdom_id, key, fields, seed, event_id=None):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    if kingdom:
        write_entry(conn, kingdom_id, "Dragon", key, year, season, dict(fields, kingdom=kingdom[0]),
                    source_event_id=event_id if isinstance(event_id, int) else None, seed_parts=seed)


def _event(conn, kind, text, city_id=None, kingdom_id=None):
    from Data.Database.repository import create_event
    event = create_event(conn, kind, text, city_id=city_id, kingdom_id=kingdom_id)
    return event["id"] if isinstance(event, dict) else event


def _slayer(conn, survivors):
    """The surviving named fighter who struck hardest (the strongest, by level): the Dragonslayer. None if none lived."""
    named = [f for f in survivors if f.ref is not None and getattr(f, "race", "")]
    if not named:
        return None
    best = max(named, key=lambda f: (f.hit_dice or 0) + random.random())
    from Data.Combat.nemesis import grant_epithet
    conn.execute("UPDATE npcs SET renown = COALESCE(renown, 0) + ? WHERE id = ?", (DRAGONSLAYER_RENOWN, best.ref))
    grant_epithet(conn, best.ref, "the Dragonslayer")
    return best.ref


def _bury(conn, fallen, city_id, how):
    """Named fighters who fell: dead, recorded (how, if a special attack said), mourned."""
    from Data.Database.repository import get_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    dead = 0
    for f in fallen:
        if f.ref is None or not getattr(f, "race", ""):
            continue
        npc = get_npc(conn, f.ref)
        if not npc or not npc["alive"]:
            continue
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (f.ref,))
        npc = get_npc(conn, f.ref)
        death_event(conn, npc, f.cause or how, city_id=city_id)
        apply_grief(conn, npc)
        dead += 1
    return dead


# ---------------------------------------------------------------- who defends a town

def town_defenders(conn, world_id, city):
    """Everyone who stands against a dragon: the town's soldiers and watch (those at home), its casters, up to
    TOWN_DEFENDERS other grown townsfolk, and a village's household militia."""
    from Data.Combat import melee
    from Data.Government.armies import get_army_npc_ids
    from Data.Infrastructure.travel import get_traveling_npc_ids
    away = set(get_army_npc_ids(conn, world_id)) | set(get_traveling_npc_ids(conn, world_id))
    people = [dict(r) for r in conn.execute(
        """SELECT * FROM npcs WHERE city_id = ? AND alive = 1 AND age >= 16 AND jailed_until IS NULL
           ORDER BY CASE WHEN profession IN ('Knight', 'Captain', 'Soldier', 'Guard', 'Mage', 'Priest', 'Healer') THEN 0 ELSE 1 END,
                    RANDOM()""", (city["id"],))]
    fighters, others = [], 0
    for npc in people:
        if npc["id"] in away:
            continue
        sworn = npc["profession"] in ("Knight", "Captain", "Soldier", "Guard", "Mage", "Priest", "Healer")
        if not sworn:
            if others >= TOWN_DEFENDERS:
                continue
            others += 1
        skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
        fighters.append(melee.person_fighter(npc, side="town", skills=skills))
    if city["type"] == "Village":
        from Data.Monsters.raids import MAX_MILITIA
        militia = min(MAX_MILITIA, int(city["households"] or 0))
        fighters += [melee.Fighter("militia", "town", melee.HP_COMMONER, melee.COMMONER[1], melee.person_thac0(0),
                                   [melee.COMMONER[2]], hit_dice=0) for _ in range(militia)]      # untrained: a dragon's awe breaks them
    return fighters


def guise_in(conn, world_id, city_id):
    """A good dragon living in this town in mortal form, or None."""
    row = conn.execute("""SELECT d.id FROM dragons d JOIN npcs n ON n.id = d.guise_npc_id
                          WHERE d.world_id = ? AND d.status = 'Alive' AND n.alive = 1 AND n.city_id = ?""",
                       (world_id, city_id)).fetchone()
    return next((d for d in dragons(conn, world_id, good=True) if row and d["id"] == row[0]), None)


def reveal(conn, world_id, d, city, side):
    """The stranger throws off the disguise: the dragon fights for the town. Returns its fighter."""
    npc = conn.execute("SELECT name FROM npcs WHERE id = ?", (d["guise_npc_id"],)).fetchone()
    _event(conn, "Dragon", f"{npc[0] if npc else 'A stranger'} of {city['name']} threw off a mortal guise: "
                           f"{full_name(d)} rose to defend the town.", city_id=city["id"], kingdom_id=city["kingdom_id"])
    _chronicle(conn, world_id, city["kingdom_id"], "Dragon Revealed",
               {"city": city["name"], "dragon": full_name(d), "slayer": npc[0] if npc else "a stranger"}, (d["id"], city["id"]))
    return fighter_of(d, side)


def guardians(conn, world_id, city, evil=None):
    """Good dragons near the town who come to fight the evil one (each by its nature). Returns [(dragon, fighter)]."""
    out = []
    for d in dragons(conn, world_id, good=True):
        if math.dist((d["map_x"], d["map_y"]), (city["map_x"], city["map_y"])) > GUARD_REACH:
            continue
        if random.random() < KINDS[d["monster"]].get("guard", 0):
            out.append((d, fighter_of(d, "town")))
    return out


def new_dragon(conn, world_id, monster_name=None):
    """An evil dragon comes from far away and dens in the wilds of its kind's land. Returns the dragon, or None."""
    from Data.Monsters.bestiary import by_name
    from Data.Monsters.lairs import hit_points
    kinds = [monster_name] if monster_name else [k for k, v in KINDS.items() if not v["good"]]
    random.shuffle(kinds)
    towns = conn.execute("""SELECT c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                            WHERE k.world_id = ?""", (world_id,)).fetchall()
    taken = {r[0] for r in conn.execute("SELECT region_id FROM monster_lairs WHERE world_id = ? AND status = 'Active'", (world_id,))}
    for name in kinds:
        monster = by_name(name)
        regions = [dict(r) for r in conn.execute("SELECT * FROM regions WHERE world_id = ?", (world_id,))
                   if r["terrain"] in monster["terrains"] and r["id"] not in taken
                   and not (monster["cold_only"] and r["climate"] != "Cold")]
        regions = [r for r in regions if all(math.dist((t[0], t[1]), (r["center_x"], r["center_y"])) >= NEW_DRAGON_CLEARANCE
                                             for t in towns)]
        if not regions:
            continue
        region = random.choice(regions)
        cursor = conn.execute(
            """INSERT INTO monster_lairs (world_id, region_id, monster, kind, map_x, map_y, count, hit_points_each, founded_day, status)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 'Active')""",
            (world_id, region["id"], name, monster["kind"], region["center_x"], region["center_y"], hit_points(monster),
             _today(conn, world_id)))
        ensure_dragons(conn, world_id)
        d = next(x for x in dragons(conn, world_id) if x["lair_id"] == cursor.lastrowid)
        _event(conn, "Dragon", f"{full_name(d)} came out of the far places of the world and made its lair in the wilds.")
        return d
    return None


# ---------------------------------------------------------------- rampages

def wake_chance(d):
    """A poor dragon wakes hungrier (up to half again as often), a rich one sleeps longer (down to half)."""
    from Data.History.eras import effect                         # an Age of Dragons wakes them
    return WAKE_CHANCE * min(1.5, max(0.5, RICH_HOARD / 2 / max(1.0, d["hoard"]))) * effect("dragons")


def choose_town(conn, world_id, d):
    """A dragon is cunning: the richest settlement in reach for the fewest swords and spells -- nearer ones likelier.
    None if none in reach."""
    best, best_score = None, 0
    for c in conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1
                                         AND (n.profession IN ('Knight', 'Captain', 'Soldier', 'Guard')
                                              OR n.class IN ('Wizard', 'Sorcerer', 'Warlock', 'Cleric', 'Druid'))) AS swords
                             FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                             WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)).fetchall():
        dist = math.dist((c["map_x"], c["map_y"]), (d["map_x"], d["map_y"]))
        if dist > RAMPAGE_REACH:
            continue
        score = ((max(0, c["wealth"] or 0) + 50 * (c["population"] or 0) + 10) / (1 + dist / 400)
                 / (1 + c["swords"] / SWORDS_FEARED) * random.uniform(0.5, 1.5))
        if score > best_score:
            best, best_score = {k: c[k] for k in c.keys() if k != "swords"}, score
    return best


def rampage(conn, world_id, d, city):
    """An evil dragon falls on a town. Returns the report."""
    from Data.Combat import melee
    from Data.Combat.specials import aftermath
    from Data.World.disasters import _damage, _afraid
    from Data.Monsters.raids import plural
    day = _today(conn, world_id)
    beast = fighter_of(d, "dragon")
    defenders = town_defenders(conn, world_id, city)
    helpers = []
    disguised = guise_in(conn, world_id, city["id"])
    if disguised:
        helpers.append((disguised, reveal(conn, world_id, disguised, city, "town")))
    helpers += [h for h in guardians(conn, world_id, city) if not disguised or h[0]["id"] != disguised["id"]]
    for g, f in helpers:
        _event(conn, "Dragon", f"{full_name(g)} came out of the sky to fight {full_name(d)} over {city['name']}.",
               city_id=city["id"], kingdom_id=city["kingdom_id"])
    side = defenders + [f for _, f in helpers]
    # a dragon does not fight to the death: it burns and wheels for RAMPAGE_ROUNDS, then flies home with what it took --
    # unless it is slain first, or so hurt (below DRIVEN_OFF of its hit points) that it flees empty-clawed
    result = melee.battle([beast], side, max_rounds=RAMPAGE_ROUNDS) if side else \
        {"winner": "a", "a": [beast], "b": [], "dead_a": [], "dead_b": []}
    slain = beast.hp <= 0
    won = not slain and (result["winner"] == "a" or beast.hp >= DRIVEN_OFF * beast.max_hp)
    if not won and not slain:
        return driven_off(conn, world_id, d, city, result, helpers, day)
    dead = _bury(conn, result["dead_b"], city["id"], f"slain by {full_name(d)}")
    militia_dead = sum(1 for f in result["dead_b"] if f.ref is None and f.name == "militia")
    if militia_dead and city["type"] == "Village":
        conn.execute("UPDATE cities SET households = MAX(0, households - ?) WHERE id = ?", (militia_dead / 5, city["id"]))
    for g, f in helpers:                                          # good dragons who fell defending the town
        if f.hp <= 0:
            _slain(conn, g, day, f"slain by {full_name(d)} over {city['name']}")
    aftermath(conn, defenders)
    conn.execute("UPDATE dragons SET woke_day = ?, attacks = attacks + 1 WHERE id = ?", (day, d["id"]))
    report = {"dragon": d["id"], "city": city["id"], "won": won, "dead": dead + militia_dead, "gold": 0.0, "slain": False,
              "helpers": [g["id"] for g, _ in helpers]}
    if won:
        gold = round(max(0.0, city["wealth"] or 0) * TAKEN_GOLD, 2)
        conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (gold, city["id"]))
        food = conn.execute("SELECT amount FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (city["id"],)).fetchone()
        taken = round((food[0] if food else 0) * TAKEN_FOOD, 2)
        conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Food'", (taken, city["id"]))
        burned = _damage(conn, city["id"], random.uniform(*BURNED), BURN_DAMAGE)
        conn.execute("UPDATE dragons SET hoard = hoard + ? WHERE id = ?", (gold, d["id"]))
        _afraid(conn, city["id"], day)
        report.update(gold=gold, burned=burned)
        text = (f"{full_name(d)} fell on {city['name']}: {burned} buildings burned, {dead + militia_dead} killed, "
                f"and {gold:,.0f} gold carried off to its hoard.")
        event_id = _event(conn, "Dragon Attack", text, city_id=city["id"], kingdom_id=city["kingdom_id"])
        _chronicle(conn, world_id, city["kingdom_id"], "Dragon Attack (burned)",
                   {"city": city["name"], "dragon": full_name(d), "dead": dead + militia_dead}, (d["id"], day), event_id)
    else:
        killer = next((g for g, f in helpers if f.hp > 0), None)
        report["killer"] = killer["id"] if killer else None
        hoard = conn.execute("SELECT hoard FROM dragons WHERE id = ?", (d["id"],)).fetchone()[0]
        _slain(conn, d, day, full_name(killer) if killer else city["name"])
        report["slain"] = True
        if killer:                                                 # a good dragon slew it: claims the hoard, gives a share
            gift = round(hoard * GIFT_SHARE.get(killer["monster"], 0.2), 2)
            conn.execute("UPDATE dragons SET hoard = hoard + ? WHERE id = ?", (hoard - gift, killer["id"]))
            conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (gift, city["id"]))
            report["gold"] = gift
            text = (f"{full_name(killer)} slew {full_name(d)} over {city['name']}, claimed its hoard, "
                    f"and gave the town {gift:,.0f} gold of it.")
            event_id = _event(conn, "Dragon Slain", text, city_id=city["id"], kingdom_id=city["kingdom_id"])
            _chronicle(conn, world_id, city["kingdom_id"], "Dragon Slain (by a dragon)",
                       {"city": city["name"], "dragon": full_name(d), "slayer": full_name(killer), "gold": f"{gift:,.0f}"},
                       (d["id"], day), event_id)
        else:
            slayer = _slayer(conn, result["b"])
            to_town = round(hoard * TOWN_SHARE, 2)
            conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (to_town, city["id"]))
            conn.execute("UPDATE kingdoms SET treasury = treasury + ? WHERE id = ?", (round(hoard - to_town, 2), city["kingdom_id"]))
            report["gold"] = hoard
            from Data.Combat.nemesis import display_name
            who = display_name(conn, slayer) if slayer else f"the folk of {city['name']}"
            text = f"{full_name(d)} fell on {city['name']} and was slain by {who}; its hoard of {hoard:,.0f} gold was carried home."
            event_id = _event(conn, "Dragon Slain", text, city_id=city["id"], kingdom_id=city["kingdom_id"])
            _chronicle(conn, world_id, city["kingdom_id"], "Dragon Slain (town)",
                       {"city": city["name"], "dragon": full_name(d), "slayer": who, "gold": f"{hoard:,.0f}"}, (d["id"], day), event_id)
    standing = [g for g, f in helpers if f.hp > 0]
    if standing and (won or report.get("gold") is None or not killer_of(report)):
        _chronicle(conn, world_id, city["kingdom_id"], "Dragon Guardian",               # it fought for them, and lived
                   {"city": city["name"], "dragon": full_name(d), "slayer": full_name(standing[0])}, (standing[0]["id"], d["id"], day))
    conn.commit()
    return report


def driven_off(conn, world_id, d, city, result, helpers, day):
    """The town held: the dragon fled, hurt and empty-clawed. Some of the town still burned."""
    from Data.World.disasters import _damage, _afraid
    from Data.Combat.specials import aftermath
    dead = _bury(conn, result["dead_b"], city["id"], f"slain by {full_name(d)}")
    for g, f in helpers:
        if f.hp <= 0:
            _slain(conn, g, day, f"slain by {full_name(d)} over {city['name']}")
    aftermath(conn, [f for f in result.get("b", [])])
    burned = _damage(conn, city["id"], random.uniform(*BURNED) / 2, BURN_DAMAGE)
    _afraid(conn, city["id"], day)
    conn.execute("UPDATE dragons SET woke_day = ?, attacks = attacks + 1 WHERE id = ?", (day, d["id"]))
    text = f"{full_name(d)} fell on {city['name']} but was driven off, wounded, with nothing; {burned} buildings burned, {dead} killed."
    event_id = _event(conn, "Dragon Attack", text, city_id=city["id"], kingdom_id=city["kingdom_id"])
    _chronicle(conn, world_id, city["kingdom_id"], "Dragon Attack (driven off)",
               {"city": city["name"], "dragon": full_name(d), "dead": dead}, (d["id"], day), event_id)
    conn.commit()
    return {"dragon": d["id"], "city": city["id"], "won": False, "dead": dead, "gold": 0.0, "slain": False, "driven_off": True,
            "helpers": [g["id"] for g, _ in helpers]}


def killer_of(report):
    """The good dragon that slew the evil one in this rampage, if one did."""
    return report.get("killer")


# ---------------------------------------------------------------- hunts

def _band(conn, world_id, kingdom_id):
    """A kingdom's best fighters and casters at home: HUNT_BAND of them, strongest first."""
    from Data.Combat import melee
    from Data.Government.armies import get_army_npc_ids
    from Data.Infrastructure.travel import get_traveling_npc_ids
    away = set(get_army_npc_ids(conn, world_id)) | set(get_traveling_npc_ids(conn, world_id))
    fighters = []
    for npc in conn.execute(
            """SELECT n.* FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1
               AND n.age BETWEEN 18 AND 60 AND n.title IS NULL AND n.jailed_until IS NULL
               AND n.profession IN ('Knight', 'Captain', 'Soldier', 'Mage', 'Priest', 'Healer')""", (kingdom_id,)).fetchall():
        if npc["id"] in away:
            continue
        skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
        fighters.append(melee.person_fighter(dict(npc), side="hunters", skills=skills))
    fighters.sort(key=lambda f: -((f.hit_dice or 0) + f.caster_level))
    return fighters[:HUNT_BAND]


def hunt(conn, world_id, kingdom_id, d):
    """A kingdom's band goes to the lair to slay the dragon (and any kin there). Returns the report, or None."""
    from Data.Combat import melee
    from Data.Combat.specials import aftermath
    from Data.Monsters.bestiary import by_name
    from Data.Combat.nemesis import display_name
    band = _band(conn, world_id, kingdom_id)
    if len(band) < HUNT_MIN:
        return None
    day = _today(conn, world_id)
    kin = melee.monster_fighters(by_name(d["monster"]), max(0, d["count"] - 1), d["hit_points_each"], side="dragon") if d["count"] > 1 else []
    beast = fighter_of(d, "dragon")
    asleep = random.random() < KINDS[d["monster"]].get("asleep", 0)
    if asleep:
        beast.held = SLEEPING_ROUNDS                               # found asleep on its hoard: struck before it wakes
    capital = conn.execute("SELECT capital_city_id FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()[0]
    result = melee.battle([beast] + kin, band)
    won = result["winner"] == "b"
    dead = _bury(conn, result["dead_b"], capital, f"slain hunting {full_name(d)}")
    aftermath(conn, band)
    report = {"kingdom": kingdom_id, "dragon": d["id"], "won": won, "hunters": len(band), "dead": dead, "gold": 0.0, "asleep": asleep}
    if won:
        hoard = conn.execute("SELECT hoard FROM dragons WHERE id = ?", (d["id"],)).fetchone()[0]
        slayer = _slayer(conn, result["b"])
        _slain(conn, d, day, display_name(conn, slayer) if slayer else "hunters")
        conn.execute("UPDATE monster_lairs SET count = 0, status = 'Cleared' WHERE id = ?", (d["lair_id"],))
        crown = round(hoard * HUNT_CROWN_SHARE, 2)
        conn.execute("UPDATE kingdoms SET treasury = treasury + ? WHERE id = ?", (crown, kingdom_id))
        living = [f.ref for f in result["b"] if f.ref is not None]
        for ref in living:
            conn.execute("UPDATE npcs SET gold = gold + ? WHERE id = ?", (round((hoard - crown) / len(living), 2), ref))
        report["gold"] = hoard
        who = display_name(conn, slayer) if slayer else "the hunters"
        text = f"Hunters of the crown slew {full_name(d)} in its lair; {who} struck the last blow, and {hoard:,.0f} gold came home."
        event_id = _event(conn, "Dragon Slain", text, city_id=capital, kingdom_id=kingdom_id)
        _chronicle(conn, world_id, kingdom_id, "Dragon Hunt (slain)",
                   {"dragon": full_name(d), "slayer": who, "gold": f"{hoard:,.0f}", "dead": dead}, (d["id"], day), event_id)
    else:
        text = f"Hunters went out against {full_name(d)} in its lair, and {dead} of {len(band)} did not return."
        event_id = _event(conn, "Dragon Hunt", text, city_id=capital, kingdom_id=kingdom_id)
        _chronicle(conn, world_id, kingdom_id, "Dragon Hunt (failed)", {"dragon": full_name(d), "dead": dead}, (d["id"], day), event_id)
    conn.commit()
    return report


def hunts(conn, world_id):
    """Yearly: kingdoms send hunters after the dragons that burned them -- or, if greedy, after a hoard."""
    day = _today(conn, world_id)
    reports = []
    living = dragons(conn, world_id)
    for k in conn.execute("""SELECT k.id, k.capital_city_id, n.generosity FROM kingdoms k LEFT JOIN npcs n ON n.id = k.ruler_id
                             WHERE k.world_id = ? AND k.status != 'Fallen' AND k.type NOT IN ('Horde')""", (world_id,)).fetchall():
        wronged = {r[0] for r in conn.execute(
            """SELECT DISTINCT d.id FROM events e JOIN dragons d ON e.description LIKE '%' || d.name || ' the %'
               WHERE e.type = 'Dragon Attack' AND e.kingdom_id = ? AND e.day >= ? AND d.world_id = ?""",
            (k["id"], day - HUNT_MEMORY * 360, world_id))}
        targets = [d for d in living if d["id"] in wronged]
        if targets and random.random() < HUNT_CHANCE:
            target = targets[0]
        else:
            greed = k["generosity"] if k["generosity"] is not None else 0
            if greed > GREEDY or random.random() >= HUNT_GREED:
                continue
            seat = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (k["capital_city_id"],)).fetchone()
            if not seat:
                continue
            near = [d for d in living if math.dist((d["map_x"], d["map_y"]), (seat[0], seat[1])) <= HUNT_REACH
                    and (not d["good"] or greed <= GREEDY_HUNTS_GOOD)]
            if not near:
                continue
            target = max(near, key=lambda d: d["hoard"])
        report = hunt(conn, world_id, k["id"], target)
        if report:
            reports.append(report)
            living = dragons(conn, world_id)
    return reports


# ---------------------------------------------------------------- the good dragons' ways

def take_guises(conn, world_id):
    """Yearly: a silver or gold dragon without a mortal form may take one, in the nearest town. Returns how many did."""
    from Data.World.villagers import make_individual
    n = 0
    for d in dragons(conn, world_id, good=True):
        kind = KINDS[d["monster"]]
        if not kind.get("guise") or d["guise_npc_id"] and conn.execute(
                "SELECT alive FROM npcs WHERE id = ?", (d["guise_npc_id"],)).fetchone()[0]:
            continue
        if random.random() >= kind["guise"]:
            continue
        town = conn.execute(
            """SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND k.status != 'Fallen'
               AND c.type != 'Village' ORDER BY (c.map_x - ?) * (c.map_x - ?) + (c.map_y - ?) * (c.map_y - ?) LIMIT 1""",
            (world_id, d["map_x"], d["map_x"], d["map_y"], d["map_y"])).fetchone()
        if not town or math.dist((town["map_x"], town["map_y"]), (d["map_x"], d["map_y"])) > GUISE_REACH:
            continue
        households = town["households"]
        npc = make_individual(conn, town["id"], town["id"], profession=kind["trade"], age=random.randint(30, 60))
        conn.execute("UPDATE cities SET households = ? WHERE id = ?", (households, town["id"]))
        conn.execute("UPDATE npcs SET gold = gold + 200 WHERE id = ?", (npc["id"],))
        conn.execute("UPDATE dragons SET guise_npc_id = ? WHERE id = ?", (npc["id"], d["id"]))
        _event(conn, "Dragon", f"A stranger named {npc['name']} came to live in {town['name']}.", city_id=town["id"],
               kingdom_id=town["kingdom_id"])
        n += 1
    return n


def pranks_and_gossip(conn, world_id):
    """Yearly: copper dragons tease the nearest town; brass dragons carry news and warm two kingdoms."""
    from Data.Database.repository import adjust_kingdom_favor
    out = {"pranks": 0, "gossip": 0}
    for d in dragons(conn, world_id, good=True):
        if d["monster"] == "Dragon, Copper" and random.random() < PRANK_CHANCE:
            town = conn.execute(
                """SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND k.status != 'Fallen'
                   ORDER BY (c.map_x - ?) * (c.map_x - ?) + (c.map_y - ?) * (c.map_y - ?) LIMIT 1""",
                (world_id, d["map_x"], d["map_x"], d["map_y"], d["map_y"])).fetchone()
            if not town:
                continue
            gold = round(max(0, town["wealth"] or 0) * PRANK_GOLD, 2) if random.random() < 0.5 else 0
            conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (gold, town["id"]))
            conn.execute("UPDATE dragons SET hoard = hoard + ? WHERE id = ?", (gold, d["id"]))
            conn.execute("UPDATE npcs SET needs_happiness = MIN(100, needs_happiness + 3) WHERE city_id = ? AND alive = 1", (town["id"],))
            what = (f"and {gold:,.0f} gold from the town chest turned up in the dragon's hoard" if gold
                    else "and left the town laughing at its riddles")
            _event(conn, "Dragon", f"{full_name(d)} played its pranks on {town['name']}, {what}.", city_id=town["id"],
                   kingdom_id=town["kingdom_id"])
            out["pranks"] += 1
        elif d["monster"] == "Dragon, Brass" and random.random() < GOSSIP_CHANCE:
            near = conn.execute(
                """SELECT k.id, k.name FROM kingdoms k JOIN cities c ON c.id = k.capital_city_id WHERE k.world_id = ?
                   AND k.status != 'Fallen' ORDER BY (c.map_x - ?) * (c.map_x - ?) + (c.map_y - ?) * (c.map_y - ?) LIMIT 2""",
                (world_id, d["map_x"], d["map_x"], d["map_y"], d["map_y"])).fetchall()
            if len(near) == 2:
                adjust_kingdom_favor(conn, near[0]["id"], near[1]["id"], GOSSIP_FAVOR)
                adjust_kingdom_favor(conn, near[1]["id"], near[0]["id"], GOSSIP_FAVOR)
                _event(conn, "Dragon", f"{full_name(d)} flew between {near[0]['name']} and {near[1]['name']} trading news and "
                                       f"gossip, and each thought a little better of the other.", kingdom_id=near[0]["id"])
                out["gossip"] += 1
    return out


def bronze_aid(conn, world_id, attacker, target):
    """A bronze dragon near a town an empire or a horde assails may join its defence. Returns the dragon, or None."""
    if attacker.get("type") not in ("Empire", "Horde"):
        return None
    for d in dragons(conn, world_id, good=True):
        if d["monster"] != "Dragon, Bronze":
            continue
        if math.dist((d["map_x"], d["map_y"]), (target["map_x"], target["map_y"])) > GUARD_REACH:
            continue
        if random.random() < KINDS[d["monster"]]["guard"]:
            _event(conn, "Dragon", f"{full_name(d)}, who hates a tyrant, came down to fight for {target['name']} against "
                                   f"{attacker['name']}.", city_id=target["id"], kingdom_id=target["kingdom"])
            _chronicle(conn, world_id, target["kingdom"], "Dragon Guardian",
                       {"city": target["name"], "dragon": attacker["name"], "slayer": full_name(d)}, (d["id"], target["id"]))
            return d
    return None


def raid_guise(conn, world_id, village):
    """Raiders fall on a town where a good dragon lives disguised: it reveals itself. Returns its fighter, or None."""
    d = guise_in(conn, world_id, village["id"])
    return reveal(conn, world_id, d, village, "village") if d else None


# ---------------------------------------------------------------- the year

def dragons_year(conn, world_id):
    """Yearly: dragons are named, evil ones wake and burn, kingdoms hunt them, good ones take mortal form,
    play pranks and carry gossip. Returns what happened."""
    ensure_dragons(conn, world_id)
    from Data.History.eras import effect
    if len(dragons(conn, world_id, good=False)) < MAX_EVIL and random.random() < NEW_DRAGON_CHANCE * effect("dragons"):
        new_dragon(conn, world_id)
    rampages = []
    for d in dragons(conn, world_id, good=False):
        if random.random() >= wake_chance(d):
            continue
        city = choose_town(conn, world_id, d)
        if city:
            rampages.append(rampage(conn, world_id, d, city))
    out = {"rampages": rampages, "hunts": hunts(conn, world_id), "guises": take_guises(conn, world_id)}
    out.update(pranks_and_gossip(conn, world_id))
    conn.commit()
    return out