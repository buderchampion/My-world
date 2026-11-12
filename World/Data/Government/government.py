import random
from Data.Database.repository import get_city_npcs, save_npc

# Professions considered notable enough to plausibly rule -- tried
# first, before falling back to any employed adult in the city.
RULER_CANDIDATE_PROFESSIONS = ["Knight", "Captian", "Merchant", "Scholar", "Priest"]

NON_RULER_PROFESSIONS = ("Laborer", "Child")


def assign_city_ruler(conn, city):
    """
    Picks one NPC to rule this city -- King/Queen for a Capital, a Count or
    Countess for a province seat, Mayor everywhere else. Sets city["ruler"]
    and saves the chosen NPC with their new title.

    A province seat is hereditary (Data.Government.provinces): when its lord
    has died, their heir inherits first; only if there is none does the
    crown appoint a new lord from the town's notables.
    """

    from Data.Government.provinces import seat_province, install_lord
    from Data.Government.succession import inherit_crown, inherit_county
    from Data.Database.repository import get_npc

    is_capital = city["type"] == "Capital"

    # A republic's capital is never inherited: the Great Council elects a Doge
    # for life (Data.Government.republic).
    if is_capital:
        from Data.Government.republic import is_republic, elect_doge
        if is_republic(conn, city["kingdom"]):
            doge = elect_doge(conn, city)
            city["ruler"] = doge["id"] if doge else None
            return doge

    province = None if is_capital else seat_province(conn, city["id"])

    # A crown or county whose holder has died stays in the family if it can
    # (Data.Government.succession). A throne emptied by revolt has no previous
    # holder recorded here, so the people choose.
    if (is_capital or province) and city.get("ruler"):
        previous = get_npc(conn, city["ruler"])
        if previous and not previous["alive"]:
            heir = inherit_crown(conn, city, previous) if is_capital else inherit_county(conn, province, city, previous)
            if heir:
                city["ruler"] = heir["id"]
                return heir

    npcs = get_city_npcs(conn, city["id"])

    # only the living can rule: a city's people include its dead
    candidates = [
        npc for npc in npcs
        if npc["alive"] and npc["profession"] in RULER_CANDIDATE_PROFESSIONS and npc["title"] is None
    ]

    if not candidates:
        candidates = [
            npc for npc in npcs
            if npc["alive"] and npc["profession"] not in NON_RULER_PROFESSIONS and npc["title"] is None
        ]

    if not candidates:
        city["ruler"] = None
        return None

    ruler = random.choice(candidates)

    if province:
        lord = install_lord(conn, province, city, ruler)        # appointed: a new house
        city["ruler"] = lord["id"]
        return lord

    if city["type"] == "Capital":
        ruler["title"] = "King" if ruler["gender"] == "Male" else "Queen"
        # a new dynasty: no regency carries over from the old one
        conn.execute("UPDATE kingdoms SET regent_id = NULL WHERE id = ?", (city["kingdom"],))
    else:
        ruler["title"] = "Mayor"

    save_npc(conn, ruler)

    conn.commit()

    city["ruler"] = ruler["id"]
    
    # Save it. Setting city["ruler"] only changes the copy in memory, and
    # nothing else writes it back, so without this every city looked
    # rulerless again at the next monthly tick and crowned yet another
    # king or mayor -- one coronation per city per month, forever.
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (ruler["id"], city["id"]))

    from Data.Events.events import coronation_event
    coronation_event(conn, ruler, city)
    conn.commit()

    return ruler


def assign_kingdom_ruler(kingdom, capital_city):
    """
    The Capital's ruler doubles as the kingdom's monarch. Call this
    after assign_city_ruler has already been run on the capital.
    """

    kingdom["ruler"] = capital_city.get("ruler")

    return kingdom["ruler"]