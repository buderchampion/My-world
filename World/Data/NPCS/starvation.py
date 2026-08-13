import random

# Below this hunger level, there's a real risk of dying from
# starvation. Hunger now genuinely recovers (see Data.DailyLife --
# Home and dedicated eating trips both restore it), so hitting this
# threshold at all means something is actually wrong: no food source
# reachable, or a genuine local famine.
CRITICAL_HUNGER_THRESHOLD = 20

# Chance per day of dying once hunger is at rock bottom (0). Scales
# down linearly to 0% right at the threshold, same graduated-risk
# shape as old-age death in aging.py, not an instant cutoff.
MAX_STARVATION_DEATH_CHANCE = 0.05


def starvation_death_chance(hunger: int) -> float:

    if hunger >= CRITICAL_HUNGER_THRESHOLD:
        return 0.0

    return MAX_STARVATION_DEATH_CHANCE * (CRITICAL_HUNGER_THRESHOLD - hunger) / CRITICAL_HUNGER_THRESHOLD


def apply_starvation_check(npc: dict) -> bool:
    """
    Rolls a single day's starvation risk for one NPC, based on their
    current hunger. Mutates npc in place and returns True if they died
    this check. Does not save or commit -- caller's responsibility,
    matching every other *.py system in this project (aging, healing,
    employment, etc. all follow the same pattern).
    """

    if not npc["alive"]:
        return False

    if random.random() < starvation_death_chance(npc["needs"]["hunger"]):
        npc["alive"] = False
        npc["health"] = 0
        npc["workplace"] = None
        npc["home"] = None
        return True

    return False