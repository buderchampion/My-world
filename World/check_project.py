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
import random
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
                "check_alliances(", "choose_war_target(", "start_campaign(self.conn", "advance_campaigns(self.conn", "resolve_civil_war(self.conn",
                "apply_starvation_check(npc)", "advance_armies(", "advance_supply_lines(",
                "run_harvest(", "feed_households(", "village_hardship(", "buy_food(",
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
            "Data/Combat/combat.py": ['npc["id"] not in away'],
        },
    },
    {
        "name": "Supply lines: caravans from the capital feed armies in the field",
        "tables": ["supply_caravans"],
        "functions": {
            "Data/Government/supply.py": ["dispatch_supplies", "advance_supply_lines", "resupply_threshold_days"],
        },
        "wired": {"Data/Simulation/simulation_manager.py": ["advance_supply_lines(self.conn"]},
    },
    {
        "name": "Regions: terrain, climate, rivers and farmland; every settlement in one",
        "tables": ["regions"],
        "functions": {
            "Data/World/regions.py": ["generate_regions", "ensure_regions", "assign_settlements_to_regions", "region_neighbors"],
            "read_regions.py": ["main"],
        },
        "wired": {
            "Data/World/world_generator.py": ["ensure_regions("],
            "run_world.py": ["ensure_regions("],
            "Data/Database/database.py": ['"region_id"'],
        },
    },
    {
        "name": "Countryside: villages of peasant households, about 70% rural, settlements on good land",
        "functions": {
            "Data/World/countryside.py": [
                "plan_villages", "named_villagers", "choose_site", "ensure_households", "total_people",
            ],
        },
        "wired": {
            "Data/Kingdoms/kingdom_generator.py": ["countryside.plan_villages(", "UPDATE cities SET households"],
            "Data/Cities/city_generator.py": ["population=None"],
            "run_world.py": ["ensure_households("],
            "Data/Database/database.py": ['"households"'],
        },
    },
    {
        "name": "Harvests: households reap by land and weather, pay grain tax, eat, and sell the surplus",
        "tables": ["harvests"],
        "functions": {
            "Data/World/harvest.py": [
                "run_harvest", "roll_weather", "feed_households", "ensure_household_stores", "days_until_harvest",
            ],
        },
        "wired": {
            "Data/Simulation/simulation_manager.py": ["run_harvest(self.conn", "feed_households(city)"],
            "Data/World/world_generator.py": ["ensure_household_stores("],
            "run_world.py": ["ensure_household_stores("],
            "Data/Database/repository.py": ["UPDATE cities SET household_store"],
            "Data/Economy/economy.py": ["households_daily"],
            "Data/Database/database.py": ['"household_store"', '"hungry_days"'],
        },
    },
    {
        "name": "Villagers enter the story: famine flight, banditry, and levies",
        "functions": {
            "Data/World/villagers.py": [
                "make_individual", "famine_flight", "turn_bandit", "village_hardship", "levy_villagers", "send_levies_home",
            ],
        },
        "wired": {
            "Data/Simulation/simulation_manager.py": ["village_hardship(self.conn"],
            "Data/Government/armies.py": ["levy_villagers(", "send_levies_home(", "levied"],
            "Data/Database/database.py": ['"levied"'],
        },
    },
    {
        "name": "Buying food: households pay from pooled gold, labourers earn a little, temples give alms",
        "functions": {
            "Data/Economy/food_market.py": ["buy_food", "food_price", "give_starting_savings"],
        },
        "wired": {
            "Data/Simulation/simulation_manager.py": ["buy_food(self.conn, city", "LABOUR_WAGE", 'npc.get("fed", 1.0)'],
            "Data/Economy/economy.py": ['if resource == "Food":'],
            "Data/World/world_generator.py": ["give_starting_savings("],
            "Data/Database/database.py": ['"fed"'],
        },
    },
    {
        "name": "Hard times: harvest-driven food prices, revolt resets them, villages die in famine",
        "functions": {
            "Data/Economy/food_market.py": ["price_level", "update_price_level", "reset_price_level"],
        },
        "wired": {
            "Data/World/harvest.py": ["update_price_level(", "_famine_deaths"],
            "Data/Combat/combat.py": ["reset_price_level("],
            "Data/Database/repository.py": ['city.pop("_famine_deaths"'],
            "Data/World/villagers.py": ["famine_dead"],
            "Data/Database/database.py": ['"price_level"', '"famine_dead"'],
        },
    },
    {
        "name": "Fast-forward: run a world for years without waiting, with yearly reports",
        "functions": {"fast_forward.py": ["main", "year_report"]},
    },
    {
        "name": "Provinces: counties held by hereditary lords, villages under the nearest seat or the crown",
        "tables": ["provinces"],
        "functions": {
            "Data/Government/provinces.py": ["ensure_provinces", "install_lord", "seat_province"],
        },
        "wired": {
            "Data/Government/government.py": ["inherit_county(", "install_lord("],
            "Data/World/world_generator.py": ["ensure_provinces("],
            "run_world.py": ["ensure_provinces("],
            "Data/Infrastructure/migration.py": ['"Countess"'],
            "Data/Database/database.py": ['"province_id"'],
        },
    },
    {
        "name": "Succession: crowns and counties stay in the family; child rulers get regents",
        "functions": {
            "Data/Government/succession.py": [
                "line_of_succession", "choose_heir", "choose_regent", "inherit_crown", "inherit_county",
                "vacate_seats", "check_regencies",
            ],
        },
        "wired": {
            "Data/Government/government.py": ["inherit_crown("],
            "Data/Simulation/simulation_manager.py": ["check_regencies(self.conn"],
            "Data/Database/database.py": ['"regent_id"'],
        },
    },
    {
        "name": "Feudal duties: lords keep a share of their county's taxes; their loyalty drifts with how they are treated",
        "functions": {"Data/Government/feudal.py": ["lord_cut", "pay_lords", "loyalty_target", "update_loyalty"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["pay_lords(self.conn", "update_loyalty(self.conn"],
            "Data/World/harvest.py": ["lords_grain"],
            "Data/Combat/combat.py": ["last_war_won"],
            "Data/Database/database.py": ['"loyalty"', '"last_war_won"'],
        },
    },
    {
        "name": "Rebellion: restless lords rise together, hire mercenaries, and are crushed or break away",
        "functions": {"Data/Government/rebellion.py": ["consider_rebellions", "rise", "crush", "break_away", "sides"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["consider_rebellions(self.conn"],
            "Data/Government/feudal.py": ["LORD_SPENDING"],
        },
    },
    {
        "name": "Personality and kingdom health: three traits for everyone, inherited; Loyalty, Unrest, Stability",
        "functions": {
            "Data/NPCS/personality.py": ["random_traits", "inherited_traits", "describe", "ensure_personalities"],
            "Data/Government/health.py": ["kingdom_health", "update_health"],
        },
        "wired": {
            "Data/Database/repository.py": ["random_traits()"],
            "Data/NPCS/family.py": ["inherited_traits(parent_a, parent_b)"],
            "Data/Simulation/simulation_manager.py": ["update_health(self.conn"],
            "run_world.py": ["ensure_personalities("],
            "Data/Database/database.py": ['"generosity"', '"health_stability"'],
        },
    },
    {
        "name": "Edicts: each season (and in a crisis) rulers choose by the realm's health and their own nature",
        "tables": ["kingdom_edicts"],
        "functions": {"Data/Government/edicts.py": ["review", "choose", "needs", "apply_edict", "situation", "lean"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["review(self.conn, self.world_id)", "martial_law_active("],
            "Data/Government/health.py": ["crisis=True"],
            "Data/Economy/economy.py": ["exports_banned("],
            "Data/Government/rebellion.py": ["MARTIAL_LAW_FACTOR"],
            "Data/Government/feudal.py": ["STANDARD_TAX_RATE"],
            "Data/Database/database.py": ['"martial_law_until"'],
        },
    },
    {
        "name": "Merchant republics: a quarter of new kingdoms; Doges elected for life; no counties; a smaller countryside",
        "functions": {"Data/Government/republic.py": ["elect_doge", "republic_count", "is_republic"]},
        "wired": {
            "Data/Government/government.py": ["elect_doge("],
            "Data/World/world_generator.py": ["republic_count("],
            "Data/Kingdoms/kingdom_generator.py": ["REPUBLIC_RURAL_SHARE"],
            "Data/Government/provinces.py": ['kingdom["type"] == "Republic"'],
            "Data/History/chronicle.py": ["ELECTION_TEMPLATES"],
            "Data/Database/database.py": ['"type"'],
        },
    },
    {
        "name": "Republics' economy: customs duties paid by foreigners; a grain office that buys reserves abroad",
        "functions": {"Data/Government/republic.py": ["duties", "duty_rate", "run_grain_offices"]},
        "wired": {
            "Data/Economy/economy.py": ["duties(seller_kingdom, buyer_kingdom"],
            "Data/Simulation/simulation_manager.py": ["run_grain_offices(self.conn"],
        },
    },
    {
        "name": "Campaigns: armies march on a real town and fight there; soldiers away do not defend home",
        "tables": ["campaigns"],
        "functions": {"Data/Government/campaigns.py": ["start_campaign", "advance_campaigns", "fight", "rally", "choose_target", "raise_levies"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["start_campaign(self.conn", "advance_campaigns(self.conn"],
            "Data/Combat/combat.py": ["transfer_conquest(", "target_city=None"],
            "Data/Government/armies.py": ["recruits=()"],
        },
    },
    {
        "name": "Armor: made at Armories from Steel; armies and defenders wear it; it adds strength and saves lives",
        "functions": {"Data/Combat/combat.py": ["armor_share", "city_armor", "side_power"]},
        "wired": {
            "Data/Combat/combat.py": ["side_power(attacker_military, attacker_armor)", "armor_share=attacker_armor"],
            "Data/Government/armies.py": ['take("Armor"'],
            "Data/Government/campaigns.py": ["attacker_armor=attacker_armor"],
            "Data/Database/database.py": ['"armor"'],
        },
    },
    {
        "name": "Sieges: walled towns are besieged and blockaded, breached by siege works or starved, then stormed",
        "functions": {"Data/Government/campaigns.py": ["walled", "besieged_city_ids", "siege_day_passes"]},
        "wired": {
            "Data/Government/campaigns.py": ["'Besieging'", "defender_bonus=WALL_DEFENSE * STARVING_STRENGTH"],
            "Data/Combat/combat.py": ["* defender_bonus"],
            "Data/Government/armies.py": ['take("Tools"'],
            "Data/Simulation/simulation_manager.py": ["besieged_city_ids(self.conn"],
            "Data/Economy/economy.py": ["besieged_city_ids(conn"],
        },
    },
    {
        "name": "Occupation: a garrison holds a conquered town; its people resent it, may rise, and after a year it is integrated",
        "tables": ["occupations", "occupation_garrison"],
        "functions": {"Data/Government/occupation.py": ["begin_occupation", "occupations_monthly", "rise", "garrison_of", "occupied"]},
        "wired": {
            "Data/Government/campaigns.py": ["begin_occupation("],
            "Data/Simulation/simulation_manager.py": ["occupations_monthly(self.conn"],
        },
    },
    {
        "name": "Captured capitals: a far stronger army marches on the capital; its fall moves the seat or ends the kingdom",
        "functions": {"Data/Government/conquest.py": ["aims_at_capital", "fall_of_capital"]},
        "wired": {
            "Data/Combat/combat.py": ["fall_of_capital("],
            "Data/Government/campaigns.py": ["aims_at_capital("],
            "Data/Database/repository.py": ["status != 'Fallen'"],
            "Data/Database/database.py": ['"status"'],
        },
    },
    {
        "name": "Expansion: villages grow on their land; crowded lands send settlers to empty farmland, keeping neutral ground",
        "functions": {"Data/World/expansion.py": ["grow_villages", "consider_settlement", "find_site", "settle", "region_fill"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["grow_villages(self.conn", "consider_settlement(self.conn"]},
    },
    {
        "name": "Founding kingdoms start apart, with neutral ground between their settlements",
        "wired": {"Data/Kingdoms/kingdom_generator.py": ["keep_clear(", "KINGDOM_SPACING", "NEUTRAL_GROUND"]},
    },
    {
        "name": "The job market: each month the jobless take free places in their town's workplaces (not barracks or dens)",
        "functions": {"Data/Economy/jobs.py": ["hire_jobless", "trade_for_building"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["hire_jobless(self.conn"]},
    },
    {
        "name": "Town treasuries and public works: towns tax their citizens; the ruler's edict enlarges full buildings, with crown loans",
        "functions": {"Data/Buildings/upgrades.py": ["collect_town_taxes", "upgradable", "public_works", "works_need", "places_added", "can_pay",
                                                     "buildable", "construct", "build_new"]},
        "wired": {
            "Data/Simulation/simulation_manager.py": ["collect_town_taxes(self.conn"],
            "Data/Government/edicts.py": ['"public works"', "public_works(conn, kingdom)"],
            "Data/Database/database.py": ['"level"', '"debt"'],
        },
    },
    {
        "name": "Nemesis engine: battle kills are credited; notable killings recorded; renown, champions, epithets; kin hold grievances",
        "tables": ["killings", "grievances"],
        "functions": {"Data/Combat/nemesis.py": ["record_battle", "rank_of", "display_name", "grant_epithet", "hold_grievances",
                                                  "seek_nemeses", "slay"]},
        "wired": {
            "Data/Combat/combat.py": ['context="raid"', 'context="war"', 'context="civil war"', "seek_nemeses(conn, attackers, defenders",
                                      "seek_nemeses(conn, attacker_military, defender_military", "seek_nemeses(conn, loyalists, rebels"],
            "Data/Government/rebellion.py": ['context="rising"', "seek_nemeses(conn, fighters, crown"],
            "Data/Government/occupation.py": ['context="uprising"', "seek_nemeses(conn, rebels, garrison"],
            "Data/Database/database.py": ['"renown"', '"epithet"'],
        },
    },
    {
        "name": "Nemesis engine in peacetime: avengers train, duel, journey to their nemesis, or hire assassins",
        "functions": {"Data/Combat/vendetta.py": ["pursue_vendettas", "arrivals", "train", "hire_assassin"],
                      "Data/Combat/nemesis.py": ["duel", "win_chance"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["pursue_vendettas(self.conn", "arrivals(self.conn"]},
    },
    {
        "name": "Seeds and saves: worlds record their seed; named saves, loading, autosaves; a startup menu",
        "functions": {"Data/Infrastructure/seeds.py": ["new_seed", "parse_seed", "apply_seed", "world_seed"],
                      "Data/Infrastructure/saves.py": ["save_world", "list_saves", "load_save", "delete_save", "new_world", "autosave", "autosave_current"]},
        "wired": {
            "Data/World/world_generator.py": ["apply_seed("],
            "run_world.py": ["startup_menu()", "autosave_current()"],
            "fast_forward.py": ["generate_world(args.seed)"],
            "Data/Database/database.py": ['"seed"'],
        },
    },
    {
        "name": "Families: half the adults over 25 start married; close friends court for six months before marrying; children come over the years",
        "tables": ["courtships"],
        "functions": {"Data/NPCS/family.py": ["found_families", "form_families"]},
        "wired": {"Data/World/world_generator.py": ["found_families(conn"]},
    },
    {
        "name": "Contentment: happiness drifts toward what a person's life is like, not up from where they spend their hours",
        "functions": {"Data/NPCS/contentment.py": ["life_levels", "drift_happiness"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["drift_happiness(self.conn"], "Data/Events/events.py": ["died_day"]},
    },
    {
        "name": "Caravans and sieges: none sets out for a besieged town; one arriving there is seized by the besiegers",
        "functions": {"Data/Infrastructure/caravans.py": ["seize_at_siege"], "Data/Government/campaigns.py": ["besieging_army"]},
        "wired": {"Data/Infrastructure/caravans.py": ["seize_at_siege(conn, caravan)", "besieging_army(conn, destination_city_id)"],
                  "Data/Government/republic.py": ["besieging_army(conn, republic"]},
    },
    {
        "name": "Refugees and slums: with no house free they live in the slums or turn bandit, and move into a house once they can afford one",
        "functions": {"Data/World/slums.py": ["shelter", "rehouse", "slums_of", "slum_ids"]},
        "wired": {"Data/World/villagers.py": ["shelter(conn, family, town_id)"], "Data/Simulation/simulation_manager.py": ["rehouse(self.conn"],
                  "Data/Buildings/upgrades.py": ["type = 'Slums'"], "Data/NPCS/contentment.py": ["SLUMS"]},
    },
    {
        "name": "The bestiary: the Monster Manual's monsters, sorted into raiders, beasts and peaceful ones, with their habitats",
        "functions": {"Data/Monsters/bestiary.py": ["bestiary", "of_this_world", "classify", "habitat_of"]},
    },
    {
        "name": "Monster lairs: placed by terrain and climate, mostly in the wilds, a few weaker ones within kingdoms; on the map",
        "tables": ["monster_lairs"],
        "functions": {"Data/Monsters/lairs.py": ["place_lairs", "ensure_lairs", "candidates"], "Data/Map/map_model.py": ["lair_details"],
                      "Data/Map/map_render.py": ["draw_lairs"]},
        "wired": {"Data/World/world_generator.py": ["place_lairs(conn"], "run_world.py": ["ensure_lairs(connection"]},
    },
    {
        "name": "Fighting by the book: AD&D melee -- to hit by THAC0 against armor class, damage by the monster's ranges, morale",
        "functions": {"Data/Combat/melee.py": ["monster_thac0", "person_thac0", "monster_fighters", "person_fighter", "attack_all", "battle"]},
    },
    {
        "name": "Monster raids: raider lairs strike villages; the fight is by the book; the dead, the food and the lair's losses are kept",
        "functions": {"Data/Monsters/raids.py": ["monster_raids", "raid", "nearest_village", "defenders_of", "plural"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["monster_raids(self.conn"]},
    },
    {
        "name": "Beasts and travelers: beasts near the roads fall on caravans and people on journeys; the beaten lose goods or lives",
        "functions": {"Data/Monsters/travelers.py": ["beasts_on_the_roads", "on_the_road", "ambush"]},
        "wired": {"Data/Simulation/simulation_manager.py": ["beasts_on_the_roads(self.conn"]},
    },
    {
        "name": "Bandits strike caravans only near their camps, at half a percent a day per camp in reach",
        "functions": {"Data/Infrastructure/caravans.py": ["bandit_camps", "bandit_chance"]},
        "wired": {"Data/Infrastructure/caravans.py": ["bandit_chance(camps, here)"], "Data/Government/supply.py": ["bandit_chance(camps, here)"]},
    },
    {
        "name": "Skills in place: adults start with their years of experience; fighting skill alone (two Swordsmanship levels a fighting level) makes a fighter",
        "functions": {"Data/NPCS/experience.py": ["give_experience", "level_for"], "Data/Combat/melee.py": ["fighting_bonus", "fighting_level"]},
        "wired": {"Data/World/world_generator.py": ["give_experience(conn"], "Data/Combat/combat.py": ["fighting_bonus(skills)"]},
    },
    {
        "name": "Magic by the Player's Handbook: casters cast in fights, heal the wounded, bards inspire, druids tend the land and calm beasts",
        "functions": {"Data/Magic/magic.py": ["casting_level", "prepare", "cast_in_battle", "inspire", "heal", "heal_the_wounded",
                                              "bard_towns", "druid_harvest_bonus", "druid_homes", "calmed"]},
        "wired": {"Data/Combat/melee.py": ["cast_in_battle(fighter", "arm_caster(fighter"],
                  "Data/Combat/combat.py": ["CASTING_POWER * casting_level("],
                  "Data/Simulation/simulation_manager.py": ["heal_the_wounded(self.conn"],
                  "Data/Government/campaigns.py": ["heal(conn, sorted(healers"],
                  "Data/NPCS/contentment.py": ["INSPIRED * songs.get("],
                  "Data/World/harvest.py": ["druid_harvest_bonus(conn, world_id)"],
                  "Data/Monsters/travelers.py": ["calmed(lair, homes)"]},
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
        from Data.World.regions import TERRAIN_FARMLAND
        names = load_json("Data/Names/RegionNames.json")
        if not names.get("prefixes"):
            r.fail("RegionNames.json: no prefixes")
        for terrain in TERRAIN_FARMLAND:
            if not names.get("words", {}).get(terrain):
                r.fail(f"RegionNames.json: no name words for {terrain} regions")
    except (OSError, json.JSONDecodeError) as e:
        r.fail(f"could not load RegionNames.json: {e}")
    except ImportError as e:
        r.fail(f"Data/World/regions.py could not be imported ({e})")

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

    world, ok = run_step(r, "generate a fresh world", lambda: generate_world(kingdoms=random.randint(3, 5)))   # the big map, fewer kingdoms: a quick check
    if not ok:
        return

    world_id = world["id"]
    conn = get_connection()
    holder["conn"] = conn

    kingdoms = get_world_kingdoms(conn, world_id)
    npc_count = conn.execute("SELECT COUNT(*) FROM npcs").fetchone()[0]
    seed = conn.execute("SELECT seed FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
    r.info(f"{len(kingdoms)} kingdoms, {npc_count} NPCs, seed {seed}  (rebuild this exact world: python saves.py new {seed})")

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

        # every other kingdom neither short nor in surplus, so this buyer and
        # seller are the only ones in the market (trade buys from whoever has
        # the biggest surplus -- after a harvest that could be anyone)
        for other in kingdoms[2:]:
            for c in get_kingdom_cities(conn, other["id"]):
                city = get_city(conn, c["id"])
                city["resources"]["Food"] = economy.get_target_stock("Food", city)
                save_city(conn, city)

        conn.commit()

        trades = economy.simulate_cross_kingdom_trade_day(conn, world_id)
        conn.commit()

        n = sum(1 for t in trades if t["buyer_id"] == buyer["id"] and t["seller_id"] == seller["id"])   # by id: names need not be unique

        assert n > 0, "a forced Food shortage next to a Food surplus produced no trade at all"

        got_buyer = get_kingdom_favor(conn, buyer["id"], seller["id"])
        got_seller = get_kingdom_favor(conn, seller["id"], buyer["id"])
        # the seller may also have bought something from the buyer the same day
        back = sum(1 for t in trades if t["buyer_id"] == seller["id"] and t["seller_id"] == buyer["id"])
        want_buyer = n * economy.TRADE_FAVOR_GAIN + back * economy.TRADE_FAVOR_LOSS
        want_seller = n * economy.TRADE_FAVOR_LOSS + back * economy.TRADE_FAVOR_GAIN

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
                    if resource == "Food":     # plus last year's surplus, until the first harvest (Data.World.harvest)
                        from Data.World.harvest import days_until_harvest
                        clock = conn.execute("SELECT clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
                        expected = round(expected + city["population"] * days_until_harvest(clock["clock_month"], clock["clock_day"]), 2)
                    if row is None or abs(row["amount"] - expected) > 0.01:
                        wrong.append(f"{city['name']} {resource}: {row['amount'] if row else 'none'} (expected {expected})")
            assert not wrong, f"{len(wrong)} starting stocks are wrong, e.g. " + "; ".join(wrong[:3])

            from Data.Economy.food_market import STARTING_SAVINGS_DAYS
            savings = STARTING_SAVINGS_DAYS * load_json("Data/Economy/ResourceTypes.json")["Food"]["base_price"]
            poorer = conn.execute("SELECT COUNT(*) FROM npcs WHERE profession != 'Child' AND gold != ?", (savings,)).fetchone()[0]
            assert poorer == 0, f"{poorer} adults did not start with {savings} gold of savings"

        run_step(r, "every city starts with its storehouses stocked for its population", starting_stock)

        def regions_made():
            import statistics
            from Data.World import regions as R
            from Data.Kingdoms.kingdom_generator import MAP_WIDTH, MAP_HEIGHT

            regs = R.get_regions(conn, world_id)
            expected = max(1, round(MAP_WIDTH / R.REGION_SIZE)) * max(1, round(MAP_HEIGHT / R.REGION_SIZE))
            assert len(regs) == expected, f"the map should hold {expected} regions, but has {len(regs)}"

            misplaced = 0
            for city in conn.execute("SELECT map_x, map_y, region_id FROM cities"):
                nearest = min(regs, key=lambda g: (g["center_x"] - city["map_x"]) ** 2 + (g["center_y"] - city["map_y"]) ** 2)
                misplaced += nearest["id"] != city["region_id"]
            assert misplaced == 0, f"{misplaced} settlements are not in the region they sit in (or in none)"

            kinds = {t: sum(g["terrain"] == t for g in regs) for t in R.TERRAIN_FARMLAND}
            assert all(kinds.values()), f"not every kind of land appears: {kinds}"

            neighbors = R.region_neighbors(regs)
            by_id = {g["id"]: g for g in regs}
            gap = statistics.mean(abs(by_id[a]["elevation"] - by_id[b]["elevation"]) for a in neighbors for b in neighbors[a])
            # the world's own land: a loose sanity limit (real land almost never passes 0.27,
            # random land is about 0.34) -- random worlds have long tails, so this cannot be strict
            assert gap < 0.30, f"neighboring regions do not resemble each other (height gap {gap:.2f}; random land is about 0.34)"

            # the smoothing itself, strictly: land made from a fixed seed always comes out the same
            # (0.18 smoothed, 0.28 with one pass, 0.35 with none), so this can never fail by chance
            import random as random_module, sqlite3 as sqlite_module
            state = random_module.getstate()
            try:
                scratch = sqlite_module.connect(':memory:')
                scratch.row_factory = sqlite_module.Row
                scratch.executescript(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Data', 'Database', 'schema.sql')).read())
                scratch.execute("INSERT INTO worlds (id, name) VALUES (1, 'w')")
                random_module.seed(2024)
                R.generate_regions(scratch, 1, 2000, 2000)
                fixed = R.get_regions(scratch, 1)
                fixed_neighbors = R.region_neighbors(fixed)
                fixed_by = {g['id']: g for g in fixed}
                fixed_gap = statistics.mean(abs(fixed_by[a]['elevation'] - fixed_by[b]['elevation']) for a in fixed_neighbors for b in fixed_neighbors[a])
            finally:
                random_module.setstate(state)
            assert fixed_gap < 0.23, f"the land is not smoothed as it should be (fixed-seed height gap {fixed_gap:.3f}, should be about 0.18)"

            rivers = [g for g in regs if g["has_river"]]
            stranded = [g["name"] for g in rivers if not any(by_id[n]["has_river"] for n in neighbors[g["id"]])]
            assert rivers and not stranded, f"rivers are missing or broken (stranded: {stranded})"
            heights = [g["elevation"] for g in rivers]
            assert min(heights) < 0.6 and max(heights) - min(heights) >= 0.4, \
                f"rivers do not flow down from the heights into the lowlands (river heights {min(heights):.2f} to {max(heights):.2f})"

            assert R.ensure_regions(conn, world_id) == 0 and len(R.get_regions(conn, world_id)) == len(regs), \
                "running the region setup again changed the world"

        run_step(r, "the land has regions (terrain, climate, rivers) and every settlement sits in one", regions_made)

        def countryside_made():
            import statistics
            from Data.World import countryside as C

            cities = [dict(c) for c in conn.execute(
                """SELECT c.*, r.farmland, r.terrain FROM cities c JOIN regions r ON r.id = c.region_id
                   JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?""", (world_id,))]

            from Data.Government.republic import REPUBLIC_RURAL_SHARE
            types = {row["id"]: row["type"] for row in conn.execute("SELECT id, type FROM kingdoms WHERE world_id = ?", (world_id,))}
            for kingdom_id in {c["kingdom_id"] for c in cities}:
                mine = [c for c in cities if c["kingdom_id"] == kingdom_id]
                rural = sum(C.total_people(c) for c in mine if c["type"] == "Village")
                share = rural / sum(C.total_people(c) for c in mine)
                want = REPUBLIC_RURAL_SHARE if types[kingdom_id] == "Republic" else C.RURAL_SHARE
                assert abs(share - want) <= 0.04, f"a {types[kingdom_id]} has {share:.0%} of its people in villages, not about {want:.0%}"

            villages = [c for c in cities if c["type"] == "Village"]
            assert villages and all(v["households"] >= C.MIN_HOUSEHOLDS for v in villages), "a village has too few households (or none)"
            adults = {row["city_id"]: row["n"] for row in conn.execute("SELECT city_id, COUNT(*) n FROM npcs WHERE age >= 16 GROUP BY city_id")}
            assert all(adults.get(v["id"], 0) == C.named_villagers(v["households"]) for v in villages), \
                "a village's adult named villagers do not match its size (their children come on top)"
            living = {row["city_id"]: row["n"] for row in conn.execute("SELECT city_id, COUNT(*) n FROM npcs GROUP BY city_id")}
            assert all(living.get(v["id"], 0) == v["population"] for v in villages), \
                "a village does not have exactly as many individual NPCs as named villagers"
            assert all(c["households"] == 0 for c in cities if c["type"] != "Village"), "a town or capital was given households"

            land = conn.execute("SELECT AVG(farmland) FROM regions WHERE world_id = ?", (world_id,)).fetchone()[0]
            settled = statistics.mean(c["farmland"] for c in cities)
            # Measured over ten worlds: +0.18 to +0.50 (typically +0.25), with a rare crowded world as low as
            # +0.06. Placement blind to land would score about 0, so +0.03 still catches it without false alarms.
            assert settled > land + 0.03, f"settlements do not favor good land (their land {settled:.2f}, the map {land:.2f})"
            assert all(c["terrain"] != "Mountains" for c in cities if c["type"] == "Capital"), \
                "a capital was placed in the mountains: " + ", ".join(c["name"] for c in cities if c["type"] == "Capital" and c["terrain"] == "Mountains")

            assert C.ensure_households(conn, world_id) == 0, "running the household setup again changed the world"

        run_step(r, "most people live in village households, on land that can feed them", countryside_made)

        def everyone_has_a_personality():
            import statistics
            from Data.NPCS.family import create_child
            from Data.Database.repository import get_npc
            missing = conn.execute("SELECT COUNT(*) FROM npcs WHERE generosity IS NULL OR mercy IS NULL OR boldness IS NULL").fetchone()[0]
            assert missing == 0, f"{missing} NPCs have no personality"
            a, b = [row[0] for row in conn.execute("SELECT id FROM npcs WHERE profession != 'Child' ORDER BY id LIMIT 2")]
            # parents at -60 / +60, well inside the -100..+100 bounds: at +-90 the cap pulls children's
            # average about 7 points toward the middle, and chance then failed the check now and then
            conn.execute("UPDATE npcs SET generosity = -60, mercy = 60, boldness = 0 WHERE id IN (?, ?)", (a, b))
            conn.commit()
            kids = [create_child(conn, get_npc(conn, a), get_npc(conn, b), "Town") for _ in range(60)]   # enough that chance cannot fail it
            assert abs(statistics.mean(k["generosity"] for k in kids) + 60) < 15 and abs(statistics.mean(k["mercy"] for k in kids) - 60) < 15, \
                "children do not take after their parents' personalities"
            conn.execute(f"DELETE FROM npcs WHERE id IN ({','.join(str(k['id']) for k in kids)})")
            conn.commit()

        run_step(r, "every NPC has a personality, and children take after their parents", everyone_has_a_personality)

        def counties_made():
            for kingdom in conn.execute("SELECT id, capital_city_id, type FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():
                if kingdom["type"] == "Republic":
                    assert not conn.execute("SELECT 1 FROM provinces WHERE kingdom_id = ?", (kingdom["id"],)).fetchone(), \
                        "a republic was given counties"
                    continue
                cities = [dict(c) for c in conn.execute("SELECT * FROM cities WHERE kingdom_id = ?", (kingdom["id"],))]
                seats = {row["seat_city_id"]: row["id"] for row in conn.execute(
                    "SELECT id, seat_city_id FROM provinces WHERE kingdom_id = ?", (kingdom["id"],))}
                towns = [c for c in cities if c["type"] != "Village" and c["id"] != kingdom["capital_city_id"]]
                assert sorted(seats) == sorted(c["id"] for c in towns), "not every town besides the capital is a county seat"
                centers = [c for c in cities if c["id"] in seats or c["id"] == kingdom["capital_city_id"]]
                for village in (c for c in cities if c["type"] == "Village"):
                    nearest = min(centers, key=lambda c: ((c["map_x"] - village["map_x"]) ** 2 + (c["map_y"] - village["map_y"]) ** 2, c["id"]))
                    assert village["province_id"] == seats.get(nearest["id"]), \
                        f"{village['name']} is not under its nearest seat (or the crown, if the capital is nearest)"

        run_step(r, "every town besides the capital is a county; villages fall under the nearest seat or the crown", counties_made)

        def kingdoms_start_apart():
            # measured on fresh worlds: the closest settlements of two kingdoms start 345-716 apart (the
            # neutral ground is 375; a crowded six-kingdom map cannot always keep it all), where unspaced
            # placement put kingdoms touching (7-17 apart). Crowded six-kingdom maps have gone as low as 195, so the
    # guard is 100: ten times the old touching distance, with room for crowded maps.
            import math
            rows = conn.execute("SELECT kingdom_id, map_x, map_y FROM cities").fetchall()
            gap = min(math.dist((a["map_x"], a["map_y"]), (b["map_x"], b["map_y"])) for a in rows for b in rows if a["kingdom_id"] != b["kingdom_id"])
            assert gap >= 100, f"two founding kingdoms' settlements are only {gap:.0f} apart -- kingdoms are not keeping neutral ground"

        run_step(r, "founding kingdoms start apart, with neutral ground between them", kingdoms_start_apart)

        def the_bestiary():
            from Data.Monsters.bestiary import bestiary, of_this_world, by_name
            kinds = {m["kind"] for m in bestiary()}
            assert {"raider", "beast", "peaceful", "aquatic", "planar"} <= kinds, f"the bestiary is missing kinds of monster: {kinds}"
            assert len(of_this_world()) >= 200, f"only {len(of_this_world())} monsters live in this world"
            for name, kind in (("Orc", "raider"), ("Frost Giant", "raider"), ("Troll", "raider"), ("Wolf", "beast"), ("Owlbear", "beast"),
                               ("Unicorn", "peaceful"), ("Shark", "aquatic"), ("Demon, Type I (Vrock)", "planar")):
                m = by_name(name)
                assert m and m["kind"] == kind, f"{name} is sorted as {m and m['kind']}, not {kind}"
            assert all(m["stats"]["ac"] is not None for m in of_this_world()), "a monster of this world has no armor class"

        run_step(r, "the bestiary sorts the Monster Manual into raiders, beasts and peaceful monsters, with habitats", the_bestiary)

        def monster_lairs():
            import math as math_module
            from Data.Monsters.bestiary import by_name
            import Data.Monsters.lairs as LR
            lairs = conn.execute("""SELECT l.*, r.terrain, r.climate FROM monster_lairs l JOIN regions r ON r.id = l.region_id
                                    WHERE l.world_id = ?""", (world_id,)).fetchall()
            assert len(lairs) >= 50, f"only {len(lairs)} monster lairs in the world"
            towns = conn.execute("SELECT map_x, map_y FROM cities").fetchall()
            for lair in lairs:
                m = by_name(lair["monster"])
                assert m and m["kind"] in ("raider", "beast", "peaceful"), f"a lair of {lair['monster']}, which is not a monster of this world"
                assert lair["terrain"] in m["terrains"], f"{lair['monster']} dens in {lair['terrain']}, not its habitat"
                assert not (m["cold_only"] and lair["climate"] != "Cold"), f"{lair['monster']}, a creature of the cold, dens in {lair['climate']} lands"
                if lair["within_kingdom_id"]:
                    assert (m["stats"]["hit_dice"] or 99) <= LR.WITHIN_MAX_HIT_DICE and m["kind"] != "peaceful", \
                        f"{lair['monster']} lurks within a kingdom, but is too strong (or peaceful) for that"
                gap = min(math_module.dist((t[0], t[1]), (lair["map_x"], lair["map_y"])) for t in towns)
                assert gap >= LR.CLEAR_OF_SETTLEMENTS - 1, f"a lair of {lair['monster']} is only {gap:.0f} from a settlement"
            wild = sum(1 for lair in lairs if not lair["within_kingdom_id"])
            assert wild >= 0.8 * len(lairs), f"only {wild} of {len(lairs)} lairs are in the wilds"

        run_step(r, "monster lairs fit their habitat and climate, most in the wilds, a few weaker ones within kingdoms", monster_lairs)

        def fighting_by_the_book():
            import Data.Combat.melee as ML
            from Data.Monsters.bestiary import by_name
            from Data.Monsters.lairs import hit_points
            assert [ML.monster_thac0(hd) for hd in (1, 2, 6, 10, 16)] == [19, 16, 13, 10, 7], "the monsters' to-hit table is wrong"
            assert [ML.person_thac0(lv) for lv in (0, 1, 3, 9)] == [21, 20, 18, 12], "the fighters' to-hit table is wrong"
            orc = ML.monster_fighters(by_name("Orc"), 1, 4.5)[0]
            dummy = ML.Fighter("dummy", "x", 10 ** 9, 7, 20, [(1, 1)])
            hits = sum(1 for _ in range(4000) if ML.attack_all(orc, dummy) > 0) / 4000
            assert abs(hits - 0.45) < 0.03, f"an orc hits armor class 7 {hits:.0%} of the time, not 45%"
            people = lambda n, armored: [ML.person_fighter({"name": "s", "profession": "Soldier", "id": i}, armored=armored,
                                                           skills={"Swordsmanship": 1}) for i in range(n)]
            orcs = lambda: ML.monster_fighters(by_name("Orc"), 10, hit_points(by_name("Orc")))
            plain = sum(ML.battle(orcs(), people(10, False))["winner"] == "b" for _ in range(300))
            mailed = sum(ML.battle(orcs(), people(10, True))["winner"] == "b" for _ in range(300))
            assert mailed > plain, f"chain mail did not help soldiers against orcs ({plain} wins without, {mailed} with)"
            r = ML.battle(ML.monster_fighters(by_name("Wolf"), 30, 1000), people(4, False))
            assert r["winner"] == "a" and len(r["dead_b"]) >= 2, "a side that lost half its number did not break"
            skel = by_name("Skeleton")
            r = ML.battle(ML.monster_fighters(skel, 4, 1), ML.monster_fighters(by_name("Ogre"), 1, 10 ** 6))
            assert r["fled"] != "a" and not r["a"], "mindless skeletons broke and fled"

        run_step(r, "fighting by the book: to hit by THAC0 against armor class, monsters' own damage, armor helps, morale breaks the living", fighting_by_the_book)

        def skills_in_place():
            import Data.Combat.melee as ML
            from Data.Combat.combat import get_combat_power
            from Data.NPCS.experience import level_for, experience_for
            young, old = (conn.execute(f"""SELECT AVG(s.level) FROM npc_skills s JOIN npcs n ON n.id = s.npc_id
                                           WHERE n.alive = 1 AND n.age {cond}""").fetchone()[0] for cond in ("BETWEEN 16 AND 20", ">= 40"))
            assert young and old and old > young + 2, f"older people do not start more skilled than the young ({young} vs {old})"
            expected = level_for(experience_for(30, 1.0))[0]           # at a village's pace, the slowest
            thirty = conn.execute("""SELECT MIN(s.level) FROM npc_skills s JOIN npcs n ON n.id = s.npc_id
                                     WHERE n.alive = 1 AND n.age = 30""").fetchone()[0]
            assert thirty is None or thirty >= expected, f"a 30-year-old's skills start at {thirty}, not the {expected} their years of work earned"
            assert [ML.fighting_bonus({"Swordsmanship": lv}) for lv in (1, 2, 3, 9, 15)] == [0, 0, 1, 4, 7], "Swordsmanship does not give a fighting level per two levels"
            assert ML.fighting_level(3, {"Swordsmanship": 40}) == ML.MAX_FIGHTING_LEVEL, "fighting level is not capped"
            farmer = {"profession": "Farmer", "skills": {"Farming": {"level": 15}, "Animal Care": {"level": 15}}}
            soldier = {"profession": "Soldier", "skills": {"Swordsmanship": {"level": 15}}}
            assert get_combat_power(farmer) == 1, "a farmer's Farming counted as fighting strength"
            town = 360 * 10 * 1.25
            assert [level_for(y * town)[0] for y in (1, 10, 40)] == [3, 9, 13], "skills do not rise at the agreed pace (3 after a year, 9 after ten, 13 after forty)"
            assert get_combat_power(soldier) > get_combat_power({"profession": "Soldier", "skills": {}}), "a trained soldier is no stronger than a recruit"
            assert not conn.execute("SELECT 1 FROM npcs WHERE profession = 'Captian'").fetchone(), "the Captain trade is still misspelled"

        run_step(r, "skills in place: adults start with their years of work, and only fighting skill makes a fighter", skills_in_place)

        def magic_works():
            import Data.Combat.melee as ML
            from Data.Magic import magic as MG
            from Data.Monsters.bestiary import by_name
            # who casts: two skill levels a casting level, hedge casters at half, no class no magic
            assert MG.casting_level("Wizard", "Mage", {"Arcane Knowledge": 13}) == 7, "a master mage does not cast at level 7"
            assert MG.casting_level("Druid", "Farmer", {"Healing": 13}) == 3, "a farmer-druid does not cast as a hedge caster (half)"
            assert MG.casting_level("Fighter", "Mage", {"Arcane Knowledge": 13}) == 0, "a fighter cast spells"
            assert MG.per_day("Wizard", 5) == [4, 2, 1] and MG.per_day("Sorcerer", 5) == [5, 3, 2], "spells a day do not follow the book"
            assert MG.known("Sorcerer", 7) == MG.known("Sorcerer", 7), "a sorcerer forgot their spells overnight"
            assert all(len(v) <= MG.SORCERER_KINDS for v in MG.known("Sorcerer", 7).values()), "a sorcerer knows the whole wizard list"
            # in fights: a master wizard turns a fight soldiers would lose
            mage = lambda: ML.person_fighter({"id": 9, "name": "w", "profession": "Mage", "class": "Wizard"}, skills={"Arcane Knowledge": 13})
            assert mage().spells and mage().caster_level == 7, "a mage came to the fight without spells"
            soldiers = lambda n: [ML.person_fighter({"id": i, "name": "s", "profession": "Soldier", "class": "None"},
                                                    skills={"Swordsmanship": 9}) for i in range(n)]
            orcs = lambda: ML.monster_fighters(by_name("Orc"), 20, 5)
            plain = sum(ML.battle(orcs(), soldiers(6))["winner"] == "b" for _ in range(200))
            magic = sum(ML.battle(orcs(), soldiers(5) + [mage()])["winner"] == "b" for _ in range(200))
            assert magic > plain + 60, f"a master wizard did not turn the fight ({plain} wins without, {magic} with)"
            sleepy = ML.monster_fighters(by_name("Orc"), 4, 5)
            MG.cast(mage(), "Sleep", {"effect": "sleep", "hit_dice": [16, 16], "max_hit_dice_each": 4}, [], sleepy, {}, {})
            assert all(o.held for o in sleepy), "Sleep did not put four orcs to sleep"
            # bards sing, clerics heal in the fight
            bard = ML.person_fighter({"id": 8, "name": "b", "profession": "Mage", "class": "Bard"}, skills={"Charisma": 13})
            side = soldiers(3) + [bard]
            state = {"morale": 0.5, "blessed": set()}
            assert MG.inspire(side, state) == 2 and state["morale"] > 0.5 and side[0].to_hit == 2, "a master bard's song did nothing"
            # healing in town: a cleric heals the worst wounded
            from Data.Infrastructure.travel import get_traveling_npc_ids
            from Data.Government.armies import get_army_npc_ids
            away = set(get_traveling_npc_ids(conn, world_id)) | set(get_army_npc_ids(conn, world_id))   # healers heal at home
            healer = next((h for h in conn.execute(
                """SELECT n.id, n.city_id FROM npcs n WHERE n.alive = 1 AND n.class = 'Cleric' AND n.age >= 16
                   AND (SELECT level FROM npc_skills s WHERE s.npc_id = n.id AND s.skill_name = 'Religion') >= 3""")
                           if h[0] not in away), None)
            patient = healer and next((p[0] for p in conn.execute("SELECT id FROM npcs WHERE alive = 1 AND city_id = ? AND id != ?",
                                                                  (healer[1], healer[0])) if p[0] not in away), None)
            if patient:
                conn.execute("UPDATE npcs SET health = 100 WHERE city_id = ?", (healer[1],))
                conn.execute("UPDATE npcs SET health = 30 WHERE id = ?", (patient,))
                done = MG.heal_the_wounded(conn, world_id)
                after = conn.execute("SELECT health FROM npcs WHERE id = ?", (patient,)).fetchone()[0]
                assert after > 30 and done.get(healer[1]), f"a town's cleric did not heal its wounded (health {after})"
            # druids tend fields (never more than Plant Growth's share) and calm beasts near them
            bonus = MG.druid_harvest_bonus(conn, world_id)
            assert all(0 < b <= MG.PLANT_GROWTH_HARVEST for b in bonus.values()), "a druid's harvest bonus is out of bounds"
            homes = MG.druid_homes(conn, world_id)
            if homes:
                x, y = homes[0]
                assert MG.calmed({"map_x": x + 10, "map_y": y}, homes), "a lair beside a druid's home is not calmed"
            assert not MG.calmed({"map_x": -10 ** 6, "map_y": -10 ** 6}, homes), "a lair far from any druid is calmed"

        run_step(r, "magic by the book: casters cast in fights, heal the wounded, bards inspire, druids tend fields and calm beasts", magic_works)

        def map_draws():
            # the map reads the world as it now is: a schema change that breaks it shows up here
            try:
                import os as os_module
                os_module.environ.setdefault("SDL_VIDEODRIVER", "dummy")
                import pygame, numpy  # noqa: F401
            except ImportError:
                print("  --    (map not checked: pygame-ce and numpy are not installed -- see Data/Map/requirements.txt)")
                return
            from Data.Map import map_model, map_render       # a package import, so editors (Pylance) can follow it
            from Data.Database import database as database_module
            pygame.init()
            view = map_model.load_world(map_model.open_world(database_module.DB_FILE))
            assert view.regions and view.settlements and view.roads, "the map found no regions, settlements or roads"
            terrain, territory = map_render.build_terrain(view, 200), map_render.build_territory(view, 200)
            canvas = pygame.Surface((400, 400))
            canvas.blit(terrain, (0, 0)); canvas.blit(territory, (0, 0))
            font = pygame.font.SysFont(None, 14)
            to_screen = lambda x, y: (int(x * 0.2), int(y * 0.2))
            map_render.draw_rivers(canvas, view, to_screen, 0.2)
            map_render.draw_roads(canvas, view, to_screen, 0.2)
            map_render.draw_settlements(canvas, view, to_screen, 0.2, font, font)
            map_render.draw_kingdom_names(canvas, view, to_screen, 0.2, font)
            moving = map_model.load_movement(map_model.open_world(database_module.DB_FILE), view)   # armies, sieges, caravans
            map_render.draw_movement(canvas, view, moving, to_screen, 0.2, font)
            map_render.draw_lairs(canvas, view, to_screen, 0.2)
            if view.lairs:
                lair_conn = map_model.open_world(database_module.DB_FILE)
                assert map_model.lair_details(lair_conn, view.lairs[0][0])['rows'], 'a lair panel came back empty'
                lair_conn.close()
            map_render.draw_legend(canvas, font, (0, 0))
            # a click's details panel: a town, a kingdom and (if any) an army
            panel_conn = map_model.open_world(database_module.DB_FILE)
            town_id = next(s.id for s in view.settlements.values() if s.kind != 'Village')
            for details in (map_model.town_details(panel_conn, town_id), map_model.kingdom_details(panel_conn, next(iter(view.kingdoms))),
                            *[map_model.army_details(panel_conn, a.id) for a in moving.armies[:1]]):
                assert details and details['title'] and details['rows'], 'a details panel came back empty'
                map_render.draw_panel(canvas, details, (font, font, font), 0)
            panel_conn.close()

        run_step(r, "the map reads the world and draws its land, realms, roads, settlements, armies, sieges, caravans, and a click's details", map_draws)

        def families():
            import Data.NPCS.family as F
            from Data.Database.repository import get_city, set_relationship
            from Data.Government.edicts import today

            over = conn.execute("SELECT COUNT(*) FROM npcs WHERE alive = 1 AND age >= ?", (F.FOUNDING_AGE,)).fetchone()[0]
            wed = conn.execute("SELECT COUNT(*) FROM npcs WHERE alive = 1 AND age >= ? AND spouse_id IS NOT NULL", (F.FOUNDING_AGE,)).fetchone()[0]
            assert 0.4 <= wed / over <= 0.6, f"{wed / over:.0%} of adults over {F.FOUNDING_AGE} start married, not about half"
            assert not conn.execute("SELECT 1 FROM npcs a JOIN npcs b ON b.id = a.spouse_id WHERE a.alive = 1 AND b.alive = 1 AND b.spouse_id IS NOT a.id").fetchone(), \
        "a marriage is one-sided"   # living couples: a widow's late spouse still names them, rightly
            couples = conn.execute("SELECT COUNT(*) FROM npcs a JOIN npcs b ON b.id = a.spouse_id WHERE a.id < b.id AND a.alive = 1 AND b.alive = 1").fetchone()[0]
            together = conn.execute("SELECT COUNT(*) FROM npcs a JOIN npcs b ON b.id = a.spouse_id WHERE a.id < b.id AND a.alive = 1 AND b.alive = 1 AND a.home_id = b.home_id").fetchone()[0]
            assert together >= 0.9 * couples, f"only {together} of {couples} founding couples live together"

            town = conn.execute("""SELECT city_id FROM npcs WHERE alive = 1 AND spouse_id IS NULL AND age BETWEEN 20 AND 40
                                   GROUP BY city_id ORDER BY COUNT(*) DESC LIMIT 1""").fetchone()[0]
            singles = [row[0] for row in conn.execute(
                "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND spouse_id IS NULL AND age BETWEEN 20 AND 40 ORDER BY id LIMIT 4", (town,))]
            a, b, c, d = singles
            ids = ",".join(str(i) for i in singles)
            conn.execute(f"DELETE FROM relationships WHERE npc_a_id IN ({ids}) OR npc_b_id IN ({ids})")
            conn.execute(f"UPDATE npcs SET age = 25 WHERE id IN ({ids})")
            set_relationship(conn, a, b, "Friend", 85)                      # a and b are close friends; c and d strangers
            conn.execute("DELETE FROM courtships")
            conn.commit()
            saved = (F.COURTSHIP_CHANCE, F.WEDDING_CHANCE, F.BIRTH_CHANCE)
            F.COURTSHIP_CHANCE, F.WEDDING_CHANCE, F.BIRTH_CHANCE = 1.0, 1.0, 0.0
            married = lambda x: conn.execute("SELECT spouse_id FROM npcs WHERE id = ?", (x,)).fetchone()[0]
            try:
                F.form_families(conn, get_city(conn, town))
                pair = (min(a, b), max(a, b))
                assert conn.execute("SELECT 1 FROM courtships WHERE npc_a_id = ? AND npc_b_id = ?", pair).fetchone(), "close friends did not begin to court"
                assert not married(c) and not married(d), "strangers married"
                assert not married(a), "a new courtship went straight to the altar"
                day = today(conn, world_id)
                conn.execute("UPDATE courtships SET since_day = ? WHERE npc_a_id = ?", (day - 2 * F.DAYS_PER_MONTH, pair[0]))
                conn.commit()
                F.form_families(conn, get_city(conn, town))
                assert not married(a), "a courtship of two months ended in marriage (it takes at least six)"
                conn.execute("UPDATE courtships SET since_day = ? WHERE npc_a_id = ?", (day - F.COURTSHIP_MONTHS * F.DAYS_PER_MONTH, pair[0]))
                conn.commit()
                F.form_families(conn, get_city(conn, town))
                assert married(a) == b and married(b) == a, "a courtship of six months did not end in marriage"

                F.WEDDING_CHANCE, F.BIRTH_CHANCE = 0.0, 1.0
                before = conn.execute("SELECT COUNT(*) FROM npcs WHERE parent_a_id IN (?, ?) AND parent_b_id IN (?, ?)", (a, b, a, b)).fetchone()[0]
                F.form_families(conn, get_city(conn, town))
                after = conn.execute("SELECT COUNT(*) FROM npcs WHERE parent_a_id IN (?, ?) AND parent_b_id IN (?, ?)", (a, b, a, b)).fetchone()[0]
                assert after == before + 1, "the married couple did not have a child"
            finally:
                F.COURTSHIP_CHANCE, F.WEDDING_CHANCE, F.BIRTH_CHANCE = saved
                conn.execute("DELETE FROM courtships")
                conn.commit()

        run_step(r, "half the adults over 25 start married; close friends court for six months before marrying; married couples have children", families)

        def contentment():
            import Data.NPCS.contentment as CT
            from Data.Government.edicts import today
            pair = conn.execute("""SELECT a.id, a.spouse_id FROM npcs a JOIN npcs b ON b.id = a.spouse_id
                                   WHERE a.alive = 1 AND b.alive = 1 AND a.age BETWEEN 25 AND 50 AND a.home_id IS NOT NULL LIMIT 1""").fetchone()
            content, spouse = pair["id"], pair["spouse_id"]
            lonely = conn.execute("SELECT id FROM npcs WHERE alive = 1 AND age BETWEEN 25 AND 50 AND spouse_id IS NULL AND id != ? LIMIT 1", (spouse,)).fetchone()[0]
            smith = conn.execute("SELECT profession FROM npcs WHERE alive = 1 AND workplace_id IS NOT NULL AND profession NOT IN ('Laborer', 'Child') LIMIT 1").fetchone()[0]
            conn.execute("UPDATE npcs SET fed = 1, gold = 500, needs_stress = 0, needs_happiness = 100, profession = ? WHERE id = ?", (smith, content))
            conn.execute("UPDATE npcs SET fed = 0, gold = 0, needs_stress = 60, needs_happiness = 100, profession = 'Laborer', workplace_id = NULL, home_id = NULL WHERE id = ?", (lonely,))
            conn.commit()
            songs, CT.INSPIRED = CT.INSPIRED, 0                      # a bard in town would lift both: this step weighs life alone
            try:
                for _ in range(30):
                    CT.drift_happiness(conn, world_id, today(conn, world_id))
            finally:
                CT.INSPIRED = songs
            happy = lambda i: conn.execute("SELECT needs_happiness FROM npcs WHERE id = ?", (i,)).fetchone()[0]
            assert 70 <= happy(content) < 100, f"a fed, housed, married tradesman's happiness did not settle where life puts it ({happy(content)})"
            assert happy(lonely) <= 40, f"a hungry, homeless, penniless laborer is still at {happy(lonely)} after a month"
            assert not conn.execute("SELECT 1 FROM npcs WHERE alive = 1 AND profession = 'Unemployed'").fetchone(), "someone is still 'Unemployed' (they are Laborers)"

        run_step(r, "happiness follows life: a fed, housed, married tradesman stays content; a hungry homeless laborer sinks", contentment)

        def seeds_and_saves():
            import sqlite3 as sqlite_module
            import tempfile
            from pathlib import Path as PathType
            import Data.Infrastructure.saves as SV
            from Data.Database import database as database_module

            assert conn.execute("SELECT seed FROM worlds WHERE id = ?", (world_id,)).fetchone()[0] is not None, "the world did not record its seed"

            # a round trip on a temporary copy of this world, in a temporary Saves folder (nothing real is touched)
            scratch = PathType(tempfile.mkdtemp())
            copy = scratch / "world.db"
            target = sqlite_module.connect(copy)
            conn.backup(target)
            target.close()
            saved_dir, saved_db = SV.SAVES_DIR, database_module.DB_FILE
            SV.SAVES_DIR, database_module.DB_FILE = scratch / "Saves", copy
            try:
                summary = SV.save_world("check")
                assert summary["seed"] is not None and summary["kingdoms"], "a save's summary is missing the world's seed or kingdoms"
                moved = sqlite_module.connect(copy)
                moved.execute("UPDATE worlds SET clock_year = 99")
                moved.commit()
                moved.close()
                SV.load_save("check")
                back = sqlite_module.connect(copy)
                year = back.execute("SELECT clock_year FROM worlds").fetchone()[0]
                back.close()
                assert year != 99, "loading a save did not put the saved world back"
                assert SV.find_save("before-load"), "loading did not keep the world it replaced"
                for year in range(1, 5):
                    moved = sqlite_module.connect(copy)
                    moved.execute("UPDATE worlds SET clock_year = ?", (year,))
                    moved.commit()
                    moved.close()
                    SV.autosave_current()
                autos = sorted(s["name"] for s in SV.list_saves() if s.get("autosave"))
                assert autos == [f"autosave-year-{y}" for y in (2, 3, 4)], f"autosaves were not kept to the last three: {autos}"
            finally:
                SV.SAVES_DIR, database_module.DB_FILE = saved_dir, saved_db

        run_step(r, "the world records its seed; a save, a load and yearly autosaves (only the last three kept)", seeds_and_saves)

        def harvests_work():
            from Data.World import harvest as H
            from Data.Economy.economy import get_target_stock

            clock = conn.execute("SELECT clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
            days = H.days_until_harvest(clock["clock_month"], clock["clock_day"])
            villages = [dict(v) for v in conn.execute(
                """SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                   WHERE k.world_id = ? AND c.households > 0""", (world_id,))]
            assert villages and all(abs(v["household_store"] - v["households"] * H.FAMILY_SIZE * days) < 0.01 for v in villages), \
                f"a new world's villages should start with {days} days of food for their households"

            def food(city_id):
                return conn.execute(
                    "SELECT COALESCE(SUM(amount), 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (city_id,)
                ).fetchone()[0]

            capitals = {k["id"]: k["capital_city_id"] for k in conn.execute("SELECT id, capital_city_id FROM kingdoms WHERE world_id = ?", (world_id,))}
            before = {k: food(c) for k, c in capitals.items()}

            from Data.Simulation.simulation_manager import SimulationManager
            seasons = SimulationManager(conn, world_id)     # this step runs before the shared manager exists
            month = seasons.clock.month
            seasons.clock.month = H.HARVEST_MONTH          # the first day of Autumn
            try:
                seasons.on_season()
            finally:
                seasons.clock.month = month

            year = seasons.clock.year
            weather = {row["region_id"]: row["factor"] for row in conn.execute(
                "SELECT h.region_id, h.factor FROM harvests h JOIN regions r ON r.id = h.region_id WHERE r.world_id = ? AND h.year = ?",
                (world_id, year))}
            regions = conn.execute("SELECT id, farmland FROM regions WHERE world_id = ?", (world_id,)).fetchall()
            assert len(weather) == len(regions), "the Autumn hook did not bring in a harvest with weather for every region"

            # each kingdom's food prices follow its own harvest: up 20% after a bad one, unchanged otherwise
            for row in conn.execute("SELECT kingdom_id, description FROM events WHERE type = 'Harvest'"):
                level = conn.execute("SELECT price_level FROM kingdoms WHERE id = ?", (row["kingdom_id"],)).fetchone()[0]
                expected = 1.2 if " a bad harvest" in row["description"] else 1.0
                assert abs(level - expected) < 1e-6, f"after its harvest a kingdom's food prices are {level}x, not {expected}x"

            farmland = {r["id"]: r["farmland"] for r in regions}
            from Data.Government.feudal import LORD_SHARE
            from Data.Magic.magic import druid_harvest_bonus
            tended = druid_harvest_bonus(conn, world_id)               # druids tend some villages' fields
            tax = {}
            for v in villages:
                reaped = (v["households"] * H.HARVEST_PER_HOUSEHOLD * farmland[v["region_id"]] * weather[v["region_id"]]
                          * (1 + tended.get(v["id"], 0)))
                crowns_share = 1.0 if v["province_id"] is None else 1.0 - LORD_SHARE   # counties' lords keep theirs
                tax[v["kingdom_id"]] = tax.get(v["kingdom_id"], 0) + reaped * H.GRAIN_TAX_SHARE * crowns_share
            for kingdom_id, expected in tax.items():
                got = food(capitals[kingdom_id]) - before[kingdom_id]
                assert abs(got - expected) < 1, f"a capital received {got:.0f} Food in grain tax, not {expected:.0f} (the crown's share of its villages' 10% grain tax)"

            village = {"households": 10, "household_store": 0.0, "hungry_days": 0, "resources": {"Food": 20.0}}
            assert H.feed_households(village) == 30 and village["hungry_days"] == 1, \
                "households with no store and too little at market did not go hungry"

            city = {"population": 20, "households": 10, "household_store": 0.0, "resources": {}}
            hungry_target = get_target_stock("Food", city)
            city["household_store"] = 10000.0
            assert hungry_target > get_target_stock("Food", city), \
                "trade does not count a village's households once their own food has run out"

        run_step(r, "harvests come in at Autumn: seed, grain tax, the year's food, and surplus for market", harvests_work)

        starving_probe = {}    # a starving village set up below, checked after the month tick

        def villagers_enter_story():
            import Data.World.villagers as VL
            from Data.Database.repository import get_npc, get_kingdom, get_city, save_city
            from Data.Government import armies as A

            village = dict(conn.execute(
                """SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                   WHERE k.world_id = ? AND c.households >= 20 ORDER BY c.households DESC LIMIT 1""", (world_id,)
            ).fetchone())

            def free_houses():
                return conn.execute(
                    """SELECT COUNT(*) FROM buildings b JOIN cities c ON c.id = b.city_id
                       WHERE c.kingdom_id = ? AND c.type != 'Village' AND b.type = 'House'
                       AND (SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1) < b.capacity""",
                    (village["kingdom_id"],)
                ).fetchone()[0]

            def townsfolk():
                return conn.execute(
                    "SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND c.type != 'Village'",
                    (village["kingdom_id"],)
                ).fetchone()[0]

            camps = conn.execute(
                "SELECT COUNT(*) FROM buildings b JOIN cities c ON c.id = b.city_id WHERE b.type = 'Bandit Camp' AND c.kingdom_id = ?",
                (village["kingdom_id"],)
            ).fetchone()[0]

            room, people_before = free_houses(), townsfolk()
            conn.execute("UPDATE cities SET hungry_days = ? WHERE id = ?", (VL.BANDITRY_AFTER_HUNGRY_DAYS + 5, village["id"]))
            conn.commit()

            chance = VL.BANDITRY_CHANCE_PER_MONTH
            VL.BANDITRY_CHANCE_PER_MONTH = 1.0          # make banditry certain for this test
            import Data.World.slums as SL_module
            refugee_bandits, SL_module.BANDIT_CHANCE = SL_module.BANDIT_CHANCE, 0.0   # every fleeing family arrives whole (refugees' banditry is tested with the slums)
            try:
                report = VL.village_hardship(conn, world_id)
            finally:
                VL.BANDITRY_CHANCE_PER_MONTH = chance
                SL_module.BANDIT_CHANCE = refugee_bandits

            fled, bandits = report["households_fled"], report["bandits"]
            after = conn.execute("SELECT households FROM cities WHERE id = ?", (village["id"],)).fetchone()[0]

            assert fled >= 1 or room == 0, "a starving village lost no households, though the towns had free houses"
            assert townsfolk() - people_before >= 5 * fled, "fleeing households did not arrive in the towns as families of five"
            assert bandits >= 1 or camps == 0, "a starving village lost no young men to banditry, though the kingdom has Bandit Camps"
            lost = village["households"] - after
            assert abs(lost - (fled + bandits / VL.FAMILY_SIZE)) < 1e-6, \
                f"the village's households do not add up ({lost:.2f} lost for {fled} families and {bandits} bandits)"

            if fled:
                couple = conn.execute(
                    """SELECT id, spouse_id FROM npcs WHERE spouse_id IS NOT NULL AND profession = 'Laborer'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                partner = get_npc(conn, couple["spouse_id"])
                assert partner["spouse"] == couple["id"], "a fleeing couple did not arrive married"

            kingdom = get_kingdom(conn, village["kingdom_id"])
            capital = get_city(conn, kingdom["capital"])
            capital["resources"].update({"Food": 99999.0, "Weapons": 999.0})
            save_city(conn, capital)
            army = A.raise_army(conn, kingdom, get_city(conn, kingdom["capital"]), size=1, levies=4)
            levied = [row[0] for row in conn.execute("SELECT npc_id FROM army_members WHERE army_id = ? AND levied = 1", (army["id"],))]
            assert len(levied) == 4, f"an army raised with 4 levies has {len(levied)}"
            assert all(get_npc(conn, i)["profession"] == "Soldier" for i in levied), "levied men are not serving as Soldiers"
            assert A.disband_army(conn, army["id"]), "the army with levies could not disband at home"
            assert all(get_npc(conn, i)["profession"] in ("Farmer", "Laborer") for i in levied), \
                "levies did not go back to the land when their army disbanded"


        run_step(r, "hungry villages lose families to the towns and men to banditry; levies serve and go home", villagers_enter_story)

        def food_is_bought():
            import Data.Economy.food_market as FM
            from Data.Database.repository import get_city, get_npc, save_npc
            from Data.Simulation.simulation_manager import SimulationManager

            town = get_city(conn, conn.execute(
                """SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                   WHERE k.world_id = ? AND c.type != 'Village' ORDER BY c.population DESC LIMIT 1""", (world_id,)
            ).fetchone()[0])
            people = {row["id"]: dict(row) for row in conn.execute(
                "SELECT id, gold, fed, needs_stress, needs_happiness FROM npcs WHERE city_id = ? AND alive = 1", (town["id"],))}
            ids = ",".join(map(str, people))
            wealth_before = conn.execute("SELECT wealth FROM cities WHERE id = ?", (town["id"],)).fetchone()[0]
            food_before = town["resources"].get("Food", 0)

            def setup(gold, food, wealth):
                conn.execute(f"UPDATE npcs SET gold = ?, fed = 1 WHERE id IN ({ids})", (gold,))
                conn.execute("UPDATE cities SET wealth = ? WHERE id = ?", (wealth, town["id"]))
                conn.commit()
                town["resources"]["Food"] = food

            def everyone_ate(full=True):
                fed = [row[0] for row in conn.execute(f"SELECT fed FROM npcs WHERE id IN ({ids})")]
                return all(f >= 0.999 for f in fed) if full else all(f == 0 for f in fed)

            try:
                setup(1000, 10 ** 7, 0)
                FM.buy_food(conn, town, has_temple=False)
                assert everyone_ate(), "with money and food in the market, not everyone ate"

                setup(0, 10 ** 7, 0)
                FM.buy_food(conn, town, has_temple=True)
                assert everyone_ate(False), "people with no money ate, though there was no charity to pay for it"

                setup(0, 10 ** 7, 10 ** 9)
                FM.buy_food(conn, town, has_temple=True)
                assert everyone_ate(), "a temple with charity funds did not feed the penniless"

                setup(0, 10 ** 7, 10 ** 9)
                FM.buy_food(conn, town, has_temple=False)
                assert everyone_ate(False), "without a working temple, the penniless were still given alms"

                setup(1000, 0, 10 ** 9)
                FM.buy_food(conn, town, has_temple=True)
                assert everyone_ate(False), "people ate in a city with no Food at all"

                # a meal restores hunger only as far as people ate
                someone = next(iter(people))
                hours = SimulationManager(conn, world_id)
                hours.clock.hour = 18
                results = []
                for fed in (0.0, 1.0):
                    npc = get_npc(conn, someone)
                    npc["needs"]["hunger"] = 40
                    save_npc(conn, npc)
                    conn.execute(f"UPDATE npcs SET fed = ? WHERE id IN ({ids})", (fed,))
                    conn.commit()
                    hours.on_hour()
                    results.append(get_npc(conn, someone)["needs"]["hunger"])
                assert results[0] <= 40 < results[1] or results[0] < results[1], \
                    f"a meal restored hunger regardless of whether they ate (unfed {results[0]}, fed {results[1]})"
            finally:
                conn.executemany(
                    "UPDATE npcs SET gold = ?, fed = ?, needs_stress = ?, needs_happiness = ? WHERE id = ?",
                    [(p["gold"], p["fed"], p["needs_stress"], p["needs_happiness"], p["id"]) for p in people.values()]
                )
                conn.execute("UPDATE cities SET wealth = ? WHERE id = ?", (wealth_before, town["id"]))
                conn.commit()
                town["resources"]["Food"] = food_before

        run_step(r, "households buy food with pooled gold; temples give alms; no food means hunger", food_is_bought)

        def hard_times():
            import Data.Economy.food_market as FM
            import Data.World.harvest as H
            import Data.World.villagers as VL
            from Data.Database.repository import get_city, get_kingdom, save_city

            kingdom_id = kingdoms[0]["id"]

            def level():
                return round(FM.price_level(conn, kingdom_id), 4)

            try:
                # start from normal: the harvest step before this one brought in a real
                # harvest, and a bad one may already have raised this kingdom's prices
                FM.reset_price_level(conn, kingdom_id)
                seen = []
                for verdict in ("bad", "bad", "bad", "fair", "good"):
                    FM.update_price_level(conn, kingdom_id, verdict)
                    seen.append(level())
                assert seen[:3] == [1.2, 1.68, 2.688], f"bad years in a row should raise prices to 1.2, 1.68, 2.688x, not {seen[:3]}"
                assert seen[3] == seen[2], "an ordinary harvest changed food prices"
                assert abs(seen[4] - (1 + (seen[3] - 1) * (1 - FM.GOOD_YEAR_EASE))) < 1e-3, "a good harvest did not ease prices back toward normal"

                capital = get_city(conn, get_kingdom(conn, kingdom_id)["capital"])
                FM.reset_price_level(conn, kingdom_id)
                normal = FM.food_price(capital, conn)
                conn.execute("UPDATE kingdoms SET price_level = 3.0 WHERE id = ?", (kingdom_id,))
                assert abs(FM.food_price(capital, conn) - 3 * normal) < 0.01, "a kingdom's price level does not change what food costs"
            finally:
                FM.reset_price_level(conn, kingdom_id)
                conn.commit()

            # a harvest really moves prices: one with bad weather everywhere must raise every
            # kingdom's price level by exactly one more bad year, from wherever it stood
            before = {row["id"]: (row["price_level"], row["bad_harvest_years"]) for row in conn.execute(
                "SELECT id, price_level, bad_harvest_years FROM kingdoms WHERE world_id = ?", (world_id,))}
            real_weather = H.roll_weather
            H.roll_weather = lambda regions, neighbors, last: {g["id"]: ("Bad", H.WEATHER_FACTOR["Bad"], -3.0) for g in regions}
            try:
                summary = H.run_harvest(conn, world_id, 9999)
            finally:
                H.roll_weather = real_weather
            for kingdom_id in summary:
                old, years = before[kingdom_id]
                now = conn.execute("SELECT price_level FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()[0]
                want = round(old * (1 + FM.BAD_YEAR_RISE * (years + 1)), 4)
                assert abs(now - want) < 1e-3, f"a bad harvest left a kingdom's food prices at {now}x, not {want}x"
            conn.executemany("UPDATE kingdoms SET price_level = ?, bad_harvest_years = ? WHERE id = ?",
                             [(lvl, yrs, kid) for kid, (lvl, yrs) in before.items()])
            conn.commit()

            # a starving village loses households, saved exactly once, safe from older copies
            village_id = conn.execute(
                """SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                   WHERE k.world_id = ? AND c.households >= 20 ORDER BY c.households DESC LIMIT 1""", (world_id,)
            ).fetchone()[0]
            conn.execute("UPDATE cities SET household_store = 0, hungry_days = ?, famine_dead = 0 WHERE id = ?",
                         (H.FAMINE_AFTER_HUNGRY_DAYS + 5, village_id))
            conn.execute("UPDATE city_resources SET amount = 0 WHERE city_id = ? AND resource_name = 'Food'", (village_id,))
            conn.commit()
            older = get_city(conn, village_id)
            village = get_city(conn, village_id)
            before = village["households"]
            H.feed_households(village)
            save_city(conn, village)
            save_city(conn, village)
            save_city(conn, older)
            conn.commit()
            after = conn.execute("SELECT households, famine_dead FROM cities WHERE id = ?", (village_id,)).fetchone()
            expected = before * (1 - H.FAMINE_DEATH_RATE)
            assert abs(after["households"] - expected) < 1e-6, \
                f"a starving village should lose {H.FAMINE_DEATH_RATE:.1%} of its households once (had {before}, now {after['households']}, expected {expected:.3f})"
            assert after["famine_dead"] > 0, "famine deaths were not counted"

            VL.village_hardship(conn, world_id)
            logged = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Famine'").fetchone()[0]
            cleared = conn.execute("SELECT famine_dead FROM cities WHERE id = ?", (village_id,)).fetchone()[0]
            assert (logged > 0 or after["famine_dead"] < 1) and cleared < 1, "the month's famine deaths were not logged and cleared"

        run_step(r, "bad harvests raise food prices, good ones ease them; long hunger kills villagers", hard_times)

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
    def mark_meals():
        # a marker the day's food buying must overwrite (checked right after the day tick)
        conn.execute("UPDATE npcs SET fed = 0.123 WHERE alive = 1")
        conn.commit()

    run_step(r, "(setup) mark everyone's meals before the day tick", mark_meals)

    run_step(r, "day tick   (economy, wages, trade, caravans, travel)", manager.on_day)

    def day_tick_sells_food():
        total, unchanged = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN fed = 0.123 THEN 1 ELSE 0 END) FROM npcs WHERE alive = 1"
        ).fetchone()
        # people away from home (travellers, armies) buy nothing, so a few may keep the marker
        assert unchanged <= total * 0.1, f"the day tick did not sell people their food ({unchanged} of {total} untouched)"

    run_step(r, "the day tick sells every household its food", day_tick_sells_food)

    def day_tick_pays_lords():
        counties = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(p.treasury), 0) FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ?",
            (world_id,)).fetchone()
        if counties[0]:
            assert counties[1] > 0, "the day tick paid the lords nothing of their counties' taxes"

    run_step(r, "the day tick pays each lord their share of the county's taxes", day_tick_pays_lords)
    def prepare_starving_village():
        # set up right before the month tick, so no other step can feed it first
        # leave a starving village for the game's own month tick to deal with (checked after that tick)
        roomiest = conn.execute(
            """SELECT c.kingdom_id, COUNT(*) n FROM buildings b JOIN cities c ON c.id = b.city_id
               JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND c.type != 'Village' AND b.type = 'House'
               AND (SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1) < b.capacity
               GROUP BY c.kingdom_id ORDER BY n DESC LIMIT 1""", (world_id,)
        ).fetchone()
        if roomiest:
            other = conn.execute(
                "SELECT id, households FROM cities WHERE kingdom_id = ? AND households >= 20 AND id != ? ORDER BY households DESC LIMIT 1",
                (roomiest["kingdom_id"], -1)
            ).fetchone()
            if other:
                conn.execute("UPDATE cities SET hungry_days = 30, household_store = 0 WHERE id = ?", (other["id"],))
                conn.execute("UPDATE city_resources SET amount = 0 WHERE city_id = ? AND resource_name = 'Food'", (other["id"],))
                conn.commit()
                starving_probe.update(id=other["id"], households=other["households"])

    run_step(r, "(setup) a village starving just before the month tick", prepare_starving_village)

    def prepare_grain_office():
        # right before the month tick: a republic's reserve empty, a crown with grain to sell
        from Data.Database.repository import get_city, save_city
        rep = conn.execute("SELECT id, capital_city_id FROM kingdoms WHERE world_id = ? AND type = 'Republic' ORDER BY id LIMIT 1", (world_id,)).fetchone()
        seller = conn.execute("SELECT capital_city_id FROM kingdoms WHERE world_id = ? AND type = 'Crown' ORDER BY id LIMIT 1", (world_id,)).fetchone()
        conn.execute("UPDATE kingdoms SET treasury = 1000000, export_ban_until = NULL WHERE world_id = ?", (world_id,))
        capital = get_city(conn, rep["capital_city_id"]); capital["resources"]["Food"] = 0.0; save_city(conn, capital)
        seat = get_city(conn, seller["capital_city_id"]); seat["resources"]["Food"] = 10.0 ** 6; save_city(conn, seat)
        conn.execute("DELETE FROM events WHERE type = 'Grain Office'")
        conn.commit()

    run_step(r, "(setup) a republic's grain reserve empty just before the month tick", prepare_grain_office)

    vacancy = {}

    def prepare_vacancies():
        # right before the month tick: two workers die in a full civilian workplace of a town with jobless people
        row = conn.execute(
            """SELECT b.id, b.capacity FROM buildings b JOIN cities c ON c.id = b.city_id
               WHERE c.kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ?) AND b.category = 'Civillian' AND b.capacity >= 3
                 AND (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) = b.capacity
                 AND (SELECT COUNT(*) FROM npcs n WHERE n.city_id = b.city_id AND n.alive = 1 AND n.profession = 'Laborer' AND n.age >= 16) >= 2
               ORDER BY b.id LIMIT 1""", (world_id,)).fetchone()
        assert row, "(setup) no full workplace in a town with jobless people"
        trade = conn.execute(          # what most of its workers do: one odd worker must not mislead the check
            "SELECT profession FROM npcs WHERE workplace_id = ? AND alive = 1 GROUP BY profession ORDER BY COUNT(*) DESC LIMIT 1",
            (row["id"],)).fetchone()[0]
        for npc in conn.execute("SELECT id FROM npcs WHERE workplace_id = ? AND alive = 1 AND title IS NULL LIMIT 2", (row["id"],)).fetchall():   # never a ruler
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (npc["id"],))
        conn.commit()
        town = conn.execute("SELECT city_id FROM buildings WHERE id = ?", (row["id"],)).fetchone()[0]
        jobless = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Laborer' AND age >= 16",
                               (town,)).fetchone()[0]
        vacancy.update(building=row["id"], capacity=row["capacity"], trade=trade, town=town, jobless_before=jobless)

    run_step(r, "(setup) two workers die in a full workplace just before the month tick", prepare_vacancies)

    def empty_purses():
        # right before the month tick: every town's purse empty. Towns also earn gold selling food each day,
        # but the month tick sells nothing -- so whatever is in the purses after it is the town tax.
        conn.execute("UPDATE cities SET wealth = 0")
        conn.commit()

    run_step(r, "(setup) every town's purse empty just before the month tick", empty_purses)

    run_step(r, "month tick (families, relationships, tourism, migration)", manager.on_month)

    def month_tick_moves_the_starving():
        if not starving_probe:
            return
        now = conn.execute("SELECT households FROM cities WHERE id = ?", (starving_probe["id"],)).fetchone()[0]
        assert now < starving_probe["households"], "the monthly tick did not move households out of a starving village"

    run_step(r, "the month tick moves families out of a starving village", month_tick_moves_the_starving)

    def month_tick_weighs_health():
        changed = conn.execute(
            "SELECT COUNT(*) FROM kingdoms WHERE world_id = ? AND (health_stability != 100 OR health_unrest != 0 OR health_loyalty != 70)",
            (world_id,)).fetchone()[0]
        assert changed, "the month tick did not work out any kingdom's health"

    run_step(r, "the month tick works out each kingdom's Loyalty, Unrest and Stability", month_tick_weighs_health)

    def month_tick_runs_grain_office():
        assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Grain Office'").fetchone()[0], \
            "the month tick did not send any grain office to buy"

    run_step(r, "the month tick sends republics' grain offices to buy", month_tick_runs_grain_office)

    def month_tick_fills_vacancies():
        if not vacancy:
            return
        filled = conn.execute("SELECT COUNT(*) FROM npcs WHERE workplace_id = ? AND alive = 1 AND profession = ?",
                              (vacancy["building"], vacancy["trade"])).fetchone()[0]
        if filled != vacancy["capacity"]:          # explain the shortfall: too few jobless, or the hired left or died
            left = conn.execute("SELECT COUNT(*) FROM npcs WHERE profession = ? AND city_id != ? AND alive = 1 AND workplace_id IS NULL",
                                (vacancy["trade"], vacancy["town"])).fetchone()[0]
            places = conn.execute("SELECT capacity FROM buildings WHERE id = ?", (vacancy["building"],)).fetchone()[0]
            raise AssertionError(
                f"the month tick did not fill the dead workers' places with jobless townsfolk ({filled} of {vacancy['capacity']} "
                f"{vacancy['trade']}s; the town had {vacancy['jobless_before']} Laborers before the tick; the building now has "
                f"{places} places; {left} {vacancy['trade']}(s) without a workplace now live elsewhere)")

    run_step(r, "the month tick's job market fills the places of the dead with jobless townsfolk of that trade", month_tick_fills_vacancies)

    def month_tick_fills_purses():
        assert conn.execute("SELECT COALESCE(SUM(wealth), 0) FROM cities").fetchone()[0] > 0, "the month tick did not tax the towns into their purses"

    run_step(r, "the month tick taxes every town's citizens into its purse", month_tick_fills_purses)

    def rulers_saved():
        saved = conn.execute("SELECT COUNT(*) FROM cities WHERE ruler_id IS NOT NULL").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM cities").fetchone()[0]
        assert saved >= total * 0.9, f"only {saved} of {total} cities have a saved ruler after the monthly tick"

        # a crown's first ruler is crowned; a republic's first Doge is elected
        founding = conn.execute(
            """SELECT kingdom_id, COUNT(*) n FROM chronicle_entries
               WHERE event_type IN ('Coronation', 'Election') GROUP BY kingdom_id"""
        ).fetchall()
        assert len(founding) == len(kingdoms) and all(row["n"] == 1 for row in founding), \
            "every kingdom's chronicle should open with exactly one coronation or election, its founding ruler"

        # every city whose ruler is alive must keep that same ruler through a quiet month.
        # (A city that had nobody fit to rule may gain its first ruler as people arrive --
        # famine refugees, bandits, levies sent home -- and that is no failure.)
        ruled = {row["id"]: row["ruler_id"] for row in conn.execute(
            "SELECT c.id, c.ruler_id FROM cities c JOIN npcs n ON n.id = c.ruler_id WHERE n.alive = 1")}
        entries_before = conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0]
        import Data.Monsters.raids as raids_module                  # a raid is no quiet month: none this time...
        import Data.Government.rebellion as rebellion_module        # ...and nor is a lord's rising
        raids_held, raids_module.RAID_CHANCE = raids_module.RAID_CHANCE, 0.0
        risings_held, rebellion_module.RISING_CHANCE_AT_ZERO = rebellion_module.RISING_CHANCE_AT_ZERO, 0.0
        try:
            manager.on_month()
        finally:
            raids_module.RAID_CHANCE = raids_held
            rebellion_module.RISING_CHANCE_AT_ZERO = risings_held
        replaced = [cid for cid, rid in ruled.items()
                    if conn.execute("SELECT ruler_id FROM cities WHERE id = ?", (cid,)).fetchone()[0] != rid]
        assert not replaced, f"a quiet month replaced {len(replaced)} living rulers (rulers are not being saved)"
        entries_after = conn.execute("SELECT COUNT(*) FROM chronicle_entries").fetchone()[0]
        if entries_after != entries_before:
            added = [(row["kingdom_id"], row["event_type"], row["text"][:160]) for row in conn.execute(
                "SELECT * FROM chronicle_entries ORDER BY id DESC LIMIT ?", (entries_after - entries_before,))]
            dead_monarchs = [k["name"] for k in conn.execute(
                "SELECT k.name FROM kingdoms k JOIN npcs n ON n.id = k.ruler_id WHERE n.alive = 0")]
            raise AssertionError(f"a quiet month added chronicle entries: {added}; monarchs dead now: {dead_monarchs}")

    run_step(r, "rulers are saved, each realm's founding monarch is chronicled once", rulers_saved)

    def republics_elect():
        from Data.Government.republic import republic_count
        kingdoms = conn.execute("SELECT id, type, ruler_id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall()
        republics = [k for k in kingdoms if k["type"] == "Republic"]
        assert len(republics) == republic_count(len(kingdoms)), f"{len(republics)} republics among {len(kingdoms)} kingdoms"
        for k in republics:
            title = conn.execute("SELECT title FROM npcs WHERE id = ?", (k["ruler_id"],)).fetchone() if k["ruler_id"] else None
            assert title and title[0] == "Doge", "a republic's head is not an elected Doge"
            assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE kingdom_id = ? AND event_type = 'Election'",
                                (k["id"],)).fetchone()[0] >= 1, "a republic's election was not chronicled"

    run_step(r, "a quarter of the kingdoms are republics whose Great Councils elect Doges", republics_elect)

    def lords_inherit():
        import Data.Government.provinces as P
        from Data.Government.government import assign_city_ruler
        from Data.Database.repository import get_npc, get_city

        provinces = [dict(p) for p in conn.execute(
            """SELECT p.* FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ?""", (world_id,))]
        if not provinces:
            return
        for province in provinces:
            lord = get_npc(conn, province["lord_id"]) if province["lord_id"] else None
            assert lord and lord["alive"] and lord["title"] in P.LORD_TITLES, f"the {province['name']} has no living Count or Countess"
            ruler = conn.execute("SELECT ruler_id FROM cities WHERE id = ?", (province["seat_city_id"],)).fetchone()[0]
            assert ruler == lord["id"], f"the lord of the {province['name']} does not rule its seat"

        # stage an inheritance: the lord dies, leaving a grown child and a young one
        province = provinces[0]
        lord = province["lord_id"]
        def untitled_adult(age):
            row = conn.execute(
                "SELECT id FROM npcs WHERE alive = 1 AND title IS NULL AND profession != 'Child' ORDER BY id DESC LIMIT 1 OFFSET ?",
                (age,)).fetchone()
            conn.execute("UPDATE npcs SET age = ?, parent_a_id = ? WHERE id = ?", (age, lord, row[0]))
            return row[0]
        elder, young = untitled_adult(30), untitled_adult(12)
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (lord,))
        conn.commit()
        heir = assign_city_ruler(conn, get_city(conn, province["seat_city_id"]))
        after = conn.execute("SELECT lord_id, house FROM provinces WHERE id = ?", (province["id"],)).fetchone()
        assert heir and heir["id"] == elder, "the dead lord's eldest grown child did not inherit"
        assert after["lord_id"] == elder and after["house"] == province["house"], "the inheritance did not keep the house"
        assert get_npc(conn, elder)["city_id"] == province["seat_city_id"], "the heir did not move to the seat"

    run_step(r, "every county has its lord; a lord's eldest grown child inherits and the house goes on", lords_inherit)

    def crown_stays_in_family():
        import Data.Government.succession as S
        from Data.Government.government import assign_city_ruler
        from Data.Database.repository import get_city, get_kingdom

        crown_id = conn.execute(           # a crown: a republic's Doge has no heirs
            "SELECT id FROM kingdoms k WHERE world_id = ? AND type = 'Crown' AND status != 'Fallen' AND EXISTS (SELECT 1 FROM provinces p WHERE p.kingdom_id = k.id) ORDER BY id DESC LIMIT 1", (world_id,)).fetchone()[0]
        kingdom = get_kingdom(conn, crown_id)
        monarch = conn.execute("SELECT ruler_id FROM cities WHERE id = ?", (kingdom["capital"],)).fetchone()[0]
        child = conn.execute(           # someone of the crown's own capital, not of another realm
            "SELECT id FROM npcs WHERE alive = 1 AND title IS NULL AND id != ? AND city_id = ? ORDER BY id LIMIT 1 OFFSET 7",
            (monarch, kingdom["capital"])
        ).fetchone()[0]
        # worlds start with families, so the monarch may have children of their own: set them aside, so this
        # child really is the only one (an older real child would rightly inherit first)
        conn.execute("UPDATE npcs SET parent_a_id = NULL WHERE parent_a_id = ?", (monarch,))
        conn.execute("UPDATE npcs SET parent_b_id = NULL WHERE parent_b_id = ?", (monarch,))
        conn.execute("UPDATE npcs SET age = 9, parent_a_id = ? WHERE id = ?", (monarch, child))
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (monarch,))
        conn.commit()

        crowned = assign_city_ruler(conn, get_city(conn, kingdom["capital"]))
        row = conn.execute("SELECT ruler_id, regent_id FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone()
        assert crowned and crowned["id"] == child, "the dead monarch's only child did not inherit the crown"
        assert row["ruler_id"] == child, "the kingdom's record does not name its new monarch"
        assert row["regent_id"] is not None, "a 9-year-old monarch was given no regent"
        assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Regency'").fetchone()[0] > 0, \
            "the regency was not chronicled"

        conn.execute("UPDATE npcs SET age = ? WHERE id = ?", (S.ADULT_AGE, child))
        conn.commit()
        S.check_regencies(conn, world_id)
        assert conn.execute("SELECT regent_id FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone()[0] is None, \
            "the regency did not end when the young monarch came of age"

    run_step(r, "a dead monarch's child inherits the crown, with a regent until they come of age", crown_stays_in_family)

    def lords_loyalty():
        import Data.Government.feudal as F
        moved = conn.execute(
            "SELECT COUNT(*) FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ? AND p.loyalty != ?",
            (world_id, F.LOYALTY_START)).fetchone()[0]
        has_counties = conn.execute(
            "SELECT COUNT(*) FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ?", (world_id,)).fetchone()[0]
        assert moved or not has_counties, "the month ticks never moved any lord's loyalty"

        province = dict(conn.execute(
            "SELECT p.* FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ? LIMIT 1", (world_id,)).fetchone())
        kingdom = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (province["kingdom_id"],)).fetchone())
        calm = dict(kingdom, price_level=1.0, last_war_year=None)
        base, _ = F.loyalty_target(conn, province, calm, 1)
        worse, _ = F.loyalty_target(conn, province, dict(calm, price_level=3.0, last_war_year=1, last_war_won=0), 1)
        assert abs((base - worse) - (2 * F.BREAD_PER_STEP - F.WAR_LOST)) < 0.01, \
            f"dear bread and a lost war should cost a lord {2 * F.BREAD_PER_STEP - F.WAR_LOST} loyalty, not {base - worse}"

        conn.execute("UPDATE kingdoms SET price_level = 9.0 WHERE id = ?", (kingdom["id"],))
        conn.execute("UPDATE provinces SET loyalty = ? WHERE id = ?", (F.RESTLESS_BELOW + 0.1, province["id"]))
        conn.commit()
        try:
            F.update_loyalty(conn, world_id)
            assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Restless Lord'").fetchone()[0] > 0, \
                "a lord pushed below the restless line was not logged"
        finally:
            conn.execute("UPDATE kingdoms SET price_level = ? WHERE id = ?", (kingdom["price_level"], kingdom["id"]))
            conn.commit()

    run_step(r, "lords' loyalty follows how the crown treats them, and the restless are logged", lords_loyalty)

    def lords_rebel():
        import Data.Government.rebellion as R
        import random as random_module
        from Data.Simulation.simulation_manager import SimulationManager

        class Scripted:                       # rolls in order, then 0.5
            def __init__(self, rolls): self.rolls = list(rolls)
            def random(self): return self.rolls.pop(0) if self.rolls else 0.5
            def __getattr__(self, name): return getattr(random_module, name)

        provinces = [dict(p) for p in conn.execute(
            "SELECT p.* FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ? ORDER BY p.id", (world_id,))]
        if not provinces:
            return

        # 1. through the real monthly tick: one restless lord rises and is crushed
        rebel = provinces[0]
        conn.execute("UPDATE provinces SET loyalty = 70 WHERE kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ?)", (world_id,))
        conn.execute("UPDATE provinces SET loyalty = 0 WHERE id = ?", (rebel["id"],))
        conn.commit()
        risings_before = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Rebellion' AND description LIKE ?",
                                      (f"%{rebel['name']}%",)).fetchone()[0]
        real_random, real_ratio = R.random, R.MIN_STRENGTH_RATIO
        R.random, R.MIN_STRENGTH_RATIO = Scripted([0.0, 0.999999]), 0       # rise, then lose the battle
        try:
            SimulationManager(conn, world_id).on_month()
        finally:
            R.random, R.MIN_STRENGTH_RATIO = real_random, real_ratio
        # three separate questions, each with its own answer if it fails (houses are named after a founder's
        # first name, so two houses can share a name: the new lord is compared by who they are, not by name)
        risings = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Rebellion' AND description LIKE ?",
                               (f"%{rebel['name']}%",)).fetchone()[0]
        assert risings > risings_before, f"the restless lord of the {rebel['name']} did not rise through the monthly tick"
        lord_alive = conn.execute("SELECT alive FROM npcs WHERE id = ?", (rebel["lord_id"],)).fetchone()[0]
        assert not lord_alive, "a crushed rebel lord was not executed"
        now = conn.execute("SELECT lord_id FROM provinces WHERE id = ?", (rebel["id"],)).fetchone()
        assert now["lord_id"] != rebel["lord_id"], "a crushed rebel lord's house kept the county"

        # 2. a coalition breaks away together
        pair = conn.execute(
            """SELECT p.kingdom_id FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id WHERE k.world_id = ?
               GROUP BY p.kingdom_id HAVING COUNT(*) >= 2 ORDER BY p.kingdom_id DESC LIMIT 1""", (world_id,)).fetchone()
        if not pair:
            return
        leader, ally = [dict(p) for p in conn.execute("SELECT * FROM provinces WHERE kingdom_id = ? ORDER BY id LIMIT 2", (pair[0],))]
        R.random = Scripted([0.0])
        try:
            result = R.rise(conn, leader, [ally])
        finally:
            R.random = real_random
        new = conn.execute("SELECT * FROM kingdoms WHERE name = ?", (result.get("new_kingdom"),)).fetchone()
        assert result["rebels_win"] and new and new["capital_city_id"] == leader["seat_city_id"] and new["ruler_id"] == leader["lord_id"], \
            "a victorious coalition did not found a kingdom ruled from its leader's seat"
        assert conn.execute("SELECT kingdom_id FROM provinces WHERE id = ?", (ally["id"],)).fetchone()[0] == new["id"], \
            "the ally's county did not join the new kingdom"
        assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Breakaway'").fetchone()[0] >= 2, \
            "the breakaway was not chronicled by both kingdoms"

    run_step(r, "a restless lord rises through the month tick and is crushed; a coalition breaks away as a new kingdom", lords_rebel)

    def rulers_issue_edicts():
        import Data.Government.edicts as E
        from Data.Simulation.simulation_manager import SimulationManager
        from Data.Economy.economy import simulate_cross_kingdom_trade_day
        from Data.Database.repository import get_npc, get_kingdom_cities, get_city, save_city

        kingdom = dict(conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND ruler_id IS NOT NULL ORDER BY id LIMIT 1", (world_id,)).fetchone())
        ruler = kingdom["ruler_id"]
        saved_kingdom = dict(kingdom)
        saved_traits = tuple(conn.execute("SELECT generosity, mercy, boldness FROM npcs WHERE id = ?", (ruler,)).fetchone())
        saved_lords = conn.execute("SELECT id, loyalty FROM provinces WHERE kingdom_id = ?", (kingdom["id"],)).fetchall()
        # a calm realm is a fed one: its villages' hunger and its people's meals are staged too, and restored after
        # a quiet realm has no works to call for either: its towns have no Wood to build with (restored after)
        saved_wood = conn.execute("SELECT city_id, amount FROM city_resources WHERE resource_name = 'Wood' AND city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],)).fetchall()
        saved_hunger = conn.execute("SELECT id, hungry_days FROM cities WHERE kingdom_id = ?", (kingdom["id"],)).fetchall()
        saved_meals = conn.execute("SELECT n.id, n.fed FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ?", (kingdom["id"],)).fetchall()
        try:
            conn.execute("""UPDATE kingdoms SET health_unrest = 5, health_stability = 95, health_loyalty = 65, treasury = 1000000,
                            price_level = 1, tax_rate = 0.10, regent_id = NULL, export_ban_until = NULL, martial_law_until = NULL
                            WHERE id = ?""", (kingdom["id"],))
            conn.execute("UPDATE provinces SET loyalty = 65 WHERE kingdom_id = ?", (kingdom["id"],))
            conn.execute("UPDATE cities SET hungry_days = 0 WHERE kingdom_id = ?", (kingdom["id"],))
            conn.execute("UPDATE city_resources SET amount = 0 WHERE resource_name = 'Wood' AND city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],))
            conn.execute("UPDATE npcs SET fed = 1 WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],))
            conn.execute("DELETE FROM kingdom_edicts WHERE kingdom_id = ?", (kingdom["id"],))
            conn.commit()
            day = E.today(conn, world_id)
            staged = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone())

            conn.execute("UPDATE npcs SET generosity = 0, mercy = 0, boldness = 0 WHERE id = ?", (ruler,))
            conn.commit()
            assert E.choose(conn, staged, get_npc(conn, ruler), day)[0] is None, "an even-tempered ruler meddled with a calm realm"
            conn.execute("UPDATE npcs SET generosity = -90 WHERE id = ?", (ruler,))
            conn.commit()
            assert E.choose(conn, staged, get_npc(conn, ruler), day)[0] == "raise taxes", "a greedy ruler did not raise taxes in a calm, rich realm"

            seasons = SimulationManager(conn, world_id)
            seasons.clock.month = 4                         # Summer: a review, no harvest
            seasons.on_season()
            assert conn.execute("SELECT COUNT(*) FROM kingdom_edicts WHERE kingdom_id = ?", (kingdom["id"],)).fetchone()[0] >= 1, \
                "the seasonal review issued no edict"

            conn.execute("UPDATE kingdoms SET export_ban_until = ? WHERE id = ?", (day + 90, kingdom["id"]))
            for other in conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():
                for c in get_kingdom_cities(conn, other["id"]):
                    city = get_city(conn, c["id"])
                    city["resources"]["Food"] = 10.0 ** 6 if other["id"] == kingdom["id"] else 0.0
                    save_city(conn, city)
            conn.commit()
            sold = [t for t in simulate_cross_kingdom_trade_day(conn, world_id) if t["resource"] == "Food" and t["seller"] == kingdom["name"]]
            assert not sold, "a kingdom that banned food exports still sold Food abroad"
        finally:
            conn.execute("""UPDATE kingdoms SET tax_rate = ?, treasury = ?, price_level = ?, regent_id = ?, export_ban_until = ?,
                            martial_law_until = ? WHERE id = ?""",
                         (saved_kingdom["tax_rate"], saved_kingdom["treasury"], saved_kingdom["price_level"], saved_kingdom["regent_id"],
                          saved_kingdom["export_ban_until"], saved_kingdom["martial_law_until"], kingdom["id"]))
            conn.execute("UPDATE npcs SET generosity = ?, mercy = ?, boldness = ? WHERE id = ?", (*saved_traits, ruler))
            conn.executemany("UPDATE provinces SET loyalty = ? WHERE id = ?", [(row["loyalty"], row["id"]) for row in saved_lords])
            conn.executemany("UPDATE cities SET hungry_days = ? WHERE id = ?", [(row["hungry_days"], row["id"]) for row in saved_hunger])
            conn.executemany("UPDATE city_resources SET amount = ? WHERE resource_name = 'Wood' AND city_id = ?", [(row["amount"], row["city_id"]) for row in saved_wood])
            conn.executemany("UPDATE npcs SET fed = ? WHERE id = ?", [(row["fed"], row["id"]) for row in saved_meals])
            conn.commit()

    run_step(r, "rulers choose edicts by their nature; the seasons bring reviews; an export ban keeps Food home", rulers_issue_edicts)

    def public_works_enlarge():
        import Data.Buildings.upgrades as U
        import Data.Government.edicts as E
        from Data.Database.repository import get_city, save_city
        kingdom = dict(conn.execute("""SELECT k.* FROM kingdoms k WHERE k.world_id = ? AND k.status != 'Fallen' AND
            (SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = k.id AND n.alive = 1 AND n.profession = 'Laborer') > 20
            ORDER BY k.id LIMIT 1""", (world_id,)).fetchone())
        towns = [row[0] for row in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village'", (kingdom["id"],))]
        for town in towns:
            city = get_city(conn, town)
            city["resources"].update({"Wood": 2000.0, "Stone": 2000.0, "Tools": 2000.0})
            save_city(conn, city)
        conn.execute("UPDATE cities SET wealth = 0, debt = 0 WHERE kingdom_id = ?", (kingdom["id"],))
        conn.execute("UPDATE kingdoms SET treasury = 1000000 WHERE id = ?", (kingdom["id"],))
        conn.commit()
        assert E.situation(conn, kingdom)["works_need"] > 0, "a realm with jobless towns that can build feels no need for public works"

        levels_before = conn.execute("SELECT COALESCE(SUM(level), 0) FROM buildings WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],)).fetchone()[0]
        what = E.apply_edict(conn, kingdom, "public works", E.today(conn, world_id))
        levels_after = conn.execute("SELECT COALESCE(SUM(level), 0) FROM buildings WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],)).fetchone()[0]
        assert levels_after > levels_before, f"the public works edict enlarged nothing ({what})"
        assert conn.execute("SELECT MIN(amount) FROM city_resources WHERE resource_name = 'Wood' AND city_id IN (SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village')",
                            (kingdom["id"],)).fetchone()[0] < 2000, "public works cost the towns no materials"
        assert conn.execute("SELECT SUM(debt) FROM cities WHERE kingdom_id = ?", (kingdom["id"],)).fetchone()[0] > 0, \
            "towns with empty purses did not borrow from the crown for their works"

        # nothing left to enlarge: the edict builds anew for those still waiting
        conn.execute("UPDATE buildings SET level = 3 WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],))
        conn.commit()
        count = lambda: conn.execute("SELECT COUNT(*) FROM buildings WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)", (kingdom["id"],)).fetchone()[0]
        before = count()
        what = E.apply_edict(conn, kingdom, "public works", E.today(conn, world_id))
        assert count() > before, f"with every building at level 3, the public works edict built nothing new ({what})"
        newest = conn.execute("SELECT id, capacity FROM buildings WHERE city_id IN (SELECT id FROM cities WHERE kingdom_id = ?) AND type != 'House' ORDER BY id DESC LIMIT 1",
                              (kingdom["id"],)).fetchone()
        assert conn.execute("SELECT COUNT(*) FROM npcs WHERE workplace_id = ? AND alive = 1", (newest["id"],)).fetchone()[0] == newest["capacity"], \
            "a new workplace was not filled by the jobless"

    run_step(r, "the public works edict enlarges full buildings, then builds anew, the crown lending what towns' purses lack", public_works_enlarge)

    def republics_trade():
        import Data.Government.republic as RP
        from Data.Economy.economy import simulate_cross_kingdom_trade_day, get_target_stock
        from Data.Database.repository import get_city, save_city, get_kingdom_cities

        crown, republic = {"type": "Crown"}, {"type": "Republic"}
        assert RP.duties(republic, crown, 100) == (110, 110) and RP.duties(crown, republic, 100) == (95, 95), \
            "customs duties are not 10% on a republic's sales and 5% on its purchases"

        rep = dict(conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND type = 'Republic' ORDER BY id LIMIT 1", (world_id,)).fetchone())
        buyer = dict(conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND type = 'Crown' ORDER BY id LIMIT 1", (world_id,)).fetchone())

        # a real sale: the republic has Food to spare, the crown has none, everyone else is neutral
        conn.execute("UPDATE kingdoms SET export_ban_until = NULL, treasury = 10000000 WHERE world_id = ?", (world_id,))
        for k in conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,)).fetchall():
            for c in get_kingdom_cities(conn, k["id"]):
                city = get_city(conn, c["id"])
                city["resources"]["Food"] = 10.0 ** 6 if k["id"] == rep["id"] else 0.0 if k["id"] == buyer["id"] else get_target_stock("Food", city)
                save_city(conn, city)
        conn.commit()
        sales = [t for t in simulate_cross_kingdom_trade_day(conn, world_id)
                 if t["resource"] == "Food" and t["seller"] == rep["name"] and t["buyer"] == buyer["name"]]
        assert sales and all(abs(t["paid"] - round(t["cost"] * 1.10, 2)) < 0.02 for t in sales), \
            "a crown buying from a republic did not pay the 10% export duty"

        # the grain office: an empty reserve, a seller with grain, a small purse
        capital = get_city(conn, rep["capital_city_id"])
        capital["resources"]["Food"] = 0.0
        save_city(conn, capital)
        seller_seat = get_city(conn, buyer["capital_city_id"])
        seller_seat["resources"]["Food"] = 10.0 ** 6
        save_city(conn, seller_seat)
        conn.execute("UPDATE kingdoms SET treasury = 1000 WHERE id = ?", (rep["id"],))
        conn.commit()
        bought = [b for b in RP.run_grain_offices(conn, world_id) if b[0] == rep["name"]]
        assert bought, "an empty grain reserve bought nothing abroad"
        assert sum(b[3] for b in bought) <= 1000 * RP.GRAIN_BUDGET_SHARE + 0.01, "the grain office spent more than its budget"
        assert conn.execute("SELECT COUNT(*) FROM caravans WHERE buyer_kingdom_id = ? AND destination_city_id = ? AND resource = 'Food'",
                            (rep["id"], rep["capital_city_id"])).fetchone()[0], "the grain office's purchase never set out by caravan"

    run_step(r, "republics charge customs duties, and their grain offices buy reserves abroad", republics_trade)

    def campaign_marches_and_fights():
        import Data.Government.campaigns as CP
        import Data.Government.armies as A
        from Data.Government.edicts import today
        from Data.Government.military import MILITARY_PROFESSIONS
        from Data.Infrastructure.travel import get_traveling_npc_ids
        from Data.Database.repository import get_kingdom, get_city, save_city

        pair = None
        for a in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND type = 'Crown' ORDER BY id", (world_id,)).fetchall():
            for d in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND id != ? ORDER BY id", (world_id, a["id"])).fetchall():
                if CP.choose_target(conn, get_kingdom(conn, a["id"]), get_kingdom(conn, d["id"])):
                    pair = (a["id"], d["id"])
                    break
            if pair:
                break
        assert pair, "(setup) no kingdom can reach an enemy town by road"
        conn.execute("UPDATE campaigns SET status = 'Over'")
        attacker, defender = get_kingdom(conn, pair[0]), get_kingdom(conn, pair[1])       # the game's own kingdom records
        capital = get_city(conn, attacker["capital"])
        capital["resources"].update({"Food": 10.0 ** 6, "Weapons": 10.0 ** 4, "Potions": 1000.0})
        save_city(conn, capital)
        conn.commit()

        away = get_traveling_npc_ids(conn, world_id)
        marks = ",".join("?" * len(MILITARY_PROFESSIONS))
        free = sum(1 for row in conn.execute(
            f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
                WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (attacker["id"], *MILITARY_PROFESSIONS))
            if row[0] not in away)
        # make the target walled, so the siege is always tested (towns only sometimes have a Castle)
        import json as json_module
        from Data.Database.repository import create_building
        target_now = CP.choose_target(conn, attacker, defender)
        if not CP.walled(conn, target_now['id']):
            castle = json_module.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Data', 'Buildings', 'BuildingTypes.json')))['Castle']
            create_building(conn, target_now['id'], 'Castle', 'Castle', castle['category'], castle['capacity'])
            conn.commit()
        expected_target = CP.choose_target(conn, attacker, defender)["id"]      # before the march changes the armies
        campaign = CP.start_campaign(conn, attacker, defender, today(conn, world_id))
        assert campaign, "a kingdom at war raised no campaign"
        army = A.get_army(conn, campaign["army_id"])
        marching = conn.execute("SELECT COUNT(*) FROM army_members WHERE army_id = ? AND levied = 0", (army["id"],)).fetchone()[0]
        assert marching == round(free * CP.MARCH_SHARE), f"{marching} of {free} free soldiers marched, not two thirds"
        assert army["status"] == "Marching" and campaign["target_city_id"] == expected_target, \
            "the army is not marching on the enemy's richest reachable town"

        for _ in range(200):
            if A.get_army(conn, army["id"])["status"] == "Encamped":
                break
            A.advance_armies(conn, world_id)
        manager.on_day()                                   # arrival, through the real day tick: the siege begins
        siege = conn.execute("SELECT status FROM campaigns WHERE id = ?", (campaign["id"],)).fetchone()[0]
        assert siege == 'Besieging', f"reaching a walled town, the army did not lay siege (campaign is {siege})"
        assert campaign['target_city_id'] in CP.besieged_city_ids(conn, world_id), 'the besieged town is not blockaded'
        conn.execute("UPDATE campaigns SET siege_progress = ? WHERE id = ?", (CP.WALL_STRENGTH - 1, campaign["id"]))
        conn.commit()
        import random as random_module
        # the storm succeeds: for this one day no defenders rally, and an undefended town falls
        # (fixing a dice roll does not survive a whole day tick -- raids and others roll the same dice)
        saved_rally = CP.rally
        CP.rally = lambda *args, **kwargs: []
        try:
            manager.on_day()                               # the siege works breach the walls, and the town is stormed
        finally:
            CP.rally = saved_rally
        after = conn.execute("SELECT status, outcome FROM campaigns WHERE id = ?", (campaign["id"],)).fetchone()
        assert after["status"] == "Returning" and after["outcome"] in ("attacker_wins", "defender_wins"), \
            f"the day tick did not storm the breached town (campaign is {after['status']}, {after['outcome']})"

        # the town taken is occupied: a garrison from the army holds it, and the townsfolk resent it
        import Data.Government.occupation as OC
        occupation = OC.occupied(conn, campaign['target_city_id'])
        assert occupation, 'a town taken by a campaign was not occupied'
        garrison = OC.garrison_of(conn, occupation['id'])
        assert garrison and all(g['city_id'] == campaign['target_city_id'] for g in garrison), 'the garrison does not live in the town it holds'
        ids = ','.join(str(g['id']) for g in garrison)
        conn.execute(f"UPDATE npcs SET needs_happiness = 60 WHERE city_id = ? AND id NOT IN ({ids})", (campaign['target_city_id'],))
        conn.commit()
        saved_occ_random = OC.random
        OC.random = type('NoRising', (), {'random': lambda self: 0.99, '__getattr__': lambda self, n: getattr(random_module, n)})()
        try:
            manager.on_month()                             # resentment, through the real month tick
        finally:
            OC.random = saved_occ_random
        resent = conn.execute(f"SELECT AVG(needs_happiness) FROM npcs WHERE city_id = ? AND alive = 1 AND id NOT IN ({ids})",
                              (campaign['target_city_id'],)).fetchone()[0]
        assert resent is not None and resent < 60, 'the month tick did not make the occupied townsfolk resent their garrison'
        
        A.disband_army(conn, army["id"])                  # peace again, for the steps that follow
        conn.execute("UPDATE campaigns SET status = 'Over' WHERE id = ?", (campaign["id"],))
        conn.commit()

    run_step(r, "a campaign musters two thirds of the army, marches on a walled town, besieges it, and storms it when the walls fall", campaign_marches_and_fights)

    def caravans_meet_sieges():
        import Data.Infrastructure.caravans as CV
        import Data.Government.armies as A
        from Data.Database.repository import get_kingdom, get_city, save_city

        k_att, k_def = [get_kingdom(conn, row[0]) for row in conn.execute(
            "SELECT id FROM kingdoms WHERE world_id = ? AND status != 'Fallen' ORDER BY id LIMIT 2", (world_id,))]
        town = conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND id != ? ORDER BY id LIMIT 1", (k_def["id"], k_def["capital"])).fetchone()[0]
        capital = get_city(conn, k_att["capital"])
        capital["resources"]["Food"] = capital["resources"].get("Food", 0) + 10000
        save_city(conn, capital)
        conn.commit()
        army = A.raise_army(conn, k_att, get_city(conn, k_att["capital"]), size=3)
        saved_raids = CV.BANDIT_CHANCE
        CV.BANDIT_CHANCE = 0.0
        try:
            caravan = CV.dispatch_caravan(conn, k_def["capital"], town, "Food", 400, k_def["id"], k_def["id"])
            assert caravan, "(setup) the caravan did not set out"
            conn.execute("""INSERT INTO campaigns (attacker_kingdom_id, defender_kingdom_id, army_id, target_city_id, status, started_day, siege_day)
                            VALUES (?, ?, ?, ?, 'Besieging', 0, 0)""", (k_att["id"], k_def["id"], army["id"], town))
            conn.commit()
            wagons = conn.execute("SELECT food FROM armies WHERE id = ?", (army["id"],)).fetchone()[0]
            for _ in range(60):
                CV.advance_caravans(conn, world_id)
                if conn.execute("SELECT status FROM caravans WHERE id = ?", (caravan["id"],)).fetchone()[0] != "En Route":
                    break
            assert conn.execute("SELECT status FROM caravans WHERE id = ?", (caravan["id"],)).fetchone()[0] == "Seized", \
                "a caravan that reached a besieged town was not seized"
            assert abs(conn.execute("SELECT food FROM armies WHERE id = ?", (army["id"],)).fetchone()[0] - wagons - 400) < 0.01, \
                "the seized Food did not go into the besiegers' wagons"
            assert CV.dispatch_caravan(conn, k_def["capital"], town, "Food", 100, k_def["id"], k_def["id"]) is None, \
                "a caravan set out for a besieged town"
        finally:
            CV.BANDIT_CHANCE = saved_raids
            conn.execute("UPDATE campaigns SET status = 'Over' WHERE army_id = ?", (army["id"],))
            A.disband_army(conn, army["id"])
            conn.commit()

    run_step(r, "caravans and sieges: none sets out for a besieged town, and one arriving there is seized by the besiegers", caravans_meet_sieges)

    def refugees_and_slums():
        import Data.World.slums as SL
        import Data.World.villagers as V
        import Data.NPCS.contentment as CT
        from Data.Government.edicts import today
        village = dict(conn.execute("""SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND k.status != 'Fallen'
                                       AND c.type = 'Village' AND c.households > 10 ORDER BY c.id LIMIT 1""", (world_id,)).fetchone())
        town = conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village' ORDER BY id LIMIT 1", (village["kingdom_id"],)).fetchone()[0]
        for house in conn.execute("SELECT id, capacity FROM buildings WHERE city_id = ? AND type = 'House'", (town,)).fetchall():
            have = conn.execute("SELECT COUNT(*) FROM npcs WHERE home_id = ? AND alive = 1", (house["id"],)).fetchone()[0]
            for _ in range(house["capacity"] - have):
                V.make_individual(conn, village["id"], town, "Laborer")         # every house in town full
        conn.commit()
        saved = SL.BANDIT_CHANCE
        SL.BANDIT_CHANCE = 0.0
        try:
            family = V._move_family(conn, village, town)
        finally:
            SL.BANDIT_CHANCE = saved
        slums = SL.slum_ids(conn, town)
        assert family and slums, "a family fleeing to a town with no free house did not come (or the town has no slums)"
        homes = {conn.execute("SELECT home_id FROM npcs WHERE id = ?", (m["id"],)).fetchone()[0] for m in family}
        assert homes == slums, "refugees with no house free do not live in the slums"
        in_slums = CT.life_levels(conn, world_id, today(conn, world_id))[family[1]["id"]]     # the same person, before and after
        adults = [m["id"] for m in family if m["age"] >= 16]
        conn.execute(f"UPDATE npcs SET gold = 500 WHERE id IN ({','.join(map(str, adults))})")
        conn.execute("UPDATE npcs SET home_id = NULL WHERE home_id = (SELECT id FROM buildings WHERE city_id = ? AND type = 'House' ORDER BY id LIMIT 1)", (town,))
        conn.commit()
        others = [m['id'] for m in family]                     # only this family can pay: earlier refugees in these
        conn.execute(f"UPDATE npcs SET gold = 0 WHERE home_id IN ({','.join(map(str, slums))}) AND id NOT IN ({','.join(map(str, others))})")
        conn.commit()                                          # slums would otherwise rightly take the freed house first
        SL.rehouse(conn, world_id)
        homes = {conn.execute("SELECT home_id FROM npcs WHERE id = ?", (m["id"],)).fetchone()[0] for m in family}
        assert len(homes) == 1 and not homes & slums, "a slum family able to pay did not move into a house together"
        housed = CT.life_levels(conn, world_id, today(conn, world_id))[family[1]["id"]]
        assert housed - in_slums >= (CT.HOUSED - CT.SLUMS) - 1, f"moving out of the slums did not make life better ({in_slums:.0f} -> {housed:.0f})"

    run_step(r, "refugees with no house free live in the slums, and move into a house together once they can pay", refugees_and_slums)

    def monster_raids():
        import Data.Monsters.raids as RD
        village = dict(conn.execute("""SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?
                                       AND k.status != 'Fallen' AND c.type = 'Village' AND c.households > 5 ORDER BY c.id LIMIT 1""", (world_id,)).fetchone())
        lair_id = conn.execute("""INSERT INTO monster_lairs (world_id, monster, kind, map_x, map_y, count, hit_points_each, status)
                                 VALUES (?, 'Orc', 'raider', ?, ?, 80, 4.5, 'Active')""", (world_id, village["map_x"] + 5, village["map_y"])).lastrowid   # right beside it: no other village nearer
        conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE world_id = ? AND id != ? AND kind = 'raider'", (world_id, lair_id))
        conn.commit()
        households = village["households"]
        food = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (village["id"],)).fetchone()[0]
        saved = RD.RAID_CHANCE
        RD.RAID_CHANCE = 1.0
        try:
            reports = RD.monster_raids(conn, world_id)
        finally:
            RD.RAID_CHANCE = saved
        assert len(reports) == 1 and reports[0]["village"] == village["name"], f"the orcs did not raid the village beside them: {reports}"
        rep = reports[0]
        left = conn.execute("SELECT count FROM monster_lairs WHERE id = ?", (lair_id,)).fetchone()[0]
        assert left == 80 - rep["raiders_fallen"], "the lair did not lose exactly its fallen raiders"
        dead = conn.execute("SELECT COUNT(*), SUM(died_day IS NOT NULL) FROM npcs WHERE city_id = ? AND alive = 0", (village["id"],)).fetchone()
        assert dead[0] >= rep["named_dead"] and dead[1] >= rep["named_dead"], "villagers slain in the raid are not dead and dated"
        now = conn.execute("SELECT households FROM cities WHERE id = ?", (village["id"],)).fetchone()[0]
        assert abs((households - now) - rep["militia_dead"] / RD.FAMILY_SIZE) < 0.01, "the village did not lose households for its fallen militia"
        after = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'", (village["id"],)).fetchone()[0]
        if rep["raiders_won"]:
            assert abs(after - food * (1 - RD.FOOD_TAKEN)) < 1, "victorious raiders did not carry off a quarter of the Food"
        if rep["named_dead"] + rep["militia_dead"] >= RD.CHRONICLED:
            assert conn.execute("SELECT 1 FROM chronicle_entries WHERE event_type = 'Monster Raid' AND kingdom_id = ?", (village["kingdom_id"],)).fetchone(), \
                "a great raid was not written into the kingdom's chronicle"
        assert conn.execute("SELECT 1 FROM events WHERE type = 'Monster Raid' AND city_id = ?", (village["id"],)).fetchone(), "the raid left no event"
        conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE id = ?", (lair_id,))
        conn.commit()

    run_step(r, "monster raids: the lair loses its fallen, the village its dead (mourned), households and Food; great raids are chronicled", monster_raids)

    def beasts_and_travelers():
        import Data.Monsters.travelers as TV
        from Data.Infrastructure.caravans import dispatch_caravan
        living = [row[0] for row in conn.execute(
            "SELECT capital_city_id FROM kingdoms WHERE world_id = ? AND status != 'Fallen' AND capital_city_id IS NOT NULL ORDER BY id LIMIT 2", (world_id,))]
        origin, destination = living
        kingdom = conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (origin,)).fetchone()[0]
        caravan = dispatch_caravan(conn, origin, destination, "Food", 200, kingdom, kingdom)
        assert caravan, "(setup) the caravan did not set out"
        conn.commit()
        spot = next(((x, y) for k, i, x, y, row in TV.on_the_road(conn, world_id) if k == "caravan" and i == caravan["id"]), None)
        assert spot, "a caravan on the road was not placed along it"
        conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE world_id = ? AND kind = 'beast'", (world_id,))
        lair = conn.execute("""INSERT INTO monster_lairs (world_id, monster, kind, map_x, map_y, count, hit_points_each, status)
                              VALUES (?, 'Wolf', 'beast', ?, ?, 60, 9, 'Active')""", (world_id, spot[0] + 10, spot[1])).lastrowid
        conn.commit()
        import Data.Magic.magic as MG
        saved, calm = TV.AMBUSH_CHANCE, MG.CALM_SHARE
        TV.AMBUSH_CHANCE, MG.CALM_SHARE = 1.0, 1.0           # a druid nearby would calm the wolves: not today
        try:
            reports = [r for r in TV.beasts_on_the_roads(conn, world_id) if r["kind"] == "caravan" and r["id"] == caravan["id"]]
        finally:
            TV.AMBUSH_CHANCE, MG.CALM_SHARE = saved, calm
        assert reports, "wolves beside a caravan's road did not fall on it"
        rep = reports[0]
        status = conn.execute("SELECT status FROM caravans WHERE id = ?", (caravan["id"],)).fetchone()[0]
        assert (status == "Raided") == rep["beasts_won"], "a caravan beaten by beasts kept its goods (or lost them though it won)"
        left = conn.execute("SELECT count FROM monster_lairs WHERE id = ?", (lair,)).fetchone()[0]
        assert left == 60 - rep["beasts_fallen"], "the lair did not lose exactly its fallen"
        conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE id = ?", (lair,))
        conn.execute("UPDATE caravans SET status = 'Delivered' WHERE id = ? AND status = 'En Route'", (caravan["id"],))
        conn.commit()

    run_step(r, "beasts near a road fall on caravans and travelers; the beaten lose their goods or lives, the lair its fallen", beasts_and_travelers)

    def bandits_near_camps():
        import Data.Infrastructure.caravans as CV
        camps = CV.bandit_camps(conn, world_id)
        assert camps, "no bandit camp in the world has bandits in it"
        x, y = camps[0]
        assert CV.bandit_chance(camps, (x, y)) >= CV.BANDIT_CHANCE, "bandits do not threaten the road beside their own camp"
        far = (x + 10 * CV.BANDIT_REACH, y + 10 * CV.BANDIT_REACH)
        assert CV.bandit_chance([(x, y)], far) == 0, "bandits strike far from any camp"
        assert CV.bandit_chance([(x, y), (x + 1, y)], (x, y)) > CV.bandit_chance([(x, y)], (x, y)), "two camps near a road are no worse than one"

    run_step(r, "bandits strike caravans only near their camps, more where camps cluster", bandits_near_camps)

    def armor_protects():
        import random as random_module
        import Data.Combat.combat as C
        import Data.Government.armies as A
        from Data.Database.repository import get_kingdom, get_city, save_city, get_npc

        assert C.armor_share(7, 10) == 0.7 and C.armor_share(20, 10) == 1.0, "armor share is not suits per fighter, at most one each"
        soldiers = [get_npc(conn, row[0]) for row in conn.execute("SELECT id FROM npcs WHERE alive = 1 AND profession = 'Soldier' LIMIT 10")]
        bare, clad = C.side_power(soldiers, 0.0), C.side_power(soldiers, 1.0)
        assert abs(clad - bare * (1 + C.ARMOR_POWER)) < 0.01, f"a fully armored side does not fight at 1.5 times its strength ({bare} -> {clad})"

        stand_ins = lambda n: [{"id": -i, "health": 100, "alive": True, "skills": {}, "profession": "Soldier", "workplace": None, "home": None} for i in range(n)]
        state, real_save = random_module.getstate(), C.save_npc
        C.save_npc = lambda *a, **kw: None                # stand-in soldiers are not in the database
        try:
            random_module.seed(1); unarmored = len(C.apply_casualties(conn, stand_ins(20000), won=False, armor_share=0.0))
            random_module.seed(1); armored = len(C.apply_casualties(conn, stand_ins(20000), won=False, armor_share=1.0))
        finally:
            C.save_npc = real_save
            random_module.setstate(state)
        assert 0.5 < armored / unarmored < 0.7, f"full armor should cut deaths to about 60% (it cut them to {armored / unarmored:.0%})"

        kingdom = get_kingdom(conn, kingdoms[0]["id"])
        capital = get_city(conn, kingdom["capital"])
        capital["resources"].update({"Food": 10.0 ** 5, "Armor": 3.0})
        save_city(conn, capital)
        conn.commit()
        army = A.raise_army(conn, kingdom, get_city(conn, kingdom["capital"]), size=5)
        if army:
            carried = A.get_army(conn, army["id"])["armor"]
            A.disband_army(conn, army["id"])
            assert abs(carried - 3.0) < 0.01, f"an army of 5 did not take the capital's 3 suits of armor (took {carried})"

    run_step(r, "armor: armies carry it; it adds half again to strength and saves two lives in five", armor_protects)

    def succession_shakes_relationships():
        from Data.Database.repository import get_kingdom, adjust_kingdom_favor, get_kingdom_favor

        # a crown: a republic's successor is elected, not crowned (Data.Government.republic)
        crown_id = conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND type = 'Crown' ORDER BY id LIMIT 1",
                                (world_id,)).fetchone()[0]
        kingdom = next(k for k in kingdoms if k["id"] == crown_id)
        others = [k for k in kingdoms if k["id"] != crown_id]

        monarch = get_kingdom(conn, kingdom["id"])["ruler"]
        assert monarch is not None, "no monarch was crowned by the monthly tick"

        conn.execute("DELETE FROM kingdom_favor")
        for other in others:
            adjust_kingdom_favor(conn, kingdom["id"], other["id"], 100)
            adjust_kingdom_favor(conn, other["id"], kingdom["id"], 100)
        from Data.History.chronicle import get_chronicle
        coronations = lambda: [e for e in get_chronicle(conn, kingdom["id"]) if e["event_type"] == "Coronation"]
        before = len(coronations())          # earlier steps (the regent's) may already have crowned a successor here
        rulers_before = {o["id"]: get_kingdom(conn, o["id"])["ruler"] for o in others}
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (monarch,))
        conn.commit()

        manager.on_month()

        assert get_kingdom(conn, kingdom["id"])["ruler"] != monarch, "the dead monarch was not replaced"
        crowned = coronations()
        old_name = conn.execute("SELECT name FROM npcs WHERE id = ?", (monarch,)).fetchone()["name"]
        assert len(crowned) == before + 1 and old_name in crowned[-1]["text"], \
            "the successor's crowning was not chronicled, or does not name the monarch it replaced"
        shaken = [get_kingdom_favor(conn, kingdom["id"], o["id"]) for o in others]
        assert all(abs(f - 100) > 1e-6 for f in shaken), "a new monarch was crowned but the kingdom's feelings were not shaken"
        # (a neighbor whose own monarch also died this month -- a duel, an assassin -- is rightly shaken too)
        steady = [o for o in others if get_kingdom(conn, o["id"])["ruler"] == rulers_before[o["id"]]]
        untouched = [get_kingdom_favor(conn, o["id"], kingdom["id"]) for o in steady]
        if not all(f == 100 for f in untouched):
            moved = [(o["name"], round(get_kingdom_favor(conn, o["id"], kingdom["id"]), 2)) for o in steady
                     if get_kingdom_favor(conn, o["id"], kingdom["id"]) != 100]
            edicts = [tuple(e) for e in conn.execute("SELECT kingdom_id, edict FROM kingdom_edicts ORDER BY rowid DESC LIMIT 4")]
            raise AssertionError(f"a neighbor's feelings toward {kingdom['name']} changed although only it changed ruler: {moved}; "
                                 f"latest edicts {edicts}; this kingdom is {kingdom['id']}")

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

        def casualties_with_the_monarch(conn_, side, won, armor_share=0.0):      # wars now pass each side's armor
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

        # any kingdom whose monarch is alive: an earlier step's war can kill a
        # monarch who happens to be a soldier, so do not assume the first one
        def living_monarch(k):
            ruler = get_kingdom(conn, k["id"]).get("ruler")
            npc = get_npc(conn, ruler) if ruler else None
            return npc if npc and npc["alive"] else None

        chosen = next((k for k in kingdoms if living_monarch(k)), None)
        assert chosen, "(setup) no kingdom has a living monarch"
        kingdom = get_kingdom(conn, chosen["id"])
        cities = get_kingdom_cities(conn, kingdom["id"])
        monarch = living_monarch(kingdom)
        sovereign = f"{monarch['title']} {monarch['name']}"

        # an armed uprising, in which the monarch is sure to fall
        capital = next(c for c in cities if c["id"] == kingdom["capital"])
        capital["resources"]["Weapons"] = 5000.0
        last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM chronicle_entries").fetchone()[0]
        real_casualties, calls = combat.apply_casualties, {"n": 0}

        def casualties_with_the_monarch(conn_, side, won, armor_share=0.0):      # wars now pass each side's armor
            dead = real_casualties(conn_, side, won)
            calls["n"] += 1
            if calls["n"] == 1 and all(n["id"] != monarch["id"] for n in dead):
                fallen = get_npc(conn_, monarch["id"])
                fallen["alive"] = False
                save_npc(conn_, fallen)
                dead.append(fallen)
            return dead

        combat.apply_casualties = casualties_with_the_monarch
        conn.execute("UPDATE kingdoms SET price_level = 2.0 WHERE id = ?", (kingdom["id"],))
        conn.commit()
        try:
            result = combat.resolve_civil_war(conn, kingdom, cities, current_year=1)
        finally:
            combat.apply_casualties = real_casualties
        conn.commit()
        assert result["outcome"] != "no_battle", "(setup) the uprising never came to battle"
        level = conn.execute("SELECT price_level FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone()[0]
        expected = 1.0 if result["outcome"] == "rebels_win" else 2.0
        assert level == expected, f"after a civil war ({result['outcome']}) food prices should be {expected}x, but are {level}x"

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

        def kills_and_grievances():
            import Data.Combat.nemesis as N
            from Data.Database.repository import get_npc

            # the war and the civil war just fought each slew a monarch: the real battle code must have recorded it
            for context in ("war", "civil war"):
                slain = conn.execute("SELECT killer_id FROM killings WHERE context = ? AND victim_rank = 'monarch'", (context,)).fetchone()
                assert slain, f"a monarch fell in a {context}, but no killing was recorded"
                epithet = conn.execute("SELECT epithet FROM npcs WHERE id = ?", (slain["killer_id"],)).fetchone()["epithet"]
                assert epithet and epithet.endswith("slayer"), f"the monarch's killer in the {context} earned no epithet ({epithet})"
                assert conn.execute("SELECT kills FROM npcs WHERE id = ?", (slain["killer_id"],)).fetchone()["kills"] >= 1, "a killer's tally did not count the kill"

            # a Knight is slain: their child takes up a grievance against the killer, and it is one-way
            pair = conn.execute("SELECT id FROM npcs WHERE alive = 1 AND title IS NULL AND profession = 'Soldier' ORDER BY id LIMIT 2").fetchall()
            child = conn.execute("SELECT id FROM npcs WHERE alive = 1 AND title IS NULL AND profession NOT IN ('Soldier', 'Child') ORDER BY id DESC LIMIT 1").fetchone()["id"]
            knight, killer = get_npc(conn, pair[0]["id"]), get_npc(conn, pair[1]["id"])
            knight["profession"] = "Knight"
            conn.execute("UPDATE npcs SET profession = 'Knight', alive = 0 WHERE id = ?", (knight["id"],))
            conn.execute("UPDATE npcs SET parent_a_id = ? WHERE id = ?", (knight["id"], child))
            renown = N.renown_of(conn, killer["id"])
            slayings = N.record_battle(conn, [knight], [knight], [killer], [], context="war")
            assert slayings == [(killer["id"], knight["id"], "knight")], "a Knight's death was not recorded as a killing"
            assert N.renown_of(conn, killer["id"]) == renown + N.KILL_RENOWN + N.RANK_RENOWN["knight"], "slaying a Knight did not earn its renown"
            held = conn.execute("SELECT amount FROM grievances WHERE holder_id = ? AND target_id = ?", (child, killer["id"])).fetchone()
            assert held and held["amount"] == N.GRIEVANCE["child"], "a slain Knight's child holds no grievance against the killer"
            assert not conn.execute("SELECT 1 FROM grievances WHERE holder_id = ? AND target_id = ?", (killer["id"], child)).fetchone(), \
                "a grievance was recorded both ways"

            # revenge in battle: the child meets the killer across the line, and settles it before the general fighting
            import random as random_module
            import Data.Combat.combat as combat_module
            avenger, nemesis = get_npc(conn, child), get_npc(conn, killer["id"])
            saved = N.random
            N.random = type("AvengerWins", (), {"random": lambda self: 0.0, "__getattr__": lambda self, name: getattr(random_module, name)})()
            try:
                duels = N.seek_nemeses(conn, [avenger], [nemesis])
            finally:
                N.random = saved
            assert duels == [(child, killer["id"], True)], "an avenger did not seek out their nemesis on the other side of a battle"
            assert not conn.execute("SELECT alive FROM npcs WHERE id = ?", (killer["id"],)).fetchone()["alive"], "the nemesis slain in vengeance is not dead"
            assert conn.execute("SELECT settled FROM grievances WHERE holder_id = ? AND target_id = ?", (child, killer["id"])).fetchone()["settled"] == 1, \
                "vengeance taken did not settle the grievance"
            assert conn.execute("SELECT epithet FROM npcs WHERE id = ?", (child,)).fetchone()["epithet"], "the avenger earned no name"
            assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Vengeance'").fetchone()[0] >= 1, "the vengeance was not chronicled"
            assert combat_module.apply_casualties(conn, [nemesis], won=False) == [], "the general fighting killed the already slain a second time"
            conn.commit()

        run_step(r, "battle kills are credited; a slain monarch's killer is named; a slain Knight's child holds a grievance, and takes vengeance in battle", kills_and_grievances)

        def revenge_in_peacetime():
            import random as random_module
            import Data.Combat.nemesis as N
            import Data.Combat.vendetta as V

            class Dice:
                def __init__(self, *rolls): self.rolls = list(rolls)
                def random(self): return self.rolls.pop(0) if len(self.rolls) > 1 else self.rolls[0]
                def __getattr__(self, name): return getattr(random_module, name)

            towns = [row[0] for row in conn.execute(
                """SELECT c.id FROM cities c WHERE c.type != 'Village' AND c.kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ? AND status != 'Fallen')
                   AND (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1 AND n.title IS NULL AND n.profession NOT IN ('Child', 'Thief')) >= 6
                   ORDER BY c.id LIMIT 2""", (world_id,))]
            used = set()

            def cast(same_town=True, age=40, trade="Farmer", foe="Soldier", gold=0, boldness=0):
                def pick(town):
                    marks = ",".join(str(i) for i in used) or "0"
                    npc = conn.execute(f"SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND title IS NULL AND profession NOT IN ('Child', 'Thief') "
                                       f"AND id NOT IN ({marks}) AND id NOT IN (SELECT npc_id FROM army_members) ORDER BY id DESC LIMIT 1", (town,)).fetchone()[0]
                    used.add(npc)
                    return npc
                avenger, nemesis = pick(towns[0]), pick(towns[0] if same_town else towns[1])
                conn.execute("DELETE FROM npc_skills WHERE npc_id IN (?, ?)", (avenger, nemesis))
                conn.execute("DELETE FROM npc_travels WHERE npc_id IN (?, ?)", (avenger, nemesis))
                conn.execute("UPDATE npcs SET age = ?, profession = ?, gold = ?, boldness = ? WHERE id = ?", (age, trade, gold, boldness, avenger))
                conn.execute("UPDATE npcs SET age = 35, profession = ? WHERE id = ?", (foe, nemesis))
                conn.execute("UPDATE grievances SET settled = 1")                       # only this feud is live
                conn.execute("INSERT OR REPLACE INTO grievances (holder_id, target_id, amount, reason, since_day, settled) VALUES (?, ?, 100, 'slew their parent, Edmund', 0, 0)",
                             (avenger, nemesis))
                conn.commit()
                return avenger, nemesis

            alive = lambda npc: conn.execute("SELECT alive FROM npcs WHERE id = ?", (npc,)).fetchone()[0]
            saved_v, saved_n = V.random, N.random
            try:
                # a youth no match for a Knight trains
                avenger, nemesis = cast(age=18, foe="Knight")
                assert V.pursue_vendettas(conn, world_id) == [(avenger, "train")], "a young avenger no match for their nemesis did not train"
                learned = conn.execute("SELECT level FROM npc_skills WHERE npc_id = ? AND skill_name = ?", (avenger, V.TRAINING_SKILL)).fetchone()
                assert learned and learned[0] == 1, "a month's training taught the avenger nothing"

                # a ready avenger duels their nemesis in the town they share
                avenger, nemesis = cast(trade="Soldier", foe="Farmer")
                V.random, N.random = Dice(0.0), Dice(0.0)
                assert V.pursue_vendettas(conn, world_id) == [(avenger, "duel: won")] and not alive(nemesis), "a ready avenger did not duel their nemesis"
                assert conn.execute("SELECT context FROM killings ORDER BY id DESC LIMIT 1").fetchone()[0] == "duel", "the duel was not recorded as one"

                # a nemesis elsewhere: the avenger sets out, and fights on arriving
                avenger, nemesis = cast(same_town=False, trade="Soldier", foe="Farmer")
                V.random, N.random = Dice(0.0), saved_n
                assert V.pursue_vendettas(conn, world_id) == [(avenger, "journey")], "an avenger did not set out to find a nemesis who lives elsewhere"
                conn.execute("UPDATE npc_travels SET status = 'Staying', days_remaining = 3 WHERE npc_id = ? AND purpose = 'Vengeance'", (avenger,))
                conn.commit()
                N.random = Dice(0.0)
                assert V.arrivals(conn, world_id) == [(avenger, nemesis, True)] and not alive(nemesis), "an avenger who arrived did not fight their nemesis"

                # too old to train, no match for a Knight, but rich: a hired blade
                avenger, nemesis = cast(age=60, foe="Knight", gold=1000)
                town = conn.execute("SELECT city_id FROM npcs WHERE id = ?", (nemesis,)).fetchone()[0]
                conn.execute("UPDATE npcs SET profession = 'Laborer' WHERE city_id = ? AND profession = 'Thief'", (town,))
                blade = conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND title IS NULL AND profession != 'Child' AND id NOT IN (?, ?) ORDER BY id LIMIT 1",
                                     (town, avenger, nemesis)).fetchone()[0]
                conn.execute("UPDATE npcs SET profession = 'Thief' WHERE id = ?", (blade,))
                conn.commit()
                V.random, N.random = Dice(0.0, 0.999999), saved_n
                assert V.pursue_vendettas(conn, world_id) == [(avenger, "assassin: killed")] and not alive(nemesis), "a rich avenger's hired blade did not kill"
                last = conn.execute("SELECT killer_id, context FROM killings ORDER BY id DESC LIMIT 1").fetchone()
                assert (last["killer_id"], last["context"]) == (blade, "assassination"), "the murder was not recorded as the hired Thief's"
                assert conn.execute("SELECT gold FROM npcs WHERE id = ?", (avenger,)).fetchone()[0] == 1000 - V.ASSASSIN_FEE, "the patron did not pay the blade"
            finally:
                V.random, N.random = saved_v, saved_n
                conn.execute("UPDATE grievances SET settled = 1")
                conn.commit()

        run_step(r, "in peacetime an avenger trains, duels, journeys to their nemesis, or hires a blade", revenge_in_peacetime)

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
            # Leave out the city's tool-makers: a city with a Mine, a Smelter and a
            # Blacksmith can dig Ore, smelt Steel and forge Tools in a single day,
            # from nothing, and that output would hide the wear being tested.
            rules = economy.load_json(economy.BUILDING_PRODUCTION)
            city["buildings"] = [
                b for b in city["buildings"]
                if "Tools" not in (rules.get(b["type"], {}).get("resource"), rules.get(b["type"], {}).get("secondary_resource"))
            ]
            economy.produce_resources(city)
            return sum(v for k, v in city["resources"].items() if k != "Tools"), city["resources"]["Tools"]

        made_without, _ = made_with(0)
        made_with_plenty, tools_left = made_with(100000)
        assert made_without < made_with_plenty * 0.75, "a city with no Tools produced nearly as much as one with plenty"
        if not tools_left < 100000:
            city = get_city(conn, city_id)
            rules = economy.load_json(economy.BUILDING_PRODUCTION)
            working = sum(len(b["workers"]) for b in city["buildings"] if rules.get(b["type"]))
            raise AssertionError(
                f"working did not wear out any Tools (city {city['name']} [{city['type']}], {working} producing workers, "
                f"Tools {tools_left} after a day, made {made_with_plenty:.1f} goods)"
            )

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

        # enough Food for any march this step allows (up to 400 days), so the
        # army never runs out on the way -- a long route would otherwise empty
        # its wagons and make the eating check below meaningless
        army = A.raise_army(conn, kingdom, capital, size=5, food_days=400)
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

        def eaters():
            return conn.execute(f"SELECT COUNT(*) FROM npcs WHERE id IN ({members}) AND alive = 1").fetchone()[0]

        eaten = eaters()          # the first day's meal was eaten after any deaths that day
        days = 1
        while A.get_army(conn, army["id"])["status"] == "Marching" and days < 400:
            eaten += eaters()
            A.advance_armies(conn, kingdom["world_id"])
            days += 1
        arrived = A.get_army(conn, army["id"])
        assert arrived["status"] == "Encamped" and arrived["location_city_id"] == target["id"], "the army never reached its destination"
        assert days == road_days, f"the march took {days} days, but the roads add up to {road_days}"
        assert abs(arrived["food"] - (army["food"] - eaten)) < 0.01, \
            f"the army did not eat 1 Food per living soldier per day ({army['food']} Food at the start, " \
            f"{eaten} soldier-days eaten over {days} days, {arrived['food']} left)"
        assert not A.disband_army(conn, army["id"]), "an army was allowed to disband abroad"

        # a full treasury first: earlier steps (a war) can drain it, and an unpaid
        # army deserts -- which would try to save these four stand-in soldiers
        conn.execute("UPDATE kingdoms SET treasury = 1000000 WHERE id = ?", (kingdom["id"],))
        conn.commit()
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

    def supply_lines():
        from Data.Database.repository import get_kingdom, get_city, get_kingdom_cities, save_city
        from Data.Government import armies as A
        import Data.Government.supply as S
        from Data.Infrastructure.travel import get_travel_days

        class Dice:
            def __init__(self, roll): self.roll = roll
            def random(self): return self.roll

        kingdom = get_kingdom(conn, kingdoms[0]["id"])
        capital = get_city(conn, kingdom["capital"])
        capital["resources"].update({"Food": 20000.0, "Weapons": 500.0, "Potions": 50.0})
        save_city(conn, capital)
        target = next(
            (c for other in kingdoms[1:] for c in get_kingdom_cities(conn, other["id"])
             if A.find_route(conn, capital["id"], c["id"])),
            None
        )
        assert target, "(setup) no city in another kingdom can be reached by road"

        army = A.raise_army(conn, kingdom, get_city(conn, kingdom["capital"]), size=5)
        A.march_army(conn, army["id"], target["id"])
        while A.get_army(conn, army["id"])["status"] == "Marching":
            A.advance_armies(conn, kingdom["world_id"])

        stops = [capital["id"]] + A.find_route(conn, capital["id"], target["id"])
        journey = sum(get_travel_days(conn, a, b) for a, b in zip(stops, stops[1:]))
        threshold = S.resupply_threshold_days(conn, capital["id"], A.get_army(conn, army["id"]))
        assert threshold == max(S.RESUPPLY_BELOW_DAYS, journey + S.RESUPPLY_SAFETY_DAYS), \
            f"an army {journey} days from home asks at {threshold} days of Food, not its journey plus the safety margin"

        real_random, real_bandit_chance = S.random, S.bandit_chance
        S.random = Dice(0.99)          # no raids, so the delivery is certain
        try:
            conn.execute("UPDATE armies SET food = 10 WHERE id = ?", (army["id"],))
            conn.commit()
            manager.on_day()
            sent = conn.execute("SELECT * FROM supply_caravans WHERE army_id = ?", (army["id"],)).fetchall()
            assert len(sent) == 1, "the daily tick did not send supplies to a hungry army in the field"
            # (a whole day also moves the capital's Food through farming, eating and
            # trade deliveries, so check the caravan itself rather than the stock)
            assert sent[0]["origin_city_id"] == capital["id"] and sent[0]["food"] > 0, \
                "the supply caravan did not set out from the capital carrying Food"

            days = 1
            while conn.execute("SELECT status FROM supply_caravans WHERE army_id = ?", (army["id"],)).fetchone()[0] == "En Route" and days < 400:
                A.advance_armies(conn, kingdom["world_id"])
                S.advance_supply_lines(conn, kingdom["world_id"])
                days += 1
            status = conn.execute("SELECT status FROM supply_caravans WHERE army_id = ?", (army["id"],)).fetchone()[0]
            assert status == "Delivered", f"the supply caravan never reached the camp ({status})"
            assert days == journey, f"the supply caravan took {days} days, but the roads add up to {journey}"
            cargo = conn.execute("SELECT food FROM supply_caravans WHERE army_id = ?", (army["id"],)).fetchone()[0]
            assert A.get_army(conn, army["id"])["food"] >= cargo, \
                f"the caravan delivered {cargo} Food, but the army's wagons hold only {A.get_army(conn, army['id'])['food']}"

            conn.execute("UPDATE armies SET food = 10 WHERE id = ?", (army["id"],))
            conn.commit()
            S.random = Dice(0.0)       # this one is raided...
            S.bandit_chance = lambda camps, here: 1.0      # ...its road within bandits' reach, wherever the camps are
            S.advance_supply_lines(conn, kingdom["world_id"])
            raided = conn.execute("SELECT COUNT(*) FROM supply_caravans WHERE army_id = ? AND status = 'Raided'", (army["id"],)).fetchone()[0]
            logged = conn.execute("SELECT COUNT(*) FROM events WHERE type = 'Supply Raided'").fetchone()[0]
            assert raided == 1 and logged >= 1, "a raided supply caravan was not lost, or the raid was not logged"
        finally:
            S.random = real_random
            S.bandit_chance = real_bandit_chance

    if len(kingdoms) >= 2:
        run_step(r, "a supply caravan reaches an army in the field at road speed, and can be raided", supply_lines)

    def year_tick_and_grief():
        import Data.NPCS.aging as aging
        from Data.Database.repository import get_npc

        # A couple in one city where the one who dies comes EARLIER in the city's
        # list -- the case where the yearly tick used to save the survivor's old
        # copy over their grief, leaving them married to the dead.
        city = conn.execute(
            """SELECT city_id FROM npcs WHERE alive = 1 AND title IS NULL AND profession NOT IN ('Laborer', 'Child')
               GROUP BY city_id HAVING COUNT(*) >= 2 LIMIT 1"""
        ).fetchone()["city_id"]
        pool = [row["id"] for row in conn.execute(
            """SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND title IS NULL
               AND profession NOT IN ('Laborer', 'Child') ORDER BY id""", (city,))]
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

    forced_war = {}

    def force_a_war():
        # right before the year tick: the first crown's diplomacy picks a war with a reachable enemy
        import Data.Government.diplomacy as D
        import Data.Government.campaigns as CP
        from Data.Database.repository import get_kingdom
        conn.execute("UPDATE campaigns SET status = 'Over'")
        conn.commit()
        for a in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND type = 'Crown' ORDER BY id", (world_id,)).fetchall():
            for d in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND id != ? ORDER BY id", (world_id, a["id"])).fetchall():
                if CP.choose_target(conn, get_kingdom(conn, a["id"]), get_kingdom(conn, d["id"])):
                    forced_war.update(attacker=a["id"], defender=d["id"], real=D.choose_war_target)
                    D.choose_war_target = lambda conn_, kid, cands: (None, forced_war["defender"]) if kid == forced_war["attacker"] else None
                    return

    run_step(r, "(setup) the first crown's diplomacy chooses a war just before the year tick", force_a_war)

    run_step(r, "year tick  (aging, wars, civil war check) -- and a widow's grief survives it", year_tick_and_grief)

    def year_tick_sends_campaign():
        import Data.Government.diplomacy as D
        if "real" in forced_war:
            D.choose_war_target = forced_war["real"]
        if forced_war:
            assert conn.execute("SELECT COUNT(*) FROM campaigns WHERE attacker_kingdom_id = ? AND status = 'Marching'",
                                (forced_war["attacker"],)).fetchone()[0], "a war declared in the year tick did not become a campaign"

    run_step(r, "a war declared in the year tick becomes a campaign on the march", year_tick_sends_campaign)

    def capitals_fall():
        import random as random_module
        import Data.Government.conquest as CQ
        from Data.Combat.combat import transfer_conquest
        from Data.Database.repository import get_kingdom, get_city, get_npc, get_world_kingdoms

        class Flight:
            def choice(self, seq): return "flight"
            def __getattr__(self, name): return getattr(random_module, name)

        crowns = [k["id"] for k in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND type = 'Crown' AND status != 'Fallen' ORDER BY id", (world_id,))]
        living = [k["id"] for k in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND status != 'Fallen' ORDER BY id", (world_id,))]
        attacker = get_kingdom(conn, crowns[0])
        defender = get_kingdom(conn, next(k for k in living if k != attacker["id"] and conn.execute(   # one with a town besides its capital
        "SELECT COUNT(*) FROM cities WHERE kingdom_id = ? AND id != (SELECT capital_city_id FROM kingdoms WHERE id = ?)", (k, k)).fetchone()[0]))
        others = conn.execute("SELECT COUNT(*) FROM cities WHERE kingdom_id = ? AND id != ?", (defender["id"], defender["capital"])).fetchone()[0]
        assert others, "(setup) the defender has no town but its capital"

        # a capital falls, its ruler flees: the kingdom moves its seat
        capital = get_city(conn, defender["capital"])
        ruler = defender["ruler"]
        ruler_alive = bool(ruler) and bool(get_npc(conn, ruler)["alive"])     # an earlier step may have killed this monarch
        transfer_conquest(conn, capital, attacker, defender)
        saved = CQ.random
        CQ.random = Flight()
        try:
            CQ.fall_of_capital(conn, capital, attacker, get_kingdom(conn, defender["id"]))
        finally:
            CQ.random = saved
        after = get_kingdom(conn, defender["id"])
        assert after["capital"] and after["capital"] != capital["id"], "a kingdom whose capital fell did not move its seat"
        assert get_city(conn, after["capital"])["type"] == "Capital" and get_city(conn, capital["id"])["type"] != "Capital", \
            "the new and fallen capitals were not retitled"
        if ruler_alive:                                    # only a living ruler can flee
            assert get_npc(conn, ruler)["city_id"] == after["capital"], "the fleeing ruler did not reach the new capital"
        assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Capital Fallen'").fetchone()[0] >= 2, \
            "the fall of the capital was not chronicled by both kingdoms"

        # it loses everything: it falls, and is no longer counted among the living
        for c in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND id != ?", (defender["id"], after["capital"])).fetchall():
            transfer_conquest(conn, get_city(conn, c["id"]), attacker, get_kingdom(conn, defender["id"]))
        last = get_city(conn, after["capital"])
        transfer_conquest(conn, last, attacker, get_kingdom(conn, defender["id"]))
        CQ.fall_of_capital(conn, last, attacker, get_kingdom(conn, defender["id"]))
        assert conn.execute("SELECT status FROM kingdoms WHERE id = ?", (defender["id"],)).fetchone()[0] == "Fallen", \
            "a kingdom that lost its last town did not fall"
        assert defender["id"] not in [k["id"] for k in get_world_kingdoms(conn, world_id)], "a fallen kingdom is still counted among the living"
        assert conn.execute("SELECT COUNT(*) FROM chronicle_entries WHERE event_type = 'Kingdom Fallen'").fetchone()[0] >= 2, \
            "the kingdom's end was not chronicled by both sides"

        manager.on_day()                                   # the living world carries on
        manager.on_month()

    run_step(r, "a capital falls and the seat moves; a kingdom that loses everything falls, and the world goes on", capitals_fall)

    def settlers_found_villages():
        import math
        import Data.World.expansion as EX

        people = lambda: conn.execute("SELECT SUM(households) * 5 + SUM(population) FROM cities").fetchone()[0]
        before_ids = {row[0] for row in conn.execute("SELECT id FROM cities")}
        settled_regions = {row[0] for row in conn.execute("SELECT DISTINCT region_id FROM cities WHERE region_id IS NOT NULL")}
        households_before = conn.execute("SELECT SUM(households) FROM cities").fetchone()[0]
        assert EX.grow_villages(conn, world_id) >= 0
        assert conn.execute("SELECT SUM(households) FROM cities").fetchone()[0] >= households_before, "villages shrank in a year of growth"

        population = people()
        saved = (EX.CROWDED, EX.SETTLE_CHANCE, EX.REALM_CHANCE)
        EX.CROWDED, EX.SETTLE_CHANCE, EX.REALM_CHANCE = 0.0, 1.0, 0.0      # every kingdom sends settlers, none found a realm
        try:
            founded = EX.consider_settlement(conn, world_id)
        finally:
            EX.CROWDED, EX.SETTLE_CHANCE, EX.REALM_CHANCE = saved
        if not founded:                   # legitimate when no free land keeps the neutral ground (late in a crowded world)
            could = [k["name"] for k in conn.execute("SELECT id, name FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall()
            if EX.find_site(conn, k["id"], world_id)]
            assert not could, f"no kingdom sent settlers, though {', '.join(could)} had free land to settle"
            return
        assert abs(people() - population) < 0.01, "settling made people appear from nowhere (or lose them)"
        for village_id in founded:
            v = conn.execute("SELECT * FROM cities WHERE id = ?", (village_id,)).fetchone()
            assert village_id not in before_ids and v["type"] == "Village" and v["households"] > 0, "a settlement is not a new village with households"
            assert v["region_id"] not in settled_regions, "settlers took land in a region already settled"
            capital = conn.execute("SELECT capital_city_id FROM kingdoms WHERE id = ?", (v["kingdom_id"],)).fetchone()[0]
            assert conn.execute("SELECT 1 FROM roads WHERE (city_a_id = ? AND city_b_id = ?) OR (city_a_id = ? AND city_b_id = ?)",
                                (village_id, capital, capital, village_id)).fetchone(), "a new village has no road to its capital"
            nearest_foreign = min((math.dist((v["map_x"], v["map_y"]), (o["map_x"], o["map_y"]))
                                   for o in conn.execute("SELECT map_x, map_y FROM cities WHERE kingdom_id != ?", (v["kingdom_id"],))), default=1e9)
            assert nearest_foreign >= EX.NEUTRAL_GROUND - 45, \
                f"a new village sits {nearest_foreign:.0f} from another kingdom's settlement, inside the neutral ground ({EX.NEUTRAL_GROUND})"

    run_step(r, "villages grow; settlers found villages on empty land, keeping neutral ground between kingdoms", settlers_found_villages)

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