import json
from Data.Database.database import get_connection

# ---------------------------------------------------------------------------
# NPCs
# ---------------------------------------------------------------------------

def create_npc(conn, city_id, name, gender, age, npc_class, profession):

    cursor = conn.execute(
        """
        INSERT INTO npcs (city_id, name, gender, age, class, profession)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (city_id, name, gender, age, npc_class, profession)
    )

    return get_npc(conn, cursor.lastrowid)


def get_npc(conn, npc_id):

    row = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()

    if row is None:
        return None

    npc = dict(row)

    npc["workplace"] = npc.pop("workplace_id")
    npc["home"] = npc.pop("home_id")
    npc["spouse"] = npc.pop("spouse_id")
    npc["location"] = npc.pop("location_id")
    npc["alive"] = bool(npc["alive"])

    npc["parents"] = [p for p in (npc.pop("parent_a_id"), npc.pop("parent_b_id")) if p is not None]

    npc["needs"] = {
        "hunger": npc.pop("needs_hunger"),
        "energy": npc.pop("needs_energy"),
        "social": npc.pop("needs_social")
    }

    schedule_json = npc.pop("schedule_json")
    npc["schedule"] = json.loads(schedule_json) if schedule_json else {}

    city_row = conn.execute("SELECT type FROM cities WHERE id = ?", (npc["city_id"],)).fetchone()
    npc["city_type"] = city_row["type"] if city_row else None

    npc["skills"] = {}

    for skill_row in conn.execute("SELECT * FROM npc_skills WHERE npc_id = ?", (npc_id,)):
        npc["skills"][skill_row["skill_name"]] = {"level": skill_row["level"], "xp": skill_row["xp"]}

    npc["relationships"] = {}

    for rel_row in conn.execute(
        "SELECT * FROM relationships WHERE npc_a_id = ? OR npc_b_id = ?", (npc_id, npc_id)
    ):
        other_id = rel_row["npc_b_id"] if rel_row["npc_a_id"] == npc_id else rel_row["npc_a_id"]
        npc["relationships"][str(other_id)] = {"type": rel_row["type"], "reputation": rel_row["reputation"]}

    return npc


def save_npc(conn, npc):

    conn.execute(
        """
        UPDATE npcs SET
            name = ?, gender = ?, age = ?, class = ?, profession = ?,
            workplace_id = ?, home_id = ?, gold = ?, spouse_id = ?, title = ?,
            location_id = ?, schedule_json = ?,
            needs_hunger = ?, needs_energy = ?, needs_social = ?,
            health = ?, alive = ?
        WHERE id = ?
        """,
        (
            npc["name"], npc["gender"], npc["age"], npc["class"], npc["profession"],
            npc["workplace"], npc["home"], npc["gold"], npc["spouse"], npc["title"],
            npc["location"], json.dumps(npc.get("schedule", {})),
            npc["needs"]["hunger"], npc["needs"]["energy"], npc["needs"]["social"],
            npc["health"], int(npc["alive"]),
            npc["id"]
        )
    )

    conn.execute("DELETE FROM npc_skills WHERE npc_id = ?", (npc["id"],))

    for skill_name, skill in npc.get("skills", {}).items():
        conn.execute(
            "INSERT INTO npc_skills (npc_id, skill_name, level, xp) VALUES (?, ?, ?, ?)",
            (npc["id"], skill_name, skill["level"], skill["xp"])
        )


def get_city_npcs(conn, city_id):

    rows = conn.execute("SELECT id FROM npcs WHERE city_id = ?", (city_id,)).fetchall()

    return [get_npc(conn, row["id"]) for row in rows]


def set_npc_parents(conn, child_id, parent_a_id, parent_b_id):

    conn.execute(
        "UPDATE npcs SET parent_a_id = ?, parent_b_id = ? WHERE id = ?",
        (parent_a_id, parent_b_id, child_id)
    )


def get_children(conn, npc_id):

    rows = conn.execute(
        "SELECT id FROM npcs WHERE parent_a_id = ? OR parent_b_id = ?", (npc_id, npc_id)
    ).fetchall()

    return [row["id"] for row in rows]


# ---------------------------------------------------------------------------
# Buildings
# ---------------------------------------------------------------------------

def create_building(conn, city_id, name, building_type, category, capacity):

    cursor = conn.execute(
        """
        INSERT INTO buildings (city_id, name, type, category, capacity)
        VALUES (?, ?, ?, ?, ?)
        """,
        (city_id, name, building_type, category, capacity)
    )

    return get_building(conn, cursor.lastrowid)


def get_building(conn, building_id):

    row = conn.execute("SELECT * FROM buildings WHERE id = ?", (building_id,)).fetchone()

    if row is None:
        return None

    building = dict(row)
    building["owner"] = building.pop("owner_id")

    workers = conn.execute(
        "SELECT id FROM npcs WHERE workplace_id = ? AND alive = 1", (building_id,)
    ).fetchall()

    residents = conn.execute(
        "SELECT id FROM npcs WHERE home_id = ? AND alive = 1", (building_id,)
    ).fetchall()

    building["workers"] = [row["id"] for row in workers]
    building["residents"] = [row["id"] for row in residents]

    return building


def get_city_buildings(conn, city_id):

    rows = conn.execute("SELECT id FROM buildings WHERE city_id = ?", (city_id,)).fetchall()

    return [get_building(conn, row["id"]) for row in rows]


def count_occupants(conn, building_id, occupancy_column):

    row = conn.execute(
        f"SELECT COUNT(*) as n FROM npcs WHERE {occupancy_column} = ? AND alive = 1",
        (building_id,)
    ).fetchone()

    return row["n"]


def find_open_building(conn, city_id, building_type, occupancy_column="workplace_id"):
    """
    Returns the first building of this type in this city with room left
    (comparing live occupant count against capacity), or None.
    """

    rows = conn.execute(
        "SELECT id, capacity FROM buildings WHERE city_id = ? AND type = ?",
        (city_id, building_type)
    ).fetchall()

    for row in rows:

        if count_occupants(conn, row["id"], occupancy_column) < row["capacity"]:
            return get_building(conn, row["id"])

    return None


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------

def create_city(conn, kingdom_id, name, city_type, population, map_x=0, map_y=0):

    cursor = conn.execute(
        "INSERT INTO cities (kingdom_id, name, type, population, map_x, map_y) VALUES (?, ?, ?, ?, ?, ?)",
        (kingdom_id, name, city_type, population, map_x, map_y)
    )

    return get_city(conn, cursor.lastrowid)


def get_city(conn, city_id):

    row = conn.execute("SELECT * FROM cities WHERE id = ?", (city_id,)).fetchone()

    if row is None:
        return None

    city = dict(row)
    city["kingdom"] = city.pop("kingdom_id")
    city["ruler"] = city.pop("ruler_id")

    city["resources"] = {
        r["resource_name"]: r["amount"]
        for r in conn.execute("SELECT * FROM city_resources WHERE city_id = ?", (city_id,))
    }

    city["buildings"] = get_city_buildings(conn, city_id)

    city["npcs"] = [
        {
            "id": npc["id"], "name": npc["name"], "profession": npc["profession"],
            "class": npc["class"], "workplace": npc["workplace"], "home": npc["home"]
        }
        for npc in get_city_npcs(conn, city_id)
    ]

    city["quests"] = get_city_quests(conn, city_id)
    city["events"] = get_city_events(conn, city_id)

    return city


def save_city(conn, city):

    conn.execute(
        "UPDATE cities SET name = ?, type = ?, population = ?, ruler_id = ?, map_x = ?, map_y = ? WHERE id = ?",
        (city["name"], city["type"], city["population"], city["ruler"], city["map_x"], city["map_y"], city["id"])
    )

    for resource, amount in city.get("resources", {}).items():
        conn.execute(
            """
            INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, ?, ?)
            ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = excluded.amount
            """,
            (city["id"], resource, amount)
        )

    conn.commit()


def create_road(conn, city_a_id, city_b_id, kind, distance, travel_days):

    # Store the pair in a consistent order so (A, B) and (B, A) can
    # never both get inserted as if they were different roads.
    city_a_id, city_b_id = sorted((city_a_id, city_b_id))

    cursor = conn.execute(
        "INSERT INTO roads (city_a_id, city_b_id, kind, distance, travel_days) VALUES (?, ?, ?, ?, ?)",
        (city_a_id, city_b_id, kind, distance, travel_days)
    )

    return dict(conn.execute("SELECT * FROM roads WHERE id = ?", (cursor.lastrowid,)).fetchone())


def get_city_roads(conn, city_id):

    rows = conn.execute(
        "SELECT * FROM roads WHERE city_a_id = ? OR city_b_id = ?", (city_id, city_id)
    ).fetchall()

    return [dict(row) for row in rows]


def get_world_roads(conn, world_id):

    rows = conn.execute(
        """
        SELECT r.* FROM roads r
        JOIN cities c ON c.id = r.city_a_id
        JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ?
        """, (world_id,)
    ).fetchall()

    return [dict(row) for row in rows]


def get_kingdom_cities(conn, kingdom_id):

    rows = conn.execute("SELECT id FROM cities WHERE kingdom_id = ?", (kingdom_id,)).fetchall()

    return [get_city(conn, row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# Kingdoms / Worlds
# ---------------------------------------------------------------------------

def create_kingdom(conn, world_id, name, color="#808080"):

    cursor = conn.execute(
        "INSERT INTO kingdoms (world_id, name, color) VALUES (?, ?, ?)", (world_id, name, color)
    )

    return get_kingdom(conn, cursor.lastrowid)


def get_kingdom(conn, kingdom_id):

    row = conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()

    if row is None:
        return None

    kingdom = dict(row)
    kingdom["capital"] = kingdom.pop("capital_city_id")
    kingdom["ruler"] = kingdom.pop("ruler_id")

    kingdom["cities"] = [
        {"id": c["id"], "name": c["name"], "type": c["type"]}
        for c in get_kingdom_cities(conn, kingdom_id)
    ]

    kingdom["events"] = get_kingdom_events(conn, kingdom_id)

    return kingdom


def save_kingdom(conn, kingdom):

    conn.execute(
        "UPDATE kingdoms SET name = ?, capital_city_id = ?, ruler_id = ?, color = ? WHERE id = ?",
        (kingdom["name"], kingdom["capital"], kingdom["ruler"], kingdom["color"], kingdom["id"])
    )

    conn.commit()


def set_city_kingdom(conn, city_id, kingdom_id):

    conn.execute("UPDATE cities SET kingdom_id = ? WHERE id = ?", (kingdom_id, city_id))


def create_world(conn, name):

    cursor = conn.execute("INSERT INTO worlds (name) VALUES (?)", (name,))

    return {"id": cursor.lastrowid, "name": name}


def load_clock(conn, world_id):

    from Data.Simulation.clock import Clock

    row = conn.execute(
        "SELECT clock_minute, clock_hour, clock_day, clock_month, clock_year FROM worlds WHERE id = ?",
        (world_id,)
    ).fetchone()

    return Clock(
        minute=row["clock_minute"], hour=row["clock_hour"],
        day=row["clock_day"], month=row["clock_month"], year=row["clock_year"]
    )


def save_clock(conn, world_id, clock):

    conn.execute(
        """
        UPDATE worlds SET clock_minute = ?, clock_hour = ?, clock_day = ?, clock_month = ?, clock_year = ?
        WHERE id = ?
        """,
        (clock.minute, clock.hour, clock.day, clock.month, clock.year, world_id)
    )


def get_world_kingdoms(conn, world_id):

    rows = conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall()

    return [get_kingdom(conn, row["id"]) for row in rows]


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------

def set_relationship(conn, npc_a_id, npc_b_id, rel_type, reputation):

    a_id, b_id = sorted((npc_a_id, npc_b_id))

    conn.execute(
        """
        INSERT INTO relationships (npc_a_id, npc_b_id, type, reputation) VALUES (?, ?, ?, ?)
        ON CONFLICT(npc_a_id, npc_b_id) DO UPDATE SET type = excluded.type, reputation = excluded.reputation
        """,
        (a_id, b_id, rel_type, reputation)
    )


def get_city_rival_pairs(conn, city_id):
    """
    Every Rival relationship where both NPCs live in this city, sorted
    worst-first by |reputation|.
    """

    rows = conn.execute(
        """
        SELECT r.npc_a_id, r.npc_b_id, r.reputation
        FROM relationships r
        JOIN npcs a ON a.id = r.npc_a_id
        JOIN npcs b ON b.id = r.npc_b_id
        WHERE r.type = 'Rival' AND a.city_id = ? AND b.city_id = ?
        ORDER BY ABS(r.reputation) DESC
        """,
        (city_id, city_id)
    ).fetchall()

    return [(row["npc_a_id"], row["npc_b_id"], abs(row["reputation"])) for row in rows]


# ---------------------------------------------------------------------------
# Quests / Events
# ---------------------------------------------------------------------------

def create_quest(conn, **fields):

    columns = ", ".join(fields.keys())
    placeholders = ", ".join("?" for _ in fields)

    cursor = conn.execute(
        f"INSERT INTO quests ({columns}) VALUES ({placeholders})",
        tuple(fields.values())
    )

    row = conn.execute("SELECT * FROM quests WHERE id = ?", (cursor.lastrowid,)).fetchone()

    return dict(row)


def get_city_quests(conn, city_id):

    rows = conn.execute("SELECT * FROM quests WHERE city_id = ?", (city_id,)).fetchall()

    return [dict(row) for row in rows]


def _resolve_current_day(conn, city_id, kingdom_id):
    """
    Converts the world's current clock position into a single elapsed-
    day count, so events can be ordered/compared with a plain integer.
    Looks the world up via whichever of city_id/kingdom_id is given.
    """

    if kingdom_id is None and city_id is not None:
        row = conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (city_id,)).fetchone()
        kingdom_id = row["kingdom_id"] if row else None

    if kingdom_id is None:
        return None

    row = conn.execute(
        """
        SELECT w.clock_year, w.clock_month, w.clock_day FROM worlds w
        JOIN kingdoms k ON k.world_id = w.id WHERE k.id = ?
        """, (kingdom_id,)
    ).fetchone()

    if row is None:
        return None

    from Data.Simulation.clock import MONTHS_PER_YEAR, DAYS_PER_MONTH

    return (
        (row["clock_year"] - 1) * MONTHS_PER_YEAR * DAYS_PER_MONTH
        + (row["clock_month"] - 1) * DAYS_PER_MONTH
        + (row["clock_day"] - 1)
    )


def create_event(conn, event_type, description, participants=None, city_id=None, kingdom_id=None, day=None):

    if day is None:
        day = _resolve_current_day(conn, city_id, kingdom_id)

    cursor = conn.execute(
        "INSERT INTO events (kingdom_id, city_id, type, description, day) VALUES (?, ?, ?, ?, ?)",
        (kingdom_id, city_id, event_type, description, day)
    )

    event_id = cursor.lastrowid

    for npc_id in (participants or []):
        conn.execute(
            "INSERT INTO event_participants (event_id, npc_id) VALUES (?, ?)", (event_id, npc_id)
        )

    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    event = dict(row)
    event["participants"] = list(participants or [])

    return event


def get_city_events(conn, city_id):

    rows = conn.execute("SELECT * FROM events WHERE city_id = ?", (city_id,)).fetchall()

    return [dict(row) for row in rows]


def get_kingdom_events(conn, kingdom_id):

    rows = conn.execute("SELECT * FROM events WHERE kingdom_id = ?", (kingdom_id,)).fetchall()

    return [dict(row) for row in rows]
