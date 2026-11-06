# Kingdom health: three measures, 0-100, worked out each month from what the
# simulation already tracks -- nothing here is a free-floating number.
#
#   Loyalty    the nobility: the lords' loyalty to the crown, weighted by the
#              people in each county (70 for a kingdom with no lords)
#   Unrest     the people:   half their average stress
#                            + 40 x the share of their food they could not get
#                            + 15 for each step bread prices stand above normal
#                            + 20 x the share of villages going hungry
#   Stability  the realm:    100 - half the unrest
#                            - 30 x the share of lords who are restless
#                            - 15 during a regency
#                            - 20 after a rising or civil war in the last two years
#                            - 1 per raid in the last season (at most 15)
#
# A realm whose stability falls below CRISIS_BELOW is in crisis -- logged, and
# the ruler reviews the realm at once rather than waiting for the season
# (Data.Government.edicts).

NO_LORDS_LOYALTY = 70
CRISIS_BELOW = 30

STRESS_WEIGHT, UNFED_WEIGHT, BREAD_WEIGHT, HUNGRY_VILLAGES_WEIGHT = 0.5, 40, 15, 20
UNREST_WEIGHT, RESTLESS_WEIGHT, REGENCY_COST, TURMOIL_COST, TURMOIL_YEARS = 0.5, 30, 15, 20, 2
RAID_COST, RAID_COST_MAX, RAID_MEMORY_DAYS = 1, 15, 90


def _clamp(value):
    return max(0.0, min(100.0, round(value, 1)))


def kingdom_health(conn, kingdom_id, today):
    """{'loyalty', 'unrest', 'stability'} for a kingdom, `today` being the elapsed day."""

    from Data.Government.feudal import RESTLESS_BELOW

    kingdom = conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()

    lords = conn.execute(
        """SELECT p.loyalty, (SELECT COALESCE(SUM(c.population + c.households * 5), 0) FROM cities c WHERE c.province_id = p.id) weight
           FROM provinces p JOIN npcs n ON n.id = p.lord_id WHERE p.kingdom_id = ? AND n.alive = 1""", (kingdom_id,)
    ).fetchall()
    total = sum(max(1, l["weight"]) for l in lords)
    loyalty = sum(l["loyalty"] * max(1, l["weight"]) for l in lords) / total if lords else NO_LORDS_LOYALTY
    restless = sum(1 for l in lords if l["loyalty"] < RESTLESS_BELOW) / len(lords) if lords else 0.0

    people = conn.execute(
        """SELECT AVG(n.needs_stress) stress, AVG(n.fed) fed FROM npcs n JOIN cities c ON c.id = n.city_id
           WHERE c.kingdom_id = ? AND n.alive = 1""", (kingdom_id,)
    ).fetchone()
    villages = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN hungry_days > 0 THEN 1 ELSE 0 END) hungry FROM cities WHERE kingdom_id = ? AND households > 0",
        (kingdom_id,)
    ).fetchone()
    hungry_share = (villages["hungry"] or 0) / villages["n"] if villages["n"] else 0.0

    unrest = (STRESS_WEIGHT * (people["stress"] or 0)
              + UNFED_WEIGHT * (1 - (people["fed"] if people["fed"] is not None else 1))
              + BREAD_WEIGHT * max(0.0, (kingdom["price_level"] or 1) - 1)
              + HUNGRY_VILLAGES_WEIGHT * hungry_share)
    unrest = _clamp(unrest)

    def recent(types, days):
        marks = ",".join("?" * len(types))
        return conn.execute(
            f"""SELECT COUNT(*) FROM events e LEFT JOIN cities c ON c.id = e.city_id
                WHERE e.type IN ({marks}) AND e.day > ? AND (e.kingdom_id = ? OR c.kingdom_id = ?)""",
            (*types, today - days, kingdom_id, kingdom_id)
        ).fetchone()[0]

    stability = (100
                 - UNREST_WEIGHT * unrest
                 - RESTLESS_WEIGHT * restless
                 - (REGENCY_COST if kingdom["regent_id"] else 0)
                 - (TURMOIL_COST if recent(("Rebellion", "Civil War"), TURMOIL_YEARS * 360) else 0)
                 - min(RAID_COST_MAX, RAID_COST * recent(("Raid",), RAID_MEMORY_DAYS)))

    return {"loyalty": _clamp(loyalty), "unrest": unrest, "stability": _clamp(stability)}


def update_health(conn, world_id):
    """
    Monthly: every kingdom's health is worked out and saved; a kingdom that
    falls into crisis is logged. Returns {kingdom id: health}.
    """

    from Data.Database.repository import create_event

    clock = conn.execute("SELECT clock_year, clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
    today = (clock["clock_year"] - 1) * 360 + (clock["clock_month"] - 1) * 30 + (clock["clock_day"] - 1)
    result = {}

    for kingdom in conn.execute("SELECT id, name, health_stability FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():
        health = kingdom_health(conn, kingdom["id"], today)
        conn.execute("UPDATE kingdoms SET health_loyalty = ?, health_unrest = ?, health_stability = ? WHERE id = ?",
                     (health["loyalty"], health["unrest"], health["stability"], kingdom["id"]))
        if kingdom["health_stability"] >= CRISIS_BELOW > health["stability"]:
            create_event(conn, "Crisis",
                         f"{kingdom['name']} has fallen into crisis: stability {health['stability']:.0f}, "
                         f"unrest {health['unrest']:.0f}, the lords' loyalty {health['loyalty']:.0f}.",
                         kingdom_id=kingdom["id"])
            # the ruler acts at once, not at the turn of the season
            from Data.Government.edicts import review
            review(conn, world_id, kingdom_ids=[kingdom["id"]], crisis=True)
        result[kingdom["id"]] = health

    conn.commit()
    return result