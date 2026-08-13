import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

NEEDS_EFFECTS = DATA_DIR / "DailyLife" / "NeedsEffects.json"

TIME_BLOCKS = ["Morning", "Afternoon", "Evening", "Night"]

# Below this social level, an NPC prefers going out over their default
# evening routine.
LOW_SOCIAL_THRESHOLD = 40
LOW_HUNGER_THRESHOLD = 40

# Building types that count as a "Social" destination when an NPC goes
# out looking for company.
SOCIAL_BUILDING_TYPES = ("Tavern", "Market")
FOOD_BUILDING_TYPES = ("Tavern", "Market", "Bakery")

NO_SCHEDULE_PROFESSIONS = ("Unemployed", "Child")


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def build_schedule(npc):
    """
    Assigns an NPC's default daily routine (a location TYPE per time
    block, not a specific building yet). Called once, at generation
    time -- this is the routine's template, not a per-tick advance.
    """

    if npc["profession"] in NO_SCHEDULE_PROFESSIONS:

        schedule = {block: "Home" for block in TIME_BLOCKS}

    else:

        schedule = {
            "Morning": "Workplace",
            "Afternoon": "Workplace",
            "Evening": "Home",
            "Night": "Home"
        }

    npc["schedule"] = schedule

    return npc


def get_location_id(npc, location_type, buildings=None):

    if location_type == "Workplace":
        return npc["workplace"] or npc["home"]

    if location_type in ("Social", "Food") and buildings:

        eligible_types = SOCIAL_BUILDING_TYPES if location_type == "Social" else FOOD_BUILDING_TYPES

        matching_buildings = [
            b for b in buildings
            if b["type"] in eligible_types
        ]

        if matching_buildings:
            return random.choice(matching_buildings)["id"]

    return npc["home"]


def resolve_time_block(npc, time_block, buildings=None):
    """
    Pure decision function -- given an NPC's current needs and their
    default schedule, decides where they'd actually be for this time
    block right now. Does NOT mutate the npc or advance time; it just
    answers "where would they be if this block were happening now".
    Returns (location_type, location_id).

    Hunger is checked before social (and in every time block, not just
    Evening) since going hungry is a survival need, not a preference --
    if both are low at once, eating wins.
    """

    location_type = npc.get("schedule", {}).get(time_block, "Home")

    if npc["needs"]["hunger"] < LOW_HUNGER_THRESHOLD:
        location_type = "Food"
    elif time_block == "Evening" and npc["needs"]["social"] < LOW_SOCIAL_THRESHOLD:
        location_type = "Social"

    location_id = get_location_id(npc, location_type, buildings)

    return location_type, location_id


def get_needs_effect(building_type):
    """
    Pure lookup -- returns the hunger/energy/social change a single
    time block spent at this BUILDING TYPE would cause (House, Tavern,
    Market, Bakery all have their own tuned effect; anything else
    falls back to a generic "at work" effect). Does not apply it to
    anyone; SimulationManager.on_hour() resolves the actual building
    an NPC ends up at and calls this with its real type.
    """

    effects = load_json(NEEDS_EFFECTS)

    return effects.get(building_type, effects["_default_workplace"])
