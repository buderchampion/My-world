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
from Data.Cities.districts import ensure_districts_for_city
from Data.Database.repository import get_world_kingdoms, get_kingdom_cities

# 1.0 = 1 real second passes per simulated minute (a full simulated day
# takes ~24 real minutes). Raise this to run time faster.
SIM_MINUTES_PER_REAL_SECOND = 1.0


def get_or_create_world():

    connection = get_connection()
    row = connection.execute("SELECT id, name FROM worlds ORDER BY id DESC LIMIT 1").fetchone()

    if row:
        world = dict(row)
        print(f"Resuming existing world: {world['name']} (id {world['id']})")

        # Positions must exist before roads can compute meaningful
        # distances -- always runs first, and is a no-op if this world
        # already has real positions.
        if backfill_city_positions(connection, world["id"]):
            print("  (this world predates city positions -- assigned kingdom-clustered positions retroactively)")

        created = generate_roads(connection, world["id"])
        if created:
            print(f"  (backfilled {len(created)} roads onto this world -- it predates the road network)")
        
        districts_created = 0
        for kingdom in get_world_kingdoms(connection, world["id"]):
            for city in get_kingdom_cities(connection, kingdom["id"]):
                districts_created += len(ensure_districts_for_city(connection, city["id"]))
        if districts_created:
            print(f"  (backfilled {districts_created} districts onto this world's cities)")

        connection.commit()
        connection.close()
        return world

    connection.close()
    print("No existing world found -- generating a new one. This can take a few seconds...")

    return generate_world()


def on_tick(clock, rollover):

    if rollover["day"]:
        print(f"[{clock}]")


def main():

    init_database()

    world = get_or_create_world()

    print(f"Starting simulation for '{world['name']}' at {SIM_MINUTES_PER_REAL_SECOND} sim-minute(s) per real second.")
    print("Press Ctrl+C to stop -- the clock's current position is saved after every tick, so it's safe to stop any time.")

    from Data.Simulation.logger import log_line
    log_line("SESSION", f"Started -- world '{world['name']}' (id {world['id']}), {SIM_MINUTES_PER_REAL_SECOND} sim-min/real-sec")

    connection = get_connection()

    try:
        run_realtime(connection, world["id"], sim_minutes_per_real_second=SIM_MINUTES_PER_REAL_SECOND, on_tick=on_tick)
    except KeyboardInterrupt:
        print("\nSimulation stopped. Run this again to resume from where it left off.")
        log_line("SESSION", "Stopped (Ctrl+C)")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
