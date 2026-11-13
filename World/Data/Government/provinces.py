import random

# Provinces. A kingdom is not ruled from its capital alone: every town other
# than the capital is the seat of a County, held by a hereditary lord -- a
# Count or Countess -- with the villages nearest it as its lands. The villages
# nearest the capital are the crown's own lands (province_id is NULL). Who
# inherits when a lord dies, and regencies for child lords, are
# Data.Government.succession.

LORD_TITLES = ("Count", "Countess")




def house_name(conn, lord):
    """A lord's house bears their family's name ("House Reed"); someone without one founds "House of <name>"."""
    row = conn.execute("SELECT f.name FROM npcs n JOIN families f ON f.id = n.family_id WHERE n.id = ?", (lord["id"],)).fetchone()
    return f"House {row[0]}" if row else f"House of {lord['name']}"

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

    for kingdom in conn.execute("SELECT id, capital_city_id, type FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():

        if kingdom["type"] == "Republic":
            continue                     # a republic has no hereditary lords (Data.Government.republic)

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
                house = house_name(conn, lord)
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
            # nearest, and on an exact tie the lowest city id -- the same rule everywhere
            nearest = min(centers, key=lambda c: ((c["map_x"] - village["map_x"]) ** 2 + (c["map_y"] - village["map_y"]) ** 2, c["id"]))
            conn.execute("UPDATE cities SET province_id = ? WHERE id = ?", (province_of_seat.get(nearest["id"]), village["id"]))

    conn.commit()
    return made


def install_lord(conn, province, seat_city, lord, inherited_from=None, regent=None):
    """
    Makes `lord` the Count or Countess of a province: any city they ruled
    before is given up (Data.Government.succession.vacate_seats), they move
    to the seat, take the title, and are recorded as its lord and the seat's
    ruler. A child lord gets a regent. An heir carries on the house; anyone
    else founds a new one. Logged. Returns the lord.
    """

    from Data.Database.repository import save_npc, create_event, get_npc
    from Data.Infrastructure.migration import relocate_npc
    from Data.Government.succession import vacate_seats, relation_word

    vacate_seats(conn, lord, seat_city["id"])

    if lord["city_id"] != seat_city["id"]:
        relocate_npc(conn, lord["id"], seat_city["id"])
        lord = get_npc(conn, lord["id"])

    lord["title"] = lord_title(lord)
    save_npc(conn, lord)

    house = province["house"] if inherited_from and province["house"] else house_name(conn, lord)
    conn.execute("UPDATE provinces SET lord_id = ?, house = ?, regent_id = ? WHERE id = ?",
                 (lord["id"], house, regent["id"] if regent else None, province["id"]))
    if not inherited_from:          # a new house starts afresh with the crown; an heir inherits the family's standing
        from Data.Government.feudal import LOYALTY_START
        conn.execute("UPDATE provinces SET loyalty = ? WHERE id = ?", (LOYALTY_START, province["id"]))
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (lord["id"], seat_city["id"]))

    if inherited_from:
        how = (f"on the death of {inherited_from['name']}, their {relation_word(conn, lord['id'], inherited_from['id'])}"
               if not inherited_from["alive"] else f"when {inherited_from['name']}, their {relation_word(conn, lord['id'], inherited_from['id'])}, took the throne")
        create_event(conn, "Inheritance",
                     f"{lord['name']} inherited the {province['name']} {how}, and now rules as {lord['title']} ({house}).",
                     city_id=seat_city["id"], kingdom_id=province["kingdom_id"])
    else:
        create_event(conn, "Investiture",
                     f"The crown granted the {province['name']} to {lord['name']}, who rules as {lord['title']} and founds the {house}.",
                     city_id=seat_city["id"], kingdom_id=province["kingdom_id"])

    if regent:
        create_event(conn, "Regency",
                     f"{lord['name']} is only {lord['age']}, so {regent['name']} rules the {province['name']} as regent until they come of age.",
                     city_id=seat_city["id"], kingdom_id=province["kingdom_id"])

    conn.commit()
    return lord