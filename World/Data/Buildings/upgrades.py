import json
from pathlib import Path

# Town treasuries and building upgrades.
#
#   The town tax: each month every town takes TOWN_TAX_RATE of its adults'
#   savings into its own purse (cities.wealth). A town in debt to the crown
#   pays half of it toward the debt.
#
#   Upgrades: buildings rise from level 1 to MAX_LEVEL, each level adding
#   LEVEL_GAIN of the building's original places; an upgraded building is
#   rebuilt like new. Output follows, since production is per worker.
#
#   Public works: when the ruler decrees them (Data.Government.edicts), each
#   town upgrades up to UPGRADES_PER_TOWN workplaces that are full while its
#   jobless wait -- those making what the town lacks first. Each added place
#   costs the town COST_PER_PLACE in materials and GOLD_PER_PLACE in gold;
#   short of gold, it borrows the rest from the crown, which lends while it
#   keeps at least CROWN_KEEPS of its treasury. Otherwise the town goes
#   without. The new places are filled at once by the job market.

TOWN_TAX_RATE = 0.02
DEBT_REPAY_SHARE = 0.5
MAX_LEVEL = 3
LEVEL_GAIN = 0.5
COST_PER_PLACE = {"Wood": 10, "Stone": 10, "Tools": 2}
GOLD_PER_PLACE = 25
UPGRADES_PER_TOWN = 3
MIN_JOBLESS = 3                 # a town calls for works when at least this many wait for work
CROWN_KEEPS = 0.5
ADULT_AGE = 16

DATA = Path(__file__).resolve().parents[1]


def collect_town_taxes(conn, world_id):
    """Monthly: every town taxes its adults' savings; debtors repay the crown. Returns (taxed, repaid)."""
    taxed = repaid = 0.0
    for city in conn.execute(
        """SELECT c.id, c.debt, c.kingdom_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)).fetchall():
        tax = conn.execute("SELECT COALESCE(SUM(gold), 0) FROM npcs WHERE city_id = ? AND alive = 1 AND age >= ? AND gold > 0",
                           (city["id"], ADULT_AGE)).fetchone()[0] * TOWN_TAX_RATE
        if tax <= 0:
            continue
        conn.execute("UPDATE npcs SET gold = ROUND(gold * ?, 2) WHERE city_id = ? AND alive = 1 AND age >= ? AND gold > 0",
                     (1 - TOWN_TAX_RATE, city["id"], ADULT_AGE))
        repay = min(city["debt"] or 0, tax * DEBT_REPAY_SHARE)
        conn.execute("UPDATE cities SET wealth = wealth + ?, debt = debt - ? WHERE id = ?", (round(tax - repay, 2), round(repay, 2), city["id"]))
        if repay:
            conn.execute("UPDATE kingdoms SET treasury = treasury + ? WHERE id = ?", (round(repay, 2), city["kingdom_id"]))
        taxed += tax
        repaid += repay
    conn.commit()
    return round(taxed, 2), round(repaid, 2)


def places_added(building_type):
    base = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))[building_type]["capacity"]
    return max(1, round(base * LEVEL_GAIN))


def upgradable(conn, city_id):
    """The town's workplaces worth upgrading now: full, below MAX_LEVEL, while at least
    MIN_JOBLESS wait for work -- those making what the town lacks first."""
    from Data.Economy.jobs import HIRING_CATEGORIES
    from Data.Database.repository import get_city
    from Data.Economy.economy import get_target_stock
    jobless = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Unemployed' AND age >= ?",
                           (city_id, ADULT_AGE)).fetchone()[0]
    if jobless < MIN_JOBLESS:
        return []
    production = json.load(open(DATA / "Economy" / "BuildingProduction.json"))
    city = get_city(conn, city_id)
    marks = ",".join("?" * len(HIRING_CATEGORIES))
    full = [dict(b) for b in conn.execute(
        f"""SELECT b.id, b.type, b.capacity, b.level FROM buildings b WHERE b.city_id = ? AND b.category IN ({marks}) AND b.level < ?
              AND (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) >= b.capacity""",
        (city_id, *HIRING_CATEGORIES, MAX_LEVEL))]

    def shortage(b):
        resource = production.get(b["type"], {}).get("resource")
        if not resource:
            return 0.0
        target = get_target_stock(resource, city)
        return max(0.0, 1 - city["resources"].get(resource, 0) / target) if target else 0.0

    return sorted(full, key=lambda b: (-shortage(b), b["id"]))


def can_pay(conn, city, kingdom, places):
    """(materials ok, gold the town has, gold it must borrow, can the crown lend it)"""
    materials = all(city["resources"].get(r, 0) >= n * places for r, n in COST_PER_PLACE.items())
    gold = GOLD_PER_PLACE * places
    purse = conn.execute("SELECT wealth FROM cities WHERE id = ?", (city["id"],)).fetchone()[0]
    from_town = min(max(0.0, purse), gold)
    loan = gold - from_town
    crown = conn.execute("SELECT treasury FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone()[0]
    return materials, from_town, loan, loan <= 0 or crown - loan >= CROWN_KEEPS * crown


def works_need(conn, kingdom):
    """The share of the kingdom's towns (not villages) that are bursting and could pay for at least one upgrade."""
    from Data.Database.repository import get_city
    towns = [r[0] for r in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village'", (kingdom["id"],))]
    if not towns:
        return 0.0
    ready = 0
    for town in towns:
        options = upgradable(conn, town)
        if options:
            city = get_city(conn, town)
            materials, _, _, lendable = can_pay(conn, city, kingdom, places_added(options[0]["type"]))
            ready += materials and lendable
    return ready / len(towns)


def public_works(conn, kingdom):
    """The ruler's public works: each town upgrades its most needed full workplaces. Returns a summary."""
    from Data.Database.repository import get_city, save_city
    from Data.Economy.jobs import hire_jobless

    upgraded, towns, borrowed = 0, 0, 0.0
    for town in [r[0] for r in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village'", (kingdom["id"],))]:
        done_here = 0
        for building in upgradable(conn, town)[:UPGRADES_PER_TOWN]:
            city = get_city(conn, town)
            places = places_added(building["type"])
            materials, from_town, loan, lendable = can_pay(conn, city, kingdom, places)
            if not materials or not lendable:
                continue
            for resource, per_place in COST_PER_PLACE.items():
                city["resources"][resource] = round(city["resources"][resource] - per_place * places, 2)
            save_city(conn, city)
            conn.execute("UPDATE cities SET wealth = wealth - ?, debt = debt + ? WHERE id = ?", (from_town, loan, town))
            if loan:
                conn.execute("UPDATE kingdoms SET treasury = treasury - ? WHERE id = ?", (loan, kingdom["id"]))
                borrowed += loan
            conn.execute("UPDATE buildings SET capacity = capacity + ?, level = level + 1, condition = 100 WHERE id = ?",
                         (places, building["id"]))
            upgraded += 1
            done_here += 1
        towns += done_here > 0

    if upgraded:
        hire_jobless(conn, kingdom["world_id"])
    conn.commit()
    return {"upgraded": upgraded, "towns": towns, "borrowed": round(borrowed, 2)}