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
        "wealth": "REAL NOT NULL DEFAULT 0",
        "region_id": "INTEGER REFERENCES regions(id)",
        "households": "INTEGER NOT NULL DEFAULT 0",
        "household_store": "REAL NOT NULL DEFAULT 0",
        "hungry_days": "INTEGER NOT NULL DEFAULT 0",
        "famine_dead": "REAL NOT NULL DEFAULT 0",
        "province_id": "INTEGER REFERENCES provinces(id)",
    })

    # Who in an army was levied from the villages (they go home as Farmers).
    _add_missing_columns(connection, "army_members", {"levied": "INTEGER NOT NULL DEFAULT 0"})

    # How well each NPC ate today, 0-1 (Data.Economy.food_market): meals restore
    # only this share of their hunger.
    _add_missing_columns(connection, "npcs", {"fed": "REAL NOT NULL DEFAULT 1"})

    # Personality, -100 to +100 each (Data.NPCS.personality). NULL until set.
    _add_missing_columns(connection, "npcs", {
        "generosity": "INTEGER",
        "mercy": "INTEGER",
        "boldness": "INTEGER",
    })

    _add_missing_columns(connection, "kingdoms", {
        "treasury": "REAL NOT NULL DEFAULT 1000",
        "tax_rate": "REAL NOT NULL DEFAULT 0.1",
        "last_civil_war_year": "INTEGER",
        "price_level": "REAL NOT NULL DEFAULT 1",
        "bad_harvest_years": "INTEGER NOT NULL DEFAULT 0",
        "regent_id": "INTEGER REFERENCES npcs(id)",
        "last_war_year": "INTEGER",
        "last_war_won": "INTEGER",
        "health_loyalty": "REAL NOT NULL DEFAULT 70",
        "health_unrest": "REAL NOT NULL DEFAULT 0",
        "health_stability": "REAL NOT NULL DEFAULT 100",
        "export_ban_until": "INTEGER",
        "martial_law_until": "INTEGER",
        "type": "TEXT NOT NULL DEFAULT 'Crown'",       # Crown or Republic (Data.Government.republic)
    })

    # who rules for a child lord (Data.Government.succession)
    _add_missing_columns(connection, "provinces", {
        "regent_id": "INTEGER REFERENCES npcs(id)",
        "loyalty": "REAL NOT NULL DEFAULT 70",
        "treasury": "REAL NOT NULL DEFAULT 0",
    })
    
    _add_missing_columns(connection, "buildings", {
        "district_id": "INTEGER REFERENCES districts(id)",
    })

    _add_missing_columns(connection, "npcs", {
        "needs_happiness": "INTEGER NOT NULL DEFAULT 70",
        "needs_stress": "INTEGER NOT NULL DEFAULT 20",
    })

    _add_missing_columns(connection, "relationships", {
        "origin": "TEXT",
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