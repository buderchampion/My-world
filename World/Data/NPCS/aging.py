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

    # A year of their life passes. How far it moves them on depends on their
    # people (Data.NPCS.races): an elf's stage of life moves only every 7.5
    # years, so old age is weighed only when it does.
    from Data.NPCS.races import grow_older
    start = npc["age"]
    steps = grow_older(npc)

    if any(random.random() < natural_death_chance(start + i + 1) for i in range(steps)):
        npc["alive"] = False
        npc["health"] = 0
        npc["workplace"] = None
        npc["home"] = None
        return True

    return False