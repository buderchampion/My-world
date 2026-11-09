import random

# The fall of capitals. A campaign (Data.Government.campaigns) marches on the
# enemy's capital only when it is far stronger: when its marching force is at
# least CAPITAL_ODDS times the defender's whole army. Capitals are walled, so
# they must be besieged.
#
# When a capital falls it becomes an ordinary (walled) city of the conqueror,
# occupied like any town (Data.Government.occupation), and chance decides the
# ruler's fate, a third each:
#   flight     -- they escape to the kingdom's new capital, still ruling;
#   death      -- they are slain in the fall;
#   captivity  -- they are carried off to the conqueror's capital, crown lost.
# After a death or a capture, the crown passes down their line at the new
# capital (a republic elects a new Doge).
#
# The new capital is the kingdom's largest remaining town; a county seat
# that becomes the capital is absorbed into the crown's own lands. A kingdom
# with no town left falls: its history ends, chronicled by both sides.

CAPITAL_ODDS = 2.0
FATES = ("flight", "death", "captivity")


def aims_at_capital(conn, attacker, defender):
    """Is the attacker's marching force at least CAPITAL_ODDS times the defender's whole army?"""
    from Data.Government.military import MILITARY_PROFESSIONS
    from Data.Government.campaigns import MARCH_SHARE, HOUSEHOLDS_PER_LEVY
    marks = ",".join("?" * len(MILITARY_PROFESSIONS))

    def soldiers(kingdom_id):
        return conn.execute(
            f"""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
                WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (kingdom_id, *MILITARY_PROFESSIONS)).fetchone()[0]

    levies = sum(int(r[0] // HOUSEHOLDS_PER_LEVY) for r in conn.execute(
        "SELECT households FROM cities WHERE kingdom_id = ? AND households >= 1", (attacker["id"],)))
    marching = soldiers(attacker["id"]) * MARCH_SHARE + levies
    return marching >= CAPITAL_ODDS * max(1, soldiers(defender["id"]))


def fall_of_capital(conn, city, attacker, defender):
    """
    `city`, the defender's capital, has fallen to the attacker (it is already
    the attacker's). Decides the ruler's fate, gives the kingdom a new capital
    or ends it, and chronicles it from both sides. Returns a summary.
    """

    from Data.Database.repository import get_npc, save_npc, get_city, create_event
    from Data.Events.events import death_event
    from Data.Infrastructure.migration import relocate_npc
    from Data.History.chronicle import write_entry, current_date, record_coronation
    from Data.Government.succession import inherit_crown
    from Data.Government.republic import elect_doge

    world_id = defender["world_id"]
    year, season = current_date(conn, world_id)
    conn.execute("UPDATE cities SET type = 'City', ruler_id = NULL WHERE id = ?", (city["id"],))   # one capital per kingdom

    remaining = conn.execute(
        """SELECT id, name, type, population + households * 5 people FROM cities WHERE kingdom_id = ?
           ORDER BY (type = 'Village'), people DESC, id""", (defender["id"],)).fetchall()
    ruler_id = defender.get("ruler_id") or defender.get("ruler")      # a raw row, or the game's kingdom record
    ruler = get_npc(conn, ruler_id) if ruler_id else None
    if ruler and not ruler["alive"]:
        ruler = None
    fate = random.choice(FATES) if ruler else None
    title = (ruler["title"] or "ruler") if ruler else None

    new_capital = dict(remaining[0]) if remaining else None
    if new_capital:
        # a county seat that becomes the capital is absorbed into the crown's own lands
        seat = conn.execute("SELECT * FROM provinces WHERE seat_city_id = ?", (new_capital["id"],)).fetchone()
        if seat:
            if seat["lord_id"]:
                lord = get_npc(conn, seat["lord_id"])
                if lord and lord["title"] in ("Count", "Countess"):
                    lord["title"] = None
                    save_npc(conn, lord)
            conn.execute("UPDATE cities SET province_id = NULL WHERE province_id = ?", (seat["id"],))
            conn.execute("DELETE FROM provinces WHERE id = ?", (seat["id"],))
        conn.execute("UPDATE cities SET type = 'Capital', ruler_id = NULL WHERE id = ?", (new_capital["id"],))
        conn.execute("UPDATE kingdoms SET capital_city_id = ? WHERE id = ?", (new_capital["id"], defender["id"]))

    # the ruler's fate
    words = ""
    if ruler:
        if fate == "flight" and new_capital:
            relocate_npc(conn, ruler["id"], new_capital["id"])
            conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (ruler["id"], new_capital["id"]))
            words = f"its {title}, {ruler['name']}, fled to {new_capital['name']}"
        elif fate == "death" or (fate == "flight" and not new_capital):
            fate = "death"
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (ruler["id"],))
            ruler["alive"] = False
            death_event(conn, ruler, "the fall of the capital", kingdom_id=defender["id"])
            words = f"its {title}, {ruler['name']}, was slain in the fall"
        else:
            ruler["title"] = None
            save_npc(conn, ruler)
            attacker_capital = attacker.get("capital") or attacker.get("capital_city_id")
            if attacker_capital:
                relocate_npc(conn, ruler["id"], attacker_capital)
            words = f"its {title}, {ruler['name']}, was carried off in chains to {attacker['name']}"

        # a new ruler at the new capital, if the old one is lost
        if fate in ("death", "captivity") and new_capital:
            capital = get_city(conn, new_capital["id"])
            successor = elect_doge(conn, capital) if defender.get("type") == "Republic" else inherit_crown(conn, capital, ruler)
            if successor is None and defender.get("type") != "Republic":
                from Data.Government.government import assign_city_ruler
                successor = assign_city_ruler(conn, capital)
            if successor:
                conn.execute("UPDATE kingdoms SET ruler_id = ? WHERE id = ?", (successor["id"], defender["id"]))
                from Data.Database.repository import get_kingdom
                record_coronation(conn, world_id, get_kingdom(conn, defender["id"]), successor, "succession", predecessor=ruler)

    fields = {"city": city["name"], "fate": words or "its throne stood empty"}
    if new_capital:
        write_entry(conn, defender["id"], "Capital Fallen", "Capital Fallen (lost)", year, season,
                    dict(fields, kingdom=defender["name"], other=attacker["name"]), seed_parts=(city["id"],))
        write_entry(conn, attacker["id"], "Capital Fallen", "Capital Fallen (taken)", year, season,
                    dict(fields, kingdom=attacker["name"], other=defender["name"]), seed_parts=(city["id"],))
        create_event(conn, "Capital Fallen", f"{city['name']}, capital of {defender['name']}, fell to {attacker['name']}; {fields['fate']}.",
                     city_id=city["id"], kingdom_id=defender["id"])
    else:
        conn.execute("UPDATE kingdoms SET status = 'Fallen', capital_city_id = NULL, ruler_id = NULL, regent_id = NULL WHERE id = ?",
                     (defender["id"],))
        write_entry(conn, defender["id"], "Kingdom Fallen", "Kingdom Fallen (lost)", year, season,
                    dict(fields, kingdom=defender["name"], other=attacker["name"]), seed_parts=(city["id"],))
        write_entry(conn, attacker["id"], "Kingdom Fallen", "Kingdom Fallen (conquered)", year, season,
                    dict(fields, kingdom=attacker["name"], other=defender["name"]), seed_parts=(city["id"],))
        create_event(conn, "Kingdom Fallen", f"With {city['name']} taken, {defender['name']} is no more; {fields['fate']}.",
                     city_id=city["id"], kingdom_id=defender["id"])

    conn.commit()
    return {"fate": fate, "new_capital": new_capital["name"] if new_capital else None, "fallen": not new_capital}