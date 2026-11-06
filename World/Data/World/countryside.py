import math
import random

# The countryside. Most medieval people lived in villages and farmed, so most
# of a kingdom's people live in its villages here too. They are not all
# simulated one by one: a village holds a count of peasant HOUSEHOLDS, plus a
# set of NAMED villagers (headman, priest, smith, notable families) who are
# ordinary individual NPCs. Towns and cities keep every person as an NPC.
#
# A city's "population" still means its individual NPCs -- the economy, trade
# and starting stock all rely on that -- and households are kept separately.
# Until harvests exist, households neither eat from stock nor produce; they
# are counted as people, nothing more.

# Share of a kingdom's people who live in its villages.
RURAL_SHARE = 0.70

# People in one peasant household.
FAMILY_SIZE = 5

# Named villagers grow with the square root of a village's households -- a
# big village has more of them, but a smaller share of its people:
# 25 households -> 20 named, 100 -> 40, 225 -> 60.
NAMED_PER_ROOT_HOUSEHOLD = 4
NAMED_MIN, NAMED_MAX = 8, 60

# No village is smaller than this many households.
MIN_HOUSEHOLDS = 15

# Settlements are placed where the land can feed them: each is chosen from
# this many candidate sites, favoring farmland (and rivers, for capitals and
# towns). Better land also makes a bigger village: households are shared out
# by farmland raised to this power.
SITE_CANDIDATES = 8
KINGDOM_CANDIDATES = 12
RIVER_SITE_BONUS = 0.3

# How strongly a random site pick favors good land: weights are the site's
# score raised to this power. (A kingdom's heartland and its capital take the
# very best candidate instead.)
SITE_WEIGHT_POWER = 3
VILLAGE_LAND_POWER = 1.5


def named_villagers(households):
    return max(NAMED_MIN, min(NAMED_MAX, round(NAMED_PER_ROOT_HOUSEHOLD * math.sqrt(households))))


def total_people(city):
    """Everyone in a settlement: its individual NPCs plus its households."""
    return city["population"] + city.get("households", 0) * FAMILY_SIZE


def region_at(regions, x, y):
    return min(regions, key=lambda r: (r["center_x"] - x) ** 2 + (r["center_y"] - y) ** 2)


def site_score(regions, x, y, prefer_river=False):
    region = region_at(regions, x, y)
    return region["farmland"] + (RIVER_SITE_BONUS if prefer_river and region["has_river"] else 0)


def choose_site(regions, candidates, prefer_river=False, best=False):
    """
    Picks one of the candidate (x, y) points by the land there: the best one
    if best=True, otherwise at random weighted toward good farmland (so
    settlements favor good land without all crowding onto one spot).
    """

    scored = [(site_score(regions, x, y, prefer_river), (x, y)) for x, y in candidates]

    if best:
        return max(scored)[1]

    weights = [max(0.01, score) ** SITE_WEIGHT_POWER for score, _ in scored]
    return random.choices([point for _, point in scored], weights=weights, k=1)[0]


def plan_villages(urban_people, village_farmland, rural_share=RURAL_SHARE):
    """
    Given the kingdom's town-dwellers and the farmland at each village's site,
    returns [(households, named villagers)] per village, sized so about
    RURAL_SHARE of the kingdom's people live in villages and better land
    holds bigger villages.
    """

    rural_target = urban_people * rural_share / (1 - rural_share)
    weights = [max(0.05, f) ** VILLAGE_LAND_POWER for f in village_farmland]
    total_weight = sum(weights)

    named = [NAMED_MIN] * len(weights)
    households = []

    for _ in range(3):      # named villagers depend on size, and size on them: settle in a few passes
        to_share = max(0.0, rural_target - sum(named)) / FAMILY_SIZE
        households = [max(MIN_HOUSEHOLDS, round(to_share * w / total_weight)) for w in weights]
        named = [named_villagers(h) for h in households]

    return list(zip(households, named))


def ensure_households(conn, world_id):
    """
    A world made before households existed: gives its villages households,
    sized and shared out the same way as a new world's (without touching
    their people). Safe to call every start; does nothing for a world that
    has them. Returns how many villages were given households.
    """

    if conn.execute(
        """
        SELECT 1 FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households > 0 LIMIT 1
        """, (world_id,)
    ).fetchone():
        return 0

    given = 0

    for kingdom in conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():

        cities = conn.execute(
            """
            SELECT c.id, c.type, c.population, COALESCE(r.farmland, 1.0) farmland
            FROM cities c LEFT JOIN regions r ON r.id = c.region_id WHERE c.kingdom_id = ?
            """, (kingdom["id"],)
        ).fetchall()

        villages = [c for c in cities if c["type"] == "Village"]
        if not villages:
            continue

        urban = sum(c["population"] for c in cities if c["type"] != "Village")
        rural_target = urban * RURAL_SHARE / (1 - RURAL_SHARE) - sum(v["population"] for v in villages)
        weights = [max(0.05, v["farmland"]) ** VILLAGE_LAND_POWER for v in villages]

        for village, weight in zip(villages, weights):
            households = max(MIN_HOUSEHOLDS, round(max(0.0, rural_target) / FAMILY_SIZE * weight / sum(weights)))
            conn.execute("UPDATE cities SET households = ? WHERE id = ?", (households, village["id"]))
            given += 1

    conn.commit()
    return given