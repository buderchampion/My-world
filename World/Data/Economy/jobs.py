import json
from collections import Counter
from pathlib import Path

# The job market. Each month, jobless adults ("Laborer") take free places in
# their own town's workplaces, learning the trade of the place they join (the
# trade its workers already follow, or else the trade that building is for).
#
# Only HIRING_CATEGORIES hire this way: workshops, farms, markets, temples,
# libraries. Barracks and guard posts recruit instead (recruit, below) --
# filling them with the jobless at once would swell every army in a month --
# castles are the ruler's household, and no one is "hired" into a thieves' den.
#
# Coming of age: a child who reaches WORKING_AGE (in their people's stages of
# life -- an elf takes far longer) leaves childhood and joins the jobless,
# to be hired, recruited or sent to day labour like anyone else. Measured with
# the long test run (long_run.py): with no such step, a world's children grew
# old as "Child" -- after thirty years four thousand grown folk had never
# worked, and every army had dwindled to nothing.
#
# Recruitment: each month every barracks and guard post with room takes up to
# RECRUITS_PER_POST of its town's young jobless (WORKING_AGE to
# RECRUIT_MAX_AGE) as Soldiers and Guards -- but only while the crown's
# treasury holds RECRUIT_RESERVE_DAYS of pay for its whole army, new men and all.

HIRING_CATEGORIES = ("Civillian", "Academic", "Religious")
WORKING_AGE = 16
RECRUITS_PER_POST = 2
RECRUIT_MAX_AGE = 40
RECRUIT_RESERVE_DAYS = 180
MILITARY_POSTS = {"Barracks": "Soldier", "Guard Post": "Guard"}

DATA = Path(__file__).resolve().parents[1]


def trade_for_building():
    """Building type -> the profession(s) that work it."""
    by_building = {}
    for profession, building in json.load(open(DATA / "Buildings" / "ProfessionBuildings.json")).items():
        by_building.setdefault(building, []).append(profession)
    return by_building


def _new_class(race, profession):
    from Data.NPCS.npc_generator import generate_class
    from Data.NPCS.races import suited_class
    return suited_class(race, generate_class(profession), profession)


def come_of_age(conn, world_id):
    """Monthly: children grown to WORKING_AGE leave childhood and join the jobless. Returns how many."""
    grown = conn.execute(
        """SELECT n.id, n.race FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND n.alive = 1 AND n.profession = 'Child' AND n.age >= ?""", (world_id, WORKING_AGE)).fetchall()
    conn.executemany("UPDATE npcs SET profession = 'Laborer', class = ? WHERE id = ?",
                     [(_new_class(race, "Laborer"), npc_id) for npc_id, race in grown])
    conn.commit()
    return len(grown)


def recruit(conn, world_id):
    """Monthly: barracks and guard posts with room take young jobless as Soldiers and Guards, while the crown can pay. Returns how many."""
    from Data.Government.military import MILITARY_PROFESSIONS, UPKEEP_GOLD_PER_SOLDIER_PER_DAY
    marks = ",".join("?" * len(MILITARY_PROFESSIONS))
    taken = 0
    for kingdom in conn.execute("SELECT id, treasury FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        army = conn.execute(
            f"""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
                WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (kingdom["id"], *MILITARY_PROFESSIONS)).fetchone()[0]
        afford = int((kingdom["treasury"] or 0) / (UPKEEP_GOLD_PER_SOLDIER_PER_DAY * RECRUIT_RESERVE_DAYS)) - army
        if afford <= 0:
            continue
        for post in conn.execute(
                f"""SELECT b.id, b.type, b.capacity, b.city_id,
                           (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) workers
                    FROM buildings b JOIN cities c ON c.id = b.city_id
                    WHERE c.kingdom_id = ? AND b.type IN ({",".join("?" * len(MILITARY_POSTS))}) ORDER BY b.id""",
                (kingdom["id"], *MILITARY_POSTS)).fetchall():
            room = min(RECRUITS_PER_POST, post["capacity"] - post["workers"], afford)
            if room <= 0:
                continue
            trade = MILITARY_POSTS[post["type"]]
            for npc_id, race in conn.execute(
                    """SELECT id, race FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Laborer'
                       AND age BETWEEN ? AND ? AND jailed_until IS NULL ORDER BY age, id LIMIT ?""",
                    (post["city_id"], WORKING_AGE, RECRUIT_MAX_AGE, room)).fetchall():
                conn.execute("UPDATE npcs SET profession = ?, class = ?, workplace_id = ? WHERE id = ?",
                             (trade, _new_class(race, trade), post["id"], npc_id))
                taken += 1
                afford -= 1
            if afford <= 0:
                break
    conn.commit()
    return taken


def hire_jobless(conn, world_id):
    """Monthly: the jobless take free places in their town. Returns how many were hired."""

    types = json.load(open(DATA / "Buildings" / "BuildingTypes.json"))
    trades = trade_for_building()
    hiring_types = [t for t, v in types.items() if v["category"] in HIRING_CATEGORIES and t in trades]
    if not hiring_types:
        return 0
    marks = ",".join("?" * len(hiring_types))
    hired = 0

    for city in conn.execute(
        """SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)).fetchall():
        jobless = [row[0] for row in conn.execute(
            "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Laborer' AND age >= ? AND jailed_until IS NULL ORDER BY age, id",
            (city["id"], WORKING_AGE))]
        if not jobless:
            continue
        for building in conn.execute(
            f"""SELECT b.id, b.type, b.capacity,
                       (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) workers
                FROM buildings b WHERE b.city_id = ? AND b.type IN ({marks}) ORDER BY b.id""", (city["id"], *hiring_types)).fetchall():
            free = building["capacity"] - building["workers"]
            if free <= 0 or not jobless:
                continue
            current = Counter(row[0] for row in conn.execute(
                "SELECT profession FROM npcs WHERE workplace_id = ? AND alive = 1", (building["id"],)))
            trade = current.most_common(1)[0][0] if current else trades[building["type"]][0]
            for npc_id in jobless[:free]:
                conn.execute("UPDATE npcs SET profession = ?, workplace_id = ? WHERE id = ?", (trade, building["id"], npc_id))
                hired += 1
            jobless = jobless[free:]

    conn.commit()
    return hired