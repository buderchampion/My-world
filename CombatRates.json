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

    connection.commit()
    connection.close()
