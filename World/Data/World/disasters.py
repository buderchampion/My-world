"""
Disasters: fires, floods, earthquakes and locusts.

Fire        each month a town may burn (FIRE_CHANCE, more in a big town and in
            summer). The fire takes a share of its buildings down to rubble
            (their condition falls; they are repaired with Wood and Stone as
            ever, and work poorly until then), a share of its stores, and a
            few lives.
Flood       in spring above all, a settlement on a river may flood: its stores
            (a village's household store, a town's market) are spoiled in part,
            a few drown, and the region's next harvest comes in smaller.
Earthquake  rare: the land shakes around one region. Every settlement within
            QUAKE_REACH loses condition on its buildings (walls too), the
            nearer the worse, and some die under the stones.
Locusts     now and then a swarm settles on warm, open land: that region and
            its neighbors within LOCUST_REACH reap only a share of their harvest.

Every disaster is mourned (its dead), news of it makes its town afraid for a
while (Data.NPCS.contentment), the worst are chronicled, and a great one calls
the ruler to a crisis review (the granary, lower taxes). Lost stores and lost
harvests raise prices through the markets as any shortage does.
"""

from __future__ import annotations

import math
import random

FIRE_CHANCE = 0.004            # a month, for a town of FIRE_TOWN_SIZE named people (a capital burns more often)...
FIRE_TOWN_SIZE = 300
FIRE_SUMMER = 2.0              # ...twice as likely in summer
FIRE_BURNS = (0.05, 0.30)      # the share of buildings a fire takes
FIRE_STORES = (0.05, 0.25)     # the share of each store it takes
FIRE_DEATHS = (0.0, 0.02)      # the share of townsfolk it kills
GREAT_FIRE = 0.22              # a fire that takes this share of a town (or GREAT_FIRE_DEAD lives) is a Great Fire: chronicled
GREAT_FIRE_DEAD = 10

FLOOD_CHANCE = 0.03            # a year, for a settlement on a river (most of it in spring)
FLOOD_SEASONS = {"Spring": 0.6, "Summer": 0.15, "Autumn": 0.15, "Winter": 0.1}
FLOOD_STORES = (0.2, 0.6)      # the share of its stores a flood spoils
FLOOD_DEATHS = (0.0, 0.01)
FLOOD_HARVEST = 0.7            # the region's next harvest, at this share

QUAKE_CHANCE = 0.04            # a year, somewhere in the world
QUAKE_REACH = 600              # map units around the heart of it
QUAKE_DAMAGE = (20, 70)        # condition lost by every building at its heart (less further out)
QUAKE_DEATHS = (0.0, 0.03)

LOCUST_CHANCE = 0.06           # a year, somewhere in the world (warm or temperate, open land)
LOCUST_LANDS = ("Plains", "Steppe")
LOCUST_REACH = 400
LOCUST_HARVEST = 0.35          # a stripped region reaps this share

AFRAID_DAYS = 30               # a stricken town is afraid this long (Data.NPCS.contentment)
CHRONICLE_DEAD = 5             # any disaster that kills this many of the named is chronicled


def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def _season(conn, world_id):
    from Data.Simulation.clock import SEASON_BY_MONTH
    month = conn.execute("SELECT clock_month FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
    return SEASON_BY_MONTH.get(month, "Spring")


def _towns(conn, world_id, where="1 = 1", args=()):
    return [dict(r) for r in conn.execute(
        f"""SELECT c.id, c.name, c.type, c.kingdom_id, c.region_id, c.map_x, c.map_y, c.households, k.name kingdom,
                   (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1) people
            FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? AND k.status != 'Fallen' AND {where}""", (world_id, *args))]


def _kill(conn, city, share, cause):
    """Some of a town's people die of the disaster. Returns how many."""
    from Data.Database.repository import get_npc, save_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    living = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1", (city["id"],))]
    n = min(len(living), int(round(len(living) * share)))
    for npc_id in random.sample(living, n):
        npc = get_npc(conn, npc_id)
        if city["type"] == "Village" and living:
            conn.execute("UPDATE cities SET households = MAX(0, households - households / ?) WHERE id = ?", (len(living), city["id"]))
        npc.update(alive=False, health=0, workplace=None, home=None)
        save_npc(conn, npc)
        death_event(conn, npc, cause, city_id=city["id"])
        apply_grief(conn, npc)
    return n


def _spoil(conn, city_id, share):
    """A share of every store in town is lost (and of a village's household store)."""
    conn.execute("UPDATE city_resources SET amount = ROUND(amount * ?, 2) WHERE city_id = ?", (1 - share, city_id))
    conn.execute("UPDATE cities SET household_store = household_store * ? WHERE id = ?", (1 - share, city_id))


def _damage(conn, city_id, share, lost):
    """`share` of a town's buildings lose `lost` condition (a range: each its own). Returns how many were hit."""
    ids = [r[0] for r in conn.execute("SELECT id FROM buildings WHERE city_id = ? AND type != 'Slums'", (city_id,))]
    hit = random.sample(ids, min(len(ids), max(1 if ids and share > 0 else 0, int(round(len(ids) * share)))))
    for b in hit:
        conn.execute("UPDATE buildings SET condition = MAX(0, condition - ?) WHERE id = ?", (random.uniform(*lost), b))
    return len(hit)


def _afraid(conn, city_id, day):
    conn.execute("UPDATE cities SET afraid_until = ? WHERE id = ?", (day + AFRAID_DAYS, city_id))


def _chronicle(conn, world_id, kingdom_id, key, fields, seed):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom_id, "Disaster", key, year, season, fields, seed_parts=seed)


def _report(conn, kind, text, city_id, kingdom_id=None):
    from Data.Database.repository import create_event
    create_event(conn, kind, text, city_id=city_id, kingdom_id=kingdom_id)


def fire(conn, world_id, city, day=None):
    """A fire in this town. Returns its report."""
    day = _today(conn, world_id) if day is None else day
    burned = random.uniform(*FIRE_BURNS)
    hit = _damage(conn, city["id"], burned, (60, 100))
    _spoil(conn, city["id"], random.uniform(*FIRE_STORES))
    dead = _kill(conn, city, random.uniform(*FIRE_DEATHS), "fire")
    _afraid(conn, city["id"], day)
    great = burned >= GREAT_FIRE or dead >= GREAT_FIRE_DEAD
    _report(conn, "Fire", f"{'A great fire' if great else 'Fire'} swept {city['name']}: {hit} buildings burned"
            + (f", {dead} dead." if dead else "."), city["id"])
    if great:
        _chronicle(conn, world_id, city["kingdom_id"], "Great Fire", {"kingdom": city["kingdom"], "city": city["name"], "dead": dead},
                   (city["id"], day))
    return {"kind": "fire", "city": city["id"], "buildings": hit, "dead": dead, "great": great}


def flood(conn, world_id, city, day=None):
    """A river floods this settlement. Returns its report."""
    day = _today(conn, world_id) if day is None else day
    _spoil(conn, city["id"], random.uniform(*FLOOD_STORES))
    hit = _damage(conn, city["id"], 0.3, (10, 40))
    dead = _kill(conn, city, random.uniform(*FLOOD_DEATHS), "flood")
    _afraid(conn, city["id"], day)
    year = conn.execute("SELECT clock_year FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
    blight(conn, city["region_id"], year, "flood", FLOOD_HARVEST)
    _report(conn, "Flood", f"The river flooded {city['name']}" + (f"; {dead} drowned." if dead else "."), city["id"])
    if dead >= CHRONICLE_DEAD or city["type"] != "Village":
        _chronicle(conn, world_id, city["kingdom_id"], "Flood", {"kingdom": city["kingdom"], "city": city["name"], "dead": dead},
                   (city["id"], day))
    return {"kind": "flood", "city": city["id"], "buildings": hit, "dead": dead}


def blight(conn, region_id, year, kind, factor):
    """This region's harvest this year is cut to `factor` (the worst blight counts)."""
    if region_id is None:
        return
    row = conn.execute("SELECT factor FROM region_blights WHERE region_id = ? AND year = ?", (region_id, year)).fetchone()
    if row is None:
        conn.execute("INSERT INTO region_blights (region_id, year, kind, factor) VALUES (?, ?, ?, ?)", (region_id, year, kind, factor))
    elif factor < row[0]:
        conn.execute("UPDATE region_blights SET kind = ?, factor = ? WHERE region_id = ? AND year = ?", (kind, factor, region_id, year))


def blights(conn, world_id, year):
    """{region id: harvest factor} of this year's floods and swarms."""
    return {r[0]: r[1] for r in conn.execute(
        """SELECT b.region_id, b.factor FROM region_blights b JOIN regions r ON r.id = b.region_id
           WHERE r.world_id = ? AND b.year = ?""", (world_id, year))}


def earthquake(conn, world_id, region=None, day=None):
    """The land shakes around a region. Returns its report."""
    day = _today(conn, world_id) if day is None else day
    if region is None:
        regions = conn.execute("SELECT id, name, center_x, center_y FROM regions WHERE world_id = ?", (world_id,)).fetchall()
        if not regions:
            return None
        region = random.choice(regions)
    x, y = region["center_x"], region["center_y"]
    struck, dead_total, dead_in = [], 0, {}
    for city in _towns(conn, world_id):
        if city["map_x"] is None:
            continue
        d = math.dist((x, y), (city["map_x"], city["map_y"]))
        if d > QUAKE_REACH:
            continue
        near = 1 - d / QUAKE_REACH                    # 1 at the heart, 0 at the edge
        lo, hi = QUAKE_DAMAGE
        _damage(conn, city["id"], 0.3 + 0.7 * near, (lo * near, hi * near))
        dead = _kill(conn, city, random.uniform(*QUAKE_DEATHS) * near, "earthquake")
        _afraid(conn, city["id"], day)
        struck.append(city)
        dead_total += dead
        dead_in[city["kingdom_id"]] = dead_in.get(city["kingdom_id"], 0) + dead
        _report(conn, "Earthquake", f"The earth shook at {city['name']}" + (f"; {dead} died under the stones." if dead else "."), city["id"])
    kingdoms = {}
    for c in struck:
        kingdoms.setdefault(c["kingdom_id"], c)
    for kid, c in kingdoms.items():
        _chronicle(conn, world_id, kid, "Earthquake", {"kingdom": c["kingdom"], "city": c["name"],
                                                       "dead": dead_in.get(kid, 0)}, (kid, day))
    return {"kind": "earthquake", "region": region["id"], "towns": [c["id"] for c in struck], "dead": dead_total,
            "kingdoms": list(kingdoms)}


def locusts(conn, world_id, region=None, day=None):
    """A swarm strips a warm, open region and its neighbors. Returns its report."""
    day = _today(conn, world_id) if day is None else day
    if region is None:
        open_land = conn.execute(
            f"""SELECT id, name, center_x, center_y FROM regions WHERE world_id = ? AND climate != 'Cold'
                AND terrain IN ({','.join('?' * len(LOCUST_LANDS))})""", (world_id, *LOCUST_LANDS)).fetchall()
        if not open_land:
            return None
        region = random.choice(open_land)
    year = conn.execute("SELECT clock_year FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
    stripped = []
    for r in conn.execute("SELECT id, center_x, center_y FROM regions WHERE world_id = ?", (world_id,)):
        if math.dist((r["center_x"], r["center_y"]), (region["center_x"], region["center_y"])) <= LOCUST_REACH:
            blight(conn, r["id"], year, "locusts", LOCUST_HARVEST)
            stripped.append(r["id"])
    marks = ",".join("?" * len(stripped))
    hit = _towns(conn, world_id, f"c.region_id IN ({marks})", stripped) if stripped else []
    kingdoms = {}
    for c in hit:
        kingdoms.setdefault(c["kingdom_id"], c)
        _afraid(conn, c["id"], day)
    for kid, c in kingdoms.items():
        _chronicle(conn, world_id, kid, "Locusts", {"kingdom": c["kingdom"], "city": c["name"]}, (kid, day))
        _report(conn, "Locusts", f"Locusts settled on the fields around {c['name']}.", c["id"], kid)
    return {"kind": "locusts", "region": region["id"], "regions": stripped, "kingdoms": list(kingdoms)}


def disasters_month(conn, world_id):
    """Monthly: fires, floods, and (rarely) an earthquake or a swarm. Returns the reports;
    kingdoms struck hard are called to a crisis review."""
    from Data.Government.edicts import review
    day = _today(conn, world_id)
    season = _season(conn, world_id)
    reports = []
    for city in _towns(conn, world_id, "c.type != 'Village'"):
        size = max(0.5, city["people"] / FIRE_TOWN_SIZE)
        if random.random() < FIRE_CHANCE * size * (FIRE_SUMMER if season == "Summer" else 1.0):
            reports.append(fire(conn, world_id, city, day))
    flood_share = FLOOD_SEASONS.get(season, 0.25) / 3            # this month's part of the season's share
    for city in _towns(conn, world_id, "c.region_id IN (SELECT id FROM regions WHERE has_river = 1)"):
        if random.random() < FLOOD_CHANCE * flood_share:
            reports.append(flood(conn, world_id, city, day))
    if random.random() < QUAKE_CHANCE / 12:
        q = earthquake(conn, world_id, day=day)
        if q:
            reports.append(q)
    if season in ("Spring", "Summer") and random.random() < LOCUST_CHANCE / 6:
        l = locusts(conn, world_id, day=day)
        if l:
            reports.append(l)
    conn.commit()
    stricken = {k for r in reports for k in r.get("kingdoms", [])} | \
        {conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (r["city"],)).fetchone()[0]
         for r in reports if r.get("great") or r.get("dead", 0) >= CHRONICLE_DEAD and r.get("city")}
    if stricken:
        review(conn, world_id, kingdom_ids=stricken, crisis=True)
    return reports


def afraid_towns(conn, day):
    """Towns still shaken by a disaster."""
    return {r[0] for r in conn.execute("SELECT id FROM cities WHERE afraid_until >= ?", (day,))}