PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS worlds (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    clock_minute INTEGER NOT NULL DEFAULT 0,
    clock_hour   INTEGER NOT NULL DEFAULT 0,
    clock_day    INTEGER NOT NULL DEFAULT 1,
    clock_month  INTEGER NOT NULL DEFAULT 1,
    clock_year   INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS kingdoms (
    id              INTEGER PRIMARY KEY,
    world_id        INTEGER NOT NULL REFERENCES worlds(id),
    name            TEXT NOT NULL,
    capital_city_id INTEGER REFERENCES cities(id),
    ruler_id        INTEGER REFERENCES npcs(id),
    color           TEXT,  -- hex color string, e.g. "#3F7FBF" -- used to
                            -- tell kingdoms apart on the map
    treasury        REAL NOT NULL DEFAULT 1000,  -- shared kingdom-level gold,
                            -- separate from individual NPCs' own gold
    tax_rate        REAL NOT NULL DEFAULT 0.1    -- fraction of each day's
                            -- wages collected into the treasury
);

CREATE TABLE IF NOT EXISTS kingdom_relations (
    kingdom_a_id     INTEGER NOT NULL REFERENCES kingdoms(id),
    kingdom_b_id     INTEGER NOT NULL REFERENCES kingdoms(id),
    status           TEXT NOT NULL DEFAULT 'Neutral',
    peace_until_year INTEGER,

    PRIMARY KEY (kingdom_a_id, kingdom_b_id)
);

CREATE TABLE IF NOT EXISTS caravans (
    id                  INTEGER PRIMARY KEY,
    origin_city_id      INTEGER NOT NULL REFERENCES cities(id),
    destination_city_id INTEGER NOT NULL REFERENCES cities(id),
    resource            TEXT NOT NULL,
    amount              REAL NOT NULL,
    buyer_kingdom_id    INTEGER NOT NULL REFERENCES kingdoms(id),
    seller_kingdom_id   INTEGER NOT NULL REFERENCES kingdoms(id),
    days_remaining      INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'En Route'  -- En Route, Delivered, Raided
);

CREATE TABLE IF NOT EXISTS cities (
    id          INTEGER PRIMARY KEY,
    kingdom_id  INTEGER NOT NULL REFERENCES kingdoms(id),
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    population  INTEGER NOT NULL,   -- the INITIAL/target population rolled at
                                     -- generation time, not a live headcount.
                                     -- Actual current population is always a
                                     -- query: SELECT COUNT(*) FROM npcs WHERE
                                     -- city_id = ? AND alive = 1
    ruler_id    INTEGER REFERENCES npcs(id),
    map_x       INTEGER NOT NULL DEFAULT 0,
    map_y       INTEGER NOT NULL DEFAULT 0,
    wealth      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS city_resources (
    city_id         INTEGER NOT NULL REFERENCES cities(id),
    resource_name   TEXT NOT NULL,
    amount          REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (city_id, resource_name)
);

CREATE TABLE IF NOT EXISTS roads (
    id           INTEGER PRIMARY KEY,
    city_a_id    INTEGER NOT NULL REFERENCES cities(id),
    city_b_id    INTEGER NOT NULL REFERENCES cities(id),
    kind         TEXT NOT NULL,   -- "Capital Road" (within a kingdom) or "Trade Route" (between kingdom capitals)
    distance     REAL NOT NULL,   -- straight-line distance between the two cities' map_x/map_y
    travel_days  INTEGER NOT NULL,
    UNIQUE (city_a_id, city_b_id)
);

CREATE TABLE IF NOT EXISTS buildings (
    id          INTEGER PRIMARY KEY,
    city_id     INTEGER NOT NULL REFERENCES cities(id),
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    category    TEXT NOT NULL,
    capacity    INTEGER NOT NULL,
    condition   INTEGER NOT NULL DEFAULT 100,
    owner_id    INTEGER REFERENCES npcs(id),
    district_id INTEGER REFERENCES districts(id)
    -- No workers/residents columns -- "who works/lives here" is always
    -- SELECT id FROM npcs WHERE workplace_id = ? / home_id = ?
);

CREATE TABLE IF NOT EXISTS districts (
    id          INTEGER PRIMARY KEY,
    city_id     INTEGER NOT NULL REFERENCES cities(id),
    name        TEXT NOT NULL,
    category    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS npcs (
    id              INTEGER PRIMARY KEY,
    city_id         INTEGER NOT NULL REFERENCES cities(id),
    name            TEXT NOT NULL,
    gender          TEXT NOT NULL,
    age             INTEGER NOT NULL,
    class           TEXT NOT NULL,
    profession      TEXT NOT NULL,
    workplace_id    INTEGER REFERENCES buildings(id),
    home_id         INTEGER REFERENCES buildings(id),
    gold            REAL NOT NULL DEFAULT 0,
    spouse_id       INTEGER REFERENCES npcs(id),
    parent_a_id     INTEGER REFERENCES npcs(id),
    parent_b_id     INTEGER REFERENCES npcs(id),
    title           TEXT,
    location_id     INTEGER REFERENCES buildings(id),
    schedule_json    TEXT,   -- small fixed shape ({"Morning": "Workplace", ...})
                              -- not worth normalizing into its own table
    needs_hunger    INTEGER NOT NULL DEFAULT 100,
    needs_energy    INTEGER NOT NULL DEFAULT 100,
    needs_social    INTEGER NOT NULL DEFAULT 100,
    health          INTEGER NOT NULL DEFAULT 100,
    alive           INTEGER NOT NULL DEFAULT 1   -- SQLite has no real boolean
    -- No city_type column -- always derivable via JOIN to cities.type
    -- No children list -- always SELECT id FROM npcs WHERE parent_a_id = ?
    --   OR parent_b_id = ?
);

CREATE TABLE IF NOT EXISTS npc_skills (
    npc_id      INTEGER NOT NULL REFERENCES npcs(id),
    skill_name  TEXT NOT NULL,
    level       INTEGER NOT NULL DEFAULT 1,
    xp          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (npc_id, skill_name)
);

CREATE TABLE IF NOT EXISTS relationships (
    npc_a_id    INTEGER NOT NULL REFERENCES npcs(id),
    npc_b_id    INTEGER NOT NULL REFERENCES npcs(id),
    type        TEXT NOT NULL,
    reputation  INTEGER NOT NULL,
    PRIMARY KEY (npc_a_id, npc_b_id),
    CHECK (npc_a_id < npc_b_id)   -- store each pair once; query with
                                   -- WHERE npc_a_id = ? OR npc_b_id = ?
);

CREATE TABLE IF NOT EXISTS quests (
    id                  INTEGER PRIMARY KEY,
    city_id             INTEGER NOT NULL REFERENCES cities(id),
    type                TEXT NOT NULL,
    title               TEXT NOT NULL,
    giver_id            INTEGER REFERENCES npcs(id),
    reward_gold         REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'Available',
    target_building_id  INTEGER REFERENCES buildings(id),   -- Bounty
    target_npc_a_id     INTEGER REFERENCES npcs(id),         -- Diplomacy
    target_npc_b_id     INTEGER REFERENCES npcs(id),         -- Diplomacy
    from_city_id        INTEGER REFERENCES cities(id),       -- Supply Run
    to_city_id          INTEGER REFERENCES cities(id),       -- Supply Run
    resource            TEXT,                                -- Supply Run
    quantity            INTEGER                              -- Supply Run
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    kingdom_id  INTEGER REFERENCES kingdoms(id),   -- set for kingdom-scale events (wars)
    city_id     INTEGER REFERENCES cities(id),      -- set for city-scale events (weddings, raids, coronations)
    type        TEXT NOT NULL,
    description TEXT NOT NULL,
    day         INTEGER   -- elapsed days since world start (Data.Simulation.clock);
                            -- auto-filled by create_event() from the world's current
                            -- clock position unless explicitly overridden
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id    INTEGER NOT NULL REFERENCES events(id),
    npc_id      INTEGER NOT NULL REFERENCES npcs(id),
    PRIMARY KEY (event_id, npc_id)
);

-- Indexes on the columns that get queried constantly (this is where
-- "who works here" / "who's home here" performance actually lives)
CREATE INDEX IF NOT EXISTS idx_npcs_city ON npcs(city_id);
CREATE INDEX IF NOT EXISTS idx_npcs_workplace ON npcs(workplace_id);
CREATE INDEX IF NOT EXISTS idx_npcs_home ON npcs(home_id);
CREATE INDEX IF NOT EXISTS idx_npcs_profession ON npcs(profession);
CREATE INDEX IF NOT EXISTS idx_npcs_parents ON npcs(parent_a_id, parent_b_id);
CREATE INDEX IF NOT EXISTS idx_buildings_city ON buildings(city_id);
CREATE INDEX IF NOT EXISTS idx_buildings_type ON buildings(type);
CREATE INDEX IF NOT EXISTS idx_cities_kingdom ON cities(kingdom_id);
CREATE INDEX IF NOT EXISTS idx_relationships_b ON relationships(npc_b_id);
