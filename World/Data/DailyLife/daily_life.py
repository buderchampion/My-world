import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

NEEDS_EFFECTS = DATA_DIR / "DailyLife" / "NeedsEffects.json"

TIME_BLOCKS = ["Morning", "Afternoon", "Evening", "Night"]

# Below this social level, an NPC prefers going out over their default
# evening routine.
LOW_SOCIAL_THRESHOLD = 40

# Building types that count as a "Social" destination when an NPC goes
# out looking for company.
SOCIAL_BUILDING_TYPES = ("Tavern", "Market")

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

    if location_type == "Social" and buildings:

        social_buildings = [
            b for b in buildings
            if b["type"] in SOCIAL_BUILDING_TYPES
        ]

        if social_buildings:
            return random.choice(social_buildings)["id"]

    return npc["home"]


def resolve_time_block(npc, time_block, buildings=None):
    """
    Pure decision function -- given an NPC's current needs and their
    default schedule, decides where they'd actually be for this time
    block right now. Does NOT mutate the npc or advance time; it just
    answers "where would they be if this block were happening now".
    Returns (location_type, location_id).
    """

    location_type = npc.get("schedule", {}).get(time_block, "Home")

    if time_block == "Evening" and npc["needs"]["social"] < LOW_SOCIAL_THRESHOLD:
        location_type = "Social"

    location_id = get_location_id(npc, location_type, buildings)

    return location_type, location_id


def get_needs_effect(location_type):
    """
    Pure lookup -- returns the hunger/energy/social change a single
    time block spent at this location type would cause. Does not
    apply it to anyone; that's for SimulationManager.on_hour() to do
    once needs decay gets wired in (see Data.Simulation.simulation_manager).
    """

    effects = load_json(NEEDS_EFFECTS)

    return effects.get(location_type, effects["Home"])
