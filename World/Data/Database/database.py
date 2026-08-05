import sqlite3
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

SCHEMA_FILE = DATA_DIR / "Database" / "schema.sql"
DB_FILE = DATA_DIR / "Database" / "world.db"


def get_connection():

    connection = sqlite3.connect(DB_FILE)

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")

    connection.row_factory = sqlite3.Row

    return connection


def init_database():
    """
    Creates every table/index from schema.sql if they don't already
    exist. Safe to call every time the program starts.
    """

    connection = get_connection()

    with open(SCHEMA_FILE, "r") as f:
        connection.executescript(f.read())

    _add_missing_columns(connection, "worlds", {
        "clock_minute": "INTEGER NOT NULL DEFAULT 0",
        "clock_hour": "INTEGER NOT NULL DEFAULT 0",
        "clock_day": "INTEGER NOT NULL DEFAULT 1",
        "clock_month": "INTEGER NOT NULL DEFAULT 1",
        "clock_year": "INTEGER NOT NULL DEFAULT 1",
    })

    _add_missing_columns(connection, "cities", {
        "map_x": "INTEGER NOT NULL DEFAULT 0",
        "map_y": "INTEGER NOT NULL DEFAULT 0",
    })

    connection.commit()
    connection.close()


def _add_missing_columns(connection, table, columns):
    """
    CREATE TABLE IF NOT EXISTS skips tables that already exist, so a
    database created before a given column existed in schema.sql would
    otherwise be missing it permanently. This adds any that aren't
    there yet, on any table, without touching existing data.
    """

    existing_columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}

    for column, definition in columns.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
