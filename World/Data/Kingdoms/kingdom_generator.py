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
# kingdoms' centers keep this far apart: two kingdoms' spread plus the neutral
# ground between their settlements (Data.World.expansion.NEUTRAL_GROUND = 375)
KINGDOM_SPACING = 2 * 300 + 375
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


BIAS_RANGE = (0.5, 2.0)


def generate_kingdom(conn, world_id, color="#808080", kingdom_type="Crown"):

    # a name no other kingdom in this world bears (two realms of one name would confuse every chronicle)
    taken = {row[0] for row in conn.execute("SELECT name FROM kingdoms WHERE world_id = ?", (world_id,))}
    name = next((n for n in (generate_kingdom_name() for _ in range(200)) if n not in taken), None)
    if name is None:
        name = f"{generate_kingdom_name()} {len(taken) + 1}"
    kingdom = create_kingdom(conn, world_id, name, color)

    # a Crown or a merchant Republic (Data.Government.republic)
    conn.execute("UPDATE kingdoms SET type = ? WHERE id = ?", (kingdom_type, kingdom["id"]))
    from Data.Government.republic import REPUBLIC_RURAL_SHARE
    rural_share = REPUBLIC_RURAL_SHARE if kingdom_type == "Republic" else None

    from Data.Buildings.building_generator import CHAIN_CATEGORIES
    biases = {category: random.uniform(*BIAS_RANGE) for category in CHAIN_CATEGORIES}

    # Where the land can feed them: with regions in place, the kingdom's
    # heartland and every settlement are chosen from candidate sites favoring
    # good farmland (and rivers, for the capital and towns). Without regions
    # (old test scripts), everything is placed at random as before.
    from Data.World.regions import get_regions
    from Data.World import countryside

    regions = get_regions(conn, world_id)

    def on_map(point):
        return min(MAP_WIDTH - 1, max(0, point[0])), min(MAP_HEIGHT - 1, max(0, point[1]))

    if regions:
        # Kingdoms keep wide neutral ground between them (as settlers do,
        # Data.World.expansion): a new kingdom's center stays KINGDOM_SPACING
        # from every other kingdom's capital, and each of its settlements
        # NEUTRAL_GROUND from theirs. Where the map is too crowded for that,
        # the farthest candidates are taken instead -- as much room as it allows.
        from Data.World.expansion import NEUTRAL_GROUND
        import math
        foreign = conn.execute(
            """SELECT c.map_x, c.map_y, c.type FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND c.kingdom_id != ?""", (world_id, kingdom["id"])).fetchall()
        foreign_capitals = [(c["map_x"], c["map_y"]) for c in foreign if c["type"] == "Capital"]
        foreign_settlements = [(c["map_x"], c["map_y"]) for c in foreign]

        def keep_clear(candidates, others, gap):
            """Candidates at least `gap` from all `others`; failing that, the farthest quarter of them."""
            if not others:
                return candidates
            distance = lambda p: min(math.dist(p, o) for o in others)
            clear = [p for p in candidates if distance(p) >= gap]
            if clear:
                return clear
            ranked = sorted(candidates, key=distance, reverse=True)
            return ranked[:max(1, len(ranked) // 4)]

        # decent land first (a kingdom is not founded on a mountain), then room from the others
        pool = [(random.randint(MAP_MARGIN, MAP_WIDTH - MAP_MARGIN), random.randint(MAP_MARGIN, MAP_HEIGHT - MAP_MARGIN))
                for _ in range(countryside.KINGDOM_CANDIDATES * (8 if foreign_capitals else 1))]   # more to choose from, to find room
        decent = [p for p in pool if countryside.region_at(regions, *p)["terrain"] != "Mountains"
                  and countryside.region_at(regions, *p)["farmland"] > 0] or pool
        center_x, center_y = countryside.choose_site(regions, keep_clear(decent, foreign_capitals, KINGDOM_SPACING), best=True)

        def site(radius, river=False, best=False, lowland=False):
            count = countryside.SITE_CANDIDATES * (3 if foreign_settlements else 1)              # more to choose from, to find room
            candidates = [on_map(random_point_near(center_x, center_y, radius)) for _ in range(count)]
            if lowland:                    # a capital is not built on a mountain, even near one
                candidates = [p for p in candidates if countryside.region_at(regions, *p)["terrain"] != "Mountains"] or candidates
            candidates = keep_clear(candidates, foreign_settlements, NEUTRAL_GROUND)
            return countryside.choose_site(regions, candidates, prefer_river=river, best=best)
    else:
        center_x = random.randint(MAP_MARGIN, MAP_WIDTH - MAP_MARGIN)
        center_y = random.randint(MAP_MARGIN, MAP_HEIGHT - MAP_MARGIN)

        def site(radius, river=False, best=False, lowland=False):
            return random_point_near(center_x, center_y, radius)

    capital_x, capital_y = site(CAPITAL_JITTER, river=True, best=True, lowland=True)

    capital = generate_city(conn, kingdom["id"], "Capital", capital_x, capital_y, biases)

    kingdom["capital"] = capital["id"]
    town_dwellers = capital["population"]

    for _ in range(random.randint(2, 5)):
        x, y = site(CITY_SPREAD_RADIUS, river=True)
        town_dwellers += generate_city(conn, kingdom["id"], "Town", x, y, biases)["population"]

    village_sites = [site(CITY_SPREAD_RADIUS) for _ in range(random.randint(5, 10))]

    if regions:
        # Villages hold most of the kingdom's people as households, bigger on
        # better land, each with its named villagers as individual NPCs.
        # planned for the towns' people once their children are born (Data.NPCS.family), so the
        # countryside keeps its share of all the people, children included
        from Data.NPCS.family import FOUNDING_CHILD_SHARE
        plan = countryside.plan_villages(
            round(town_dwellers * (1 + FOUNDING_CHILD_SHARE)), [countryside.region_at(regions, x, y)["farmland"] for x, y in village_sites],
            **({"rural_share": rural_share} if rural_share else {})
        )
        for (x, y), (households, named) in zip(village_sites, plan):
            village = generate_city(conn, kingdom["id"], "Village", x, y, biases, population=named)
            conn.execute("UPDATE cities SET households = ? WHERE id = ?", (households, village["id"]))
    else:
        for x, y in village_sites:
            generate_city(conn, kingdom["id"], "Village", x, y, biases)

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