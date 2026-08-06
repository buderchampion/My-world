import time
from Data.Simulation.clock import Clock
from Data.Database.repository import (
    load_clock, save_clock, get_world_kingdoms, get_kingdom_cities,
    get_city_npcs, save_npc, save_city
)


class SimulationManager:
    """
    Owns a world's Clock and advances it one minute at a time, wiring
    coarser-grained rollovers (hour/day/month/season/year) to whatever
    systems should react to them.

    Currently wired:
    - DAY: Employment (wages + skill xp, taxed at each kingdom's own
      tax_rate into its treasury), Economy (production, consumption,
      kingdom trade), and a small daily chance of a raid for every
      city with a living Bandit Camp.
    - MONTH: Family formation (marriages/births), Government succession
      (a new ruler whenever a city's ruler slot is empty or its holder
      has died), Relationships (coworkers who don't already have one
      form a relationship), and Quests (regenerated for whatever needs
      one right now -- skips anything already open) -- for every city.
    - YEAR: aging + natural old-age death for every living NPC, and a
      small per-kingdom chance of war breaking out, weighted toward
      whichever neighboring kingdom is closest -- excluding anyone
      Allied or still under an active Peace Treaty. Every war
      automatically produces a Peace Treaty between the two combatants
      afterward (Data.Database.repository.set_kingdom_relation).

    Every rollover from month upward also gets a line in
    Logs/simulation.log (Data.Simulation.logger) marking when it
    happened, independent of any specific event.
    """

    def __init__(self, conn, world_id):
        self.conn = conn
        self.world_id = world_id
        self.clock = load_clock(conn, world_id)

    def tick(self) -> dict:
        rollover = self.clock.advance_minute()
        save_clock(self.conn, self.world_id, self.clock)
        self.conn.commit()

        from Data.Simulation.logger import log_line

        if rollover["hour"]:
            self.on_hour()
        if rollover["day"]:
            self.on_day()
        if rollover["month"]:
            log_line("CALENDAR", f"New month -- {self.clock}")
            self.on_month()
        if rollover["season"]:
            log_line("CALENDAR", f"Season changed to {self.clock.season} -- {self.clock}")
            self.on_season()
        if rollover["year"]:
            log_line("CALENDAR", f"New year -- {self.clock}")
            self.on_year()

        return rollover

    def on_hour(self) -> None:
        # Reserved for future wiring (e.g. needs decay, NPC location
        # resolution via Data.DailyLife.daily_life).
        pass

    def on_day(self) -> None:
        from Data.NPCS.employment import apply_workday
        from Data.Economy.economy import simulate_city_economy_day, simulate_kingdom_trade_day, total_resource_value
        from Data.Combat.combat import resolve_raid
        from Data.Database.repository import add_treasury, add_city_wealth
        import random
        
        # Chance, per city per day, that a city's own Bandit Camp (if
        # it has one, with living occupants) actually raids that day --
        # not a certainty every tick, so raids feel occasional rather
        # than relentless. ~78% chance of at least one raid per camp
        # over a 30-day month at this rate.
        RAID_CHANCE_PER_DAY = 0.05
        CITY_WEALTH_MARKET_FEE_RATE = 0.02

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            cities = get_kingdom_cities(self.conn, kingdom["id"])
            tax_collected = 0.0

            for city in cities:

                simulate_city_economy_day(city)
                
                market_revenue = round(total_resource_value(city) * CITY_WEALTH_MARKET_FEE_RATE, 2)
                if market_revenue > 0:
                    add_city_wealth(self.conn, city["id"], market_revenue)

                for npc in get_city_npcs(self.conn, city["id"]):
                    if npc["alive"] and npc["profession"] != "Unemployed":
                        tax_collected += apply_workday(npc, tax_rate=kingdom.get("tax_rate", 0.0))
                        save_npc(self.conn, npc)

                for building in city.get("buildings", []):
                    if building["type"] == "Bandit Camp" and building["workers"]:
                        if random.random() < RAID_CHANCE_PER_DAY:
                            resolve_raid(self.conn, city, building)

            if tax_collected > 0:
                add_treasury(self.conn, kingdom["id"], round(tax_collected, 2))

            simulate_kingdom_trade_day(cities)

            for city in cities:
                save_city(self.conn, city)

        self.conn.commit()

    def on_month(self) -> None:
        from Data.NPCS.family import form_families
        from Data.Government.government import assign_city_ruler, assign_kingdom_ruler
        from Data.Relationships.relationships import form_relationships
        from Data.Quests.quests import generate_quests_for_city
        from Data.Database.repository import get_npc, save_kingdom

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            cities = get_kingdom_cities(self.conn, kingdom["id"])

            for city in cities:

                form_families(self.conn, city)
                form_relationships(self.conn, city)

                nearby_cities = [c for c in cities if c["id"] != city["id"]]
                generate_quests_for_city(self.conn, city, nearby_cities=nearby_cities)

                ruler_id = city.get("ruler")
                ruler = get_npc(self.conn, ruler_id) if ruler_id is not None else None
                ruler_alive = bool(ruler and ruler["alive"])

                if not ruler_alive:

                    assign_city_ruler(self.conn, city)

                    if city["id"] == kingdom.get("capital"):
                        assign_kingdom_ruler(kingdom, city)
                        save_kingdom(self.conn, kingdom)

        self.conn.commit()

    def on_season(self) -> None:
        # Reserved.
        pass

    def on_year(self) -> None:
        from Data.NPCS.aging import apply_aging
        from Data.Events.events import death_event
        from Data.Combat.combat import resolve_kingdom_war
        from Data.Database.repository import get_kingdom, get_city, get_world_roads
        import random

        # Each kingdom independently rolls a small yearly chance of
        # going to war -- rare and weighty, not a constant background
        # hum. Roughly one war every 2-3 years across a typical
        # 5-kingdom world at this rate.
        WAR_CHANCE_PER_KINGDOM_PER_YEAR = 0.08

        kingdoms = get_world_kingdoms(self.conn, self.world_id)

        # Aging + natural old-age death for every living NPC.
        for kingdom in kingdoms:
            for city in get_kingdom_cities(self.conn, kingdom["id"]):
                for npc in get_city_npcs(self.conn, city["id"]):
                    if apply_aging(npc):
                        death_event(self.conn, npc, "old age", city_id=city["id"])
                    save_npc(self.conn, npc)

        self.conn.commit()

        # Kingdom wars: weighted toward whichever neighboring kingdom
        # is closest via the Trade Route road network, so kingdoms
        # that are actually near each other fight more often than
        # distant strangers who'd have no real reason to clash.
        from Data.Database.repository import get_kingdom_relation

        trade_routes = [r for r in get_world_roads(self.conn, self.world_id) if r["kind"] == "Trade Route"]

        for kingdom in kingdoms:

            if random.random() >= WAR_CHANCE_PER_KINGDOM_PER_YEAR:
                continue

            capital_id = kingdom.get("capital")
            if capital_id is None:
                continue

            neighbor_routes = [r for r in trade_routes if capital_id in (r["city_a_id"], r["city_b_id"])]
            if not neighbor_routes:
                continue

            candidates = []

            for route in neighbor_routes:

                other_capital_id = route["city_b_id"] if route["city_a_id"] == capital_id else route["city_a_id"]
                other_capital = get_city(self.conn, other_capital_id)
                other_kingdom_id = other_capital["kingdom"]

                relation = get_kingdom_relation(self.conn, kingdom["id"], other_kingdom_id)

                if relation["status"] == "Allied":
                    continue

                if relation["status"] == "Peace Treaty" and relation["peace_until_year"]:
                    if self.clock.year < relation["peace_until_year"]:
                        continue

                candidates.append((route, other_kingdom_id))

            if not candidates:
                continue

            weights = [1.0 / max(1.0, route["distance"]) for route, _ in candidates]
            chosen_route, defender_kingdom_id = random.choices(candidates, weights=weights, k=1)[0]

            defender_kingdom = get_kingdom(self.conn, defender_kingdom_id)

            attacker_cities = get_kingdom_cities(self.conn, kingdom["id"])
            defender_cities = get_kingdom_cities(self.conn, defender_kingdom["id"])

            resolve_kingdom_war(self.conn, kingdom, defender_kingdom, attacker_cities, defender_cities)

        self.conn.commit()


def run_realtime(conn, world_id, sim_minutes_per_real_second: float = 1.0, on_tick=None, max_ticks: int | None = None):
    """
    Runs the simulation continuously in real time. By default, 1 real
    second advances the simulated clock by 1 minute -- pass a larger
    sim_minutes_per_real_second to run faster (e.g. 60.0 means 1 real
    second = 1 simulated hour).

    on_tick(clock, rollover), if given, is called after every tick --
    useful for logging/printing progress without modifying this loop.

    max_ticks stops the loop after that many ticks (mainly for testing;
    omit it to run indefinitely until interrupted).
    """

    manager = SimulationManager(conn, world_id)
    seconds_per_tick = 1.0 / sim_minutes_per_real_second

    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        start = time.time()

        rollover = manager.tick()

        if on_tick:
            on_tick(manager.clock, rollover)

        ticks += 1
        elapsed = time.time() - start
        remaining = seconds_per_tick - elapsed

        if remaining > 0:
            time.sleep(remaining)

    return manager
