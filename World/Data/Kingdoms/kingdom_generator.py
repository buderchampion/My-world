from pathlib import Path
import json
import math
import random
from Data.Cities.city_generator import generate_city
from Data.Database.repository import create_kingdom, get_kingdom, save_kingdom

DATA_DIR = Path(__file__).resolve().parent.parent

PREFIXES = DATA_DIR / "Names" / "KingdomPrefixes.json"
SUFFIXES = DATA_DIR / "Names" / "KingdomSuffixes.json"

# The whole world map is treated as an abstract MAP_WIDTH x MAP_HEIGHT
# plane -- these are just coordinate units, not tied to any real unit
# of distance.
MAP_WIDTH = 2000
MAP_HEIGHT = 2000
MAP_MARGIN = 150  # keeps kingdom centers off the very edge

# How far from its kingdom's center a city can be placed.
CAPITAL_JITTER = 40
CITY_SPREAD_RADIUS = 300

# A small palette of visually distinct colors, cycled through so
# kingdoms are easy to tell apart (rather than fully random hex
# values, which can end up muddy or too similar to each other).
KINGDOM_COLORS = [
    "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4",
    "#46F0F0", "#F032E6", "#BFEF45", "#FABEBE", "#469990"
]


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def generate_kingdom_name():

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    return random.choice(prefixes) + random.choice(suffixes)


def random_point_near(center_x, center_y, max_radius):

    angle = random.uniform(0, 2 * math.pi)
    distance = random.uniform(0, max_radius)

    x = center_x + distance * math.cos(angle)
    y = center_y + distance * math.sin(angle)

    return int(x), int(y)


def generate_kingdom(conn, world_id, color="#808080"):

    kingdom = create_kingdom(conn, world_id, generate_kingdom_name(), color)

    center_x = random.randint(MAP_MARGIN, MAP_WIDTH - MAP_MARGIN)
    center_y = random.randint(MAP_MARGIN, MAP_HEIGHT - MAP_MARGIN)

    capital_x, capital_y = random_point_near(center_x, center_y, CAPITAL_JITTER)

    capital = generate_city(conn, kingdom["id"], "Capital", capital_x, capital_y)

    kingdom["capital"] = capital["id"]

    for _ in range(random.randint(2, 5)):
        x, y = random_point_near(center_x, center_y, CITY_SPREAD_RADIUS)
        generate_city(conn, kingdom["id"], "Town", x, y)

    for _ in range(random.randint(5, 10)):
        x, y = random_point_near(center_x, center_y, CITY_SPREAD_RADIUS)
        generate_city(conn, kingdom["id"], "Village", x, y)

    save_kingdom(conn, kingdom)

    # Re-fetch: pulls in every city generated above.
    return get_kingdom(conn, kingdom["id"])


def backfill_city_positions(conn, world_id):
    """
    For a world whose cities all still sit at the default (0, 0) --
    i.e. a world generated before city positions existed in the schema
    at all -- assigns each kingdom a center point and scatters its
    cities around it, exactly like a freshly-generated world gets.

    Safe to call on any world: does nothing (returns False) if any
    city in it already has a real position, so this can run every
    time without double-assigning or disturbing a world that already
    has meaningful positions.
    """

    any_real_position = conn.execute(
        """
        SELECT 1 FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND (c.map_x != 0 OR c.map_y != 0) LIMIT 1
        """, (world_id,)
    ).fetchone()

    if any_real_position:
        return False

    kingdoms = conn.execute(
        "SELECT id, capital_city_id FROM kingdoms WHERE world_id = ?", (world_id,)
    ).fetchall()

    for kingdom in kingdoms:

        center_x = random.randint(MAP_MARGIN, MAP_WIDTH - MAP_MARGIN)
        center_y = random.randint(MAP_MARGIN, MAP_HEIGHT - MAP_MARGIN)

        cities = conn.execute("SELECT id FROM cities WHERE kingdom_id = ?", (kingdom["id"],)).fetchall()

        for city in cities:

            if city["id"] == kingdom["capital_city_id"]:
                x, y = random_point_near(center_x, center_y, CAPITAL_JITTER)
            else:
                x, y = random_point_near(center_x, center_y, CITY_SPREAD_RADIUS)

            conn.execute("UPDATE cities SET map_x = ?, map_y = ? WHERE id = ?", (x, y, city["id"]))

    return True
