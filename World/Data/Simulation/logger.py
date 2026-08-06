"""
A minimal persistent logger -- appends timestamped lines to
Logs/simulation.log at the project root. Separate from the events
table (which is queryable, structured world-history data) and from
watch_events.py (which is a live, ephemeral console view): this is
the permanent, plain-text record of what happened and when, on disk,
survives independently of the database.
"""

from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "Logs"
LOG_FILE = LOG_DIR / "simulation.log"


def log_line(category: str, message: str) -> None:
    """
    Appends one timestamped line. Creates the Logs folder on first use.
    Best-effort: a logging failure should never crash the simulation
    itself, so write errors are swallowed rather than raised.
    """

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] [{category}] {message}\n")

    except OSError:
        pass
