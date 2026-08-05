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
    - DAY: Employment (wages + skill xp), Economy (production,
      consumption, kingdom trade), and a small daily chance of a raid
      for every city with a living Bandit Camp.
    - MONTH: Family formation (marriages/births) and Government
      succession (a new ruler is chosen whenever a city's ruler slot
      is empty or its holder has died) for every city.

    Deliberately NOT yet wired: Relationships, Quests, aging. Each of
    those needs its own "how often should this actually fire" decision
    -- see the on_season/on_year hooks below, reserved for that.
    """

    def __init__(self, conn, world_id):
        self.conn = conn
        self.world_id = world_id
        self.clock = load_clock(conn, world_id)

    def tick(self) -> dict:
        rollover = self.clock.advance_minute()
        save_clock(self.conn, self.world_id, self.clock)
        self.conn.commit()

        if rollover["hour"]:
            self.on_hour()
        if rollover["day"]:
            self.on_day()
        if rollover["month"]:
            self.on_month()
        if rollover["season"]:
            self.on_season()
        if rollover["year"]:
            self.on_year()

        return rollover

    def on_hour(self) -> None:
        # Reserved for future wiring (e.g. needs decay, NPC location
        # resolution via Data.DailyLife.daily_life).
        pass

    def on_day(self) -> None:
        from Data.NPCS.employment import apply_workday
        from Data.Economy.economy import simulate_city_economy_day, simulate_kingdom_trade_day
        from Data.Combat.combat import resolve_raid
        import random

        # Chance, per city per day, that a city's own Bandit Camp (if
        # it has one, with living occupants) actually raids that day --
        # not a certainty every tick, so raids feel occasional rather
        # than relentless. ~78% chance of at least one raid per camp
        # over a 30-day month at this rate.
        RAID_CHANCE_PER_DAY = 0.05

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            cities = get_kingdom_cities(self.conn, kingdom["id"])

            for city in cities:

                simulate_city_economy_day(city)

                for npc in get_city_npcs(self.conn, city["id"]):
                    if npc["alive"] and npc["profession"] != "Unemployed":
                        apply_workday(npc)
                        save_npc(self.conn, npc)

                for building in city.get("buildings", []):
                    if building["type"] == "Bandit Camp" and building["workers"]:
                        if random.random() < RAID_CHANCE_PER_DAY:
                            resolve_raid(self.conn, city, building)

            simulate_kingdom_trade_day(cities)

            for city in cities:
                save_city(self.conn, city)

        self.conn.commit()

    def on_month(self) -> None:
        from Data.NPCS.family import form_families
        from Data.Government.government import assign_city_ruler, assign_kingdom_ruler
        from Data.Database.repository import get_npc, save_kingdom

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            for city in get_kingdom_cities(self.conn, kingdom["id"]):

                form_families(self.conn, city)

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
        # Reserved (e.g. aging NPCs).
        pass


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
