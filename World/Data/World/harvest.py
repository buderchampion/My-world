import random

# Harvests. Once a year, at the start of Autumn, every village's peasant
# households reap what their region's land and weather allow. A share is
# kept as seed, a share goes to the crown as grain tax, the households keep
# enough to eat until next harvest, and the rest -- the surplus -- goes to the
# village market, where ordinary trade carries it to the towns. Through the
# year, households eat from their store; if it runs out they buy from the
# village market; if that is empty too, the village goes hungry.

# Food a household's fields yield in a year, on farmland 1.0 in a normal
# year. A household of five eats 1,800 a year, so a good-land family feeds
# itself and about one more mouth -- sized so that a typical kingdom just
# about feeds itself in a normal year (see the countryside notes).
HARVEST_PER_HOUSEHOLD = 3600

# Of every harvest: kept back as next year's seed, and sent to the crown's
# capital as grain tax.
SEED_SHARE = 0.20
GRAIN_TAX_SHARE = 0.10

# The harvest season begins in this month (Autumn); a year has 360 days.
HARVEST_MONTH = 7
DAYS_PER_YEAR = 360

# Weather. Each region's weather each year is a score built from three parts:
# a world-wide mood (some years are hard almost everywhere), a regional part
# shared with neighbors (bad weather covers an area), and a carry-over from
# last year (hard years can come in pairs). Scores below BAD_BELOW give a bad
# harvest, above GOOD_ABOVE a good one -- each about 1 year in 5.
WORLD_WEIGHT = 0.45
REGIONAL_WEIGHT = 0.9
CARRY_OVER = 0.35
BAD_BELOW = -0.88
GOOD_ABOVE = 0.88
WEATHER_FACTOR = {"Good": 1.2, "Normal": 1.0, "Bad": 0.55}

FAMILY_SIZE = 5     # people per household (matches Data.World.countryside)


def days_until_harvest(month, day):
    """Days from (month, day) until the first day of the next harvest month."""
    today = (month - 1) * 30 + (day - 1)
    harvest = (HARVEST_MONTH - 1) * 30
    return (harvest - today) % DAYS_PER_YEAR or DAYS_PER_YEAR


def roll_weather(regions, neighbors, last_scores):
    """
    {region id: (quality, factor, score)} for this year's harvest. regions are
    region rows; neighbors maps id -> neighbor ids; last_scores maps id -> last
    year's score (missing in a world's first year).
    """

    world_mood = random.gauss(0, 1)
    local = {r["id"]: random.gauss(0, 1) for r in regions}
    regional = {
        rid: (local[rid] + sum(local[n] for n in neighbors[rid])) / (1 + len(neighbors[rid])) ** 0.5
        for rid in local
    }

    weather = {}
    for region in regions:
        rid = region["id"]
        score = WORLD_WEIGHT * world_mood + REGIONAL_WEIGHT * regional[rid] / 1.0 + CARRY_OVER * last_scores.get(rid, 0.0)
        if score < BAD_BELOW:
            quality = "Bad"
        elif score > GOOD_ABOVE:
            quality = "Good"
        else:
            quality = "Normal"
        weather[rid] = (quality, WEATHER_FACTOR[quality], round(score, 3))

    return weather


def _add_food(conn, city_id, amount):
    if amount > 0:
        conn.execute(
            """
            INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
            ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount
            """,
            (city_id, round(amount, 2))
        )


def run_harvest(conn, world_id, year):
    """
    The year's harvest for every village in the world: rolls the weather,
    reaps, keeps seed, sends grain tax to each kingdom's capital, fills the
    households' store until next harvest, and puts the surplus on the village
    market. Records the weather per region. Changes stocks in the database
    directly, so call it outside any loop that will later save older copies
    of these cities (on_season is). Returns a summary per kingdom.
    """

    from Data.World.regions import get_regions, region_neighbors
    from Data.Database.repository import create_event

    regions = get_regions(conn, world_id)
    if not regions:
        return {}

    last = {
        row["region_id"]: row["score"] for row in conn.execute(
            """SELECT h.region_id, h.score FROM harvests h JOIN regions r ON r.id = h.region_id
               WHERE r.world_id = ? AND h.year = ?""", (world_id, year - 1))
    }
    weather = roll_weather(regions, region_neighbors(regions), last)

    for rid, (quality, factor, score) in weather.items():
        conn.execute(
            "INSERT INTO harvests (region_id, year, quality, factor, score) VALUES (?, ?, ?, ?, ?)",
            (rid, year, quality, factor, score)
        )

    farmland = {r["id"]: r["farmland"] for r in regions}
    year_need = FAMILY_SIZE * DAYS_PER_YEAR

    summary = {}
    villages = conn.execute(
        """
        SELECT c.id, c.kingdom_id, c.region_id, c.households, c.household_store, k.capital_city_id, k.name kingdom
        FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households > 0 AND c.region_id IS NOT NULL
        """, (world_id,)
    ).fetchall()

    for v in villages:

        quality, factor, _ = weather[v["region_id"]]
        reaped = v["households"] * HARVEST_PER_HOUSEHOLD * farmland[v["region_id"]] * factor
        seed = reaped * SEED_SHARE
        tax = reaped * GRAIN_TAX_SHARE
        left = reaped - seed - tax

        room = max(0.0, v["households"] * year_need - v["household_store"])
        kept = min(left, room)
        surplus = left - kept

        conn.execute("UPDATE cities SET household_store = household_store + ? WHERE id = ?", (round(kept, 2), v["id"]))
        _add_food(conn, v["capital_city_id"], tax)
        _add_food(conn, v["id"], surplus)

        s = summary.setdefault(v["kingdom_id"], {"kingdom": v["kingdom"], "reaped": 0.0, "tax": 0.0, "surplus": 0.0,
                                                  "need": 0.0, "villages": 0, "bad": 0, "good": 0})
        s["reaped"] += reaped; s["tax"] += tax; s["surplus"] += surplus
        s["need"] += v["households"] * year_need
        s["villages"] += 1
        s["bad"] += quality == "Bad"
        s["good"] += quality == "Good"

    for kingdom_id, s in summary.items():
        verdict = "bad" if s["bad"] > s["villages"] / 2 else ("good" if s["good"] > s["villages"] / 2 else "middling" if s["bad"] else "fair")
        create_event(
            conn, "Harvest",
            f"{s['kingdom']} brought in a {verdict} harvest: {s['reaped']:.0f} Food reaped in {s['villages']} villages "
            f"({s['bad']} bad, {s['good']} good), {s['tax']:.0f} paid as grain tax, {s['surplus']:.0f} sent to market.",
            kingdom_id=kingdom_id
        )

    conn.commit()
    return summary


def feed_households(city):
    """
    One day of eating for a village's households: from their own store
    first, then bought from the village market (city Food stock), which trade
    refills. If both run dry the village goes hungry: hungry_days counts the
    days in a row without enough. Mutates the city dict; save_city persists it.
    Returns the Food the households could not get today (0 if they ate).
    """

    households = city.get("households") or 0
    if households <= 0:
        return 0.0

    need = households * FAMILY_SIZE
    store = city.get("household_store") or 0.0

    from_store = min(store, need)
    city["household_store"] = round(store - from_store, 2)
    need -= from_store

    market = city.setdefault("resources", {}).get("Food", 0)
    from_market = min(market, need)
    city["resources"]["Food"] = round(market - from_market, 2)
    need -= from_market

    city["hungry_days"] = (city.get("hungry_days") or 0) + 1 if need > 0.5 else 0

    return round(need, 2)


def ensure_household_stores(conn, world_id):
    """
    A world that has never had a harvest (a new world, or one made before
    harvests existed) gives each village's households enough to eat until
    the first harvest -- they were plainly living off last year's crop. Once
    a world has had a harvest this does nothing, so a village that has
    honestly eaten through its store is never refilled for free. Returns how
    many villages were stocked.
    """

    had_harvest = conn.execute(
        "SELECT 1 FROM harvests h JOIN regions r ON r.id = h.region_id WHERE r.world_id = ? LIMIT 1", (world_id,)
    ).fetchone()
    if had_harvest:
        return 0

    clock = conn.execute("SELECT clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
    days = days_until_harvest(clock["clock_month"], clock["clock_day"])
    rows = conn.execute(
        """
        SELECT c.id, c.households FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households > 0 AND c.household_store <= 0
        """, (world_id,)
    ).fetchall()

    for row in rows:
        conn.execute("UPDATE cities SET household_store = ? WHERE id = ?",
                     (row["households"] * FAMILY_SIZE * days, row["id"]))

    conn.commit()
    return len(rows)