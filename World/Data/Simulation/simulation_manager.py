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
                        if need == "happiness":
                            continue          # happiness follows a person's life, day by day (Data.NPCS.contentment)
                        # A meal restores hunger only as far as the household
                        # could afford food today (Data.Economy.food_market).
                        if need == "hunger" and delta > 0:
                            delta = delta * npc.get("fed", 1.0)
                        npc["needs"][need] = max(0, min(100, npc["needs"][need] + delta))

                    save_npc(self.conn, npc)

        self.conn.commit()

    def on_day(self) -> None:
        from Data.NPCS.employment import apply_workday
        from Data.NPCS.healing import apply_healing, city_has_active_temple, drink_potion
        from Data.NPCS.starvation import apply_starvation_check
        from Data.Events.events import death_event
        from Data.Relationships.relationships import apply_grief
        from Data.Economy.economy import (
            simulate_city_economy_day, simulate_kingdom_trade_day, simulate_cross_kingdom_trade_day,
            total_resource_value
        )
        from Data.Combat.combat import resolve_raid
        from Data.Government.military import apply_military_upkeep, MILITARY_PROFESSIONS
        from Data.Database.repository import add_treasury, add_city_wealth, get_npc
        import random

        # NPCs whose record grief changed today (a spouse starved); see the
        # per-NPC loop below for why they must be reloaded.
        grieving = set()

        # Food is bought, not handed out (Data.Economy.food_market). People away
        # from home -- travellers, soldiers in the field -- neither work here
        # nor buy here.
        from Data.Economy.food_market import buy_food, LABOUR_WAGE
        from Data.Infrastructure.travel import get_traveling_npc_ids
        away_ids = get_traveling_npc_ids(self.conn, self.world_id)

        # Soldiers away with an army are fed and armed from its wagons, not
        # from their capital's upkeep.
        from Data.Government.armies import get_army_npc_ids
        deployed_ids = get_army_npc_ids(self.conn, self.world_id)
        
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

        # People in a kingdom that has run out of Cloth entirely lose this
        # much happiness a day, until supplies arrive.
        NO_CLOTH_HAPPINESS_PER_DAY = 1

        for kingdom in get_world_kingdoms(self.conn, self.world_id):

            cities = get_kingdom_cities(self.conn, kingdom["id"])
            tax_collected = 0.0
            military_npcs = []
            lords_takings = {}         # province id -> the lord's cut of today's taxes

            for city in cities:

                city_taxes_before = tax_collected

                simulate_city_economy_day(city)

                # Village households eat from their own store, then the market.
                from Data.World.harvest import feed_households
                feed_households(city)
                
                market_revenue = round(total_resource_value(city) * CITY_WEALTH_MARKET_FEE_RATE, 2)
                if market_revenue > 0:
                    add_city_wealth(self.conn, city["id"], market_revenue)

                has_temple = city_has_active_temple(city)
                happiness_sum = 0
                living_count = 0

                for npc in get_city_npcs(self.conn, city["id"]):

                    # This list was loaded before anyone died today. If grief
                    # has since changed this person (a spouse just died), reload
                    # them, or saving their old copy below would erase the grief
                    # and leave them married to the dead.
                    if npc["id"] in grieving:
                        npc = get_npc(self.conn, npc["id"])

                    if not npc["alive"]:
                        continue

                    if apply_starvation_check(npc):
                        death_event(self.conn, npc, "starvation", city_id=city["id"])
                        spouse_id = npc.get("spouse")
                        if apply_grief(self.conn, npc) and spouse_id:
                            grieving.add(spouse_id)
                        save_npc(self.conn, npc)
                        continue

                    happiness_sum += npc["needs"]["happiness"]
                    living_count += 1

                    changed = False

                    if npc["profession"] != "Laborer":
                        tax_collected += apply_workday(npc, tax_rate=kingdom.get("tax_rate", 0.0))
                        changed = True
                    elif npc["id"] not in away_ids:
                        # no trade: a day's casual labour (Data.Economy.food_market)
                        npc["gold"] = round(npc["gold"] + LABOUR_WAGE, 2)
                        changed = True

                    if drink_potion(npc, city):
                        changed = True

                    if apply_healing(npc, has_temple):
                        changed = True

                    if npc["profession"] in MILITARY_PROFESSIONS:
                        military_npcs.append(npc)

                    if changed:
                        save_npc(self.conn, npc)

                # Every household buys its day's food from the city's stock.
                buy_food(self.conn, city, has_temple, away_ids)

                if living_count > 0:

                    avg_happiness = happiness_sum / living_count

                    if avg_happiness > VISITOR_HAPPINESS_THRESHOLD:
                        visitor_revenue = round(
                            (avg_happiness - VISITOR_HAPPINESS_THRESHOLD) * living_count
                            * VISITOR_REVENUE_PER_HAPPINESS_POINT, 2
                        )
                        tax_collected += visitor_revenue

                # A county's lord keeps their share of what its towns and villages
                # paid today; the rest goes up to the crown (Data.Government.feudal).
                if city.get("province_id"):
                    from Data.Government.feudal import lord_cut
                    cut = lord_cut(tax_collected - city_taxes_before)
                    tax_collected -= cut
                    lords_takings[city["province_id"]] = lords_takings.get(city["province_id"], 0.0) + cut

                for building in city.get("buildings", []):
                    if building["type"] == "Bandit Camp" and building["workers"]:
                        if random.random() < RAID_CHANCE_PER_DAY:
                            resolve_raid(self.conn, city, building)

            if tax_collected > 0:
                add_treasury(self.conn, kingdom["id"], round(tax_collected, 2))
            if lords_takings:
                from Data.Government.feudal import pay_lords
                pay_lords(self.conn, lords_takings)

            capital_city = next((c for c in cities if c["id"] == kingdom.get("capital")), None)
            if capital_city is not None:
                apply_military_upkeep(self.conn, kingdom, military_npcs, capital_city, deployed_ids=deployed_ids)

            # a besieged town is blockaded: no trade in or out (Data.Government.campaigns)
            from Data.Government.campaigns import besieged_city_ids
            blockaded = besieged_city_ids(self.conn, self.world_id)
            simulate_kingdom_trade_day([c for c in cities if c["id"] not in blockaded])

            # A kingdom with no Cloth left anywhere, even after its cities have
            # traded, is genuinely without: everyone in it is a little less
            # happy. A city that simply makes none, or is still waiting for its
            # share, is not -- the realm has cloth, and trade will bring it.
            # Done in the database, after each city's people were saved above,
            # so no older copy can overwrite it.
            if sum(c["resources"].get("Cloth", 0) for c in cities) <= 0:
                for city in cities:
                    self.conn.execute(
                        "UPDATE npcs SET needs_happiness = MAX(0, needs_happiness - ?) WHERE city_id = ? AND alive = 1",
                        (NO_CLOTH_HAPPINESS_PER_DAY, city["id"])
                    )

            for city in cities:
                save_city(self.conn, city)

        # Everyone's happiness drifts toward what their life is like, now the day's meals are bought
        # (Data.NPCS.contentment).
        from Data.NPCS.contentment import drift_happiness
        from Data.Government.edicts import today as contentment_day
        drift_happiness(self.conn, self.world_id, contentment_day(self.conn, self.world_id))

        simulate_cross_kingdom_trade_day(self.conn, self.world_id)

        from Data.Infrastructure.caravans import advance_caravans
        advance_caravans(self.conn, self.world_id)

        from Data.Infrastructure.travel import advance_npc_travels, collect_tourist_revenue
        collect_tourist_revenue(self.conn, self.world_id)
        travel_result = advance_npc_travels(self.conn, self.world_id)

        # An avenger who has reached their nemesis's town challenges them (Data.Combat.vendetta).
        from Data.Combat.vendetta import arrivals
        arrivals(self.conn, self.world_id)

        from Data.Infrastructure.migration import relocate_npc
        for trip in travel_result["arrived"]:
            if trip["purpose"] == "Migration":
                relocate_npc(self.conn, trip["npc_id"], trip["destination_city_id"])

        # Armies eat from their wagons and march on. This changes soldiers'
        # hunger in the database, so it runs after the per-NPC loop above has
        # saved everyone -- otherwise those older copies would overwrite it.
        from Data.Government.armies import advance_armies
        advance_armies(self.conn, self.world_id)

        # Supply lines: armies running low send home for more, and supply
        # caravans on the road travel, deliver, or get raided. After the
        # armies have eaten and moved today, for the same reason as above.
        from Data.Government.supply import advance_supply_lines
        advance_supply_lines(self.conn, self.world_id)

        # Campaigns: an army at its target fights; one home again disbands.
        from Data.Government.campaigns import advance_campaigns
        from Data.Government.edicts import today as campaign_day
        advance_campaigns(self.conn, self.world_id, campaign_day(self.conn, self.world_id))

        self.conn.commit()

    def on_month(self) -> None:
        from Data.NPCS.family import form_families
        from Data.Government.government import assign_city_ruler, assign_kingdom_ruler
        from Data.Government.diplomacy import apply_succession_shock
        from Data.History.chronicle import record_coronation
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

                        # Rulers are not placed when a world is created, so the
                        # very first crowning has no previous monarch: that is
                        # the realm's founding, not a succession. A previous
                        # monarch who is still alive was deposed, not
                        # replaced after dying.
                        previous = get_npc(self.conn, previous_monarch_id) if previous_monarch_id is not None else None
                        overthrown = bool(previous and previous["alive"])

                        # A real succession shakes the kingdom's relationships.
                        if previous_monarch_id is not None:
                            apply_succession_shock(self.conn, kingdom["id"], overthrown=overthrown)

                        # The chronicle records every crowning, the first included.
                        if kingdom.get("ruler") is not None:
                            if previous_monarch_id is None:
                                kind = "founding"
                            else:
                                kind = "overthrow" if overthrown else "succession"
                            record_coronation(
                                self.conn, self.world_id, kingdom,
                                get_npc(self.conn, kingdom["ruler"]), kind, predecessor=previous
                            )

        # Child rulers who come of age rule in their own right; dead regents
        # are replaced (Data.Government.succession).
        from Data.Government.succession import check_regencies
        check_regencies(self.conn, self.world_id)

        # Each lord's loyalty to the crown drifts with how they are treated
        # (Data.Government.feudal).
        from Data.Government.feudal import update_loyalty
        update_loyalty(self.conn, self.world_id)

        # Restless lords may rise against the crown -- and win their county
        # its freedom (Data.Government.rebellion).
        from Data.Government.rebellion import consider_rebellions
        consider_rebellions(self.conn, self.world_id)

        # Anyone who entered the story without a family name gets one
        # (Data.NPCS.family_names); births and weddings name people as they happen.
        from Data.NPCS.family_names import ensure_family_names
        ensure_family_names(self.conn, self.world_id)

        # Slum families who can afford it move into a house (Data.World.slums).
        from Data.World.slums import rehouse
        rehouse(self.conn, self.world_id)

        # Old scores: avengers train, hire blades, duel, or set out to find
        # their nemesis (Data.Combat.vendetta).
        from Data.Combat.vendetta import pursue_vendettas
        pursue_vendettas(self.conn, self.world_id)

        # Every town taxes its citizens' savings into its own purse, and debtors
        # repay the crown (Data.Buildings.upgrades).
        from Data.Buildings.upgrades import collect_town_taxes
        collect_town_taxes(self.conn, self.world_id)

        # The job market: the jobless take free places in their town (Data.Economy.jobs).
        from Data.Economy.jobs import hire_jobless
        hire_jobless(self.conn, self.world_id)

        # Republics' grain offices top up their reserves from abroad
        # (Data.Government.republic).
        from Data.Government.republic import run_grain_offices
        run_grain_offices(self.conn, self.world_id)

        # Occupied towns resent their garrisons, may rise, and after a year are
        # counted part of the realm (Data.Government.occupation).
        from Data.Government.occupation import occupations_monthly
        from Data.Government.edicts import today as occupation_day
        occupations_monthly(self.conn, self.world_id, occupation_day(self.conn, self.world_id))

        # Each kingdom's Loyalty, Unrest and Stability, from all of the above
        # (Data.Government.health).
        from Data.Government.health import update_health
        update_health(self.conn, self.world_id)

        from Data.Infrastructure.travel import consider_tourism
        consider_tourism(self.conn, self.world_id)

        from Data.Infrastructure.migration import consider_migration
        consider_migration(self.conn, self.world_id)

        # Hungry villages lose households to the towns and young men to
        # banditry (Data.World.villagers).
        from Data.World.villagers import village_hardship
        village_hardship(self.conn, self.world_id)

        # Monster raids: raider lairs strike villages near them (Data.Monsters.raids).
        from Data.Monsters.raids import monster_raids
        monster_raids(self.conn, self.world_id)
        
        self.conn.commit()

    def on_season(self) -> None:
        # Autumn: the year's harvest comes in (Data.World.harvest).
        if self.clock.season == "Autumn":
            from Data.World.harvest import run_harvest
            run_harvest(self.conn, self.world_id, self.clock.year)

        # Every ruler reviews the realm and may issue an edict (Data.Government.edicts).
        from Data.Government.edicts import review
        review(self.conn, self.world_id)

    def on_year(self) -> None:
        from Data.NPCS.aging import apply_aging
        from Data.Events.events import death_event
        from Data.Relationships.relationships import apply_grief
        from Data.Combat.combat import (
            resolve_kingdom_war, check_civil_war_eligibility, resolve_civil_war,
            CIVIL_WAR_CHANCE_IF_ELIGIBLE
        )
        from Data.Database.repository import get_kingdom, get_world_roads, get_npc
        import random

        kingdoms = get_world_kingdoms(self.conn, self.world_id)

        # Aging + natural old-age death for every living NPC. The list for
        # each city is loaded up front, so anyone whose record grief has
        # changed since (their spouse just died) is reloaded before being
        # aged and saved -- otherwise saving their old copy would erase the
        # grief and leave them married to the dead.
        grieving = set()

        for kingdom in kingdoms:
            for city in get_kingdom_cities(self.conn, kingdom["id"]):
                for npc in get_city_npcs(self.conn, city["id"]):
                    if npc["id"] in grieving:
                        npc = get_npc(self.conn, npc["id"])
                    if apply_aging(npc):
                        death_event(self.conn, npc, "old age", city_id=city["id"])
                        spouse_id = npc.get("spouse")
                        if apply_grief(self.conn, npc) and spouse_id:
                            grieving.add(spouse_id)
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

            # The war is a campaign: an army marches on a real town and fights
            # there when it arrives (Data.Government.campaigns).
            from Data.Government.campaigns import start_campaign
            from Data.Government.edicts import today as campaign_day
            start_campaign(self.conn, kingdom, defender_kingdom, campaign_day(self.conn, self.world_id))

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

            # martial law makes a rising far less likely (Data.Government.edicts)
            from Data.Government.edicts import martial_law_active, today as edict_day, MARTIAL_LAW_FACTOR
            civil_war_chance = CIVIL_WAR_CHANCE_IF_ELIGIBLE * (
                MARTIAL_LAW_FACTOR if martial_law_active(kingdom, edict_day(self.conn, self.world_id)) else 1)
            if random.random() >= civil_war_chance:
                continue

            resolve_civil_war(self.conn, kingdom, cities, self.clock.year)

            for city in cities:
                save_city(self.conn, city)

        self.conn.commit()

        # Villages grow, and crowded lands send settlers to found new villages --
        # now and then, far from the crown, a new realm (Data.World.expansion).
        from Data.World.expansion import grow_villages, consider_settlement
        grow_villages(self.conn, self.world_id)
        consider_settlement(self.conn, self.world_id)


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