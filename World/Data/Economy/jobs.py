import json
from collections import Counter
from pathlib import Path

# The job market. Each month, jobless adults ("Laborer") take free places in
# their own town's workplaces, learning the trade of the place they join (the
# trade its workers already follow, or else the trade that building is for).
#
# Only HIRING_CATEGORIES hire this way: workshops, farms, markets, temples,
# libraries. Barracks and guard posts recruit (Data.Government.military) --
# filling them with the jobless would swell every army at once -- castles are
# the ruler's household, and no one is "hired" into a thieves' den.

HIRING_CATEGORIES = ("Civillian", "Academic", "Religious")
WORKING_AGE = 16

DATA = Path(__file__).resolve().parents[1]


def trade_for_building():
    """Building type -> the profession(s) that work it."""
    by_building = {}
    for profession, building in json.load(open(DATA / "Buildings" / "ProfessionBuildings.json")).items():
        by_building.setdefault(building, []).append(profession)
    return by_building


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
            "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Laborer' AND age >= ? ORDER BY age, id",
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