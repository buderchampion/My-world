import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

# The Save/Load Manager. The world lives in one file (world.db); saves are
# copies of it in the Saves folder, each with a small summary beside it
# (<name>.db and <name>.json), so listing saves never opens a world.
#
#   save      copies the world, safely even while it runs (SQLite's backup)
#   load      puts a save in place of the world -- after first saving the
#             world as it was, as "before-load", so nothing is ever lost
#   new       backs the world up the same way ("before-new"), then generates
#             a fresh one from a seed (Data.Infrastructure.seeds)
#   autosave  each in-game year (run_world.py); only the last
#             AUTOSAVES_KEPT are kept

SAVES_DIR = Path(__file__).resolve().parents[2] / "Saves"
AUTOSAVE_PREFIX = "autosave-"
AUTOSAVES_KEPT = 3


def _db_file():
    from Data.Database import database
    return Path(database.DB_FILE)


def slug(name):
    """A save's file name from its name: letters, digits, dashes."""
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(name)).strip("-").lower()
    if not text:
        raise ValueError("a save needs a name with at least one letter or digit")
    return text


def summarize(conn):
    """What a save's summary records about the world in it."""
    world = conn.execute("SELECT * FROM worlds ORDER BY id LIMIT 1").fetchone()
    if world is None:
        return {}
    cols = world.keys()
    people = conn.execute("SELECT COUNT(*) FROM npcs WHERE alive = 1").fetchone()[0]
    rural = conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities").fetchone()[0]
    kingdoms = [r[0] for r in conn.execute(
        "SELECT name FROM kingdoms WHERE world_id = ? AND status != 'Fallen' ORDER BY id", (world["id"],))]
    month = world["clock_month"]
    return {
        "world": world["name"],
        "seed": world["seed"] if "seed" in cols else None,
        "year": world["clock_year"], "month": month, "day": world["clock_day"],
        "kingdoms": kingdoms,
        "people": int(people + rural * 5),
    }


def _write(source_conn, name, autosave=False):
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    key = slug(name)
    target = SAVES_DIR / f"{key}.db"
    if target.exists():
        target.unlink()
    dest = sqlite3.connect(target)
    source_conn.backup(dest)                          # a consistent copy, even mid-run
    dest.close()
    summary = summarize(source_conn)
    now = datetime.now()
    summary.update(name=str(name), key=key, saved_at=now.strftime("%Y-%m-%d %H:%M"), saved_ts=now.timestamp(), autosave=autosave)
    (SAVES_DIR / f"{key}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def save_world(name, conn=None):
    """Saves the current world under `name` (replacing a save of that name). Returns its summary."""
    own = conn is None
    if own:
        if not _db_file().exists():
            raise FileNotFoundError("there is no world to save -- generate one first")
        conn = sqlite3.connect(_db_file())
        conn.row_factory = sqlite3.Row
    try:
        return _write(conn, name)
    finally:
        if own:
            conn.close()


def list_saves():
    """Every save's summary, newest first."""
    if not SAVES_DIR.exists():
        return []
    saves = []
    for summary_file in SAVES_DIR.glob("*.json"):
        if (SAVES_DIR / f"{summary_file.stem}.db").exists():
            saves.append(json.loads(summary_file.read_text(encoding="utf-8")))
    return sorted(saves, key=lambda s: s.get("saved_ts", 0), reverse=True)     # to the microsecond, not the minute


def find_save(name):
    key = slug(name)
    for summary in list_saves():
        if summary["key"] == key:
            return summary
    return None


def _replace_world(source_file):
    """Puts a copy of `source_file` in place of the world file -- written into it through SQLite's
    backup, never by deleting it (Windows will not delete a file anything still holds open)."""
    db = _db_file()
    src = sqlite3.connect(source_file)
    dest = sqlite3.connect(db)
    src.backup(dest)
    dest.close()
    src.close()


def load_save(name):
    """Puts the named save in place of the world, first saving the current world as "before-load".
    Returns the loaded save's summary."""
    summary = find_save(name)
    if summary is None:
        raise FileNotFoundError(f"no save called '{name}'")
    if _db_file().exists():
        save_world("before-load")
    _replace_world(SAVES_DIR / f"{summary['key']}.db")
    return summary


def delete_save(name):
    summary = find_save(name)
    if summary is None:
        raise FileNotFoundError(f"no save called '{name}'")
    for ext in (".db", ".json"):
        (SAVES_DIR / f"{summary['key']}{ext}").unlink(missing_ok=True)
    return summary


def new_world(seed=None):
    """Backs up the current world as "before-new", then generates a fresh one from `seed`. Returns the world."""
    db = _db_file()
    if db.exists():
        save_world("before-new")
        try:
            for ext in ("", "-wal", "-shm"):
                Path(str(db) + ext).unlink(missing_ok=True)
        except PermissionError:
            raise PermissionError("the world file is in use -- stop run_world.py (Ctrl+C) and try again; "
                                  "your world was saved as 'before-new'")
    from Data.World.world_generator import generate_world
    return generate_world(seed)


def autosave(conn):
    """The yearly autosave: saves as "autosave-year-N" and keeps only the last AUTOSAVES_KEPT."""
    year = conn.execute("SELECT clock_year FROM worlds ORDER BY id LIMIT 1").fetchone()[0]
    summary = _write(conn, f"{AUTOSAVE_PREFIX}year-{year}", autosave=True)
    autos = [s for s in list_saves() if s.get("autosave")]
    for old in sorted(autos, key=lambda s: (s.get("year", 0), s.get("saved_at", "")), reverse=True)[AUTOSAVES_KEPT:]:
        delete_save(old["key"])
    return summary


def describe(summary):
    """One line about a save, for listings and menus."""
    from Data.Simulation.clock import SEASON_BY_MONTH        # the game's own calendar
    when = f"year {summary.get('year')}, {SEASON_BY_MONTH.get(summary.get('month'), '?')}" if summary.get("year") else "?"
    kingdoms = summary.get("kingdoms") or []
    return (f"{summary['name']:<24} {when:<18} {len(kingdoms)} kingdoms, {summary.get('people', 0):,} people"
            f"   seed {summary.get('seed')}   saved {summary.get('saved_at')}")


def autosave_current():
    """The yearly autosave, through a connection of its own (for the real-time runner)."""
    conn = sqlite3.connect(_db_file(), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return autosave(conn)
    finally:
        conn.close()