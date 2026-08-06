import random

# Below this age, natural death risk is zero. NPCs are generated
# between 18-70 (see npc_generator.py), so this only starts to matter
# for NPCs who've actually lived a full simulated life.
OLD_AGE_THRESHOLD = 60

# Death chance grows by this much per year past the threshold, capped
# so it never quite reaches certainty (a rare few live to a ripe old
# age). At these rates: 2% at 61, ~22% at 70, ~62% at 90.
DEATH_CHANCE_PER_YEAR_OVER = 0.02
MAX_DEATH_CHANCE = 0.75


def natural_death_chance(age: int) -> float:

    if age <= OLD_AGE_THRESHOLD:
        return 0.0

    return min(MAX_DEATH_CHANCE, (age - OLD_AGE_THRESHOLD) * DEATH_CHANCE_PER_YEAR_OVER)


def apply_aging(npc: dict) -> bool:
    """
    Ages a single NPC by one year and rolls for natural death if
    they're old enough for it to be a risk. Mutates npc in place and
    returns True if they died this year. Does not save or commit --
    that's the caller's responsibility, matching every other *.py
    system in this project (family, employment, combat, etc. all
    follow the same "mutate the dict, caller persists it" pattern).
    """

    if not npc["alive"]:
        return False

    npc["age"] += 1

    if random.random() < natural_death_chance(npc["age"]):
        npc["alive"] = False
        npc["health"] = 0
        npc["workplace"] = None
        npc["home"] = None
        return True

    return False
