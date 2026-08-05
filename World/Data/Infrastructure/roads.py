import math
from Data.Database.repository import get_world_kingdoms, get_world_roads, create_road

# How many map units of distance correspond to one day of travel.
# CITY_SPREAD_RADIUS in kingdom_generator.py is 300, so a city at the
# far edge of its kingdom's own cluster is roughly a 3-4 day ride from
# its capital; kingdom-to-kingdom trade routes can run much longer,
# since kingdom centers are scattered across the whole map.
DISTANCE_PER_TRAVEL_DAY = 90


def road_distance(city_a: dict, city_b: dict) -> float:
    return math.hypot(city_a["map_x"] - city_b["map_x"], city_a["map_y"] - city_b["map_y"])


def travel_days_for(distance: float) -> int:
    return max(1, round(distance / DISTANCE_PER_TRAVEL_DAY))


def generate_roads(conn, world_id: int) -> list[dict]:
    """
    Builds a road network for a world: every city connects directly to
    its own kingdom's capital (hub-and-spoke), and every pair of
    kingdom capitals gets a longer "Trade Route" connecting them.

    Safe to call on a world that already has roads -- returns an empty
    list and does nothing if any exist, so this same function generates
    roads for a freshly-created world AND backfills them onto a world
    that predates this system, without needing two separate code paths.
    """

    if get_world_roads(conn, world_id):
        return []

    created = []
    capitals = []

    for kingdom in get_world_kingdoms(conn, world_id):

        cities = kingdom["cities"]
        capital_id = kingdom["capital"]
        capital = next((c for c in cities if c["id"] == capital_id), None)

        if capital is None:
            continue

        # kingdom["cities"] only carries id/name/type (see get_kingdom) --
        # we need map_x/map_y too, so re-fetch the capital in full.
        from Data.Database.repository import get_city
        capital = get_city(conn, capital["id"])
        capitals.append(capital)

        for city_summary in cities:
            if city_summary["id"] == capital["id"]:
                continue
            city = get_city(conn, city_summary["id"])
            distance = road_distance(capital, city)
            road = create_road(conn, capital["id"], city["id"], "Capital Road", distance, travel_days_for(distance))
            created.append(road)

    for i in range(len(capitals)):
        for j in range(i + 1, len(capitals)):
            distance = road_distance(capitals[i], capitals[j])
            road = create_road(
                conn, capitals[i]["id"], capitals[j]["id"], "Trade Route", distance, travel_days_for(distance)
            )
            created.append(road)

    return created
