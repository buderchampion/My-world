"""
The bestiary: the Monster Manual's monsters (Monsters.json, imported by
Tools/import_monster_manual.py), each sorted for this world --

    raider    intelligent (Low or better) and evil: raids villages near its lair
    beast     below Average intelligence and not a raider: falls on travelers crossing the wilds
    peaceful  Average intelligence or better, and not evil: keeps to its lair

-- with where it lives (terrains, and climate for creatures of the cold),
and how common it is. Kept aside, not placed in this world (yet):

    aquatic   it only swims (in the book, // marks swimming), and this world has no sea
    planar    demons, devils, elementals and other beings of other planes
    people    men, elves, dwarves and the other peoples -- for the races of kingdoms
    mundane   farm and draught animals: not monsters of the wilds

The book has no terrain row (that came in later editions; habitat is only in
its prose, which was not imported), so habitats here are this world's own rules.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

MONSTERS = Path(__file__).resolve().parent / "Monsters.json"

INTELLIGENCE = [("non", 0), ("animal", 1), ("semi", 2), ("low", 3), ("average", 4), ("avg", 4), ("very", 5),
                ("high", 6), ("exceptional", 7), ("genius", 8), ("supra", 9), ("god", 10)]
FREQUENCY_WEIGHT = [("very", 4), ("uncommon", 20), ("rare", 11), ("rore", 11), ("common", 65)]   # the book's own odds
RAIDER_INTELLIGENCE = 3               # Low or better, and evil: raids villages
PEACEFUL_INTELLIGENCE = 4             # Average or better, and not evil: keeps to its lair; anything less falls on travelers

PLANAR = r"\b(Demon|Devil|Elemental|Djinni|Efreeti|Aerial Servant|Invisible Stalker|Night Hag|Nightmare|Xorn|Couatl|Ki-Rin|" \
         r"Titan|Tiamat|Bahamut|Lammasu|Shedu|Hell Hound|Imp|Quasit|Salamander|Mezzoloth|Rakshasa|Barghest|Intellect Devourer|Larva)"
AQUATIC = r"\b(Plesiosaurus|Dinichthys|Elasmosaurus|Archelon|Mosasaurus|Nothosaurus|Tylosaurus|Strangle Weed|Weed|Shark|Whale|Dolphin|Eel|Barracuda|Lamprey|Octopus|Squid|Kraken|Sea |Merman|Mermen|Sahuagin|Locathah|Ixitxachitl|" \
          r"Kopoacinth|Triton|Hippocampus|Pike|Gar\b|Ray\b|Ray,|Man-O-War|Crab|Dragon Turtle|Turtle, Sea|Morkoth|Masher|Eye Of The Deep|" \
          r"Water Weird|Nixie|Kuo-Toa|Scrag)"
DEMON_PAGES = range(17, 24)           # the book's demons and devils: planar, whatever a column is named
MUNDANE = r"\b(Bull|Mule|Herd Animal|Cattle|Horse|Pony|Camel|Dog, War|Donkey|Ox|Sheep|Goat)\b"     # farm and draught animals
DINOSAUR_PAGES = range(24, 30)        # the book's dinosaurs, whatever a column is named
WARM = r"Dinosaur|Iguanadon|Titanothere|Baluchitherium|Mastodon"      # the lost, warm lands of old beasts
PEOPLE = r"^(Men\b|Man\b|Caveman|Elf|Dwarf|Gnome|Halfling|Half-|Human|Pilgrim|Merchant|Bandit|Berserker|Buccaneer|Dervish|Nomad|" \
         r"Tribesman|Pirate|Brigand)"

# habitats: family pattern -> terrains (first match wins). Terrains: Plains, Forest, Hills, Mountains, Marsh, Steppe.
HABITATS = [
    (r"Dragon, Black|Black Dragon|Lizard|Crocodile|Frog|Toad|Leech|Will-O|Shambling|Troll|Hydra|Catoblepas|Bullywug|Gar|Water Moccasin", ["Marsh"]),
    (r"Dragon, Green|Green Dragon|Treant|Dryad|Pixie|Sprite|Brownie|Unicorn|Centaur|Owlbear|Spider|Wolverine|Boar|Bear|Wolf|Elk|Stag|"
     r"Leprechaun|Satyr|Sylph|Wood|Ape|Tiger|Leopard|Panther|Weasel|Badger|Beetle|Ettercap|Displacer|Blink Dog|Shrieker", ["Forest"]),
    (r"Dragon, Blue|Blue Dragon|Dragon, Brass|Brass Dragon|Lion|Hyena|Jackal|Camel|Ant Lion|Gnoll|Lamia|Sphinx|Dervish|Dinosaur|"
     r"Titanothere|Mastodon|Mammoth|Elephant|Rhinoceros|Buffalo|Cattle|Horse|Flightless Bird|Basilisk|Purple Worm|Anhkheg", ["Steppe", "Plains"]),
    (r"Dragon|Giant, Stone|Stone Giant|Cloud Giant|Storm Giant|Fire Giant|Frost Giant|Griffon|Hippogriff|Roc|Wyvern|Manticore|Chimera|"
     r"Gargoyle|Harpy|Eagle|Yeti|Remorhaz|Dwarf|Gorgon|Cockatrice|Pegasus|Mountain", ["Mountains", "Hills"]),
    (r"Hill Giant|Ogre|Orc|Goblin|Hobgoblin|Kobold|Bugbear|Ettin|Cyclops|Umber Hulk|Carrion Crawler|Rust Monster|Mind Flayer|"
     r"Gelatinous|Jelly|Ooze|Slime|Pudding|Mold|Piercer|Lurker|Trapper|Roper|Mimic|Cave|Troglodyte|Bat", ["Hills", "Mountains", "Forest"]),
    (r"Skeleton|Zombie|Ghoul|Ghast|Wight|Wraith|Spectre|Vampire|Lich|Mummy|Ghost|Shadow|Banshee|Groaning|Poltergeist|Haunt", None),
]
COLD = r"Frost|Dragon, White|White Dragon|Winter Wolf|Yeti|Remorhaz|Ice Toad|Mammoth|Polar|Snow"
ALL_TERRAINS = ["Plains", "Forest", "Hills", "Mountains", "Marsh", "Steppe"]


def intelligence_of(text):
    t = (text or "").lower().replace(" ", "").replace("0", "o")       # 'l o w' -> 'low'
    for word, level in INTELLIGENCE:
        if word in t:
            return level
    return 0


def is_evil(text):
    return bool(re.search(r"ev[i1l]", (text or "").lower()))


def frequency_weight(text):
    t = (text or "").lower().replace(" ", "")
    for word, weight in FREQUENCY_WEIGHT:
        if word in t:
            return weight
    return 11


def only_swims(move):
    """In the book '//' marks a swimming speed: '//24\"' or '0\"//18\"' is a creature of the water."""
    m = (move or "").replace(" ", "")
    return "//" in m and not re.match(r"[1-9]", m)


def habitat_of(name):
    for pattern, terrains in HABITATS:
        if re.search(pattern, name, re.IGNORECASE):
            return terrains or ALL_TERRAINS            # undead haunt any land
    return ALL_TERRAINS


def classify(monster):
    name = monster["name"]
    out = dict(monster)
    out["intelligence_level"] = intelligence_of(monster.get("intelligence"))
    out["evil"] = is_evil(monster.get("alignment"))
    out["weight"] = frequency_weight(monster.get("frequency"))
    if re.search(PLANAR, name, re.IGNORECASE) or monster.get("page") in DEMON_PAGES:
        out["kind"] = "planar"
    elif re.search(MUNDANE, name, re.IGNORECASE):
        out["kind"] = "mundane"                        # ordinary animals, not monsters of the wilds
    elif re.search(PEOPLE, name, re.IGNORECASE):
        out["kind"] = "people"
    elif re.search(AQUATIC, name, re.IGNORECASE) or only_swims(monster.get("move")):
        out["kind"] = "aquatic"
    elif out["intelligence_level"] >= RAIDER_INTELLIGENCE and out["evil"]:
        out["kind"] = "raider"
    elif out["intelligence_level"] < PEACEFUL_INTELLIGENCE:
        out["kind"] = "beast"                          # an owlbear or a lizard man is no less dangerous for not being evil
    else:
        out["kind"] = "peaceful"
    out["terrains"] = habitat_of(name)
    out["cold_only"] = bool(re.search(COLD, name, re.IGNORECASE))
    out["warm_only"] = bool(re.search(WARM, name, re.IGNORECASE)) or monster.get("page") in DINOSAUR_PAGES
    out["usable"] = out["kind"] in ("raider", "beast", "peaceful") and monster["stats"].get("hit_dice") is not None
    return out


@lru_cache(maxsize=1)
def bestiary():
    """Every monster, classified (cached: the file does not change while the world runs)."""
    return [classify(m) for m in json.loads(MONSTERS.read_text(encoding="utf-8"))]


def of_this_world():
    """The monsters that live in this world: raiders, beasts and peaceful ones with usable statistics."""
    return [m for m in bestiary() if m["usable"]]


def by_name(name):
    return next((m for m in bestiary() if m["name"] == name), None)