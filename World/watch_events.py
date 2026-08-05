"""
Live event feed. Run this in a second terminal alongside run_world.py
to watch history accumulate in real time -- weddings, coronations,
raids, wars, as they happen.

Read-only, and safe to run at the same time as run_world.py: SQLite's
WAL mode (already enabled by Data.Database.database.get_connection)
allows one writer and any number of readers at once without either
blocking the other.
"""

import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from Data.Database.database import get_connection
from Data.Simulation.clock import DAYS_PER_MONTH, MONTHS_PER_YEAR

POLL_SECONDS = 2.0


def day_to_calendar(day):

    if day is None:
        return "an unknown date"

    days_per_year = MONTHS_PER_YEAR * DAYS_PER_MONTH

    year = day // days_per_year + 1
    remainder = day % days_per_year
    month = remainder // DAYS_PER_MONTH + 1
    day_of_month = remainder % DAYS_PER_MONTH + 1

    return f"Year {year}, Month {month}, Day {day_of_month}"


def main():

    connection = get_connection()

    last_seen_id = connection.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()["m"]

    print(f"Watching for new events (starting after event id {last_seen_id}).")
    print("Run this alongside run_world.py in another terminal. Press Ctrl+C to stop.")
    print()

    try:
        while True:

            rows = connection.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id", (last_seen_id,)
            ).fetchall()

            for row in rows:
                calendar = day_to_calendar(row["day"])
                print(f"[{calendar}] {row['type']}: {row['description']}")
                last_seen_id = row["id"]

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("\nStopped watching.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
