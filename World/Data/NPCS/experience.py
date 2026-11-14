"""
Experience at the start of a world. People are not born the day the world
begins: an adult has worked their trade since WORK_FROM_AGE, so each of their
class's skills starts where those years of work would have brought it -- the
same experience a workday gives during play (Data.NPCS.employment), over
WORKDAYS_PER_YEAR days a year. A 50-year-old knight starts a veteran; a
17-year-old apprentice, near the beginning.
"""

from __future__ import annotations

from Data.NPCS.employment import SKILL_XP_PER_DAY, xp_to_next_level

WORK_FROM_AGE = 16
WORKDAYS_PER_YEAR = 250


def level_for(total_xp):
    """(level, xp left over) reached from level 1 with this much experience."""
    level, xp = 1, total_xp
    while xp >= xp_to_next_level(level):
        xp -= xp_to_next_level(level)
        level += 1
    return level, xp


def years_of_work(age):
    return max(0, (age or 0) - WORK_FROM_AGE)


def give_experience(conn, world_id):
    """Sets every living adult's class skills to what their years of work earned. Returns how many people."""
    rows = conn.execute(
        """SELECT n.id, n.age FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND n.alive = 1 AND n.age >= ?""", (world_id, WORK_FROM_AGE)).fetchall()
    updates = []
    for npc_id, age in rows:
        level, xp = level_for(years_of_work(age) * WORKDAYS_PER_YEAR * SKILL_XP_PER_DAY)
        updates.append((level, xp, npc_id))
    conn.executemany("UPDATE npc_skills SET level = MAX(level, ?), xp = ? WHERE npc_id = ?", updates)
    conn.commit()
    return len(updates)