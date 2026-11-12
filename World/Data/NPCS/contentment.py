# Contentment. Each day a person's happiness drifts DRIFT of the way toward
# the level their life sets: fed or hungry, housed or not (or crowded), with a
# trade or laboring, married, a parent, recently bereaved, living under an
# occupier, stressed, penniless. Happiness no longer climbs from where people
# spend their hours (that pinned everyone at 100); a festival or a grief
# still moves it at once, and it then drifts back to what life is like.

BASE = 50
DRIFT = 0.10
FED_WEIGHT = 30                 # from -15 (unfed) to +15 (fully fed)
HOUSED, HOMELESS, CROWDED = 5, -15, -10
WORKING, LABORING = 10, 3
MARRIED, PARENT = 10, 5
BEREAVED, BEREAVED_DAYS = -15, 90
OCCUPIED = -10
STRESS_WEIGHT = 0.2
POOR, POOR_BELOW = -5, 10
ADULT_AGE, OLD_AGE = 16, 60


def life_levels(conn, world_id, day):
    """{npc_id: the happiness their life sets} for every living person."""
    people = conn.execute(
        """SELECT n.id, n.age, n.fed, n.gold, n.home_id, n.profession, n.needs_stress, n.city_id,
                  (SELECT alive FROM npcs s WHERE s.id = n.spouse_id) spouse_alive
           FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND n.alive = 1""", (world_id,)).fetchall()
    crowded = {r[0] for r in conn.execute(
        """SELECT b.id FROM buildings b WHERE b.type = 'House' AND
           (SELECT COUNT(*) FROM npcs x WHERE x.home_id = b.id AND x.alive = 1 AND x.age >= ?) > b.capacity""", (ADULT_AGE,))}
    parents = {r[0] for r in conn.execute(
        "SELECT parent_a_id FROM npcs WHERE alive = 1 AND parent_a_id IS NOT NULL UNION SELECT parent_b_id FROM npcs WHERE alive = 1 AND parent_b_id IS NOT NULL")}
    bereaved = set()
    for dead in conn.execute("SELECT id, spouse_id, parent_a_id, parent_b_id FROM npcs WHERE alive = 0 AND died_day >= ?",
                             (day - BEREAVED_DAYS,)).fetchall():
        bereaved.update(x for x in (dead["spouse_id"], dead["parent_a_id"], dead["parent_b_id"]) if x)
        bereaved.update(r[0] for r in conn.execute("SELECT id FROM npcs WHERE parent_a_id = ? OR parent_b_id = ?", (dead["id"], dead["id"])))
    occupied = {r[0] for r in conn.execute("SELECT city_id FROM occupations WHERE status = 'Occupied'")} \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'occupations'").fetchone() else set()

    levels = {}
    for p in people:
        level = BASE + FED_WEIGHT * ((p["fed"] if p["fed"] is not None else 1.0) - 0.5)
        if p["home_id"] is None:
            level += HOMELESS
        else:
            level += CROWDED if p["home_id"] in crowded else HOUSED
        if ADULT_AGE <= p["age"] < OLD_AGE and p["profession"] != "Child":
            level += LABORING if p["profession"] == "Laborer" else WORKING
        if p["spouse_alive"]:
            level += MARRIED
        if p["id"] in parents:
            level += PARENT
        if p["id"] in bereaved:
            level += BEREAVED
        if p["city_id"] in occupied:
            level += OCCUPIED
        level -= STRESS_WEIGHT * (p["needs_stress"] or 0)
        if p["age"] >= ADULT_AGE and (p["gold"] or 0) < POOR_BELOW:
            level += POOR
        levels[p["id"]] = max(0.0, min(100.0, level))
    return levels


def drift_happiness(conn, world_id, day):
    """Daily: everyone's happiness moves DRIFT of the way toward what their life sets. Returns how many changed."""
    levels = life_levels(conn, world_id, day)
    now = dict(conn.execute(
        f"SELECT id, needs_happiness FROM npcs WHERE id IN ({','.join(str(i) for i in levels) or '0'})").fetchall())
    updates = [(round(now[i] + (level - now[i]) * DRIFT), i) for i, level in levels.items()
               if round(now[i] + (level - now[i]) * DRIFT) != now[i]]
    conn.executemany("UPDATE npcs SET needs_happiness = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)