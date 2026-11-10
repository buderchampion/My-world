import math
import random

# Expansion. Villages grow, and when their land is full, families go out and
# settle new land.
#
#   Growth: once a year, a village whose people are fed grows by GROWTH_RATE
#   (about 1%), slowing as its region's land fills: each region supports
#   CAPACITY_PER_FARMLAND households for each point of its farmland. Below
#   60% full a village grows at the full rate; at 100% it stops.
#
#   Settlement: when one of a kingdom's regions is CROWDED, there is a
#   SETTLE_CHANCE each year that a party of families leaves its most crowded
#   village to found a new village on the best empty farmland within REACH of
#   the kingdom's settlements -- but never within NEUTRAL_GROUND of another
#   kingdom's: realms keep wide neutral land between them, so trade is a
#   journey and borders do not touch.
#
#   New realms: settlers who go farther than FRONTIER from their own capital
#   have a REALM_CHANCE of founding their own kingdom instead, the new village
#   its capital -- chronicled as a founding.

GROWTH_RATE = 0.01
CAPACITY_PER_FARMLAND = 280          # households a region supports per point of farmland
FULL_RATE_BELOW = 0.6                # a village grows at the full rate while its region is below this full
CROWDED = 0.8                        # a region this full sends out settlers
SETTLE_CHANCE = 0.3                  # each year, for a kingdom with a crowded region
PARTY = (15, 25)                     # households in a settling party (never more than a third of their village)
MIN_FARMLAND = 0.6                   # settlers only take decent land
REACH = 750                          # map units from the kingdom's nearest settlement
NEUTRAL_GROUND = 375                 # map units every new village keeps from any other kingdom's settlements
FRONTIER = 700                       # map units from the capital beyond which settlers may found their own realm
REALM_CHANCE = 0.15
NEW_REALM_ENMITY = -10               # a new realm and the one it left begin slightly cool toward each other


def region_fill(conn, region_id):
    """How full a region's land is: its households against what its farmland supports."""
    row = conn.execute("SELECT farmland FROM regions WHERE id = ?", (region_id,)).fetchone()
    used = conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities WHERE region_id = ?", (region_id,)).fetchone()[0]
    capacity = CAPACITY_PER_FARMLAND * max(0.05, row["farmland"])
    return used / capacity


def grow_villages(conn, world_id):
    """Yearly: every fed village grows, slowing as its land fills. Returns households added."""
    added = 0.0
    fills = {}
    for village in conn.execute(
        """SELECT c.id, c.households, c.region_id, c.hungry_days FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND c.households > 0 AND c.region_id IS NOT NULL""", (world_id,)
    ).fetchall():
        if village["hungry_days"] > 0:
            continue                                         # no growth in a hungry year
        if village["region_id"] not in fills:                # once per region, before any of it grows this year
            fills[village["region_id"]] = region_fill(conn, village["region_id"])
        fill = fills[village["region_id"]]
        rate = GROWTH_RATE * max(0.0, min(1.0, (1 - fill) / (1 - FULL_RATE_BELOW)))
        if rate > 0:
            growth = village["households"] * rate
            conn.execute("UPDATE cities SET households = households + ? WHERE id = ?", (growth, village["id"]))
            added += growth
    conn.commit()
    return round(added, 2)


def find_site(conn, kingdom_id, world_id):
    """The best empty farmland the kingdom's settlers can reach without crowding another realm, or None."""
    own = conn.execute("SELECT map_x, map_y FROM cities WHERE kingdom_id = ?", (kingdom_id,)).fetchall()
    others = conn.execute(
        """SELECT c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND c.kingdom_id != ?""", (world_id, kingdom_id)).fetchall()
    best = None
    for region in conn.execute(
        """SELECT * FROM regions WHERE world_id = ? AND farmland >= ?
           AND id NOT IN (SELECT region_id FROM cities WHERE region_id IS NOT NULL)""", (world_id, MIN_FARMLAND)
    ).fetchall():
        x, y = region["center_x"], region["center_y"]
        nearest_own = min((math.dist((x, y), (c["map_x"], c["map_y"])) for c in own), default=1e9)
        nearest_other = min((math.dist((x, y), (c["map_x"], c["map_y"])) for c in others), default=1e9)
        if nearest_own > REACH or nearest_other < NEUTRAL_GROUND:
            continue
        key = (region["farmland"], -nearest_own)
        if best is None or key > best[0]:
            best = (key, dict(region))
    return best[1] if best else None


def settle(conn, world_id, kingdom, source, region):
    """
    A party of families leaves `source` and founds a new village in `region`
    -- or, far enough from their capital, a realm of their own. Returns the
    new village's id.
    """

    from Data.Cities.city_generator import generate_city
    from Data.Kingdoms.kingdom_generator import BIAS_RANGE, generate_kingdom_name, KINGDOM_COLORS
    from Data.Buildings.building_generator import CHAIN_CATEGORIES
    from Data.Infrastructure.roads import road_distance, travel_days_for
    from Data.Database.repository import create_road, get_city, create_event, create_kingdom, adjust_kingdom_favor
    from Data.World.countryside import named_villagers, FAMILY_SIZE
    from Data.World.regions import assign_settlements_to_regions
    from Data.History.chronicle import write_entry, current_date

    party = min(random.randint(*PARTY), source["households"] / 3)
    named = named_villagers(party)
    x = region["center_x"] + random.randint(-40, 40)
    y = region["center_y"] + random.randint(-40, 40)
    biases = {category: random.uniform(*BIAS_RANGE) for category in CHAIN_CATEGORIES}

    capital = get_city(conn, kingdom["capital_city_id"])
    frontier = math.dist((x, y), (capital["map_x"], capital["map_y"])) > FRONTIER
    new_realm = frontier and random.random() < REALM_CHANCE

    home_kingdom = kingdom["id"]
    if new_realm:
        world_kingdoms = {row[0] for row in conn.execute("SELECT name FROM kingdoms WHERE world_id = ?", (world_id,))}
        name = next((n for n in (generate_kingdom_name() for _ in range(50)) if n not in world_kingdoms), "Freehold")
        used = {row[0] for row in conn.execute("SELECT color FROM kingdoms WHERE world_id = ?", (world_id,))}
        color = next((c for c in KINGDOM_COLORS if c not in used), "#%06X" % random.randint(0, 0xFFFFFF))
        home_kingdom = create_kingdom(conn, world_id, name, color)["id"]

    village = generate_city(conn, home_kingdom, "Village", x, y, biases, population=named)
    settlers = max(0.0, party - named / FAMILY_SIZE)            # the named villagers came from the party too
    share = party / source["households"]
    conn.execute("UPDATE cities SET households = households - ?, household_store = household_store * ? WHERE id = ?",
                 (party, 1 - share, source["id"]))
    conn.execute("UPDATE cities SET households = ?, household_store = ? WHERE id = ?",
                 (settlers, source["household_store"] * share, village["id"]))
    conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
                    ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                 (village["id"], named * 30))                      # the named villagers bring a month's food
    assign_settlements_to_regions(conn, world_id)

    year, season = current_date(conn, world_id)
    region_name = region["name"]
    if new_realm:
        conn.execute("UPDATE cities SET type = 'Capital' WHERE id = ?", (village["id"],))
        conn.execute("UPDATE kingdoms SET capital_city_id = ? WHERE id = ?", (village["id"], home_kingdom))
        new_capital = get_city(conn, village["id"])
        for other in conn.execute(
            "SELECT capital_city_id FROM kingdoms WHERE world_id = ? AND status != 'Fallen' AND id != ? AND capital_city_id IS NOT NULL",
            (world_id, home_kingdom)).fetchall():
            far = get_city(conn, other[0])
            distance = road_distance(new_capital, far)
            create_road(conn, new_capital["id"], far["id"], "Trade Route", distance, travel_days_for(distance))
        adjust_kingdom_favor(conn, home_kingdom, kingdom["id"], NEW_REALM_ENMITY)
        adjust_kingdom_favor(conn, kingdom["id"], home_kingdom, NEW_REALM_ENMITY)
        realm_name = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (home_kingdom,)).fetchone()[0]
        write_entry(conn, home_kingdom, "Founding", "Realm Founded (settlers)", year, season,
                    {"kingdom": realm_name, "other": kingdom["name"], "city": new_capital["name"]}, seed_parts=(village["id"],))
        create_event(conn, "Settlement",
                     f"Families from {source['name']} went far beyond the reach of {kingdom['name']} and founded "
                     f"{new_capital['name']} in {region_name} -- the seat of a new realm, {realm_name}.",
                     city_id=village["id"], kingdom_id=home_kingdom)
    else:
        distance = road_distance(capital, get_city(conn, village["id"]))
        create_road(conn, capital["id"], village["id"], "Capital Road", distance, travel_days_for(distance))
        centers = [dict(r) for r in conn.execute(
            """SELECT c.id, c.map_x, c.map_y, p.id province_id FROM cities c LEFT JOIN provinces p ON p.seat_city_id = c.id
               WHERE c.kingdom_id = ? AND (p.id IS NOT NULL OR c.id = ?)""", (kingdom["id"], kingdom["capital_city_id"]))]
        if centers:
            nearest = min(centers, key=lambda c: ((c["map_x"] - x) ** 2 + (c["map_y"] - y) ** 2, c["id"]))
            conn.execute("UPDATE cities SET province_id = ? WHERE id = ?", (nearest["province_id"], village["id"]))
        create_event(conn, "Settlement",
                     f"Families from {source['name']} founded the village of {get_city(conn, village['id'])['name']} in {region_name}.",
                     city_id=village["id"], kingdom_id=kingdom["id"])

    conn.commit()
    return village["id"]


def consider_settlement(conn, world_id):
    """Yearly: each kingdom with a crowded region may send out settlers. Returns the new villages' ids."""
    founded = []
    for kingdom in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND status != 'Fallen' AND capital_city_id IS NOT NULL",
                                (world_id,)).fetchall():
        kingdom = dict(kingdom)
        regions = {row[0] for row in conn.execute(
            "SELECT DISTINCT region_id FROM cities WHERE kingdom_id = ? AND households > 0 AND region_id IS NOT NULL", (kingdom["id"],))}
        crowded = [r for r in regions if region_fill(conn, r) >= CROWDED]
        if not crowded or random.random() >= SETTLE_CHANCE:
            continue
        site = find_site(conn, kingdom["id"], world_id)
        if site is None:
            continue
        marks = ",".join("?" * len(crowded))
        source = conn.execute(
            f"SELECT * FROM cities WHERE kingdom_id = ? AND region_id IN ({marks}) AND households >= ? ORDER BY households DESC LIMIT 1",
            (kingdom["id"], *crowded, PARTY[0] * 3)).fetchone()
        if source is None:
            continue
        founded.append(settle(conn, world_id, kingdom, dict(source), site))
    return founded