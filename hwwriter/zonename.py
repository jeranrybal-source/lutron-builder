"""Naming a circuit the way an engineer would describe it.

A zone and a circuit are the same thing, and `ZoneName` is what an engineer
reads in Designer and on the review sheet. So it has to say what the circuit
IS -- "Coffer", "Pendants", "5A Sockets" -- and never "Circuit 7", which tells
him nothing he did not already know from the number beside it.

On a full design most of this is reading rather than guessing, and the rules
below are in the order the evidence deserves:

1. **He says what it is for.** "LED strip for light to breakfast bar", "Supply
   for joinery lighting". Where this exists it wins outright: it is the
   designer's own intent, and no inference of ours beats it.
2. **The fitting names the circuit.** Half a catalogue describes its own
   purpose -- a picture light, a step light, a 5 amp socket. Nothing is
   inferred here either; it is a lookup.
3. **The beam separates it from its neighbours.** Two groups of the same
   downlight in one room are told apart by what he specified them as.

and then a fourth case that is not a rule but an admission: where none of the
above separates two circuits, they take the same honest descriptive name with
a number after it, and are flagged as needing one.

**A wrong name is worse than a dull one.** An engineer who reads "Artwork" on a
circuit that actually wall-washes will believe it; a dull name he corrects in
ten seconds costs nothing. So nothing here converts a beam angle into a claim
about purpose -- naming a 25-degree group "Accent" would be exactly that, and
it would be believed. It reports the angle the designer wrote and stops.

That restraint has a specific reason. The plan publishes a beam KEY -- `/N /M
/W /F /HC`, narrow through flood -- and those letters are stable. The angles
behind them are not: `W` is 30 degrees on one fitting family and 50 on another,
so a rule keyed on degrees is wrong on some fittings. The specification carries
only the degrees. Comparing them WITHIN one product family is sound and is what
rule 3 does; comparing them across families is not, and nothing here does it.

Every circuit records which rule named it, because a name that turns out wrong
has to be traceable to its cause.
"""

from __future__ import annotations

import re

# ------------------------------------------------------- 1. he says what for

# "LED strip for light to coffer (1 x11000mm...) in corner profile (X)" is the
# designer telling us plainly what the circuit is. The phrase after the
# preposition is the name.
#
# Directional words are kept because they carry meaning -- "Above Cupboards" is
# a different circuit from "Inside Cupboards" -- while "to" and "in" are
# positional noise and are dropped.
_KEEP = ("above", "under", "below", "behind", "inside", "within")
_PURPOSE = re.compile(
    r"""\bfor\s+ (?:light|lights|lighting)\s+
        (?P<where>to|in|inside|above|under|below|behind|within)\s+
        (?P<what>[^(,.\d]+)""", re.IGNORECASE | re.VERBOSE)

# "Supply for joinery lighting" -- a circuit he wants, with the fitting left to
# somebody else. It is a real zone and it is named exactly as he named it.
_SUPPLY_FOR = re.compile(r"^\s*supp(?:ly|lies)\s+for\s+(?P<what>.+?)\s*$",
                         re.IGNORECASE)

# ------------------------------------------------- 2. the fitting names itself

# Straight out of the designer's own legend and specification wording. This
# list is ordinary data and it will never be finished -- every practice writes
# its catalogue differently, and a phrase nobody has seen falls through to the
# product's own name rather than to a guess.
#
# Longest match wins, so "decorative bathroom wall light" is not read as a
# plain wall light.
_SELF_NAMING = [
    ("5 amp floor socket", "5A Floor Sockets"),
    ("5 amp socket", "5A Sockets"),
    ("5amp socket", "5A Sockets"),
    ("bedside reading light", "Bedside Reading Lights"),
    ("picture light", "Picture Lights"),
    ("bathroom wall light", "Bathroom Wall Lights"),
    ("wall light", "Wall Lights"),
    ("pendant", "Pendants"),
    ("chandelier", "Chandeliers"),
    ("mast light", "Mast Lights"),
    ("steplight", "Steplights"),
    ("step light", "Steplights"),
    ("floorwasher", "Floorwashers"),
    ("floor washer", "Floorwashers"),
    ("uplight", "Uplights"),
    ("downlight", "Downlights"),
    ("table lamp", "Table Lamps"),
    ("floor lamp", "Floor Lamps"),
    ("mirror light", "Mirror Lights"),
    ("shelf light", "Shelf Lights"),
    ("plinth light", "Plinth Lights"),
    ("bollard", "Bollards"),
    ("spike light", "Spike Lights"),
    ("strip light", "Cove"),
    ("led strip", "Cove"),
    ("led tape", "Cove"),
]

# What a product's own words say about how it is mounted, where the description
# carries no purpose at all. "Trimless", "Surface", "Adjustable" are the
# designer's words, not our reading of a picture.
_MOUNTING = [
    ("surface", "Surface Downlights"),
    ("adjustable", "Adjustable Downlights"),
    ("directional", "Adjustable Downlights"),
    ("trimless", "Ceiling Downlights"),
    ("darklight", "Ceiling Downlights"),
    ("recessed", "Ceiling Downlights"),
    ("trim", "Ceiling Downlights"),
]

# --------------------------------------------------------------- 3. the beam

_BEAM = re.compile(r"(\d{1,3})\s*(?:deg|degree|°)", re.IGNORECASE)
_HONEYCOMB = re.compile(r"/\s*hc\b|honeycomb", re.IGNORECASE)

_SMALL = {"in", "to", "on", "at", "of", "for", "the", "a", "an", "and", "with"}


def titled(text: str) -> str:
    """Title case that leaves small words alone and existing capitals intact."""
    words = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    out = []
    for i, word in enumerate(words):
        if any(c.isupper() for c in word[1:]):        # RTRA, IP, LED
            out.append(word)
        elif i and word.lower() in _SMALL:
            out.append(word.lower())
        else:
            out.append(word[:1].upper() + word[1:].lower())
    return " ".join(out)


def _purpose(description: str) -> str:
    """What the designer said the fitting is for, or ''."""
    supply = _SUPPLY_FOR.match(description)
    if supply:
        return titled(supply.group("what"))
    match = _PURPOSE.search(description)
    if not match:
        return ""
    where = match.group("where").lower()
    what = re.sub(r"\s+", " ", match.group("what")).strip(" -,")
    if not what:
        return ""
    if where in _KEEP:
        what = f"{where} {what}"
    return titled(what)


def _self_named(description: str) -> str:
    """The name the fitting gives itself, or ''."""
    low = description.lower()
    best = ""
    for phrase, name in _SELF_NAMING:
        if phrase in low and len(phrase) > len(best):
            best, chosen = phrase, name
    return chosen if best else ""


def _mounting(description: str) -> str:
    low = description.lower()
    for word, name in _MOUNTING:
        if word in low:
            return name
    return ""


def _beam(description: str) -> tuple:
    """(angles, honeycomb) the designer specified, as he wrote them.

    Repeating his own figure is a fact. Turning it into "Accent" would be a
    claim, and one an engineer would believe.
    """
    angles = {int(m.group(1)) for m in _BEAM.finditer(description)}
    return angles, bool(_HONEYCOMB.search(description))


def _beam_text(angles: set, honeycomb: bool) -> str:
    parts = "/".join(f"{a}°" for a in sorted(angles))
    return f"{parts} Honeycomb".strip() if honeycomb else parts


def _product(description: str) -> str:
    """The product's own name, with the beam and finish trimmed off."""
    text = re.sub(r"\s*[-,]?\s*\d{1,3}\s*(deg|degree|°).*$", "", description,
                  flags=re.IGNORECASE)
    text = re.sub(r"\s*\(.*?\)", "", text)
    text = re.sub(r"\s*/\s*hc\b", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -,") or description


def describe(description: str) -> tuple:
    """(what it is, its beam, which rule said so) for one fitting.

    The beam is kept apart from the noun so that a circuit carrying the same
    downlight at two beams can be named once -- "Ceiling Downlights 25°/50°"
    rather than the same words twice with an ampersand between them. Two beams
    of one product are one kind of fitting, and saying otherwise would flag
    half a specification as mixed when it is nothing of the sort.
    """
    purpose = _purpose(description)
    if purpose:
        return purpose, (set(), False), "purpose"
    beam = _beam(description)
    self_named = _self_named(description)
    if self_named:
        # A downlight is only a downlight until its beam separates it from the
        # other downlights in the same room.
        if self_named == "Downlights":
            return "Ceiling Downlights", beam, "beam" if beam[0] else "fitting"
        return self_named, (set(), False), "fitting"
    mounting = _mounting(description)
    if mounting:
        return mounting, beam, "beam" if beam[0] else "fitting"
    return _product(description), beam, "product"


def name_line(description: str) -> tuple:
    """(name, which rule named it) for one fitting, beam included."""
    base, beam, rule = describe(description)
    return f"{base} {_beam_text(*beam)}".strip(), rule


def name_circuit(circuit) -> tuple:
    """(name, rule, note) for a whole circuit.

    A circuit carrying two fittings is named for both when they are different
    things, because "Wall Lights" on a circuit that is half sockets is the
    confident-wrong kind of name this is all trying to avoid.
    """
    fittings = [ln for ln in circuit.lines if ln.is_fitting]
    if not fittings:
        return "", "", ""
    order = {"purpose": 0, "fitting": 1, "beam": 2, "product": 3}

    # One entry per kind of fitting, gathering every beam specified for it.
    groups: list = []
    for line in fittings:
        base, (angles, honeycomb), rule = describe(line.description)
        for group in groups:
            if group["base"].lower() == base.lower():
                group["angles"] |= angles
                group["honeycomb"] = group["honeycomb"] or honeycomb
                if order[rule] > order[group["rule"]]:
                    group["rule"] = rule
                break
        else:
            groups.append({"base": base, "angles": set(angles),
                           "honeycomb": honeycomb, "rule": rule})

    # "Bathroom Wall Lights" and "Wall Lights" are the same family written at
    # two levels of detail, not two kinds of fitting. The plainer name covers
    # both; the more specific one would be wrong about half the circuit.
    merged: list = []
    for group in groups:
        for other in merged:
            long, short = sorted((group["base"], other["base"]), key=len,
                                 reverse=True)
            if long.lower().endswith(short.lower()):
                other["base"] = short
                other["angles"] |= group["angles"]
                other["honeycomb"] = other["honeycomb"] or group["honeycomb"]
                if order[group["rule"]] > order[other["rule"]]:
                    other["rule"] = group["rule"]
                break
        else:
            merged.append(group)

    distinct = [(f"{g['base']} {_beam_text(g['angles'], g['honeycomb'])}".strip(),
                 g["rule"]) for g in merged]

    if len(distinct) == 1:
        return distinct[0][0], distinct[0][1], ""
    joined = " & ".join(name for name, _ in distinct[:2])
    if len(distinct) > 2:
        joined = f"{joined} & more"
    # The circuit is only as well named as its weakest line: a name half of
    # which was inferred from a beam is not a name read off the specification.
    weakest = max((rule for _, rule in distinct), key=lambda rule: order[rule])
    note = (f"This circuit carries {len(distinct)} different kinds of fitting. "
            f"Check the name says what an engineer needs to read.")
    return joined, weakest, note


def apply(spec) -> list:
    """Name every circuit in a specification, and say what still needs one.

    Two circuits in one room that end up with the same name are the residue:
    the fitting, the purpose and the beam are all identical, and only the
    drawing can separate them. They keep the honest name with a number after
    it and are reported -- exactly as a blank wattage is reported -- rather
    than being given a purpose nobody stated.
    """
    problems: list = []
    notes: dict = {}
    for circuit in spec.circuits:
        name, rule, note = name_circuit(circuit)
        circuit.zone_name, circuit.named_by = name, rule
        if note:
            notes[id(circuit)] = note

    by_room: dict = {}
    for circuit in spec.circuits:
        if circuit.zone_name:
            by_room.setdefault((circuit.room, circuit.zone_name.lower()), []).append(circuit)

    for (room, _), group in sorted(by_room.items(), key=lambda kv: kv[0]):
        if len(group) < 2:
            continue
        for i, circuit in enumerate(group, 1):
            circuit.zone_name = f"{circuit.zone_name} {i}"
            circuit.named_by = "unresolved"
        refs = ", ".join(c.ref or "(unnumbered)" for c in group)
        problems.append(
            f"{room}: circuits {refs} are the same fitting at the same beam, so "
            f"nothing in the specification tells them apart. They have been named "
            f"'{group[0].zone_name.rsplit(' ', 1)[0]}' with a number and need a "
            f"name that says what each is for.")

    for circuit in spec.circuits:
        note = notes.get(id(circuit))
        if note:
            problems.append(f"{circuit.room} {circuit.ref or '(unnumbered)'}: {note}")

    # Named after the product because the specification says nothing about what
    # it does. That is the honest answer and it builds, but it is not a name an
    # engineer reads anything from, so it belongs on the review sheet beside a
    # blank wattage rather than passing as resolved.
    unnamed = [c for c in spec.circuits if c.named_by == "product"]
    if unnamed:
        listed = ", ".join(f"{c.room} {c.ref or '(unnumbered)'} ('{c.zone_name}')"
                           for c in unnamed[:6])
        problems.append(
            f"{len(unnamed)} circuit(s) are named after the product on them because "
            f"the specification does not say what they light: {listed}"
            f"{'...' if len(unnamed) > 6 else ''}. They will build, but rename them "
            f"to something an engineer would recognise.")
    return problems


def summary(spec) -> dict:
    """How many circuits each rule named, for measuring whether this works."""
    counts: dict = {}
    for circuit in spec.circuits:
        counts[circuit.named_by] = counts.get(circuit.named_by, 0) + 1
    return counts
