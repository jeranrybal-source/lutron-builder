"""Compare the House B specification's counts against the plan's own labels.

RESEARCH, not part of the app. It needs `ezdwg`, which is not a dependency and
is not bundled, and it is here to answer one question: when a designer gives us
both a specification and a drawing, do the two agree about how many fittings
are on each circuit?

The specification states a quantity per circuit. The plan carries 118 circuit
labels -- `C1`, `C2/M`, `C2/W/HC`, `C1/W x2` -- placed at the fittings they
refer to. Where the two agree the circuit is solid. Where they disagree that is
worth an engineer's attention BEFORE he orders, and neither source is quietly
preferred over the other.

Two things this cannot do, stated up front because they bound what the numbers
below are worth:

*Rooms are matched by nearest label.* The drawing has a text label per room and
this attributes each circuit label to the closest one. Rooms are not circles,
so a label near a boundary can be attributed to the room next door. A
disagreement of one or two in adjacent rooms is more likely to be this than a
real error.

*Not every fitting is labelled.* The beam-coded labels annotate spotlights.
Sockets, pendants, wall lights and LED tape are drawn as symbols and mostly
carry no circuit label at all, so a circuit with no labels is not a circuit
with no fittings -- it is a circuit this method says nothing about.

    python3 tools/crosscheck_spec_vs_plan.py <spec.pdf> <plan.dwg>

Both paths are arguments: a drawing set lives in whoever's project folder it
belongs to, and a hard-coded one in here was a client's name in a filename.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwwriter import specpdf, zonename  # noqa: E402

SPEC = PLAN = ""     # given on the command line; see the docstring above

# `C2/W/HC x2` -- a circuit, the beams it was specified at, and how many
# fittings this one label stands for.
LABEL = re.compile(r"""^(?P<ref>C\d+)
                        (?P<beams>(?:\s*/\s*[A-Z]{1,2})*)
                        (?:\s*x\s*(?P<times>\d+))?$""", re.IGNORECASE | re.VERBOSE)

# The room labels the plan carries, some with a ceiling height attached.
ROOM_LABEL = re.compile(r"^(?P<room>[A-Z][A-Za-z /]+?)\s*(?:CH\s*:.*)?$")

# Anything in these is a legend, a title block or a revision note, not a plan.
NOT_A_ROOM = {"description", "revision", "drawn", "date", "legend", "symbol",
              "symbols", "annotations", "notes", "scale", "up", "dn"}


def clean(text: str) -> str:
    """MTEXT with AutoCAD's own formatting codes taken back out of it."""
    text = re.sub(r"\{?\\[A-Za-z][^;]*;", "", text or "")
    text = text.replace("{", "").replace("}", "").replace("\\P", " ")
    return re.sub(r"\s+", " ", text).strip()


def readable(text: str) -> bool:
    """ezdwg returns a share of strings as mojibake; those are not usable.

    Filtering rather than trusting is the whole point -- a name that parsed is
    not a name that is right.
    """
    return bool(text) and all(ord(c) < 300 for c in text)


def plan_text(path: str) -> list:
    import ezdwg.raw as raw
    out = []
    for entity in raw.decode_mtext_entities(path) + raw.decode_text_entities(path):
        body = clean(entity[1]) if isinstance(entity[1], str) else ""
        point = entity[2] if isinstance(entity[2], tuple) else None
        if readable(body) and point:
            out.append((body, float(point[0]), float(point[1])))
    return out


def _matches_room(name: str, room: str) -> bool:
    """Is this label the name of that room, rather than a sentence about it?

    Containment either way is far too loose. "TO LIGHT GLAZED KITCHEN CUPBOARD"
    contains "KITCHEN" and is an annotation sitting in a panel at the edge of
    the sheet -- taken as a room label it drags a dozen circuit labels across
    the drawing to a place no room is.
    """
    if name == room:
        return True
    # "STAIRS TO BASEMENT" and "TV ROOM" are both labels for the one room the
    # specification calls "STAIRS TO BASEMENT/ TV ROOM".
    parts = [p.strip() for p in room.split("/")]
    return any(name == p or p.startswith(name) or name.startswith(p)
               for p in parts if len(p) >= 4)


def rooms_on_plan(texts: list, wanted: list) -> list:
    """(name, x, y) for each room label, matched to the specification's rooms."""
    found = []
    for body, x, y in texts:
        match = ROOM_LABEL.match(body)
        if not match:
            continue
        name = match.group("room").strip().upper()
        if not name or name.lower() in NOT_A_ROOM or len(name) < 4:
            continue
        for room in wanted:
            if _matches_room(name, room):
                found.append((room, x, y))
                break
    return found


# A label is only attributed to a room when that room's label is clearly the
# closest. Rooms are not circles and their labels sit wherever they fit, so a
# fitting midway between two of them cannot honestly be given to either.
MARGIN = 1.2


def nearest(rooms: list, x: float, y: float):
    """(room, whether the answer is unambiguous)."""
    distances = sorted(((rx - x) ** 2 + (ry - y) ** 2, room)
                       for room, rx, ry in rooms)
    if not distances:
        return None, False
    best_d, best = distances[0]
    for d, room in distances[1:]:
        if room != best:
            return best, d >= best_d * (MARGIN ** 2)
    return best, True


def main() -> int:
    spec_path = sys.argv[1] if len(sys.argv) > 1 else SPEC
    plan_path = sys.argv[2] if len(sys.argv) > 2 else PLAN
    if not spec_path or not plan_path:
        sys.exit("Usage: python3 tools/crosscheck_spec_vs_plan.py "
                 "<spec.pdf> <plan.dwg>")
    for path in (spec_path, plan_path):
        if not os.path.exists(path):
            print(f"Not found: {path}")
            return 2

    spec = specpdf.read(spec_path)
    zonename.apply(spec)
    wanted = sorted({c.room for c in spec.circuits})

    texts = plan_text(plan_path)
    rooms = rooms_on_plan(texts, wanted)
    print(f"specification: {len(spec.circuits)} circuits in {len(wanted)} rooms")
    print(f"plan: {len(texts)} readable text objects, "
          f"{len(rooms)} room labels matched to the specification")
    missing = [r for r in wanted if r not in {name for name, _, _ in rooms}]
    if missing:
        print(f"  rooms with NO label on the plan: {', '.join(missing)}")
    print()

    # ---- the labels, attributed to a room and counted
    drawn: dict = {}
    unattributed = 0
    doubtful: dict = {}
    for body, x, y in texts:
        match = LABEL.match(body)
        if not match:
            continue
        room, sure = nearest(rooms, x, y)
        if room is None:
            unattributed += 1
            continue
        if not sure:
            # Still counted, because leaving it out would understate the room
            # just as badly -- but the room's whole comparison is then only as
            # good as a coin toss between it and its neighbour, and says so.
            doubtful[room] = doubtful.get(room, 0) + 1
        ref = match.group("ref").upper()
        times = int(match.group("times") or 1)
        entry = drawn.setdefault((room, ref), {"labels": 0, "fittings": 0, "beams": set()})
        entry["labels"] += 1
        entry["fittings"] += times
        for beam in re.findall(r"[A-Z]{1,2}", match.group("beams") or ""):
            entry["beams"].add(beam.upper())

    total_labels = sum(e["labels"] for e in drawn.values())
    print(f"plan: {total_labels} circuit labels standing for "
          f"{sum(e['fittings'] for e in drawn.values())} fittings\n")

    # ---- the comparison
    header = f"{'ROOM':28} {'CKT':6} {'SPEC':>5} {'PLAN':>5}  {'BEAMS':10} VERDICT"
    print(header)
    print("-" * len(header))
    agree = differ = silent = uncertain = 0
    for circuit in spec.circuits:
        key = (circuit.room, (circuit.ref or "").upper())
        entry = drawn.get(key)
        qty = circuit.quantity
        spec_n = "?" if qty is None else str(int(qty))
        if circuit.room in doubtful:
            uncertain += 1
            print(f"{circuit.room[:28]:28} {(circuit.ref or '-'):6} {spec_n:>5} "
                  f"{entry['fittings'] if entry else '-':>5}  {'':10} "
                  f"attribution uncertain ({doubtful[circuit.room]} of this room's "
                  f"labels sit as near the room next door)")
            continue
        if entry is None:
            silent += 1
            verdict, plan_n, beams = "not labelled on the plan", "-", ""
        else:
            plan_n = str(entry["fittings"])
            beams = "/".join(sorted(entry["beams"]))
            if qty is not None and entry["fittings"] == qty:
                agree += 1
                verdict = "agree"
            else:
                differ += 1
                verdict = "DISAGREE"
        print(f"{circuit.room[:28]:28} {(circuit.ref or '-'):6} {spec_n:>5} "
              f"{plan_n:>5}  {beams:10} {verdict}")

    print()
    print(f"agree: {agree}    disagree: {differ}    "
          f"no label on the plan: {silent}    attribution uncertain: {uncertain}"
          f"    of {len(spec.circuits)} circuits")
    if doubtful:
        print("Rooms whose labels cannot be attributed with confidence: "
              + ", ".join(f"{r} ({n})" for r, n in sorted(doubtful.items())))
    if unattributed:
        print(f"{unattributed} label(s) could not be attributed to any room.")

    # ---- labels the specification has no circuit for
    spec_keys = {(c.room, (c.ref or "").upper()) for c in spec.circuits}
    orphans = sorted(k for k in drawn if k not in spec_keys)
    if orphans:
        print(f"\n{len(orphans)} labelled circuit(s) the specification does not list "
              f"for that room:")
        for room, ref in orphans:
            print(f"   {room:28} {ref:6} {drawn[(room, ref)]['fittings']} fitting(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
