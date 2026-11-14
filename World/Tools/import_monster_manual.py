"""
Imports the stat blocks of the AD&D Monster Manual (the 1977 book, scanned
with a machine-read text layer) into Data/Monsters/Monsters.json.

Only the game numbers are taken -- frequency, numbers appearing, armor
class, movement, hit dice, attacks and damage, special attacks and
defenses, magic resistance, intelligence, alignment, size -- never the
book's descriptive text.

    py Tools/import_monster_manual.py <the PDF> [--report]

Needs the `pdftotext` program (from Poppler) to read the PDF.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

LABELS = [  # (label as printed, our key)
    ("FREQUENCY", "frequency"), ("NO. APPEARING", "number_appearing"), ("ARMOR CLASS", "armor_class"),
    ("MOVE", "move"), ("HIT DICE", "hit_dice"), ("% IN LAIR", "in_lair"), ("TREASURE TYPE", "treasure"),
    ("NO. OF ATTACKS", "attacks"), ("DAMAGE/ATTACK", "damage"), ("SPECIAL ATTACKS", "special_attacks"),
    ("SPECIAL DEFENSES", "special_defenses"), ("MAGIC RESISTANCE", "magic_resistance"),
    ("INTELLIGENCE", "intelligence"), ("ALIGNMENT", "alignment"), ("SIZE", "size"), ("PSIONIC ABILITY", "psionic"),
]
# the scan's misreadings of the labels themselves
LABEL_PATTERN = re.compile(
    r"(FREQUENCY|NO\.\s*APPEARING|ARMOR\s*CLASS|MOVE|HIT\s*DICE|(?:%|Yo|Y0|o/o|\$)\s*IN\s*LAIR|TREASURE\s*TYPE|NO\.\s*OF\s*A(?:TT|ll|lt|tl|TL)ACKS|"
    r"DAMAG(?:E\s*[/Il1|]|W)\s*ATTACK|SPECIAL\s*ATTACKS|SPECIAL\s*DEFENSES|MAGIC\s*RESISTANCE|INTELLIGENCE|ALIGNMENT|SIZE|PS[I1lL]ON[I1lL]C\s*ABIL(?:ITY|IN))\s*:",
    re.IGNORECASE)
KEY_OF = {"FREQUENCY": "frequency", "NOAPPEARING": "number_appearing", "ARMORCLASS": "armor_class", "MOVE": "move",
          "HITDICE": "hit_dice", "INLAIR": "in_lair", "TREASURETYPE": "treasure", "NOOFATTACKS": "attacks",
          "DAMAGE/ATTACK": "damage", "SPECIALATTACKS": "special_attacks", "SPECIALDEFENSES": "special_defenses",
          "MAGICRESISTANCE": "magic_resistance", "INTELLIGENCE": "intelligence", "ALIGNMENT": "alignment", "SIZE": "size",
          "PSIONICABILITY": "psionic"}
HEADING = re.compile(r"^[A-Z][A-Z ,'\-()]{2,}$")


def label_key(text):
    t = re.sub(r"[\s.:]", "", text.upper())
    t = re.sub(r"^(%|YO|Y0|O/O|\$)", "", t)
    t = re.sub(r"^DAMAG(?:E[IL1|]|W)ATTACK$", "DAMAGE/ATTACK", t)       # the slash misread as I, or E/ as W
    t = re.sub(r"^NOOFA(?:LL|LT|TL)ACKS$", "NOOFATTACKS", t)             # TT misread as ll
    t = t.replace("ABILIN", "ABILITY")
    t = t.replace("PS1ON1C", "PSIONIC").replace("PSLONLC", "PSIONIC").replace("PSIONLC", "PSIONIC").replace("PSLONIC", "PSIONIC")
    return KEY_OF.get(t)


def looks_like_name(value):
    """A variant's name above a stat column: a few words, no colon, no sentence, no numbers."""
    words = value.split()
    if value.strip().lower() in ("nil", "none", "-"):
        return False                                    # the row above, not a name
    return (re.search(r"[A-Za-z]", value) and len(words) <= 4 and ":" not in value and not value.endswith((".", ","))
            and not re.search(r"\d", value) and not value.startswith(("-", "_")))


def tokens(segment, offset):
    """Values separated by two or more spaces, with their absolute CENTRE positions (the book centres each
    value under its column, so a long value starts well left of a short one)."""
    return [(m.start() + offset + len(m.group()) / 2, m.group().strip()) for m in re.finditer(r"\S+(?: \S+)*", segment)]


def usual_boundary(pages):
    """Where a page's right half usually begins: measured on pages with stat blocks in both halves."""
    seen = []
    for page in pages:
        rights = [m.start() for line in page.split("\n") for m in LABEL_PATTERN.finditer(line) if m.start() > 40]
        if rights:
            seen.append(min(rights) - 2)
    return sorted(seen)[len(seen) // 2] if seen else 85


def parse_pages(text):
    blocks = []
    pages = text.split("\f")
    usual = usual_boundary(pages)
    last_block = None
    for page_no, page in enumerate(pages, 1):
        lines = page.split("\n")
        rights = [m.start() for line in lines for m in LABEL_PATTERN.finditer(line) if m.start() > 40]
        boundary = min(rights) - 2 if rights else usual      # a right half of prose still ends the left half's values
        for half in ("left", "right"):
            current, last_key, heading, recent = None, None, None, []
            for line in lines:
                part = line[:boundary] if half == "left" else line[boundary:]
                offset = 0 if half == "left" else boundary
                stripped = part.strip()
                found = list(LABEL_PATTERN.finditer(part))
                if not found:
                    if HEADING.match(stripped) and len(stripped) <= 40:
                        heading = stripped
                    pieces = tokens(part, offset) if current and last_key and last_key != "psionic" and stripped else []
                    if pieces and len(pieces) <= 2 * len(current["cols"]) and all(   # a value may itself be split in two
                            min(abs(c - pos) for c in current["cols"]) <= 14 and len(v) <= 24 and not v.endswith(".")
                            for pos, v in pieces):
                        for pos, value in pieces:                                   # a row that wraps onto the next line
                            col = min(range(len(current["cols"])), key=lambda i: abs(current["cols"][i] - pos))
                            current["values"][col][last_key] = (current["values"][col].get(last_key, "") + " " + value).strip()
                        continue
                    recent = (recent + [(part, offset)])[-6:]
                    continue
                m = found[0]
                key = label_key(m.group(1))
                rest = part[m.end():]
                values = tokens(rest, offset + m.end())
                if key == "frequency":
                    cols = [pos for pos, _ in values] or [offset + m.end()]
                    names = [[] for _ in cols]
                    for prev, prev_off in recent[-5:]:                               # names above each column, over a few lines
                        for pos, value in tokens(prev, prev_off):
                            first = value.split()[0].strip(",")
                            if (HEADING.match(value) or (len(first) >= 3 and first.isupper() and first.isalpha())) and len(value) > 2:
                                heading = value                                      # a capitals line is always a heading
                                names = [[] for _ in cols]                           # ...and names above it belong to another block
                                continue
                            value = re.sub(r"^(?:Nil\s+)+", "", value)               # a stray "Nil" from the row above
                            if not looks_like_name(value):
                                continue                                             # rules (---, __), prose, a previous block's rows
                            col = min(range(len(cols)), key=lambda i: abs(cols[i] - pos))
                            if abs(cols[col] - pos) <= 28:
                                names[col].append(value)
                    names = [" ".join(n) for n in names]
                    current = {"page": page_no, "heading": heading, "cols": cols, "names": names,
                               "values": [dict() for _ in cols]}
                    blocks.append(current)
                    last_block = current
                if current is None and last_block is not None and key not in ("frequency", None) \
                        and len(last_block["cols"]) == 1 and key not in last_block["values"][0]:
                    current = last_block                       # a block running on from the foot of the previous column
                if current is None or key is None:
                    continue
                for pos, value in values:
                    col = min(range(len(current["cols"])), key=lambda i: abs(current["cols"][i] - pos))
                    current["values"][col][key] = (current["values"][col].get(key, "") + " " + value).strip()
                last_key = key
                recent = []
    return blocks


# --- cleaning the scan's misreadings --------------------------------------------------------
def numeric(text):
    """'I -8/ 1-8/ I - 12' -> '1-8/1-8/1-12'"""
    t = text or ""
    t = re.sub(r"\bIO\b|\bI0\b|\b1O\b", "10", t)                      # 'IO-12' -> '10-12'
    t = re.sub(r"(?<![A-Za-z])[Il|](?![A-Za-z])", "1", t)
    t = re.sub(r"(?<=\d)[Il|]|[Il|](?=\d)", "1", t)
    t = re.sub(r"(?<=\d)O|O(?=\d)", "0", t)
    t = re.sub(r"\s*-\s*", "-", t)
    t = re.sub(r"\s*/\s*", "/", t)
    t = re.sub(r"\s*\+\s*", "+", t)
    return t.strip()


def parse_range(text):
    m = re.fullmatch(r"(\d+)-(\d+)", text)
    if m:
        return [int(m.group(1)), int(m.group(2))]
    if text.isdigit():
        return [int(text), int(text)]
    return None


def damage_ranges(text):
    """'1-6/1-6/1-8' -> [[1, 6], [1, 6], [1, 8]]. The scan sometimes reads a '/' as a '1' ('4-16/1-1011-10'):
    each '1' is tried as a '/', and kept only if every part then reads as a sensible range."""
    def read(t):
        parts = [parse_range(d) for d in t.split("/") if d]
        return parts if parts and all(p and p[0] <= p[1] <= 100 for p in parts) else None
    text = text.strip()
    if read(text):
        return read(text)
    if re.fullmatch(r"[\d/\-]+", text):
        for i, ch in enumerate(text):
            if ch == "1":
                fixed = read(text[:i] + "/" + text[i + 1:])
                if fixed:
                    return fixed
    return None


EMBEDDED = [("ALIGNMENT", "alignment"), ("INTELLIGENCE", "intelligence"), ("SIZE", "size"), ("MAGIC RESISTANCE", "magic_resistance"),
            ("SPECIAL DEFENSES", "special_defenses"), ("SPECIAL ATTACKS", "special_attacks"), ("PSIONIC ABILITY", "psionic")]


def clean(monster):
    out = dict(monster)
    for key in list(out):                       # the dragons' "CHANCE OF" row is not one we keep
        if isinstance(out[key], str) and "CHANCE OF" in out[key].upper():
            out[key] = re.split(r"CHANCE\s*OF", out[key], flags=re.IGNORECASE)[0].strip()
    for key in list(out):                       # a label that lost its colon swallowed the next row: split it out
        if not isinstance(out[key], str):
            continue
        for label, target in EMBEDDED:
            m = re.search(r"\s" + label.replace(" ", r"\s*") + r"\b\s*:?\s*", out[key])
            if m and target != key and not out.get(target):
                out[key], out[target] = out[key][:m.start()].strip(), out[key][m.end():].strip()
    for key in ("number_appearing", "armor_class", "hit_dice", "attacks", "damage", "in_lair"):
        if out.get(key):
            out[key] = numeric(out[key])
    nums = {}
    ac = re.match(r"-?\d+", out.get("armor_class", ""))
    nums["ac"] = int(ac.group()) if ac else None
    hd = re.match(r"(\d+)(?:\+(\d+))?", out.get("hit_dice", ""))
    nums["hit_dice"] = int(hd.group(1)) if hd else None
    nums["hit_dice_bonus"] = int(hd.group(2)) if hd and hd.group(2) else 0
    att = re.match(r"\d+", out.get("attacks", ""))
    nums["attacks"] = int(att.group()) if att else None
    nums["damage"] = damage_ranges(out.get("damage", ""))
    mv = re.match(r"(\d+)", numeric(out.get("move", "")).replace('"', ""))
    nums["move"] = int(mv.group(1)) if mv else None
    na = parse_range(out.get("number_appearing", ""))
    nums["number_appearing"] = na
    lair = re.match(r"(\d+)", out.get("in_lair", ""))
    nums["in_lair_percent"] = int(lair.group(1)) if lair else (0 if out.get("in_lair", "").lower().startswith("nil") else None)
    size = re.sub(r"^\+\s*", "", (out.get("size", "") or "").strip())
    out["size"] = size
    nums["size"] = size[:1].upper() if size[:1].upper() in "SML" else None
    out["stats"] = nums
    return out


# Names the scan garbled, or left without a heading: each checked against the page it is on.
# (page, name as imported) -> name
NAME_FIXES = {
    (17, "Olphin - Doppleganger -, Demogorgon, (Prince of Demons)"): "Demon, Demogorgon (Prince of Demons)",
    (19, "Type I (Vrock)"): "Demon, Type I (Vrock)",
    (19, "page 19"): "Demon, unidentified (page 19)",
    (20, "Demon, TypeV (Marilith, etc.)"): "Demon, Type V (Marilith, etc.)",
    (20, "Demon (Yeenoghuj"): "Demon, Yeenoghu (Demon Lord of Gnolls)",
    (21, "Devil, Armodeus (Arch-devi I )"): "Devil, Asmodeus (Arch-devil)",
    (22, "Devil, Dispater (Arch-devi I)"): "Devil, Dispater (Arch-devil)",
    (25, "Dinosaur, Antrodemus (AIlosaurus)"): "Dinosaur, Antrodemus (Allosaurus)",
    (25, "Dinosaur, Archelon lschyras"): "Dinosaur, Archelon Ischyras",
    (29, "Displacer Beast, DJlNNl"): "Djinni",
    (35, "Dra Gonne"): "Dragonne",
    (60, "Lamprey, I Giont"): "Lamprey, Giant",
    (61, "page 61"): "Leprechaun",
    (62, "Leucrotta, LlCH"): "Lich",
    (71, "I"): "Mimic",
    (77, "Ogre Mage (Japanese ( 'Re)"): "Ogre Mage (Japanese Ogre)",
    (80, "Portuguese Man-0-War, Giont"): "Portuguese Man-O-War, Giant",
    (87, "Sea Hag, I"): "Sea Hag",
    (87, "I"): "Sea Lion",
    (88, "I"): "Shambling Mound",
    (102, "page 102"): "unidentified (page 102)",
    (32, "Dragon"): "Dragon, Black",                 # armor class 3, 6-8 hit dice, chaotic evil
    (34, "Dragon, Gold"): "Dragon, Green",           # armor class 2, 7-9 hit dice, lawful evil (the Gold is on page 33)
}


MISREAD_WORDS = {"Dinosa Ur": "Dinosaur", "Golern": "Golem", "Lguanadon": "Iguanadon", "SHEdu": "Shedu", "Giont": "Giant", "Neutrol": "Neutral"}


def tidy_name(name):
    """'Giant, Frost, Frost Giant' -> 'Frost Giant'; 'Wolf, Wolf, Dire (Worg)' -> 'Wolf, Dire (Worg)';
    'Boar, Boar, Wild Giant Boar Uncommon' -> 'Wild Giant Boar'."""
    for wrong, right in MISREAD_WORDS.items():
        name = name.replace(wrong, right)
    name = re.sub(r"\s+(?:Common|Uncommon|Rare|Very rare|[a-z])$", "", name)       # a stray frequency word or letter
    parts = [p.strip() for p in name.split(",") if p.strip()]
    collapsed = [p for i, p in enumerate(parts) if i == 0 or p.lower() != parts[i - 1].lower()]
    head = collapsed[0].split()[0].lower() if collapsed else ""
    if len(collapsed) >= 2 and head and head in collapsed[-1].lower():
        return collapsed[-1]                         # the column's own full name already says what it is
    if collapsed and collapsed[0] == "Skeleton" and len(collapsed) > 1:
        return collapsed[-1]                         # the Skeleton page heading over the Shedu
    return ", ".join(collapsed)


# Dragons whose blocks the scan interleaves with prose, named by their numbers (each set is unique to one colour):
# (page, name as imported, armor class) -> name
NAMES_BY_ARMOR = {
    (33, "Dragon, Gold", 1): "Dragon, Copper",      # 7-9 hit dice, chaotic good
    (34, "T, V", -1): "Dragon, Red",                # 9-11 hit dice, chaotic evil
    (35, "Dragon, Red", -1): "Dragon, Silver",      # 9-11 hit dice, lawful good
    (35, "Dragonne", 3): "Dragon, White",           # 5-7 hit dice, chaotic evil (the Dragonne itself is armor class 6/2)
}


def monsters_from(blocks):
    """Each column a monster -- except columns of stray marks, with almost none of the main rows."""
    monsters = []
    for b in blocks:
        heading = (b["heading"] or "").title()
        for i, values in enumerate(b["values"]):
            if not values:
                continue
            sub = b["names"][i] if i < len(b["names"]) and b["names"][i] else None
            sub = sub[:1].upper() + sub[1:] if sub else sub                         # 'stag' -> 'Stag'
            base = re.split(r"\s*\(", heading)[0].strip(" ,") if heading else ""     # 'Dinosaur (Ankylosaurus)' -> 'Dinosaur'
            name = (f"{base}, {sub}" if sub and base and sub.lower() not in base.lower()
                    else (sub or heading or f"page {b['page']}"))
            if sum(1 for k in ("armor_class", "hit_dice", "move", "damage", "attacks", "number_appearing") if values.get(k)) < 3:
                continue
            if not re.match(r"\s*(-?\s*[\dIl|]|See|Var|Special|Nil)", values.get("armor_class", "") or "", re.IGNORECASE):
                continue                                       # prose that slipped through, not a stat block
            name = NAME_FIXES.get((b["page"], name.strip()), tidy_name(name.strip()))
            monster = clean({"name": name, "page": b["page"], **values})
            monster["name"] = NAMES_BY_ARMOR.get((b["page"], name, monster["stats"]["ac"]), name)
            monsters.append(monster)
    return monsters


def report(monsters):
    keys = {"ac": "armor_class", "hit_dice": "hit_dice", "attacks": "attacks", "damage": "damage", "move": "move",
            "number_appearing": "number_appearing", "size": "size"}
    print(f"{len(monsters)} monsters, {sum(1 for m in monsters if m['name'].startswith('page '))} without a name")
    for k, raw in keys.items():
        ok = sum(m["stats"].get(k) is not None for m in monsters)
        missing = sum(not m.get(raw) for m in monsters)
        words = len(monsters) - ok - missing
        print(f"  {k:18} {ok:4} read ({ok / max(1, len(monsters)):.0%}),  {words:3} in words,  {missing:3} missing")


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    text = subprocess.run(["pdftotext", "-layout", argv[0], "-"], capture_output=True, text=True).stdout
    monsters = monsters_from(parse_pages(text))
    if "--report" in argv:
        report(monsters)
    out = Path(__file__).resolve().parents[1] / "Data" / "Monsters" / "Monsters.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(monsters, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))