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
    "Coronation (founding)",
    "Coronation (succession)",
    "Coronation (after overthrow)",
    "Ruler Died (old age)",
    "Ruler Died (war)",
    "Ruler Died (civil war)",
    "Ruler Died (raid)",
    "Ruler Died (starvation)",
    "Ruler Died (other)",
    "War (attacker won)",
    "War (attacker won, city taken)",
    "War (attacker lost)",
    "War (defender held)",
    "War (defender lost)",
    "War (defender lost, city taken)",
)

# The only {placeholders} a template may use. {When} is the date opening a
# sentence ("In the spring of the 14th year"), {when} the same in lowercase
# for mid-sentence use; {kingdom} is the kingdom whose chronicle this is and
# {other} the realm it is writing about. For rulers: {ruler} and {title}
# ("King"/"Queen"), {predecessor} (whom a new monarch replaced), {age} and
# {age_ordinal} ("81st"), and {possessive} ("his"/"her"). For wars: {city},
# the city taken, if any.
TEMPLATE_FIELDS = (
    "When", "when", "kingdom", "other",
    "ruler", "title", "predecessor", "age", "age_ordinal", "possessive",
    "city",
)

# Which wording a monarch's death gets, by the cause death_event was given.
# Anything not listed here falls back to "Ruler Died (other)".
DEATH_CAUSE_TEMPLATES = {
    "old age": "Ruler Died (old age)",
    "war": "Ruler Died (war)",
    "civil war": "Ruler Died (civil war)",
    "raid": "Ruler Died (raid)",
    "starvation": "Ruler Died (starvation)",
}

CORONATION_TEMPLATES = {
    "founding": "Coronation (founding)",
    "succession": "Coronation (succession)",
    "overthrow": "Coronation (after overthrow)",
}


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


def current_date(conn, world_id):
    """The world's current (year, season), from the saved clock."""

    from Data.Simulation.clock import SEASON_BY_MONTH

    row = conn.execute("SELECT clock_year, clock_month FROM worlds WHERE id = ?", (world_id,)).fetchone()

    if row is None:
        return 1, None

    return row["clock_year"], SEASON_BY_MONTH.get(row["clock_month"])


def _possessive(npc):
    return {"Male": "his", "Female": "her"}.get(npc.get("gender"), "their")


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


def record_ruler_death(conn, npc, cause, source_event_id=None, foe_kingdom=None):
    """
    If the NPC who just died was a reigning monarch, their kingdom records
    the death, worded for how they died. Anyone else's death is not
    chronicle-worthy and nothing is written (returns None). Called for EVERY
    death by Data.Events.events.death_event, so no death path can miss it.

    foe_kingdom names the enemy for a death in war; without one, a war death
    falls back to the general wording rather than naming no one.
    """

    kingdom = conn.execute(
        "SELECT id, name, world_id FROM kingdoms WHERE ruler_id = ?", (npc["id"],)
    ).fetchone()

    if kingdom is None:
        return None

    key = DEATH_CAUSE_TEMPLATES.get(str(cause).lower(), "Ruler Died (other)")

    if key == "Ruler Died (war)" and not foe_kingdom:
        key = "Ruler Died (other)"

    year, season = current_date(conn, kingdom["world_id"])
    age = npc.get("age") or 0

    fields = {
        "kingdom": kingdom["name"],
        "other": foe_kingdom["name"] if foe_kingdom else "",
        "ruler": npc["name"],
        "title": npc.get("title") or "monarch",
        "age": age,
        "age_ordinal": ordinal(age),
        "possessive": _possessive(npc),
    }

    return write_entry(conn, kingdom["id"], "Ruler Died", key, year, season, fields, source_event_id, seed_parts=(npc["id"],))


def record_coronation(conn, world_id, kingdom, ruler, kind, predecessor=None):
    """
    A new monarch was crowned. kind is "founding" (the realm's first ruler),
    "succession" (after the old one died) or "overthrow" (after the old one
    was deposed). Points back to the Coronation event the crowning logged.
    """

    year, season = current_date(conn, world_id)

    source = conn.execute(
        "SELECT id FROM events WHERE type = 'Coronation' AND city_id = ? ORDER BY id DESC LIMIT 1",
        (kingdom.get("capital"),)
    ).fetchone()

    fields = {
        "kingdom": kingdom["name"],
        "ruler": ruler["name"],
        "title": ruler.get("title") or "monarch",
        "predecessor": predecessor["name"] if predecessor else "the old monarch",
        "possessive": _possessive(ruler),
    }

    return write_entry(
        conn, kingdom["id"], "Coronation", CORONATION_TEMPLATES[kind], year, season, fields,
        source["id"] if source else None, seed_parts=(ruler["id"],)
    )


def record_war(conn, attacker_kingdom, defender_kingdom, attacker_won, captured_city=None):
    """
    Both kingdoms record a war that was actually fought, each from its own
    side: the attacker tells of marching out and winning or being thrown
    back, the defender of being invaded and holding or falling. A city taken
    (captured_city is its name) is named in both accounts.

    Call it before the war's deaths are recorded, so a chronicle tells of the
    war first and then of any ruler who fell in it. Returns both entries;
    point them at the War event once it exists with attach_source_event().
    """

    year, season = current_date(conn, attacker_kingdom["world_id"])

    if attacker_won:
        attacker_key = "War (attacker won, city taken)" if captured_city else "War (attacker won)"
        defender_key = "War (defender lost, city taken)" if captured_city else "War (defender lost)"
    else:
        attacker_key, defender_key = "War (attacker lost)", "War (defender held)"

    entries = []

    for me, other, key in ((attacker_kingdom, defender_kingdom, attacker_key), (defender_kingdom, attacker_kingdom, defender_key)):
        entries.append(write_entry(
            conn, me["id"], "War", key, year, season,
            {"kingdom": me["name"], "other": other["name"], "city": captured_city or ""},
            seed_parts=(other["id"],)
        ))

    return entries


def attach_source_event(conn, entries, event_id):
    """Points entries written before their event existed at that event."""

    for entry in entries:
        conn.execute("UPDATE chronicle_entries SET source_event_id = ? WHERE id = ?", (event_id, entry["id"]))
        entry["source_event_id"] = event_id


def get_chronicle(conn, kingdom_id):
    """A kingdom's chronicle, oldest entry first."""

    rows = conn.execute(
        "SELECT * FROM chronicle_entries WHERE kingdom_id = ? ORDER BY id", (kingdom_id,)
    ).fetchall()

    return [dict(row) for row in rows]