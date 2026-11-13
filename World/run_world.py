"""
Starts (or resumes) the living simulation. Separate from main.py on
purpose: main.py generates a fresh world and exits; this runs the
clock on whatever world already exists, continuing from wherever it
left off last time you ran it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from Data.Database.database import init_database, get_connection
from Data.World.world_generator import generate_world
from Data.Simulation.simulation_manager import run_realtime
from Data.Infrastructure.roads import generate_roads
from Data.Kingdoms.kingdom_generator import backfill_city_positions
from Data.Buildings.building_generator import ensure_production_chain_buildings
from Data.Cities.districts import ensure_districts_for_city
from Data.Database.repository import get_world_kingdoms, get_kingdom_cities

# 1.0 = 1 real second passes per simulated minute (a full simulated day
# takes ~24 real minutes). Raise this to run time faster.
SIM_MINUTES_PER_REAL_SECOND = 1.0


def get_or_create_world():

    connection = get_connection()
    row = connection.execute(
        "SELECT id, name FROM worlds ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if row:
        world = dict(row)
        print(f"Resuming existing world: {world['name']} (id {world['id']})")

        # Positions must exist before roads can compute meaningful
        # distances -- always runs first, and is a no-op if this world
        # already has real positions.
        if backfill_city_positions(connection, world["id"]):
            print(
                "  (this world predates city positions -- assigned kingdom-clustered positions retroactively)"
            )

        created = generate_roads(connection, world["id"])
        if created:
            print(
                f"  (backfilled {len(created)} roads onto this world -- it predates the road network)"
            )

        # Regions: a world made before they existed gets its land now.
        from Data.World.regions import ensure_regions
        regions_created = ensure_regions(connection, world["id"])
        if regions_created:
            print(f"  (gave this world {regions_created} regions -- it predates regions)")

        # Households: a world made before them gets peasant households in its villages.
        from Data.World.countryside import ensure_households
        villages_given = ensure_households(connection, world["id"])
        if villages_given:
            print(f"  (gave {villages_given} villages peasant households -- this world predates them)")

        from Data.Government.provinces import ensure_provinces
        provinces_made = ensure_provinces(connection, world["id"])
        if provinces_made:
            print(f"  (divided this world into {provinces_made} counties -- it predates provinces)")

        from Data.NPCS.personality import ensure_personalities
        given = ensure_personalities(connection, world["id"])
        if given:
            print(f"  (gave {given} people a personality -- this world predates them)")

        from Data.NPCS.family_names import ensure_family_names
        named = ensure_family_names(connection, world["id"], of_old=True)
        if named:
            print(f"  (gave {named} people family names -- this world predates them)")

        from Data.World.harvest import ensure_household_stores
        stocked = ensure_household_stores(connection, world["id"])
        if stocked:
            print(f"  (stocked {stocked} villages with food until their first harvest)")

        districts_created = 0
        mills_bakeries_created = 0
        for kingdom in get_world_kingdoms(connection, world["id"]):
            for city in get_kingdom_cities(connection, kingdom["id"]):
                districts_created += len(
                    ensure_districts_for_city(connection, city["id"])
                )
                mills_bakeries_created += len(
                    ensure_production_chain_buildings(
                        connection, city["id"], city["type"]
                    )
                )
        if districts_created:
            print(
                f"  (backfilled {districts_created} districts onto this world's cities)"
            )
        if mills_bakeries_created:
            print(
                f"  (backfilled {mills_bakeries_created} Mills/Bakeries onto this world -- it predates the Grain->Flour->Food chain)"
            )
        connection.commit()
        connection.close()
        return world

    connection.close()
    print(
        "No existing world found -- generating a new one. This can take a few seconds..."
    )

    return generate_world()


def on_tick(clock, rollover):

    if rollover["day"]:
        print(f"[{clock}]")

    # Each new in-game year is autosaved; only the last three are kept (Data.Infrastructure.saves).
    if rollover["year"]:
        from Data.Infrastructure.saves import autosave_current
        summary = autosave_current()
        print(f"  (autosaved as '{summary['name']}')")


def startup_menu():
    """
    Continue the world, load a save, or start a new world from a seed.
    Without a terminal to answer (or with --continue), it simply continues.
    """
    import sys
    from Data.Infrastructure import saves
    from Data.Infrastructure.seeds import parse_seed

    if "--continue" in sys.argv or not sys.stdin.isatty():
        return

    while True:
        current = None
        if saves._db_file().exists():
            connection = get_connection()
            if connection.execute("SELECT 1 FROM worlds LIMIT 1").fetchone():
                current = saves.summarize(connection)
            connection.close()            # closed before any load or new world replaces the file (Windows)
        print("\n=== My World ===")
        if current:
            print(f"  [1] Continue    -- {current['world']}, year {current['year']}, seed {current['seed']}")
        print("  [2] Load a save")
        print("  [3] New world")
        print("  [4] Quit")
        choice = input("> ").strip()

        if choice == "1" and current:
            return
        if choice == "2":
            found = saves.list_saves()
            if not found:
                print("No saves yet.")
                continue
            for number, summary in enumerate(found, 1):
                print(f"  [{number}] " + saves.describe(summary))
            pick = input("Which save? (number, or Enter to go back) > ").strip()
            if pick.isdigit() and 1 <= int(pick) <= len(found):
                summary = saves.load_save(found[int(pick) - 1]["key"])
                print(f"Loaded '{summary['name']}'." + (" (the world you had is kept as 'before-load')" if current else ""))
                return
            continue
        if choice == "3":
            text = input("Seed (a number or any word; Enter for a random one) > ").strip()
            print("Generating a new world -- this takes a little while ...")
            world = saves.new_world(parse_seed(text) if text else None)
            print(f"New world generated from seed {world['seed']}." + (" (the world you had is kept as 'before-new')" if current else ""))
            return
        if choice == "4":
            raise SystemExit(0)
        print("Choose 1, 2, 3 or 4.")


def main():

    init_database()

    startup_menu()

    world = get_or_create_world()

    print(
        f"Starting simulation for '{world['name']}' at {SIM_MINUTES_PER_REAL_SECOND} sim-minute(s) per real second."
    )
    print(
        "Press Ctrl+C to stop -- the clock's current position is saved after every tick, so it's safe to stop any time."
    )

    from Data.Simulation.logger import log_line

    log_line(
        "SESSION",
        f"Started -- world '{world['name']}' (id {world['id']}), {SIM_MINUTES_PER_REAL_SECOND} sim-min/real-sec",
    )

    connection = get_connection()

    try:
        run_realtime(
            connection,
            world["id"],
            sim_minutes_per_real_second=SIM_MINUTES_PER_REAL_SECOND,
            on_tick=on_tick,
        )
    except KeyboardInterrupt:
        print("\nSimulation stopped. Run this again to resume from where it left off.")
        log_line("SESSION", "Stopped (Ctrl+C)")
    finally:
        connection.close()


if __name__ == "__main__":
    main()