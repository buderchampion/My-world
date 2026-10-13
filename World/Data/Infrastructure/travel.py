import random

# How many days an NPC typically stays at their destination before
# heading home, once real trip PURPOSES (tourism, migration, etc.)
# start actually calling dispatch_npc_travel. Lives here since it's a
# property of travel itself, not any one purpose.
DEFAULT_STAY_DAYS_RANGE = (3, 10)

# Used only if no direct road record exists between two cities
# (should be rare, given the road network is a full mesh between
# every kingdom capital) -- a conservative fallback so a trip can
# never silently fail to get a duration.
FALLBACK_TRAVEL_DAYS = 5


def get_travel_days(conn, city_a_id, city_b_id):
    """Looks up the travel_days for the road connecting these two
    cities, regardless of which order they're given in (roads are
    always stored with the lower id first). Same lookup Caravans use
    -- an NPC and a caravan travel the same real road network."""

    a_id, b_id = sorted((city_a_id, city_b_id))

    row = conn.execute(
        "SELECT travel_days FROM roads WHERE city_a_id = ? AND city_b_id = ?", (a_id, b_id)
    ).fetchone()

    return row["travel_days"] if row else FALLBACK_TRAVEL_DAYS


def dispatch_npc_travel(conn, npc, destination_city_id, purpose, stay_days=None):
    """
    Sends an NPC on a trip: travels the real road network to
    destination_city_id, stays there a while, then automatically
    heads back home. Doesn't touch the NPC's home/workplace/city_id
    at all -- this is a temporary trip, not a permanent move.

    stay_days defaults to a random pick from DEFAULT_STAY_DAYS_RANGE
    if not given. purpose is just a label for now (e.g. "Tourism") --
    the infrastructure itself doesn't behave differently based on it,
    that's for whatever future logic decides trips should happen.
    """

    if stay_days is None:
        stay_days = random.randint(*DEFAULT_STAY_DAYS_RANGE)

    travel_days = get_travel_days(conn, npc["city_id"], destination_city_id)

    cursor = conn.execute(
        """
        INSERT INTO npc_travels
            (npc_id, origin_city_id, destination_city_id, purpose, status, days_remaining, planned_stay_days)
        VALUES (?, ?, ?, ?, 'Traveling Out', ?, ?)
        """,
        (npc["id"], npc["city_id"], destination_city_id, purpose, travel_days, stay_days)
    )

    return dict(conn.execute("SELECT * FROM npc_travels WHERE id = ?", (cursor.lastrowid,)).fetchone())


def get_traveling_npc_ids(conn, world_id):
    """
    The set of NPC ids currently away from home for any reason
    (Traveling Out, Staying, or Traveling Back -- anything except
    Completed). on_hour() uses this to skip normal home/work
    schedule resolution for anyone who isn't actually there.
    """

    rows = conn.execute(
        """
        SELECT DISTINCT t.npc_id FROM npc_travels t
        JOIN npcs n ON n.id = t.npc_id
        JOIN cities c ON c.id = n.city_id
        JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND t.status != 'Completed'
        """,
        (world_id,)
    ).fetchall()

    return {row["npc_id"] for row in rows}


def advance_npc_travels(conn, world_id):
    """
    One day of progress for every active trip in this world.
    Decrements days_remaining for whichever phase a trip is
    currently in, and transitions it to the next phase once that
    countdown reaches zero:

        Traveling Out -> Staying -> Traveling Back -> Completed

    Migration-purpose trips are the one exception: arriving IS the
    point, so they skip straight from Traveling Out to Completed with
    no stay and no trip home. The caller is responsible for actually
    executing the relocation for any arrived trip whose purpose is
    "Migration" (see Data.Infrastructure.migration.relocate_npc) --
    this function only manages the journey itself.

    Returns {"arrived": [...], "returned": [...]} (lists of trip
    dicts), mostly for logging/testing.
    """

    trips = conn.execute(
        """
        SELECT t.* FROM npc_travels t
        JOIN npcs n ON n.id = t.npc_id
        JOIN cities c ON c.id = n.city_id
        JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND t.status != 'Completed'
        """,
        (world_id,)
    ).fetchall()

    arrived = []
    returned = []

    for trip in trips:

        trip = dict(trip)
        remaining = trip["days_remaining"] - 1

        if remaining > 0:
            conn.execute("UPDATE npc_travels SET days_remaining = ? WHERE id = ?", (remaining, trip["id"]))
            continue

        if trip["status"] == "Traveling Out":

            if trip["purpose"] == "Migration":
                conn.execute(
                    "UPDATE npc_travels SET status = 'Completed', days_remaining = 0 WHERE id = ?",
                    (trip["id"],)
                )
            else:
                conn.execute(
                    "UPDATE npc_travels SET status = 'Staying', days_remaining = ? WHERE id = ?",
                    (trip["planned_stay_days"], trip["id"])
                )

            arrived.append(trip)

        elif trip["status"] == "Staying":

            return_days = get_travel_days(conn, trip["origin_city_id"], trip["destination_city_id"])
            conn.execute(
                "UPDATE npc_travels SET status = 'Traveling Back', days_remaining = ? WHERE id = ?",
                (return_days, trip["id"])
            )

        elif trip["status"] == "Traveling Back":

            conn.execute(
                "UPDATE npc_travels SET status = 'Completed', days_remaining = 0 WHERE id = ?",
                (trip["id"],)
            )
            returned.append(trip)

    return {"arrived": arrived, "returned": returned}


# Gold an NPC needs on hand to even consider a leisure trip -- purely
# an eligibility gate for now, not spent up front (real spending
# happens gradually via collect_tourist_revenue while they're there).
TOURISM_MIN_GOLD = 50

# An NPC needs to be doing reasonably well to consider tourism -- this
# is a LEISURE trip for someone who's fine, not an escape for someone
# who's desperate (that's migration, a separate, later mechanic).
TOURISM_MIN_HAPPINESS = 50
TOURISM_MAX_STRESS = 50

# Chance, per month, that an eligible NPC actually decides to go.
TOURISM_CHANCE_PER_MONTH = 0.01

# A destination kingdom needs to be at least this much happier than
# home to be worth the trip -- nobody vacations somewhere worse off.
TOURISM_MIN_HAPPINESS_ADVANTAGE = 5


def consider_tourism(conn, world_id):
    """
    Monthly: NPCs who are doing well (happy enough, not too stressed,
    have some spare gold) have a small chance of deciding to travel
    somewhere nicer purely for leisure. The destination is chosen
    weighted toward kingdoms with a meaningfully higher average
    happiness than home -- a kingdom's reputation for being a good
    place to be is what actually draws visitors here, not randomness.

    Skips anyone already traveling. Returns how many new trips got
    dispatched, for testing/logging.
    """

    from Data.Database.repository import get_world_kingdoms, get_kingdom_cities, get_city_npcs

    kingdoms = get_world_kingdoms(conn, world_id)

    kingdom_happiness = {}
    kingdom_capitals = {}
    kingdom_cities_cache = {}

    for kingdom in kingdoms:

        cities = get_kingdom_cities(conn, kingdom["id"])
        kingdom_cities_cache[kingdom["id"]] = cities
        kingdom_capitals[kingdom["id"]] = kingdom.get("capital")

        living = [npc for city in cities for npc in get_city_npcs(conn, city["id"]) if npc["alive"]]

        if living:
            kingdom_happiness[kingdom["id"]] = sum(npc["needs"]["happiness"] for npc in living) / len(living)

    traveling_ids = get_traveling_npc_ids(conn, world_id)

    dispatched = 0

    for kingdom in kingdoms:

        home_happiness = kingdom_happiness.get(kingdom["id"])
        if home_happiness is None:
            continue

        candidates = [
            (other_id, happiness) for other_id, happiness in kingdom_happiness.items()
            if other_id != kingdom["id"] and happiness >= home_happiness + TOURISM_MIN_HAPPINESS_ADVANTAGE
        ]

        if not candidates:
            continue

        candidate_ids = [c[0] for c in candidates]
        candidate_weights = [c[1] for c in candidates]

        for city in kingdom_cities_cache[kingdom["id"]]:
            for npc in get_city_npcs(conn, city["id"]):

                if not npc["alive"] or npc["id"] in traveling_ids:
                    continue

                if npc["needs"]["happiness"] < TOURISM_MIN_HAPPINESS:
                    continue
                if npc["needs"]["stress"] > TOURISM_MAX_STRESS:
                    continue
                if npc["gold"] < TOURISM_MIN_GOLD:
                    continue

                if random.random() >= TOURISM_CHANCE_PER_MONTH:
                    continue

                destination_kingdom_id = random.choices(candidate_ids, weights=candidate_weights, k=1)[0]
                destination_capital = kingdom_capitals[destination_kingdom_id]

                if destination_capital is None:
                    continue

                dispatch_npc_travel(conn, npc, destination_capital, purpose="Tourism")
                dispatched += 1

    return dispatched


# Gold added to a kingdom's treasury per day, per NPC actually
# visiting it as a tourist (Staying, purpose Tourism) -- real spending
# from real, named visitors currently there. Distinct from on_day's
# ambient visitor-revenue formula, which reflects a happy city's
# general reputation rather than specific people; this stacks on top
# of that as the "real tourists happening on top of the theory".
TOURIST_SPENDING_PER_DAY = 3.0


def collect_tourist_revenue(conn, world_id):
    """
    Daily: every kingdom collects treasury income for each NPC
    currently visiting it as a tourist. Returns total revenue
    collected across the world, for testing/logging.
    """

    from Data.Database.repository import add_treasury

    rows = conn.execute(
        """
        SELECT c.kingdom_id as kingdom_id, COUNT(*) as n
        FROM npc_travels t
        JOIN cities c ON c.id = t.destination_city_id
        JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND t.status = 'Staying' AND t.purpose = 'Tourism'
        GROUP BY c.kingdom_id
        """,
        (world_id,)
    ).fetchall()

    total = 0.0

    for row in rows:

        revenue = round(row["n"] * TOURIST_SPENDING_PER_DAY, 2)

        if revenue > 0:
            add_treasury(conn, row["kingdom_id"], revenue)
            total += revenue

    return total