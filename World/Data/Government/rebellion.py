import random

# Rebellion. A restless lord (Data.Government.feudal: loyalty below
# RESTLESS_BELOW) may rise against the crown -- the lower their loyalty, the
# likelier each month. When one rises, every other restless lord of the
# kingdom joins them: hard times (dear bread, famine, a lost war) touch every
# lord at once, so a miserable realm breeds a coalition, while a lone lord
# with a grudge seldom has the strength. Each rebel lord also spends their
# purse on mercenaries. The coalition rises only if it can field at least a
# third of the crown's strength; otherwise its lords bide their time.
#
# The rebels are their counties' soldiers, levies called up from their
# villages (Data.World.villagers: real people), and mercenaries; the crown has
# its soldiers from everywhere else. The odds follow strength, as in any war.
#
#   The crown wins: every rebel lord is executed, and each county goes to a
#   new house -- no inheritance for traitors.
#   The rebels win: their counties break away together as a new kingdom. The
#   leader's seat is its capital and the leader its King or Queen; the leader's
#   county becomes the new crown's own lands, the others stay counties under
#   their lords. The leader's purse becomes the treasury, and the two kingdoms
#   begin as enemies.
#
# Surviving levies go home as Farmers. Both outcomes are chronicled.

RISING_CHANCE_AT_ZERO = 0.15   # monthly chance for a lord at 0 loyalty; falls to 0 at the restless line
MIN_STRENGTH_RATIO = 1 / 3     # rebels must field at least this share of the crown's strength
HOUSEHOLDS_PER_LEVY = 10       # the lord calls up one young man per this many households
ENMITY = -30                   # favor each way between a new kingdom and the one it left
MERCENARY_COST = 500           # gold for one mercenary, who fights as well as the crown's average soldier


def _soldiers(conn, where, params, away):
    from Data.Combat.combat import DEFENDER_PROFESSIONS
    from Data.Database.repository import get_npc
    marks = ",".join("?" * len(DEFENDER_PROFESSIONS))
    ids = [row[0] for row in conn.execute(
        f"SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE n.alive = 1 AND n.profession IN ({marks}) AND {where}",
        (*DEFENDER_PROFESSIONS, *params))]
    return [get_npc(conn, i) for i in ids if i not in away]


def sides(conn, provinces):
    """
    For a coalition of rebel provinces (all of one kingdom): (their soldiers,
    the crown's soldiers -- everyone else's, levies they can raise,
    mercenaries their lords' purses can hire).
    """
    from Data.Government.armies import get_army_npc_ids
    kingdom_id = provinces[0]["kingdom_id"]
    world_id = conn.execute("SELECT world_id FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()[0]
    away = get_army_npc_ids(conn, world_id)
    ids = tuple(p["id"] for p in provinces)
    marks = ",".join("?" * len(ids))
    rebels = _soldiers(conn, f"c.province_id IN ({marks})", ids, away)
    crown = _soldiers(conn, f"c.kingdom_id = ? AND (c.province_id IS NULL OR c.province_id NOT IN ({marks}))",
                      (kingdom_id, *ids), away)
    levies = sum(int(conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities WHERE province_id = ?",
                                  (p["id"],)).fetchone()[0] // HOUSEHOLDS_PER_LEVY) for p in provinces)
    mercenaries = sum(int(conn.execute("SELECT treasury FROM provinces WHERE id = ?", (p["id"],)).fetchone()[0] // MERCENARY_COST)
                      for p in provinces)
    return rebels, crown, levies, mercenaries


def rising_chance(loyalty):
    from Data.Government.feudal import RESTLESS_BELOW
    return max(0.0, RISING_CHANCE_AT_ZERO * (RESTLESS_BELOW - loyalty) / RESTLESS_BELOW)


def consider_rebellions(conn, world_id):
    """Monthly: a restless lord may rise, and every other restless lord of the
    kingdom joins them. At most one rising per kingdom a month. Returns the
    results of any risings."""

    from Data.Government.feudal import RESTLESS_BELOW

    results = []
    for kingdom in conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():
        restless = [dict(p) for p in conn.execute(
            """SELECT p.* FROM provinces p JOIN npcs n ON n.id = p.lord_id
               WHERE p.kingdom_id = ? AND n.alive = 1 AND p.loyalty < ? ORDER BY p.loyalty, p.id""",
            (kingdom["id"], RESTLESS_BELOW))]
        from Data.Government.edicts import martial_law_active, today, MARTIAL_LAW_FACTOR
        k_row = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone())
        damper = MARTIAL_LAW_FACTOR if martial_law_active(k_row, today(conn, world_id)) else 1   # martial law
        leader = next((p for p in restless if random.random() < damper * rising_chance(p["loyalty"])), None)
        if leader is None:
            continue
        coalition = [leader] + [p for p in restless if p["id"] != leader["id"]]
        rebels, crown, levies, mercenaries = sides(conn, coalition)
        if len(rebels) + levies + mercenaries < MIN_STRENGTH_RATIO * len(crown):
            continue                            # too weak together: they bide their time
        results.append(rise(conn, leader, coalition[1:]))
    return results


def rise(conn, leader, allies=()):
    """
    `leader`'s rising, joined by the lords of `allies` (provinces of the same
    kingdom): levies are called up and mercenaries hired in every rebel
    county, the battle is fought, and the counties are either lost to new
    houses or break away together as a new kingdom. Returns a summary.
    """

    from Data.Combat.combat import get_combat_power, apply_casualties
    from Data.Events.events import death_event
    from Data.World.villagers import make_individual, send_levies_home
    from Data.Database.repository import get_npc, create_event

    coalition = [leader] + list(allies)
    lords = [get_npc(conn, p["lord_id"]) for p in coalition]
    kingdom = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (leader["kingdom_id"],)).fetchone())

    rebels, crown, _, mercenaries = sides(conn, coalition)

    # every rebel lord calls up their villages, in proportion to their size...
    levies = []
    for province in coalition:
        villages = conn.execute(
            "SELECT id, households FROM cities WHERE province_id = ? AND households >= 1 ORDER BY households DESC",
            (province["id"],)).fetchall()
        wanted = int(sum(v["households"] for v in villages) // HOUSEHOLDS_PER_LEVY)
        total = sum(v["households"] for v in villages) or 1
        raised = 0
        for village in villages:
            for _ in range(int(round(wanted * village["households"] / total))):
                if raised >= wanted:
                    break
                levies.append(make_individual(conn, village["id"], village["id"], "Soldier",
                                              age=random.randint(16, 40), gender="Male", needs_workplace=False))
                raised += 1
    levies = [get_npc(conn, n["id"]) for n in levies]

    # ...and empties their purse on mercenaries
    for province in coalition:
        spent = int(conn.execute("SELECT treasury FROM provinces WHERE id = ?", (province["id"],)).fetchone()[0]
                    // MERCENARY_COST) * MERCENARY_COST
        conn.execute("UPDATE provinces SET treasury = treasury - ? WHERE id = ?", (spent, province["id"]))

    fighters = rebels + levies
    crown_power = sum(get_combat_power(n) for n in crown)
    average = crown_power / len(crown) if crown else 1
    rebel_power = sum(get_combat_power(n) for n in fighters) + mercenaries * average
    rebels_win = (rebel_power > 0) and (crown_power <= 0 or random.random() < rebel_power / (rebel_power + crown_power))

    rebel_dead = apply_casualties(conn, fighters, won=rebels_win)
    crown_dead = apply_casualties(conn, crown, won=not rebels_win)
    from Data.Combat.nemesis import record_battle          # who slew whom
    record_battle(conn, fighters, rebel_dead, crown, crown_dead, context="rising", kingdom_id=kingdom["id"])
    for npc in rebel_dead + crown_dead:
        death_event(conn, npc, "Rebellion", kingdom_id=kingdom["id"])

    send_levies_home(conn, [n["id"] for n in levies])

    joined = f", joined by the {', the '.join(p['name'] for p in allies)}," if allies else ""
    create_event(conn, "Rebellion",
                 f"{lords[0]['name']} of the {leader['name']}{joined} rose against the crown of {kingdom['name']} with "
                 f"{len(rebels)} soldiers, {len(levies)} levies and {mercenaries} mercenaries against {len(crown)}; "
                 + ("the rebels carried the day." if rebels_win else "the rising was crushed."),
                 city_id=leader["seat_city_id"], kingdom_id=kingdom["id"])

    summary = {"province": leader["name"], "lord": lords[0]["name"], "allies": [p["name"] for p in allies],
               "rebels_win": rebels_win, "rebels": len(fighters), "levies": len(levies),
               "mercenaries": mercenaries, "crown": len(crown)}
    if rebels_win:
        summary["new_kingdom"] = break_away(conn, leader, list(allies), kingdom, lords[0])
    else:
        for province, lord in zip(coalition, lords):
            crush(conn, province, kingdom, lord)
    conn.commit()
    return summary


def crush(conn, province, kingdom, lord):
    """A rebel lord's rising failed: they are executed and their county goes to a new house."""

    from Data.Events.events import death_event
    from Data.Government.government import assign_city_ruler
    from Data.History.chronicle import write_entry, current_date
    from Data.Database.repository import get_npc, get_city

    lord = get_npc(conn, lord["id"])
    if lord["alive"]:
        lord["alive"] = False
        conn.execute("UPDATE npcs SET alive = 0, workplace_id = NULL, home_id = NULL WHERE id = ?", (lord["id"],))
        death_event(conn, lord, "Executed", kingdom_id=kingdom["id"])

    seat = get_city(conn, province["seat_city_id"])
    seat["ruler"] = None                     # no heir for a traitor: the crown grants it anew
    conn.execute("UPDATE cities SET ruler_id = NULL WHERE id = ?", (seat["id"],))
    conn.execute("UPDATE provinces SET lord_id = NULL, regent_id = NULL WHERE id = ?", (province["id"],))
    assign_city_ruler(conn, seat)

    year, season = current_date(conn, kingdom["world_id"])
    monarch = get_npc(conn, kingdom["ruler_id"]) if kingdom["ruler_id"] else None
    write_entry(conn, kingdom["id"], "Rebellion", "Rebellion Crushed", year, season,
                {"kingdom": kingdom["name"], "ruler": lord["name"], "county": province["name"],
                 "sovereign": monarch["name"] if monarch else "the crown"}, seed_parts=(province["id"],))


def break_away(conn, leader, allies, kingdom, lord):
    """
    The rising succeeded: the rebel counties become a kingdom of their own.
    The leader's seat is the capital and its county the new crown's lands;
    the allies' counties stay counties, under their lords. Returns its name.
    """

    from Data.Kingdoms.kingdom_generator import generate_kingdom_name, KINGDOM_COLORS
    from Data.Database.repository import create_kingdom, get_npc, save_npc, adjust_kingdom_favor, get_kingdom, get_city
    from Data.Events.events import coronation_event
    from Data.History.chronicle import write_entry, current_date, record_coronation
    from Data.Government.feudal import LOYALTY_START

    world_id = kingdom["world_id"]
    taken = {row[0] for row in conn.execute("SELECT name FROM kingdoms WHERE world_id = ?", (world_id,))}
    name = next((n for n in (generate_kingdom_name() for _ in range(50)) if n not in taken), f"Free {leader['name']}")
    used = {row[0] for row in conn.execute("SELECT color FROM kingdoms WHERE world_id = ?", (world_id,))}
    color = next((c for c in KINGDOM_COLORS if c not in used), "#%06X" % random.randint(0, 0xFFFFFF))

    new_id = create_kingdom(conn, world_id, name, color)["id"]
    treasury = conn.execute("SELECT treasury FROM provinces WHERE id = ?", (leader["id"],)).fetchone()[0]

    lord = get_npc(conn, lord["id"])
    lord["title"] = "King" if lord["gender"] == "Male" else "Queen"
    save_npc(conn, lord)

    conn.execute(
        """UPDATE kingdoms SET capital_city_id = ?, ruler_id = ?, regent_id = ?, treasury = ?, tax_rate = ?, price_level = ?
           WHERE id = ?""",
        (leader["seat_city_id"], lord["id"], leader["regent_id"], treasury, kingdom["tax_rate"], kingdom["price_level"], new_id))

    # the leader's county: the new crown's own lands
    conn.execute("UPDATE cities SET kingdom_id = ?, province_id = NULL WHERE province_id = ?", (new_id, leader["id"]))
    conn.execute("UPDATE cities SET type = 'Capital', ruler_id = ? WHERE id = ?", (lord["id"], leader["seat_city_id"]))
    conn.execute("DELETE FROM provinces WHERE id = ?", (leader["id"],))

    # the allies' counties come too, as the new realm's founding nobility
    for ally in allies:
        conn.execute("UPDATE cities SET kingdom_id = ? WHERE province_id = ?", (new_id, ally["id"]))
        conn.execute("UPDATE provinces SET kingdom_id = ?, loyalty = ? WHERE id = ?", (new_id, LOYALTY_START, ally["id"]))

    adjust_kingdom_favor(conn, new_id, kingdom["id"], ENMITY)
    adjust_kingdom_favor(conn, kingdom["id"], new_id, ENMITY)

    year, season = current_date(conn, world_id)
    fields = {"county": leader["name"], "ruler": lord["name"]}
    write_entry(conn, kingdom["id"], "Breakaway", "Breakaway (lost)", year, season,
                dict(fields, kingdom=kingdom["name"], other=name), seed_parts=(leader["id"],))
    write_entry(conn, new_id, "Breakaway", "Breakaway (founded)", year, season,
                dict(fields, kingdom=name, other=kingdom["name"]), seed_parts=(leader["id"],))

    coronation_event(conn, lord, get_city(conn, leader["seat_city_id"]))
    record_coronation(conn, world_id, get_kingdom(conn, new_id), lord, "founding")

    conn.commit()
    return name