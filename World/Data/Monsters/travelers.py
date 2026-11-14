"""
Beasts and travelers. Each day, everyone on the road -- trade caravans,
supply caravans, people on journeys -- is placed partway along it (as the
map places them). A beast lair within AMBUSH_REACH of one may fall on it:
AMBUSH_CHANCE a day for each lair near enough. Half the lair hunts as a
pack, at least one beast, and the fight is by the book (Data.Combat.melee):

    a caravan        two drivers and a guard; beaten, its goods are lost
    a supply caravan the same; beaten, its army's supplies never come
    a traveler       alone, by their trade and training; beaten, they die
                     on the road, and are mourned

Either way the lair loses its fallen; with none left, it is cleared. Beasts
near a druid's home are calmer: they attack CALM_SHARE as often
(Data.Magic.magic).
"""

from __future__ import annotations

import math
import random

from Data.Combat import melee
from Data.Monsters.bestiary import by_name

AMBUSH_REACH = 200               # map units: a lair hunts the roads this near it
AMBUSH_CHANCE = 0.01             # each day, for each lair near enough: a typical trade journey through the wilds is attacked about one time in six
PACK_SHARE = 0.5                 # the share of a lair that hunts (the rest keep the den)
CARAVAN_DRIVERS = 2              # a caravan's commoners...
CARAVAN_GUARDS = 1               # ...and its hired guard
DISTANCE_PER_TRAVEL_DAY = 90     # road speed (Data.Infrastructure.roads)


def _positions(conn, world_id):
    towns = {r[0]: (r[1], r[2]) for r in conn.execute(
        """SELECT c.id, c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?""", (world_id,))}
    days = {}
    for a, b, d in conn.execute("SELECT city_a_id, city_b_id, travel_days FROM roads"):
        days[(a, b)] = days[(b, a)] = max(1, d or 1)
    return towns, days


def _along(towns, days, start, end, days_left):
    """A point on the road from `start` to `end`, `days_left` from arriving."""
    if start not in towns or end not in towns:
        return None
    (x0, y0), (x1, y1) = towns[start], towns[end]
    total = days.get((start, end)) or max(1, round(math.dist((x0, y0), (x1, y1)) / DISTANCE_PER_TRAVEL_DAY))
    done = max(0.0, min(1.0, 1 - (days_left or 0) / total))
    return x0 + (x1 - x0) * done, y0 + (y1 - y0) * done


def on_the_road(conn, world_id):
    """Everyone travelling today, placed on their road: [(kind, id, x, y, row)]."""
    towns, days = _positions(conn, world_id)
    out = []
    for c in conn.execute("SELECT * FROM caravans WHERE status = 'En Route'"):
        pos = _along(towns, days, c["origin_city_id"], c["destination_city_id"], c["days_remaining"])
        if pos:
            out.append(("caravan", c["id"], *pos, dict(c)))
    for s in conn.execute("SELECT * FROM supply_caravans WHERE status IN ('En Route', 'Returning') AND next_city_id IS NOT NULL"):
        pos = _along(towns, days, s["location_city_id"], s["next_city_id"], s["leg_days_remaining"])
        if pos:
            out.append(("supply", s["id"], *pos, dict(s)))
    for t in conn.execute("SELECT * FROM npc_travels WHERE status IN ('Traveling Out', 'Traveling Back')"):
        start, end = (t["origin_city_id"], t["destination_city_id"]) if t["status"] == "Traveling Out" \
            else (t["destination_city_id"], t["origin_city_id"])
        pos = _along(towns, days, start, end, t["days_remaining"])
        if pos:
            out.append(("traveler", t["id"], *pos, dict(t)))
    return out


def _defenders(conn, kind, row):
    if kind == "traveler":
        npc = conn.execute("SELECT * FROM npcs WHERE id = ? AND alive = 1", (row["npc_id"],)).fetchone()
        if npc is None:
            return []
        skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
        return [melee.person_fighter(dict(npc), side="travelers", skills=skills)]
    drivers = [melee.Fighter("driver", "travelers", melee.HP_COMMONER, melee.COMMONER[1], melee.person_thac0(0), [melee.COMMONER[2]])
               for _ in range(CARAVAN_DRIVERS)]
    level, ac, weapon = melee.TRADES["Guard"]
    guards = [melee.Fighter("guard", "travelers", melee.HP_PER_LEVEL * level, ac, melee.person_thac0(level), [weapon])
              for _ in range(CARAVAN_GUARDS)]
    return drivers + guards


def ambush(conn, world_id, lair, kind, ident, row):
    """A pack from `lair` falls on one traveler or caravan. Returns its report."""
    from Data.Database.repository import create_event, get_npc
    monster = by_name(lair["monster"])
    pack = min(lair["count"], max(1, round(lair["count"] * PACK_SHARE)))
    beasts = melee.monster_fighters(monster, pack, lair["hit_points_each"], side="beasts", ref=lair["id"])
    defenders = _defenders(conn, kind, row)
    if not defenders:
        return None
    result = melee.battle(beasts, defenders)
    beasts_won = result["winner"] == "a"
    fallen = len(result["dead_a"])
    left = lair["count"] - fallen
    conn.execute("UPDATE monster_lairs SET count = ?, status = ? WHERE id = ?",
                 (max(0, left), "Cleared" if left <= 0 else "Active", lair["id"]))
    lair["count"] = max(0, left)
    from Data.Monsters.raids import count_of, plural
    pack_text = count_of(pack, monster["name"])
    origin = row.get("origin_city_id") or row.get("location_city_id")
    place = conn.execute("SELECT name FROM cities WHERE id = ?", (origin,)).fetchone()
    place = place[0] if place else "the road"
    if kind == "traveler":
        npc = get_npc(conn, row["npc_id"])
        if beasts_won:
            from Data.Events.events import death_event
            from Data.Relationships.relationships import apply_grief
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (npc["id"],))
            conn.execute("UPDATE npc_travels SET status = 'Completed', days_remaining = 0 WHERE id = ?", (ident,))
            npc = get_npc(conn, npc["id"])
            death_event(conn, npc, f"slain by {plural(monster['name'], pack).lower()} on the road", city_id=npc["city_id"])
            apply_grief(conn, npc)
            text = f"{npc['name']} was slain on the road from {place} by {pack_text}."
        else:
            text = f"{npc['name']} fought off {pack_text} on the road from {place}" + (f", slaying {fallen}." if fallen else ".")
        city_id = npc["city_id"]
    else:
        table = "caravans" if kind == "caravan" else "supply_caravans"
        if beasts_won:
            conn.execute(f"UPDATE {table} SET status = 'Raided' WHERE id = ?", (ident,))
        what = "A caravan" if kind == "caravan" else "A supply caravan"
        text = (f"{what} out of {place} was overrun by {pack_text}; its goods were lost." if beasts_won
                else f"{what} out of {place} beat off {pack_text}" + (f", slaying {fallen}." if fallen else "."))
        city_id = origin
    if left <= 0:
        text += f" The lair of the {plural(monster['name'])} is no more."
    create_event(conn, "Beast Attack", text, city_id=city_id)
    return {"kind": kind, "id": ident, "monster": monster["name"], "pack": pack, "beasts_won": beasts_won,
            "beasts_fallen": fallen, "cleared": left <= 0, "text": text}


def beasts_on_the_roads(conn, world_id):
    """Daily: beasts fall on travelers near their lairs. Returns the attacks' reports."""
    lairs = [dict(r) for r in conn.execute(
        "SELECT * FROM monster_lairs WHERE world_id = ? AND status = 'Active' AND kind = 'beast' AND count > 0", (world_id,))]
    if not lairs:
        return []
    from Data.Magic.magic import druid_homes, calmed, CALM_SHARE
    homes = druid_homes(conn, world_id)
    chance = {lair["id"]: AMBUSH_CHANCE * (CALM_SHARE if calmed(lair, homes) else 1) for lair in lairs}
    reports = []
    reach2 = AMBUSH_REACH ** 2
    for kind, ident, x, y, row in on_the_road(conn, world_id):
        for lair in lairs:
            if lair["count"] <= 0 or (lair["map_x"] - x) ** 2 + (lair["map_y"] - y) ** 2 > reach2:
                continue
            if random.random() >= chance[lair["id"]] or by_name(lair["monster"]) is None:
                continue
            report = ambush(conn, world_id, lair, kind, ident, row)
            if report:
                reports.append(report)
                break                                  # one attack a day is enough for anyone
    conn.commit()
    return reports