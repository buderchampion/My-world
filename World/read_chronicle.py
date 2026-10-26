"""
Read a kingdom's chronicle -- the history the world has written about itself.

    python read_chronicle.py                  every kingdom's chronicle
    python read_chronicle.py Stonedale        just that kingdom (any capitalization, or part of its name)
    python read_chronicle.py --db other.db    read a different world database file

Read-only, and safe to run while run_world.py is running.
"""

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import Data.Database.database as database_module
from Data.History.chronicle import get_chronicle


def main():

    parser = argparse.ArgumentParser(description="Read a kingdom's chronicle.")
    parser.add_argument("kingdom", nargs="?", help="a kingdom's name, or part of it (default: all kingdoms)")
    parser.add_argument("--db", help="path to a world database file (default: the usual world.db)")
    args = parser.parse_args()

    if args.db:
        database_module.DB_FILE = Path(args.db)

    if not Path(database_module.DB_FILE).exists():
        print(f"No world database found at {database_module.DB_FILE}. Run the world first.")
        return

    connection = database_module.get_connection()

    kingdoms = connection.execute("SELECT id, name FROM kingdoms ORDER BY id").fetchall()

    if args.kingdom:
        wanted = args.kingdom.lower()
        kingdoms = [k for k in kingdoms if wanted in k["name"].lower()]

        if not kingdoms:
            print(f"No kingdom matches '{args.kingdom}'.")
            return

    for kingdom in kingdoms:

        title = f"THE CHRONICLE OF {kingdom['name'].upper()}"

        print("=" * len(title))
        print(title)
        print("=" * len(title))
        print()

        entries = get_chronicle(connection, kingdom["id"])

        if not entries:
            print("  (nothing has yet been written)")
            print()
            continue

        for entry in entries:
            print(entry["text"])
            print()

    connection.close()


if __name__ == "__main__":
    main()