"""
Horse kingdoms: hordes that settle (Part 2b of the horse lords).

Conquest    a horde with enough riders may march on a neighbour that does not
            pay it (CONQUEST_RIDERS, CONQUEST_CHANCE): a real campaign
            (Data.Government.campaigns) against the town nearest its camp --
            or the capital, if the riders far outnumber the defenders.
            Hordes and horse kingdoms fight from the saddle: in a campaign
            battle their side is CAVALRY_EDGE times as strong.

Settling    a horde becomes a horse kingdom (type 'Horse Kingdom', its khan
            now King or Queen) in one of two ways:
              - it took a town and held it until the town was counted part
                of the realm (Data.Government.occupation): the town becomes
                its seat, and the camp stays where it stands as a horse town;
              - it went PEACE_YEARS without a raid or a war: each year then
                PEACE_SETTLE_CHANCE that the khan stops wandering. The camp
                becomes the seat, and herders' families found HORSE_VILLAGES
                villages on the empty land around it.
            Its danegeld ends -- the old payers are neighbours now, and warm
            to it (SETTLED_FAVOR). Its riders are paid like any soldiers from
            now on, and its herds still feed its towns on the steppe.

Alliances   horse kingdoms are a people of oaths: each year at peace they
            and every neighbour within FRIEND_REACH warm to each other by
            OATH_WARMTH more (alliances form as usual, Data.Government.diplomacy).
            And when an ally is attacked, the riders come: ANSWER_SHARE of a
            horse kingdom's riders join the defenders of any allied town it
            can reach (AID_REACH), like the Rohirrim riding to Gondor.
"""

import math
import random

CONQUEST_RIDERS = 60
CONQUEST_CHANCE = 0.3
CAVALRY_EDGE = 1.25

PEACE_YEARS = 6
PEACE_SETTLE_CHANCE = 0.4
HORSE_VILLAGES = 2
VILLAGE_HOUSEHOLDS = (15, 25)
VILLAGE_REACH = 700
SETTLED_FAVOR = 20.0

FRIEND_REACH = 2000
OATH_WARMTH = 4.0
ANSWER_SHARE = 0.5
AID_REACH = 1600
AID_FAVOR = 10.0

HORSE_PEOPLES = ("Horde", "Horse Kingdom")


def is_horse_people(kingdom):
    return bool(kingdom) and kingdom.get("type") in HORSE_PEOPLES


def horse_kingdoms(conn, world_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM kingdoms WHERE world_id = ? AND type = 'Horse Kingdom' AND status != 'Fallen'", (world_id,))]


def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def mark_fought(conn, kingdom_id, day):
    conn.execute("UPDATE kingdoms SET last_fought_day = ? WHERE id = ?", (day, kingdom_id))


# ------------------------------------------------------------------ conquest
def nearest_town(conn, horde, defender):
    """The defender's town nearest the horde's camp that its riders can reach by road, or None."""
    from Data.Government.armies import find_route
    from Data.Database.repository import get_city
    camp = conn.execute("SELECT id, map_x, map_y FROM cities WHERE id = ?", (horde["capital_city_id"],)).fetchone()
    towns = conn.execute(
        "SELECT id, map_x, map_y FROM cities WHERE kingdom_id = ? AND type != 'Village' AND id != ?",
        (defender["id"], defender.get("capital_city_id") or defender.get("capital") or -1)).fetchall()
    for t in sorted(towns, key=lambda t: math.dist((t["map_x"], t["map_y"]), (camp["map_x"], camp["map_y"]))):
        if find_route(conn, camp["id"], t["id"]):
            return get_city(conn, t["id"])
    return None


def horde_wars(conn, world_id, trade_routes, year):
    """Yearly: a strong horde may march to conquer a neighbour that pays it nothing. Returns the campaigns started."""
    from Data.Government.horde import hordes, riders
    from Data.Government.diplomacy import war_candidates
    from Data.Government.campaigns import start_campaign, active_campaign
    from Data.Database.repository import get_kingdom
    day = _today(conn, world_id)
    started = []
    for h in hordes(conn, world_id):
        if active_campaign(conn, h["id"]) or len(riders(conn, h["id"])) < CONQUEST_RIDERS:
            continue
        if random.random() >= CONQUEST_CHANCE:
            continue
        horde = get_kingdom(conn, h["id"])
        candidates = [get_kingdom(conn, k) for _, k in war_candidates(conn, horde, trade_routes, year)]
        candidates = [k for k in candidates if k and k.get("type") != "Horde" and k.get("status") != "Fallen"]
        camp = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (h["capital_city_id"],)).fetchone()

        def reach(k):
            town = nearest_town(conn, h, k)
            return math.dist((town["map_x"], town["map_y"]), (camp["map_x"], camp["map_y"])) if town else None
        scored = [(reach(k), k) for k in candidates]
        scored = [(d, k) for d, k in scored if d is not None]
        if not scored:
            continue
        victim = min(scored, key=lambda s: s[0])[1]
        campaign = start_campaign(conn, horde, victim, day)
        if campaign:
            mark_fought(conn, h["id"], day)
            started.append(campaign)
    conn.commit()
    return started


# ------------------------------------------------------------------ settling
def _end_danegeld(conn, horde_id, day):
    from Data.Database.repository import adjust_kingdom_favor
    for t in conn.execute("SELECT id, tributary_id FROM tributaries WHERE empire_id = ? AND status = 'Paying'", (horde_id,)).fetchall():
        conn.execute("UPDATE tributaries SET status = 'Ended', ended_day = ? WHERE id = ?", (day, t["id"]))
        adjust_kingdom_favor(conn, t["tributary_id"], horde_id, SETTLED_FAVOR)
        adjust_kingdom_favor(conn, horde_id, t["tributary_id"], SETTLED_FAVOR)


def _horse_village(conn, world_id, kingdom_id, seat):
    """Herders' families found a village on empty land near the seat. Returns its id, or None."""
    from Data.Cities.city_generator import generate_city
    from Data.Kingdoms.kingdom_generator import BIAS_RANGE
    from Data.Buildings.building_generator import CHAIN_CATEGORIES
    from Data.Infrastructure.roads import road_distance, travel_days_for
    from Data.Database.repository import create_road, get_city
    from Data.World.countryside import named_villagers, FAMILY_SIZE
    from Data.World.regions import assign_settlements_to_regions
    empty = [dict(r) for r in conn.execute(
        """SELECT * FROM regions WHERE world_id = ? AND id NOT IN (SELECT region_id FROM cities WHERE region_id IS NOT NULL)""",
        (world_id,))]
    near = [r for r in empty if math.dist((r["center_x"], r["center_y"]), (seat["map_x"], seat["map_y"])) <= VILLAGE_REACH]
    if not near:
        return None
    region = max(near, key=lambda r: (r["farmland"] or 0, -math.dist((r["center_x"], r["center_y"]), (seat["map_x"], seat["map_y"]))))
    households = random.randint(*VILLAGE_HOUSEHOLDS)
    named = named_villagers(households)
    biases = {category: random.uniform(*BIAS_RANGE) for category in CHAIN_CATEGORIES}
    village = generate_city(conn, kingdom_id, "Village", region["center_x"] + random.randint(-40, 40),
                            region["center_y"] + random.randint(-40, 40), biases, population=named)
    conn.execute("UPDATE cities SET households = ?, household_store = ? WHERE id = ?",
                 (max(0.0, households - named / FAMILY_SIZE), households * FAMILY_SIZE * 120, village["id"]))
    conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
                    ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                 (village["id"], named * 60))
    village = get_city(conn, village["id"])
    distance = road_distance(seat, village)
    create_road(conn, seat["id"], village["id"], "Capital Road", distance, travel_days_for(distance))
    assign_settlements_to_regions(conn, world_id)
    return village["id"]


def settle(conn, world_id, horde, how, seat_id=None):
    """
    The horde becomes a horse kingdom. how: 'conquest' (seat_id, a town it took,
    becomes its seat) or 'peace' (its camp becomes the seat). Returns the kingdom.
    """
    from Data.Database.repository import get_kingdom, get_city, get_npc, save_npc, create_event
    from Data.Government.succession import monarch_title
    from Data.Government.horde import _chronicle
    day = _today(conn, world_id)
    camp_id = horde["capital_city_id"]
    seat_id = seat_id or camp_id
    conn.execute("UPDATE kingdoms SET type = 'Horse Kingdom' WHERE id = ?", (horde["id"],))
    if seat_id != camp_id:
        conn.execute("UPDATE cities SET type = 'City' WHERE id = ?", (camp_id,))               # a horse town, fixed now
        conn.execute("UPDATE cities SET type = 'Capital' WHERE id = ?", (seat_id,))
        conn.execute("UPDATE kingdoms SET capital_city_id = ? WHERE id = ?", (seat_id, horde["id"]))
    ruler = get_npc(conn, horde["ruler_id"]) if horde.get("ruler_id") else None
    if ruler and ruler["alive"]:
        ruler["title"] = monarch_title(conn, horde["id"], ruler["gender"])
        save_npc(conn, ruler)
        conn.execute("UPDATE cities SET ruler_id = NULL WHERE id = ?", (camp_id,))
        conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (ruler["id"], seat_id))
        if seat_id != camp_id:
            from Data.Infrastructure.migration import relocate_npc
            relocate_npc(conn, ruler["id"], seat_id)
            ruler = get_npc(conn, ruler["id"])
            ruler["title"] = monarch_title(conn, horde["id"], ruler["gender"])
            save_npc(conn, ruler)
    _end_danegeld(conn, horde["id"], day)
    seat = get_city(conn, seat_id)
    villages = []
    if how == "peace":
        for _ in range(HORSE_VILLAGES):
            v = _horse_village(conn, world_id, horde["id"], seat)
            if v:
                villages.append(v)
    kingdom = get_kingdom(conn, horde["id"])
    who = f"{ruler['title']} {ruler['name']}" if ruler and ruler["alive"] else "its lord"
    if how == "conquest":
        text = f"{kingdom['name']} rides no more: {who} made {seat['name']} the seat of a kingdom of horse lords."
    else:
        text = f"After long years of peace, {who} stopped wandering: {kingdom['name']} settled at {seat['name']} as a kingdom of horse lords."
    create_event(conn, "Horse Kingdom", text, city_id=seat_id, kingdom_id=horde["id"])
    _chronicle(conn, world_id, horde["id"], "Founding", f"Horde Settled ({how})",
               {"kingdom": kingdom["name"], "ruler": who, "city": seat["name"]}, (horde["id"], seat_id))
    conn.commit()
    return kingdom


def settle_hordes(conn, world_id):
    """Yearly: a horde that has held a town settles there; one long at peace may settle where it stands. Returns [(horde id, how)]."""
    from Data.Government.horde import hordes
    day = _today(conn, world_id)
    settled = []
    for h in hordes(conn, world_id):
        conn.execute("UPDATE kingdoms SET founded_day = ? WHERE id = ? AND founded_day IS NULL", (day, h["id"]))
        held = conn.execute(
            """SELECT c.id FROM cities c WHERE c.kingdom_id = ? AND c.id != ? AND c.type != 'Village'
               AND NOT EXISTS (SELECT 1 FROM occupations o WHERE o.city_id = c.id AND o.status = 'Occupied')
               ORDER BY c.population DESC LIMIT 1""", (h["id"], h["capital_city_id"])).fetchone()
        if held:
            settle(conn, world_id, h, "conquest", held["id"])
            settled.append((h["id"], "conquest"))
            continue
        row = conn.execute("SELECT founded_day, last_fought_day FROM kingdoms WHERE id = ?", (h["id"],)).fetchone()
        founded = row["founded_day"] if row["founded_day"] is not None else day
        quiet_since = max(founded, row["last_fought_day"] if row["last_fought_day"] is not None else founded)
        if day - quiet_since >= PEACE_YEARS * 360 and random.random() < PEACE_SETTLE_CHANCE:
            settle(conn, world_id, h, "peace")
            settled.append((h["id"], "peace"))
    conn.commit()
    return settled


# ------------------------------------------------------------------ herds and oaths
def herd_towns(conn, world_id):
    """A horse kingdom's towns on the steppe, whose herds still feed them: [city ids]."""
    return [r[0] for r in conn.execute(
        """SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id JOIN regions r ON r.id = c.region_id
           WHERE k.world_id = ? AND k.type = 'Horse Kingdom' AND k.status != 'Fallen' AND r.terrain = 'Steppe' AND c.type != 'Village'""",
        (world_id,))]


def swear_oaths(conn, world_id):
    """Yearly: horse kingdoms and their neighbours at peace warm to each other. Returns the pairs warmed."""
    from Data.Database.repository import adjust_kingdom_favor
    from Data.Government.campaigns import active_campaign
    warmed = []
    for hk in horse_kingdoms(conn, world_id):
        seat = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (hk["capital_city_id"],)).fetchone()
        if not seat:
            continue
        for other in conn.execute(
                """SELECT k.id, c.map_x, c.map_y FROM kingdoms k JOIN cities c ON c.id = k.capital_city_id
                   WHERE k.world_id = ? AND k.id != ? AND k.status != 'Fallen' AND k.type != 'Horde'""",
                (world_id, hk["id"])).fetchall():
            if math.dist((other["map_x"], other["map_y"]), (seat["map_x"], seat["map_y"])) > FRIEND_REACH:
                continue
            at_war = conn.execute(
                """SELECT 1 FROM campaigns WHERE status != 'Over' AND ((attacker_kingdom_id = ? AND defender_kingdom_id = ?)
                   OR (attacker_kingdom_id = ? AND defender_kingdom_id = ?))""", (hk["id"], other["id"], other["id"], hk["id"])).fetchone()
            if at_war:
                continue
            adjust_kingdom_favor(conn, hk["id"], other["id"], OATH_WARMTH)
            adjust_kingdom_favor(conn, other["id"], hk["id"], OATH_WARMTH)
            warmed.append((hk["id"], other["id"]))
    conn.commit()
    return warmed


def horse_kingdoms_year(conn, world_id, trade_routes, year):
    """Yearly: hordes march to conquer, hordes settle, horse kingdoms swear oaths."""
    return {"wars": horde_wars(conn, world_id, trade_routes, year),
            "settled": settle_hordes(conn, world_id),
            "oaths": swear_oaths(conn, world_id)}


def riders_answer(conn, defender, target, attacker_id, day=None):
    """
    An allied town is attacked: every horse kingdom allied to the defender, within
    AID_REACH, sends ANSWER_SHARE of its free riders. Returns [npc dicts] to fight beside the defenders.
    """
    from Data.Government.diplomacy import get_allied_kingdom_ids
    from Data.Government.armies import get_army_npc_ids
    from Data.Government.military import MILITARY_PROFESSIONS
    from Data.Database.repository import get_npc, get_kingdom, create_event, adjust_kingdom_favor
    from Data.Government.horde import _chronicle
    world_id = defender["world_id"]
    away = set(get_army_npc_ids(conn, world_id))
    aid = []
    for ally_id in get_allied_kingdom_ids(conn, defender["id"]):
        ally = get_kingdom(conn, ally_id)
        if not ally or ally.get("type") != "Horse Kingdom" or ally.get("status") == "Fallen" or ally_id == attacker_id:
            continue
        from Data.Government.campaigns import capital_of
        seat = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (capital_of(ally),)).fetchone()
        if not seat or math.dist((seat["map_x"], seat["map_y"]), (target["map_x"], target["map_y"])) > AID_REACH:
            continue
        marks = ",".join("?" * len(MILITARY_PROFESSIONS))
        free = [r[0] for r in conn.execute(
            f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
                WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (ally_id, *MILITARY_PROFESSIONS))
            if r[0] not in away]
        band = [get_npc(conn, i) for i in free[:round(len(free) * ANSWER_SHARE)]]
        if not band:
            continue
        aid.extend(band)
        adjust_kingdom_favor(conn, defender["id"], ally_id, AID_FAVOR)
        create_event(conn, "Riders Answered", f"The riders of {ally['name']} answered the call: {len(band)} rode to the aid of "
                                               f"{defender['name']} at {target['name']}.", city_id=target["id"], kingdom_id=ally_id)
        _chronicle(conn, world_id, ally_id, "Alliance", "Riders Answered",
                   {"kingdom": ally["name"], "other": defender["name"], "city": target["name"], "riders": len(band)},
                   (ally_id, target["id"], day or 0))
        _chronicle(conn, world_id, defender["id"], "Alliance", "Riders Came",
                   {"kingdom": defender["name"], "other": ally["name"], "city": target["name"], "riders": len(band)},
                   (defender["id"], target["id"], day or 0))
    return aid