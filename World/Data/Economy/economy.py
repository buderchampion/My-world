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
    """

    building_production = load_json(BUILDING_PRODUCTION)

    if "resources" not in city:
        city["resources"] = {}

    for building in city["buildings"]:

        rule = building_production.get(building["type"])

        if not rule:
            continue

        amount = len(building["workers"]) * rule["amount_per_worker"]

        resource = rule["resource"]

        city["resources"][resource] = round(
            city["resources"].get(resource, 0) + amount, 2
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

            trade_log.append({
                "resource": resource,
                "direction": "import",
                "city": city["name"],
                "amount": share
            })

        for city, available in surplus_cities:

            share = round(tradeable * (available / total_surplus), 2)

            city["resources"][resource] = round(
                city["resources"].get(resource, 0) - share, 2
            )

            trade_log.append({
                "resource": resource,
                "direction": "export",
                "city": city["name"],
                "amount": share
            })

    return trade_log
