"""
Manage saved worlds (Data.Infrastructure.saves) and seeds (Data.Infrastructure.seeds).

    python saves.py list                 every save, newest first
    python saves.py save <name>          save the current world as <name>
    python saves.py load <name>          load a save (the current world is kept as "before-load")
    python saves.py delete <name>        delete a save
    python saves.py new [seed]           a new world (the current one is kept as "before-new");
                                         the seed can be a number or any word -- the same seed
                                         always makes the same world
    python saves.py seed                 the current world's seed

Stop run_world.py before loading a save or making a new world.
"""

import sys

from Data.Infrastructure import saves
from Data.Infrastructure.seeds import parse_seed


def main(args):
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command, rest = args[0].lower(), args[1:]

    try:
        if command == "list":
            found = saves.list_saves()
            if not found:
                print("No saves yet. Save the current world with:  python saves.py save <name>")
            for summary in found:
                print("  " + saves.describe(summary))

        elif command == "save":
            if not rest:
                print("Give the save a name:  python saves.py save <name>")
                return 1
            summary = saves.save_world(" ".join(rest))
            print("Saved:  " + saves.describe(summary))

        elif command == "load":
            if not rest:
                print("Which save?  python saves.py load <name>   (see: python saves.py list)")
                return 1
            summary = saves.load_save(" ".join(rest))
            print("Loaded: " + saves.describe(summary))
            print("(the world you had is kept as 'before-load')")

        elif command == "delete":
            if not rest:
                print("Which save?  python saves.py delete <name>")
                return 1
            summary = saves.delete_save(" ".join(rest))
            print(f"Deleted '{summary['name']}'.")

        elif command == "new":
            seed = parse_seed(" ".join(rest)) if rest else None
            print("Generating a new world -- this takes a little while ...")
            world = saves.new_world(seed)
            print(f"New world generated from seed {world['seed']}. (Any world you had is kept as 'before-new'.)")

        elif command == "seed":
            from Data.Database.database import get_connection
            from Data.Infrastructure.seeds import world_seed
            conn = get_connection()
            row = conn.execute("SELECT id, name FROM worlds ORDER BY id LIMIT 1").fetchone()
            if row is None:
                print("There is no world yet.")
            else:
                seed = world_seed(conn, row["id"])
                print(f"'{row['name']}' was generated from seed {seed}." if seed is not None
                      else f"'{row['name']}' was made before seeds were recorded.")

        else:
            print(f"Unknown command '{command}'.\n{__doc__}")
            return 1

    except (FileNotFoundError, ValueError, PermissionError) as problem:
        print(problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))