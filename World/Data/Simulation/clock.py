from dataclasses import dataclass

# A simplified fantasy calendar -- clean round numbers, not tied to any
# real-world calendar's leap years/month-length irregularities.
MINUTES_PER_HOUR = 60
HOURS_PER_DAY = 24
DAYS_PER_MONTH = 30
MONTHS_PER_YEAR = 12

SEASON_BY_MONTH = {
    1: "Spring", 2: "Spring", 3: "Spring",
    4: "Summer", 5: "Summer", 6: "Summer",
    7: "Autumn", 8: "Autumn", 9: "Autumn",
    10: "Winter", 11: "Winter", 12: "Winter",
}


@dataclass
class Clock:
    minute: int = 0
    hour: int = 0
    day: int = 1
    month: int = 1
    year: int = 1

    @property
    def season(self) -> str:
        # Derived, not stored -- storing it separately would just be
        # the same fact kept in two places, the exact class of bug
        # we've been careful to avoid all through this project.
        return SEASON_BY_MONTH[self.month]

    def advance_minute(self) -> dict:
        """
        Advances the clock by exactly one minute, rolling over hour/day/
        month/season/year as needed. Returns which units rolled over
        this tick, so a caller (SimulationManager) knows what coarser-
        grained work to trigger.
        """

        rollover = {"hour": False, "day": False, "month": False, "season": False, "year": False}
        previous_season = self.season

        self.minute += 1

        if self.minute >= MINUTES_PER_HOUR:
            self.minute = 0
            self.hour += 1
            rollover["hour"] = True

            if self.hour >= HOURS_PER_DAY:
                self.hour = 0
                self.day += 1
                rollover["day"] = True

                if self.day > DAYS_PER_MONTH:
                    self.day = 1
                    self.month += 1
                    rollover["month"] = True

                    if self.month > MONTHS_PER_YEAR:
                        self.month = 1
                        self.year += 1
                        rollover["year"] = True

        if rollover["month"] and self.season != previous_season:
            rollover["season"] = True

        return rollover

    def total_minutes(self) -> int:
        """
        Absolute minute count since Year 1, Month 1, Day 1, 00:00 -- a
        single comparable number, useful for "how much time has
        passed" without redoing calendar math elsewhere.
        """

        days_elapsed = (
            (self.year - 1) * MONTHS_PER_YEAR * DAYS_PER_MONTH
            + (self.month - 1) * DAYS_PER_MONTH
            + (self.day - 1)
        )

        return days_elapsed * HOURS_PER_DAY * MINUTES_PER_HOUR + self.hour * MINUTES_PER_HOUR + self.minute

    def __str__(self) -> str:
        return f"Year {self.year}, {self.season}, Month {self.month} Day {self.day}, {self.hour:02d}:{self.minute:02d}"
