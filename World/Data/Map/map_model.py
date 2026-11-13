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