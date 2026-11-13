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