import random

# Buying food. Every day each household -- everyone sharing a home -- pools
# its adults' gold and buys its food at the city's price, from the city's own
# Food. Children eat but earn nothing, so a family lives on what its adults
# bring in. Jobless adults scrape a little as day labourers. A household that
# still cannot pay may get bread as alms, if its city has a working temple
# and charity funds to spare -- the city's wealth, which those same food sales
# fill. When the funds or the food run out, people go hungry: a household
# that ate only part of its food gets only that part of its meals' hunger
# back (Data.Simulation.simulation_manager.on_hour), and is more stressed and
# less happy -- the ordinary starvation rules and civil-war rules do the rest.

# A jobless adult's daily earnings as a labourer, porter or carter: enough to
# feed one person at normal prices, not a family.
LABOUR_WAGE = 3

# A new world's adults start with this many days of food money each.
STARTING_SAVINGS_DAYS = 30

# A full day without food costs this much stress and happiness (a part-fed
# day costs the unfed share of it).
HUNGER_STRESS = 4
HUNGER_UNHAPPINESS = 3

# Inflation. Each kingdom has a price level (1.0 = normal) that multiplies
# what Food costs, on top of each city's scarcity price. A bad harvest raises
# it -- by more each year in a row: the first by BAD_YEAR_RISE, the second by
# twice that, the third by three times (three bad years running take bread to
# about 2.7x). A good harvest takes it GOOD_YEAR_EASE of the way back toward
# normal; an ordinary year holds it. Wages do not rise with it, so families
# are squeezed. A revolt that topples the crown resets it (reset_price_level).
BAD_YEAR_RISE = 0.20
GOOD_YEAR_EASE = 0.25


def price_level(conn, kingdom_id):
    row = conn.execute("SELECT price_level FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    return row["price_level"] if row and row["price_level"] else 1.0


def food_price(city, conn=None):
    """What a day's meal (1 Food) costs in this city today: dearer when the
    city's stock is low (economy.get_price), times its kingdom's price level
    when a database connection is given."""
    from Data.Economy.economy import get_price
    level = price_level(conn, city["kingdom"]) if conn is not None and city.get("kingdom") else 1.0
    return max(0.01, get_price("Food", city) * level)


def update_price_level(conn, kingdom_id, verdict):
    """
    After a kingdom's harvest: "bad" raises its price level (more for each bad
    year in a row), "good" eases it back toward normal, anything else holds it.
    Returns (old level, new level).
    """

    row = conn.execute("SELECT price_level, bad_harvest_years FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    old, years = row["price_level"], row["bad_harvest_years"]

    if verdict == "bad":
        years += 1
        new = old * (1 + BAD_YEAR_RISE * years)
    else:
        years = 0
        new = 1 + (old - 1) * (1 - GOOD_YEAR_EASE) if verdict == "good" else old

    conn.execute("UPDATE kingdoms SET price_level = ?, bad_harvest_years = ? WHERE id = ?",
                 (round(new, 4), years, kingdom_id))
    return old, round(new, 4)


def reset_price_level(conn, kingdom_id):
    """A revolt that topples the crown wipes the slate clean: prices back to normal."""
    conn.execute("UPDATE kingdoms SET price_level = 1.0, bad_harvest_years = 0 WHERE id = ?", (kingdom_id,))


def give_starting_savings(conn, world_id):
    """A new world's adults have plainly been living somewhere: each starts
    with STARTING_SAVINGS_DAYS of food money at normal prices."""

    from Data.Economy.economy import load_json, RESOURCE_TYPES
    savings = STARTING_SAVINGS_DAYS * load_json(RESOURCE_TYPES)["Food"]["base_price"]

    conn.execute(
        """
        UPDATE npcs SET gold = gold + ? WHERE alive = 1 AND profession != 'Child'
        AND city_id IN (SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?)
        """,
        (savings, world_id)
    )
    conn.commit()


def buy_food(conn, city, has_temple, away_ids=frozenset()):
    """
    One day of food buying in one city. Reads its people's gold fresh from
    the database (the day's wages included), lets each household buy what it
    can afford from city["resources"]["Food"], gives alms where the city can,
    and records how well each person ate (npcs.fed, 0-1), with the stress
    and unhappiness of going without. Food sold and alms given move through
    the city's wealth. Mutates city["resources"]; writes NPCs and wealth to
    the database. Returns a summary.
    """

    people = [dict(row) for row in conn.execute(
        """
        SELECT id, home_id, gold, profession, needs_stress, needs_happiness
        FROM npcs WHERE city_id = ? AND alive = 1
        """, (city["id"],)
    ) if row["id"] not in away_ids]

    if not people:
        return {"households": 0, "fed": 0, "hungry": 0, "alms": 0.0, "sold": 0.0}

    households = {}
    for person in people:
        key = ("home", person["home_id"]) if person["home_id"] is not None else ("alone", person["id"])
        households.setdefault(key, []).append(person)

    price = food_price(city, conn)
    stock = city.setdefault("resources", {}).get("Food", 0)
    wealth = conn.execute("SELECT wealth FROM cities WHERE id = ?", (city["id"],)).fetchone()["wealth"]

    report = {"households": len(households), "fed": 0, "hungry": 0, "alms": 0.0, "sold": 0.0}
    updates = []

    order = list(households.values())
    random.shuffle(order)          # no one is always first to the market

    for members in order:

        need = len(members)
        adults = [m for m in members if m["profession"] != "Child"]
        purse = sum(max(0.0, m["gold"]) for m in adults)

        bought = min(need, purse / price, stock)
        cost = bought * price
        stock -= bought
        wealth += cost
        report["sold"] += bought

        if purse > 0 and cost > 0:
            for m in adults:                  # each adult pays their share of the purse
                m["gold"] = round(m["gold"] - cost * max(0.0, m["gold"]) / purse, 2)

        short = need - bought
        if short > 1e-9 and has_temple:
            alms = min(short, stock, max(0.0, wealth) / price)
            stock -= alms
            wealth -= alms * price
            bought += alms
            report["alms"] += alms

        fed = max(0.0, min(1.0, bought / need))
        report["fed" if fed >= 0.999 else "hungry"] += need

        for m in members:
            m["needs_stress"] = min(100, round(m["needs_stress"] + HUNGER_STRESS * (1 - fed)))
            m["needs_happiness"] = max(0, round(m["needs_happiness"] - HUNGER_UNHAPPINESS * (1 - fed)))
            updates.append((m["gold"], round(fed, 3), m["needs_stress"], m["needs_happiness"], m["id"]))

    city["resources"]["Food"] = round(max(0.0, stock), 2)
    conn.executemany("UPDATE npcs SET gold = ?, fed = ?, needs_stress = ?, needs_happiness = ? WHERE id = ?", updates)
    conn.execute("UPDATE cities SET wealth = ? WHERE id = ?", (round(wealth, 2), city["id"]))

    report["alms"] = round(report["alms"], 2)
    report["sold"] = round(report["sold"], 2)
    return report