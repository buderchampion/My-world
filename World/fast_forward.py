"""
Fast-forward a world: run it for years as fast as the computer allows, and
see what it actually does. Every minute is ticked exactly as in run_world.py
(so every daily, monthly, seasonal and yearly event happens just the same);
there is simply no waiting between ticks. At the end of each year -- and at
the end of the run -- it prints a report: people, births and deaths, what
happened, each kingdom's state, and the year's chronicle.

    python fast_forward.py                    one year of a brand-new world
    python fast_forward.py --years 10         ten years
    python fast_forward.py --days 40          a short run (reports at the end)
    python fast_forward.py --seed 42          the same new world every time
    python fast_forward.py --fresh            start over with a new world
    python fast_forward.py --db copy.db       run a particular world file

It uses its own database file (Data/Database/fast_forward.db), and continues
it each time, so you can run in chunks. Your real world.db is never touched
unless you point --db at it. Reports also go to fast_forward_report.txt.
"""

import argparse
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_DB = HERE / "Data" / "Database" / "fast_forward.db"
REPORT_FILE = HERE / "fast_forward_report.txt"
DAYS_PER_YEAR = 360


def say(text, report):
    print(text, flush=True)
    report.write(text + "\n")
    report.flush()


def year_report(conn, world_id, year, first_day, last_day, report):
    """Everything that happened between two elapsed days, and the state now."""

    from Data.World.countryside import FAMILY_SIZE

    people = conn.execute("SELECT COUNT(*) FROM npcs WHERE alive = 1").fetchone()[0]
    households = conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities").fetchone()[0]
    say("", report)
    say(f"==================== YEAR {year} (days {first_day}-{last_day}) ====================", report)
    say(f"People: {people:,} individuals (towns and named villagers) + {households * FAMILY_SIZE:,.0f} in "
        f"village households = {people + households * FAMILY_SIZE:,.0f}", report)

    events = conn.execute(
        "SELECT type, COUNT(*) n FROM events WHERE day >= ? AND day <= ? GROUP BY type ORDER BY n DESC",
        (first_day, last_day)
    ).fetchall()
    say("Happened: " + (", ".join(f"{e['type']} {e['n']}" for e in events) or "nothing logged"), report)

    causes = {}
    for row in conn.execute("SELECT description FROM events WHERE type = 'Death' AND day >= ? AND day <= ?",
                            (first_day, last_day)):
        match = re.search(r"died \((.+?)\)", row["description"])
        cause = match.group(1) if match else "unknown"
        causes[cause] = causes.get(cause, 0) + 1
    if causes:
        say("Deaths by cause: " + ", ".join(f"{c} {n}" for c, n in sorted(causes.items(), key=lambda x: -x[1])), report)

    famine = conn.execute(
        "SELECT description FROM events WHERE type = 'Famine' AND day >= ? AND day <= ?", (first_day, last_day)
    ).fetchall()
    if famine:
        dead = sum(int(m.group(1)) for m in (re.search(r"about (\d+) lives", f["description"]) for f in famine) if m)
        say(f"Village famine deaths: about {dead:,} people", report)

    say("", report)
    say(f"{'KINGDOM':14} {'RULER':22} {'BREAD':>6} {'TREASURY':>9} {'HAPPY':>6} {'STRESS':>7} {'ATE':>5} {'HUNGRY VILLAGES':>16} "
        f"{'LOYAL':>6} {'UNREST':>7} {'STABLE':>7}", report)
    for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? ORDER BY id", (world_id,)).fetchall():
        ruler = conn.execute("SELECT name, title, alive FROM npcs WHERE id = ?", (k["ruler_id"],)).fetchone() if k["ruler_id"] else None
        ruler_text = f"{ruler['title'] or ''} {ruler['name']}".strip() + ("" if ruler["alive"] else " (dead)") if ruler else "-"
        mood = conn.execute(
            """SELECT AVG(n.needs_happiness) h, AVG(n.needs_stress) s, AVG(n.fed) f FROM npcs n
               JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1""", (k["id"],)
        ).fetchone()
        hungry = conn.execute(
            "SELECT COUNT(*) FROM cities WHERE kingdom_id = ? AND households > 0 AND hungry_days > 0", (k["id"],)
        ).fetchone()[0]
        villages = conn.execute("SELECT COUNT(*) FROM cities WHERE kingdom_id = ? AND households > 0", (k["id"],)).fetchone()[0]
        say(f"{k['name'][:14]:14} {ruler_text[:22]:22} {k['price_level']:>5.2f}x {k['treasury']:>9,.0f} "
            f"{(mood['h'] or 0):>6.1f} {(mood['s'] or 0):>7.1f} {(mood['f'] or 0):>5.0%} {hungry:>8} of {villages} "
            f"{k['health_loyalty']:>6.0f} {k['health_unrest']:>7.0f} {k['health_stability']:>7.0f}", report)

    entries = conn.execute(
        """SELECT k.name, e.text FROM chronicle_entries e JOIN kingdoms k ON k.id = e.kingdom_id
           WHERE k.world_id = ? AND e.year = ? ORDER BY e.id""", (world_id, year)
    ).fetchall()
    if entries:
        say("", report)
        say("From the chronicles:", report)
        for e in entries:
            say(f"  [{e['name']}] {e['text']}", report)


def main():

    parser = argparse.ArgumentParser(description="Run a world for years, as fast as possible, and report on it.")
    parser.add_argument("--years", type=float, default=None, help="how many years to run (default 1)")
    parser.add_argument("--days", type=int, default=None, help="how many days to run (instead of --years)")
    parser.add_argument("--seed", type=int, default=None, help="seed for a brand-new world (the same world every time)")
    parser.add_argument("--fresh", action="store_true", help="throw the fast-forward world away and start a new one")
    parser.add_argument("--db", help=f"world database file to run (default {DEFAULT_DB.name})")
    args = parser.parse_args()

    import Data.Database.database as database_module
    import Data.Simulation.logger as logger

    db_file = Path(args.db) if args.db else DEFAULT_DB
    if args.fresh and db_file == DEFAULT_DB:
        for ext in ("", "-wal", "-shm"):
            Path(str(db_file) + ext).unlink(missing_ok=True)

    database_module.DB_FILE = db_file
    logger.LOG_DIR = HERE / "Logs"
    logger.LOG_FILE = logger.LOG_DIR / "fast_forward.log"

    from Data.Database.database import get_connection, init_database
    from Data.Simulation.simulation_manager import SimulationManager

    report = open(REPORT_FILE, "a", encoding="utf-8")

    if not db_file.exists():
        say(f"Generating a new world in {db_file.name} ...", report)
        from Data.World.world_generator import generate_world
        world_made = generate_world(args.seed)
        say(f"  seed {world_made['seed']}", report)

    init_database()
    conn = get_connection()
    world = conn.execute("SELECT id, name FROM worlds ORDER BY id LIMIT 1").fetchone()
    manager = SimulationManager(conn, world["id"])

    days = args.days if args.days is not None else round((args.years if args.years is not None else 1) * DAYS_PER_YEAR)
    say(f"\nFast-forwarding '{world['name']}' from year {manager.clock.year}, {manager.clock.season}, for {days} days ...", report)

    year_first_day = (manager.clock.year - 1) * DAYS_PER_YEAR
    started = time.time()
    days_done = 0

    while days_done < days:
        rollover = manager.tick()
        if rollover["day"]:
            days_done += 1
            if days_done % 30 == 0:
                per_day = (time.time() - started) / days_done
                print(f"  ... year {manager.clock.year}, {manager.clock.season}: {days_done} of {days} days "
                      f"({per_day:.1f} s per day, about {per_day * (days - days_done) / 60:.0f} min to go)", flush=True)
        if rollover["year"]:
            year_report(conn, world["id"], manager.clock.year - 1, year_first_day, manager.clock.year * DAYS_PER_YEAR - DAYS_PER_YEAR - 1, report)
            year_first_day = (manager.clock.year - 1) * DAYS_PER_YEAR

    # the part of a year at the end of the run
    year_report(conn, world["id"], manager.clock.year, year_first_day, year_first_day + DAYS_PER_YEAR - 1, report)
    say(f"\nRan {days_done} days in {(time.time() - started) / 60:.1f} minutes. "
        f"Run again to continue this world, or use --fresh for a new one.", report)
    report.close()
    conn.close()


if __name__ == "__main__":
    main()