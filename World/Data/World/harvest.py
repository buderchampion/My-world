import random

# Harvests. Once a year, at the start of Autumn, every village's peasant
# households reap what their region's land and weather allow. A share is
# kept as seed, a share goes to the crown as grain tax, the households keep
# enough to eat until next harvest, and the rest -- the surplus -- goes to the
# village market, where ordinary trade carries it to the towns. Through the
# year, households eat from their store; if it runs out they buy from the
# village market; if that is empty too, the village goes hungry.

# Food a household's fields yield in a year, on farmland 1.0 in a normal
# year. A household of five eats 1,800 a year. After seed and grain tax a
# family on middling land (0.7) has about 2,450 left: its year, a reserve,
# and food to sell to the towns, where about a third of the people live (more
# with their children). Measured with the long test run (long_run.py): at the
# old 3,600 the towns ran dry a hundred days after every harvest while the
# villages sat on their stores, and starved. (Any less and a kingdom on poor
# land -- farmland 0.6 -- cannot feed itself at all.)
HARVEST_PER_HOUSEHOLD = 5000

# What a family keeps back: its food until the next harvest, and a reserve of
# RESERVE_DAYS against a bad year. Anything above it goes to market at the
# harvest; and through the year, each month, families sell SELL_SHARE of what
# they hold above their keep -- more as food grows dear (the kingdom's price
# level), down to their keep and never below.
RESERVE_DAYS = 90
SELL_SHARE = 0.25
SELL_PRICE = 1.0       # coin a family takes for each Food sold (Food's base price), times the kingdom's price level

# Grain keeps, but not forever: each month a town's Food beyond KEEP_DAYS of its
# people's eating loses SPOIL_SHARE to rats and rot. Measured with long_run.py:
# without it the towns' granaries grew by millions every year; at a year's keep
# and 5% a month -- with druids tending fields and farming discoveries on top --
# they still grew by about 5 million a year for five years, so no bad year bit.
KEEP_DAYS = 180
SPOIL_SHARE = 0.10

# A new world opens as though last year's harvest was a fair one: the stores
# hold food until the first harvest and this much beyond.
START_RESERVE_DAYS = 60

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

# Famine. Once a village has gone hungry this many days in a row, its
# households start dying: up to FAMINE_DEATH_RATE of them a day when they
# have nothing at all, less when they get some (about 9% a month at worst --
# the Great Famine of 1315-17 killed 10-25% in a year).
FAMINE_AFTER_HUNGRY_DAYS = 20
FAMINE_DEATH_RATE = 0.003


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
    from Data.World.disasters import blights                     # floods and locusts cut a region's harvest
    for rid, cut in blights(conn, world_id, year).items():
        if rid in weather:
            quality, factor, score = weather[rid]
            weather[rid] = (quality, round(factor * cut, 3), score)

    for rid, (quality, factor, score) in weather.items():
        conn.execute(
            "INSERT INTO harvests (region_id, year, quality, factor, score) VALUES (?, ?, ?, ?, ?)",
            (rid, year, quality, factor, score)
        )

    farmland = {r["id"]: r["farmland"] for r in regions}
    from Data.Magic.magic import druid_harvest_bonus           # druids tend the land
    from Data.Research.discoveries import bonus, refresh       # and what its scholars have learned
    refresh(conn, world_id)
    tended = druid_harvest_bonus(conn, world_id)
    year_need = FAMILY_SIZE * DAYS_PER_YEAR

    summary = {}
    villages = conn.execute(
        """
        SELECT c.id, c.kingdom_id, c.region_id, c.households, c.household_store, k.capital_city_id, k.name kingdom,
               (SELECT p.seat_city_id FROM provinces p WHERE p.id = c.province_id) seat_id
        FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households > 0 AND c.region_id IS NOT NULL
        """, (world_id,)
    ).fetchall()

    for v in villages:

        quality, factor, _ = weather[v["region_id"]]
        reaped = v["households"] * HARVEST_PER_HOUSEHOLD * farmland[v["region_id"]] * factor * (1 + tended.get(v["id"], 0)) \
            * (1 + bonus(v["kingdom_id"], "harvest"))                       # the kingdom's farming discoveries
        seed = reaped * SEED_SHARE
        tax = reaped * GRAIN_TAX_SHARE
        left = reaped - seed - tax

        room = max(0.0, v["households"] * FAMILY_SIZE * (DAYS_PER_YEAR + RESERVE_DAYS) - v["household_store"])   # a year and a reserve
        kept = min(left, room)
        surplus = left - kept

        conn.execute("UPDATE cities SET household_store = household_store + ? WHERE id = ?", (round(kept, 2), v["id"]))
        # a county's lord keeps their share of its grain tax at the seat; the
        # rest -- and all of the crown lands' -- goes to the capital
        from Data.Government.feudal import lord_cut
        lords_grain = lord_cut(tax) if v["seat_id"] else 0.0
        _add_food(conn, v["seat_id"], lords_grain)
        _add_food(conn, v["capital_city_id"], tax - lords_grain)
        _add_food(conn, v["id"], surplus)

        s = summary.setdefault(v["kingdom_id"], {"kingdom": v["kingdom"], "reaped": 0.0, "tax": 0.0, "surplus": 0.0,
                                                  "need": 0.0, "villages": 0, "bad": 0, "good": 0})
        s["reaped"] += reaped; s["tax"] += tax; s["surplus"] += surplus
        s["need"] += v["households"] * year_need
        s["villages"] += 1
        s["bad"] += quality == "Bad"
        s["good"] += quality == "Good"

    from Data.Economy.food_market import update_price_level

    for kingdom_id, s in summary.items():
        verdict = "bad" if s["bad"] > s["villages"] / 2 else ("good" if s["good"] > s["villages"] / 2 else "middling" if s["bad"] else "fair")
        old, new = update_price_level(conn, kingdom_id, verdict)
        s["verdict"], s["price_level"] = verdict, new
        prices = (f" Bread now costs {new:.2f} times its old price (it was {old:.2f})." if abs(new - old) > 0.005
                  else f" Bread holds at {new:.2f} times its old price." if new > 1.005 else "")
        create_event(
            conn, "Harvest",
            f"{s['kingdom']} brought in a {verdict} harvest: {s['reaped']:.0f} Food reaped in {s['villages']} villages "
            f"({s['bad']} bad, {s['good']} good), {s['tax']:.0f} paid as grain tax, {s['surplus']:.0f} sent to market.{prices}",
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

    # Famine: a long hunger starts killing. The loss is saved as a change, not
    # a value (save_city applies and clears _famine_deaths), so an older copy
    # of this village can never overwrite it and saving twice cannot count it twice.
    if city["hungry_days"] >= FAMINE_AFTER_HUNGRY_DAYS and need > 0.5:
        short_share = need / (households * FAMILY_SIZE)
        lost = households * FAMINE_DEATH_RATE * short_share
        city["households"] = households - lost
        city["_famine_deaths"] = city.get("_famine_deaths", 0) + lost

    return round(need, 2)


def ensure_household_stores(conn, world_id):
    """
    A world that has never had a harvest (a new world, or one made before
    harvests existed) gives each village's households enough to eat until
    the first harvest -- they were plainly living off last year's crop --
    and stocks every settlement's market with the same for its individual
    people. Once
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
    days = days_until_harvest(clock["clock_month"], clock["clock_day"]) + START_RESERVE_DAYS
    rows = conn.execute(
        """
        SELECT c.id, c.households FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households > 0 AND c.household_store <= 0
        """, (world_id,)
    ).fetchall()

    for row in rows:
        conn.execute("UPDATE cities SET household_store = ? WHERE id = ?",
                     (row["households"] * FAMILY_SIZE * days, row["id"]))

    # Last year's surplus is still in the markets too: every settlement holds
    # enough Food for its individual people until the first harvest. Only when
    # the villages were just stocked, so calling this twice before the first
    # harvest can never stock the markets twice.
    if rows:
        for city in conn.execute(
            "SELECT c.id, c.population FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?",
            (world_id,)
        ).fetchall():
            _add_food(conn, city["id"], city["population"] * days)

    conn.commit()
    return len(rows)

def households_sell(conn, world_id):
    """
    Monthly: families sell food they hold beyond their keep (until the next
    harvest, plus RESERVE_DAYS) at the village market, where trade carries it to
    the towns: SELL_SHARE of the spare at ordinary prices, more as food grows
    dear in their kingdom (up to all of it). They take coin for it (into the
    village's purse). Returns the Food sold.
    """

    clock = conn.execute("SELECT clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
    keep_days = days_until_harvest(clock["clock_month"], clock["clock_day"]) + RESERVE_DAYS
    sold = 0.0
    for v in conn.execute(
            """SELECT c.id, c.households, c.household_store, k.price_level FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND c.households > 0""", (world_id,)).fetchall():
        spare = (v["household_store"] or 0) - v["households"] * FAMILY_SIZE * keep_days
        if spare <= 0:
            continue
        share = min(1.0, SELL_SHARE * max(1.0, v["price_level"] or 1.0) ** 2)
        amount = round(spare * share, 2)
        conn.execute("UPDATE cities SET household_store = household_store - ? WHERE id = ?", (amount, v["id"]))
        _add_food(conn, v["id"], amount)
        conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?",               # sold at the kingdom's price
                     (round(amount * SELL_PRICE * (v["price_level"] or 1.0), 2), v["id"]))
        sold += amount
    conn.commit()
    return round(sold, 2)


def spoil_stores(conn, world_id):
    """Monthly: Food a town holds beyond KEEP_DAYS of its people's eating spoils in part. Returns the Food lost."""
    lost = 0.0
    for c in conn.execute(
            """SELECT c.id, r.amount, (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1)
                      + COALESCE(c.households, 0) * 5 people
               FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id JOIN city_resources r ON r.city_id = c.id AND r.resource_name = 'Food'
               WHERE k.world_id = ?""", (world_id,)).fetchall():
        excess = (c["amount"] or 0) - c["people"] * KEEP_DAYS
        if excess > 0:
            gone = round(excess * SPOIL_SHARE, 2)
            conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Food'", (gone, c["id"]))
            lost += gone
    conn.commit()
    return round(lost, 2)