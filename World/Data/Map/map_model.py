"""
The map's view of the world: everything it draws, read from world.db in
read-only mode (the map can never change your world). Pure Python, no
Pygame, so it can be tested on its own.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

MAP_SIZE = 2000                       # worlds made before the size was recorded were 2000 x 2000 map units
REGION_SIZE = 250                     # each region is about this wide (Data.World.regions)


@dataclass
class Region:
    id: int
    name: str
    x: float
    y: float
    terrain: str
    climate: str
    elevation: float
    moisture: float
    has_river: bool
    farmland: float


@dataclass
class Settlement:
    id: int
    name: str
    kind: str                         # Capital, City, Town or Village
    x: float
    y: float
    kingdom_id: int
    province_id: int | None
    population: int
    households: float


@dataclass
class Kingdom:
    id: int
    name: str
    color: tuple
    kind: str                         # Crown or Republic
    capital_id: int | None
    fallen: bool


@dataclass
class Road:
    a: int
    b: int
    kind: str                         # Capital Road or Trade Route


@dataclass
class WorldView:
    world_id: int
    world_name: str
    size: float
    date: str
    seed: int | None
    regions: list = field(default_factory=list)
    settlements: dict = field(default_factory=dict)      # id -> Settlement
    kingdoms: dict = field(default_factory=dict)         # id -> Kingdom
    provinces: dict = field(default_factory=dict)        # id -> (name, kingdom_id, seat_id)
    roads: list = field(default_factory=list)
    rivers: list = field(default_factory=list)           # (from region, to region) segments, flowing downhill
    lairs: list = field(default_factory=list)            # (id, monster, kind, x, y, count) -- monster lairs


def hex_to_rgb(text, fallback=(150, 150, 150)):
    try:
        text = text.lstrip("#")
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError):
        return fallback


def open_world(path: Path) -> sqlite3.Connection:
    """The world database, read-only: the map only ever looks."""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def river_courses(regions):
    """Each river region flows to its lowest neighbouring river region (rivers run downhill, Data.World.regions)."""
    by_id = {r.id: r for r in regions}
    rivers = [r for r in regions if r.has_river]
    segments = []
    for r in rivers:
        lower = [o for o in rivers if o.id != r.id and o.elevation < r.elevation
                 and math.dist((r.x, r.y), (o.x, o.y)) <= REGION_SIZE * 1.6]
        if lower:
            to = min(lower, key=lambda o: (o.elevation, math.dist((r.x, r.y), (o.x, o.y))))
            segments.append((r.id, to.id))
    return segments


def load_world(conn: sqlite3.Connection) -> WorldView:
    world = conn.execute("SELECT * FROM worlds ORDER BY id LIMIT 1").fetchone()
    cols = world.keys()
    season = {1: "Spring", 2: "Spring", 3: "Spring", 4: "Summer", 5: "Summer", 6: "Summer",
              7: "Autumn", 8: "Autumn", 9: "Autumn", 10: "Winter", 11: "Winter", 12: "Winter"}.get(world["clock_month"], "?")
    size = world["map_width"] if "map_width" in cols and world["map_width"] else MAP_SIZE
    view = WorldView(world["id"], world["name"], size, f"Year {world['clock_year']}, {season}",
                     world["seed"] if "seed" in cols else None)

    view.regions = [Region(r["id"], r["name"], r["center_x"], r["center_y"], r["terrain"], r["climate"],
                           r["elevation"], r["moisture"], bool(r["has_river"]), r["farmland"])
                    for r in conn.execute("SELECT * FROM regions WHERE world_id = ?", (view.world_id,))]
    view.rivers = river_courses(view.regions)

    for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ?", (view.world_id,)):
        keys = k.keys()
        view.kingdoms[k["id"]] = Kingdom(k["id"], k["name"], hex_to_rgb(k["color"]), k["type"] if "type" in keys else "Crown",
                                         k["capital_city_id"], ("status" in keys and k["status"] == "Fallen"))
    for c in conn.execute("SELECT * FROM cities WHERE kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ?)", (view.world_id,)):
        view.settlements[c["id"]] = Settlement(c["id"], c["name"], c["type"], c["map_x"], c["map_y"], c["kingdom_id"],
                                               c["province_id"], c["population"] or 0, c["households"] or 0)
    for p in conn.execute("SELECT id, name, kingdom_id, seat_city_id FROM provinces"):
        view.provinces[p["id"]] = (p["name"], p["kingdom_id"], p["seat_city_id"])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'monster_lairs'").fetchone():
        view.lairs = [tuple(r) for r in conn.execute(
            "SELECT id, monster, kind, map_x, map_y, count FROM monster_lairs WHERE world_id = ? AND status = 'Active'", (view.world_id,))]
    view.roads = [Road(r["city_a_id"], r["city_b_id"], r["kind"]) for r in conn.execute("SELECT * FROM roads")
                  if r["city_a_id"] in view.settlements and r["city_b_id"] in view.settlements]
    return view


# --- things on the move (part 2) ----------------------------------------------------------
WALL_STRENGTH = 1000                  # siege works that breach a town's walls (Data.Government.campaigns)
DISTANCE_PER_TRAVEL_DAY = 90          # road speed (Data.Infrastructure.roads)


@dataclass
class ArmyMark:
    id: int
    name: str
    kingdom_id: int
    soldiers: int
    status: str                       # Mustered, Marching or Encamped
    x: float
    y: float
    path: list                        # the road still ahead, as map points (empty unless marching)


@dataclass
class SiegeMark:
    city_id: int
    attacker_id: int
    progress: float                   # 0..1: how far the siege works have risen against the walls
    hungry_days: int


@dataclass
class CaravanMark:
    x: float
    y: float
    kind: str                         # Trade or Supply
    kingdom_id: int
    resource: str


@dataclass
class Movement:
    armies: list = field(default_factory=list)
    sieges: list = field(default_factory=list)
    caravans: list = field(default_factory=list)
    occupied: dict = field(default_factory=dict)          # city id -> occupier kingdom id


def _road_days(conn):
    days = {}
    for r in conn.execute("SELECT city_a_id, city_b_id, travel_days FROM roads"):
        days[(r[0], r[1])] = days[(r[1], r[0])] = max(1, r[2] or 1)
    return days


def _along(view, road_days, from_id, to_id, days_left):
    """Where a traveller is on the road from one settlement to the next, `days_left` days from arriving."""
    a, b = view.settlements.get(from_id), view.settlements.get(to_id)
    if a is None:
        return None
    if b is None:
        return a.x, a.y
    total = road_days.get((from_id, to_id)) or max(1, round(math.dist((a.x, a.y), (b.x, b.y)) / DISTANCE_PER_TRAVEL_DAY))
    done = max(0.0, min(1.0, 1 - days_left / total))
    return a.x + (b.x - a.x) * done, a.y + (b.y - a.y) * done


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def load_movement(conn, view) -> Movement:
    """Armies (where they are, the road ahead), sieges, caravans on the roads, and occupied towns."""
    import json
    moving = Movement()
    road_days = _road_days(conn)
    if _has(conn, "armies"):
        sizes = dict(conn.execute("SELECT army_id, COUNT(*) FROM army_members GROUP BY army_id").fetchall()) \
            if _has(conn, "army_members") else {}
        for a in conn.execute("SELECT * FROM armies WHERE status != 'Disbanded'"):
            if a["status"] == "Marching" and a["next_city_id"]:
                pos = _along(view, road_days, a["location_city_id"], a["next_city_id"], a["leg_days_remaining"])
                ahead = [a["next_city_id"]] + [c for c in json.loads(a["route"] or "[]")]
                path = [pos] + [(view.settlements[c].x, view.settlements[c].y) for c in ahead if c in view.settlements]
            else:
                here = view.settlements.get(a["location_city_id"])
                pos, path = ((here.x, here.y) if here else None), []
            if pos:
                moving.armies.append(ArmyMark(a["id"], a["name"], a["kingdom_id"], sizes.get(a["id"], 0), a["status"], pos[0], pos[1], path))
    if _has(conn, "campaigns"):
        for c in conn.execute("SELECT * FROM campaigns WHERE status = 'Besieging'"):
            keys = c.keys()
            moving.sieges.append(SiegeMark(c["target_city_id"], c["attacker_kingdom_id"],
                                           min(1.0, (c["siege_progress"] if "siege_progress" in keys else 0) / WALL_STRENGTH),
                                           c["starving_days"] if "starving_days" in keys else 0))
    for c in conn.execute("SELECT * FROM caravans WHERE status = 'En Route'"):
        a, b = view.settlements.get(c["origin_city_id"]), view.settlements.get(c["destination_city_id"])
        if a and b:
            total = road_days.get((a.id, b.id)) or max(1, round(math.dist((a.x, a.y), (b.x, b.y)) / DISTANCE_PER_TRAVEL_DAY))
            done = max(0.0, min(1.0, 1 - c["days_remaining"] / total))
            moving.caravans.append(CaravanMark(a.x + (b.x - a.x) * done, a.y + (b.y - a.y) * done, "Trade", c["seller_kingdom_id"], c["resource"]))
    if _has(conn, "supply_caravans"):
        kingdom_of = dict(conn.execute("SELECT id, kingdom_id FROM armies").fetchall())
        for s in conn.execute("SELECT * FROM supply_caravans WHERE status IN ('En Route', 'Returning')"):
            pos = _along(view, road_days, s["location_city_id"], s["next_city_id"], s["leg_days_remaining"]) if s["next_city_id"] \
                else ((view.settlements[s["location_city_id"]].x, view.settlements[s["location_city_id"]].y) if s["location_city_id"] in view.settlements else None)
            if pos:
                moving.caravans.append(CaravanMark(pos[0], pos[1], "Supply", kingdom_of.get(s["army_id"], 0), "supplies"))
    if _has(conn, "occupations"):
        moving.occupied = dict(conn.execute("SELECT city_id, occupier_kingdom_id FROM occupations WHERE status = 'Occupied'").fetchall())
    return moving


def realm_signature(view):
    """What decides the realm layer: which kingdom and county each settlement belongs to. If it changes, redraw."""
    return tuple(sorted((s.id, s.kingdom_id, s.province_id) for s in view.settlements.values())) + \
        tuple(sorted((k.id, k.fallen) for k in view.kingdoms.values()))


# --- details for a clicked town, kingdom or army (part 3) -----------------------------------
def _name(conn, npc_id):
    """'King Joshua Reed, 47' -- a person's title, name and age (or None)."""
    if not npc_id:
        return None
    n = conn.execute("SELECT name, title, age, alive FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if n is None:
        return None
    return f"{(n['title'] + ' ') if n['title'] else ''}{n['name']}, {n['age']}" + ("" if n["alive"] else " (dead)")


def _peoples(conn, where, args):
    """'dwarves 81%, gnomes 9%, humans 6%' (worlds from before the peoples: None)."""
    try:
        rows = conn.execute(f"""SELECT n.race, COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
                                WHERE n.alive = 1 AND {where} GROUP BY n.race ORDER BY COUNT(*) DESC""", args).fetchall()
    except Exception:
        return None
    total = sum(r[1] for r in rows)
    if not total:
        return None
    return ", ".join(f"{r[0] or 'Human'} {r[1] / total:.0%}" for r in rows[:4])


def town_details(conn, city_id):
    c = conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone()
    if c is None:
        return None
    k = conn.execute("SELECT name, type FROM kingdoms WHERE id = ?", (c["kingdom_id"],)).fetchone()
    rows = [("Kingdom", f"{k['name']} ({k['type']})" if k else "-")]
    if c["province_id"]:
        p = conn.execute("SELECT name, lord_id, house FROM provinces WHERE id = ?", (c["province_id"],)).fetchone()
        if p:
            rows.append(("County", p["name"] + (f", {p['house']}" if p["house"] else "")))
            rows.append(("Lord", _name(conn, p["lord_id"]) or "none yet"))
    else:
        rows.append(("County", "crown lands"))
    governor = _name(conn, c["ruler_id"])
    if governor:
        rows.append(("Governed by", governor))
    people = conn.execute("""SELECT COUNT(*), SUM(age < 16), SUM(profession = 'Laborer'), AVG(needs_happiness)
                             FROM npcs WHERE city_id = ? AND alive = 1""", (city_id,)).fetchone()
    rows.append(("People", f"{people[0] or 0:,} named ({people[1] or 0:,} children, {people[2] or 0:,} Laborers)"))
    peoples = _peoples(conn, "n.city_id = ?", (city_id,))
    if peoples:
        rows.append(("Peoples", peoples))
    if c["households"]:
        rows.append(("Households", f"{c['households']:,.0f} farming families (about {c['households'] * 5:,.0f} people)"))
    if people[3] is not None:
        rows.append(("Happiness", f"{people[3]:.0f} of 100"))
    stores = dict(conn.execute("SELECT resource_name, amount FROM city_resources WHERE city_id = ?", (city_id,)).fetchall())
    eaters = (people[0] or 0) + (c["households"] or 0) * 5
    food = stores.get("Food", 0)
    rows.append(("Food", f"{food:,.0f} ({food / eaters:,.0f} days)" if eaters else f"{food:,.0f}"))
    others = sorted(((r, a) for r, a in stores.items() if r != "Food" and a >= 1), key=lambda x: -x[1])[:4]
    if others:
        rows.append(("Stores", ", ".join(f"{a:,.0f} {r}" for r, a in others)))
    rows.append(("Purse", f"{c['wealth']:,.0f} gold" if "wealth" in c.keys() else "-"))
    events = [f"{e['type']}: {e['description']}" for e in conn.execute(
        "SELECT type, description FROM events WHERE city_id = ? ORDER BY id DESC LIMIT 5", (city_id,))]
    return {"title": c["name"], "subtitle": c["type"], "rows": rows, "lists": [("Recent events", events or ["none yet"])]}


def kingdom_details(conn, kingdom_id):
    k = conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    if k is None:
        return None
    keys = k.keys()
    capital = conn.execute("SELECT name, ruler_id FROM cities WHERE id = ?", (k["capital_city_id"],)).fetchone() if k["capital_city_id"] else None
    rows = [("Ruler", _name(conn, capital["ruler_id"]) if capital else "-")]
    if "regent_id" in keys and k["regent_id"]:
        rows.append(("Regent", _name(conn, k["regent_id"])))
    rows.append(("Capital", capital["name"] if capital else "-"))
    rows.append(("Treasury", f"{k['treasury']:,.0f} gold"))
    if "health_loyalty" in keys:
        rows.append(("Health", f"loyalty {k['health_loyalty']:.0f}, unrest {k['health_unrest']:.0f}, stability {k['health_stability']:.0f}"))
    towns = conn.execute("SELECT SUM(type != 'Village'), SUM(type = 'Village'), COALESCE(SUM(households), 0) FROM cities WHERE kingdom_id = ?", (kingdom_id,)).fetchone()
    named = conn.execute("SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1", (kingdom_id,)).fetchone()[0]
    counties = conn.execute("SELECT COUNT(*) FROM provinces WHERE kingdom_id = ?", (kingdom_id,)).fetchone()[0]
    rows.append(("Lands", f"{towns[0] or 0} towns, {towns[1] or 0} villages, {counties} counties"))
    rows.append(("People", f"about {named + towns[2] * 5:,.0f} ({named:,} named)"))
    peoples = _peoples(conn, "c.kingdom_id = ?", (kingdom_id,))
    if peoples:
        rows.append(("Peoples", peoples))
    if _has(conn, "campaigns"):
        wars = conn.execute(
            """SELECT a.name attacker, d.name defender, c.status FROM campaigns c JOIN kingdoms a ON a.id = c.attacker_kingdom_id
               JOIN kingdoms d ON d.id = c.defender_kingdom_id WHERE c.status NOT IN ('Over') AND ? IN (c.attacker_kingdom_id, c.defender_kingdom_id)""",
            (kingdom_id,)).fetchall()
        if wars:
            rows.append(("At war", "; ".join(f"{w['attacker']} against {w['defender']} ({w['status'].lower()})" for w in wars)))
    entries = [f"Year {e['year']}, {e['season']}: {e['text']}" for e in conn.execute(
        "SELECT year, season, text FROM chronicle_entries WHERE kingdom_id = ? ORDER BY id DESC LIMIT 5", (kingdom_id,))] \
        if _has(conn, "chronicle_entries") else []
    status = f"{k['type'] if 'type' in keys else 'Crown'}" + (" (fallen)" if "status" in keys and k["status"] == "Fallen" else "")
    return {"title": k["name"], "subtitle": status, "rows": rows, "lists": [("From its chronicle, latest first", entries or ["nothing written yet"])]}


def army_details(conn, army_id):
    import json
    a = conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
    if a is None:
        return None
    town = lambda i: (conn.execute("SELECT name FROM cities WHERE id = ?", (i,)).fetchone() or ["-"])[0] if i else "-"
    soldiers = conn.execute("SELECT COUNT(*) FROM army_members WHERE army_id = ?", (army_id,)).fetchone()[0] if _has(conn, "army_members") else 0
    k = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (a["kingdom_id"],)).fetchone()
    rows = [("Kingdom", k["name"] if k else "-"), ("Soldiers", f"{soldiers:,}")]
    if a["status"] == "Marching" and a["next_city_id"]:
        ahead = len(json.loads(a["route"] or "[]"))
        rows.append(("Marching", f"from {town(a['location_city_id'])} to {town(a['next_city_id'])}, {a['leg_days_remaining']} day(s) left"
                                 + (f", then {ahead} more stage(s)" if ahead else "")))
    else:
        rows.append(("At", town(a["location_city_id"])))
    rows.append(("Bound for", town(a["destination_city_id"])))
    campaign = conn.execute("SELECT * FROM campaigns WHERE army_id = ? AND status != 'Over' ORDER BY id DESC LIMIT 1", (army_id,)).fetchone() \
        if _has(conn, "campaigns") else None
    if campaign:
        siege = f", siege works {min(1, campaign['siege_progress'] / WALL_STRENGTH):.0%}" if campaign["status"] == "Besieging" else ""
        rows.append(("Campaign", f"{campaign['status']} against {town(campaign['target_city_id'])}{siege}"))
    rows.append(("Wagons", f"{a['food']:,.0f} Food, {a['weapons']:,.0f} Weapons, {a['potions']:,.0f} Potions"
                           + (f", {a['armor']:,.0f} Armor, {a['tools']:,.0f} Tools" if "armor" in a.keys() else "")))
    if a["days_without_food"]:
        rows.append(("Hungry", f"{a['days_without_food']} day(s) without food"))
    return {"title": a["name"], "subtitle": a["status"], "rows": rows, "lists": []}


def lair_details(conn, lair_id):
    """A clicked lair: its monster's statistics from the bestiary."""
    lair = conn.execute("SELECT l.*, r.terrain, r.climate FROM monster_lairs l LEFT JOIN regions r ON r.id = l.region_id WHERE l.id = ?",
                        (lair_id,)).fetchone()
    if lair is None:
        return None
    try:
        from Data.Monsters.bestiary import by_name
    except ImportError:                    # the map run on its own, from its folder
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from Data.Monsters.bestiary import by_name
    m = by_name(lair["monster"]) or {}
    where = f"{lair['terrain'] or '?'} ({lair['climate'] or '?'})" + (", within a kingdom's lands" if lair["within_kingdom_id"] else ", in the wilds")
    rows = [("Lair of", f"{lair['count']} {lair['monster']}"), ("Kind", {"raider": "raider: raids villages near its lair",
            "beast": "beast: falls on travelers", "peaceful": "peaceful: keeps to its lair"}.get(lair["kind"], lair["kind"])),
            ("Where", where), ("Armor class", m.get("armor_class", "-")), ("Hit dice", f"{m.get('hit_dice', '-')}  ({lair['hit_points_each']:.0f} hp each)"),
            ("Attacks", f"{m.get('attacks', '-')}, damage {m.get('damage', '-')}"), ("Moves", m.get("move", "-"))]
    for label, key in (("Special attacks", "special_attacks"), ("Special defenses", "special_defenses"), ("Intelligence", "intelligence"),
                       ("Alignment", "alignment"), ("Size", "size")):
        if m.get(key) and m.get(key).lower() not in ("nil", "none"):
            rows.append((label, m[key]))
    return {"title": lair["monster"], "subtitle": f"Monster lair (Monster Manual, page {m.get('page', '?')})", "rows": rows, "lists": []}