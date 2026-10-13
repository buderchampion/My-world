import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

RESOURCE_TYPES = DATA_DIR / "Economy" / "ResourceTypes.json"
BUILDING_PRODUCTION = DATA_DIR / "Economy" / "BuildingProduction.json"
CONSUMPTION_RATES = DATA_DIR / "Economy" / "ConsumptionRates.json"

# For consumable resources (currently just Food), the "target stock" a
# city aims to hold scales with its own population instead of being a
# flat number -- a Capital of 700 and a Village of 80 cannot sensibly
# share the same target. This is how many days of consumption a city
# tries to keep in reserve.
CONSUMABLE_BUFFER_DAYS = 3

# Price multiplier is clamped between these so a resource never goes
# free (deep surplus) or absurdly expensive (deep shortage).
MIN_PRICE_MULTIPLIER = 0.5
MAX_PRICE_MULTIPLIER = 2.0

# A city counts as having a surplus/shortage of a resource once its
# stock crosses these multiples of that resource's target_stock.
SURPLUS_THRESHOLD = 1.5
SHORTAGE_THRESHOLD = 0.5


from functools import lru_cache


@lru_cache(maxsize=None)
def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def get_target_stock(resource, city, resource_types=None, consumption_rates=None):

    if resource_types is None:
        resource_types = load_json(RESOURCE_TYPES)

    info = resource_types[resource]

    if not info.get("consumable"):
        return info["target_stock"]

    if consumption_rates is None:
        consumption_rates = load_json(CONSUMPTION_RATES)

    rate = consumption_rates.get(resource)

    if not rate:
        return info["target_stock"]

    return city["population"] * rate * CONSUMABLE_BUFFER_DAYS


def produce_resources(city):
    """
    One day of production: every worker in a producing building adds
    that building type's output to the city's resource stockpile.

    Some buildings (a production CHAIN's later stages -- e.g. a Mill
    turning Grain into Flour) also require an input resource. If the
    city doesn't have enough of that input on hand, output scales down
    proportionally to however much input is actually available, rather
    than producing from nothing or crashing. Buildings with no
    input_resource in their rule are completely unaffected by this --
    same behavior as before this existed.

    A building can also have a secondary_resource (e.g. Blacksmith
    Shop makes both Tools and Weapons from the same Steel budget) --
    the primary and secondary outputs share the same input and the
    same fulfillment fraction, they just produce two different things
    from it. Buildings with no secondary_resource are unaffected.
    """

    building_production = load_json(BUILDING_PRODUCTION)

    if "resources" not in city:
        city["resources"] = {}

    for building in city["buildings"]:

        rule = building_production.get(building["type"])

        if not rule:
            continue

        workers = len(building["workers"])

        if workers <= 0:
            continue

        desired_amount = workers * rule["amount_per_worker"]
        resource = rule["resource"]

        secondary_resource = rule.get("secondary_resource")
        secondary_desired = workers * rule.get("secondary_amount_per_worker", 0) if secondary_resource else 0

        input_resource = rule.get("input_resource")

        if input_resource:

            input_needed = workers * rule.get("input_per_worker", 0)

            if input_needed <= 0:
                continue

            available_input = city["resources"].get(input_resource, 0)
            fulfillment = min(1.0, available_input / input_needed)

            if fulfillment <= 0:
                continue

            actual_amount = round(desired_amount * fulfillment, 2)
            actual_secondary = round(secondary_desired * fulfillment, 2)
            input_consumed = round(input_needed * fulfillment, 2)

            city["resources"][input_resource] = round(available_input - input_consumed, 2)

        else:
            actual_amount = desired_amount
            actual_secondary = secondary_desired

        city["resources"][resource] = round(
            city["resources"].get(resource, 0) + actual_amount, 2
        )

        if secondary_resource and actual_secondary > 0:
            city["resources"][secondary_resource] = round(
                city["resources"].get(secondary_resource, 0) + actual_secondary, 2
            )

    return city


def consume_resources(city):
    """
    One day of consumption: the population eats through consumable
    resources (currently just Food). Stock is floored at 0 rather than
    going negative -- a real "famine/deficit" state is a later phase.
    """

    consumption = load_json(CONSUMPTION_RATES)

    if "resources" not in city:
        city["resources"] = {}

    for resource, rate in consumption.items():

        demand = round(city["population"] * rate, 2)

        current = city["resources"].get(resource, 0)

        city["resources"][resource] = max(0, round(current - demand, 2))

    return city


def simulate_city_economy_day(city):
    """
    Applies one day of production followed by one day of consumption.
    No day/tick loop calls this yet -- ready for a later simulation
    phase to call repeatedly, same pattern as employment.apply_workday.
    """

    produce_resources(city)
    consume_resources(city)

    return city


def get_price(resource, city):

    resource_types = load_json(RESOURCE_TYPES)

    info = resource_types[resource]

    base_price = info["base_price"]
    target = get_target_stock(resource, city, resource_types)

    stock = city.get("resources", {}).get(resource, 0)

    if stock <= 0:
        return round(base_price * MAX_PRICE_MULTIPLIER, 2)

    multiplier = target / stock

    multiplier = max(MIN_PRICE_MULTIPLIER, min(multiplier, MAX_PRICE_MULTIPLIER))

    return round(base_price * multiplier, 2)


def total_resource_value(city):

    return sum(
        amount * get_price(resource, city)
        for resource, amount in city.get("resources", {}).items()
    )


def classify_supply(resource, city):

    resource_types = load_json(RESOURCE_TYPES)

    target = get_target_stock(resource, city, resource_types)

    stock = city.get("resources", {}).get(resource, 0)

    if stock >= target * SURPLUS_THRESHOLD:
        return "Surplus"

    if stock <= target * SHORTAGE_THRESHOLD:
        return "Shortage"

    return "Balanced"


# Fraction of a city's outstanding shortage that can be resolved by
# trade in a single day. Using a fraction instead of a flat number
# means this scales correctly regardless of kingdom/population size --
# a flat cap that works for a small world becomes a bottleneck for a
# larger one (and vice versa).
DEFAULT_TRADE_FRACTION = 0.5


def simulate_kingdom_trade_day(cities, trade_fraction=DEFAULT_TRADE_FRACTION):
    """
    One day of inter-city trade: for each resource, cities sitting on a
    surplus (above SURPLUS_THRESHOLD x target) ship a portion of it to
    cities in shortage (below SHORTAGE_THRESHOLD x target), split
    proportionally to how far over/under each city is. Each day
    resolves up to `trade_fraction` of the total outstanding shortage
    (capped by whatever surplus actually exists), so it's a gradual
    rebalancing that converges over a few days rather than snapping to
    equilibrium instantly. Mutates each city's "resources" in place and
    returns a log of what moved.
    """

    resource_types = load_json(RESOURCE_TYPES)
    consumption_rates = load_json(CONSUMPTION_RATES)

    trade_log = []

    for resource, info in resource_types.items():

        surplus_cities = []
        shortage_cities = []

        for city in cities:

            target = get_target_stock(resource, city, resource_types, consumption_rates)

            stock = city.get("resources", {}).get(resource, 0)

            if stock >= target * SURPLUS_THRESHOLD:
                surplus_cities.append((city, stock - target))

            elif stock <= target * SHORTAGE_THRESHOLD:
                shortage_cities.append((city, target - stock))

        if not surplus_cities or not shortage_cities:
            continue

        total_surplus = sum(amount for _, amount in surplus_cities)
        total_shortage = sum(amount for _, amount in shortage_cities)

        tradeable = min(total_surplus, total_shortage * trade_fraction)

        if tradeable <= 0:
            continue

        for city, need in shortage_cities:

            share = round(tradeable * (need / total_shortage), 2)

            city["resources"][resource] = round(
                city["resources"].get(resource, 0) + share, 2
            )

            trade_log.append(
                {
                    "resource": resource,
                    "direction": "import",
                    "city": city["name"],
                    "amount": share,
                }
            )

        for city, available in surplus_cities:

            share = round(tradeable * (available / total_surplus), 2)

            city["resources"][resource] = round(
                city["resources"].get(resource, 0) - share, 2
            )

            trade_log.append(
                {
                    "resource": resource,
                    "direction": "export",
                    "city": city["name"],
                    "amount": share,
                }
            )


# How much of a kingdom's outstanding shortage cross-kingdom trade can
# resolve in a single day -- deliberately smaller than the within-
# kingdom trade_fraction (0.5), since this only kicks in for whatever
# shortage internal redistribution couldn't already fix, and involves
# real gold rather than free barter.
DEFAULT_CROSS_KINGDOM_TRADE_FRACTION = 0.3

# Every successful cross-kingdom trade shifts favor for both sides --
# the buyer's favor toward the seller rises (real aid received), and
# the seller's favor toward the buyer dips slightly (giving up stock
# someone else benefits from). The gain is deliberately 3x the loss:
# two kingdoms trading back and forth nets out POSITIVE for both over
# time (a real, growing friendship), while a kingdom that only ever
# gives and never receives anything back still drifts down on its
# own, exactly the "one-sided trade route wears thin" dynamic wanted.
TRADE_FAVOR_GAIN = 3.0
TRADE_FAVOR_LOSS = -1.0


def simulate_cross_kingdom_trade_day(
    conn, world_id, trade_fraction=DEFAULT_CROSS_KINGDOM_TRADE_FRACTION
):
    """
    One day of inter-KINGDOM trade: after each kingdom's own internal
    trade has already run, any kingdom still sitting on an aggregate
    shortage of a resource can buy it from another kingdom with an
    aggregate surplus, paying real gold from its treasury rather than
    the free barter that within-kingdom trade uses. Goods move through
    each kingdom's capital city, the natural hub given the Trade Route
    road network already connects capitals directly.

    A seller's available surplus is tracked and depleted as sales
    happen within this same call, so multiple buyers competing for the
    same seller's stock in one day can't collectively over-buy it.
    Every purchase re-checks the buyer's CURRENT treasury balance
    directly from the database (not a possibly-stale in-memory value),
    so a kingdom that spends across several resources in the same day
    can never go into debt.

    Returns a log of every trade that happened, mostly for testing.
    """

    from Data.Database.repository import (
        get_world_kingdoms,
        get_kingdom_cities,
        get_city,
        save_city,
        spend_treasury,
        add_treasury,
        adjust_kingdom_favor,
    )

    resource_types = load_json(RESOURCE_TYPES)
    consumption_rates = load_json(CONSUMPTION_RATES)

    kingdoms = get_world_kingdoms(conn, world_id)
    cities_by_kingdom = {k["id"]: get_kingdom_cities(conn, k["id"]) for k in kingdoms}

    trade_log = []

    for resource in resource_types:

        surplus_entries = (
            []
        )  # [kingdom, remaining_surplus] -- remaining_surplus shrinks as sales happen
        shortage_entries = []  # [kingdom, remaining_need]

        for kingdom in kingdoms:

            cities = cities_by_kingdom[kingdom["id"]]

            total_stock = sum(c.get("resources", {}).get(resource, 0) for c in cities)
            total_target = sum(
                get_target_stock(resource, c, resource_types, consumption_rates)
                for c in cities
            )

            if total_target <= 0:
                continue

            if total_stock >= total_target * SURPLUS_THRESHOLD:
                surplus_entries.append([kingdom, total_stock - total_target])
            elif total_stock <= total_target * SHORTAGE_THRESHOLD:
                shortage_entries.append([kingdom, total_target - total_stock])

        if not surplus_entries or not shortage_entries:
            continue

        for buyer_entry in shortage_entries:

            buyer_kingdom, need = buyer_entry

            buyer_capital_id = buyer_kingdom.get("capital")
            if buyer_capital_id is None:
                continue

            surplus_entries.sort(key=lambda entry: -entry[1])

            for seller_entry in surplus_entries:

                if need <= 0:
                    break

                seller_kingdom, available = seller_entry

                if available <= 0 or seller_kingdom["id"] == buyer_kingdom["id"]:
                    continue

                seller_capital_id = seller_kingdom.get("capital")
                if seller_capital_id is None:
                    continue
                seller_capital = get_city(conn, seller_capital_id)

                amount = round(min(need, available) * trade_fraction, 2)
                if amount <= 0:
                    continue

                price_per_unit = get_price(resource, seller_capital)
                if price_per_unit <= 0:
                    continue

                current_treasury = conn.execute(
                    "SELECT treasury FROM kingdoms WHERE id = ?", (buyer_kingdom["id"],)
                ).fetchone()["treasury"]

                affordable_amount = round(current_treasury / price_per_unit, 2)
                amount = min(amount, affordable_amount)

                if amount <= 0:
                    continue

                cost = round(amount * price_per_unit, 2)

                if cost <= 0 or not spend_treasury(conn, buyer_kingdom["id"], cost):
                    continue

                add_treasury(conn, seller_kingdom["id"], cost)

                adjust_kingdom_favor(conn, buyer_kingdom["id"], seller_kingdom["id"], TRADE_FAVOR_GAIN)
                adjust_kingdom_favor(conn, seller_kingdom["id"], buyer_kingdom["id"], TRADE_FAVOR_LOSS)

                seller_capital["resources"][resource] = round(seller_capital["resources"].get(resource, 0) - amount, 2)
                save_city(conn, seller_capital)

                from Data.Infrastructure.caravans import dispatch_caravan
                dispatch_caravan(
                    conn, seller_capital["id"], buyer_capital_id, resource, amount,
                    buyer_kingdom["id"], seller_kingdom["id"]
                )

                seller_entry[1] -= amount
                need -= amount

                trade_log.append(
                    {
                        "resource": resource,
                        "amount": amount,
                        "cost": cost,
                        "buyer": buyer_kingdom["name"],
                        "seller": seller_kingdom["name"],
                    }
                )

    return trade_log
