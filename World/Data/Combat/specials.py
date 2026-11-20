"""
Monsters' special attacks, by the book (Data/Monsters/SpecialAttacks.json):
everything a monster does beyond its plain blows, in the fight of
Data.Combat.melee.

Saving throws   d20 against a number by level (people) or hit dice (monsters),
                as fighters in the Dungeon Master's Guide, in five kinds:
                paralyzation, poison and death; petrification; wands; breath;
                spells. Dwarves and halflings shrug off poison and magic (+4),
                gnomes magic; elves resist sleep and charm nine times in ten,
                and no ghoul's touch can freeze them.

When a fight opens (opening): an ambusher may catch its foes unready (they
                lose the first round); the sight of a mummy, a ghost or a lich
                may send them fleeing; a troglodyte's stench or a skunk's musk
                weakens or blinds them; a dragonne's roar does both.

A monster's turn (act): instead of its blows it may breathe (a cone, line or
                cloud over `area` foes: damage -- a dragon's is its own hit
                points -- or stone, sleep, fear, slowing or death; save for
                half or none), hurl rocks or shoot spikes from afar in the
                first rounds, wail (a banshee: save or die), or blast minds
                (a mind flayer: the foes stand stunned). Gazes, songs and a
                beholder's eye rays come each round as well as its blows.

A blow that lands (on_hit) may poison (save or die), paralyze, turn to stone,
                put to sleep, carry disease (caught after the fight,
                Data.NPCS.sickness), drain life levels (wights, wraiths,
                spectres, vampires: a person drained of all their levels
                dies; the living lose their training, Data.NPCS.experience),
                weaken, age (a ghost), burn with acid, rust armor away,
                swallow whole, latch on to drain blood, smother, or turn
                flesh to slime. A bear that lands both paws hugs, a great cat
                rakes with its rear claws (squeeze); a charging beast strikes
                twice as hard in the first round.

End of each round (round_end): the gripped bleed, the swallowed are
                digested, the smothered choke, the slimed rot; a victim whose
                monster has fallen is free.

After the fight (finish, aftermath): the swallowed and smothered whose
                monster lives are lost; the slimed die unless their side won
                the field (and then half are saved, the slime burned away).
                The living carry their drained levels, years and sicknesses
                home. Each fighter keeps a `cause` for the death record.
"""

from __future__ import annotations

import copy
import json
import random
from functools import lru_cache
from pathlib import Path

SPECIALS_FILE = Path(__file__).resolve().parent.parent / "Monsters" / "SpecialAttacks.json"

# Saving throws as fighters (DMG): up to this level -> paralyzation/poison/death, petrification, wands, breath, spells.
SAVES = [(0, (16, 17, 18, 20, 19)), (2, (14, 15, 16, 17, 17)), (4, (13, 14, 15, 16, 16)), (6, (11, 12, 13, 13, 14)),
         (8, (10, 11, 12, 12, 13)), (10, (8, 9, 10, 9, 11)), (12, (7, 8, 9, 8, 10)), (14, (5, 6, 7, 5, 8)),
         (16, (4, 5, 6, 4, 7)), (10 ** 6, (3, 4, 5, 4, 6))]
SAVE_KINDS = {"paralyzation": 0, "poison": 0, "death": 0, "petrification": 1, "polymorph": 1, "wand": 2, "breath": 3, "spell": 4}

BREATH_CHANCE = 1 / 3          # a breather with breath left uses it after the first round this often
CHARMED_ROUNDS = 999           # a charmed fighter stands helpless for the rest of the fight
SLIME_SAVED = 0.5              # the slimed whose side holds the field: this share are saved (burned or cut free)
MIN_DAMAGE_SCALE = 0.25        # weakening never leaves a fighter weaker than this
RUSTED_AC = 9                  # armor eaten away: little better than none
DRAIN_HP = 5.5                 # hit points lost with each life level drained (a fighter's d10)
BEHOLDER_RAYS = ("death", "stone", "sleep", "charm", "fear", "slow", "wounds", "disintegrate")
UNDEAD_IMMUNE = ("sleep", "charm", "hold", "fear", "confuse")      # the mindless feel none of these


@lru_cache(maxsize=1)
def table():
    with open(SPECIALS_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def specials_of(name):
    """A fresh copy of a monster's special attacks (each fighter keeps its own uses)."""
    return copy.deepcopy(table().get(name, []))


def roll(dice):
    lo, hi = dice
    return random.randint(min(lo, hi), max(lo, hi)) if hi else 0


def a_name(name):
    name = (name or "monster").split(" (")[0]
    if "," in name:                                    # "Dragon, Red" -> "red dragon"
        head, tail = [p.strip() for p in name.split(",", 1)]
        name = f"{tail} {head}"
    name = name.lower()
    return ("an " if name[0] in "aeiou" else "a ") + name


# ---------------------------------------------------------------- saving throws

def save_number(level, kind):
    level = max(0, int(level or 0))
    for top, numbers in SAVES:
        if level <= top:
            return numbers[SAVE_KINDS[kind]]
    return SAVES[-1][1][SAVE_KINDS[kind]]


def _ability(f, key, default=0):
    race = getattr(f, "race", "")
    if not race:
        return default
    from Data.NPCS.races import abilities
    return abilities(race).get(key, default)


def save(f, kind, mod=0, mental=False):
    """Does `f` make its saving throw of this kind? kind None: there is no save. `mental`: an attack on the mind
    (fear, charm, a mind blast), against which Wisdom helps (Data.NPCS.abilities)."""
    if kind is None:
        return False
    bonus = mod + (getattr(f, "wis_save", 0) if mental else 0)
    if kind in ("spell", "wand"):
        bonus += _ability(f, "save_vs_magic")
    if kind == "poison":
        bonus += _ability(f, "save_vs_poison")
    return random.randint(1, 20) + bonus >= save_number(f.hit_dice, kind)


# ---------------------------------------------------------------- arming

def arm(fighter, monster):
    """Gives a monster fighter its special attacks (and a caster's spells, a peryton's edge)."""
    fighter.specials = specials_of(monster["name"])
    for spec in fighter.specials:
        if spec["type"] == "edge":
            fighter.to_hit += spec.get("to_hit", 0)
        elif spec["type"] == "spells":
            from Data.Magic.magic import arm_caster
            arm_caster(fighter, spec["class"], spec["level"])
    return fighter


def _alive(fs):
    return [f for f in fs if f.alive]


# ---------------------------------------------------------------- afflictions

def afflict(f, effect, src, word="", rounds=None):
    """Puts an effect on `f` (who has already failed any save). Returns True if it took."""
    if f.mindless and effect in UNDEAD_IMMUNE:
        return False
    if effect == "sleep":
        from Data.NPCS.races import resists_sleep
        if resists_sleep(getattr(f, "race", "")):
            return False
        f.held = max(f.held, roll(rounds or (5, 30)))
    elif effect == "hold":
        f.held = max(f.held, roll(rounds or (2, 8)))
    elif effect == "charm":
        if random.random() < _ability(f, "resist_charm", 0):
            return False
        f.held = CHARMED_ROUNDS
        f.fx["charmed"] = True
    elif effect == "fear":
        f.out = True
    elif effect == "slow":
        f.damage_scale = max(MIN_DAMAGE_SCALE, f.damage_scale * 0.5)
    elif effect == "confuse":
        f.stunned = max(f.stunned, roll(rounds or (3, 12)))
    elif effect == "stone":
        f.hp = 0
        f.cause = f"turned to stone by {a_name(src.name)}"
    elif effect in ("death", "disintegrate"):
        f.hp = 0
        f.cause = f"slain by {a_name(src.name)}'s {word or 'power'}"
    else:
        return False
    return True


def _harm(f, amount, src, word):
    f.hp -= amount
    if f.hp <= 0 and not f.cause:
        f.cause = f"slain by {a_name(src.name)}'s {word}" if word else f"slain by {a_name(src.name)}"


# ---------------------------------------------------------------- when the fight opens

def opening(side_a, side_b):
    """Ambushes, terrors and stenches before the first blow. Once for each kind of monster on a side."""
    for mine, theirs in ((side_a, side_b), (side_b, side_a)):
        done = set()
        for f in mine:
            for spec in getattr(f, "specials", None) or []:
                key = (f.name, spec["type"])
                if key in done:
                    continue
                t = spec["type"]
                if t == "surprise":
                    done.add(key)
                    if random.randint(1, 6) <= spec.get("chance", 2):
                        for foe in theirs:
                            foe.stunned = max(foe.stunned, 1)
                elif t == "fear":
                    done.add(key)
                    for foe in _alive(theirs):
                        if spec.get("max_level") is not None and (foe.hit_dice or 0) > spec["max_level"]:
                            continue                                # dragon awe: only the untrained break at the sight
                        if not foe.mindless and not save(foe, spec.get("save", "spell"), mental=True):
                            afflict(foe, "fear", f)
                            if spec.get("age"):
                                foe.fx["aged"] = foe.fx.get("aged", 0) + roll(spec["age"])
                elif t in ("stench", "roar"):
                    done.add(key)
                    for foe in _alive(theirs):
                        if foe.mindless or save(foe, spec.get("save", "poison")):
                            continue
                        foe.damage_scale = max(MIN_DAMAGE_SCALE, foe.damage_scale * spec.get("weaken", 1.0))
                        if spec.get("blind"):
                            foe.stunned = max(foe.stunned, roll(spec["blind"]))
                        if spec.get("flee") and random.random() < spec["flee"]:
                            foe.out = True


# ---------------------------------------------------------------- a monster's turn

def _breathe(f, spec, foes):
    option = random.choice(spec["choices"]) if spec.get("choices") else spec
    effect = option.get("effect", "damage")
    word = option.get("word", spec.get("word", "breath"))
    targets = _alive(foes)
    random.shuffle(targets)
    for foe in targets[:spec.get("area", 1)]:
        saved = save(foe, spec.get("save", "breath"), spec.get("save_mod", 0))
        if effect == "damage":
            amount = max(0, f.hp) if option.get("hp", spec.get("hp")) else roll(option.get("dice", spec.get("dice", (1, 6))))
            if saved and option.get("half", spec.get("half")):
                amount /= 2
            elif saved:
                continue
            if option.get("element", spec.get("element")) == "fire":
                from Data.NPCS.races import fire_share
                amount *= fire_share(getattr(foe, "race", ""))
            _harm(foe, amount, f, word)
        elif not saved:
            if afflict(foe, effect, f, word, option.get("rounds", spec.get("rounds"))) and effect in ("stone", "death"):
                foe.cause = (f"turned to stone by {a_name(f.name)}'s {word}" if effect == "stone"
                             else f"killed by {a_name(f.name)}'s {word}")


def _gaze(f, spec, foes):
    if random.random() >= spec.get("chance", 1.0):
        return
    targets = [x for x in _alive(foes) if not x.held]
    random.shuffle(targets)
    for foe in targets[:spec.get("targets", 1)]:
        if not save(foe, spec.get("save", "spell"), spec.get("save_mod", 0), mental=spec["effect"] in ("charm", "fear", "confuse")):
            afflict(foe, spec["effect"], f, spec.get("word", "gaze"), spec.get("rounds"))


def _eyerays(f, spec, foes):
    for _ in range(spec.get("rays", 3)):
        live = _alive(foes)
        if not live:
            return
        foe = random.choice(live)
        ray = random.choice(BEHOLDER_RAYS)
        if ray == "wounds":
            _harm(foe, roll((3, 24)), f, "eye ray")
        elif ray == "stone":
            if not save(foe, "petrification"):
                afflict(foe, "stone", f)
        elif not save(foe, "death" if ray in ("death", "disintegrate") else "spell"):
            afflict(foe, ray, f, "eye ray", (10, 40))


def _volley(f, spec, foes):
    word = spec.get("word", "missiles")
    for _ in range(spec.get("shots", 1)):
        live = _alive(foes)
        if not live:
            return
        foe = random.choice(live)
        die = random.randint(1, 20)
        if foe.held or die == 20 or (die != 1 and die + f.to_hit >= f.thac0 - foe.ac):
            _harm(foe, roll(spec["dice"]) * f.damage_scale, f, word)


def act(f, allies, foes, rnd):
    """A monster's specials on its turn. Returns True if it used its turn on one (no blows this round)."""
    specs = getattr(f, "specials", None)
    if not specs:
        return False
    uses = f.fx.setdefault("uses", {})
    for i, spec in enumerate(specs):                          # each round, beside its blows
        t = spec["type"]
        if t in ("gaze", "song"):
            _gaze(f, spec, foes)
        elif t == "eyerays":
            _eyerays(f, spec, foes)
    for i, spec in enumerate(specs):                          # instead of its blows
        t = spec["type"]
        if t == "breath":
            left = uses.get(i, spec.get("uses", 3))
            if left > 0 and (rnd == 1 or random.random() < spec.get("chance", BREATH_CHANCE)):
                uses[i] = left - 1
                _breathe(f, spec, foes)
                return True
        elif t == "volley" and rnd <= spec.get("rounds", 1):
            _volley(f, spec, foes)
            return True
        elif t == "wail" and not uses.get(i):
            uses[i] = 1
            targets = _alive(foes)
            random.shuffle(targets)
            for foe in targets[:spec.get("targets", 8)]:
                if not save(foe, spec.get("save", "spell")):
                    foe.hp = 0
                    foe.cause = f"struck dead by {a_name(f.name)}'s wail"
            return True
        elif t == "mindblast" and (rnd - 1) % spec.get("every", 4) == 0:
            targets = _alive(foes)
            random.shuffle(targets)
            for foe in targets[:spec.get("targets", 4)]:
                if not foe.mindless and not save(foe, spec.get("save", "spell"), mental=True):
                    foe.stunned = max(foe.stunned, roll(spec.get("rounds", (3, 12))))
            return True
    return False


# ---------------------------------------------------------------- a blow that lands

def _matches(spec, index):
    a = spec.get("attack", "any")
    return a == "any" or a == index


def on_hit(attacker, target, index, natural, damage, rnd):
    """Specials of one landed blow (blow `index`, d20 roll `natural`). Returns extra damage."""
    extra = 0.0
    src = attacker
    for spec in getattr(attacker, "specials", None) or []:
        t = spec["type"]
        if t == "charge" and rnd == 1:
            extra += damage
            continue
        if t in ("squeeze",) or not _matches(spec, index) or spec.get("attack") == "both":
            continue
        if spec.get("chance") is not None and t not in ("gaze", "breath") and random.random() >= spec["chance"]:
            continue
        if t == "poison":
            if save(target, "poison", spec.get("save_mod", 0)):
                continue
            if spec.get("paralysis"):
                afflict(target, "hold", src, rounds=spec.get("rounds"))
            elif spec.get("dice") and spec["dice"][1]:
                extra += roll(spec["dice"])
                target.cause = target.cause or f"poisoned by {a_name(src.name)}"
            elif not target.mindless:
                target.hp = 0
                target.cause = f"poisoned by {a_name(src.name)}"
        elif t == "paralyze":
            if spec.get("immunity") in (_ability(target, "immune", []) or []):
                continue
            if not target.mindless and not save(target, spec.get("save", "paralyzation")):
                afflict(target, "hold", src, rounds=spec.get("rounds"))
        elif t == "petrify":
            if not save(target, spec.get("save", "petrification")):
                afflict(target, "stone", src)
        elif t == "sleep":
            if not save(target, spec.get("save", "spell")):
                afflict(target, "sleep", src, rounds=spec.get("rounds"))
        elif t == "disease":
            if not save(target, spec.get("save")) and not target.mindless:
                target.fx["illness"] = spec.get("illness", "Wound Rot")
        elif t == "drain":
            if spec.get("kills"):
                target.hp = 0
                target.cause = f"{spec.get('word', 'slain')} by {a_name(src.name)}"
                continue
            for _ in range(spec.get("levels", 1)):
                target.fx["drained"] = target.fx.get("drained", 0) + 1
                target.hit_dice = (target.hit_dice or 0) - 1
                target.thac0 += 1
                target.max_hp = max(1.0, target.max_hp - DRAIN_HP)
                target.hp -= DRAIN_HP
                if target.hit_dice < 0 or target.hp <= 0:
                    target.hp = 0
                    target.cause = f"drained of life by {a_name(src.name)}"
                    break
        elif t == "weaken":
            if not save(target, spec.get("save")):
                target.damage_scale = max(MIN_DAMAGE_SCALE, target.damage_scale * spec.get("scale", 0.8))
        elif t == "age":
            target.fx["aged"] = target.fx.get("aged", 0) + roll(spec.get("years", (10, 40)))
        elif t == "acid":
            extra += roll(spec.get("dice", (0, 0)))
        elif t == "rust":
            target.ac = max(target.ac, RUSTED_AC)
        elif t == "swallow":
            if natural >= spec.get("roll", 19) and target.swallowed_by is None and target.alive:
                target.swallowed_by = attacker
                target.fx["digest"] = spec.get("dice", (2, 8))
                target.cause = f"swallowed whole by {a_name(src.name)}"
        elif t == "grip":
            target.fx.setdefault("grips", []).append((attacker, spec.get("dice", (1, 4)), spec.get("word", "drained of blood")))
        elif t == "envelop":
            _envelop(attacker, target, spec)
        elif t == "slime":
            if "dying" not in target.fx:
                target.fx["dying"] = [roll(spec.get("rounds", (1, 4))), spec.get("word", f"consumed by {a_name(src.name)}")]
    return extra


def _envelop(attacker, target, spec):
    if "envelop" in target.fx:
        return
    target.held = CHARMED_ROUNDS
    target.fx["envelop"] = [attacker, roll(spec.get("rounds", (2, 6))), spec.get("dice", (0, 0)),
                            f"{spec.get('word', 'smothered')} by {a_name(attacker.name)}"]


def after_attacks(attacker, target, hits, n_attacks):
    """A hug, a rake, a rending: when the first blows all land. Returns extra damage."""
    extra = 0.0
    for spec in getattr(attacker, "specials", None) or []:
        if spec["type"] == "squeeze" and len(hits) >= 1 and all(i in hits for i in range(min(spec.get("needs", 2), n_attacks))):
            extra += sum(roll(d) for d in spec.get("dice", []))
        elif spec["type"] == "envelop" and spec.get("attack") == "both" and len(hits) >= n_attacks and target.alive:
            _envelop(attacker, target, spec)
    return extra


# ---------------------------------------------------------------- end of each round, and of the fight

def _free(holder):
    return holder.hp <= 0


def round_end(fighters):
    for f in fighters:
        if f.hp <= 0:
            continue
        grips = f.fx.get("grips")
        if grips:
            still = []
            for holder, dice, word in grips:
                if holder.hp > 0 and not holder.out:
                    f.hp -= roll(dice)
                    if f.hp <= 0:
                        f.cause = f"{word} by {a_name(holder.name)}"
                        break
                    still.append((holder, dice, word))
            f.fx["grips"] = still
            if f.hp <= 0:
                continue
        if f.swallowed_by is not None:
            if _free(f.swallowed_by):
                f.swallowed_by = None                          # cut free from the dead beast
                f.cause = ""
            else:
                f.hp -= roll(f.fx.get("digest", (2, 8)))
                continue
        env = f.fx.get("envelop")
        if env:
            holder, left, dice, cause = env
            if _free(holder):
                f.held = 0
                del f.fx["envelop"]
            else:
                f.hp -= roll(dice)
                env[1] = left - 1
                if env[1] <= 0 or f.hp <= 0:
                    f.hp = 0
                    f.cause = cause
                    continue
        dying = f.fx.get("dying")
        if dying:
            dying[0] -= 1
            if dying[0] <= 0:
                f.hp = 0
                f.cause = dying[1]
                continue
        if f.stunned:
            f.stunned -= 1


def finish(side_a, side_b, winner):
    """The fight is over: the swallowed and smothered whose monster lives are lost; the slimed may be saved."""
    for key, side in (("a", side_a), ("b", side_b)):
        for f in side:
            if f.hp <= 0:
                continue
            if f.swallowed_by is not None:
                if _free(f.swallowed_by):
                    f.swallowed_by = None
                else:
                    f.hp = 0                                    # carried off inside it
                    continue
            env = f.fx.get("envelop")
            if env and not _free(env[0]):
                if winner == key:
                    f.held = 0                                  # their friends pulled them out
                else:
                    f.hp = 0
                    f.cause = env[3]
                    continue
            dying = f.fx.get("dying")
            if dying:
                if winner == key and random.random() < SLIME_SAVED:
                    del f.fx["dying"]
                else:
                    f.hp = 0
                    f.cause = dying[1]


def aftermath(conn, fighters):
    """Carries home what the living took from the fight: drained levels (lost training), years, sickness.
    Returns {"drained": n, "aged": n, "sick": n}."""
    out = {"drained": 0, "aged": 0, "sick": 0}
    for f in fighters:
        if f.ref is None or not getattr(f, "race", "") or f.hp <= 0:
            continue                                            # monsters and nameless militia carry nothing home
        if f.fx.get("drained"):
            lost = 2 * f.fx["drained"]                          # two Swordsmanship levels make a fighting level (melee)
            conn.execute("UPDATE npc_skills SET level = MAX(1, level - ?) WHERE npc_id = ? AND skill_name = 'Swordsmanship'",
                         (lost, f.ref))
            out["drained"] += 1
        if f.fx.get("aged"):
            conn.execute("UPDATE npcs SET age = age + ? WHERE id = ?", (f.fx["aged"], f.ref))
            out["aged"] += 1
        if f.fx.get("illness"):
            from Data.NPCS.sickness import catch
            catch(conn, f.ref, f.fx["illness"])
            out["sick"] += 1
    from Data.NPCS.abilities import deeds                      # and what they came through may have made them stronger
    deeds(conn, fighters)
    from Data.NPCS.personality import nudge                    # ...and harder (Data.NPCS.personality)
    nudge(conn, [f.ref for f in fighters if f.ref is not None and getattr(f, "race", "") and f.hp > 0], "battle")
    return out