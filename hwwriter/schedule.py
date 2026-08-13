"""
The engineering schedule: the CSVs in, one validated object graph out.

The CSVs use human-readable string keys (area names, zone names, module labels)
rather than database IDs, so the same files are reviewable as ordinary
engineering schedules and can be produced by any CAD or lighting-design tool.
Joins between files are by those names.

Validation runs to completion and reports *every* problem at once.  Stopping at
the first bad row would mean an engineer fixes one typo, re-runs, waits, and
finds the next -- which is how a ten-minute correction becomes an afternoon.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field

# Fade time is stored as a Lutron-internal enum, not seconds.  Only 2s has been
# confirmed against Designer output; anything else is a guess, so we say so
# rather than silently writing a fade the engineer did not ask for.
FADE_RAW_CONFIRMED = {0: 0, 2: 8}
DEFAULT_FADE_SECONDS = 2

# tblPresetAssignment command shapes, from the observed Designer rows.
CMD_SET_LEVEL = (2, 1)        # zone -> absolute level
CMD_UNAFFECTED = (1, 1)       # zone -> leave alone in this scene
CMD_RECALL_SCENE = (5, 3)     # area -> recall scene number

OBJ_AREA = 2
OBJ_ZONE = 15

PARAM_FADE = 1
PARAM_DELAY = 2
PARAM_LEVEL = 3
PARAM_SCENE_NUMBER = 7

# What one output of a four-channel Lutron phase module will drive (D9). The
# first takes more than the other three, and this writer deliberately does not
# assign outputs, so a circuit is grouped to the figure that fits ANY of them
# and one between the two is passed on as "first output only" (D10). Neither
# applies to DALI or switched circuits, which have no per-zone ceiling.
PHASE_OUTPUT_W = 500
PHASE_FIRST_OUTPUT_W = 800

# Warnings the review sheet shows in its own "what the drawings did not say"
# panel, matched on a phrase from the message itself. They stay in `warnings`
# because the command line still prints them; the sheet just must not say the
# same thing twice, once in a red box and once in a list of two hundred.
UNREAD_WARNING_MARKERS = ("have no fixture type", "no RunLength_m")


class ValidationError(Exception):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s) in the schedule")


@dataclass
class Area:
    name: str
    parent: str          # floor name, must exist in the shell
    notes: str = ""
    # How many circuits a supplied document says this room has -- a circuit
    # estimate, a switching schedule, a panel schedule. It is the designer
    # counting his own scheme, so it is the best check there is on whether the
    # read found the whole room: the designer's estimate for House A states
    # 521 circuits and the read produced 194, matching in 3 rooms of 47, and
    # nothing anywhere said so.
    stated_circuits: int | None = None
    # How every other file refers to this room. Normally the room name; where
    # a house has the same room name on two floors -- a WC on three of them is
    # ordinary -- it becomes "Floor > Room" so the two stay distinct without
    # anyone inventing a suffix the drawings do not carry. `name` is what
    # Designer shows; `uid` is only ever a key.
    uid: str = ""

    def __post_init__(self):
        if not self.uid:
            self.uid = self.name


@dataclass
class Fixture:
    ref: str
    description: str
    load_type_id: int
    wattage: float
    lamp_quantity: int
    lamp_wattage: float
    low_end: int
    high_end: int
    phase_control: int
    dimming_range: int = 0
    manufacturer_name: str = ""
    manufacturer_model: str = ""
    # Where the electrical data came from: "drawings", "spec sheet", or a URL.
    # Wattage drives circuit loading, so a researched value must be traceable.
    data_source: str = ""
    notes: str = ""
    # Linear product -- tape, profile, cove -- is specified per metre, and the
    # circuit carries a run length instead of a count. Zero means "not linear".
    watts_per_metre: float = 0
    # Field names on THIS row that were blank and have been filled with a
    # default. The review sheet marks them; nothing may present one as read.
    assumed: set = field(default_factory=set)


@dataclass
class Load:
    area: str
    zone: str
    fixture_ref: str
    count: int
    zone_number: int | None = None
    load_type_override: int | None = None
    low_end_override: int | None = None
    high_end_override: int | None = None
    notes: str = ""
    # Metres of linear product on this circuit, where the fitting is priced per
    # metre. Fractional: 3.4 m of tape is an ordinary run.
    run_length_m: float | None = None
    # Other rooms this one circuit also lights -- stairs, landings, a corridor,
    # an open-plan space. It is still ONE circuit and is counted once; these
    # rooms exist so the sheet does not report them as having no light.
    additional_areas: list = field(default_factory=list)
    assumed: set = field(default_factory=set)
    # The designer's own circuit reference ("C1", "002B") where ZoneNumber was
    # not a plain whole number. Kept so the review can SHOW it -- a reference
    # that is kept but never shown is indistinguishable from one that was
    # ignored (James, 2026-08-12).
    zone_ref: str = ""

    @property
    def unresolved(self) -> bool:
        """No fixture was read for this circuit, so it cannot be built.

        Distinct from a fixture ref that names something absent from the
        catalogue -- that is a typo and stays a problem. A BLANK ref is the
        extraction saying honestly that it could not tell, and one of those
        must not take the other 193 circuits down with it.
        """
        return not (self.fixture_ref or "").strip()


@dataclass
class Module:
    name: str
    model_info_id: int
    model_label: str
    area: str
    link: str
    address: int | None
    output_count: int | None = None
    serial: str = ""


@dataclass
class OutputAssignment:
    area: str
    zone: str
    module: str
    output: int


@dataclass
class Keypad:
    name: str
    area: str
    station: str
    device: str
    model_info_id: int
    model_label: str
    link: str
    address: int | None
    serial: str = ""
    notes: str = ""


@dataclass
class ButtonAction:
    action_type: str
    target_area: str = ""
    target_scene: str = ""
    target_zone: str = ""
    level: int | None = None
    fade: int = DEFAULT_FADE_SECONDS
    delay: int = 0


@dataclass
class Button:
    keypad: str
    number: int              # 1-based index into the model's engraved buttons
    label: str
    # `label` IS the engraving text -- it is written through to the button's
    # engraving in Designer and is what gets etched on a real faceplate. So the
    # row's Notes matter: an extraction that PROPOSED the button function also
    # proposed the engraving, and engraved faceplates are made to order and
    # non-returnable. The review sheet says so.
    notes: str = ""
    actions: list[ButtonAction] = field(default_factory=list)

    @property
    def label_proposed(self) -> bool:
        return "propos" in self.notes.lower()


@dataclass
class SceneLevel:
    zone: str
    command: str             # SetLevel | Unaffected
    level: int | None
    fade: int = DEFAULT_FADE_SECONDS
    delay: int = 0
    notes: str = ""
    # Did the row actually carry a level? A blank one used to arrive as 0,
    # which is not "we could not tell" -- it is "switch this circuit off",
    # asserted, and built that way.
    level_stated: bool = True


@dataclass
class Scene:
    area: str
    name: str
    number: int
    levels: list[SceneLevel] = field(default_factory=list)

    @property
    def notes(self) -> str:
        """The scene's own note: the first thing any of its rows had to say.

        Scenes.csv is one row per circuit, so a note about the scene as a whole
        ("levels proposed, not read") arrives repeated on every row.
        """
        for lv in self.levels:
            if lv.notes:
                return lv.notes
        return ""


# What a scene row may ask a circuit to do. Deliberately the set the BUILDER
# genuinely implements, not the one INSTRUCTIONS.md advertises: it also lists
# DMXColor and Spectrum, and build.py implements neither -- it treats
# everything except Unaffected as an ordinary brightness, so a DMX colour
# preset index of 2 would silently be built as 2%.
SCENE_COMMANDS = {"setlevel", "zonelevel", "unaffected"}
SCENE_COMMANDS_SHOWN = ("SetLevel", "ZoneLevel", "Unaffected")
SCENE_COMMANDS_UNIMPLEMENTED = {"dmxcolor", "spectrum"}

# Ways of writing "set this circuit to a brightness" that mean exactly that and
# nothing else. Measured across six real reads on 2026-08-07: Opus writes
# `Level`, Sonnet writes `GotoLevel`/`GoToLevel`, Fable writes `SetLevel` --
# and the real House A job on disk carries `Level` on all 436 of its
# scene rows. Refusing them produced hundreds of "problems" that were nothing
# but a vocabulary mismatch, on schedules that were otherwise fine.
#
# This is an explicit allowlist, NOT "anything unrecognised is a level". The
# round-2 defect stands: everything except Unaffected is built as a brightness,
# so a misspelt `Unaffectd` must still be refused rather than switching on a
# circuit the scene meant to leave alone. Every word here is unmistakably a
# level and none is one keystroke from "Unaffected".
SCENE_COMMAND_SYNONYMS = {"level", "gotolevel", "setlevelpct", "zonelevelpct"}


def normalise_command(value: str) -> str:
    """Fold a CommandType to its canonical form: `GoTo Level` -> `gotolevel`."""
    return re.sub(r"[^a-z0-9]", "", (value or "").strip().lower())


@dataclass
class Schedule:
    areas: list[Area] = field(default_factory=list)
    fixtures: list[Fixture] = field(default_factory=list)
    loads: list[Load] = field(default_factory=list)
    modules: list[Module] = field(default_factory=list)
    outputs: list[OutputAssignment] = field(default_factory=list)
    keypads: list[Keypad] = field(default_factory=list)
    buttons: list[Button] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Recorded during reading, reported by validate(): duplicate scene numbers
    # collapse as the file is read, so nothing downstream can see them.
    merged_scenes: list[str] = field(default_factory=list)
    # Room names another file used that belong to more than one floor. Recorded
    # as the files are read, because that is where the reference is seen.
    ambiguous_areas: list[str] = field(default_factory=list)
    # Where the schedule came from -- "plans", "spreadsheet" or "drawing",
    # read from the provenance file the extraction leaves beside the CSVs.
    # None for a folder with no record. The review reads it: a load schedule
    # CANNOT carry keypads or scenes, and asking "did you forget them?" about
    # a source that never had them is a warning firing on correct input
    # (James, 2026-08-12 -- "it makes it look like we forgot keypads").
    source: str | None = None

    def area(self, uid: str) -> Area | None:
        return next((a for a in self.areas if a.uid == uid), None)

    def area_name(self, uid: str) -> str:
        """The room's own name for a key that may carry its floor."""
        a = self.area(uid)
        return a.name if a else uid


# ------------------------------------------------------------------ reading

def _rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        out = []
        for row in csv.DictReader(fh):
            clean = {}
            for k, v in row.items():
                if k is None:
                    # A row with more fields than headers -- an unquoted comma in
                    # a Notes column. Keep the data rather than crashing on it.
                    continue
                if isinstance(v, list):
                    v = ",".join(x or "" for x in v)
                clean[k.strip()] = (v or "").strip()
            out.append(clean)
        return out


def _new_rows(path: str) -> list[dict]:
    """Only rows marked NEW. EXISTING rows describe what the shell already has."""
    out = []
    for row in _rows(path):
        action = row.get("Action", "NEW").upper().strip()
        if action in ("", "NEW"):
            out.append(row)
        elif action != "EXISTING":
            # A typo ("NEWW") used to drop the row silently -- a circuit or a
            # keypad simply absent from the finished project, with nothing said.
            raise ValueError(
                f"{os.path.basename(path)}: Action='{row.get('Action')}' is not "
                f"understood. Use NEW (write it) or EXISTING (already in the "
                f"shell). A misspelt Action would silently omit this row.")
    return out


def _int(row: dict, key: str, default=None):
    v = row.get(key, "")
    if v in ("", None):
        return default
    try:
        num = float(v)
    except ValueError as exc:
        raise ValueError(f"{key}='{v}' is not a number") from exc
    if num != int(num):
        # Silently truncating 1.9 to 1 loses a fixture, a button or a scene.
        raise ValueError(f"{key}='{v}' must be a whole number, not a fraction.")
    return int(num)


def half_up(value) -> int:
    """Round a wattage the way an engineer expects, and ONE definition of it.

    Designer stores wattage as an integer, so something has to round. Python's
    round() is ties-to-even: 0.5 -> 0 and 2.5 -> 2, so a genuine half-watt
    fitting becomes "no wattage" in the built file. Half-up instead.

    The review sheet and the builder both call this, because the whole point is
    that the sheet states the number the file will actually carry.
    """
    from decimal import ROUND_HALF_UP, Decimal
    return int(Decimal(str(float(value or 0))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _num(row: dict, key: str, default=None):
    """A quantity that is genuinely allowed to be fractional.

    _int refuses fractions because truncating 1.9 loses a fixture, a button or
    a scene -- but that reasoning does not apply to a WATTAGE. LED tape is
    specified per metre and 9.6 W/m is an ordinary figure. Applying the whole-
    number rule to it made a real paid extraction unloadable: the House A
    Manor read of 2026-08-04 carries 'Contour HD27 linear LED 9.6W per metre'
    and could not be opened at all afterwards. Wattage is only ever displayed
    and totalled, never written into the .hw as an integer.
    """
    v = row.get(key, "")
    if v in ("", None):
        return default
    try:
        num = float(v)
    except ValueError as exc:
        raise ValueError(f"{key}='{v}' is not a number") from exc
    return int(num) if num == int(num) else num


_WRITTEN_RANGE = re.compile(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*%?\s*$")


def _dimming(row: dict) -> tuple[int, int, int, bool]:
    """`DimmingRange`, `LowEnd_pct`, `HighEnd_pct` — tolerating a written range.

    The fourth value says whether the trims were DEFAULTED rather than read.
    5% / 90% is the right guess for mains-dimmed LED and the wrong one for a
    fitting the designer trimmed deliberately, and the sheet has to be able to
    tell the engineer which of the two he is looking at.

    `DimmingRange` is Designer's own numeric field (0 = default); the actual
    percentages belong in `LowEnd_pct` / `HighEnd_pct`. An extraction reading
    the column name literally writes the human range there instead ("5-90"),
    duplicating the two columns beside it. Throwing an entire drawing set away
    over a redundant cell is the wrong trade, so take the range as the low/high
    when those are blank and leave `DimmingRange` at its default. A range that
    *contradicts* an explicit low/high is a real conflict and still raises.
    """
    raw = (row.get("DimmingRange") or "").strip()
    m = _WRITTEN_RANGE.match(raw)
    if not m:
        lo, hi = _int(row, "LowEnd_pct"), _int(row, "HighEnd_pct")
        return (_int(row, "DimmingRange", 0),
                5 if lo is None else lo, 90 if hi is None else hi,
                lo is None or hi is None)
    lo_r, hi_r = int(m.group(1)), int(m.group(2))
    lo, hi = _int(row, "LowEnd_pct"), _int(row, "HighEnd_pct")
    if (lo is not None and lo != lo_r) or (hi is not None and hi != hi_r):
        raise ValueError(
            f"DimmingRange='{raw}' disagrees with LowEnd_pct='{row.get('LowEnd_pct')}' / "
            f"HighEnd_pct='{row.get('HighEnd_pct')}'. Put the percentages in "
            f"LowEnd_pct/HighEnd_pct and leave DimmingRange blank.")
    return 0, lo if lo is not None else lo_r, hi if hi is not None else hi_r, False


# What a fitting is driven by when the drawings do not say (James, 2026-08-08).
#
# DALI, not phase. Virtually every architectural fitting made today is available
# with a DALI driver and that is the house preference; mains reverse-phase is
# for things that take a LAMP -- table and floor lamps, decorative pendants,
# chandeliers, 5A socket circuits. Defaulting everything to reverse phase was
# wrong on most fittings of most jobs, and wrong in the expensive direction: it
# puts an architectural circuit on a phase dimmer that then needs rewiring.
#
# Either way the value is recorded as ASSUMED and shown as such -- this only
# changes which assumption is made, never whether it is admitted to.
LOAD_TYPE_DALI = 16
LOAD_TYPE_LED_REVERSE_PHASE = 119

# Words in a fitting's description that mean it takes a lamp, or is decorative
# and so wired as an ordinary dimmed circuit rather than on a DALI bus.
_LAMP_WORDS = ("lamp", "pendant", "chandelier", "decorative", "sconce",
               "candle", "festoon", "5a", "socket")


def default_load_type(description: str) -> int:
    """The dimming type to assume for a fitting whose driver nobody stated."""
    text = (description or "").lower()
    if any(word in text for word in _LAMP_WORDS):
        return LOAD_TYPE_LED_REVERSE_PHASE
    return LOAD_TYPE_DALI


def _trim(value) -> str:
    """A number as an engineer writes it: 9.6 stays 9.6, 20.0 becomes 20."""
    n = round(float(value or 0), 2)
    return f"{int(n)}" if n == int(n) else f"{n}"


_LOAD_TYPES: dict[int, str] = {}


def load_type_names() -> dict[int, str]:
    """Designer's own load-type list, by ID -- read once, from the same file
    the extraction is told to choose from."""
    if not _LOAD_TYPES:
        from ._paths import docs
        path = os.path.join(docs(), "LoadTypes_REFERENCE.csv")
        for row in _rows(path):
            try:
                _LOAD_TYPES[int(row["LoadTypeID"])] = row.get("Description", "")
            except (KeyError, ValueError):
                continue
    return _LOAD_TYPES


def load_type_name(load_type_id: int) -> str:
    return load_type_names().get(load_type_id, "")


# How a circuit is dimmed, in the four kinds an engineer actually distinguishes.
# The point is D6: DALI, phase, 0-10V and switched never share a circuit,
# because one output can only speak one of them.
FAMILY_LABELS = {"dali": "DALI", "0-10v": "0–10V", "switched": "switched",
                 "phase": "phase", "": "unknown"}


# Positive evidence for each family, read off the reference file's own
# descriptions rather than a hand-kept list of IDs, so a load type nobody has
# met yet still lands correctly. Order matters: "Non Dimmed EcoSystem" is
# switched, not DALI.
#
# Everything that matches NOTHING here is deliberately unknown rather than
# assumed to be phase. The reference file's 118 entries include fans, motors,
# shades, thermostats, DMX and contact closures, and a rule that treats
# "anything unrecognised is phase" refuses a perfectly good 900 W DMX circuit
# for exceeding a limit that has nothing to do with it.
_FAMILY_EVIDENCE = (
    ("switched", ("non dim", "non-dim", "nondim", "relay", "contact closure",
                  "receptacle")),
    ("dali", ("dali", "ecosystem")),
    ("0-10v", ("0-10", "0 to 10", "zero to 10", "10 to zero")),
    ("phase", ("phase", "incadescent", "incandescent", "halogen",
               "low voltage", "neon")),
)


def control_family(load_type_id: int) -> str:
    """`119` -> `phase`, `16` -> `dali`, `118` -> `switched`, `43` -> ``."""
    desc = load_type_name(load_type_id).lower()
    if not desc:
        return ""
    for family, evidence in _FAMILY_EVIDENCE:
        if any(word in desc for word in evidence):
            return family
    return ""


def circuit_watts(load: Load, fixtures: dict) -> float | None:
    """What this circuit draws -- or None when nobody actually knows.

    None is the whole point (D1). A blank fixture count used to arrive here as
    1, and 1 x 9W read on the sheet exactly like a count somebody had made. A
    circuit whose count was assumed, whose fitting has no wattage, or which is
    linear product with no run length, has no honest total, so it gets none and
    the sheet shows an em dash instead of a number nobody stands behind.
    """
    fx = fixtures.get(load.fixture_ref)
    if fx is None:
        return None
    if fx.watts_per_metre:
        return None if load.run_length_m is None else fx.watts_per_metre * load.run_length_m
    if not fx.wattage or "count" in load.assumed:
        return None
    return fx.wattage * load.count


def _zone_number(row: dict, lettered: list) -> int | None:
    """`ZoneNumber`, tolerating a drawing's own alphanumeric circuit reference.

    Designer's zone number is an integer, but lighting drawings routinely
    number circuits `001A`, `002B`, `003C` -- a real House C read
    (2026-08-07) carried 106 of them across 151 circuits, and demanding an
    integer threw ValueError out of load_schedule. The extraction was good:
    151 circuits, correctly read. It just could not be opened, so a paid read
    was lost to a naming convention.

    So a reference that is not a whole number means "the drawing numbers this
    circuit its own way", which is the same information content as leaving it
    blank: the zone gets its position in the room. It is NOT parsed down to
    leading digits -- 002A, 002B and 002C would all collapse to 2 and collide.
    The caller reports what was set aside; nothing is silently dropped.
    """
    raw = (row.get("ZoneNumber") or "").strip()
    if not raw:
        return None
    try:
        return _int(row, "ZoneNumber")
    except ValueError:
        lettered.append(raw)
        return None


# A reference shaped "letters, then a number": C1, CCT-3, LP 07. NOT a number
# followed by letters (001A, 002B) -- that is the suffix convention, where the
# digits are shared and only the letter distinguishes circuits, so parsing the
# digits out collapses 002A/002B/002C onto one number.
_REF_WITH_NUMBER = re.compile(r"^[A-Za-z]{1,4}[-\s]?0*(\d+)$")


def _derive_zone_numbers_from_refs(s: Schedule) -> None:
    """C1, C2, C3 restarting in every room IS Designer's numbering -- use it.

    James filled the circuit-number column exactly as the template teaches
    (2026-08-12) and the numbers went nowhere: kept in the CSV, shown nowhere,
    given to nobody. Where a room's references are letters-then-number and the
    numbers are unique within that room, the number is the designer's own
    numbering and Designer receives it. Anything else -- suffix styles,
    clashing numbers, prose -- stays a kept reference and Designer numbers the
    room itself. Per ROOM, all or nothing: half a room numbered by us and half
    by Designer would interleave two numbering schemes."""
    by_area: dict[str, list] = {}
    for ld in s.loads:
        by_area.setdefault(ld.area, []).append(ld)
    for area_loads in by_area.values():
        with_refs = [ld for ld in area_loads
                     if ld.zone_ref and ld.zone_number is None]
        if not with_refs:
            continue
        # ALL of them, or none. The first version checked only the circuits
        # that HAD a reference, so a room of C1, C2 and one blank got 1 and 2
        # from the designer and its third number from Designer -- two
        # numbering schemes interleaved in one room, which is the exact thing
        # this rule exists to prevent. (Found by Gemini in the pre-release
        # review, 2026-08-13, in code I had written the rule for myself.)
        unnumbered = [ld for ld in area_loads if ld.zone_number is None]
        if len(with_refs) != len(unnumbered):
            continue
        matches = [_REF_WITH_NUMBER.match(ld.zone_ref) for ld in with_refs]
        if not all(matches):
            continue
        numbers = [int(m.group(1)) for m in matches]
        taken = {ld.zone_number for ld in area_loads
                 if ld.zone_number is not None}
        if len(set(numbers)) != len(numbers) or set(numbers) & taken:
            continue
        for ld, number in zip(with_refs, numbers, strict=True):
            ld.zone_number = number


def _drop_partial_room_numbering(s: Schedule) -> None:
    """A room is numbered by the designer or by Designer, never by both.

    Where a schedule numbers SOME circuits in a room and leaves others blank,
    the blanks are auto-numbered from 1 -- so a room whose only stated number
    is 1 gets a second circuit also numbered 1, written into the .hw with no
    duplicate warning, because the validator only compares stated numbers.
    Proven by Codex in the pre-release review (2026-08-13): two circuits, both
    circuit 1, silently. The stated numbers in such a room are dropped and the
    whole room goes to Designer, which is what already happens to a room with
    no numbers at all -- and it is said out loud rather than done quietly.
    """
    by_area: dict[str, list] = {}
    for ld in s.loads:
        by_area.setdefault(ld.area, []).append(ld)
    for area, loads in by_area.items():
        numbered = [ld for ld in loads if ld.zone_number is not None]
        if not numbered or len(numbered) == len(loads):
            continue
        s.warnings.append(
            f"{s.area_name(area)}: {len(numbered)} of {len(loads)} circuit(s) "
            f"carry a circuit number and the rest are blank. Mixing the two "
            f"numbers one circuit twice, so the numbers here were left to "
            f"Designer -- number every circuit in a room, or none of them.")
        for ld in numbered:
            ld.zone_number = None


def _warn_if_files_disagree_in_age(folder: str, s: Schedule) -> None:
    """Flag a folder holding files from two different extractions.

    The build reads whatever it finds, so a leftover Modules.csv from an
    earlier read merges silently with today's rooms. On a real job that
    produced 175 validation errors; a compatible leftover would instead produce
    a wrong project that looks entirely valid.
    """
    import time as _t
    seen = []
    for name in ("Areas.csv", "FixturesCatalog.csv", "LoadSchedule.csv",
                 "Modules.csv", "OutputAssignments.csv", "Keypads.csv",
                 "Buttons.csv", "Scenes.csv"):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            seen.append((name, os.path.getmtime(path)))
    if len(seen) < 2:
        return
    newest = max(m for _, m in seen)
    stragglers = [n for n, m in seen if newest - m > 3600]
    if stragglers:
        when = _t.strftime("%d %b %H:%M", _t.localtime(min(
            m for n, m in seen if n in stragglers)))
        s.warnings.append(
            f"{', '.join(stragglers)} {'is' if len(stragglers) == 1 else 'are'} "
            f"much older than the rest of this schedule (last written {when}). "
            f"That usually means two different reads are mixed in one folder. "
            f"Move the old file(s) out and build again, or the project will "
            f"combine them.")


def _area_resolver(s: Schedule):
    """How every other file's `AreaName` finds its room.

    Rooms are identified by floor AND name, because a house with a WC on three
    floors is ordinary and refusing it forced the extraction to invent suffixes
    the drawings do not carry. Where a name belongs to one room it is used as
    written; where it belongs to several, the reference must say which floor
    ("First Floor > WC"), and a bare name is recorded as ambiguous rather than
    silently taken to mean the first one.
    """
    by_name: dict[str, list[Area]] = {}
    for a in s.areas:
        by_name.setdefault(a.name, []).append(a)
    for group in by_name.values():
        if len(group) > 1:
            for a in group:
                a.uid = f"{a.parent} > {a.name}" if a.parent else a.name
    by_uid = {a.uid: a for a in s.areas}

    def resolve(raw: str) -> str:
        raw = (raw or "").strip()
        if raw in by_uid:
            return raw
        group = by_name.get(raw, [])
        if len(group) == 1:
            return group[0].uid
        if len(group) > 1 and raw not in s.ambiguous_areas:
            s.ambiguous_areas.append(raw)
        return raw

    return resolve


def load_schedule(folder: str, module_types: dict[str, int],
                  keypad_models: dict[str, int]) -> Schedule:
    """Read the seven CSVs (plus the optional Areas.csv) out of `folder`."""
    s = Schedule()
    p = lambda n: os.path.join(folder, n)  # noqa: E731

    # The provenance record, if the read left one. Tolerant of absence and of
    # damage: an old or hand-assembled folder simply has no stated source.
    try:
        import json as _json
        with open(p("read-with.json"), encoding="utf-8") as fh:
            s.source = (_json.load(fh) or {}).get("source")
    except (OSError, ValueError):
        s.source = None

    for row in _new_rows(p("Areas.csv")):
        s.areas.append(Area(row["AreaName"], row.get("ParentArea", ""),
                            row.get("Notes", ""),
                            stated_circuits=_int(row, "StatedCircuits")))
    area_of = _area_resolver(s)

    for row in _new_rows(p("FixturesCatalog.csv")):
        dim_range, low_end, high_end, trims_assumed = _dimming(row)
        assumed = set()
        if trims_assumed:
            assumed.add("trims")
        if (row.get("LampQuantity") or "").strip() == "":
            assumed.add("lamp_quantity")
        description = row.get("Description", "")
        if (row.get("LoadTypeID") or "").strip() == "":
            # D2: the file still builds -- but the value is marked, never shown
            # as read. What it defaults TO depends on the fitting: see
            # default_load_type.
            assumed.add("load_type_id")
        s.fixtures.append(Fixture(
            ref=row["FixtureRef"],
            description=description,
            load_type_id=_int(row, "LoadTypeID", default_load_type(description)),
            wattage=_num(row, "FixtureWattage_W", 0),
            lamp_quantity=_int(row, "LampQuantity", 1),
            lamp_wattage=_num(row, "LampWattage_W", 0),
            low_end=low_end,
            high_end=high_end,
            # Follow the load type, including an assumed one. Defaulting this
            # to 0 while assuming reverse phase produced a warning about a
            # disagreement we had just invented ourselves.
            phase_control=_int(row, "PhaseControl",
                               1 if _int(row, "LoadTypeID",
                                         default_load_type(description)) == 119 else 0),
            dimming_range=dim_range,
            manufacturer_name=row.get("ManufacturerName", ""),
            manufacturer_model=row.get("ManufacturerModel", ""),
            data_source=row.get("DataSource", ""),
            notes=row.get("Notes", ""),
            watts_per_metre=_num(row, "Wattage_W_per_m", 0),
            assumed=assumed,
        ))

    lettered: list[str] = []
    for row in _new_rows(p("LoadSchedule.csv")):
        assumed = set()
        if (row.get("NumberOfFixtures") or "").strip() == "":
            # A blank count arriving as 1 is how "we could not tell" became
            # "one downlight" on 109 of 109 House A circuits.
            assumed.add("count")
        number = _zone_number(row, lettered)
        raw_ref = (row.get("ZoneNumber") or "").strip()
        s.loads.append(Load(
            area=area_of(row["AreaName"]), zone=row["ZoneName"],
            fixture_ref=row["FixtureRef"],
            count=_int(row, "NumberOfFixtures", 1),
            zone_number=number,
            zone_ref=raw_ref if number is None and raw_ref else "",
            load_type_override=_int(row, "LoadTypeID_Override"),
            low_end_override=_int(row, "LowEnd_pct_Override"),
            high_end_override=_int(row, "HighEnd_pct_Override"),
            notes=row.get("Notes", ""),
            run_length_m=_num(row, "RunLength_m"),
            additional_areas=[area_of(x) for x in
                              re.split(r"\s*[;|]\s*", row.get("AdditionalAreas", "") or "")
                              if x.strip()],
            assumed=assumed,
        ))

    _derive_zone_numbers_from_refs(s)
    _drop_partial_room_numbering(s)
    lettered = sorted({ld.zone_ref for ld in s.loads
                       if ld.zone_ref and ld.zone_number is None})

    if lettered and s.source not in ("spreadsheet", "drawing"):
        # Not a remark at all for a spreadsheet or drawing import: their
        # circuit-number column is explicitly offered as "the designer's own
        # reference", and the template's own worked example uses C1, C2, C3 --
        # James copied them from the guidance page and was then warned about
        # it (2026-08-12). Following the instructions must produce silence.
        # For a plans read the references arrive without a human choosing a
        # column, so it is still worth a sentence -- worded to lead with what
        # was KEPT. The first version led with what was not used, and read as
        # "your circuit numbers didn't work"; nothing was missing, the
        # sentence was.
        shown = ", ".join(sorted(set(lettered))[:6])
        s.warnings.append(
            f"The designer's circuit references ({shown}"
            f"{'...' if len(set(lettered)) > 6 else ''}) are kept exactly as "
            f"written, beside each circuit. They are not Designer's circuit "
            f"numbers -- that field only takes a plain whole number, and "
            f"Designer numbers circuits within each room itself, so it was "
            f"left to. Nothing is missing. Only write a plain whole number in "
            f"the circuit-number column if you want to force Designer's own "
            f"numbering.")

    # A schedule file much older than the rest is the signature of two runs
    # mixed in one folder -- extraction archives the previous set now, but a
    # project created before that, or hand-assembled, can still be mixed.
    _warn_if_files_disagree_in_age(folder, s)

    for row in _new_rows(p("Modules.csv")):
        label = row["ModuleType"]
        s.modules.append(Module(
            name=row["ModuleName"], model_label=label,
            model_info_id=module_types.get(label, -1),
            area=area_of(row["AreaName"]), link=row.get("LinkName", ""),
            address=_int(row, "AddressOnLink"),
            output_count=_int(row, "OutputCount"),
            serial=row.get("SerialNumber", ""),
        ))

    for row in _new_rows(p("OutputAssignments.csv")):
        s.outputs.append(OutputAssignment(
            area=area_of(row["AreaName"]), zone=row["ZoneName"],
            module=row["ModuleName"], output=_int(row, "OutputNumber", 1)))

    for row in _new_rows(p("Keypads.csv")):
        label = row["KeypadModel"]
        s.keypads.append(Keypad(
            name=row["KeypadName"], area=area_of(row["AreaName"]),
            station=row.get("StationName", ""), device=row.get("DeviceName", "Device 1"),
            model_label=label, model_info_id=keypad_models.get(label, -1),
            link=row.get("LinkName", ""), address=_int(row, "AddressOnLink"),
            serial=row.get("SerialNumber", ""), notes=row.get("Notes", "")))

    # Buttons: several rows may share (keypad, number, label) when one button
    # fires actions in several areas at once -- a "Goodnight" button, say.
    grouped: dict[tuple[str, int], Button] = {}
    for row in _new_rows(p("Buttons.csv")):
        number = _int(row, "ButtonNumber")
        if number is None:
            # Defaulting a blank to 1 silently merged two different buttons
            # into button 1, which then fired both sets of actions.
            raise ValueError(
                f"Buttons.csv: the '{row.get('ButtonLabel', '')}' row for keypad "
                f"'{row.get('KeypadName', '')}' has no ButtonNumber. Every button "
                f"action needs one, or two buttons collapse into one.")
        key = (row["KeypadName"], number)
        btn = grouped.get(key)
        if btn is None:
            btn = Button(keypad=key[0], number=key[1], label=row.get("ButtonLabel", ""),
                         notes=row.get("Notes", ""))
            grouped[key] = btn
            s.buttons.append(btn)
        btn.actions.append(ButtonAction(
            action_type=row.get("ActionType", "RecallAreaScene"),
            target_area=area_of(row.get("TargetArea", "")),
            target_scene=row.get("TargetScene", ""),
            target_zone=row.get("TargetZone", ""),
            level=_int(row, "Level_pct"),
            fade=_int(row, "Fade_seconds", DEFAULT_FADE_SECONDS),
            delay=_int(row, "Delay_seconds", 0)))

    scenes: dict[tuple[str, int], Scene] = {}
    for row in _new_rows(p("Scenes.csv")):
        key = (area_of(row["AreaName"]), _int(row, "SceneNumber", 0))
        sc = scenes.get(key)
        if sc is None:
            sc = Scene(area=key[0], name=row.get("SceneName", f"Scene {key[1]}"), number=key[1])
            scenes[key] = sc
            s.scenes.append(sc)
        else:
            # Two DIFFERENT scenes sharing a number merge into this one, taking
            # the first name and both sets of levels -- a scene that is neither
            # of the two the engineer wrote. validate() has a check for this,
            # but it could never fire: the merge happens here, before it looks.
            other = (row.get("SceneName") or "").strip()
            # One clash, reported ONCE. This sits in the per-ROW loop, so a
            # ten-circuit scene colliding with another produced the identical
            # message ten times and told the engineer they had ten problems.
            if other and other != sc.name:
                msg = (f"Scenes: '{key[0]}' has two different scenes numbered {key[1]} "
                       f"('{sc.name}' and '{other}'). They would merge into one scene "
                       f"carrying both sets of levels.")
                if msg not in s.merged_scenes:
                    s.merged_scenes.append(msg)
        cmd = (row.get("CommandType") or "SetLevel").strip()
        raw_level = (row.get("Level_pct") or "").strip()
        level = None if cmd.lower() == "unaffected" or raw_level.lower() == "unaffected" \
            else _int(row, "Level_pct", 0)
        sc.levels.append(SceneLevel(
            zone=row["ZoneName"], command=cmd, level=level,
            fade=_int(row, "Fade_seconds", DEFAULT_FADE_SECONDS),
            delay=_int(row, "Delay_seconds", 0),
            notes=row.get("Notes", ""),
            level_stated=bool(raw_level)))

    return s


# --------------------------------------------------------------- validation

def validate(s: Schedule, shell_floors: set[str], shell_links: set[str],
             module_blueprints: dict, keypad_blueprints: dict) -> Schedule:
    p: list[str] = []
    w = s.warnings

    area_names = {a.uid for a in s.areas}
    fixture_refs = {f.ref for f in s.fixtures}
    load_keys = {(ld.area, ld.zone) for ld in s.loads}
    module_names = {m.name for m in s.modules}
    keypad_names = {k.name for k in s.keypads}
    scene_keys = {(sc.area, sc.name) for sc in s.scenes}

    # Duplicate identities make the review sheet and the generated file
    # disagree: the sheet keeps the LAST row of a duplicate pair, while the
    # builder takes the FIRST fixture's control properties. What was approved
    # and what was written would be different things.
    seen_refs: set[str] = set()
    for fx in s.fixtures:
        if fx.ref in seen_refs:
            p.append(f"FixturesCatalog: '{fx.ref}' appears more than once. Each "
                     f"FixtureRef must be unique -- otherwise the review sheet "
                     f"and the generated file disagree about which one it means.")
        seen_refs.add(fx.ref)

    seen_levels: set = set()
    for sc in s.scenes:
        for lvl in getattr(sc, "levels", []):
            key = (sc.area, sc.number, getattr(lvl, "zone", None))
            if key in seen_levels:
                p.append(f"Scenes: '{sc.area}' scene {sc.number} sets "
                         f"'{key[2]}' more than once. Both get written and the "
                         f"review sheet shows only the last.")
            seen_levels.add(key)

    seen_missing_floors: set[str] = set()
    for a in s.areas:
        if not a.parent:
            p.append(f"Areas: '{a.name}' has no ParentArea -- name the floor it belongs to.")
        elif a.parent not in shell_floors and a.parent not in seen_missing_floors:
            seen_missing_floors.add(a.parent)
            w.append(f"floor '{a.parent}' is not in the shell -- it will be CREATED "
                     f"(cloned from an existing floor). Existing floors: "
                     f"{', '.join(sorted(shell_floors)) or '(none)'}. "
                     f"Check the spelling if you meant one of those.")

    # Two rooms with the same name on the SAME floor are still a duplicate; the
    # same name on two floors is an ordinary house and is now allowed, keyed on
    # floor + name (see _area_resolver).
    seen_area: set[tuple[str, str]] = set()
    seen_uid: set[str] = set()
    for a in s.areas:
        if (a.parent, a.name) in seen_area:
            p.append(f"Areas: '{a.name}' appears twice on '{a.parent}'. Two rooms on "
                     f"one floor cannot share a name -- every other file refers to a "
                     f"room by it.")
        seen_area.add((a.parent, a.name))
        # The qualified form of a repeated name is itself a name a room could
        # be called. Two rooms resolving to one key would silently share an
        # area in the built file, taking each other's circuits.
        if a.uid in seen_uid:
            p.append(f"Areas: two rooms both resolve to '{a.uid}'. Rename one -- "
                     f"a room called '{a.uid}' collides with the floor-qualified "
                     f"form of a repeated room name.")
        seen_uid.add(a.uid)

    # The designer counting his own scheme, against what the read found. This
    # is the strongest check there is on whether a room was read WHOLE, and it
    # is the one nobody was making: the House A estimate states 521 circuits,
    # the read produced 194, and the two figures never met.
    short = [(a, a.stated_circuits, sum(1 for ld in s.loads if ld.area == a.uid))
             for a in s.areas if a.stated_circuits is not None]
    missing = [(a, said, got) for a, said, got in short if got < said]
    if missing:
        total_said = sum(said for _, said, _ in short)
        total_got = sum(got for _, _, got in short)
        worst = ", ".join(f"{a.name} ({got} of {said})"
                          for a, said, got in sorted(missing, key=lambda x: x[2] - x[1])[:8])
        w.append(
            f"{len(missing)} room(s) have FEWER circuits than the documents say: "
            f"{worst}{'...' if len(missing) > 8 else ''}. Across the rooms with a "
            f"stated count, the documents say {total_said} circuits and this "
            f"schedule has {total_got}. A circuit that is not here is not built, "
            f"not wired and not on any panel -- check these rooms against the "
            f"drawings before building.")
    over = [(a, said, got) for a, said, got in short if got > said]
    if over:
        shown = ", ".join(f"{a.name} ({got} of {said})" for a, said, got in over[:8])
        w.append(f"{len(over)} room(s) have MORE circuits than the documents say: "
                 f"{shown}{'...' if len(over) > 8 else ''}. That can be right -- a "
                 f"circuit estimate is an estimate -- but check it was not read twice.")

    for name in s.ambiguous_areas:
        uids = [a.uid for a in s.areas if a.name == name]
        p.append(f"'{name}' is the name of {len(uids)} rooms, so a row naming it "
                 f"does not say which one is meant. Write it as one of "
                 f"{', '.join(repr(u) for u in uids)} wherever it is referred to.")

    # A circuit with NO fixture at all is the extraction saying it could not
    # tell. That used to refuse the whole build -- an honest re-read of a real
    # job produced 194 of them and nothing buildable, while the guessing read
    # before it built fine. Report them, build the rest.
    unresolved = [ld for ld in s.loads if ld.unresolved]
    if unresolved:
        shown = ", ".join(f"{ld.area} / {ld.zone}" for ld in unresolved[:6])
        w.append(
            f"{len(unresolved)} circuit(s) have no fixture type ({shown}"
            f"{'...' if len(unresolved) > 6 else ''}). The drawings did not say "
            f"what is on them, so they cannot be built and are left out of the "
            f"project -- everything else is built as normal. They are listed on "
            f"the review sheet: fill in FixtureRef and build again to add them.")

    for ld in s.loads:
        if ld.area not in area_names:
            p.append(f"LoadSchedule: zone '{ld.zone}' is in area '{ld.area}', which is not in Areas.csv.")
        if ld.fixture_ref and ld.fixture_ref not in fixture_refs:
            p.append(f"LoadSchedule: zone '{ld.area} / {ld.zone}' uses fixture '{ld.fixture_ref}', "
                     f"which is not in FixturesCatalog.csv.")
        for extra in ld.additional_areas:
            if extra not in area_names:
                p.append(f"LoadSchedule: zone '{ld.area} / {ld.zone}' also serves "
                         f"'{extra}', which is not in Areas.csv.")
            elif extra == ld.area:
                p.append(f"LoadSchedule: zone '{ld.area} / {ld.zone}' lists its own "
                         f"room in AdditionalAreas.")
        if ld.count < 1:
            p.append(f"LoadSchedule: zone '{ld.area} / {ld.zone}' has NumberOfFixtures={ld.count}.")
        if ld.run_length_m is not None and ld.run_length_m <= 0:
            p.append(f"LoadSchedule: zone '{ld.area} / {ld.zone}' has "
                     f"RunLength_m={ld.run_length_m}.")

    if len(load_keys) != len(s.loads):
        p.append("LoadSchedule: duplicate AreaName + ZoneName rows.")

    # Designer numbers zones uniquely within a room; the builder writes an
    # explicit ZoneNumber straight through, so two circuits claiming the same
    # one would land in the file as written. Warn rather than refuse: the
    # consequence inside Designer is unverified, but it is never intended.
    seen_zone_num: dict[tuple[str, int], str] = {}
    for ld in s.loads:
        if ld.zone_number is None:
            continue
        zkey = (ld.area, ld.zone_number)
        if zkey in seen_zone_num:
            w.append(f"'{ld.area}': circuits '{seen_zone_num[zkey]}' and "
                     f"'{ld.zone}' both claim ZoneNumber {ld.zone_number}. "
                     f"Designer numbers zones uniquely within a room -- give "
                     f"one of them a different number or leave it blank.")
        seen_zone_num[zkey] = ld.zone

    # ---- what the circuit is, electrically (D6, D7, D9, D10) --------------
    by_ref = {f.ref: f for f in s.fixtures}
    known_types = load_type_names()

    # A load type Designer does not have is written into the switch leg exactly
    # as given, so a mistyped 999 produces a circuit nothing can drive -- and it
    # slips past every check below, because an unrecognised code has no family.
    for fx in s.fixtures:
        if fx.load_type_id not in known_types:
            p.append(f"FixturesCatalog: '{fx.ref}' has LoadTypeID "
                     f"{fx.load_type_id}, which is not in LoadTypes_REFERENCE.csv.")
    for ld in s.loads:
        if ld.load_type_override is not None and ld.load_type_override not in known_types:
            p.append(f"LoadSchedule: '{ld.area} / {ld.zone}' has "
                     f"LoadTypeID_Override {ld.load_type_override}, which is not "
                     f"in LoadTypes_REFERENCE.csv.")

    for ld in s.loads:
        fx = by_ref.get(ld.fixture_ref)
        if fx is None:
            continue

        # D6: one control type per circuit. A zone already holds exactly one
        # fitting type, so the only way to ask for two is an override that
        # needs different hardware from the fitting itself.
        #
        # NOT every override: a DALI fitting on a switched circuit is ordinary
        # and is what the override column is for -- the same product goes in a
        # store room on a relay and in the hall on a DALI bus. 19 House A
        # circuits do exactly that, correctly. What is impossible is swapping
        # one DIMMING method for another: a DALI driver cannot be phase-dimmed
        # and a non-dim fitting cannot be dimmed at all, whatever the CSV says.
        if ld.load_type_override is not None:
            was, now = control_family(fx.load_type_id), control_family(ld.load_type_override)
            if was and now and was != now and now != "switched":
                p.append(
                    f"LoadSchedule: '{ld.area} / {ld.zone}' overrides {fx.ref} from "
                    f"{FAMILY_LABELS[was]} to {FAMILY_LABELS[now]} "
                    f"({load_type_name(fx.load_type_id)} -> "
                    f"{load_type_name(ld.load_type_override)}). Those need different "
                    f"drivers, so the circuit cannot be both -- correct the fitting in "
                    f"FixturesCatalog, or the override here. (Switching a dimmable "
                    f"fitting is fine; this is a change between dimming methods.)")

        # D7: linear product is measured, not counted.
        if fx.watts_per_metre and ld.run_length_m is None:
            w.append(f"'{ld.area} / {ld.zone}' is {fx.ref}, which is specified per "
                     f"metre ({_trim(fx.watts_per_metre)} W/m), but the circuit has no "
                     f"RunLength_m. Its load cannot be worked out -- measure the run "
                     f"off the drawings and put the metres in RunLength_m.")
        if ld.run_length_m is not None and not fx.watts_per_metre:
            p.append(f"LoadSchedule: '{ld.area} / {ld.zone}' gives a run length of "
                     f"{_trim(ld.run_length_m)} m, but {fx.ref} has no per-metre "
                     f"wattage. Put the W/m in FixturesCatalog's Wattage_W_per_m, or "
                     f"count the fittings instead.")

        # D9/D10: the load ceiling is a PHASE dimming limit and nothing else.
        # DALI and switched circuits have no per-zone ceiling -- 100 m of tape
        # can sit on one DALI address.
        watts = circuit_watts(ld, by_ref)
        family = control_family(ld.load_type_override
                                if ld.load_type_override is not None else fx.load_type_id)
        if watts is not None and family == "phase":
            if watts > PHASE_FIRST_OUTPUT_W:
                p.append(
                    f"LoadSchedule: '{ld.area} / {ld.zone}' draws {_trim(watts)} W on "
                    f"phase dimming. A four-channel phase module takes "
                    f"{PHASE_FIRST_OUTPUT_W:.0f} W on its first output and "
                    f"{PHASE_OUTPUT_W:.0f} W on the other three, so no output can "
                    f"drive this circuit. Split it.")
            elif watts > PHASE_OUTPUT_W:
                w.append(
                    f"'{ld.area} / {ld.zone}' draws {_trim(watts)} W on phase dimming, "
                    f"over the {PHASE_OUTPUT_W:.0f} W the second, third and fourth "
                    f"outputs of a phase module take. FIRST OUTPUT ONLY -- say so to "
                    f"whoever lays out the panel, or split the circuit.")

    for m in s.modules:
        if m.model_info_id < 0:
            p.append(f"Modules: '{m.name}' has ModuleType '{m.model_label}', which is not in "
                     f"ModuleTypes_REFERENCE.csv.")
        elif m.model_info_id not in module_blueprints:
            p.append(f"Modules: '{m.name}' is a {m.model_label}, but the shell contains no module of "
                     f"that type. Add one in Designer, save, and re-run -- the writer copies the "
                     f"shape of a real row and cannot invent it.")
        if m.area not in area_names:
            p.append(f"Modules: '{m.name}' lives in area '{m.area}', which is not in Areas.csv.")
        if m.link and m.link not in shell_links:
            p.append(f"Modules: '{m.name}' is on comm link '{m.link}', which is not in the shell. "
                     f"Links available: {', '.join(sorted(shell_links)) or '(none)'}.")

    if len(module_names) != len(s.modules):
        p.append("Modules: duplicate ModuleName rows.")

    seen_link_addr: dict[tuple[str, int], str] = {}
    for m in s.modules:
        if m.link and m.address is not None:
            key = (m.link, m.address)
            if key in seen_link_addr:
                p.append(f"Modules: '{m.name}' and '{seen_link_addr[key]}' both claim address "
                         f"{m.address} on link '{m.link}'.")
            seen_link_addr[key] = m.name

    p.extend(s.merged_scenes)

    # Per MODULE, not per assignment. Sitting inside the OutputAssignments loop
    # it fired once for every wired output -- eight identical problems for one
    # module -- and skipped entirely any module with no assignments yet, which
    # the builder still creates channels for.
    for mod in s.modules:
        physical = (module_blueprints[mod.model_info_id].output_count
                    if mod.model_info_id in module_blueprints else None)
        if physical and mod.output_count and mod.output_count > physical:
            p.append(f"Modules: '{mod.name}' claims {mod.output_count} outputs, but a "
                     f"{mod.model_label} physically has {physical}.")

    # A keypad is looked up by NAME when its buttons are programmed, so two
    # keypads sharing one silently receive each other's button programming.
    seen_kp: set[str] = set()
    for k in s.keypads:
        if k.name in seen_kp:
            # Buttons.csv identifies a keypad by NAME and carries no area, so the
            # name has to carry the room -- which is why the contract asks for
            # "Area > Station". Say that, rather than just refusing: the fix is
            # to name them properly, not to delete one.
            # Only SUGGEST a name when the suggestion is actually different and
            # actually unique. Blindly interpolating produced ' > Door' when the
            # area was blank, and 'Hall > Hall > Door' when the name already
            # carried the room -- telling the engineer to use the name they have.
            suggestion = f"{k.area} > {k.station}".strip() if k.area and k.station else ""
            hint = (f" Name each one for the room it is in, in the form "
                    f"'{suggestion}', so the two are different."
                    if suggestion and suggestion != k.name
                    else " Give each one a different name -- the room it is in is the "
                         "usual way, as in 'Kitchen > By Rear Door'.")
            p.append(f"Keypads: '{k.name}' appears more than once. A button names its "
                     f"keypad and nothing else, so two keypads sharing a name both get "
                     f"the same programming.{hint}")
        seen_kp.add(k.name)

    seen_load_out: dict[tuple[str, str], str] = {}
    seen_out: dict[tuple[str, int], str] = {}
    for o in s.outputs:
        # Two loads on one output was caught; ONE load on two outputs was not.
        # The builder keeps the last, so the circuit is driven by one output and
        # the other is quietly left doing nothing.
        lk = (o.area, o.zone)
        if lk in seen_load_out:
            p.append(f"OutputAssignments: '{o.area} / {o.zone}' is wired to both "
                     f"{seen_load_out[lk]} and {o.module} output {o.output}. Only the "
                     f"last would drive it.")
        seen_load_out[lk] = f"{o.module} output {o.output}"
        if (o.area, o.zone) not in load_keys:
            p.append(f"OutputAssignments: '{o.area} / {o.zone}' is not in LoadSchedule.csv.")
        if o.module not in module_names:
            p.append(f"OutputAssignments: '{o.area} / {o.zone}' wires to module '{o.module}', "
                     f"which is not in Modules.csv.")
        else:
            mod = next(m for m in s.modules if m.name == o.module)
            # The PHYSICAL module wins. Reading it the other way round let a CSV
            # claim OutputCount=99 on a four-output unit, and the file was built
            # with channels the real hardware does not have.
            physical = (module_blueprints[mod.model_info_id].output_count
                        if mod.model_info_id in module_blueprints else None)
            cap = physical or mod.output_count
            if cap and o.output > cap:
                p.append(f"OutputAssignments: output {o.output} on '{o.module}' exceeds its "
                         f"{cap} outputs.")
        if o.output < 1:
            p.append(f"OutputAssignments: '{o.area} / {o.zone}' has OutputNumber={o.output}.")
        key = (o.module, o.output)
        if key in seen_out:
            p.append(f"OutputAssignments: '{o.module}' output {o.output} is claimed by both "
                     f"'{seen_out[key]}' and '{o.area} / {o.zone}'.")
        seen_out[key] = f"{o.area} / {o.zone}"

    # Naming every unwired circuit is useful when a few were missed and useless
    # when none is wired at all -- which is the NORMAL state of a fresh read,
    # because the extraction is told not to propose panels. That produced 194
    # identical lines at the top of the review sheet, burying the four things
    # the engineer actually had to look at.
    unwired = load_keys - {(o.area, o.zone) for o in s.outputs}
    if unwired and not s.outputs:
        w.append(f"None of the {len(unwired)} circuits is wired to a module output. "
                 f"That is the normal state of a fresh read: the shell deliberately "
                 f"contains no panels, so the circuits arrive in Designer marked Not "
                 f"Assigned and you attach them there.")
    elif unwired:
        shown = ", ".join(f"'{a} / {z}'" for a, z in sorted(unwired)[:10])
        w.append(f"{len(unwired)} circuit(s) have no output assignment ({shown}"
                 f"{'...' if len(unwired) > 10 else ''}) -- they will exist in "
                 f"Designer but drive nothing until you wire them.")

    for k in s.keypads:
        if k.model_info_id < 0:
            p.append(f"Keypads: '{k.name}' has KeypadModel '{k.model_label}', which is not in "
                     f"KeypadModels_REFERENCE.csv.")
        elif k.model_info_id not in keypad_blueprints:
            p.append(f"Keypads: '{k.name}' is a {k.model_label}, but the shell contains no keypad of "
                     f"that model. Add one in Designer, save, and re-run.")
        if k.area not in area_names:
            p.append(f"Keypads: '{k.name}' is in area '{k.area}', which is not in Areas.csv.")
        if k.link and k.link not in shell_links:
            p.append(f"Keypads: '{k.name}' is on comm link '{k.link}', which is not in the shell.")
        if k.link and k.address is not None:
            key = (k.link, k.address)
            if key in seen_link_addr:
                p.append(f"Keypads: '{k.name}' claims address {k.address} on link '{k.link}', "
                         f"already used by '{seen_link_addr[key]}'.")
            seen_link_addr[key] = k.name

    # Same shape as the unwired circuits above: the extraction is told always to
    # leave LinkName blank, so one line per keypad is 49 identical lines saying
    # the system is in exactly the state it is supposed to be in.
    offlink = [k.name for k in s.keypads if not k.link]
    if offlink and len(offlink) == len(s.keypads):
        w.append(f"None of the {len(offlink)} keypads is on a comm link. That is the "
                 f"normal state of a fresh read: you assign them to a link in Designer.")
    elif offlink:
        shown = ", ".join(f"'{n}'" for n in offlink[:10])
        w.append(f"{len(offlink)} keypad(s) have no LinkName ({shown}"
                 f"{'...' if len(offlink) > 10 else ''}) -- they will exist in Designer "
                 f"but sit off-link until you assign them to a comm link there.")

    for b in s.buttons:
        if b.number < 1:
            # The builder programmes physical buttons 1..N. A row numbered 0
            # validated, appeared on the review sheet, and was then silently
            # absent from the built file -- the worst of both.
            p.append(f"Buttons: keypad '{b.keypad}' has a row for button "
                     f"{b.number}. Buttons are numbered from 1, so this row "
                     f"would be shown on the review sheet but never written.")
        if b.keypad not in keypad_names:
            p.append(f"Buttons: button {b.number} refers to keypad '{b.keypad}', "
                     f"which is not in Keypads.csv.")
        else:
            kp = next(k for k in s.keypads if k.name == b.keypad)
            bp = keypad_blueprints.get(kp.model_info_id)
            if bp and b.number > len(bp.engraved_buttons):
                p.append(f"Buttons: keypad '{b.keypad}' is a {kp.model_label} with "
                         f"{len(bp.engraved_buttons)} buttons; row asks for button {b.number}.")
        for act in b.actions:
            if act.action_type == "RecallAreaScene":
                if (act.target_area, act.target_scene) not in scene_keys:
                    p.append(f"Buttons: '{b.keypad}' button {b.number} ('{b.label}') recalls scene "
                             f"'{act.target_scene}' in '{act.target_area}', which is not in Scenes.csv.")
            elif act.action_type == "DirectZoneLevel":
                if (act.target_area or "", act.target_zone) not in load_keys:
                    p.append(f"Buttons: '{b.keypad}' button {b.number} targets zone "
                             f"'{act.target_area} / {act.target_zone}', which is not in LoadSchedule.csv.")
                if act.level is None:
                    p.append(f"Buttons: '{b.keypad}' button {b.number} is DirectZoneLevel "
                             f"but has no Level_pct.")
                elif not 0 <= act.level <= 100:
                    # Scene levels were range-checked and button levels were
                    # not, so a button could be written to set a circuit to
                    # 150%.
                    p.append(f"Buttons: '{b.keypad}' button {b.number} sets "
                             f"'{act.target_zone}' to {act.level}%.")
            else:
                p.append(f"Buttons: '{b.keypad}' button {b.number} has unsupported ActionType "
                         f"'{act.action_type}'. Supported: RecallAreaScene, DirectZoneLevel.")
            if act.fade not in FADE_RAW_CONFIRMED:
                # Scene fades were warned about and button fades were not, so a
                # designer's 4s on a button was rewritten to 2s in silence.
                w.append(f"Fade of {act.fade}s on '{b.keypad}' button {b.number} "
                         f"is not a calibrated value; writing the "
                         f"{DEFAULT_FADE_SECONDS}s encoding instead. Confirmed "
                         f"fades: {sorted(FADE_RAW_CONFIRMED)}.")

    synonyms: list[str] = []
    seen_scene_num: dict[tuple[str, int], str] = {}
    # Two scenes in a room sharing a NAME was only ever caught by the web scene
    # editor -- and the app tells engineers to edit the CSVs in Excel, which
    # goes nowhere near it. A button recalls a scene by name, and the builder
    # keeps one number per (area, name), so the second silently steals every
    # button pointing at the first. Compared casefolded, the way the builder
    # will collapse them.
    seen_scene_name: dict[tuple[str, str], int] = {}
    for sc in s.scenes:
        nkey = (sc.area, sc.name.strip().casefold())
        if nkey in seen_scene_name and seen_scene_name[nkey] != sc.number:
            p.append(f"Scenes: '{sc.area}' has two scenes called '{sc.name}' "
                     f"(numbers {seen_scene_name[nkey]} and {sc.number}). A button "
                     f"names the scene it recalls, so both would point at the same "
                     f"one. Give them different names.")
        seen_scene_name[nkey] = sc.number
    for sc in s.scenes:
        if sc.area not in area_names:
            p.append(f"Scenes: '{sc.name}' is in area '{sc.area}', which is not in Areas.csv.")
        if not 0 <= sc.number <= 30:
            p.append(f"Scenes: '{sc.area} / {sc.name}' has SceneNumber {sc.number}; must be 0-30.")
        key = (sc.area, sc.number)
        if key in seen_scene_num:
            p.append(f"Scenes: '{sc.area}' has two scenes numbered {sc.number} "
                     f"('{seen_scene_num[key]}' and '{sc.name}').")
        seen_scene_num[key] = sc.name
        for lv in sc.levels:
            # Everything except Unaffected is built as a plain brightness, so a
            # misspelt "Unaffectd" switches a circuit the scene meant to leave
            # alone, and a DMXColor preset index becomes a percentage.
            cmd = normalise_command(lv.command)
            if (lv.command or "").strip() and not cmd:
                # normalise_command strips every non-alphanumeric character, so
                # a CommandType of "-" or "?" folded to "" and slipped past the
                # check below -- then built as an ordinary brightness, switching
                # on a circuit whose instruction nobody could read.
                p.append(f"Scenes: '{sc.area} / {sc.name}' sets '{lv.zone}' with "
                         f"CommandType='{lv.command}', which is not a word. Use one "
                         f"of: {', '.join(SCENE_COMMANDS_SHOWN)}.")
            elif cmd and cmd in SCENE_COMMAND_SYNONYMS:
                synonyms.append((lv.command or "").strip())
            elif cmd and cmd not in SCENE_COMMANDS:
                if cmd in SCENE_COMMANDS_UNIMPLEMENTED:
                    p.append(f"Scenes: '{sc.area} / {sc.name}' sets '{lv.zone}' with "
                             f"CommandType='{lv.command}'. This writer does not build "
                             f"colour or colour-temperature scenes yet -- it would be "
                             f"written as an ordinary brightness of that number.")
                else:
                    p.append(f"Scenes: '{sc.area} / {sc.name}' sets '{lv.zone}' with "
                             f"CommandType='{lv.command}', which is not understood. Use "
                             f"one of: {', '.join(SCENE_COMMANDS_SHOWN)}. A misspelt "
                             f"'Unaffected' would switch a circuit the scene meant to "
                             f"leave alone.")
            if lv.level is not None and not lv.level_stated:
                # A blank Level_pct became 0 and was built as "switch this
                # circuit off in this scene" -- an assertion made out of a cell
                # nobody filled in. The two meanings are a word apart and only
                # the author knows which was meant.
                p.append(f"Scenes: '{sc.area} / {sc.name}' has no Level_pct for "
                         f"'{lv.zone}'. Give it a level, or write "
                         f"CommandType='Unaffected' if the scene should leave that "
                         f"circuit as it is -- a blank would be built as 0%, which "
                         f"switches it off.")
            if (sc.area, lv.zone) not in load_keys:
                p.append(f"Scenes: '{sc.area} / {sc.name}' sets zone '{lv.zone}', "
                         f"which is not a load in that area.")
            if lv.level is not None and not 0 <= lv.level <= 100:
                p.append(f"Scenes: '{sc.area} / {sc.name}' sets '{lv.zone}' to {lv.level}%.")
            if lv.fade not in FADE_RAW_CONFIRMED:
                w.append(f"Fade of {lv.fade}s on '{sc.area} / {sc.name}' is not a calibrated value; "
                         f"writing the {DEFAULT_FADE_SECONDS}s encoding instead. "
                         f"Confirmed fades: {sorted(FADE_RAW_CONFIRMED)}.")

    if synonyms:
        shown = ", ".join(sorted(set(synonyms))[:4])
        w.append(f"{len(synonyms)} scene row(s) use '{shown}' where the contract "
                 f"says SetLevel. It means the same thing and is built as an "
                 f"ordinary brightness, so it was accepted -- but "
                 f"'{sorted(set(synonyms))[0]}' is not a word this writer "
                 f"documents, so check the levels on the review sheet.")

    # One line per fitting made 27 identical lines on a real read, because a set
    # of drawings that does not state a driver does not state a phase-control
    # flag either. One line, naming them.
    no_phase = [f.ref for f in s.fixtures if f.load_type_id == 119 and f.phase_control != 1]
    if no_phase:
        shown = ", ".join(no_phase[:10])
        w.append(f"{len(no_phase)} fitting(s) are LED Reverse Phase with PhaseControl "
                 f"not set to 1 ({shown}{'...' if len(no_phase) > 10 else ''}). Designer "
                 f"expects 1 for reverse-phase dimming.")

    for f in s.fixtures:
        if not 0 <= f.low_end <= 100 or not 0 <= f.high_end <= 100:
            p.append(f"FixturesCatalog: '{f.ref}' has trim values outside 0-100.")
        if f.low_end >= f.high_end:
            p.append(f"FixturesCatalog: '{f.ref}' LowEnd {f.low_end}% is not below HighEnd {f.high_end}%.")

    if p:
        raise ValidationError(p)
    return s


def fade_raw(seconds: int) -> int:
    return FADE_RAW_CONFIRMED.get(seconds, FADE_RAW_CONFIRMED[DEFAULT_FADE_SECONDS])


def read_reference(path: str, key_col: str, value_col: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in _rows(path):
        try:
            out[row[key_col]] = int(row[value_col])
        except (KeyError, ValueError):
            continue
    return out
