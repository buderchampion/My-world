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

    HOUR_TO_TIME_BLOCK = {6: "Morning", 12: "Afternoon", 18: "Evening", 22: "Night"}

    def on_hour(self) -> None:

        time_block = self.HOUR_TO_TIME_BLOCK.get(self.clock.hour)

        if time_block is None:
            return

        from Data.DailyLife.daily_life import resolve_time_block, get_needs_effect
        from Data.Database.repository import get_city
        from Data.Infrastructure.travel import get_traveling_npc_ids

        traveling_npc_ids = get_traveling_npc_ids(self.conn, self.world_id)

        for kingdom in get_world_kingdoms(self.conn, self.world_id):
            for city_summary in get_kingdom_cities(self.conn, kingdom["id"]):

                city = get_city(self.conn, city_summary["id"])
                buildings = city.get("buildings", [])
                building_types_by_id = {b["id"]: b["type"] for b in buildings}

                for npc in get_city_npcs(self.conn, city["id"]):

                    if not npc["alive"]:
                        continue

                    if npc["id"] in traveling_npc_ids:
                        effect = get_needs_effect("Traveling")
                        for need, delta in effect.items():
                            npc["needs"][need] = max(0, min(100, npc["needs"][need] + delta))
                        save_npc(self.conn, npc)
                        continue

                    location_type, location_id = resolve_time_block(npc, time_block, buildings)
                    building_type = building_types_by_id.get(location_id, "House")
                    effect = get_needs_effect(building_type)

                    npc["location"] = location_id

                    for need, delta in effect.items():
                        npc["needs"][need] = max(0, min(100, npc["needs"][need] + delta))

                    save_npc(self.conn, npc)

        self.conn.commit()

    def on_day(self) -> None:
        from Data.NPCS.employment import apply_workday
        from Data.NPCS.healing import apply_healing, city_has_active_temple
        from Data.NPCS.starvation import apply_starvation_check
        from Data.Events.events import death_event
        from Data.Relationships.relationships import apply_grief
        from Data.Economy.economy import (
            simulate_city_economy_day, simulate_kingdom_trade_day, simulate_cross_kingdom_trade_day,
            total_resource_value
        )
        from Data.Combat.combat import resolve_raid
        from Data.Government.military import apply_military_upkeep, MILITARY_PROFESSIONS
        from Data.Database.repository import add_treasury, add_city_wealth
        import random
        
        # Chance, per city per day, that a city's own Bandit Camp (if
        # it has one, with living occupants) actually raids that day --
        # not a certainty every tick, so raids feel occasional rather
        # than relentless. ~78% chance of at least one raid per camp
        # over a 30-day month at this rate.
        RAID_CHANCE_PER_DAY = 0.05
        CITY_WEALTH_MARKET_FEE_RATE = 0.02

        # A city with genuinely happy residents draws extra visitors --
        # trade, tourism, word of mouth -- generating real treasury
        # income on top of ordinary taxes. Below the threshold, a city
        # isn't pleasant enough to draw anyone extra, so this
        # contributes nothing; above it, revenue scales with both how
        # happy the population is and how many people there are to be
        # happy in the first place.
        VISITOR_HAPPINESS_THRESHOLD = 60
        VISITOR_REVENUE_PER_HAPPINESS_POINT = 0.01

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            cities = get_kingdom_cities(self.conn, kingdom["id"])
            tax_collected = 0.0
            military_npcs = []

            for city in cities:

                simulate_city_economy_day(city)
                
                market_revenue = round(total_resource_value(city) * CITY_WEALTH_MARKET_FEE_RATE, 2)
                if market_revenue > 0:
                    add_city_wealth(self.conn, city["id"], market_revenue)

                has_temple = city_has_active_temple(city)
                happiness_sum = 0
                living_count = 0

                for npc in get_city_npcs(self.conn, city["id"]):

                    if not npc["alive"]:
                        continue

                    if apply_starvation_check(npc):
                        death_event(self.conn, npc, "starvation", city_id=city["id"])
                        apply_grief(self.conn, npc)
                        save_npc(self.conn, npc)
                        continue

                    happiness_sum += npc["needs"]["happiness"]
                    living_count += 1

                    changed = False

                    if npc["profession"] != "Unemployed":
                        tax_collected += apply_workday(npc, tax_rate=kingdom.get("tax_rate", 0.0))
                        changed = True

                    if apply_healing(npc, has_temple):
                        changed = True

                    if npc["profession"] in MILITARY_PROFESSIONS:
                        military_npcs.append(npc)

                    if changed:
                        save_npc(self.conn, npc)

                if living_count > 0:

                    avg_happiness = happiness_sum / living_count

                    if avg_happiness > VISITOR_HAPPINESS_THRESHOLD:
                        visitor_revenue = round(
                            (avg_happiness - VISITOR_HAPPINESS_THRESHOLD) * living_count
                            * VISITOR_REVENUE_PER_HAPPINESS_POINT, 2
                        )
                        tax_collected += visitor_revenue

                for building in city.get("buildings", []):
                    if building["type"] == "Bandit Camp" and building["workers"]:
                        if random.random() < RAID_CHANCE_PER_DAY:
                            resolve_raid(self.conn, city, building)

            if tax_collected > 0:
                add_treasury(self.conn, kingdom["id"], round(tax_collected, 2))

            capital_city = next((c for c in cities if c["id"] == kingdom.get("capital")), None)
            if capital_city is not None:
                apply_military_upkeep(self.conn, kingdom, military_npcs, capital_city)

            simulate_kingdom_trade_day(cities)

            for city in cities:
                save_city(self.conn, city)

        simulate_cross_kingdom_trade_day(self.conn, self.world_id)

        from Data.Infrastructure.caravans import advance_caravans
        advance_caravans(self.conn, self.world_id)

        from Data.Infrastructure.travel import advance_npc_travels, collect_tourist_revenue
        collect_tourist_revenue(self.conn, self.world_id)
        travel_result = advance_npc_travels(self.conn, self.world_id)

        from Data.Infrastructure.migration import relocate_npc
        for trip in travel_result["arrived"]:
            if trip["purpose"] == "Migration":
                relocate_npc(self.conn, trip["npc_id"], trip["destination_city_id"])

        self.conn.commit()

    def on_month(self) -> None:
        from Data.NPCS.family import form_families
        from Data.Government.government import assign_city_ruler, assign_kingdom_ruler
        from Data.Government.diplomacy import apply_succession_shock
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

                    is_capital = city["id"] == kingdom.get("capital")
                    previous_monarch_id = kingdom.get("ruler") if is_capital else None

                    assign_city_ruler(self.conn, city)

                    if is_capital:
                        assign_kingdom_ruler(kingdom, city)
                        save_kingdom(self.conn, kingdom)

                        # A real succession shakes the kingdom's relationships.
                        # The very first crowning (nobody had ruled yet, so
                        # there is no previous monarch) does not -- rulers are
                        # not placed when a world is created. A previous
                        # monarch who is still alive was deposed, not
                        # replaced after dying.
                        if previous_monarch_id is not None:
                            previous = get_npc(self.conn, previous_monarch_id)
                            apply_succession_shock(
                                self.conn, kingdom["id"],
                                overthrown=bool(previous and previous["alive"])
                            )

        from Data.Infrastructure.travel import consider_tourism
        consider_tourism(self.conn, self.world_id)

        from Data.Infrastructure.migration import consider_migration
        consider_migration(self.conn, self.world_id)

        self.conn.commit()
    def on_season(self) -> None:
        # Reserved.
        pass

    def on_year(self) -> None:
        from Data.NPCS.aging import apply_aging
        from Data.Events.events import death_event
        from Data.Relationships.relationships import apply_grief
        from Data.Combat.combat import (
            resolve_kingdom_war, check_civil_war_eligibility, resolve_civil_war,
            CIVIL_WAR_CHANCE_IF_ELIGIBLE
        )
        from Data.Database.repository import get_kingdom, get_world_roads
        import random

        kingdoms = get_world_kingdoms(self.conn, self.world_id)

        # Aging + natural old-age death for every living NPC.
        for kingdom in kingdoms:
            for city in get_kingdom_cities(self.conn, kingdom["id"]):
                for npc in get_city_npcs(self.conn, city["id"]):
                    if apply_aging(npc):
                        death_event(self.conn, npc, "old age", city_id=city["id"])
                        apply_grief(self.conn, npc)
                    save_npc(self.conn, npc)

        self.conn.commit()

        # Diplomacy, all before wars so a pair that has just become allies
        # cannot be sent to war the very same year: peaceful years warm
        # kingdoms toward each other, alliances whose favor has soured end,
        # and then any pair whose goodwill has grown high enough allies
        # (ending first, so a freed-up ally slot can be used right away).
        from Data.Government.diplomacy import (
            apply_peace_dividend, end_failed_alliances, check_alliances,
            war_candidates, choose_war_target,
        )
        apply_peace_dividend(self.conn, self.world_id, self.clock.year)
        end_failed_alliances(self.conn, self.world_id, self.clock.year)
        check_alliances(self.conn, self.world_id, self.clock.year)

        # Kingdom wars. Whether a kingdom goes to war this year depends on its
        # grievances (a kingdom with no enemies rarely does), and who it
        # attacks favors near neighbors it also resents -- see
        # Data.Government.diplomacy.choose_war_target. Allies and kingdoms it
        # is still under a Peace Treaty with are never candidates.
        trade_routes = [r for r in get_world_roads(self.conn, self.world_id) if r["kind"] == "Trade Route"]

        for kingdom in kingdoms:

            candidates = war_candidates(self.conn, kingdom, trade_routes, self.clock.year)

            choice = choose_war_target(self.conn, kingdom["id"], candidates)

            if choice is None:
                continue

            chosen_route, defender_kingdom_id = choice

            defender_kingdom = get_kingdom(self.conn, defender_kingdom_id)

            attacker_cities = get_kingdom_cities(self.conn, kingdom["id"])
            defender_cities = get_kingdom_cities(self.conn, defender_kingdom["id"])

            resolve_kingdom_war(self.conn, kingdom, defender_kingdom, attacker_cities, defender_cities)

        self.conn.commit()

        # Civil wars: checked after kingdom wars, so a kingdom that
        # just fought (and spent treasury, took casualties) has that
        # already reflected before eligibility gets checked, and one
        # that just had a civil war respects its own fresh cooldown.
        for kingdom_summary in kingdoms:

            kingdom = get_kingdom(self.conn, kingdom_summary["id"])
            cities = get_kingdom_cities(self.conn, kingdom["id"])

            eligibility = check_civil_war_eligibility(self.conn, kingdom, cities, self.clock.year)

            if not eligibility["eligible"]:
                continue

            if random.random() >= CIVIL_WAR_CHANCE_IF_ELIGIBLE:
                continue

            resolve_civil_war(self.conn, kingdom, cities, self.clock.year)

            for city in cities:
                save_city(self.conn, city)

        self.conn.commit()
        from Data.NPCS.aging import apply_aging
        from Data.Events.events import death_event
        from Data.Relationships.relationships import apply_grief
        from Data.Combat.combat import (
            resolve_kingdom_war, check_civil_war_eligibility, resolve_civil_war,
            CIVIL_WAR_CHANCE_IF_ELIGIBLE
        )
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
                        apply_grief(self.conn, npc)
                    save_npc(self.conn, npc)

        self.conn.commit()
        # Peaceful years warm kingdoms toward each other, then any pair whose
        # goodwill has grown high enough allies. Both run before wars, so a
        # pair that has just become allies cannot be sent to war the very
        # same year.
        from Data.Government.diplomacy import apply_peace_dividend, check_alliances
        apply_peace_dividend(self.conn, self.world_id, self.clock.year)
        check_alliances(self.conn, self.world_id, self.clock.year)

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

        # Civil wars: checked after kingdom wars, so a kingdom that
        # just fought (and spent treasury, took casualties) has that
        # already reflected before eligibility gets checked, and one
        # that just had a civil war respects its own fresh cooldown.
        for kingdom_summary in kingdoms:

            kingdom = get_kingdom(self.conn, kingdom_summary["id"])
            cities = get_kingdom_cities(self.conn, kingdom["id"])

            eligibility = check_civil_war_eligibility(self.conn, kingdom, cities, self.clock.year)

            if not eligibility["eligible"]:
                continue

            if random.random() >= CIVIL_WAR_CHANCE_IF_ELIGIBLE:
                continue

            resolve_civil_war(self.conn, kingdom, cities, self.clock.year)

            for city in cities:
                save_city(self.conn, city)

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
