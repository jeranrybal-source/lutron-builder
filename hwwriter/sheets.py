"""Read a designer's own load schedule out of a CSV or an Excel workbook.

Sets sometimes arrive with a good spreadsheet of the loads -- a room, a circuit
description, a fitting type, a count. Where that exists it is the best data
this tool will ever get: it is the designer's own figures, it is exact, it
costs nothing, and it skips the reading step entirely. The whole difficulty is
that there is no standard. Every practice names its columns differently, puts
its header row in a different place, and merges the room cell down a group of
circuits. So this module does three things and stops:

    read the file  ->  find the header row  ->  guess what each column means

and then hands a mapping to a human to correct, because a wrong column silently
produces a plausible schedule for the wrong house.

Deliberately standard library only. The writer has to run on a machine that has
Lutron Designer and nothing else, and an .xlsx is a zip of XML -- reading the
cells out of one is a hundred lines, which is cheaper than a dependency in the
shipped exe.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from xml.etree import ElementTree

# The one owner of what a written dimming range looks like ("5-90"). The
# schedule reader parses it on the way out, so the importer must accept
# exactly the same form on the way in -- a second pattern here would be a
# second opinion that could disagree with it.
from .schedule import _WRITTEN_RANGE

# The old binary .xls is a compound-document format with no reasonable stdlib
# reader. Saying so is far better than a wall of mojibake.
LEGACY_XLS = ("That is the old binary Excel format (.xls). Open it in Excel or "
              "Numbers and save it as .xlsx or .csv, then choose it again.")


class SheetError(Exception):
    """Anything that stops the file being read at all."""


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ------------------------------------------------------------------ reading

def _decode(data: bytes) -> str:
    """Text out of bytes, without ever failing.

    Designers' exports come out of Excel on Windows far more often than not, so
    the realistic candidates are UTF-8 (with or without a BOM) and cp1252. A
    pound sign or a degree symbol in cp1252 is invalid UTF-8, and refusing the
    whole file over one character in a Notes column would be absurd.
    """
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _sniff_delimiter(text: str) -> str:
    """Comma, semicolon or tab.

    Excel on a machine with a European locale writes semicolons, and a schedule
    pasted out of a table is often tabs. Counting on the first few lines beats
    csv.Sniffer here, which guesses from punctuation inside the values and has
    picked a space before now.
    """
    head = "\n".join(text.splitlines()[:20])
    counts = {d: head.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def _read_csv(data: bytes) -> list[list[str]]:
    text = _decode(data)
    delim = _sniff_delimiter(text)
    return [[(c or "").strip() for c in row]
            for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out = []
    for si in ElementTree.fromstring(raw):
        # A styled cell splits its text across several <r> runs, so the string
        # is every <t> underneath joined -- taking only the first would return
        # "Circuit" for a cell reading "Circuit Description".
        out.append("".join(t.text or "" for t in si.iter()
                           if _localname(t.tag) == "t"))
    return out


def _col_index(ref: str) -> int:
    """'C7' -> 2. Cells are addressed, not positional: a row that skips an
    empty column emits no <c> for it, so reading them in order would shift
    every later value one column to the left."""
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return 0
    n = 0
    for ch in letters.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _number(text: str) -> str:
    """Trim Excel's float noise: 12.0 -> 12, 9.600000000000001 -> 9.6."""
    try:
        val = float(text)
    except (TypeError, ValueError):
        return text
    if val == int(val) and abs(val) < 1e15:
        return str(int(val))
    return f"{round(val, 6):g}"


def _read_sheet_xml(raw: bytes, strings: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in ElementTree.fromstring(raw).iter():
        if _localname(row.tag) != "row":
            continue
        cells: dict[int, str] = {}
        for c in row:
            if _localname(c.tag) != "c":
                continue
            kind = c.get("t")
            value = ""
            for child in c:
                name = _localname(child.tag)
                if name == "v":
                    value = child.text or ""
                elif name == "is":     # inline string
                    value = "".join(t.text or "" for t in child.iter()
                                    if _localname(t.tag) == "t")
            if kind == "s":
                try:
                    value = strings[int(value)]
                except (ValueError, IndexError):
                    value = ""
            elif kind in (None, "n"):
                value = _number(value)
            cells[_col_index(c.get("r") or "")] = value.strip()
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def _sheet_parts(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(sheet name, part path) in the workbook's own tab order.

    The order matters: a workbook whose first tab is a cover sheet and whose
    second is the schedule should offer them in that order, because that is
    what the user is looking at in Excel.
    """
    rels: dict[str, str] = {}
    try:
        for rel in ElementTree.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
            rels[rel.get("Id") or ""] = rel.get("Target") or ""
    except KeyError:
        pass
    out = []
    for el in ElementTree.fromstring(zf.read("xl/workbook.xml")).iter():
        if _localname(el.tag) != "sheet":
            continue
        name = el.get("name") or "Sheet"
        rid = next((v for k, v in el.attrib.items() if _localname(k) == "id"), "")
        target = rels.get(rid, "")
        if not target:
            continue
        target = target.lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
        if path in zf.namelist():
            out.append((name, path))
    return out


def _read_xlsx(data: bytes) -> list[tuple[str, list[list[str]]]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SheetError("That file is not a readable .xlsx workbook.") from None
    with zf:
        strings = _shared_strings(zf)
        parts = _sheet_parts(zf)
        if not parts:
            raise SheetError("That workbook has no worksheets in it.")
        return [(name, _read_sheet_xml(zf.read(path), strings)) for name, path in parts]


def read_tables(filename: str, data: bytes) -> list[tuple[str, list[list[str]]]]:
    """Every table in the file, as (sheet name, rows of text cells).

    A CSV is one table named after the file. A workbook is one per worksheet,
    in tab order -- which sheet holds the schedule is the user's call, because
    a workbook routinely carries a cover sheet, a revision log and three
    schedules and only one of them is the loads.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" or ext == ".xls":
        raise SheetError(LEGACY_XLS)
    if data[:2] == b"PK" or ext in (".xlsx", ".xlsm"):
        return [(name, rows) for name, rows in _read_xlsx(data)]
    if ext in (".csv", ".tsv", ".txt", ""):
        return [(os.path.basename(filename) or "Sheet 1", _read_csv(data))]
    raise SheetError(f"Cannot read a {ext} file. Use .csv or .xlsx.")


# ----------------------------------------------------------- header finding

def _looks_like_header(cells: list[str]) -> int:
    """How strongly a row reads as column headings. Higher is better."""
    filled = [c for c in cells if c.strip()]
    if len(filled) < 2:
        return -1
    score = 0
    for cell in filled:
        if _match_field(cell):
            score += 4              # a heading we recognise is the best signal
        if re.fullmatch(r"[-+]?\d[\d,.]*", cell.strip()):
            score -= 2              # a number is data, not a heading
        if len(cell) <= 40:
            score += 1
    return score


def find_header(rows: list[list[str]]) -> int:
    """Which row holds the column headings.

    Never assume row 1. Real schedules open with a practice name, a project
    title, a revision block and a blank line, and picking row 1 there maps
    every column to the wrong thing while looking like it worked. Only the
    first 30 rows are considered -- past that it is not a header, it is a
    second table further down the sheet.
    """
    best, best_score = 0, -2
    for i, row in enumerate(rows[:30]):
        score = _looks_like_header(row)
        # A header has data under it. A bold title line above a blank row does
        # not, and this is what separates them.
        if score > 0 and not any(any(c.strip() for c in r) for r in rows[i + 1:i + 4]):
            score -= 5
        if score > best_score:
            best, best_score = i, score
    return best


# ------------------------------------------------------------------ mapping

# What the schedule needs, in the order the mapping screen shows it. `synonyms`
# are matched against a squashed form of the heading (letters and digits only,
# lowercased), so "Load Type", "load-type" and "LOADTYPE" are one thing.
#
# These lists are the whole point of the feature and they will never be
# finished: every new practice brings a name nobody thought of. They are
# ordinary data, added to as jobs arrive.
FIELDS = [
    {"key": "floor", "label": "Floor",
     "help": "The storey. Becomes the parent area in Designer.",
     "synonyms": ["floor", "level", "storey", "story", "floorlevel", "levelfloor"]},
    {"key": "room", "label": "Room", "required": True,
     "help": "The room the circuit is in. Every circuit needs one.",
     "synonyms": ["room", "area", "roomname", "areaname", "location", "space",
                  "roomarea", "arearoom", "roomlocation", "description1"]},
    {"key": "circuit", "label": "Circuit description", "required": True,
     "help": "What the circuit is -- 'Ceiling Downlights', 'Island Pendants'.",
     "synonyms": ["circuit", "circuitdescription", "zone", "zonename", "load",
                  "loaddescription", "circuitname", "lightingcircuit",
                  "descriptionofload", "zonedescription", "loadname", "channel",
                  "circuitdetail", "loaddetail"]},
    {"key": "circuit_number", "label": "Circuit number",
     "help": "The designer's own circuit reference, if the sheet carries one. "
             "Letters are fine — C1, 2a — it is kept as written; Designer "
             "numbers circuits itself.",
     "synonyms": ["circuitno", "circuitnumber", "ckt", "cktno", "zoneno",
                  "zonenumber", "channelno", "no", "ref", "circuitref"]},
    {"key": "fixture_ref", "label": "Fitting type / reference",
     "help": "The legend code -- 'A', 'F4', 'DL-01'. Ties the circuit to a fitting.",
     "synonyms": ["type", "fittingtype", "fixturetype", "luminairetype",
                  "fixtureref", "fittingref", "typeref", "catref", "code",
                  "fittingcode", "typecode", "item", "itemref", "luminaireref",
                  "fixturecode", "lighttype"]},
    {"key": "fixture_description", "label": "Fitting description",
     "help": "What the fitting is -- 'recessed downlight', 'table lamp circuit'. "
             "This is what decides the assumed dimming type.",
     "synonyms": ["fitting", "luminaire", "fixture", "fittingdescription",
                  "fixturedescription", "luminairedescription", "product",
                  "productdescription", "description", "details", "fittingdetails",
                  "lightfitting"]},
    {"key": "manufacturer", "label": "Manufacturer",
     "help": "Optional. Recorded on the fitting so it can be checked.",
     "synonyms": ["manufacturer", "make", "brand", "supplier", "manuf", "mfr"]},
    {"key": "model", "label": "Model / product code",
     "help": "Optional. Recorded on the fitting so it can be checked.",
     "synonyms": ["model", "modelno", "productcode", "partnumber", "partno",
                  "catno", "catalogueno", "catalogno", "ordercode", "sku",
                  "modelnumber", "productref"]},
    {"key": "quantity", "label": "Number of fittings",
     "help": "How many fittings are on the circuit. Left blank it is flagged, never guessed.",
     "synonyms": ["qty", "quantity", "no", "nr", "number", "count", "nooff",
                  "numberof", "nooffittings", "numberoffittings", "qtyoff",
                  "fittings", "noofluminaires", "quantityoff"]},
    {"key": "wattage", "label": "Wattage",
     "help": "The wattage. Say below whether it is per fitting or the circuit total.",
     "synonyms": ["w", "watt", "watts", "wattage", "power", "load", "loadw",
                  "loadwatts", "circuitload", "va", "powerw", "wattagew",
                  "totalload", "connectedload", "loading"]},
    {"key": "wattage_per_m", "label": "Watts per metre",
     "help": "For tape and profile, which is specified per metre, not per fitting.",
     "synonyms": ["wm", "wperm", "wattspermetre", "wattspermeter", "wattagem",
                  "wpermetre", "wmetre", "wattsm"]},
    {"key": "run_length", "label": "Run length (metres)",
     "help": "For tape and profile. Metres x W/m is the load.",
     "synonyms": ["length", "runlength", "metres", "meters", "m", "lengthm",
                  "linearmetres", "runm", "lineallength", "runlengthm"]},
    {"key": "load_type", "label": "Dimming / control type",
     # These strings are rendered into the page, so they carry the app's own
     # punctuation rather than the source's "--".
     "help": "DALI, mains, 0-10V, switched. Left unmapped, the fitting decides — "
             "DALI for architectural fittings, mains reverse phase for lamps.",
     "synonyms": ["dimming", "dimmingtype", "dimtype", "control", "controltype",
                  "driver", "drivertype", "loadtype", "protocol", "dimmingmethod",
                  "controlprotocol", "dimmingprotocol", "type2", "switching"]},
    # Added on James's catch (2026-08-12): the template had nowhere to put the
    # trims, so every spreadsheet import landed on the flagged 5%/90% default.
    # "range" alone is deliberately NOT a synonym -- on a lighting schedule a
    # "Range" column is as likely to be the product range as the trims, and a
    # column of product names read as trims is exactly the kind of believable
    # wrong output this importer exists to refuse.
    {"key": "dim_range", "label": "Dimming range",
     "help": "The trims, low to high, written like 5-90. Left blank, 5%/90% is "
             "assumed and flagged — never guessed.",
     "synonyms": ["dimmingrange", "dimrange", "trims", "trim", "dimmingtrims",
                  "trimrange", "dimminglevels", "trimspct", "dimmingrangepct"]},
    {"key": "notes", "label": "Notes",
     "help": "Carried through onto the circuit so nothing in the sheet is lost.",
     "synonyms": ["notes", "note", "comments", "comment", "remarks", "remark",
                  "commentsnotes"]},
]

FIELD_KEYS = [f["key"] for f in FIELDS]
REQUIRED = [f["key"] for f in FIELDS if f.get("required")]

# The heading to WRITE for each field, as opposed to `label`, which is the
# heading to SHOW a person. They are not the same thing and confusing them is a
# real defect, not a tidiness one: `label` is prose, and two of them --
# "Model / product code" and "Dimming / control type" -- are read by
# `guess_mapping` as the FITTING CODE, because of the words "code" and "type".
# A joined sheet headed with labels therefore had its fitting-code column
# stolen and every other column left unmapped (found in a browser, 2026-08-12).
#
# Every entry here is asserted by a test to round-trip through `guess_mapping`
# back to its own key. This is the one source of the vocabulary: the blank
# template's sheets are built from it too, so the words a designer is given are
# by construction the words the importer knows.
CANONICAL_HEADINGS = {
    "floor": "Floor",
    "room": "Room",
    "circuit": "Circuit description",
    "circuit_number": "Circuit number",
    "fixture_ref": "Fitting code",
    "fixture_description": "Fitting description",
    "manufacturer": "Manufacturer",
    "model": "Model",
    "quantity": "Number of fittings",
    "wattage": "Wattage",
    "wattage_per_m": "Watts per metre",
    "run_length": "Run length (m)",
    "load_type": "Dimming type",
    "dim_range": "Dimming range",
    "notes": "Notes",
}


def squash(text: str) -> str:
    """'No. of Fittings' -> 'nooffittings'; 'Load (W)' -> 'loadw'.

    The bracketed unit is KEPT, and that is the whole reason this exists.
    Dropping it makes 'Load (W)' identical to 'Load' -- and 'Load' is a
    perfectly normal heading for the circuit DESCRIPTION on a UK schedule. A
    sheet headed 'Room, Description, Load (W), Qty' then maps its circuit
    column to the watts, which reads a column of numbers as circuit names and
    loses every wattage, while producing a schedule that looks fine.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _match_field(heading: str) -> str | None:
    key = squash(heading)
    if not key:
        return None
    for field in FIELDS:
        if key in field["synonyms"]:
            return field["key"]
    return None


def guess_mapping(headings: list[str]) -> dict[str, int]:
    """{field key: column index} for everything recognised.

    Exact synonym matches first, across every column, before any looser rule
    gets a turn -- otherwise a "Load Description" column can be claimed as the
    wattage by a substring rule before the column actually called "Load (W)"
    is even reached.
    """
    taken: set[int] = set()
    out: dict[str, int] = {}
    for i, heading in enumerate(headings):
        key = _match_field(heading)
        if key and key not in out:
            out[key] = i
            taken.add(i)
    for i, heading in enumerate(headings):
        if i in taken:
            continue
        squashed = squash(heading)
        if not squashed:
            continue
        for field in FIELDS:
            if field["key"] in out:
                continue
            # Loose: the heading STARTS with a synonym, or a synonym of four or
            # more characters appears inside it. The length floor is what stops
            # "w" and "m" matching half the sheet.
            if any(squashed.startswith(s) or (len(s) >= 4 and s in squashed)
                   for s in field["synonyms"]):
                out[field["key"]] = i
                taken.add(i)
                break
    return out


# ------------------------------------------------- two sheets, joined by code

# The fields that describe a FITTING rather than a circuit. These are the ones
# that used to be retyped on every circuit using that fitting.
FITTING_FIELDS = ("fixture_description", "manufacturer", "model",
                  "wattage", "wattage_per_m", "load_type", "dim_range")


def looks_like_fittings(mapping: dict[str, int]) -> bool:
    """A sheet of fitting TYPES: a code, something describing it, and no room."""
    return ("fixture_ref" in mapping and "room" not in mapping
            and any(f in mapping for f in FITTING_FIELDS))


def looks_like_zones(mapping: dict[str, int]) -> bool:
    """A sheet of CIRCUITS: a room, what the circuit is, and a fitting code."""
    return all(k in mapping for k in ("room", "circuit", "fixture_ref"))


def combine(zone_rows: list, zone_header: int, zone_mapping: dict,
            fitting_rows: list, fitting_header: int,
            fitting_mapping: dict) -> tuple:
    """Join a fittings sheet onto a zones sheet by the fitting code.

    Returns (rows, mapping) in the one-table shape `convert` already takes, so
    the two-sheet template rides the same tested conversion as everything else
    rather than growing a second path through it.

    Why the split exists at all (James, 2026-08-12): *"the way that you complete
    fittings in Lutron is that you add your fittings first and then you allocate
    your fittings to a zone"* — so the sheet is shaped the way the work is done,
    and a fitting's description, make and wattage are written once however many
    circuits use it.
    """
    def cell(row, mapping, key):
        i = mapping.get(key)
        return (row[i] or "").strip() if i is not None and i < len(row) else ""

    fittings: dict = {}
    for raw in fitting_rows[fitting_header + 1:]:
        ref = cell(raw, fitting_mapping, "fixture_ref")
        if not ref or ref in fittings:
            continue
        fittings[ref] = {f: cell(raw, fitting_mapping, f) for f in FITTING_FIELDS}

    # Canonical headings, never labels -- see CANONICAL_HEADINGS. The joined
    # sheet is re-read by guess_mapping like any other, including whenever the
    # user moves the header row, so its words have to be words it knows.
    header = [CANONICAL_HEADINGS[key] for key in FIELD_KEYS]
    mapping = {key: i for i, key in enumerate(FIELD_KEYS)}
    rows = [header]
    used: set = set()
    unknown: set = set()

    for raw in zone_rows[zone_header + 1:]:
        if not any((c or "").strip() for c in raw):
            continue
        ref = cell(raw, zone_mapping, "fixture_ref")
        known = fittings.get(ref)
        if ref:
            (used if known else unknown).add(ref)
        out = []
        for key in FIELD_KEYS:
            # The zone sheet wins where it says anything at all — a per-circuit
            # override is legitimate (the same fitting switched in one room and
            # dimmed in another) and must not be overwritten by the type row.
            value = cell(raw, zone_mapping, key)
            if not value and known and key in FITTING_FIELDS:
                value = known.get(key, "")
            out.append(value)
        rows.append(out)

    notes = []
    if unknown:
        notes.append(
            f"{len(unknown)} fitting code(s) used on the zones sheet are not on "
            f"the fittings sheet ({', '.join(sorted(unknown)[:6])}"
            f"{'...' if len(unknown) > 6 else ''}). Those circuits still build, "
            f"but with no description and no wattage -- so their load is unknown "
            f"and is flagged, not guessed. Check for a typo in the code.")
    spare = sorted(set(fittings) - used)
    if spare:
        notes.append(
            f"{len(spare)} fitting(s) on the fittings sheet are used by no "
            f"circuit ({', '.join(spare[:6])}{'...' if len(spare) > 6 else ''}). "
            f"They are left out of the catalogue. That is fine if you listed "
            f"spares; it is a typo if you meant to use them.")
    return rows, mapping, notes


JOINED_SHEET_NAME = "Fittings + Zones (joined)"


def detect_pair(tables: list) -> dict:
    """Find a fittings sheet and a zones sheet in a workbook and join them.

    Returns None unless exactly the two shapes are present -- anything else is
    an ordinary workbook and takes the ordinary path. Deliberately conservative:
    a wrong join is a schedule for a house nobody designed, which is the failure
    this whole importer is built to refuse.
    """
    found: dict = {}
    for index, (_, rows) in enumerate(tables):
        header = find_header(rows)
        headings = rows[header] if header < len(rows) else []
        mapping = guess_mapping(headings)
        if looks_like_zones(mapping):
            found.setdefault("zones", (index, rows, header, mapping))
        elif looks_like_fittings(mapping):
            found.setdefault("fittings", (index, rows, header, mapping))
    if "zones" not in found or "fittings" not in found:
        return None

    _, zone_rows, zone_header, zone_mapping = found["zones"]
    _, fit_rows, fit_header, fit_mapping = found["fittings"]
    rows, _, notes = combine(zone_rows, zone_header, zone_mapping,
                             fit_rows, fit_header, fit_mapping)
    if len(rows) < 2:
        return None      # nothing filled in yet -- a blank template
    return {"table": (JOINED_SHEET_NAME, rows), "notes": notes,
            "index": len(tables)}


def missing_required(mapping: dict[str, int]) -> list[str]:
    """Required fields with no column, as their labels."""
    labels = {f["key"]: f["label"] for f in FIELDS}
    return [labels[k] for k in REQUIRED if mapping.get(k) is None]


def mapping_problems(mapping: dict[str, int]) -> list[str]:
    """Everything that stops this mapping producing a usable schedule."""
    out = [f"No column is mapped to {label}." for label in missing_required(mapping)]
    if mapping.get("fixture_ref") is None and mapping.get("fixture_description") is None:
        # Every circuit must name a fitting -- a circuit without one cannot be
        # built at all, and a whole schedule of them produces a Lutron file
        # with the house missing from it. Better to say so here than to write
        # 300 unbuildable rows and let the build explain it.
        out.append("Map either 'Fitting type / reference' or 'Fitting description' -- "
                   "a circuit that names no fitting cannot be built.")
    counts: dict[int, list[str]] = {}
    for key, col in mapping.items():
        if col is not None:
            counts.setdefault(col, []).append(key)
    labels = {f["key"]: f["label"] for f in FIELDS}
    for keys in counts.values():
        if len(keys) > 1:
            out.append("One column is mapped to more than one thing: "
                       + " and ".join(labels[k] for k in keys) + ".")
    return out


# ------------------------------------------------------- text -> load types

# What a designer writes in a "Dimming" column, and the Lutron load type it
# means. Ordered: the first phrase found in the cell wins, so the longer and
# more specific phrases come first. Anything not in here is left BLANK and
# reported, never guessed -- a wrong dimming type is how a dimmer is destroyed.
_LOAD_TYPE_WORDS = [
    ("dali emergency", 41), ("dali", 16), ("dsi", 110),
    ("ecosystem", 116), ("eco system", 116),
    ("0-10", 112), ("0 10", 112), ("010v", 112), ("1-10", 112),
    ("0/10", 112), ("analogue", 112), ("analog", 112),
    ("dmx", 115),
    ("reverse phase", 119), ("trailing edge", 119), ("trailing-edge", 119),
    ("forward phase", 117), ("leading edge", 117), ("leading-edge", 117),
    ("mains dim", 119), ("phase dim", 119), ("mains", 119),
    ("incandescent", 1), ("halogen", 1), ("tungsten", 1),
    ("non-dim", 118), ("non dim", 118), ("nondim", 118), ("undimmed", 118),
    ("switched", 118), ("switch", 118), ("relay", 118), ("on/off", 118),
    ("contactor", 118),
]


def load_type_from_text(text: str) -> int | None:
    """The Lutron load type a dimming column's wording means, or None.

    None is a real answer and the safe one: an unrecognised word leaves the
    cell blank, which the schedule then defaults by what the FITTING is and
    marks as assumed on the review sheet. A number invented here would look
    exactly like one the designer specified.
    """
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not low:
        return None
    for phrase, load_type in _LOAD_TYPE_WORDS:
        if phrase in low:
            return load_type
    return None


# ------------------------------------------------------- rows -> schedules

HEADERS = {
    "Areas.csv": ["Action", "AreaName", "ParentArea", "StatedCircuits", "Notes"],
    "FixturesCatalog.csv": [
        "Action", "FixtureRef", "Description", "LoadTypeID", "LoadTypeName",
        "FixtureWattage_W", "Wattage_W_per_m", "LampQuantity", "LampWattage_W",
        "DimmingRange", "LowEnd_pct", "HighEnd_pct", "PhaseControl",
        "ManufacturerName", "ManufacturerModel", "DataSource", "Notes"],
    "LoadSchedule.csv": [
        "Action", "AreaName", "ZoneName", "ZoneNumber", "FixtureRef",
        "NumberOfFixtures", "RunLength_m", "AdditionalAreas", "LoadTypeID_Override",
        "LowEnd_pct_Override", "HighEnd_pct_Override", "Notes"],
    "Keypads.csv": ["Action", "KeypadName", "AreaName", "StationName", "DeviceName",
                    "KeypadModel", "LinkName", "AddressOnLink", "ButtonCount", "Notes"],
    "Buttons.csv": ["Action", "KeypadName", "ButtonNumber", "ButtonLabel", "ActionType",
                    "TargetArea", "TargetScene", "TargetZone", "Level_pct",
                    "Fade_seconds", "Delay_seconds", "Notes"],
    "Scenes.csv": ["Action", "AreaName", "SceneName", "SceneNumber", "ZoneName",
                   "CommandType", "Level_pct", "Fade_seconds", "Delay_seconds", "Notes"],
}

_TOTAL_ROW = re.compile(r"^\s*(sub[\s-]*)?total\b", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def _to_number(text: str) -> float | None:
    """The number in a cell, or None.

    Schedules write '12 no.', '9.6W/m', '~20', '3 x 8W' and '1,200'. Taking the
    FIRST number in the cell is right for all of those except the last kind,
    where it is the multiplier -- so a cell containing an 'x' between two
    numbers is refused outright rather than read as 3. Ambiguity here becomes a
    wrong load, and a blank is flagged where a wrong number is not.
    """
    text = (text or "").strip()
    if not text:
        return None
    if re.search(r"\d\s*[x×*]\s*\d", text, re.IGNORECASE):
        return None
    # A comma is a thousands separator here and a DECIMAL POINT in most of
    # Europe. Stripping them all read "24,5" as 245 -- a tenfold wattage, with
    # nothing on the review sheet to show for it, on the first schedule a
    # Dutch or German engineer opened (found 2026-08-13, before anyone else
    # had the tool). Decided per cell, and only where the shape is certain:
    #   both separators present -> the LAST one is the decimal
    #   1,200 / 1,200,000       -> grouping, strip
    #   24,5 or 24,55           -> decimal comma, no grouping has 1-2 digits
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        text = (text.replace(",", "") if dot > comma
                else text.replace(".", "").replace(",", "."))
    elif comma >= 0:
        if re.search(r"\d,\d{3}(?:\D|$)", text) or re.search(r"\d,\d{3},\d{3}", text):
            text = text.replace(",", "")
        elif re.search(r"\d,\d{1,2}(?:\D|$)", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    m = _NUMBER.search(text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if value == int(value) else f"{round(value, 3):g}"


def _csv_text(header: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def convert(rows: list[list[str]], header_row: int, mapping: dict[str, int],
            fill_down: bool = True,
            wattage_is_total: bool = False) -> tuple[dict[str, str], list[str], dict]:
    """Turn mapped spreadsheet rows into the six schedule CSVs.

    Returns (files, report lines, counts). Nothing here invents a value: where
    the sheet is silent the cell is written BLANK, which is what the rest of
    the tool already understands -- it defaults the blank so the file still
    builds, and marks it on the review sheet as assumed rather than read. That
    distinction is the whole point, and filling a gap in here to make the
    output look complete would destroy it.
    """
    report: list[str] = []
    notes_col = mapping.get("notes")

    def cell(row: list[str], key: str) -> str:
        i = mapping.get(key)
        if i is None or i >= len(row):
            return ""
        return (row[i] or "").strip()

    # ---- 1. the rows that are actually circuits
    data = rows[header_row + 1:]
    records: list[dict] = []
    blank = spacer = totals = repeated = 0
    last_room = last_floor = ""
    for raw in data:
        if not any((c or "").strip() for c in raw):
            blank += 1
            continue
        room, floor = cell(raw, "room"), cell(raw, "floor")
        circuit = cell(raw, "circuit")
        # Some sheets repeat their header at every page break.
        if _match_field(room) == "room" and _match_field(circuit) == "circuit":
            repeated += 1
            continue
        # A subtotal line writes "TOTAL" in whichever column happens to be on
        # the left -- the room, the floor, or the circuit -- and its figure in
        # the quantity column. Test all three, and test them BEFORE the room is
        # filled down, or the word is overwritten by the room above it and the
        # row's 25 fittings walk into the schedule as a circuit.
        if (_TOTAL_ROW.match(circuit)
                or (not circuit and (_TOTAL_ROW.match(room) or _TOTAL_ROW.match(floor)))):
            totals += 1
            continue
        if fill_down:
            # A merged room cell in Excel puts the name on the FIRST row of the
            # group and leaves the rest empty. Without this, every circuit but
            # the first in each room loses its room.
            room = room or last_room
            floor = floor or last_floor
        if room:
            last_room, last_floor = room, floor or last_floor
        if not room or not circuit:
            spacer += 1
            continue
        records.append({"room": room, "floor": floor, "circuit": circuit, "raw": raw})

    if not records:
        raise SheetError(
            "No circuits were found. Check that the header row is the right one "
            "and that the Room and Circuit columns are mapped to the right "
            "columns -- the preview above shows what is being read.")

    # ---- 2. areas, and which room names need their floor to tell them apart
    floors_of: dict[str, set[str]] = {}
    for rec in records:
        floors_of.setdefault(rec["room"], set()).add(rec["floor"])
    ambiguous = {room for room, fl in floors_of.items() if len(fl) > 1}

    def area_name(rec: dict) -> str:
        # A WC on three floors is an ordinary house. Where a name is unique
        # write it plainly; where it is not, every other file has to say which
        # one it means, and 'Floor > Room' is how the schedule does that.
        if rec["room"] in ambiguous and rec["floor"]:
            return f"{rec['floor']} > {rec['room']}"
        return rec["room"]

    areas: list[list[str]] = []
    seen_areas: set[tuple[str, str]] = set()
    for rec in records:
        key = (rec["room"], rec["floor"])
        if key in seen_areas:
            continue
        seen_areas.add(key)
        areas.append(["New", rec["room"], rec["floor"], "", ""])
    if ambiguous:
        report.append(f"{len(ambiguous)} room name(s) appear on more than one floor "
                      f"({', '.join(sorted(ambiguous)[:6])}"
                      f"{'...' if len(ambiguous) > 6 else ''}); their circuits are "
                      f"written as 'Floor > Room' so each names one room only.")
    # `is None`, never falsiness: the floor is very often column A, and index 0
    # is false. Written the short way this fired on every well-mapped sheet.
    if mapping.get("floor") is None:
        report.append("No Floor column was mapped, so every room sits on one unnamed "
                      "floor. Designer will want them on storeys -- either map a "
                      "floor column or set ParentArea in Areas.csv before building.")
    else:
        # A MAPPED floor column left blank used to say nothing at all, which is
        # the worse half of the same problem: the rooms arrive with no storey
        # and nothing tells you (James, 2026-08-12). Worse still, two rooms with
        # the same name on different floors cannot be told apart without it, so
        # they merge into one -- a WC on three floors becomes one WC, and two
        # thirds of its circuits move house.
        blank = sum(1 for rec in records if not rec["floor"])
        if blank:
            every = blank == len(records)
            report.append(
                f"{'Every circuit' if every else f'{blank} circuit(s)'} "
                f"had the Floor column left empty, so "
                f"{'those' if not every else 'their'} rooms are written with no "
                f"storey above them. Designer wants rooms on storeys, and two "
                f"rooms sharing a name on different floors cannot be told apart "
                f"without it -- they merge into one. Fill the floor in, or set "
                f"ParentArea in Areas.csv before building.")

    # ---- 3. the fitting catalogue
    fixtures: dict[str, dict] = {}
    minted = 0
    disagreed: set[str] = set()
    unrecognised_types: set[str] = set()
    unrecognised_ranges: set[str] = set()
    for rec in records:
        raw = rec["raw"]
        ref = cell(raw, "fixture_ref")
        desc = cell(raw, "fixture_description")
        if not ref:
            # A description with no code is still a fitting: give it its own
            # reference so the circuit binds, rather than dropping the circuit.
            ref = desc[:60].strip()
            if ref:
                minted += 1
        rec["ref"] = ref
        if not ref:
            continue
        qty = _to_number(cell(raw, "quantity"))
        watt = _to_number(cell(raw, "wattage"))
        per_fitting = watt
        derived = False
        if watt is not None and wattage_is_total:
            if qty:
                per_fitting, derived = watt / qty, True
            else:
                # A circuit total with no count divides by nothing. Blank and
                # flagged beats a per-fitting wattage that is really a circuit.
                per_fitting = None
        load_type = load_type_from_text(cell(raw, "load_type"))
        if mapping.get("load_type") is not None and load_type is None and cell(raw, "load_type"):
            unrecognised_types.add(cell(raw, "load_type"))
        # The trims, accepted only in the written form the schedule reader
        # itself parses ("5-90") -- one owner of the vocabulary, like the
        # dimming words. Anything else is left blank and reported, because a
        # trims cell written as prose becomes a defaulted fitting that LOOKS
        # deliberately trimmed.
        dim_range = cell(raw, "dim_range") or None
        if dim_range and not _WRITTEN_RANGE.match(dim_range):
            unrecognised_ranges.add(dim_range)
            dim_range = None
        entry = {
            "desc": desc or ref,
            "load_type": load_type,
            "watt": per_fitting,
            "per_m": _to_number(cell(raw, "wattage_per_m")),
            "dim_range": dim_range,
            "manufacturer": cell(raw, "manufacturer"),
            "model": cell(raw, "model"),
            "derived": derived,
        }
        if ref not in fixtures:
            fixtures[ref] = entry
        else:
            first = fixtures[ref]
            for field in ("watt", "per_m", "load_type", "dim_range"):
                if entry[field] is not None and first[field] is None:
                    first[field] = entry[field]      # a later row filling a gap
                elif (entry[field] is not None and first[field] is not None
                      and entry[field] != first[field]):
                    disagreed.add(ref)
            if not first["desc"] and entry["desc"]:
                first["desc"] = entry["desc"]

    if minted:
        report.append(f"{minted} circuit(s) had no fitting code, so the fitting's own "
                      f"description was used as its reference. They will read oddly in "
                      f"Designer but they bind, and the load is right.")
    if disagreed:
        report.append(f"{len(disagreed)} fitting(s) are given different wattages or "
                      f"dimming types on different rows "
                      f"({', '.join(sorted(disagreed)[:6])}"
                      f"{'...' if len(disagreed) > 6 else ''}). The FIRST row's figures "
                      f"were used for the catalogue. Check these against the schedule.")
    if unrecognised_types:
        report.append(f"{len(unrecognised_types)} dimming description(s) were not "
                      f"recognised ({', '.join(sorted(unrecognised_types)[:8])}"
                      f"{'...' if len(unrecognised_types) > 8 else ''}), so those "
                      f"fittings were left blank and are marked assumed on the review "
                      f"sheet rather than guessed at.")
    if unrecognised_ranges:
        report.append(f"{len(unrecognised_ranges)} dimming range(s) were not in the "
                      f"low-high form this reads, like 5-90 "
                      f"({', '.join(sorted(unrecognised_ranges)[:8])}"
                      f"{'...' if len(unrecognised_ranges) > 8 else ''}), so those "
                      f"fittings keep the flagged 5%/90% default rather than a "
                      f"misread trim.")

    fixture_rows = []
    for ref, f in fixtures.items():
        source_note = "designer's schedule" if (f["watt"] is not None
                                                or f["per_m"] is not None) else ""
        note = "Wattage divided from the circuit total by the count." if f["derived"] else ""
        fixture_rows.append([
            "New", ref, f["desc"],
            str(f["load_type"]) if f["load_type"] is not None else "",
            "", _fmt(f["watt"]), _fmt(f["per_m"]), "", "",
            f["dim_range"] or "", "", "", "",
            f["manufacturer"], f["model"], source_note, note])

    # ---- 4. the circuits
    load_rows = []
    used: dict[tuple[str, str], int] = {}
    renamed = 0
    unbound = 0
    no_count = 0
    for rec in records:
        raw = rec["raw"]
        area = area_name(rec)
        zone = rec["circuit"]
        number = cell(raw, "circuit_number")
        key = (area, zone)
        used[key] = used.get(key, 0) + 1
        if used[key] > 1:
            # Two circuits in one room called the same thing cannot both be
            # written -- the schedule keys a circuit on room + name, and a
            # scene or a button naming it would not know which it meant.
            zone = f"{zone} ({number})" if number else f"{zone} ({used[key]})"
            renamed += 1
        qty = _to_number(cell(raw, "quantity"))
        run = _to_number(cell(raw, "run_length"))
        # Tape and profile are measured in METRES, not fittings, so a run
        # length IS the count for that circuit and its load is metres x W/m.
        # Counting it as "no count" fired this warning on a correctly filled
        # template (James, 2026-08-12) — and a warning that goes off on correct
        # input is how people learn to ignore warnings.
        if qty is None and run is None:
            no_count += 1
        if not rec["ref"]:
            unbound += 1
        notes = (raw[notes_col].strip()
                 if notes_col is not None and notes_col < len(raw) else "")
        load_rows.append([
            "New", area, zone, number, rec["ref"], _fmt(qty),
            _fmt(_to_number(cell(raw, "run_length"))), "", "", "", "", notes])

    if renamed:
        report.append(f"{renamed} circuit(s) shared a room and a name with another, so "
                      f"they were given a suffix to tell them apart. Rename them to "
                      f"something an engineer would recognise before building.")
    if unbound:
        report.append(f"{unbound} circuit(s) name no fitting and will NOT be built. "
                      f"Fill in FixtureRef in LoadSchedule.csv, or add the fitting to "
                      f"FixturesCatalog.csv, for each one you want.")
    if no_count:
        report.append(f"{no_count} circuit(s) have no fitting count, so their load is "
                      f"unknown. That is flagged on the review sheet, not guessed -- "
                      f"circuit loading and panel sizing exclude them until you fill "
                      f"the counts in.")
    for n, what in ((blank, "nothing in them"), (spacer, "no room or no circuit"),
                    (totals, "a total in the circuit column"),
                    (repeated, "the column headings repeated")):
        if n:
            report.append(f"Skipped {n} row(s) with {what}.")

    files = {
        "Areas.csv": _csv_text(HEADERS["Areas.csv"], areas),
        "FixturesCatalog.csv": _csv_text(HEADERS["FixturesCatalog.csv"], fixture_rows),
        "LoadSchedule.csv": _csv_text(HEADERS["LoadSchedule.csv"], load_rows),
        # Written empty, with their headers. A spreadsheet of loads says nothing
        # about keypads or scenes, and an empty file is honest about that where
        # a missing one would be indistinguishable from a failed write.
        "Keypads.csv": _csv_text(HEADERS["Keypads.csv"], []),
        "Buttons.csv": _csv_text(HEADERS["Buttons.csv"], []),
        "Scenes.csv": _csv_text(HEADERS["Scenes.csv"], []),
    }
    counts = {"areas": len(areas), "fixtures": len(fixture_rows),
              "circuits": len(load_rows), "unbound": unbound, "no_count": no_count}
    return files, report, counts


def _wrap(text: str, width: int = 68) -> list:
    """Long warnings, folded so the report reads in a plain text window."""
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def report_text(source: str, sheet: str, header_row: int, headings: list[str],
                mapping: dict[str, int], counts: dict, notes: list[str],
                fill_down: bool, wattage_is_total: bool,
                from_drawing: bool = False, drawing_notes: list = None) -> str:
    """The report that travels with the import, in the same place a read's does.

    It exists so that six months later somebody can tell what this schedule was
    built from and what was decided for them, without the app being open. Which
    is exactly why it must not call a drawing a spreadsheet: the two are read
    by different code with very different confidence, and the whole value of
    this file is that it says which one produced these numbers.
    """
    origin = "AN AUTOCAD DRAWING" if from_drawing else "A SPREADSHEET"
    cells = ("drawing's own circuit labels" if from_drawing
             else "spreadsheet's own cells")
    lines = [
        f"READ FROM {origin}, NOT BY AI",
        "",
        f"Source file : {source}",
        f"Sheet       : {sheet}",
        f"Header row  : row {header_row + 1}",
        "",
        "No AI was involved and nothing was charged. Every value below came out",
        f"of the {cells}, or was left blank on purpose. A blank",
        "is defaulted so the file still builds and is marked on the review sheet",
        "as assumed -- check those against the drawings, as always.",
        "",
    ]
    if drawing_notes:
        lines += ["WHAT THE DRAWING COULD NOT TELL US"]
        for note in drawing_notes:
            folded = _wrap(note)
            lines.append(f"  * {folded[0]}")
            lines += [f"    {line}" for line in folded[1:]]
        lines.append("")
    lines.append("COLUMNS USED")
    for field in FIELDS:
        col = mapping.get(field["key"])
        where = f"column {col + 1} ({headings[col]!r})" if (
            col is not None and col < len(headings)) else "-- not mapped"
        lines.append(f"  {field['label']:<26} {where}")
    lines += [
        "",
        f"  Room repeated down merged cells : {'yes' if fill_down else 'no'}",
        f"  Wattage column read as          : "
        f"{'the circuit total' if wattage_is_total else 'one fitting'}",
        "",
        "WHAT CAME OUT",
        f"  {counts['areas']} room(s)",
        f"  {counts['fixtures']} fitting type(s)",
        f"  {counts['circuits']} circuit(s)",
        "",
        "NO KEYPADS AND NO SCENES",
        "A schedule of loads says nothing about keypads or scenes, so none were",
        "written. Add them in Designer, or read the plans with the AI to have",
        "them worked out.",
    ]
    if notes:
        lines += ["", "WHAT YOU SHOULD LOOK AT"]
        lines += [f"  * {n}" for n in notes]
    return "\n".join(lines) + "\n"
