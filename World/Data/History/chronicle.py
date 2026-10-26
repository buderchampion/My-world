import json
import zlib
from pathlib import Path

TEMPLATES_FILE = Path(__file__).resolve().parent / "ChronicleTemplates.json"

# Every template key the code writes with. check_project.py confirms that
# ChronicleTemplates.json has at least one template for each of these, so
# deleting or renaming one by accident is caught before it can crash a run.
REQUIRED_TEMPLATE_KEYS = (
    "Alliance Formed",
    "Alliance Ended (cooled)",
    "Alliance Ended (spurned)",
)

# The only {placeholders} a template may use. {When} is the date opening a
# sentence ("In the spring of the 14th year"), {when} the same in lowercase
# for mid-sentence use; {kingdom} is the kingdom whose chronicle this is and
# {other} the realm it is writing about.
TEMPLATE_FIELDS = ("When", "when", "kingdom", "other")


def ordinal(n):
    """1 -> 1st, 2 -> 2nd, 3 -> 3rd, 4 -> 4th, 11 -> 11th, 21 -> 21st ..."""

    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

    return f"{n}{suffix}"


def format_when(year, season):
    """Returns ("In the spring of the 14th year", "in the spring of the 14th year").
    Era is deliberately absent -- it joins the date once the Era system exists."""

    if season:
        when = f"in the {season.lower()} of the {ordinal(year)} year"
    else:
        when = f"in the {ordinal(year)} year"

    return when[0].upper() + when[1:], when


def load_templates():
    # Read fresh each time (a handful of tiny entries a year), so edits to
    # the JSON take effect without restarting anything.
    with open(TEMPLATES_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def pick_template(key, *seed_parts):
    """
    Chooses one of the templates for this kind of entry. The choice depends
    only on the key and seed_parts (never on the clock or a random stream),
    so the same event always reads the same way when a world is replayed,
    while different kingdoms and different years still get different wording.
    """

    options = load_templates()[key]

    seed = "|".join(str(part) for part in (key, *seed_parts))

    return options[zlib.crc32(seed.encode("utf-8")) % len(options)]


def current_season(conn, world_id):
    """The world's current season, from the saved clock. None if unknown."""

    from Data.Simulation.clock import SEASON_BY_MONTH

    row = conn.execute("SELECT clock_month FROM worlds WHERE id = ?", (world_id,)).fetchone()

    return SEASON_BY_MONTH.get(row["clock_month"]) if row else None


def write_entry(conn, kingdom_id, event_type, template_key, year, season, fields, source_event_id=None, seed_parts=()):
    """
    Writes one chronicle entry for one kingdom and returns it. fields fills
    the template's {kingdom}/{other} placeholders. Does not commit -- the
    caller's own transaction does, like every other writer in this project.
    """

    when_capitalized, when = format_when(year, season)

    template = pick_template(template_key, kingdom_id, year, *seed_parts)

    text = template.format(When=when_capitalized, when=when, **fields)

    cursor = conn.execute(
        """
        INSERT INTO chronicle_entries (kingdom_id, event_type, text, year, season, source_event_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (kingdom_id, event_type, text, year, season, source_event_id)
    )

    return {
        "id": cursor.lastrowid, "kingdom_id": kingdom_id, "event_type": event_type,
        "text": text, "year": year, "season": season, "source_event_id": source_event_id,
    }


def record_alliance_formed(conn, world_id, kingdom_a, kingdom_b, year, source_event_id=None):
    """Both kingdoms record the alliance, each in its own words."""

    season = current_season(conn, world_id)

    return [
        write_entry(
            conn, me["id"], "Alliance Formed", "Alliance Formed", year, season,
            {"kingdom": me["name"], "other": other["name"]},
            source_event_id, seed_parts=(other["id"],)
        )
        for me, other in ((kingdom_a, kingdom_b), (kingdom_b, kingdom_a))
    ]


def record_alliance_ended(conn, world_id, kingdom_a, kingdom_b, cooler, year, source_event_id=None):
    """
    Both kingdoms record the end of the alliance, but from their own side of
    it: the one whose regard fell tells of withdrawing its hand, the other
    of being forsaken. cooler is the kingdom whose favor was lower.
    """

    season = current_season(conn, world_id)

    entries = []

    for me, other in ((kingdom_a, kingdom_b), (kingdom_b, kingdom_a)):

        key = "Alliance Ended (cooled)" if me["id"] == cooler["id"] else "Alliance Ended (spurned)"

        entries.append(write_entry(
            conn, me["id"], "Alliance Ended", key, year, season,
            {"kingdom": me["name"], "other": other["name"]},
            source_event_id, seed_parts=(other["id"],)
        ))

    return entries


def get_chronicle(conn, kingdom_id):
    """A kingdom's chronicle, oldest entry first."""

    rows = conn.execute(
        "SELECT * FROM chronicle_entries WHERE kingdom_id = ? ORDER BY id", (kingdom_id,)
    ).fetchall()

    return [dict(row) for row in rows]