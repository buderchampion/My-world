"""
See the land: a text map of the world's regions and settlements, then a list
of every region with its terrain, climate, river, farmland and settlements.

    python read_regions.py                 the map and the list
    python read_regions.py --map           just the map
    python read_regions.py --db other.db   read a different world database file

Read-only, and safe to run while run_world.py is running.
"""

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import Data.Database.database as database_module
from Data.Kingdoms.kingdom_generator import MAP_WIDTH, MAP_HEIGHT

SYMBOLS = {"Mountains": "^", "Hills": "n", "Forest": "T", "Marsh": "%", "Plains": ".", "Steppe": ","}
SETTLEMENTS = {"Capital": "@", "City": "O", "Town": "o", "Village": "*"}
COLUMNS, ROWS = 80, 32


def main():

    parser = argparse.ArgumentParser(description="See the world's regions.")
    parser.add_argument("--map", action="store_true", help="show only the map")
    parser.add_argument("--db", help="path to a world database file (default: the usual world.db)")
    args = parser.parse_args()

    if args.db:
        database_module.DB_FILE = Path(args.db)

    if not Path(database_module.DB_FILE).exists():
        print(f"No world database found at {database_module.DB_FILE}. Run the world first.")
        return

    conn = database_module.get_connection()
    world = conn.execute("SELECT id FROM worlds ORDER BY id DESC LIMIT 1").fetchone()
    regions = [dict(r) for r in conn.execute("SELECT * FROM regions WHERE world_id = ? ORDER BY id", (world["id"],))]

    if not regions:
        print("This world has no regions yet -- start it once with run_world.py and they will be created.")
        return

    cities = [dict(c) for c in conn.execute(
        """SELECT c.name, c.type, c.map_x, c.map_y, c.region_id, k.name kingdom
           FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?""", (world["id"],))]

    # --- the map: each character shows the land of the nearest region
    grid = []
    for row in range(ROWS):
        line = []
        for col in range(COLUMNS):
            x, y = (col + 0.5) * MAP_WIDTH / COLUMNS, (row + 0.5) * MAP_HEIGHT / ROWS
            nearest = min(regions, key=lambda r: (r["center_x"] - x) ** 2 + (r["center_y"] - y) ** 2)
            line.append(SYMBOLS.get(nearest["terrain"], "?"))
        grid.append(line)

    def cell(x, y):
        return min(ROWS - 1, int(y * ROWS / MAP_HEIGHT)), min(COLUMNS - 1, int(x * COLUMNS / MAP_WIDTH))

    for region in regions:                       # a river runs through the heart of its region
        if region["has_river"]:
            r, c = cell(region["center_x"], region["center_y"])
            grid[r][c] = "~"

    for city in sorted(cities, key=lambda c: c["type"] != "Village"):   # capitals and towns drawn last, on top
        r, c = cell(city["map_x"], city["map_y"])
        grid[r][c] = SETTLEMENTS.get(city["type"], "o")

    print("+" + "-" * COLUMNS + "+   north is up")
    for line in grid:
        print("|" + "".join(line) + "|")
    print("+" + "-" * COLUMNS + "+")
    print("  ^ mountains  n hills  T forest  % marsh  . plains  , steppe  ~ river")
    print("  @ capital  O city  o town  * village")

    if args.map:
        return

    # --- the list
    by_region = {}
    for city in cities:
        by_region.setdefault(city["region_id"], []).append(city)

    print()
    print(f"{'REGION':16} {'TERRAIN':10} {'CLIMATE':10} {'RIVER':6} {'FARMLAND':9} SETTLEMENTS")
    for region in sorted(regions, key=lambda r: -r["farmland"]):
        here = by_region.get(region["id"], [])
        names = ", ".join(f"{c['name']} ({c['type'][0]}, {c['kingdom']})" for c in here) or "-"
        print(f"{region['name']:16} {region['terrain']:10} {region['climate']:10} {'yes' if region['has_river'] else '':6} "
              f"{region['farmland']:<9} {names}")

    conn.close()


if __name__ == "__main__":
    main()