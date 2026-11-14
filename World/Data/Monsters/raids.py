"""
Monster raids. Each month a raider lair (intelligent, evil: orcs, gnolls,
giants, evil dragons -- Data.Monsters.bestiary) may send out a war band of
half its number against the nearest village within RAID_REACH.

The village defends itself with its named adults, each fighting by their
trade (Data.Combat.melee), and a militia of one man per household. The fight
is by the book. Then:

    raiders win    defenders die, the village loses FOOD_TAKEN of its Food,
                   and its households shrink by the militia who fell
    village wins   the war band breaks, its dead lost to the lair
    either way     the lair loses its dead -- with none left, it is cleared;
                   named people who die are mourned like any other death
"""

from __future__ import annotations

import math
import random

from Data.Combat import melee
from Data.Monsters.bestiary import by_name

RAID_CHANCE = 0.12               # each raider lair, each month
RAID_REACH = 700                 # the furthest a war band goes, in map units
BAND_SHARE = 0.5                 # the share of a lair that goes raiding (the rest guard it)
MIN_BAND = 3
NAMED_DEFENDERS = 30             # at most this many of the village's named adults stand
MAX_MILITIA = 40                 # ...with one man per household, up to this many
FOOD_TAKEN = 0.25                # of the village's Food, if the raiders win
FAMILY_SIZE = 5                  # people per household (Data.World.countryside)
CHRONICLED = 10                  # a raid that kills this many is written into the kingdom's chronicle


def plural(name, n=2):
    """'Orc' -> 'Orcs', 'Wolf' -> 'Wolves', 'Fire Giant' -> 'Fire Giants'; names with a comma or bracket
    ('Beetle, Giant, Fire') become 'of the Beetle, Giant, Fire'. One stays as it is."""
    if n == 1:
        return name
    if "," in name or "(" in name:
        return f"creatures of the kind {name}"
    for end, repl in (("man", "men"), ("fe", "ves"), ("f", "ves"), ("s", "ses"), ("x", "xes"), ("y", "ies")):
        tail = name[-len(end):]
        if tail.lower() == end and not (end == "y" and name[-2:-1] in "aeiou"):
            return name[: -len(end)] + (repl.capitalize() if tail[:1].isupper() else repl)   # 'Lizard Man' -> 'Lizard Men'
    return name + "s"


def count_of(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {plural(word, n)}"


def nearest_village(conn, world_id, lair):
    best = None
    for v in conn.execute(
            """SELECT c.id, c.name, c.kingdom_id, c.map_x, c.map_y, c.households FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND c.type = 'Village'""", (world_id,)):
        d = math.dist((v["map_x"], v["map_y"]), (lair["map_x"], lair["map_y"]))
        if d <= RAID_REACH and (best is None or d < best[0]):
            best = (d, dict(v))
    return best[1] if best else None


def defenders_of(conn, village):
    """The village's named adults (by their trades) and its household militia (commoners)."""
    named = [dict(r) for r in conn.execute(
        "SELECT * FROM npcs WHERE city_id = ? AND alive = 1 AND age >= 16 ORDER BY RANDOM() LIMIT ?", (village["id"], NAMED_DEFENDERS))]
    fighters = []
    for npc in named:
        skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc["id"],)).fetchall())
        fighters.append(melee.person_fighter(npc, side="village", skills=skills))
    militia = min(MAX_MILITIA, int(village["households"] or 0))
    fighters += [melee.Fighter("militia", "village", melee.HP_COMMONER, melee.COMMONER[1], melee.person_thac0(0),
                               [melee.COMMONER[2]], ref=None) for _ in range(militia)]
    return fighters


def kill(conn, npc_id, city_id):
    """A named villager slain in a raid: dead, recorded, and mourned."""
    from Data.Database.repository import get_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (npc_id,))
    npc = get_npc(conn, npc_id)
    death_event(conn, npc, "Monster raid", city_id=city_id)
    apply_grief(conn, npc)


def raid(conn, world_id, lair, village):
    """One raid, fought out. Returns its report."""
    from Data.Database.repository import create_event
    monster = by_name(lair["monster"])
    band = min(lair["count"], max(MIN_BAND, round(lair["count"] * BAND_SHARE)))
    raiders = melee.monster_fighters(monster, band, lair["hit_points_each"], side="raiders", ref=lair["id"])
    defenders = defenders_of(conn, village)
    if not defenders:
        result = {"winner": "a", "dead_a": [], "dead_b": [], "rounds": 0}
    else:
        result = melee.battle(raiders, defenders)
    raiders_won = result["winner"] == "a"

    # the lair loses its dead
    fallen = len(result["dead_a"])
    left = lair["count"] - fallen
    conn.execute("UPDATE monster_lairs SET count = ?, status = ? WHERE id = ?",
                 (max(0, left), "Cleared" if left <= 0 else "Active", lair["id"]))

    # the village: its named dead, its militia's dead, its Food
    named_dead = [f.ref for f in result["dead_b"] if f.ref is not None]
    militia_dead = sum(1 for f in result["dead_b"] if f.ref is None)
    for npc_id in named_dead:
        kill(conn, npc_id, village["id"])
    if militia_dead:
        conn.execute("UPDATE cities SET households = MAX(0, households - ?) WHERE id = ?", (militia_dead / FAMILY_SIZE, village["id"]))
    taken = 0.0
    if raiders_won:
        food = conn.execute("SELECT amount FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (village["id"],)).fetchone()
        taken = round((food[0] if food else 0) * FOOD_TAKEN, 1)
        conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Food'", (taken, village["id"]))

    dead = len(named_dead) + militia_dead
    band_text = count_of(band, monster["name"])
    if raiders_won:
        text = (f"{band_text} from a lair in the wilds raided {village['name']}: {count_of(dead, 'villager')} killed "
                f"and {taken:,.0f} Food carried off; {fallen} of the raiders fell.")
    else:
        text = f"{band_text} raided {village['name']} and were driven off: {fallen} of the raiders fell, and {count_of(dead, 'villager')}."
    if left <= 0:
        text += f" The lair of the {plural(monster['name'])} is no more."
    event_id = create_event(conn, "Monster Raid", text, city_id=village["id"], kingdom_id=village["kingdom_id"])
    if dead >= CHRONICLED:                      # a great raid is written into the kingdom's chronicle
        from Data.History.chronicle import write_entry, current_date
        year, season = current_date(conn, world_id)
        kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (village["kingdom_id"],)).fetchone()[0]
        write_entry(conn, village["kingdom_id"], "Monster Raid", "Monster Raid (sacked)" if raiders_won else "Monster Raid (repelled)",
                    year, season, {"kingdom": kingdom, "village": village["name"], "monsters": plural(monster["name"]).lower()
                                   if "," not in monster["name"] else plural(monster["name"]), "dead": dead},
                    source_event_id=event_id if isinstance(event_id, int) else None, seed_parts=(lair["id"],))
    conn.commit()
    return {"lair": lair["id"], "monster": monster["name"], "village": village["name"], "band": band, "raiders_won": raiders_won,
            "raiders_fallen": fallen, "named_dead": len(named_dead), "militia_dead": militia_dead, "food_taken": taken,
            "cleared": left <= 0, "text": text}


def monster_raids(conn, world_id):
    """Monthly: each raider lair may raid the nearest village in reach. Returns the raids' reports."""
    reports = []
    lairs = [dict(r) for r in conn.execute(
        "SELECT * FROM monster_lairs WHERE world_id = ? AND status = 'Active' AND kind = 'raider' AND count > 0", (world_id,))]
    for lair in lairs:
        if random.random() >= RAID_CHANCE or by_name(lair["monster"]) is None:
            continue
        village = nearest_village(conn, world_id, lair)
        if village:
            reports.append(raid(conn, world_id, lair, village))
    return reports