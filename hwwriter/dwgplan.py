"""Read a lighting plan straight out of an AutoCAD drawing.

**This is the experimental way in and it is the weakest of the three.** It is
here because a DWG is often the only thing a job has, and reading the rooms and
the circuit counts off it beats typing them. It is not here because it is
trustworthy, and the screen it feeds says so.

What a drawing gives up reliably, measured on House B (the lighting designer):

- **The circuit labels.** `C1`, `C2/M`, `C2/W/HC`, `C1/W x2` -- a circuit, the
  beams it was specified at, and sometimes a multiplier. 118 of them, exactly.
- **The room labels**, where the designer wrote them.
- **The annotation panel**, which ties a note to a room and a circuit. Four on
  House B, and every one agreed with the specification.

What it does NOT give up, and this is the reason for the warnings:

- **Which room a fitting is in.** Labels are attributed to the nearest room
  label because a drawing has no room boundaries to read. Rooms are not
  circles, and on House B **9 rooms out of 10** have at least one label
  sitting as close to the room next door as to their own. Every room reports
  whether its own count can be trusted, and most of the time it cannot.
- **What the fitting is, or what it draws.** No fitting name, no wattage. Those
  columns come out blank, are flagged, and have to be filled in.
- **Roughly a quarter of the text.** It comes back as mojibake -- 216 of 895
  text objects on House B. Anything unreadable is dropped rather than
  guessed at, so a room whose label did not survive loses its circuits.

So the output is a starting point that saves typing, not a schedule. It is
turned into the same rows a spreadsheet would produce and goes through the same
mapping screen, so every column is shown and can be corrected before a single
row is written -- which is the only reason shipping something this rough is
defensible at all.
"""

from __future__ import annotations

import re
import sys

# `C2/W/HC x2` -- a circuit, its beams, and how many fittings the label is for.
LABEL = re.compile(r"""^(?P<ref>C\d{1,2})
                        (?P<beams>(?:\s*/\s*[A-Z]{1,2})*)
                        (?:\s*x\s*(?P<times>\d+))?$""", re.IGNORECASE | re.VERBOSE)

ROOM_LABEL = re.compile(r"^(?P<room>[A-Z][A-Za-z /'&-]{3,40}?)\s*(?:CH\s*:.*)?$")

# Headings on a title block, a legend or a revision table read exactly like
# room names and are not rooms. The legend's own mounting categories are the
# worst of them: "CEILING RECESSED" is a perfectly good room name in every
# respect except being one.
NOT_A_ROOM = {
    "description", "revision", "revisions", "drawn", "date", "legend", "symbol",
    "symbols", "annotations", "annotation", "notes", "note", "scale", "client",
    "project", "drawing", "title", "sheet", "issue", "status", "checked",
    "approved", "north", "key", "detail", "details", "general", "specification",
    "ceiling", "floor", "wall", "existing", "proposed", "demolition", "name",
    "number", "ceiling recessed", "ceiling directional", "wall recessed",
    "floor recessed", "surface mounted", "wall mounted", "concealed",
    "decorative", "sockets/control", "sockets / control", "dimmer switch",
    "locally switched", "dropped ceiling", "higher ceiling", "beamwidths/lenses",
    "driver located remotely", "project name", "project number",
}

# A phrase that starts like this is a note about the lighting, not a room.
NOT_A_ROOM_START = re.compile(
    r"^(to|for|in|on|strip|this|all|see|please|typical|locally|dropped|higher|"
    r"decorative\b.*sourced)\b", re.IGNORECASE)

# Words that only ever appear on a title block or a sheet name.
NOT_A_ROOM_ANY = re.compile(
    r"\b(lighting plan|floor plan|not to scale|project|scale|drawn by|"
    r"revision|client name|drawing title)\b", re.IGNORECASE)

# These name a storey, and the drawing uses them as banners over each plan.
# They are worth keeping -- as the floor, not as a room.
FLOOR_NAME = re.compile(
    r"""^((lower|upper)\s+ground(\s+floor)?|ground(\s+floor)?|basement
        |(first|second|third|1st|2nd|3rd)\s+floor|mezzanine|attic|loft)$""",
    re.IGNORECASE | re.VERBOSE)

# How far a room label may sit from a circuit label and still be believable as
# a room. A legend heading or a name in the title block has no circuits
# anywhere near it, and that is what separates them without a word list.
NEAR = 12000.0

# The room label has to be clearly the closest one for its circuits to be
# counted with any confidence.
MARGIN = 1.5

MIN_PYTHON = (3, 10)


class PlanError(Exception):
    """The drawing cannot be read, and why."""


def available() -> bool:
    """Can this machine read a DWG at all?"""
    if sys.version_info < MIN_PYTHON:
        return False
    try:
        import ezdwg.raw  # noqa: F401
    except ImportError:
        return False
    return True


def _require():
    if sys.version_info < MIN_PYTHON:
        # The whole tool wants 3.10 now and says so at startup, so this is
        # belt and braces rather than the first line of defence.
        raise PlanError(
            f"Reading a drawing needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or "
            f"newer, and this is running on "
            f"{sys.version_info[0]}.{sys.version_info[1]}. Run it with a newer "
            f"Python, or use the packaged Lutron Builder.exe.")
    try:
        import ezdwg.raw as raw
    except ImportError as exc:
        raise PlanError(
            "Reading a drawing needs the ezdwg library: pip install ezdwg\n"
            "(Only the drawing option needs it; nothing else does.)") from exc
    return raw


def clean(text: str) -> str:
    """MTEXT with AutoCAD's own formatting codes taken back out of it."""
    text = re.sub(r"\{?\\[A-Za-z][^;]*;", "", text or "")
    text = text.replace("{", "").replace("}", "").replace("\\P", " ")
    return re.sub(r"\s+", " ", text).strip()


def readable(text: str) -> bool:
    """Did this string survive being decoded?

    A large share of a DWG's text comes back as mojibake, and the failure is
    silent: the string parses, it is simply not the string that was written.
    Dropping what cannot be read is the only safe answer, because a room called
    an unreadable name is worse than a room that is missing.
    """
    return bool(text) and all(ord(c) < 300 for c in text)


def texts(path: str) -> list:
    """(text, x, y) for every readable piece of text in the drawing."""
    raw = _require()
    try:
        entities = raw.decode_mtext_entities(path) + raw.decode_text_entities(path)
    except Exception as exc:                      # noqa: BLE001 - surfaced below
        raise PlanError(
            f"That drawing could not be read ({exc}). It may be a version this "
            f"cannot open, or the file may be damaged.") from exc
    out = []
    for entity in entities:
        body = clean(entity[1]) if isinstance(entity[1], str) else ""
        point = entity[2] if isinstance(entity[2], tuple) else None
        if readable(body) and point:
            out.append((body, float(point[0]), float(point[1])))
    return out


def rooms_in(found: list, labels: list = None) -> tuple:
    """(rooms, floors), each as (name, x, y).

    A drawing does not mark which of its words are room names, so this is a
    sieve rather than a lookup. The last stage of it is the useful one: a
    candidate with no circuit label anywhere near it is a legend heading, a
    name in the title block or a sheet title, whatever it happens to read like.
    That test needs no word list and does not care what a practice calls things.
    """
    rooms, floors = [], []
    for body, x, y in found:
        match = ROOM_LABEL.match(body)
        if not match:
            continue
        name = match.group("room").strip()
        if LABEL.match(name) or name.lower() in NOT_A_ROOM:
            continue
        if NOT_A_ROOM_START.match(name) or NOT_A_ROOM_ANY.search(name):
            continue
        # A room label is a name, not a sentence.
        if len(name.split()) > 4:
            continue
        if FLOOR_NAME.match(name):
            floors.append((name.upper(), x, y))
        else:
            rooms.append((name.upper(), x, y))

    if labels:
        rooms = [(name, x, y) for name, x, y in rooms
                 if any((lx - x) ** 2 + (ly - y) ** 2 <= NEAR ** 2
                        for _, lx, ly in labels)]
    return rooms, floors


def nearest(rooms: list, x: float, y: float):
    """(room, whether that answer is unambiguous)."""
    distances = sorted(((rx - x) ** 2 + (ry - y) ** 2, room)
                       for room, rx, ry in rooms)
    if not distances:
        return None, False
    best_distance, best = distances[0]
    for distance, room in distances[1:]:
        if room != best:
            return best, distance >= best_distance * (MARGIN ** 2)
    return best, True


def annotations(found: list, rooms: list) -> dict:
    """{(room, circuit): the designer's note} from the annotation panel.

    He writes the note and puts the room and circuit it belongs to directly
    beneath it. Where one exists it is the best name a circuit will ever get,
    because it is what he meant rather than what we inferred.
    """
    out = {}
    references = [(body, x, y) for body, x, y in found
                  if re.match(r"^[A-Z][A-Za-z /'&-]+\s+C\d{1,2}$", body)]
    for body, x, y in references:
        room, ref = body.rsplit(" ", 1)
        above = [(ny, note) for note, nx, ny in found
                 if abs(nx - x) < 400 and 0 < ny - y < 600
                 and not re.match(r"^[A-Z][A-Za-z /'&-]+\s+C\d{1,2}$", note)]
        if above:
            out[(room.strip().upper(), ref.upper())] = min(above)[1]
    return out


def read(path: str) -> dict:
    """Everything the drawing gives up, with its own confidence attached."""
    found = texts(path)
    labels = [(match, x, y) for body, x, y in found
              if (match := LABEL.match(body))]
    rooms, floors = rooms_in(found, labels)
    if not rooms:
        raise PlanError(
            "No room names could be read from that drawing. Either it does not "
            "carry them -- they are often in a separate architectural file -- or "
            "its text did not decode. Use the spreadsheet or the lighting plans.")

    notes = annotations(found, rooms)
    circuits: dict = {}
    doubtful: dict = {}
    room_floor: dict = {}
    placed = 0
    for match, x, y in labels:
        room, sure = nearest(rooms, x, y)
        if room is None:
            continue
        placed += 1
        if not sure:
            doubtful[room] = doubtful.get(room, 0) + 1
        ref = match.group("ref").upper()
        entry = circuits.setdefault((room, ref), {"count": 0, "beams": set()})
        entry["count"] += int(match.group("times") or 1)
        for beam in re.findall(r"[A-Z]{1,2}", match.group("beams") or ""):
            entry["beams"].add(beam.upper())

    # Each plan sits under a banner naming its storey, so the nearest banner to
    # a room is the floor that room is on.
    for name, x, y in rooms:
        floor, _ = nearest(floors, x, y)
        if floor:
            room_floor.setdefault(name, floor)

    if not circuits:
        raise PlanError(
            "No circuit labels could be read from that drawing. A preliminary "
            "layout usually has none -- the circuits have not been decided yet. "
            "There is nothing here to build a schedule from.")

    return {"rooms": sorted({r for r, _, _ in rooms}), "circuits": circuits,
            "annotations": notes, "doubtful": doubtful, "labels": placed,
            "floors": room_floor}


HEADINGS = ["Floor", "Room", "Circuit description", "Circuit number",
            "Fitting type", "Fitting description", "Manufacturer", "Model",
            "Quantity", "Wattage", "Watts per metre", "Run length (m)",
            "Dimming type", "Notes"]


def _sort_key(item):
    (room, ref), _ = item
    digits = re.sub(r"\D", "", ref)
    return room, int(digits) if digits else 0


def to_rows(plan: dict) -> list:
    """The drawing as the rows a spreadsheet would have had.

    Deliberately the same shape the spreadsheet route produces, so this goes
    through the same mapping screen, the same conversion and the same review as
    every other way in. A second, parallel import path would be a second place
    for the counts to go wrong.
    """
    rows = [list(HEADINGS)]
    for (room, ref), entry in sorted(plan["circuits"].items(), key=_sort_key):
        note = plan["annotations"].get((room, ref), "")
        beams = "/".join(sorted(entry["beams"]))
        # The drawing does not say what a circuit is for. Where the designer
        # annotated it he does; otherwise this has to be named by a person, and
        # it says so in the cell rather than inventing something plausible.
        description = note.title() if note else f"Circuit {ref} -- needs a name"
        remarks = [f"Read from the drawing; {entry['count']} label(s)"]
        if beams:
            remarks.append(f"beams {beams}")
        if room in plan["doubtful"]:
            remarks.append("CHECK THE ROOM -- some labels sit as near the room "
                           "next door")
        rows.append([plan.get("floors", {}).get(room, ""), room, description,
                     ref, "", "", "", "", str(entry["count"]), "", "", "", "",
                     "; ".join(remarks)])
    return rows


def report(plan: dict) -> list:
    """What an engineer has to be told before he trusts any of this."""
    out = [f"Read {plan['labels']} circuit label(s) across "
           f"{len(plan['circuits'])} circuit(s) in {len(plan['rooms'])} room(s)."]
    if plan["annotations"]:
        out.append(f"{len(plan['annotations'])} circuit(s) carried the designer's "
                   f"own note on the drawing, and those are named from it.")
    named = sum(1 for key in plan["circuits"] if key not in plan["annotations"])
    if named:
        out.append(f"{named} circuit(s) have no name on the drawing at all. They "
                   f"read 'needs a name' and must be renamed to something an "
                   f"engineer would recognise before you build.")
    if plan["doubtful"]:
        rooms = ", ".join(f"{room} ({n})" for room, n in sorted(plan["doubtful"].items()))
        out.append(f"A drawing has no room boundaries, so each label is given to "
                   f"the nearest room name. In {len(plan['doubtful'])} room(s) some "
                   f"labels sit as close to the room next door, so those counts "
                   f"may be split wrongly between neighbours: {rooms}. Check them "
                   f"against the drawing.")
    out.append("No fitting and no wattage can be read from a drawing, so every "
               "circuit here has neither. Fill them in, or import the designer's "
               "schedule instead -- circuit loading and panel sizing do not work "
               "without them.")
    return out
