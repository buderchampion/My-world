"""
The world report: how the realms stand, on one page.

    python world_report.py                  every kingdom
    python world_report.py Stonedale        just the kingdoms whose name contains this
    python world_report.py --db other.db    read a different world database file

Kingdoms (type, people, monarch, capital, size, treasury, lords' loyalty,
discoveries), the favor matrix (how each realm feels about each other),
alliances and treaties, tribute and danegeld, wars under way, and the
dragons of the world with their hoards.

Read-only, and safe to run while run_world.py is running.
"""

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import Data.Database.database as database_module


def rows(conn, sql, args=()):
    """A query's rows -- none if the world is older than the table it reads (a world made before dragons, say)."""
    import sqlite3
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def heading(text):
    print()
    print(text)
    print("-" * len(text))


def short(name, width=6):
    return name[:width]


def kingdoms_section(conn, kingdoms):
    heading("Kingdoms")
    for k in kingdoms:
        if k["status"] == "Fallen":
            print(f"  {k['name']} ({k['race']}): fallen")
            continue
        ruler = conn.execute("SELECT name, title, epithet, age FROM npcs WHERE id = ?", (k["ruler_id"],)).fetchone() if k["ruler_id"] else None
        who = f"{ruler['title'] or ''} {ruler['name']}{' ' + ruler['epithet'] if ruler['epithet'] else ''} ({ruler['age']})".strip() \
            if ruler else "no ruler"
        capital = conn.execute("SELECT name FROM cities WHERE id = ?", (k["capital_city_id"],)).fetchone() if k["capital_city_id"] else None
        places, named = conn.execute(
            """SELECT COUNT(DISTINCT c.id), (SELECT COUNT(*) FROM npcs n JOIN cities c2 ON c2.id = n.city_id
                                              WHERE c2.kingdom_id = ? AND n.alive = 1)
               FROM cities c WHERE c.kingdom_id = ?""", (k["id"], k["id"])).fetchone()
        loyalty = conn.execute("SELECT AVG(loyalty), COUNT(*) FROM provinces WHERE kingdom_id = ?", (k["id"],)).fetchone()
        known = [r[0] for r in rows(conn, "SELECT discovery FROM kingdom_discoveries WHERE kingdom_id = ? ORDER BY day", (k["id"],))]
        print(f"  {k['name']} -- {k['type']} of the {k['race']} people, at {capital[0] if capital else '?'}")
        print(f"      {who}")
        print(f"      {places} places, {named:,} named people, treasury {k['treasury'] or 0:,.0f} gold, prices x{k['price_level'] or 1:.2f}")
        if loyalty[1]:
            print(f"      {loyalty[1]} counties, lords' loyalty {loyalty[0]:.0f} on average")
        print(f"      discoveries: {', '.join(known) if known else 'none yet'}")


def favor_section(conn, kingdoms):
    living = [k for k in kingdoms if k["status"] != "Fallen"]
    if len(living) < 2:
        return
    heading("Favor (row's feeling toward column, -100 to 100)")
    favor = {(r[0], r[1]): r[2] for r in conn.execute("SELECT from_kingdom_id, to_kingdom_id, favor FROM kingdom_favor")}
    print("  " + " " * 8 + "".join(f"{short(k['name']):>8}" for k in living))
    for a in living:
        cells = "".join(f"{'--' if a['id'] == b['id'] else round(favor.get((a['id'], b['id']), 0)):>8}" for b in living)
        print(f"  {short(a['name'], 8):<8}{cells}")


def relations_section(conn, ids, year):
    heading("Alliances and treaties")
    pairs = rows(conn,
        """SELECT a.name, b.name, r.status, r.peace_until_year FROM kingdom_relations r
           JOIN kingdoms a ON a.id = r.kingdom_a_id JOIN kingdoms b ON b.id = r.kingdom_b_id
           WHERE r.status != 'Neutral' AND a.status != 'Fallen' AND b.status != 'Fallen' AND a.id < b.id""")
    pairs = [r for r in pairs if any(name in ids for name in (r[0], r[1]))
             and not (r[2] == "Peace Treaty" and (r[3] or 0) <= year)]          # a treaty whose years have run out binds no one
    if not pairs:
        print("  none")
    for a, b, status, until in pairs:
        print(f"  {a} and {b}: {status}" + (f" (peace until year {until})" if until else ""))


def tribute_section(conn, ids):
    heading("Tribute and danegeld")
    paying = rows(conn,
        """SELECT e.name, t.name, x.how, x.tribute_gold, x.missed FROM tributaries x
           JOIN kingdoms e ON e.id = x.empire_id JOIN kingdoms t ON t.id = x.tributary_id WHERE x.status = 'Paying'""")
    paying = [r for r in paying if r[0] in ids or r[1] in ids]
    if not paying:
        print("  none")
    for empire, payer, how, gold, missed in paying:
        kind = "danegeld" if how == "danegeld" else "tribute"
        print(f"  {payer} pays {empire} {kind} of {gold:,.0f} gold a year" + (f" ({missed} year(s) behind)" if missed else ""))


def wars_section(conn, ids):
    heading("Wars under way")
    wars = rows(conn,
        """SELECT a.name, d.name, c.name, w.status FROM campaigns w JOIN kingdoms a ON a.id = w.attacker_kingdom_id
           JOIN kingdoms d ON d.id = w.defender_kingdom_id JOIN cities c ON c.id = w.target_city_id
           WHERE w.status != 'Over'""")
    wars = [r for r in wars if r[0] in ids or r[1] in ids]
    if not wars:
        print("  none")
    for attacker, defender, town, status in wars:
        print(f"  {attacker} against {defender} at {town}: {status}")


def dragons_section(conn):
    heading("Dragons")
    living = rows(conn,
        """SELECT d.name, d.monster, d.good, d.hoard, d.attacks, n.name AS guise, c.name AS town FROM dragons d
           LEFT JOIN npcs n ON n.id = d.guise_npc_id AND n.alive = 1 LEFT JOIN cities c ON c.id = n.city_id
           WHERE d.status = 'Alive' ORDER BY d.good, d.hoard DESC""")
    if not living:
        print("  none known")
    from Data.Monsters.dragons import KINDS
    for name, monster, good, hoard, attacks, guise, town in living:
        colour = KINDS.get(monster, {}).get("colour", monster)
        line = f"  {name} the {colour} ({'good' if good else 'evil'}): hoard {hoard:,.0f} gold"
        if attacks:
            line += f", has burned {attacks} town(s)"
        if guise:
            line += f"; lives in {town} as {guise}"
        print(line)


def main():
    parser = argparse.ArgumentParser(description="How the realms stand, on one page.")
    parser.add_argument("kingdom", nargs="?", help="a kingdom's name, or part of it (default: all kingdoms)")
    parser.add_argument("--db", help="path to a world database file (default: the usual world.db)")
    args = parser.parse_args()

    if args.db:
        database_module.DB_FILE = Path(args.db)
    if not Path(database_module.DB_FILE).exists():
        print(f"No world database found at {database_module.DB_FILE}. Run the world first.")
        return

    conn = database_module.get_connection()
    world = conn.execute("SELECT id, clock_year, clock_month, clock_day FROM worlds ORDER BY id DESC LIMIT 1").fetchone()
    kingdoms = [dict(k) for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? ORDER BY id", (world["id"],))]
    if args.kingdom:
        kingdoms = [k for k in kingdoms if args.kingdom.lower() in k["name"].lower()]
        if not kingdoms:
            print(f"No kingdom matches '{args.kingdom}'.")
            return
    ids = {k["name"] for k in kingdoms}

    title = f"THE WORLD IN YEAR {world['clock_year']}, MONTH {world['clock_month']}, DAY {world['clock_day']}"
    print("=" * len(title))
    print(title)
    print("=" * len(title))
    kingdoms_section(conn, kingdoms)
    favor_section(conn, kingdoms)
    relations_section(conn, ids, world["clock_year"])
    tribute_section(conn, ids)
    wars_section(conn, ids)
    if not args.kingdom:
        dragons_section(conn)


if __name__ == "__main__":
    main()