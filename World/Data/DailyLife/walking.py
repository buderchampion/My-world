"""
Walking between buildings. A person no longer appears at their next place in
an instant (SimulationManager.on_hour): they walk there, across their town's
districts (Data.Cities.districts: one for each kind of building -- homes,
trades, temples, markets).

    Across town   going to a building in another district takes ACROSS
                  minutes by the size of the town (a capital's streets are
                  long, a village's lanes short); within a district,
                  WITHIN minutes; staying where one is, none.
    Its cost      every minute on foot costs ENERGY_PER_MINUTE energy, and
                  the time spent walking is time not spent there: what a
                  block at the place gives (a meal's hunger, a tavern's
                  company, rest) is cut by the share of the block walked
                  away (BLOCK_MINUTES). What it costs (work's toll on energy)
                  is not cut: work starts when the worker arrives, and ends
                  on time.
"""

ACROSS = {"Capital": 40, "City": 30, "Town": 20, "Village": 5}
WITHIN = {"Capital": 8, "City": 6, "Town": 4, "Village": 2}
ENERGY_PER_MINUTE = 0.05        # forty minutes across a capital: -2 energy
BLOCK_MINUTES = 360             # a time block is about six hours


def minutes(from_building, to_building, district_of, city_type):
    """How long the walk from one building to another takes, in minutes."""
    if from_building is None or to_building is None or from_building == to_building:
        return 0
    a, b = district_of.get(from_building), district_of.get(to_building)
    if a is not None and a == b:
        return WITHIN.get(city_type, 4)
    return ACROSS.get(city_type, 20)


def walk(npc, from_building, to_building, district_of, city_type, effect):
    """Walks the person over and returns the block's effect on their needs, less what the walk took.
    Records the minutes in npc["walked"] (today's total, for the curious)."""
    m = minutes(from_building, to_building, district_of, city_type)
    if not m:
        return effect
    share = max(0.0, 1 - m / BLOCK_MINUTES)
    out = {need: (delta * share if delta > 0 else delta) for need, delta in effect.items()}
    out["energy"] = out.get("energy", 0) - m * ENERGY_PER_MINUTE
    npc["walked"] = npc.get("walked", 0) + m
    return out