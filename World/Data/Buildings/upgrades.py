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
#
#   New buildings: a town whose jobless still wait after its upgrades builds
#   up to BUILDS_PER_TOWN new workplaces, of the kinds its kit allows for a
#   town of its size, those making what it lacks first; a town with at least
#   HOMELESS_FOR_HOUSE homeless adults builds a House (up to BUILDS_PER_TOWN,
#   apart from the workplaces: a roof does not wait behind jobs). A new building costs
#   NEW_BUILDING_FACTOR times an upgrade's price per place (the foundations),
#   paid the same way.

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
NEW_BUILDING_FACTOR = 1.5
BUILDS_PER_TOWN = 2
HOMELESS_FOR_HOUSE = 5

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


def can_pay(conn, city, kingdom, places, factor=1.0):
    """(materials ok, gold the town has, gold it must borrow, can the crown lend it)"""
    materials = all(city["resources"].get(r, 0) >= n * places * factor for r, n in COST_PER_PLACE.items())
    gold = GOLD_PER_PLACE * places * factor
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
    types = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))
    ready = 0
    for town in towns:
        city = get_city(conn, town)
        options = upgradable(conn, town)
        can = False
        if options:
            materials, _, _, lendable = can_pay(conn, city, kingdom, places_added(options[0]["type"]))
            can = materials and lendable
        if not can and jobless_count(conn, town) >= MIN_JOBLESS:          # or it could build anew
            for building_type in buildable(conn, town)[:3]:
                materials, _, _, lendable = can_pay(conn, city, kingdom, types[building_type]["capacity"], NEW_BUILDING_FACTOR)
                if materials and lendable:
                    can = True
                    break
        ready += can
    return ready / len(towns)


def public_works(conn, kingdom):
    """The ruler's public works: each town upgrades its most needed full workplaces. Returns a summary."""
    from Data.Database.repository import get_city, save_city
    from Data.Economy.jobs import hire_jobless

    upgraded, built, towns, borrowed = 0, 0, 0, 0.0
    for town in [r[0] for r in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village'", (kingdom["id"],))]:
        done_here = 0
        added = 0
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
            added += places
        # then new buildings for whoever still waits (and Houses for the homeless)
        new, loan = build_new(conn, town, kingdom, jobless_count(conn, town) - added)
        built += new
        borrowed += loan
        towns += (done_here + new) > 0

    if upgraded or built:
        hire_jobless(conn, kingdom["world_id"])
    conn.commit()
    return {"upgraded": upgraded, "built": built, "towns": towns, "borrowed": round(borrowed, 2)}



def jobless_count(conn, city_id):
    return conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Unemployed' AND age >= ?",
                        (city_id, ADULT_AGE)).fetchone()[0]


def homeless_count(conn, city_id):
    return conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1 AND home_id IS NULL AND age >= ?",
                        (city_id, ADULT_AGE)).fetchone()[0]


def buildable(conn, city_id):
    """The kinds of workplace this town may build (its kit's, for a town of its size), those making what it lacks first."""
    from Data.Economy.jobs import HIRING_CATEGORIES, trade_for_building
    from Data.Database.repository import get_city
    from Data.Economy.economy import get_target_stock
    city = get_city(conn, city_id)
    types = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))
    kit = json.load(open(DATA / "Buildings" / "CityBuildingKits.json")).get(city["type"], {})
    production = json.load(open(DATA / "Economy" / "BuildingProduction.json"))
    trades = trade_for_building()
    allowed = [t for t, bounds in kit.items() if bounds[1] >= 1 and t in types and t in trades and types[t]["category"] in HIRING_CATEGORIES]

    def shortage(t):
        resource = production.get(t, {}).get("resource")
        target = get_target_stock(resource, city) if resource else 0
        return max(0.0, 1 - city["resources"].get(resource, 0) / target) if target else 0.0

    return sorted(allowed, key=lambda t: (-shortage(t), -types[t]["capacity"], t))


def construct(conn, city_id, building_type):
    """A new building of this type in the town, named and placed in a district as the generator does."""
    from Data.Database.repository import create_building
    data = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))[building_type]
    building = create_building(conn, city_id, f"{building_type} #_", building_type, data["category"], data["capacity"])
    # districts are one per category in each town (Data.Cities.districts): join it, or found it
    district = conn.execute("SELECT id FROM districts WHERE city_id = ? AND category = ? ORDER BY id LIMIT 1",
                            (city_id, data["category"])).fetchone()
    district_id = district[0] if district else conn.execute(
        "INSERT INTO districts (city_id, name, category) VALUES (?, ?, ?)", (city_id, f"{data['category']} District", data["category"])).lastrowid
    conn.execute("UPDATE buildings SET name = ?, district_id = ? WHERE id = ?", (f"{building_type} #{building['id']}", district_id, building["id"]))
    return building["id"]


def pay(conn, city_id, kingdom, places, factor):
    """Takes the price of `places` from the town (the crown lending what its purse lacks). Returns the loan, or None if it cannot pay."""
    from Data.Database.repository import get_city, save_city
    city = get_city(conn, city_id)
    materials, from_town, loan, lendable = can_pay(conn, city, kingdom, places, factor)
    if not materials or not lendable:
        return None
    for resource, per_place in COST_PER_PLACE.items():
        city["resources"][resource] = round(city["resources"][resource] - per_place * places * factor, 2)
    save_city(conn, city)
    conn.execute("UPDATE cities SET wealth = wealth - ?, debt = debt + ? WHERE id = ?", (from_town, loan, city_id))
    if loan:
        conn.execute("UPDATE kingdoms SET treasury = treasury - ? WHERE id = ?", (loan, kingdom["id"]))
    return loan


def build_new(conn, city_id, kingdom, jobless_waiting):
    """After its upgrades, a town builds new workplaces for jobless still waiting, and Houses for the homeless.
    Returns (buildings built, gold borrowed)."""
    types = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))
    built, borrowed = 0, 0.0
    for building_type in buildable(conn, city_id):
        if built >= BUILDS_PER_TOWN or jobless_waiting < MIN_JOBLESS:
            break
        places = types[building_type]["capacity"]
        loan = pay(conn, city_id, kingdom, places, NEW_BUILDING_FACTOR)
        if loan is None:
            continue
        construct(conn, city_id, building_type)
        built += 1
        borrowed += loan
        jobless_waiting -= places

    homeless = [r[0] for r in conn.execute(
        "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND home_id IS NULL AND age >= ? ORDER BY id", (city_id, ADULT_AGE))]
    house = types["House"]["capacity"]
    houses = 0                         # Houses have their own allowance: a roof does not wait behind jobs
    while len(homeless) >= HOMELESS_FOR_HOUSE and houses < BUILDS_PER_TOWN:
        loan = pay(conn, city_id, kingdom, house, NEW_BUILDING_FACTOR)
        if loan is None:
            break
        home = construct(conn, city_id, "House")
        for npc_id in homeless[:house]:
            conn.execute("UPDATE npcs SET home_id = ? WHERE id = ?", (home, npc_id))
        homeless = homeless[house:]
        houses += 1
        built += 1
        borrowed += loan
    return built, borrowed