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

    # suits of armor an army carries (Data.Combat.combat)
    _add_missing_columns(connection, "armies", {"armor": "REAL NOT NULL DEFAULT 0", "tools": "REAL NOT NULL DEFAULT 0"})

    # the jobless were once "Unemployed"; they are Laborers, paid a day's labour (Data.Economy.food_market)
    connection.execute("UPDATE npcs SET profession = 'Laborer' WHERE profession = 'Unemployed'")

    # the Captain trade was once spelled "Captian": worlds made before the fix are corrected
    connection.execute("UPDATE npcs SET profession = 'Captain' WHERE profession = 'Captian'")

    # the Rogue class was once spelled "Rouge": worlds made before the fix are corrected
    connection.execute("UPDATE npcs SET class = 'Rogue' WHERE class = 'Rouge'")
    connection.commit()

    # given names and families (Data.NPCS.family_names)
    _add_missing_columns(connection, "npcs", {"given_name": "TEXT", "family_id": "INTEGER"})

    # the day each person died, so grief can last while a loss is recent (Data.NPCS.contentment)
    _add_missing_columns(connection, "npcs", {"died_day": "INTEGER"})

    # the seed a world was generated from (Data.Infrastructure.seeds)
    _add_missing_columns(connection, "worlds", {"seed": "INTEGER", "map_width": "INTEGER", "map_height": "INTEGER"})
    # which generation of people a world has (Data.NPCS.personality.PEOPLE_VERSION): an older world stays 0
    _add_missing_columns(connection, "worlds", {"people_version": "INTEGER NOT NULL DEFAULT 0"})

    # kills, renown and epithets (Data.Combat.nemesis)
    _add_missing_columns(connection, "npcs", {
        "kills": "INTEGER NOT NULL DEFAULT 0",
        "renown": "REAL NOT NULL DEFAULT 0",
        "epithet": "TEXT",
    })

    # building levels, and what a town owes the crown (Data.Buildings.upgrades)
    _add_missing_columns(connection, "buildings", {"level": "INTEGER NOT NULL DEFAULT 1"})
    _add_missing_columns(connection, "cities", {"debt": "REAL NOT NULL DEFAULT 0"})

    # sieges (Data.Government.campaigns)
    _add_missing_columns(connection, "campaigns", {
        "siege_day": "INTEGER",
        "siege_progress": "REAL NOT NULL DEFAULT 0",
        "starving_days": "INTEGER NOT NULL DEFAULT 0",
    })

    # the peoples of the world (Data.NPCS.races): a person's people and the years they have really
    # lived (`age` is their stage of life); a kingdom's people. Older worlds were all human.
    _add_missing_columns(connection, "npcs", {"race": "TEXT NOT NULL DEFAULT 'Human'", "years": "INTEGER"})
    _add_missing_columns(connection, "kingdoms", {"race": "TEXT NOT NULL DEFAULT 'Human'"})
    connection.execute("UPDATE npcs SET years = age WHERE years IS NULL AND race = 'Human'")

    # sickness (Data.NPCS.sickness): what someone is sick of, the days it has left and how hard it fell;
    # a kingdom's quarantine (Data.Government.edicts)
    _add_missing_columns(connection, "npcs", {"disease": "TEXT", "disease_days": "INTEGER NOT NULL DEFAULT 0",
                                              "disease_severity": "REAL"})
    _add_missing_columns(connection, "kingdoms", {"quarantine_until": "INTEGER"})
    # research (Data.Research.discoveries): points toward the discovery being studied
    _add_missing_columns(connection, "kingdoms", {"research": "REAL NOT NULL DEFAULT 0", "studying": "TEXT"})
    _add_missing_columns(connection, "cities", {"afraid_until": "INTEGER"})
    # prosperity (Data.Economy.prosperity): a town's score; a family's rise to merchant house and nobility
    _add_missing_columns(connection, "cities", {"prosperity": "REAL"})
    _add_missing_columns(connection, "families", {"status": "TEXT", "ennobled_year": "INTEGER"})
    # crime (Data.Government.crime): a person's convictions, their guild, and when they leave jail
    _add_missing_columns(connection, "npcs", {"convictions": "INTEGER NOT NULL DEFAULT 0", "guild_id": "INTEGER",
                                              "jailed_until": "INTEGER"})        # shaken by a disaster (Data.World.disasters)
    _add_missing_columns(connection, "outbreaks", {"chronicled": "INTEGER NOT NULL DEFAULT 0"})
    # adventurers and their quests (Data.Quests.adventure)
    _add_missing_columns(connection, "quests", {"posted_day": "INTEGER", "taken_day": "INTEGER", "done_day": "INTEGER",
                                                "outcome": "TEXT", "target_lair_id": "INTEGER", "target_dragon_id": "INTEGER",
                                                "caravan_id": "INTEGER", "crime_id": "INTEGER"})
    _add_missing_columns(connection, "caravans", {"escort_quest_id": "INTEGER", "escort_checked": "INTEGER NOT NULL DEFAULT 0"})
    connection.execute("CREATE INDEX IF NOT EXISTS idx_npcs_disease ON npcs(disease)")

    # How well each NPC ate today, 0-1 (Data.Economy.food_market): meals restore
    # only this share of their hunger.
    _add_missing_columns(connection, "npcs", {"fed": "REAL NOT NULL DEFAULT 1"})

    # Personality, -100 to +100 each (Data.NPCS.personality). NULL until set.
    _add_missing_columns(connection, "npcs", {
        "generosity": "INTEGER",
        "mercy": "INTEGER",
        "boldness": "INTEGER",
        # the other nine, for the Decision Engine (new worlds only: Data.NPCS.personality.PEOPLE_VERSION)
        "ambition": "INTEGER",
        "diligence": "INTEGER",
        "curiosity": "INTEGER",
        "vengefulness": "INTEGER",
        "sociability": "INTEGER",
        "affection": "INTEGER",
        "honour": "INTEGER",
        "piety": "INTEGER",
        "loyalty": "INTEGER",
        # ability scores, after the AD&D 1st edition Player's Handbook (Data.NPCS.abilities; new worlds only)
        "strength": "INTEGER",
        "intelligence": "INTEGER",
        "wisdom": "INTEGER",
        "dexterity": "INTEGER",
        "constitution": "INTEGER",
        "charisma": "INTEGER",
        "strength_pct": "INTEGER NOT NULL DEFAULT 0",          # exceptional Strength: 18/01 .. 18/00 (100)
        "ability_gains": "TEXT",                               # what training and deeds have added: {"train:strength": 1}
        "ability_age": "INTEGER NOT NULL DEFAULT 0",           # the aging stages already felt (1 middle, 2 old, 3 venerable)
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
        "status": "TEXT NOT NULL DEFAULT 'Standing'",  # Standing, or Fallen (Data.Government.conquest)
        "founded_day": "INTEGER",                       # a horde's founding (Data.Government.horse_kingdom)
        "last_fought_day": "INTEGER",                   # a horde's last raid or war: its peace is counted from here
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