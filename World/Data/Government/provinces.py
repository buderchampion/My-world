import random

# Provinces. A kingdom is not ruled from its capital alone: every town other
# than the capital is the seat of a County, held by a hereditary lord -- a
# Count or Countess -- with the villages nearest it as its lands. The villages
# nearest the capital are the crown's own lands (province_id is NULL). When a
# lord dies their heir inherits: the eldest living child old enough to rule,
# else the surviving spouse; failing both, the crown appoints a new lord from
# the town's notables, and a new house begins.

# An heir must be at least this old to inherit.
HEIR_MIN_AGE = 16

LORD_TITLES = ("Count", "Countess")


def lord_title(npc):
    return "Count" if npc["gender"] == "Male" else "Countess"


def seat_province(conn, city_id):
    """The province this city is the seat of, or None."""
    row = conn.execute("SELECT * FROM provinces WHERE seat_city_id = ?", (city_id,)).fetchone()
    return dict(row) if row else None


def ensure_provinces(conn, world_id):
    """
    Divides each kingdom of a world into provinces, if it has none yet: one
    County per town other than the capital, and every village assigned to
    whichever is nearest -- the capital (the crown's own lands) or a county
    seat. A town that already has a ruler (an older world's Mayor) makes them
    its first Count or Countess. Safe to call every start: does nothing for a
    kingdom that already has provinces. Returns how many provinces it made.
    """

    from Data.Database.repository import get_npc, save_npc

    made = 0

    for kingdom in conn.execute("SELECT id, capital_city_id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():

        if conn.execute("SELECT 1 FROM provinces WHERE kingdom_id = ? LIMIT 1", (kingdom["id"],)).fetchone():
            continue

        cities = [dict(c) for c in conn.execute(
            "SELECT id, name, type, map_x, map_y, ruler_id FROM cities WHERE kingdom_id = ?", (kingdom["id"],)
        )]
        seats = [c for c in cities if c["type"] != "Village" and c["id"] != kingdom["capital_city_id"]]
        capital = next((c for c in cities if c["id"] == kingdom["capital_city_id"]), None)

        province_of_seat = {}
        for seat in seats:
            lord = get_npc(conn, seat["ruler_id"]) if seat["ruler_id"] else None
            house = None
            if lord and lord["alive"]:
                lord["title"] = lord_title(lord)
                save_npc(conn, lord)
                house = f"House of {lord['name']}"
            cursor = conn.execute(
                "INSERT INTO provinces (kingdom_id, name, seat_city_id, lord_id, house) VALUES (?, ?, ?, ?, ?)",
                (kingdom["id"], f"County of {seat['name']}", seat["id"], lord["id"] if lord and lord["alive"] else None, house)
            )
            province_of_seat[seat["id"]] = cursor.lastrowid
            conn.execute("UPDATE cities SET province_id = ? WHERE id = ?", (cursor.lastrowid, seat["id"]))
            made += 1

        centers = seats + ([capital] if capital else [])
        for village in (c for c in cities if c["type"] == "Village"):
            if not centers:
                break
            nearest = min(centers, key=lambda c: (c["map_x"] - village["map_x"]) ** 2 + (c["map_y"] - village["map_y"]) ** 2)
            conn.execute("UPDATE cities SET province_id = ? WHERE id = ?", (province_of_seat.get(nearest["id"]), village["id"]))

    conn.commit()
    return made


def choose_heir(conn, lord_id):
    """
    Who inherits from a lord: their eldest living child old enough to rule
    who holds no title already, else their living spouse (if untitled), else
    None. Returns the heir as an NPC dict.
    """

    from Data.Database.repository import get_npc

    child = conn.execute(
        """
        SELECT id FROM npcs WHERE alive = 1 AND age >= ? AND title IS NULL
        AND (parent_a_id = ? OR parent_b_id = ?)
        ORDER BY age DESC, id LIMIT 1
        """, (HEIR_MIN_AGE, lord_id, lord_id)
    ).fetchone()
    if child:
        return get_npc(conn, child["id"])

    lord = get_npc(conn, lord_id)
    if lord and lord["spouse"]:
        spouse = get_npc(conn, lord["spouse"])
        if spouse and spouse["alive"] and spouse["title"] is None:
            return spouse

    return None


def install_lord(conn, province, seat_city, lord, inherited_from=None):
    """
    Makes `lord` the Count or Countess of a province: titled, living at the
    seat (moved there if they lived elsewhere), recorded as the province's
    lord and the seat's ruler, and logged. A lord who did not inherit founds
    a new house. Returns the lord.
    """

    from Data.Database.repository import save_npc, create_event, get_npc
    from Data.Infrastructure.migration import relocate_npc

    if lord["city_id"] != seat_city["id"]:
        relocate_npc(conn, lord["id"], seat_city["id"])
        lord = get_npc(conn, lord["id"])

    lord["title"] = lord_title(lord)
    save_npc(conn, lord)

    house = province["house"] if inherited_from and province["house"] else f"House of {lord['name']}"
    conn.execute("UPDATE provinces SET lord_id = ?, house = ? WHERE id = ?", (lord["id"], house, province["id"]))
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (lord["id"], seat_city["id"]))

    if inherited_from:
        relation = "child" if lord["id"] in [r[0] for r in conn.execute(
            "SELECT id FROM npcs WHERE parent_a_id = ? OR parent_b_id = ?", (inherited_from["id"], inherited_from["id"]))] else "spouse"
        create_event(conn, "Inheritance",
                     f"{lord['name']} inherited the {province['name']} on the death of {inherited_from['name']}, "
                     f"their {'parent' if relation == 'child' else 'spouse'}, and now rules as {lord['title']} ({house}).",
                     city_id=seat_city["id"], kingdom_id=province["kingdom_id"])
    else:
        create_event(conn, "Investiture",
                     f"The crown granted the {province['name']} to {lord['name']}, who rules as {lord['title']} and founds the {house}.",
                     city_id=seat_city["id"], kingdom_id=province["kingdom_id"])

    conn.commit()
    return lord