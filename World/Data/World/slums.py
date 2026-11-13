import math
import random

# Refugees and the slums. Refugees who reach a town with no house free are
# not turned away:
#
#   * each man of fighting age among them has a BANDIT_CHANCE of going to the
#     nearest Bandit Camp of the kingdom instead;
#   * everyone else lives in the town's Slums (made the first time anyone
#     needs them) and works as a day laborer;
#   * each month, a slum family that can afford a house -- HOUSING_DEPOSIT a
#     grown member, paid into the town's purse -- moves into one with room
#     for them all; and public works counts slum dwellers as people needing a
#     roof (Data.Buildings.upgrades), so the town grows to hold them.

SLUMS = "Slums"
BANDIT_CHANCE = 0.25
FIGHTING_AGE = (16, 40)
HOUSING_DEPOSIT = 30
ADULT_AGE = 16


def slum_ids(conn, city_id=None):
    """The Slums buildings (of one town, or everywhere)."""
    if city_id is None:
        return {r[0] for r in conn.execute("SELECT id FROM buildings WHERE type = ?", (SLUMS,))}
    return {r[0] for r in conn.execute("SELECT id FROM buildings WHERE type = ? AND city_id = ?", (SLUMS, city_id))}


def slums_of(conn, city_id):
    """The town's Slums, made the first time anyone needs them."""
    existing = slum_ids(conn, city_id)
    if existing:
        return min(existing)
    from Data.Buildings.upgrades import construct
    building = construct(conn, city_id, SLUMS)
    town = conn.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone()["name"]
    conn.execute("UPDATE buildings SET name = ? WHERE id = ?", (f"Slums of {town}", building))
    return building


def _nearest_camp(conn, city_id):
    town = conn.execute("SELECT kingdom_id, map_x, map_y FROM cities WHERE id = ?", (city_id,)).fetchone()
    camps = conn.execute(
        """SELECT b.id, c.id city_id, c.map_x, c.map_y FROM buildings b JOIN cities c ON c.id = b.city_id
           WHERE b.type = 'Bandit Camp' AND c.kingdom_id = ?""", (town["kingdom_id"],)).fetchall()
    if not camps:
        return None
    return min(camps, key=lambda c: math.dist((c["map_x"], c["map_y"]), (town["map_x"], town["map_y"])))


def shelter(conn, family, town_id):
    """Refugees with no house in town: men of fighting age may turn bandit; the rest live in the Slums as
    day laborers. `family`: their NPC dicts. Returns (to the slums, to banditry)."""
    from Data.Database.repository import save_npc, create_event
    from Data.Infrastructure.migration import relocate_npc
    slums, camp = None, _nearest_camp(conn, town_id)
    housed, bandits = 0, 0
    for npc in family:
        if (camp and npc.get("gender") == "Male" and FIGHTING_AGE[0] <= npc["age"] <= FIGHTING_AGE[1]
                and random.random() < BANDIT_CHANCE):
            relocate_npc(conn, npc["id"], camp["city_id"])
            room = conn.execute("SELECT b.capacity - (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) FROM buildings b WHERE b.id = ?",
                                (camp["id"],)).fetchone()[0]
            conn.execute("UPDATE npcs SET profession = 'Bandit', workplace_id = ? WHERE id = ?", (camp["id"] if room > 0 else None, npc["id"]))
            bandits += 1
            continue
        slums = slums or slums_of(conn, town_id)
        npc["home"] = slums
        if npc["age"] >= ADULT_AGE and npc["profession"] != "Child" and not npc.get("workplace"):
            npc["profession"] = "Laborer"
        save_npc(conn, npc)
        housed += 1
    if bandits:
        town = conn.execute("SELECT name FROM cities WHERE id = ?", (town_id,)).fetchone()["name"]
        create_event(conn, "Turned Bandit", f"Finding no roof in {town}, {bandits} refugee(s) took to banditry.", city_id=town_id)
    return housed, bandits


def rehouse(conn, world_id):
    """Monthly: slum families that can afford it move into a house with room for them all. Returns how many people moved."""
    moved = 0
    for city in conn.execute(
            "SELECT DISTINCT b.city_id FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id "
            "WHERE b.type = ? AND k.world_id = ?", (SLUMS, world_id)).fetchall():
        slum = slums_of(conn, city["city_id"])
        dwellers = [dict(r) for r in conn.execute(
            "SELECT id, age, gold, spouse_id, family_id FROM npcs WHERE home_id = ? AND alive = 1 ORDER BY id", (slum,))]
        families = {}
        for d in dwellers:                         # a household: the people of one family in the slums
            families.setdefault(d["family_id"] or -d["id"], []).append(d)
        for members in families.values():
            adults = [m for m in members if m["age"] >= ADULT_AGE]
            deposit = HOUSING_DEPOSIT * max(1, len(adults))
            purse = sum(max(0, m["gold"] or 0) for m in adults)
            if purse < deposit:
                continue
            house = conn.execute(
                """SELECT b.id FROM buildings b WHERE b.city_id = ? AND b.type = 'House'
                   AND b.capacity - (SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1) >= ? ORDER BY b.id LIMIT 1""",
                (city["city_id"], len(members))).fetchone()
            if house is None:
                break                              # no house with room: they wait for the town to grow
            for m in adults:                       # each grown member pays their share of the deposit
                share = deposit * max(0, m["gold"] or 0) / purse
                conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (round(share, 2), m["id"]))
            conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (deposit, city["city_id"]))
            for m in members:
                conn.execute("UPDATE npcs SET home_id = ? WHERE id = ?", (house["id"], m["id"]))
            moved += len(members)
    conn.commit()
    return moved