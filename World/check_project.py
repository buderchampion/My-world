"""
check_project.py -- the pre-commit safety net for the world simulator.

Run it from the World folder before every commit:

    python check_project.py            full check, about a minute
    python check_project.py --quick    static checks only, about a second

What it catches (every one of these has actually bitten this project):
  * typos and missing keys in the JSON config files ("targer_stock")
  * config files that disagree with each other
  * syntax errors, and features that are missing or never wired in
    (the "is this copy up to date?" question)
  * indentation drift that silently changes what a function does
  * a change that stops favor, relocation, or backfill from working
  * git accidentally tracking world.db (which corrupted it twice)

The full check builds a throwaway world in a temp folder. It never
touches your real world.db or your Logs folder.

Exit code is 0 when everything passes and 1 when anything FAILs, so it
can also be wired into a git pre-commit hook later.
"""

import argparse
import ast
import difflib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

SKIP_DIRS = {"__pycache__", ".vs", ".git", ".idea", ".vscode", "Logs", "Benchmark", "old"}

# JSON files that are known to be empty and unused -- not worth a warning.
KNOWN_EMPTY_JSON = {"Data/Counters.json"}


# ---------------------------------------------------------------------------
# FEATURE MANIFEST
#
# Everything the project is supposed to contain. Each time a new feature is
# added, add an entry here -- from then on this script tells you immediately
# if a copy of the project is missing it, or has it but never calls it.
#
#   tables     -- tables schema.sql must create
#   columns    -- (table, column) pairs schema.sql must create
#   resources  -- names that must exist in ResourceTypes.json
#   functions  -- {file: [top-level function names that must exist]}
#   wired      -- {file: [text that must appear in it]}  (proves it's called)
#   once       -- {file: [text that must appear EXACTLY once]}  (catches a
#                 new version pasted above an old one without deleting it,
#                 which leaves both copies running one after the other)
# ---------------------------------------------------------------------------

FEATURES = [
    {
        "name": "Happiness and Stress",
        "columns": [("npcs", "needs_happiness"), ("npcs", "needs_stress")],
    },
    {
        "name": "Production chains and Weapons",
        "resources": ["Grain", "Flour", "Steel", "Stone", "Weapons"],
        "functions": {"Data/Buildings/building_generator.py": ["ensure_production_chain_buildings"]},
        "wired": {"run_world.py": ["ensure_production_chain_buildings"]},
    },
    {
        "name": "Kingdom bias (breadbasket / industrial kingdoms)",
        "wired": {
            "Data/Buildings/building_generator.py": ["CHAIN_CATEGORIES"],
            "Data/Kingdoms/kingdom_generator.py": ["BIAS_RANGE", "CHAIN_CATEGORIES"],
        },
    },
    {
        "name": "Military upkeep (gold, food, weapons)",
        "functions": {"Data/Government/military.py": ["apply_military_upkeep"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["apply_military_upkeep"]},
    },
    {
        "name": "Starvation",
        "functions": {"Data/NPCS/starvation.py": ["apply_starvation_check", "starvation_death_chance"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["apply_starvation_check"]},
    },
    {
        "name": "Civil War",
        "columns": [("kingdoms", "last_civil_war_year")],
        "functions": {
            "Data/Combat/combat.py": ["check_civil_war_eligibility", "resolve_civil_war"],
            "Data/Events/events.py": ["civil_war_event"],
        },
        "wired": {"Data/Simulation/simulation_manager.py": ["check_civil_war_eligibility", "resolve_civil_war"]},
    },
    {
        "name": "Caravans",
        "tables": ["caravans"],
        "functions": {
            "Data/Infrastructure/caravans.py": ["dispatch_caravan", "advance_caravans"],
            "Data/Events/events.py": ["caravan_raided_event"],
        },
        "wired": {
            "Data/Economy/economy.py": ["dispatch_caravan"],
            "Data/Simulation/simulation_manager.py": ["advance_caravans"],
        },
    },
    {
        "name": "Memory: grief",
        "functions": {"Data/Relationships/relationships.py": ["apply_grief"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["apply_grief"],
            "Data/Combat/combat.py": ["apply_grief"],
        },
    },
    {
        "name": "Memory: relationship history and battle bonds",
        "columns": [("relationships", "origin")],
        "functions": {
            "Data/Relationships/relationships.py": [
                "classify_relationship_type", "evolve_relationships_from_battle",
                "form_relationships_from_battle",
            ],
        },
        "wired": {"Data/Combat/combat.py": [
            "evolve_relationships_from_battle", "form_relationships_from_battle", "participants=",
        ]},
    },
    {
        "name": "Travel and Tourism",
        "tables": ["npc_travels"],
        "functions": {
            "Data/Infrastructure/travel.py": [
                "dispatch_npc_travel", "advance_npc_travels", "get_traveling_npc_ids",
                "consider_tourism", "collect_tourist_revenue",
            ],
        },
        "wired": {"Data/Simulation/simulation_manager.py": [
            "advance_npc_travels", "get_traveling_npc_ids", "consider_tourism", "collect_tourist_revenue",
        ]},
    },
    {
        "name": "Migration",
        "functions": {
            "Data/Infrastructure/migration.py": [
                "consider_migration", "relocate_npc", "get_family_members", "find_open_house_in_kingdom",
            ],
        },
        "wired": {"Data/Simulation/simulation_manager.py": ["consider_migration", "relocate_npc"]},
    },
    {
        "name": "Kingdom Favor",
        "tables": ["kingdom_favor"],
        "functions": {"Data/Database/repository.py": ["get_kingdom_favor", "adjust_kingdom_favor"]},
        "wired": {"Data/Economy/economy.py": ["adjust_kingdom_favor", "TRADE_FAVOR_GAIN", "TRADE_FAVOR_LOSS"]},
    },
    {
        "name": "Diplomacy: alliances, succession shocks, war by favor",
        "functions": {
            "Data/Government/diplomacy.py": [
                "apply_peace_dividend", "end_failed_alliances", "check_alliances", "get_allied_kingdom_ids",
                "apply_succession_shock", "apply_war_favor", "war_chance", "grudge_factor",
                "war_candidates", "choose_war_target",
            ],
            "Data/Events/events.py": ["alliance_event", "alliance_ended_event"],
        },
        # the trailing "(" matters: it proves the function is CALLED, not merely imported
        "wired": {
            "Data/Simulation/simulation_manager.py": [
                "apply_peace_dividend(", "end_failed_alliances(", "check_alliances(",
                "apply_succession_shock(", "war_candidates(", "choose_war_target(",
            ],
            "Data/Combat/combat.py": ["apply_war_favor("],
        },
    },
    {
        "name": "Rulers are saved (one coronation per succession, not per month)",
        "wired": {
            "Data/Government/government.py": ["UPDATE cities SET ruler_id"],
            "Data/Combat/combat.py": ['capital_city["ruler"] = None'],
        },
    },
    {
        "name": "Chronicle: each kingdom writes its own history",
        "tables": ["chronicle_entries"],
        "functions": {
            "Data/History/chronicle.py": [
                "write_entry", "pick_template", "format_when", "get_chronicle", "current_date",
                "record_alliance_formed", "record_alliance_ended", "record_ruler_death", "record_coronation",
                "record_war", "attach_source_event", "sovereign_of", "record_civil_war",
            ],
            "read_chronicle.py": ["main"],
        },
        "wired": {
            "Data/Government/diplomacy.py": ["record_alliance_formed(", "record_alliance_ended("],
            "Data/Events/events.py": ["record_ruler_death("],
            "Data/Simulation/simulation_manager.py": ["record_coronation("],
            "Data/Combat/combat.py": [
                "foe_kingdom=defender_kingdom", "foe_kingdom=attacker_kingdom", "record_war(", "attach_source_event(",
                "sovereign_of(", "record_civil_war(",
            ],
        },
    },
    {
        "name": "Grief survives being saved (widows and widowers are freed and actually grieve)",
        "wired": {
            "Data/Simulation/simulation_manager.py": ["grieving.add(", 'if npc["id"] in grieving:'],
            "Data/Combat/combat.py": ["survivors = [npc for city in cities"],
        },
    },
    {
        "name": "Each tick runs each system once (no leftover duplicate code)",
        "once": {
            "Data/Simulation/simulation_manager.py": [
                "def on_hour(", "def on_day(", "def on_month(", "def on_year(",
                "form_families(", "consider_tourism(", "consider_migration(", "record_coronation(",
                "apply_succession_shock(", "apply_aging(", "apply_peace_dividend(", "end_failed_alliances(",
                "check_alliances(", "choose_war_target(", "resolve_kingdom_war(self.conn", "resolve_civil_war(self.conn",
                "apply_starvation_check(npc)", "advance_armies(",
            ],
            "Data/Combat/combat.py": ["def resolve_kingdom_war(", "record_war(", "def resolve_civil_war(", "record_civil_war("],
            "check_project.py": ["def check_repo(", 'r.section("Git hygiene")'],
        },
    },
    {
        "name": "Consumption sinks and starting stock (Tools, Cloth, repairs, Potions)",
        "functions": {
            "Data/Economy/economy.py": ["maintain_buildings", "spoil_potions", "stock_starting_supplies", "produce_resources"],
            "Data/NPCS/healing.py": ["drink_potion"],
        },
        "wired": {
            "Data/Economy/economy.py": ["maintain_buildings(city)", "spoil_potions(city)", "tool_factor", "TOOLS_WEAR_PER_WORKER"],
            "Data/Simulation/simulation_manager.py": ["drink_potion(npc, city)", "NO_CLOTH_HAPPINESS_PER_DAY"],
            "Data/World/world_generator.py": ["stock_starting_supplies("],
            "Data/Database/repository.py": ["UPDATE buildings SET condition"],
        },
    },
    {
        "name": "Armies: one record each (roster, supplies, position on the roads)",
        "tables": ["armies", "army_members"],
        "functions": {
            "Data/Government/armies.py": [
                "find_route", "raise_army", "march_army", "advance_armies", "disband_army", "get_army_npc_ids",
            ],
        },
        "wired": {
            "Data/Simulation/simulation_manager.py": ["advance_armies(self.conn", "deployed_ids=deployed_ids"],
            "Data/Infrastructure/travel.py": ["get_army_npc_ids(conn, world_id)"],
            "Data/Government/military.py": ["deployed_ids"],
        },
    },
    {
        "name": "Ruling households do not emigrate",
        "wired": {"Data/Infrastructure/migration.py": ["RULING_TITLES", 'title") in RULING_TITLES']},
    },
]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

class Report:

    def __init__(self):
        self.failures = 0
        self.warnings = 0

    def section(self, title):
        print(f"\n== {title} ==", flush=True)

    def ok(self, message):
        print(f"  OK    {message}", flush=True)

    def warn(self, message):
        self.warnings += 1
        print(f"  WARN  {message}", flush=True)

    def fail(self, message):
        self.failures += 1
        print(f"  FAIL  {message}", flush=True)

    def info(self, message):
        print(f"  --    {message}", flush=True)


def rel(path):
    return path.relative_to(ROOT).as_posix()


def project_files(suffix):
    for path in sorted(ROOT.rglob("*" + suffix)):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def read_text(path):
    return path.read_text(encoding="utf-8-sig")


def load_json(relative):
    return json.loads(read_text(ROOT / relative))


# ---------------------------------------------------------------------------
# 1. Syntax and JSON
# ---------------------------------------------------------------------------

def check_syntax(r):
    r.section("Python syntax")
    count = 0
    start = r.failures

    for path in project_files(".py"):
        count += 1
        try:
            ast.parse(read_text(path), filename=str(path))
        except SyntaxError as e:
            r.fail(f"{rel(path)} line {e.lineno}: {e.msg}")
        except UnicodeDecodeError as e:
            r.fail(f"{rel(path)}: not readable as UTF-8 ({e.reason})")

    if r.failures == start:
        r.ok(f"{count} Python files parse cleanly")


def check_json(r):
    r.section("JSON files")
    count = 0
    start = r.failures

    for path in project_files(".json"):
        count += 1
        text = read_text(path)

        if not text.strip():
            if rel(path) not in KNOWN_EMPTY_JSON:
                r.warn(f"{rel(path)} is empty")
            continue

        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            r.fail(f"{rel(path)} line {e.lineno}: {e.msg}")

    if r.failures == start:
        r.ok(f"{count} JSON files are valid")


# ---------------------------------------------------------------------------
# 2. Config consistency
# ---------------------------------------------------------------------------

def check_keys(r, where, info, required, optional=()):
    """Reports missing and unknown keys, recognising a near-miss spelling
    as a typo rather than as two unrelated problems."""

    missing = set(required) - set(info)
    unknown = set(info) - set(required) - set(optional)

    for key in sorted(missing):
        close = difflib.get_close_matches(key, sorted(unknown), n=1)
        if close:
            r.fail(f"{where}: key '{close[0]}' looks like a typo for '{key}'")
            unknown.discard(close[0])
        else:
            r.fail(f"{where}: missing key '{key}'")

    for key in sorted(unknown):
        r.fail(f"{where}: unknown key '{key}'")


def check_config(r):
    r.section("Config files agree with each other")
    start = r.failures

    try:
        resources = load_json("Data/Economy/ResourceTypes.json")
        production = load_json("Data/Economy/BuildingProduction.json")
        consumption = load_json("Data/Economy/ConsumptionRates.json")
        building_types = load_json("Data/Buildings/BuildingTypes.json")
        profession_buildings = load_json("Data/Buildings/ProfessionBuildings.json")
        kits = load_json("Data/Buildings/CityBuildingKits.json")
        professions = load_json("Data/NPCS/Professions.json")
        wages = load_json("Data/NPCS/Wages.json")
        classes = load_json("Data/NPCS/ProfessionClasses.json")
        needs_effects = load_json("Data/DailyLife/NeedsEffects.json")
    except (OSError, json.JSONDecodeError) as e:
        r.fail(f"could not load a config file: {e}")
        return

    # Resources: this is the exact check that would have caught "targer_stock".
    for name, info in resources.items():
        check_keys(r, f"ResourceTypes.json '{name}'", info, {"base_price", "target_stock", "consumable"})

    # Production rules.
    for building, rule in production.items():
        where = f"BuildingProduction.json '{building}'"
        check_keys(
            r, where, rule, {"resource", "amount_per_worker"},
            {"input_resource", "input_per_worker", "secondary_resource", "secondary_amount_per_worker"},
        )

        if building not in building_types:
            r.fail(f"{where}: not a building in BuildingTypes.json")

        for field in ("resource", "input_resource", "secondary_resource"):
            if field in rule and rule[field] not in resources:
                r.fail(f"{where}: {field} '{rule[field]}' is not in ResourceTypes.json")

        if "input_resource" in rule and "input_per_worker" not in rule:
            r.fail(f"{where}: has input_resource but no input_per_worker")

        if "secondary_resource" in rule and "secondary_amount_per_worker" not in rule:
            r.fail(f"{where}: has secondary_resource but no secondary_amount_per_worker")

    for resource in consumption:
        if resource not in resources:
            r.fail(f"ConsumptionRates.json: '{resource}' is not in ResourceTypes.json")

    # Professions <-> buildings, wages, classes.
    for profession, building in profession_buildings.items():
        if building is not None and building not in building_types:
            r.fail(f"ProfessionBuildings.json: '{profession}' works at '{building}', which is not in BuildingTypes.json")

    mapped = {name.lower() for name in profession_buildings}

    for category, names in professions.items():
        for name in names:
            if name not in wages:
                r.fail(f"Professions.json: '{name}' has no entry in Wages.json")
            if name not in classes:
                r.fail(f"Professions.json: '{name}' has no entry in ProfessionClasses.json")
            if name.lower() not in mapped:
                r.fail(f"Professions.json: '{name}' has no entry in ProfessionBuildings.json")

    # City building kits.
    for city_type, kit in kits.items():
        for building, bounds in kit.items():
            where = f"CityBuildingKits.json {city_type} '{building}'"
            if building not in building_types:
                r.fail(f"{where}: not a building in BuildingTypes.json")
            if not (isinstance(bounds, list) and len(bounds) == 2 and all(isinstance(n, int) for n in bounds)
                    and 0 <= bounds[0] <= bounds[1]):
                r.fail(f"{where}: range {bounds} must be [min, max] with 0 <= min <= max")

    # Daily-life needs effects.
    needs = {"hunger", "energy", "social", "happiness", "stress"}

    for key, effect in needs_effects.items():
        check_keys(r, f"NeedsEffects.json '{key}'", effect, needs)

    for required in ("House", "_default_workplace", "Traveling"):
        if required not in needs_effects:
            r.fail(f"NeedsEffects.json: missing the '{required}' entry")

    try:
        from Data.DailyLife import daily_life
        destinations = set(daily_life.FOOD_BUILDING_TYPES) | set(daily_life.SOCIAL_BUILDING_TYPES)
        for building in sorted(destinations):
            if building not in needs_effects:
                r.fail(f"daily_life.py sends NPCs to '{building}' but NeedsEffects.json has no entry for it")
            if building not in building_types:
                r.fail(f"daily_life.py sends NPCs to '{building}', which is not in BuildingTypes.json")
    except Exception as e:
        r.warn(f"could not cross-check daily_life.py ({type(e).__name__}: {e})")

    try:
        from Data.Buildings import building_generator
        for category, names in building_generator.CHAIN_CATEGORIES.items():
            for name in names:
                if name not in building_types:
                    r.fail(f"building_generator.CHAIN_CATEGORIES['{category}'] lists '{name}', not in BuildingTypes.json")
    except Exception as e:
        r.warn(f"could not cross-check CHAIN_CATEGORIES ({type(e).__name__}: {e})")

    try:
        starting = load_json("Data/Economy/StartingStock.json")
        known = load_json("Data/Economy/ResourceTypes.json")
        for resource, amount in starting.items():
            if resource not in known:
                r.fail(f"StartingStock.json: '{resource}' is not a resource in ResourceTypes.json")
            elif not isinstance(amount, (int, float)) or amount < 0:
                r.fail(f"StartingStock.json: '{resource}' should be a non-negative amount per person, not {amount!r}")
    except (OSError, json.JSONDecodeError) as e:
        r.fail(f"could not load StartingStock.json: {e}")

    try:
        from Data.History import chronicle
        templates = load_json("Data/History/ChronicleTemplates.json")

        for key in chronicle.REQUIRED_TEMPLATE_KEYS:
            if not templates.get(key):
                r.fail(f"ChronicleTemplates.json: no templates for '{key}'")

        for key, options in templates.items():
            for number, text in enumerate(options, start=1):
                try:
                    text.format(**{field: "x" for field in chronicle.TEMPLATE_FIELDS})
                except (KeyError, IndexError, ValueError) as e:
                    r.fail(f"ChronicleTemplates.json '{key}' #{number}: unknown or broken placeholder {e}")
    except (OSError, json.JSONDecodeError) as e:
        r.fail(f"could not load ChronicleTemplates.json: {e}")
    except ImportError as e:
        r.fail(f"Data/History/chronicle.py could not be imported ({e}) -- check it exists with exactly that name, all lowercase")
    except Exception as e:
        r.fail(f"the chronicle templates could not be checked ({type(e).__name__}: {e})")

    if r.failures == start:
        r.ok("resources, production, professions, kits, needs effects and chronicle templates are consistent")


# ---------------------------------------------------------------------------
# 3. Feature manifest ("is this copy up to date?")
# ---------------------------------------------------------------------------

def find_exact(relative):
    """
    Looks for ROOT/relative the way Python's import will: names must match
    EXACTLY, letter case included. (Windows' own file checks ignore case, so
    a file saved as Chronicle.py looks present there but fails to import.)
    Returns (exact_match, the name actually on disk or None).
    """

    current, actual = ROOT, []

    for part in Path(relative).parts:
        try:
            entries = os.listdir(current)
        except OSError:
            return False, None
        if part in entries:
            match = part
        else:
            near = [e for e in entries if e.lower() == part.lower()]
            if not near:
                return False, None
            match = near[0]
        actual.append(match)
        current = current / match

    actual_path = "/".join(actual)
    return actual_path == Path(relative).as_posix(), actual_path


def file_problem(relative):
    """None if the file is exactly where and what it should be; otherwise a
    plain explanation of what is wrong with it."""

    exact, actual = find_exact(relative)

    if exact:
        return None
    if actual:
        return f"{actual} must be named exactly {relative} (Python names are case-sensitive, even on Windows)"

    _, as_txt = find_exact(relative + ".txt")
    if as_txt:
        return f"found {as_txt} -- rename it to {relative} (the editor added .txt)"

    name = Path(relative).name.lower()
    elsewhere = [
        rel(p) for p in ROOT.rglob("*")
        if p.is_file() and p.name.lower() == name
        and not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    ]
    if elsewhere:
        return f"{relative} is missing, but {elsewhere[0]} exists -- it belongs at {relative}"

    return f"file {relative} is missing"


def top_level_functions(path):
    try:
        tree = ast.parse(read_text(path))
    except (SyntaxError, UnicodeDecodeError):
        return None
    return {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def check_features(r):
    r.section("Features present and wired in")
    start = r.failures

    tables, columns = set(), {}
    schema = ROOT / "Data/Database/schema.sql"

    try:
        memory = sqlite3.connect(":memory:")
        memory.executescript(read_text(schema))
        tables = {row[0] for row in memory.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {t: {row[1] for row in memory.execute(f"PRAGMA table_info({t})")} for t in tables}
        memory.close()
    except (OSError, sqlite3.Error) as e:
        r.fail(f"schema.sql does not run: {e}")

    try:
        resources = load_json("Data/Economy/ResourceTypes.json")
    except (OSError, json.JSONDecodeError):
        resources = {}

    incomplete = 0

    for feature in FEATURES:
        gaps = []

        for table in feature.get("tables", []):
            if table not in tables:
                gaps.append(f"table '{table}' missing from schema.sql")

        for table, column in feature.get("columns", []):
            if column not in columns.get(table, set()):
                gaps.append(f"column {table}.{column} missing from schema.sql")

        for resource in feature.get("resources", []):
            if resource not in resources:
                gaps.append(f"resource '{resource}' missing from ResourceTypes.json")

        for file, names in feature.get("functions", {}).items():
            path = ROOT / file
            problem = file_problem(file)
            if problem:
                gaps.append(problem)
                continue
            found = top_level_functions(path)
            if found is None:
                continue
            for name in names:
                if name not in found:
                    gaps.append(f"{name}() missing from {file}")

        for file, snippets in feature.get("wired", {}).items():
            path = ROOT / file
            problem = file_problem(file)
            if problem:
                gaps.append(problem)
                continue
            text = read_text(path)
            for snippet in snippets:
                if snippet not in text:
                    gaps.append(f"'{snippet}' never appears in {file}")

        for file, snippets in feature.get("once", {}).items():
            problem = file_problem(file)
            if problem:
                gaps.append(problem)
                continue
            text = read_text(ROOT / file)
            repeated = []
            for snippet in snippets:
                # a quoted mention (like the ones in this manifest) is not a copy of the code
                count = text.count(snippet) - text.count('"' + snippet) - text.count("'" + snippet)
                if count == 0:
                    gaps.append(f"'{snippet}' never appears in {file}")
                elif count > 1:
                    repeated.append(f"{snippet} (x{count})")
            if repeated:
                gaps.append(f"{file} contains leftover duplicate code -- an old copy is probably still below "
                            f"a newer one, and both run. Repeated: " + ", ".join(repeated))

        if gaps:
            incomplete += 1
            r.fail(f"{feature['name']}: " + "; ".join(gaps))

    if incomplete:
        r.info("this copy is out of date, or an edit was only half-applied")
    elif r.failures == start:
        r.ok(f"all {len(FEATURES)} features are present and wired in")


# ---------------------------------------------------------------------------
# 4. Git hygiene
# ---------------------------------------------------------------------------

def find_repo_root(start):
    for folder in [start, *start.parents]:
        if (folder / ".git").exists():
            return folder
    return None


def why_git_cannot_use(repo, name):
    """Explains, in plain words, why a git config file isn't working."""

    path = repo / name

    if path.exists():
        if path.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return (f"{name} is saved as UTF-16, which git cannot read -- "
                    f"re-save it as UTF-8 (in Notepad: File > Save As > Encoding: UTF-8)")
        return f"{name} exists in {repo} but does not do the job -- check its contents"

    if (repo / (name + ".txt")).exists():
        return f"found '{name}.txt' in {repo} -- rename it to '{name}'"

    return f"there is no {name} in {repo}"


def check_repo(r):
    r.section("Git hygiene")

    repo = find_repo_root(ROOT)

    if repo is None:
        r.info("this folder is not inside a git repository -- skipped")
        return

    r.info(f"repository root: {repo}")
    start = r.failures

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=30)

    try:
        git("--version")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        r.info("git is not available on PATH -- skipped")
        return

    db_path = (ROOT / "Data" / "Database" / "world.db").relative_to(repo).as_posix()

    # Ask git itself rather than parsing .gitignore by hand: git is the authority
    # (it honours nested .gitignore files, global ignores, and encoding quirks).
    # --no-index so a file that is already tracked is still judged on its pattern.
    ignored = git("check-ignore", "-q", "--no-index", "--", db_path)

    if ignored.returncode == 1:
        r.fail(f"git is NOT ignoring world.db -- {why_git_cannot_use(repo, '.gitignore')}")
    elif ignored.returncode != 0:
        r.info("could not ask git about .gitignore -- ignore check skipped")

    attribute = git("check-attr", "text", "--", db_path)

    if attribute.returncode == 0 and "text: unset" not in attribute.stdout:
        r.warn(f"git does not treat *.db as binary -- {why_git_cannot_use(repo, '.gitattributes')}"
               f" (it should contain the line: *.db binary)")

    tracked = git("ls-files")

    if tracked.returncode != 0:
        r.info("could not list tracked files -- tracked-file check skipped")
        return

    generated = []

    for name in tracked.stdout.splitlines():
        base = name.rsplit("/", 1)[-1]
        if base in ("world.db", "world.db-wal", "world.db-shm"):
            r.fail(f"git is tracking '{name}' -- run: git rm --cached \"{name}\"")
        elif "__pycache__/" in name or name.endswith(".pyc"):
            generated.append(name)

    if generated:
        r.warn(
            f"git is tracking {len(generated)} generated file(s), e.g. '{generated[0]}' -- "
            f"run: git rm -r --cached .   then: git add .   (your .gitignore will skip them)"
        )

    if r.failures == start:
        r.ok("world.db is ignored and not tracked")


# ---------------------------------------------------------------------------
# 5. Simulation smoke test
# ---------------------------------------------------------------------------

def run_step(r, label, fn):
    started = time.time()

    try:
        result = fn()
    except AssertionError as e:
        r.fail(f"{label}: {e}")
        return None, False
    except Exception as e:
        frame = traceback.extract_tb(e.__traceback__)[-1]
        r.fail(f"{label}: {type(e).__name__}: {e}  (at {Path(frame.filename).name}:{frame.lineno})")
        return None, False

    r.ok(f"{label} ({time.time() - started:.1f}s)")
    return result, True


def smoke_test(r):
    r.section("Simulation smoke test (throwaway world -- your real world.db and Logs are untouched)")

    try:
        import Data.Database.database as database_module
        import Data.Simulation.logger as logger_module
    except Exception as e:
        r.fail(f"could not import the database/logger modules: {type(e).__name__}: {e}")
        return

    original = (database_module.DB_FILE, logger_module.LOG_DIR, logger_module.LOG_FILE)
    holder = {}

    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            database_module.DB_FILE = tmp / "check_world.db"
            logger_module.LOG_DIR = tmp / "Logs"
            logger_module.LOG_FILE = logger_module.LOG_DIR / "simulation.log"

            try:
                _smoke_body(r, holder)
            finally:
                if holder.get("conn") is not None:
                    holder["conn"].close()
    finally:
        database_module.DB_FILE, logger_module.LOG_DIR, logger_module.LOG_FILE = original


def _smoke_body(r, holder):
    try:
        from Data.World.world_generator import generate_world
        from Data.Database.database import get_connection
        from Data.Database.repository import (
            get_world_kingdoms, get_kingdom_cities, get_city, save_city, get_kingdom_favor, get_npc,
        )
        from Data.Simulation.simulation_manager import SimulationManager
        from Data.Economy import economy
    except Exception as e:
        r.fail(f"could not import the simulation: {type(e).__name__}: {e}")
        return

    world, ok = run_step(r, "generate a fresh world", generate_world)
    if not ok:
        return

    world_id = world["id"]
    conn = get_connection()
    holder["conn"] = conn

    kingdoms = get_world_kingdoms(conn, world_id)
    npc_count = conn.execute("SELECT COUNT(*) FROM npcs").fetchone()[0]
    r.info(f"{len(kingdoms)} kingdoms, {npc_count} NPCs")

    # --- forced scenarios, run on the fresh world before any ticking ---------

    def favor_wiring():
        buyer, seller = kingdoms[0], kingdoms[1]

        for c in get_kingdom_cities(conn, buyer["id"]):
            city = get_city(conn, c["id"])
            city["resources"]["Food"] = 0.0
            save_city(conn, city)

        for c in get_kingdom_cities(conn, seller["id"]):
            city = get_city(conn, c["id"])
            city["resources"]["Food"] = 100000.0
            save_city(conn, city)

        conn.commit()

        trades = economy.simulate_cross_kingdom_trade_day(conn, world_id)
        conn.commit()

        n = sum(1 for t in trades if t["buyer"] == buyer["name"] and t["seller"] == seller["name"])

        assert n > 0, "a forced Food shortage next to a Food surplus produced no trade at all"

        got_buyer = get_kingdom_favor(conn, buyer["id"], seller["id"])
        got_seller = get_kingdom_favor(conn, seller["id"], buyer["id"])
        want_buyer = n * economy.TRADE_FAVOR_GAIN
        want_seller = n * economy.TRADE_FAVOR_LOSS

        assert abs(got_buyer - want_buyer) < 1e-6 and abs(got_seller - want_seller) < 1e-6, (
            f"after {n} trade(s), favor should be buyer->seller {want_buyer} and seller->buyer "
            f"{want_seller}, but is {got_buyer} and {got_seller}"
        )

        caravans = conn.execute("SELECT COUNT(*) FROM caravans").fetchone()[0]
        assert caravans == len(trades), f"{len(trades)} trades happened but {caravans} caravans were dispatched"

    if len(kingdoms) >= 2:
        def starting_stock():
            per_person = load_json("Data/Economy/StartingStock.json")
            wrong = []
            for city in conn.execute("SELECT id, name, population FROM cities"):
                for resource, amount in per_person.items():
                    row = conn.execute(
                        "SELECT amount FROM city_resources WHERE city_id = ? AND resource_name = ?", (city["id"], resource)
                    ).fetchone()
                    expected = round(city["population"] * amount, 2)
                    if row is None or abs(row["amount"] - expected) > 0.01:
                        wrong.append(f"{city['name']} {resource}: {row['amount'] if row else 'none'} (expected {expected})")
            assert not wrong, f"{len(wrong)} starting stocks are wrong, e.g. " + "; ".join(wrong[:3])

        run_step(r, "every city starts with its storehouses stocked for its population", starting_stock)

        run_step(r, "a trade moves kingdom favor and dispatches a caravan", favor_wiring)
    else:
        r.warn("fewer than 2 kingdoms generated -- favor check skipped")

    def alliances():
        from Data.Government.diplomacy import check_alliances, ALLIANCE_FAVOR_THRESHOLD
        from Data.Database.repository import adjust_kingdom_favor, get_kingdom_relation

        a, b = kingdoms[0], kingdoms[1]

        def clear():
            conn.execute("DELETE FROM kingdom_favor")
            conn.execute("DELETE FROM kingdom_relations")
            conn.execute("DELETE FROM events WHERE type = 'Alliance'")
            conn.commit()

        clear()

        # one kingdom adoring another that does not return the feeling is not an alliance
        adjust_kingdom_favor(conn, a["id"], b["id"], ALLIANCE_FAVOR_THRESHOLD + 10)
        assert not check_alliances(conn, world_id, 1), "an alliance formed although only one kingdom liked the other"

        # mutual goodwill forms exactly one alliance, and logs it once
        adjust_kingdom_favor(conn, b["id"], a["id"], ALLIANCE_FAVOR_THRESHOLD + 10)
        formed = check_alliances(conn, world_id, 1)
        assert len(formed) == 1, f"mutual goodwill should form 1 alliance, formed {len(formed)}"
        assert get_kingdom_relation(conn, a["id"], b["id"])["status"] == "Allied", "the pair's relation was not set to Allied"
        assert not check_alliances(conn, world_id, 2), "an alliance that already existed was formed a second time"

        logged = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Alliance'").fetchone()[0]
        assert logged == 1, f"expected 1 Alliance event in the log, found {logged}"

        clear()   # leave the world as we found it for the steps after this one

    if len(kingdoms) >= 2:
        run_step(r, "mutual goodwill forms an alliance, one-sided does not", alliances)

    def peace_dividend():
        from Data.Government.diplomacy import apply_peace_dividend
        from Data.Database.repository import get_kingdom_favor, set_kingdom_relation

        a, b = kingdoms[0], kingdoms[1]

        def clear():
            conn.execute("DELETE FROM kingdom_favor")
            conn.execute("DELETE FROM kingdom_relations")
            conn.commit()

        clear()

        apply_peace_dividend(conn, world_id, 5)
        warmed_ab = get_kingdom_favor(conn, a["id"], b["id"])
        warmed_ba = get_kingdom_favor(conn, b["id"], a["id"])
        assert warmed_ab > 0 and warmed_ba > 0, "a year of peace did not warm the pair in both directions"

        # a recent war (an active peace treaty) pauses the warming
        set_kingdom_relation(conn, a["id"], b["id"], "Peace Treaty", peace_until_year=10)
        conn.commit()
        apply_peace_dividend(conn, world_id, 5)
        assert (get_kingdom_favor(conn, a["id"], b["id"]) == warmed_ab
                and get_kingdom_favor(conn, b["id"], a["id"]) == warmed_ba), \
            "favor kept growing while a peace treaty was active"

        clear()

    if len(kingdoms) >= 2:
        run_step(r, "a peaceful year warms kingdoms, a recent war pauses it", peace_dividend)

    def alliances_end():
        from Data.Government.diplomacy import end_failed_alliances, ALLIANCE_BREAK_THRESHOLD
        from Data.Database.repository import adjust_kingdom_favor, get_kingdom_relation, set_kingdom_relation

        a, b = kingdoms[0], kingdoms[1]

        def clear():
            conn.execute("DELETE FROM kingdom_favor")
            conn.execute("DELETE FROM kingdom_relations")
            conn.execute("DELETE FROM events WHERE type = 'Alliance Ended'")
            conn.commit()

        clear()
        set_kingdom_relation(conn, a["id"], b["id"], "Allied")
        adjust_kingdom_favor(conn, a["id"], b["id"], 90)
        adjust_kingdom_favor(conn, b["id"], a["id"], 90)
        conn.commit()
        assert not end_failed_alliances(conn, world_id, 1), "a healthy alliance was ended"

        # one side souring is enough to end it
        adjust_kingdom_favor(conn, b["id"], a["id"], ALLIANCE_BREAK_THRESHOLD - 90 - 1)
        ended = end_failed_alliances(conn, world_id, 2)
        assert len(ended) == 1, f"a soured alliance should end, but {len(ended)} ended"
        assert get_kingdom_relation(conn, a["id"], b["id"])["status"] == "Neutral", "the ended alliance was not set back to Neutral"
        logged = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Alliance Ended'").fetchone()[0]
        assert logged == 1, f"expected 1 Alliance Ended event, found {logged}"

        clear()

    if len(kingdoms) >= 2:
        run_step(r, "a soured alliance ends, a healthy one does not", alliances_end)

    def chronicle_records_alliances():
        from Data.Government.diplomacy import check_alliances, end_failed_alliances, ALLIANCE_FAVOR_THRESHOLD
        from Data.Database.repository import adjust_kingdom_favor
        from Data.History.chronicle import get_chronicle, load_templates, format_when

        a, b = kingdoms[0], kingdoms[1]

        def clear():
            conn.execute("DELETE FROM kingdom_favor")
            conn.execute("DELETE FROM kingdom_relations")
            conn.execute("DELETE FROM chronicle_entries")
            conn.execute("DELETE FROM events WHERE type IN ('Alliance', 'Alliance Ended')")
            conn.commit()

        clear()

        adjust_kingdom_favor(conn, a["id"], b["id"], ALLIANCE_FAVOR_THRESHOLD + 10)
        adjust_kingdom_favor(conn, b["id"], a["id"], ALLIANCE_FAVOR_THRESHOLD + 10)
        conn.commit()
        check_alliances(conn, world_id, 3)

        for me, other in ((a, b), (b, a)):
            entries = get_chronicle(conn, me["id"])
            assert len(entries) == 1, f"{me['name']}'s chronicle should hold 1 entry after an alliance, but holds {len(entries)}"
            text = entries[0]["text"]
            assert me["name"] in text and other["name"] in text, f"{me['name']}'s alliance entry does not name both realms: {text!r}"
            assert "{" not in text and "}" not in text, f"an alliance entry has an unfilled placeholder: {text!r}"

        # the alliance sours on B's side: B tells it as withdrawing, A as being forsaken
        adjust_kingdom_favor(conn, b["id"], a["id"], -100)
        conn.commit()
        end_failed_alliances(conn, world_id, 4)

        capitalized, lowercase = format_when(4, "Spring")
        templates = load_templates()

        def told_as(text, key, me, other):
            return any(
                t.format(When=capitalized, when=lowercase, kingdom=me["name"], other=other["name"]) == text
                for t in templates[key]
            )

        entries_a, entries_b = get_chronicle(conn, a["id"]), get_chronicle(conn, b["id"])
        assert len(entries_a) == 2 and len(entries_b) == 2, "both chronicles should record the alliance ending"
        assert told_as(entries_b[1]["text"], "Alliance Ended (cooled)", b, a), "the kingdom whose regard fell did not tell it as withdrawing"
        assert told_as(entries_a[1]["text"], "Alliance Ended (spurned)", a, b), "the kingdom that was left did not tell it as being forsaken"

        clear()

    if len(kingdoms) >= 2:
        run_step(r, "alliances are written into both chronicles, each from its own side", chronicle_records_alliances)

    def war_costs_favor():
        from Data.Government.diplomacy import apply_war_favor, war_chance, WAR_FAVOR_DEFENDER, WAR_FAVOR_ATTACKER
        from Data.Database.repository import get_kingdom_favor

        a, b = kingdoms[0], kingdoms[1]

        conn.execute("DELETE FROM kingdom_favor")
        conn.commit()

        apply_war_favor(conn, a["id"], b["id"])
        assert get_kingdom_favor(conn, b["id"], a["id"]) == WAR_FAVOR_DEFENDER, "the defender's favor toward the attacker did not drop"
        assert get_kingdom_favor(conn, a["id"], b["id"]) == WAR_FAVOR_ATTACKER, "the attacker's favor toward the defender did not drop"
        assert war_chance([-100]) > war_chance([0]) > 0, "a grievance should make war more likely than having none"

        conn.execute("DELETE FROM kingdom_favor")
        conn.commit()

    if len(kingdoms) >= 2:
        run_step(r, "a war costs favor, and a grievance makes war likelier", war_costs_favor)

    def backfill():
        from Data.Buildings.building_generator import ensure_production_chain_buildings

        kit = load_json("Data/Buildings/CityBuildingKits.json")["Village"]
        row = conn.execute("SELECT id FROM cities WHERE type = 'Village' LIMIT 1").fetchone()

        assert row is not None, "no Village was generated to test the backfill with"

        village = row["id"]
        doomed = "SELECT id FROM buildings WHERE city_id = ? AND type IN ('Mill', 'Bakery')"

        conn.execute(f"UPDATE npcs SET workplace_id = NULL WHERE workplace_id IN ({doomed})", (village,))
        conn.execute(f"UPDATE npcs SET location_id = NULL WHERE location_id IN ({doomed})", (village,))
        conn.execute("DELETE FROM buildings WHERE city_id = ? AND type IN ('Mill', 'Bakery')", (village,))
        conn.commit()

        ensure_production_chain_buildings(conn, village, "Village")
        conn.commit()

        for building in ("Mill", "Bakery"):
            minimum = kit.get(building, [0, 0])[0]
            made = conn.execute(
                "SELECT COUNT(*) FROM buildings WHERE city_id = ? AND type = ?", (village, building)
            ).fetchone()[0]
            assert not (minimum > 0 and made == 0), (
                f"the backfill never created a {building} (the village kit guarantees at least {minimum})"
            )

    run_step(r, "backfilling Mills and Bakeries creates both", backfill)

    def relocation():
        from Data.Infrastructure.migration import relocate_npc

        destination = get_kingdom_cities(conn, kingdoms[1]["id"])[0]["id"]
        row = conn.execute(
            """
            SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession != 'Child' LIMIT 1
            """,
            (kingdoms[0]["id"],),
        ).fetchone()

        relocate_npc(conn, row["id"], destination)
        conn.commit()

        moved = get_npc(conn, row["id"])
        assert moved["city_id"] == destination, (
            "relocate_npc ran but the NPC's city did not change in the database "
            "(save_npc must write city_id)"
        )

    if len(kingdoms) >= 2:
        run_step(r, "relocating an NPC to another kingdom is saved", relocation)

    # --- tick every level of the clock ---------------------------------------

    manager = SimulationManager(conn, world_id)
    manager.clock.hour = 6      # a time-block transition hour, so on_hour does real work
    manager.clock.minute = 0

    run_step(r, "hour tick  (needs and schedules)", manager.on_hour)
    run_step(r, "day tick   (economy, wages, trade, caravans, travel)", manager.on_day)
    run_step(r, "month tick (families, relationships, tourism, migration)", manager.on_month)

    def rulers_saved():
        saved = conn.execute("SELECT COUNT(*) FROM cities WHERE ruler_id IS NOT NULL").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
        assert saved >= total * 0.9, f"only {saved} of {total} cities have a saved ruler after the monthly tick"

        founding = conn.execute(
            "SELECT kingdom_id, COUNT(*) n FROM chronicle_entries WHERE event_type = 'Coronation' GROUP BY kingdom_id"
        ).fetchall()
        assert len(founding) == len(kingdoms) and all(row["n"] == 1 for row in founding), \
            "every kingdom's chronicle should open with exactly one coronation, its founding monarch"

        before = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Coronation'").fetchone()[0]
        entries_before = conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0]
        manager.on_month()
        after = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Coronation'").fetchone()[0]
        assert after == before, f"a second month with every ruler alive crowned {after - before} more rulers (rulers are not being saved)"
        entries_after = conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0]
        assert entries_after == entries_before, "a quiet month added chronicle entries (the founding coronation is being written again)"

    run_step(r, "rulers are saved, each realm's founding monarch is chronicled once", rulers_saved)

    def succession_shakes_relationships():
        from Data.Database.repository import get_kingdom, adjust_kingdom_favor, get_kingdom_favor

        kingdom = kingdoms[0]
        others = kingdoms[1:]

        monarch = get_kingdom(conn, kingdom["id"])["ruler"]
        assert monarch is not None, "no monarch was crowned by the monthly tick"

        conn.execute("DELETE FROM kingdom_favor")
        for other in others:
            adjust_kingdom_favor(conn, kingdom["id"], other["id"], 100)
            adjust_kingdom_favor(conn, other["id"], kingdom["id"], 100)
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (monarch,))
        conn.commit()

        manager.on_month()

        assert get_kingdom(conn, kingdom["id"])["ruler"] != monarch, "the dead monarch was not replaced"
        from Data.History.chronicle import get_chronicle
        crowned = [e for e in get_chronicle(conn, kingdom["id"]) if e["event_type"] == "Coronation"]
        old_name = conn.execute("SELECT name FROM npcs WHERE id = ?", (monarch,)).fetchone()["name"]
        assert len(crowned) == 2 and old_name in crowned[-1]["text"], \
            "the successor's crowning was not chronicled, or does not name the monarch it replaced"
        shaken = [get_kingdom_favor(conn, kingdom["id"], o["id"]) for o in others]
        assert all(abs(f - 100) > 1e-6 for f in shaken), "a new monarch was crowned but the kingdom's feelings were not shaken"
        untouched = [get_kingdom_favor(conn, o["id"], kingdom["id"]) for o in others]
        assert all(f == 100 for f in untouched), "the neighbors' feelings changed although only this kingdom changed ruler"

        conn.execute("DELETE FROM kingdom_favor")
        conn.commit()

    if len(kingdoms) >= 2:
        run_step(r, "a new monarch shakes the kingdom's relationships", succession_shakes_relationships)
    def monarch_deaths():
        from Data.Database.repository import get_kingdom, get_npc
        from Data.Events.events import death_event
        from Data.History.chronicle import get_chronicle

        before = conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0]
        commoner = conn.execute("SELECT id FROM npcs WHERE alive = 1 AND title IS NULL LIMIT 1").fetchone()["id"]
        death_event(conn, get_npc(conn, commoner), "old age")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0] == before, "a commoner's death was written into a chronicle"

        kingdom, foe = kingdoms[0], kingdoms[1]
        monarch = get_npc(conn, get_kingdom(conn, kingdom["id"])["ruler"])
        death_event(conn, monarch, "War", kingdom_id=kingdom["id"], foe_kingdom=foe)
        conn.commit()
        died = [e for e in get_chronicle(conn, kingdom["id"]) if e["event_type"] == "Ruler Died"]
        assert died and monarch["name"] in died[-1]["text"] and foe["name"] in died[-1]["text"], \
            "a monarch slain in war was not chronicled with their name and the enemy's"

    if len(kingdoms) >= 2:
        run_step(r, "a monarch's death is chronicled (naming any foe), a commoner's is not", monarch_deaths)

    def war_chronicled():
        import Data.Combat.combat as combat
        from Data.Database.repository import get_kingdom, get_kingdom_cities
        from Data.History.chronicle import get_chronicle, load_templates, format_when, current_date

        a, d = kingdoms[0], kingdoms[1]
        wars = lambda k: [e for e in get_chronicle(conn, k["id"]) if e["event_type"] == "War"]
        before_a, before_d = len(wars(a)), len(wars(d))
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM chronicle_entries").fetchone()[0]

        # A real war, except that the defender's monarch is sure to fall in it,
        # so the order of the chronicle (the war, then the fallen) is always tested.
        from Data.Database.repository import get_npc, save_npc
        monarch_id = get_kingdom(conn, d["id"])["ruler"]
        assert monarch_id is not None, "(setup) the defending kingdom has no monarch"
        real_casualties, calls = combat.apply_casualties, {"n": 0}

        def casualties_with_the_monarch(conn_, side, won):
            dead = real_casualties(conn_, side, won)
            calls["n"] += 1
            if calls["n"] == 2 and all(n["id"] != monarch_id for n in dead):   # the second call is the defender's side
                monarch = get_npc(conn_, monarch_id)
                monarch["alive"] = False
                save_npc(conn_, monarch)
                dead.append(monarch)
            return dead

        combat.apply_casualties = casualties_with_the_monarch
        try:
            result = combat.resolve_kingdom_war(
                conn, get_kingdom(conn, a["id"]), get_kingdom(conn, d["id"]),
                get_kingdom_cities(conn, a["id"]), get_kingdom_cities(conn, d["id"])
            )
        finally:
            combat.apply_casualties = real_casualties
        assert result["outcome"] != "no_battle", "(setup) the test war never came to battle"

        wa, wd = wars(a), wars(d)
        assert len(wa) == before_a + 1 and len(wd) == before_d + 1, "a war was not written into both kingdoms' chronicles"

        city = result["captured_city"]
        if result["outcome"] == "attacker_wins":
            attacker_key = "War (attacker won, city taken)" if city else "War (attacker won)"
            defender_key = "War (defender lost, city taken)" if city else "War (defender lost)"
        else:
            attacker_key, defender_key = "War (attacker lost)", "War (defender held)"

        capitalized, lowercase = format_when(*current_date(conn, world_id))
        templates = load_templates()

        def told_as(text, key, me, other):
            return any(
                t.format(When=capitalized, when=lowercase, kingdom=me["name"], other=other["name"], city=city or "") == text
                for t in templates[key]
            )

        assert told_as(wa[-1]["text"], attacker_key, a, d), f"the attacker's account does not match the outcome ({result['outcome']}, city: {city})"
        assert told_as(wd[-1]["text"], defender_key, d, a), f"the defender's account does not match the outcome ({result['outcome']}, city: {city})"
        assert wa[-1]["source_event_id"] and wa[-1]["source_event_id"] == wd[-1]["source_event_id"], \
            "the two war entries are not linked to the same War event"

        fallen = conn.execute(
            "SELECT MIN(id) FROM chronicle_entries WHERE event_type = 'Ruler Died' AND kingdom_id = ? AND id > ?",
            (d["id"], last_id)
        ).fetchone()[0]
        assert fallen is not None, "the monarch who fell in the war was not chronicled"
        assert fallen > max(wa[-1]["id"], wd[-1]["id"]), \
            "a ruler who fell in the war was written into the chronicle before the war itself"

    if len(kingdoms) >= 2:
        run_step(r, "a war is written into both chronicles, each from its own side, before its fallen", war_chronicled)

    def civil_war_chronicled():
        import Data.Combat.combat as combat
        from Data.Database.repository import get_kingdom, get_kingdom_cities, get_npc, save_npc
        from Data.History.chronicle import get_chronicle, load_templates, format_when, current_date

        kingdom = get_kingdom(conn, kingdoms[0]["id"])
        cities = get_kingdom_cities(conn, kingdom["id"])
        monarch = get_npc(conn, kingdom["ruler"]) if kingdom.get("ruler") else None
        assert monarch and monarch["alive"], "(setup) the kingdom has no living monarch"
        sovereign = f"{monarch['title']} {monarch['name']}"

        # an armed uprising, in which the monarch is sure to fall
        capital = next(c for c in cities if c["id"] == kingdom["capital"])
        capital["resources"]["Weapons"] = 5000.0
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM chronicle_entries").fetchone()[0]
        real_casualties, calls = combat.apply_casualties, {"n": 0}

        def casualties_with_the_monarch(conn_, side, won):
            dead = real_casualties(conn_, side, won)
            calls["n"] += 1
            if calls["n"] == 1 and all(n["id"] != monarch["id"] for n in dead):
                fallen = get_npc(conn_, monarch["id"])
                fallen["alive"] = False
                save_npc(conn_, fallen)
                dead.append(fallen)
            return dead

        combat.apply_casualties = casualties_with_the_monarch
        try:
            result = combat.resolve_civil_war(conn, kingdom, cities, current_year=1)
        finally:
            combat.apply_casualties = real_casualties
        conn.commit()
        assert result["outcome"] != "no_battle", "(setup) the uprising never came to battle"

        written = [e for e in get_chronicle(conn, kingdom["id"]) if e["id"] > last_id]
        uprising = [e for e in written if e["event_type"] == "Civil War"]
        assert len(uprising) == 1, f"the kingdom should record the civil war once, recorded it {len(uprising)} times"

        key = "Civil War (rebels won)" if result["outcome"] == "rebels_win" else "Civil War (loyalists won)"
        capitalized, lowercase = format_when(*current_date(conn, world_id))
        expected = {t.format(When=capitalized, when=lowercase, kingdom=kingdom["name"], sovereign=sovereign)
                    for t in load_templates()[key]}
        assert uprising[0]["text"] in expected, \
            f"the civil war entry does not match the outcome ({result['outcome']}) or does not name {sovereign}, who reigned when it began"

        source = conn.execute("SELECT type FROM events WHERE id = ?", (uprising[0]["source_event_id"],)).fetchone()
        assert source and source["type"] == "Civil War", "the civil war entry is not linked to the Civil War event"

        fallen_entry = [e for e in written if e["event_type"] == "Ruler Died"]
        assert fallen_entry and fallen_entry[0]["id"] > uprising[0]["id"], \
            "the monarch who fell in the uprising was not chronicled after it"

        others = conn.execute(
            "SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Civil War' AND kingdom_id != ? AND id > ?",
            (kingdom["id"], last_id)
        ).fetchone()[0]
        assert others == 0, "another kingdom wrote about this kingdom's civil war"

    if len(kingdoms) >= 2:
        run_step(r, "a civil war is chronicled by its own kingdom, naming the sovereign, before its fallen", civil_war_chronicled)

    def sinks_work():
        from Data.Database.repository import get_city, save_city, get_npc
        from Data.Economy import economy
        from Data.NPCS.healing import drink_potion, POTION_HEAL

        # a city with workers: production with no Tools is clearly lower than with plenty
        city_id = conn.execute(
            """SELECT b.city_id FROM buildings b JOIN npcs n ON n.workplace_id = b.id
               WHERE n.alive = 1 AND b.type IN ('Farm', 'Quarry', 'Mine') GROUP BY b.city_id ORDER BY COUNT(*) DESC LIMIT 1"""
        ).fetchone()[0]

        def made_with(tools):
            city = get_city(conn, city_id)
            city["resources"] = {"Tools": tools}
            economy.produce_resources(city)
            return sum(v for k, v in city["resources"].items() if k != "Tools"), city["resources"]["Tools"]

        made_without, _ = made_with(0)
        made_with_plenty, tools_left = made_with(100000)
        assert made_without < made_with_plenty * 0.75, "a city with no Tools produced nearly as much as one with plenty"
        assert tools_left < 100000, "working did not wear out any Tools"

        # buildings wear without Wood and Stone, the wear is SAVED, and repairs restore it
        city = get_city(conn, city_id)
        city["resources"]["Wood"] = city["resources"]["Stone"] = 0
        economy.maintain_buildings(city)
        save_city(conn, city)
        worn = get_city(conn, city_id)
        assert all(b["condition"] < 100 for b in worn["buildings"]), "buildings did not wear, or their wear was not saved"
        worn["resources"]["Wood"] = worn["resources"]["Stone"] = 100000
        economy.maintain_buildings(worn)
        assert all(b["condition"] == 100 for b in worn["buildings"]), "repairs did not restore worn buildings"
        assert worn["resources"]["Wood"] < 100000 and worn["resources"]["Stone"] < 100000, "repairs cost no Wood or Stone"
        save_city(conn, worn)

        # a wounded person drinks a potion; a healthy one does not
        npc = get_npc(conn, conn.execute("SELECT id FROM npcs WHERE alive = 1 LIMIT 1").fetchone()["id"])
        city = {"resources": {"Potions": 5}}
        npc["health"] = 50
        assert drink_potion(npc, city) and npc["health"] == 50 + POTION_HEAL and city["resources"]["Potions"] == 4, \
            "a wounded NPC did not drink a potion"
        npc["health"] = 100
        assert not drink_potion(npc, city) and city["resources"]["Potions"] == 4, "a healthy NPC drank a potion"

    run_step(r, "Tools, repairs and Potions are used up, and building wear is saved", sinks_work)

    def armies_march():
        from Data.Database.repository import get_kingdom, get_city, get_kingdom_cities
        from Data.Government import armies as A
        from Data.Government.military import apply_military_upkeep
        from Data.Infrastructure.travel import get_traveling_npc_ids, get_travel_days

        kingdom = get_kingdom(conn, kingdoms[0]["id"])
        capital = get_city(conn, kingdom["capital"])
        target = next(
            (c for other in kingdoms[1:] for c in get_kingdom_cities(conn, other["id"])
             if A.find_route(conn, capital["id"], c["id"])),
            None
        )
        assert target, "(setup) no city in another kingdom can be reached by road"

        # stock the capital first: earlier steps empty stockpiles on purpose
        # (the trade step forces a Food shortage), and this step should not
        # depend on whatever they left behind
        from Data.Database.repository import save_city
        capital["resources"].update({"Food": 5000.0, "Weapons": 500.0, "Potions": 50.0})
        save_city(conn, capital)

        army = A.raise_army(conn, kingdom, capital, size=5)
        assert army and len(army["members"]) == 5, "an army of 5 soldiers could not be raised"
        assert army["food"] > 0, "the army set out with no Food in its wagons"
        assert set(army["members"]) <= get_traveling_npc_ids(conn, kingdom["world_id"]), \
            "soldiers in an army do not count as away from home"

        route = A.find_route(conn, capital["id"], target["id"])
        stops = [capital["id"]] + route
        road_days = sum(get_travel_days(conn, a, b) for a, b in zip(stops, stops[1:]))
        assert A.march_army(conn, army["id"], target["id"]), "the army could not set out"

        # the first day through the real daily tick: armies must advance there, and
        # the soldiers' meal must survive the day's saves of everyone
        members = ",".join(map(str, army["members"]))
        conn.execute(f"UPDATE npcs SET needs_hunger = 30 WHERE id IN ({members})")
        conn.commit()
        manager.on_day()
        hunger = [row[0] for row in conn.execute(f"SELECT needs_hunger FROM npcs WHERE id IN ({members}) AND alive = 1")]
        assert hunger and min(hunger) > 30, "the daily tick did not feed the army, or a later save erased the meal"

        days = 1
        while A.get_army(conn, army["id"])["status"] == "Marching" and days < 400:
            A.advance_armies(conn, kingdom["world_id"])
            days += 1
        arrived = A.get_army(conn, army["id"])
        assert arrived["status"] == "Encamped" and arrived["location_city_id"] == target["id"], "the army never reached its destination"
        assert days == road_days, f"the march took {days} days, but the roads add up to {road_days}"
        assert abs(arrived["food"] - (army["food"] - 5 * days)) < 0.01, "the army did not eat 1 Food per soldier per day"
        assert not A.disband_army(conn, army["id"]), "an army was allowed to disband abroad"

        result = apply_military_upkeep(
            conn, kingdom, [{"id": i, "profession": "Soldier"} for i in range(1, 5)],
            {"resources": {"Food": 100, "Weapons": 100}}, deployed_ids={1, 2}
        )
        assert result["food_cost"] == 2 and result["gold_cost"] == 8, "deployed soldiers are fed by the capital as well as their wagons"

        A.march_army(conn, army["id"], capital["id"])
        while A.get_army(conn, army["id"])["status"] == "Marching":
            A.advance_armies(conn, kingdom["world_id"])
        assert A.disband_army(conn, army["id"]), "the army could not disband at home"
        assert not (set(army["members"]) & get_traveling_npc_ids(conn, kingdom["world_id"])), "disbanded soldiers still count as away"

    if len(kingdoms) >= 2:
        run_step(r, "an army marches by road at road speed, eats from its wagons, and disbands at home", armies_march)

    def year_tick_and_grief():
        import Data.NPCS.aging as aging
        from Data.Database.repository import get_npc

        # A couple in one city where the one who dies comes EARLIER in the city's
        # list -- the case where the yearly tick used to save the survivor's old
        # copy over their grief, leaving them married to the dead.
        city = conn.execute(
            """SELECT city_id FROM npcs WHERE alive = 1 AND title IS NULL AND profession NOT IN ('Unemployed', 'Child')
               GROUP BY city_id HAVING COUNT(*) >= 2 LIMIT 1"""
        ).fetchone()["city_id"]
        pool = [row["id"] for row in conn.execute(
            """SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND title IS NULL
               AND profession NOT IN ('Unemployed', 'Child') ORDER BY id""", (city,))]
        dead, survivor = pool[0], pool[-1]
        for a, b in ((dead, survivor), (survivor, dead)):
            conn.execute("UPDATE npcs SET spouse_id = ?, needs_stress = 10 WHERE id = ?", (b, a))
        conn.execute("UPDATE npcs SET age = 200 WHERE id = ?", (dead,))
        conn.commit()

        real = aging.natural_death_chance
        aging.natural_death_chance = lambda age: 1.0 if age > 150 else real(age)
        try:
            manager.on_year()
        finally:
            aging.natural_death_chance = real

        assert not get_npc(conn, dead)["alive"], "(setup) the chosen NPC did not die of old age"
        widow = get_npc(conn, survivor)
        assert widow["spouse"] is None, "a widow is still married to the dead after the yearly tick (grief erased by a stale save)"
        assert widow["needs"]["stress"] > 10, "a widow felt no grief after the yearly tick (grief erased by a stale save)"

    run_step(r, "year tick  (aging, wars, civil war check) -- and a widow's grief survives it", year_tick_and_grief)

    # --- invariants that should hold after any amount of simulation ----------

    def invariants():
        problems = []

        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            problems.append("database integrity check failed")

        out_of_range = conn.execute(
            """
            SELECT COUNT(*) FROM npcs WHERE alive = 1 AND (
                needs_hunger NOT BETWEEN 0 AND 100 OR needs_energy NOT BETWEEN 0 AND 100 OR
                needs_social NOT BETWEEN 0 AND 100 OR needs_happiness NOT BETWEEN 0 AND 100 OR
                needs_stress NOT BETWEEN 0 AND 100)
            """
        ).fetchone()[0]
        if out_of_range:
            problems.append(f"{out_of_range} living NPCs have a need outside 0-100")

        negative = conn.execute("SELECT COUNT(*) FROM kingdoms WHERE treasury < 0").fetchone()[0]
        if negative:
            problems.append(f"{negative} kingdoms have a negative treasury")

        bad_favor = conn.execute(
            "SELECT COUNT(*) FROM kingdom_favor WHERE favor < -100 OR favor > 100"
        ).fetchone()[0]
        if bad_favor:
            problems.append(f"{bad_favor} favor values are outside -100..100")

        orphans = conn.execute(
            "SELECT COUNT(*) FROM npcs n LEFT JOIN cities c ON c.id = n.city_id WHERE n.alive = 1 AND c.id IS NULL"
        ).fetchone()[0]
        if orphans:
            problems.append(f"{orphans} living NPCs belong to a city that does not exist")

        if conn.execute("SELECT COUNT(*) FROM npcs WHERE alive = 1").fetchone()[0] == 0:
            problems.append("nobody is alive")

        assert not problems, "; ".join(problems)

    run_step(r, "world is still sane afterward", invariants)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pre-commit safety checks for the world simulator.")
    parser.add_argument("--quick", action="store_true", help="static checks only (skips the simulation smoke test)")
    args = parser.parse_args()

    started = time.time()
    r = Report()

    check_syntax(r)
    check_json(r)
    check_config(r)
    check_features(r)
    check_repo(r)

    if args.quick:
        r.section("Simulation smoke test")
        r.info("skipped (--quick)")
    elif r.failures:
        r.section("Simulation smoke test")
        r.info("skipped: fix the failures above first, they would only cause confusing crashes here")
    else:
        smoke_test(r)

    print("\n" + "=" * 60)

    if r.failures:
        print(f"RESULT: {r.failures} problem(s) found -- fix before committing.", flush=True)
    else:
        note = f" ({r.warnings} warning(s))" if r.warnings else ""
        print(f"RESULT: ALL CLEAR{note} -- safe to commit.", flush=True)

    print(f"Finished in {time.time() - started:.1f}s", flush=True)

    sys.exit(1 if r.failures else 0)


if __name__ == "__main__":
    main()