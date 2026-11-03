import json
import math
import random
from pathlib import Path

# Regions are natural areas of land, not political ones -- a region can
# straddle a kingdom border, as a river valley does. Each has a terrain, a
# climate, maybe a river, and a farmland quality that says how well it grows
# food. Every settlement belongs to the region it sits in. Nothing is drawn;
# the visual map comes later.

DATA_DIR = Path(__file__).resolve().parents[1]
REGION_NAMES = DATA_DIR / "Names" / "RegionNames.json"

# Roughly how many map units across a region is. The map is cut into a grid of
# cells this size, and each region's center is placed somewhere inside its
# cell, so a bigger map simply gets more regions.
REGION_SIZE = 250

# Regions whose centers are within this many cells of each other are
# neighbors (diagonals included).
NEIGHBOR_CELLS = 1.6

# Elevation and moisture start random and are then averaged with the
# neighbors this many times, so land forms ranges, fens and plains rather
# than a random patchwork.
SMOOTHING_PASSES = 3

# Terrain from elevation and moisture, both ranked 0-1 so these are shares:
# the highest 12% of the land is Mountains, the next 18% Hills. Moisture is
# then ranked among the remaining LOWLANDS only, so their mix is reliable:
# the wettest 15% are Marsh, the next 30% Forest, the driest 15% Steppe, and
# the rest Plains.
MOUNTAINS_ABOVE = 0.88
HILLS_ABOVE = 0.70
MARSH_ABOVE = 0.85
FOREST_ABOVE = 0.55
STEPPE_BELOW = 0.15

# How well each kind of land grows food (1.0 = good open farmland), scaled by
# climate, plus a bonus for a river (floodplain soil, water). Steppe is poor
# for fields but good pasture -- for herds and horse lords, later.
TERRAIN_FARMLAND = {
    "Plains": 1.0, "Forest": 0.6, "Hills": 0.6, "Steppe": 0.4, "Marsh": 0.3, "Mountains": 0.15,
}
CLIMATE_FARMLAND = {"Cold": 0.7, "Temperate": 1.0, "Warm": 0.9}
RIVER_FARMLAND_BONUS = 0.25

# One river for about every this many regions. Rivers rise in the highest
# ground and flow downhill, region by region.
REGIONS_PER_RIVER = 16


def _terrain(elevation, moisture):
    if elevation >= MOUNTAINS_ABOVE:
        return "Mountains"
    if elevation >= HILLS_ABOVE:
        return "Hills"
    if moisture >= MARSH_ABOVE:
        return "Marsh"
    if moisture >= FOREST_ABOVE:
        return "Forest"
    if moisture <= STEPPE_BELOW:
        return "Steppe"
    return "Plains"


def _climate(y, height):
    """North (top of the map) is cold, the middle temperate, the south warm."""
    if y < height / 3:
        return "Cold"
    if y < 2 * height / 3:
        return "Temperate"
    return "Warm"


def _rank(values):
    """Each value's position among all of them, 0 (lowest) to 1 (highest)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for position, i in enumerate(order):
        ranks[i] = position / max(1, len(values) - 1)
    return ranks


def farmland_for(terrain, climate, has_river):
    return round(TERRAIN_FARMLAND[terrain] * CLIMATE_FARMLAND[climate] + (RIVER_FARMLAND_BONUS if has_river else 0), 2)


def region_neighbors(regions, cell_size=REGION_SIZE):
    """{region id: [neighbor ids]} from region centers."""

    reach = NEIGHBOR_CELLS * cell_size
    neighbors = {r["id"]: [] for r in regions}

    for a in regions:
        for b in regions:
            if a["id"] != b["id"] and math.dist((a["center_x"], a["center_y"]), (b["center_x"], b["center_y"])) <= reach:
                neighbors[a["id"]].append(b["id"])

    return neighbors


def _unique_names(count, terrains):
    names = json.load(open(REGION_NAMES, encoding="utf-8"))
    used = set()
    result = []

    for terrain in terrains:
        for attempt in range(200):
            name = random.choice(names["prefixes"]) + random.choice(names["words"][terrain])
            if attempt >= 100:
                name += f" {attempt - 98}"          # every pairing taken: number it
            if name not in used:
                break
        used.add(name)
        result.append(name)

    return result


def generate_regions(conn, world_id, width, height, size=REGION_SIZE):
    """
    Cuts a width x height map into regions about `size` across and gives
    each its land: elevation and moisture (smoothed so neighbors resemble
    each other), terrain, climate by latitude, rivers flowing downhill from
    the highest ground, and farmland quality. Returns the new regions.
    """

    cols, rows = max(1, round(width / size)), max(1, round(height / size))
    cell_w, cell_h = width / cols, height / rows

    centers = [
        (round((c + random.uniform(0.2, 0.8)) * cell_w), round((r + random.uniform(0.2, 0.8)) * cell_h))
        for r in range(rows) for c in range(cols)
    ]
    count = len(centers)
    on_edge = [r in (0, rows - 1) or c in (0, cols - 1) for r in range(rows) for c in range(cols)]

    reach = NEIGHBOR_CELLS * max(cell_w, cell_h)
    neighbors = [
        [j for j in range(count) if j != i and math.dist(centers[i], centers[j]) <= reach]
        for i in range(count)
    ]

    def smoothed(values):
        for _ in range(SMOOTHING_PASSES):
            values = [
                0.5 * values[i] + 0.5 * (sum(values[j] for j in neighbors[i]) / len(neighbors[i]) if neighbors[i] else values[i])
                for i in range(count)
            ]
        return _rank(values)

    elevation = smoothed([random.random() for _ in range(count)])
    moisture = smoothed([random.random() for _ in range(count)])

    # rank moisture among the lowlands alone (see the shares above)
    lowland = [i for i in range(count) if elevation[i] < HILLS_ABOVE]
    lowland_moisture = dict(zip(lowland, _rank([moisture[i] for i in lowland])))

    terrain = [_terrain(elevation[i], lowland_moisture.get(i, moisture[i])) for i in range(count)]
    climate = [_climate(centers[i][1], height) for i in range(count)]

    # Rivers: rise in the highest regions not already on a river, and flow to
    # the lowest neighbor for as long as there is a lower one, stopping at the
    # map's edge (the sea, beyond the map) or a low basin.
    has_river = [False] * count
    sources = sorted(range(count), key=lambda i: -elevation[i])

    for _ in range(max(1, count // REGIONS_PER_RIVER)):
        start = next((i for i in sources if not has_river[i]), None)
        if start is None:
            break
        current = start
        for _ in range(count):
            has_river[current] = True
            if on_edge[current] and current != start:
                break
            lower = [j for j in neighbors[current] if elevation[j] < elevation[current]]
            if not lower:
                break
            current = min(lower, key=lambda j: elevation[j])

    names = _unique_names(count, terrain)

    regions = []
    for i in range(count):
        cursor = conn.execute(
            """
            INSERT INTO regions (world_id, name, center_x, center_y, terrain, climate, elevation, moisture, has_river, farmland)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (world_id, names[i], centers[i][0], centers[i][1], terrain[i], climate[i],
             round(elevation[i], 3), round(moisture[i], 3), int(has_river[i]),
             farmland_for(terrain[i], climate[i], has_river[i]))
        )
        regions.append({"id": cursor.lastrowid, "name": names[i], "terrain": terrain[i]})

    return regions


def get_regions(conn, world_id):
    return [dict(row) for row in conn.execute("SELECT * FROM regions WHERE world_id = ? ORDER BY id", (world_id,))]


def assign_settlements_to_regions(conn, world_id):
    """Puts every settlement of this world that has no region yet into the
    region it sits in (the one whose center is nearest). Returns how many."""

    regions = get_regions(conn, world_id)

    if not regions:
        return 0

    cities = conn.execute(
        """
        SELECT c.id, c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.region_id IS NULL
        """,
        (world_id,)
    ).fetchall()

    for city in cities:
        nearest = min(regions, key=lambda r: (r["center_x"] - city["map_x"]) ** 2 + (r["center_y"] - city["map_y"]) ** 2)
        conn.execute("UPDATE cities SET region_id = ? WHERE id = ?", (nearest["id"], city["id"]))

    return len(cities)


def ensure_regions(conn, world_id):
    """
    Gives a world its regions if it has none -- a new world, or one made
    before regions existed -- and puts any settlement without a region into
    one. Safe to call every start: does nothing for a world already done.
    Returns how many regions were created (0 if it already had them).
    """

    from Data.Kingdoms.kingdom_generator import MAP_WIDTH, MAP_HEIGHT

    created = 0

    if not conn.execute("SELECT 1 FROM regions WHERE world_id = ? LIMIT 1", (world_id,)).fetchone():
        created = len(generate_regions(conn, world_id, MAP_WIDTH, MAP_HEIGHT))

    assign_settlements_to_regions(conn, world_id)
    conn.commit()

    return created