"""
Monster lairs. At generation each region of the world may hold a lair: most
in the neutral ground between kingdoms, a few -- weaker monsters only --
lurking within a kingdom's lands. A lair's monster lives where the bestiary
says its kind lives (terrain, and climate for creatures of the cold), chosen
by how common it is; its numbers are rolled from the book's "number
appearing", and each has the average hit points of its hit dice.
"""

from __future__ import annotations

import math
import random

from Data.Monsters.bestiary import of_this_world

LAIR_CHANCE = 0.5                # each region of the wilds holds a lair this often
WITHIN_CHANCE = 0.25             # ...and a region within a kingdom's lands, this often (about a dozen in a world)
WITHIN_MAX_HIT_DICE = 5          # what lurks within a kingdom is never stronger than this
TERRITORY_REACH = 180            # land within this of a kingdom's settlement is that kingdom's (as the map draws it)
CLEAR_OF_SETTLEMENTS = 90        # a lair keeps at least this far from any town or village
HIT_POINTS_PER_DIE = 4.5         # the average of a d8
LAIR_SPREAD = 90                 # how far from its region's centre a lair may lie
MAX_IN_LAIR = 150                # the largest band kept in one lair (the book's 30-300 orcs are a whole tribe's lands)


def hit_points(monster):
    stats = monster["stats"]
    return round((stats.get("hit_dice") or 1) * HIT_POINTS_PER_DIE + (stats.get("hit_dice_bonus") or 0), 1)


def roll_number(monster):
    low, high = monster["stats"].get("number_appearing") or [1, 1]
    return max(1, min(MAX_IN_LAIR, random.randint(min(low, high), max(low, high))))


def candidates(region, within_kingdom):
    """The monsters that could den in this region."""
    out = []
    for m in of_this_world():
        if region["terrain"] not in m["terrains"]:
            continue
        if (m["cold_only"] and region["climate"] != "Cold") or (m.get("warm_only") and region["climate"] != "Warm"):
            continue
        if within_kingdom and ((m["stats"].get("hit_dice") or 99) > WITHIN_MAX_HIT_DICE or m["kind"] == "peaceful"):
            continue
        out.append(m)
    return out


def place_lairs(conn, world_id):
    """Places the world's lairs. Returns how many."""
    settlements = conn.execute(
        """SELECT c.map_x, c.map_y, c.kingdom_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ?""", (world_id,)).fetchall()
    regions = conn.execute("SELECT * FROM regions WHERE world_id = ?", (world_id,)).fetchall()
    day = 0
    placed = 0
    for region in regions:
        for _ in range(8):                                     # a few tries to find ground clear of settlements
            x = region["center_x"] + random.uniform(-LAIR_SPREAD, LAIR_SPREAD)
            y = region["center_y"] + random.uniform(-LAIR_SPREAD, LAIR_SPREAD)
            nearest = min(settlements, key=lambda s: math.dist((s["map_x"], s["map_y"]), (x, y)), default=None)
            gap = math.dist((nearest["map_x"], nearest["map_y"]), (x, y)) if nearest else 10 ** 9
            if gap >= CLEAR_OF_SETTLEMENTS:
                break
        else:
            continue
        within = gap <= TERRITORY_REACH
        if random.random() >= (WITHIN_CHANCE if within else LAIR_CHANCE):
            continue
        pool = candidates(region, within)
        if not pool:
            continue
        monster = random.choices(pool, weights=[m["weight"] for m in pool])[0]
        conn.execute(
            """INSERT INTO monster_lairs (world_id, region_id, monster, kind, map_x, map_y, count, hit_points_each,
                                          within_kingdom_id, founded_day, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')""",
            (world_id, region["id"], monster["name"], monster["kind"], round(x, 1), round(y, 1), roll_number(monster),
             hit_points(monster), nearest["kingdom_id"] if within else None, day))
        placed += 1
    conn.commit()
    return placed


def ensure_lairs(conn, world_id):
    """Older worlds get their lairs when they start."""
    if conn.execute("SELECT 1 FROM monster_lairs WHERE world_id = ? LIMIT 1", (world_id,)).fetchone():
        return 0
    return place_lairs(conn, world_id)