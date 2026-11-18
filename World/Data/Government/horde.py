"""
Horse lords: the nomad hordes of the steppe. No world begins with one.

Rising      each year, while the world has empty Steppe (STEPPE_NEEDED regions
            with no settlement) and fewer than MAX_HORDES hordes ride, a
            warlord may unite the steppe tribes (RISE_CHANCE): a new kingdom
            of type Horde -- mostly humans and half-orcs, sometimes all orcs
            (RACE_WEIGHTS) -- with a camp in an empty steppe region, far from
            any town (CAMP_CLEARANCE). The camp is the horde's capital: horse
            lines (barracks), herders, smiths and a shaman (the "Camp" kit in
            Data/Buildings/CityBuildingKits.json), HORDE_PEOPLE folk.

The camp    each season it moves on, to another empty steppe region within
            MOVE_REACH (its roads to other capitals are measured anew). Each
            month the herds feed it: HERD_FOOD a head a day, a little more
            than its people eat. Riders are not paid: they live off the herds
            and what they take (Data.Government.military skips a horde's gold
            and weapons), and its horse lines take every young jobless rider
            they have room for (Data.Economy.jobs.recruit).

Raids       each summer month (RAID_MONTHS) a horde may ride (RAID_CHANCE):
            RAID_SHARE of its riders against the nearest village within
            RAID_REACH of a kingdom that pays it nothing. The fight is by the
            book (Data.Combat.melee), riders charging (CHARGE). If the riders
            win, they carry off FOOD_TAKEN of the village's market grain and
            STORE_TAKEN of its families' stores to the camp, GOLD_TAKEN of its
            purse to the khan, take CAPTIVES families as captives (they join
            the horde, named) and burn BURNED of its farms. Great raids are
            chronicled.

Danegeld    a kingdom whose village was sacked may pay the khan to stay away
            (DANEGELD_CHANCE, more for every raid it has suffered from that
            horde in the last two years, less for a bold ruler): it becomes a
            tributary of the horde (Data.Government.empire, how = 'danegeld'),
            paying DANEGELD_PER_HEAD gold a year for each of its subjects
            (long_run.py: priced like an empire's tribute, a tenth of the
            treasury, a kingdom paid 183,000 a year to stop raids that took
            5,000) and grain, until it dares to stop. A horde
            never marches on a payer that falls behind -- it simply rides
            again once the payments stop.

The khan    chosen by strength, not birth: when a khan dies, the horde's
            strongest fighter (fighting level, then renown) is raised Khan or
            Khanum. If the runner-up is nearly as strong (RIVAL_CLOSE) and the
            horde big enough (SPLIT_PEOPLE), the rival may lead SPLIT_SHARE of
            it away (SPLIT_CHANCE) to the steppe as a horde of their own.
"""

import math
import random

RISE_CHANCE = 0.05               # a year: about once in twenty years
MAX_HORDES = 2
STEPPE_NEEDED = 3
CAMP_CLEARANCE = 500             # map units from the nearest town or village
RACE_WEIGHTS = {"Human": 0.45, "Half-Orc": 0.30, "Orc": 0.25}
HORDE_PEOPLE = (180, 280)
START_FOOD_DAYS = 90
HERD_FOOD = 1.25                 # Food a head a day from the herds
MOVE_REACH = 1200

RAID_MONTHS = (4, 5, 6)          # summer
RAID_CHANCE = 0.6
RAID_REACH = 1400
RAID_SHARE = 0.5
MIN_RAIDERS = 5
CHARGE = 1                       # to-hit for riders charging home
FOOD_TAKEN = 0.4
STORE_TAKEN = 0.1
GOLD_TAKEN = 0.5
CAPTIVES = 0.02                  # of the village's families, at most MAX_CAPTIVES
MAX_CAPTIVES = 6
BURNED = 0.03
CHRONICLED = 10

DANEGELD_PER_HEAD = 2.0          # gold a year, each of the payer's subjects: a few raids' worth, bought off
DANEGELD_CHANCE = 0.25
DANEGELD_PER_RAID = 0.15
BOLDNESS_SWAY = 0.3

RIVAL_CLOSE = 0.85
SPLIT_PEOPLE = 120
SPLIT_SHARE = 0.4
SPLIT_CHANCE = 0.5


# ------------------------------------------------------------------ helpers
def hordes(conn, world_id):
    return [dict(k) for k in conn.execute(
        "SELECT * FROM kingdoms WHERE world_id = ? AND type = 'Horde' AND status != 'Fallen' AND capital_city_id IS NOT NULL",
        (world_id,))]


def is_horde(conn, kingdom_id):
    row = conn.execute("SELECT type FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    return bool(row) and row[0] == "Horde"


def riders(conn, kingdom_id):
    """The horde's riders at home and fit to ride: [npc rows]."""
    from Data.Government.military import MILITARY_PROFESSIONS
    marks = ",".join("?" * len(MILITARY_PROFESSIONS))
    return [dict(r) for r in conn.execute(
        f"""SELECT n.* FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks}) AND n.jailed_until IS NULL
            AND n.id NOT IN (SELECT npc_id FROM army_members)""", (kingdom_id, *MILITARY_PROFESSIONS))]


def free_steppe(conn, world_id):
    """Steppe regions with no town or village in them, [region rows]."""
    return [dict(r) for r in conn.execute(
        """SELECT * FROM regions r WHERE r.world_id = ? AND r.terrain = 'Steppe'
           AND NOT EXISTS (SELECT 1 FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                           WHERE c.region_id = r.id AND k.status != 'Fallen' AND k.type != 'Horde')""", (world_id,))]


def _clear_of_towns(conn, world_id, region, clearance=CAMP_CLEARANCE):
    for c in conn.execute(
            """SELECT c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)):
        if math.dist((c[0], c[1]), (region["center_x"], region["center_y"])) < clearance:
            return False
    return True


def _chronicle(conn, world_id, kingdom_id, kind, key, fields, seed):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom_id, kind, key, year, season, fields, seed_parts=seed)


def _reroad(conn, world_id, camp_id):
    """Measure the camp's trade routes to every other capital anew (it has moved, or is new)."""
    from Data.Database.repository import get_city, create_road
    from Data.Infrastructure.roads import road_distance, travel_days_for
    camp = get_city(conn, camp_id)
    conn.execute("DELETE FROM roads WHERE (city_a_id = ? OR city_b_id = ?) AND kind = 'Trade Route'", (camp_id, camp_id))
    for (other,) in conn.execute(
            "SELECT capital_city_id FROM kingdoms WHERE world_id = ? AND status != 'Fallen' AND capital_city_id IS NOT NULL AND capital_city_id != ?",
            (world_id, camp_id)).fetchall():
        far = get_city(conn, other)
        distance = road_distance(camp, far)
        create_road(conn, camp_id, other, "Trade Route", distance, travel_days_for(distance))


# ------------------------------------------------------------------ the khan
def strength(conn, npc):
    """How a horde judges a would-be khan: fighting level first, then renown."""
    from Data.Combat import melee
    skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
    fighter = melee.person_fighter(npc, skills=skills)
    return fighter.hit_dice * 10 + (npc.get("renown") or 0)


def khan_title(gender):
    return "Khan" if gender == "Male" else "Khanum"


def choose_khan(conn, camp, previous=None, allow_split=True):
    """
    The strongest of the horde is raised khan. A rival nearly as strong may
    lead part of the horde away (split_horde). Returns the new khan (a dict) or None.
    """
    from Data.Database.repository import get_npc, save_npc, create_event
    from Data.Events.events import coronation_event
    from Data.Infrastructure.migration import relocate_npc
    kingdom = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (camp["kingdom"],)).fetchone())
    contenders = [dict(r) for r in conn.execute(
        """SELECT n.* FROM npcs n JOIN cities c ON c.id = n.city_id
           WHERE c.kingdom_id = ? AND n.alive = 1 AND n.age >= 16 AND n.profession != 'Child'
             AND (n.title IS NULL OR n.title IN ('Khan', 'Khanum'))""", (kingdom["id"],))]
    if not contenders:
        return None
    ranked = sorted(((strength(conn, n) + random.uniform(0, 3), n) for n in contenders), key=lambda t: -t[0])
    khan = ranked[0][1]
    if khan["city_id"] != camp["id"]:
        relocate_npc(conn, khan["id"], camp["id"])
    khan = get_npc(conn, khan["id"])
    khan["title"] = khan_title(khan["gender"])
    save_npc(conn, khan)
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (khan["id"], camp["id"]))
    conn.execute("UPDATE kingdoms SET ruler_id = ?, regent_id = NULL WHERE id = ?", (khan["id"], kingdom["id"]))
    coronation_event(conn, khan, camp)
    if previous is not None:
        _chronicle(conn, kingdom["world_id"], kingdom["id"], "Coronation", "Khan Chosen",
                   {"kingdom": kingdom["name"], "ruler": f"{khan['title']} {khan['name']}", "old": previous.get("name", "the old khan")},
                   (khan["id"],))

    if allow_split and len(ranked) > 1:
        people = conn.execute("""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
                                 WHERE c.kingdom_id = ? AND n.alive = 1""", (kingdom["id"],)).fetchone()[0]
        best, rival_score, rival = ranked[0][0], ranked[1][0], ranked[1][1]
        if people >= SPLIT_PEOPLE and best > 0 and rival_score >= RIVAL_CLOSE * best and random.random() < SPLIT_CHANCE:
            split_horde(conn, kingdom["world_id"], kingdom, khan, rival)
    conn.commit()
    return khan


def split_horde(conn, world_id, kingdom, khan, rival):
    """The rival rides off with SPLIT_SHARE of the horde to found their own. Returns the new horde's id, or None."""
    from Data.Database.repository import create_event, adjust_kingdom_favor
    camp_id = kingdom["capital_city_id"]
    places = [r for r in free_steppe(conn, world_id) if r["id"] != conn.execute(
        "SELECT region_id FROM cities WHERE id = ?", (camp_id,)).fetchone()[0]]
    if not places:
        return None
    region = random.choice(places)
    new_id, new_camp = found_horde(conn, world_id, region, race=kingdom.get("race"), people=0, chronicle=False)
    # the rival, and a share of the horde's folk -- whole families, riders among them -- go with them
    followers = [r[0] for r in conn.execute(
        """SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
           WHERE c.kingdom_id = ? AND n.alive = 1 AND n.id NOT IN (?, ?) ORDER BY RANDOM()""",
        (kingdom["id"], khan["id"], rival["id"])).fetchall()]
    going = [rival["id"]] + followers[:int(len(followers) * SPLIT_SHARE)]
    conn.executemany("UPDATE npcs SET city_id = ?, workplace_id = NULL, home_id = NULL WHERE id = ?", [(new_camp, i) for i in going])
    food = conn.execute("SELECT amount FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (camp_id,)).fetchone()
    share = (food[0] if food else 0) * SPLIT_SHARE
    conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Food'", (share, camp_id))
    conn.execute("UPDATE city_resources SET amount = amount + ? WHERE city_id = ? AND resource_name = 'Food'", (share, new_camp))
    from Data.Database.repository import get_city
    choose_khan(conn, get_city(conn, new_camp), allow_split=False)
    conn.execute("UPDATE cities SET population = (SELECT COUNT(*) FROM npcs WHERE city_id = cities.id AND alive = 1) WHERE id IN (?, ?)",
                 (camp_id, new_camp))
    adjust_kingdom_favor(conn, new_id, kingdom["id"], -40)
    adjust_kingdom_favor(conn, kingdom["id"], new_id, -40)
    new = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (new_id,)).fetchone()[0]
    for kid in (kingdom["id"], new_id):
        _chronicle(conn, world_id, kid, "Founding", "Horde Split",
                   {"kingdom": kingdom["name"], "other": new, "ruler": f"{khan_title(khan['gender'])} {khan['name']}",
                    "rival": rival["name"], "region": region["name"]}, (new_id, kid))
    create_event(conn, "Horde", f"{rival['name']} would not bow to {khan['name']}, and rode off with a third of {kingdom['name']} "
                                f"to found {new} in {region['name']}.", kingdom_id=new_id)
    return new_id


# ------------------------------------------------------------------ rising
def found_horde(conn, world_id, region, race=None, people=None, chronicle=True):
    """A horde rises in an empty steppe region. Returns (kingdom id, camp id)."""
    from Data.Database.repository import create_kingdom, create_city, create_event, adjust_kingdom_favor, get_city
    from Data.Buildings.building_generator import generate_buildings
    from Data.Cities.city_generator import generate_city_name
    from Data.NPCS.npc_generator import generate_npc
    from Data.Kingdoms.kingdom_generator import generate_kingdom_name, KINGDOM_COLORS
    from Data.NPCS.races import realm_name, seed_kingdom_feelings
    from Data.World.regions import assign_settlements_to_regions

    race = race or random.choices(list(RACE_WEIGHTS), weights=list(RACE_WEIGHTS.values()))[0]
    taken = {row[0] for row in conn.execute("SELECT name FROM kingdoms WHERE world_id = ?", (world_id,))}
    name = realm_name(race, taken, generate_kingdom_name) or "the Horde"
    used = {row[0] for row in conn.execute("SELECT color FROM kingdoms WHERE world_id = ?", (world_id,))}
    color = next((c for c in KINGDOM_COLORS if c not in used), "#%06X" % random.randint(0, 0xFFFFFF))
    kingdom_id = create_kingdom(conn, world_id, name, color)["id"]
    conn.execute("UPDATE kingdoms SET type = 'Horde', race = ?, treasury = 0 WHERE id = ?", (race, kingdom_id))

    people = random.randint(*HORDE_PEOPLE) if people is None else people
    x = region["center_x"] + random.randint(-40, 40)
    y = region["center_y"] + random.randint(-40, 40)
    camp = create_city(conn, kingdom_id, generate_city_name(conn, kingdom_id), "Capital", people, x, y)
    generate_buildings(conn, camp["id"], "Camp", max(people, min(HORDE_PEOPLE)))
    for _ in range(people):
        generate_npc(conn, camp["id"], "Camp")
    conn.execute("UPDATE kingdoms SET capital_city_id = ? WHERE id = ?", (camp["id"], kingdom_id))
    conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
                    ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = excluded.amount""",
                 (camp["id"], max(people, min(HORDE_PEOPLE)) * START_FOOD_DAYS))
    assign_settlements_to_regions(conn, world_id)
    seed_kingdom_feelings(conn, kingdom_id)
    _reroad(conn, world_id, camp["id"])
    for (other,) in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND id != ? AND status != 'Fallen'",
                                 (world_id, kingdom_id)).fetchall():
        adjust_kingdom_favor(conn, other, kingdom_id, -20)          # the settled peoples fear the riders

    if people:
        # the horse lines take their riders, and the strongest is raised khan
        from Data.Economy.jobs import recruit
        for _ in range(20):
            if not recruit(conn, world_id, only=kingdom_id):
                break
        choose_khan(conn, get_city(conn, camp["id"]), allow_split=False)
    if chronicle:
        khan = conn.execute("SELECT n.name, n.title FROM kingdoms k JOIN npcs n ON n.id = k.ruler_id WHERE k.id = ?", (kingdom_id,)).fetchone()
        _chronicle(conn, world_id, kingdom_id, "Founding", "Horde Risen",
                   {"kingdom": name, "ruler": f"{khan['title']} {khan['name']}" if khan else "a warlord", "region": region["name"]},
                   (kingdom_id,))
        create_event(conn, "Horde", f"The tribes of {region['name']} united under one khan: {name} rides.", kingdom_id=kingdom_id)
    conn.commit()
    return kingdom_id, camp["id"]


def rise_hordes(conn, world_id):
    """Yearly: perhaps a warlord unites the steppe tribes. Returns the new horde's id, or None."""
    if len(hordes(conn, world_id)) >= MAX_HORDES or random.random() >= RISE_CHANCE:
        return None
    places = [r for r in free_steppe(conn, world_id) if _clear_of_towns(conn, world_id, r)]
    if len(free_steppe(conn, world_id)) < STEPPE_NEEDED or not places:
        return None
    return found_horde(conn, world_id, random.choice(places))[0]


# ------------------------------------------------------------------ the camp
def herds_month(conn, world_id):
    """Monthly: the herds feed each camp. Returns Food added."""
    added = 0.0
    for h in hordes(conn, world_id):
        people = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1", (h["capital_city_id"],)).fetchone()[0]
        food = round(people * HERD_FOOD * 30, 2)
        conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
                        ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                     (h["capital_city_id"], food))
        added += food
    conn.commit()
    return round(added, 2)


def move_camps(conn, world_id):
    """Each season: every camp moves on to another empty steppe region within reach. Returns [(horde id, region id)]."""
    from Data.World.regions import assign_settlements_to_regions
    moved = []
    for h in hordes(conn, world_id):
        camp = conn.execute("SELECT id, map_x, map_y, region_id FROM cities WHERE id = ?", (h["capital_city_id"],)).fetchone()
        if conn.execute("SELECT 1 FROM campaigns WHERE target_city_id = ? AND status != 'Over'", (camp["id"],)).fetchone():
            continue                                            # besieged: it cannot slip away
        places = [r for r in free_steppe(conn, world_id)
                  if r["id"] != camp["region_id"] and math.dist((r["center_x"], r["center_y"]), (camp["map_x"], camp["map_y"])) <= MOVE_REACH]
        if not places:
            continue
        hunting = [r for r in places if _target(conn, world_id, h, at=(r["center_x"], r["center_y"]))]
        region = random.choice(hunting or places)              # the riders drift toward prey that does not pay
        conn.execute("UPDATE cities SET map_x = ?, map_y = ?, region_id = ? WHERE id = ?",
                     (region["center_x"] + random.randint(-40, 40), region["center_y"] + random.randint(-40, 40), region["id"], camp["id"]))
        _reroad(conn, world_id, camp["id"])
        moved.append((h["id"], region["id"]))
    if moved:
        assign_settlements_to_regions(conn, world_id)
    conn.commit()
    return moved


# ------------------------------------------------------------------ raids
def _target(conn, world_id, horde, at=None):
    """The nearest village within raiding reach of the camp (or of the point `at`) whose kingdom does not pay."""
    from Data.Government.empire import tribute_of
    camp = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (horde["capital_city_id"],)).fetchone()
    if at is not None:
        camp = {"map_x": at[0], "map_y": at[1]}
    best = None
    for v in conn.execute(
            """SELECT c.id, c.name, c.kingdom_id, c.map_x, c.map_y, c.households, c.household_store, c.wealth
               FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND c.type = 'Village' AND k.id != ? AND k.type != 'Horde'""",
            (world_id, horde["id"])):
        d = math.dist((v["map_x"], v["map_y"]), (camp["map_x"], camp["map_y"]))
        if d > RAID_REACH or (best and d >= best[0]):
            continue
        if tribute_of(conn, horde["id"], v["kingdom_id"]):
            continue                                            # it pays: it is spared
        best = (d, dict(v))
    return best[1] if best else None


def _slain(conn, npc_id, city_id, cause):
    from Data.Database.repository import get_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (npc_id,))
    npc = get_npc(conn, npc_id)
    death_event(conn, npc, cause, city_id=city_id)
    apply_grief(conn, npc)


def raid(conn, world_id, horde, village):
    """One raid, fought out. Returns its report."""
    from Data.Combat import melee
    from Data.Monsters.raids import defenders_of, FAMILY_SIZE
    from Data.Database.repository import create_event, add_treasury
    from Data.NPCS.npc_generator import generate_npc
    band_npcs = riders(conn, horde["id"])
    random.shuffle(band_npcs)
    band_npcs = band_npcs[:max(MIN_RAIDERS, round(len(band_npcs) * RAID_SHARE))]
    band = []
    for npc in band_npcs:
        skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
        f = melee.person_fighter(npc, side="riders", skills=skills)
        f.to_hit += CHARGE
        band.append(f)
    defenders = defenders_of(conn, village)
    result = melee.battle(band, defenders) if defenders else {"winner": "a", "dead_a": [], "dead_b": [], "rounds": 0}
    won = result["winner"] == "a"

    camp_id = horde["capital_city_id"]
    for f in result["dead_a"]:
        _slain(conn, f.ref, camp_id, f"slain raiding {village['name']}")
    named_dead = [f.ref for f in result["dead_b"] if f.ref is not None]
    militia_dead = sum(1 for f in result["dead_b"] if f.ref is None)
    for npc_id in named_dead:
        _slain(conn, npc_id, village["id"], f"Horde raid")
    if militia_dead:
        conn.execute("UPDATE cities SET households = MAX(0, households - ?) WHERE id = ?", (militia_dead / FAMILY_SIZE, village["id"]))

    food = gold = 0.0
    captives = burned = 0
    if won:
        market = conn.execute("SELECT amount FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (village["id"],)).fetchone()
        market = market[0] if market else 0.0
        store = conn.execute("SELECT household_store, households, wealth FROM cities WHERE id = ?", (village["id"],)).fetchone()
        food = round(market * FOOD_TAKEN + (store["household_store"] or 0) * STORE_TAKEN, 2)
        gold = round(max(0.0, store["wealth"] or 0) * GOLD_TAKEN, 2)
        families = store["households"] or 0
        captives = min(MAX_CAPTIVES, max(1, round(families * CAPTIVES))) if families >= 10 else 0
        burned = round((store["households"] or 0) * BURNED, 2)
        conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Food'",
                     (round(market * FOOD_TAKEN, 2), village["id"]))
        conn.execute("""UPDATE cities SET household_store = household_store * ?, wealth = wealth - ?,
                        households = MAX(0, households - ?) WHERE id = ?""",
                     (1 - STORE_TAKEN, gold, captives + burned, village["id"]))
        conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
                        ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                     (camp_id, food))
        if gold:
            add_treasury(conn, horde["id"], gold)
        for _ in range(captives):                       # the captives are carried off to the camp
            npc = generate_npc(conn, camp_id, None)
            conn.execute("UPDATE npcs SET profession = 'Laborer', workplace_id = NULL WHERE id = ?", (npc["id"],))

    dead = len(named_dead) + militia_dead
    kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (village["kingdom_id"],)).fetchone()[0]
    if won:
        text = (f"Riders of {horde['name']} fell on {village['name']} in {kingdom}: {dead} villagers killed, "
                f"{food:,.0f} Food and {gold:,.0f} gold carried off, {captives} families taken and the fields burned; "
                f"{len(result['dead_a'])} riders fell.")
    else:
        text = f"Riders of {horde['name']} fell on {village['name']} in {kingdom} and were driven off: {len(result['dead_a'])} riders and {dead} villagers fell."
    create_event(conn, "Horde Raid", text, city_id=village["id"], kingdom_id=village["kingdom_id"])
    if dead >= CHRONICLED or (won and captives):
        _chronicle(conn, world_id, village["kingdom_id"], "Horde Raid", "Horde Raid (sacked)" if won else "Horde Raid (repelled)",
                   {"kingdom": kingdom, "village": village["name"], "other": horde["name"], "dead": dead}, (horde["id"], village["id"]))
    conn.commit()
    return {"horde": horde["id"], "village": village["id"], "kingdom": village["kingdom_id"], "won": won, "band": len(band),
            "riders_fallen": len(result["dead_a"]), "dead": dead, "food": food, "gold": gold, "captives": captives}


def raids_suffered(conn, horde_id, kingdom_id, since_day):
    return conn.execute(
        """SELECT COUNT(*) FROM events WHERE type = 'Horde Raid' AND kingdom_id = ? AND day >= ?
           AND description LIKE ?""", (kingdom_id, since_day,
                                       "Riders of " + conn.execute("SELECT name FROM kingdoms WHERE id = ?", (horde_id,)).fetchone()[0] + " fell on%")).fetchone()[0]


def danegeld_chance(conn, horde_id, kingdom, day):
    chance = DANEGELD_CHANCE + DANEGELD_PER_RAID * max(0, raids_suffered(conn, horde_id, kingdom["id"], day - 720) - 1)
    row = conn.execute("SELECT boldness FROM npcs WHERE id = ?", (kingdom["ruler_id"],)).fetchone() if kingdom.get("ruler_id") else None
    if row and row[0] is not None:
        chance -= BOLDNESS_SWAY * row[0] / 100
    return max(0.0, min(1.0, chance))


def horde_raids(conn, world_id, month):
    """Monthly, in summer: each horde may ride on a village; a sacked kingdom may pay danegeld. Returns the raid reports."""
    from Data.Government.empire import submit, tribute_of
    from Data.Government.edicts import today
    if month not in RAID_MONTHS:
        return []
    day = today(conn, world_id)
    reports = []
    for horde in hordes(conn, world_id):
        if random.random() >= RAID_CHANCE or len(riders(conn, horde["id"])) < MIN_RAIDERS:
            continue
        village = _target(conn, world_id, horde)
        if not village:
            continue
        report = raid(conn, world_id, horde, village)
        reports.append(report)
        if report["won"] and not tribute_of(conn, horde["id"], village["kingdom_id"]):
            victim = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (village["kingdom_id"],)).fetchone())
            if random.random() < danegeld_chance(conn, horde["id"], victim, day):
                subjects = conn.execute(
                    """SELECT (SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1)
                              + (SELECT COALESCE(SUM(households), 0) FROM cities WHERE kingdom_id = ?) * 5""",
                    (victim["id"], victim["id"])).fetchone()[0]
                submit(conn, world_id, horde, victim, "danegeld", day, owed=subjects * DANEGELD_PER_HEAD)
                report["danegeld"] = True
    conn.commit()
    return reports