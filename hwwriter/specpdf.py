"""Read a designer's specification table out of a ruled PDF.

Some practices issue the circuit schedule as a PDF rather than a spreadsheet,
and it is the same document either way: a room, a circuit reference, the
fittings on it, a wattage each and a count. the lighting designer's House B
specification is the worked example -- `AREA | CIRCUIT NUMBER | FITTING |
FINISH | L REF | LOAD | QTY | TOTAL LOAD | VOLTAGE | DIM | SUPPLIER | INFO` --
and where a set arrives like that, the reading problem is already solved by the
designer. Nothing needs inferring and no model needs asking.

The difficulty is only that a PDF has no cells. It has text at coordinates and
a drawing of a table around it, and the two are not connected. Reading it as
plain text loses the connection entirely: a fitting whose description wraps
onto three lines arrives as three lines, its circuit reference sits vertically
centred beside the middle one, and there is no longer anything to say which
row anything belonged to.

So this module rebuilds the cells from the table's own ruling. The borders are
drawn as very thin rectangles, so the vertical ones give the column boundaries
and the horizontal ones give the rows, and every piece of text then falls into
exactly one cell. That is the designer's own grid rather than a guess at it,
and it is why a wrapped description reassembles correctly.

Two consequences worth stating:

*It only works on a table that is actually ruled.* A specification laid out
with whitespace has no rectangles to read and this module refuses it by name
rather than returning a plausible mess.

*The geometry is shared by every page.* The columns are measured once and
agree across all four House B pages to a tenth of a point, which matters
because only page 1 carries the headings.

pypdf is the only thing needed beyond the standard library, and it is already
what the text-only read uses. The writer itself stays dependency-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ----------------------------------------------------------------- geometry

# A content stream is postfix: operands, then the operator that consumes them.
# Only four operators matter here -- `re` draws a rectangle, and q/Q/cm move
# the coordinate system a rectangle is drawn in.
_TOKEN = re.compile(rb"""
      (?P<num>-?\d*\.?\d+)
    | (?P<str>\((?:[^()\\]|\\.|\\\n)*\))
    | (?P<op>[A-Za-z'"*]+)
    | (?P<skip><[0-9A-Fa-f\s]*>|[<>\[\]{}/])
""", re.VERBOSE)

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

_ESCAPES = {ord("n"): "\n", ord("r"): "\r", ord("t"): "\t", ord("b"): "\b",
            ord("f"): "\f", ord("("): "(", ord(")"): ")", ord("\\"): "\\"}


def _literal(raw: bytes) -> str:
    """The characters of a PDF string literal, escapes resolved.

    Latin-1, which is right for the WinAnsi encoding these documents use and
    wrong for a font with its own. That case is caught by comparing the whole
    page against pypdf's reading of it rather than by guessing here.
    """
    body, out, i = raw[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch != 0x5C:                      # backslash
            out.append(chr(ch))
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        nxt = body[i]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 1
        elif 0x30 <= nxt <= 0x37:           # octal, up to three digits
            digits = ""
            while i < len(body) and len(digits) < 3 and 0x30 <= body[i] <= 0x37:
                digits += chr(body[i])
                i += 1
            out.append(chr(int(digits, 8)))
        elif nxt in (0x0A, 0x0D):           # a line broken inside a string
            i += 1
            if i < len(body) and body[i] == 0x0A and nxt == 0x0D:
                i += 1
        else:
            out.append(chr(nxt))
            i += 1
    return "".join(out)


class SpecError(Exception):
    """The PDF is not a ruled specification table, or cannot be read as one."""


def _multiply(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def walk(data: bytes) -> tuple:
    """(rectangles, text positions) from a content stream, in page coordinates.

    The transformation stack is tracked because a rectangle drawn inside a
    scaled block is not where its own four numbers say it is -- House B's
    page 1 has a logo placed that way, and taking the numbers at face value
    puts a phantom column through the middle of the table.

    Text positions are read here rather than taken from pypdf's own reader,
    which reports the text matrix without the offsets applied to it and so
    returns (0, 0) for most runs on this document. The positions are what
    decide which cell a word belongs to, so they have to be right.
    """
    ctm, stack, operands, strings = _IDENTITY, [], [], []
    rects, texts = [], []
    pending = None
    for match in _TOKEN.finditer(data):
        number = match.group("num")
        if number is not None:
            operands.append(float(number))
            continue
        if match.group("str") is not None:
            strings.append(_literal(match.group("str")))
            continue
        if match.group("op") is None:
            operands = []
            continue
        op = match.group("op").decode("latin-1")
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            if stack:
                ctm = stack.pop()
        elif op == "cm" and len(operands) >= 6:
            ctm = _multiply(tuple(operands[-6:]), ctm)
        elif op == "BT":
            pending = None
        elif op in ("Tm", "Td", "TD") and pending is None:
            if op == "Tm" and len(operands) >= 6:
                a, b, c, d, e, f = _multiply(tuple(operands[-6:]), ctm)
                pending = (e, f)
            elif len(operands) >= 2:
                a, b, c, d, e, f = ctm
                x, y = operands[-2:]
                pending = (a * x + c * y + e, b * x + d * y + f)
        elif op in ("Tj", "TJ", "'", '"'):
            body = "".join(strings)
            if body.strip() and pending:
                texts.append((pending[0], pending[1], body))
            strings = []
        elif op == "ET":
            pending = None
        elif op == "re" and len(operands) >= 4:
            x, y, w, h = operands[-4:]
            a, b, c, d, e, f = ctm
            xs, ys = [], []
            for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                xs.append(a * px + c * py + e)
                ys.append(b * px + d * py + f)
            rects.append((min(xs), min(ys), max(xs), max(ys)))
        operands = []
    return rects, texts


def _cluster(values: list, tolerance: float = 2.5) -> list:
    """Collapse edge positions that are the same rule drawn twice."""
    out: list = []
    for v in sorted(values):
        if out and v - out[-1] <= tolerance:
            continue
        out.append(v)
    return out


# A border is thinner than this, and longer than that. Both are generous: the
# point is only to tell a rule apart from a cell, and the two are orders of
# magnitude apart.
_THIN = 2.5
_LONG = 6.0


def grid(rects: list) -> tuple:
    """(column boundaries, row boundaries) read from the table's own borders.

    The rules are thin rectangles, which is exactly what a filter looking for
    cells throws away -- so this deliberately keeps the slivers and discards
    nothing else.
    """
    verticals, horizontals = [], []
    for x0, y0, x1, y1 in rects:
        width, height = x1 - x0, y1 - y0
        if width <= _THIN and height >= _LONG:
            verticals.append((x0 + x1) / 2)
        if height <= _THIN and width >= _LONG:
            horizontals.append((y0 + y1) / 2)
    return _cluster(verticals), _cluster(horizontals)


def _slot(edges: list, value: float) -> int:
    """Which band of `edges` a coordinate falls in, or -1 outside them all."""
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return -1


def page_rows(page) -> list:
    """The rows of one PDF page's ruled table, each a list of cell strings.

    Text is placed by its own baseline, so a run is assigned to the row whose
    band contains that baseline nudged upward by a point -- a character sitting
    exactly on a rule belongs to the cell above it, not below.

    Both the words and their positions are read from the page's own drawing
    instructions, so a word cannot drift away from the place it was drawn.
    """
    rects, texts = walk(page.get_contents().get_data())
    columns, rows = grid(rects)
    if len(columns) < 2 or len(rows) < 2:
        return []

    cells: dict = {}
    for x, y, text in texts:
        body = text.strip()
        if not body:
            continue
        column, row = _slot(columns, x), _slot(rows, y + 1.0)
        if column < 0 or row < 0:
            continue
        cells.setdefault((row, column), []).append((round(y, 1), x, body))

    out = []
    for row in range(len(rows) - 1):
        line = []
        for column in range(len(columns) - 1):
            runs = cells.get((row, column), [])
            # Top line first, then left to right, which is how the cell reads.
            runs.sort(key=lambda r: (-r[0], r[1]))
            line.append(" ".join(r[2] for r in runs).strip())
        out.append(line)
    # The rules run bottom-up in PDF space; a document reads top-down.
    out.reverse()
    return out


def table_rows(path: str) -> list:
    """Every row of the specification table, in document order.

    Raises rather than returning something plausible: a specification this
    cannot read is a specification the engineer must be told about.
    """
    try:
        import pypdf
    except ImportError as exc:                        # pragma: no cover - env
        raise SpecError(
            "Reading a specification PDF needs the pypdf library: pip install pypdf"
        ) from exc
    try:
        reader = pypdf.PdfReader(path)
    except Exception as exc:                          # noqa: BLE001 - surfaced below
        raise SpecError(f"{path} could not be opened as a PDF ({exc}).") from exc
    out = []
    for page in reader.pages:
        out.extend(page_rows(page))
    if not out:
        raise SpecError(
            "No ruled table was found in this PDF. This reader rebuilds the "
            "designer's own cells from the lines the table is drawn with, so a "
            "specification laid out with spaces instead of a real table cannot "
            "be read this way -- send it as a normal plan read instead.")
    return out


# -------------------------------------------------------------- the table

HEADINGS = ["AREA", "CIRCUIT NUMBER", "FITTING", "FINISH", "L REF", "LOAD",
            "QTY", "TOTAL LOAD", "VOLTAGE", "DIM", "SUPPLIER", "INFO"]

# Order matters only in that every one of these is checked; a description
# matching any of them is hardware that makes a fitting work, not a fitting.
# Counting a driver as a fitting doubles a circuit's count and its load, and
# the numbers stay plausible while doing it -- which is why this is explicit
# and conservative rather than a search for the word "driver" anywhere.
_ACCESSORY = re.compile(
    r"""^\s*(
          (dali|on/?off|mains|phase|elv|leading[- ]edge|trailing[- ]edge)?\s*drivers?\b
        | drivers?\s+for\b
        | transformers?\b
        | installation\s+sleeves?\b
        | remote\s+(driver|transformer)s?\b
        | power\s+supply\s+unit\b | psu\b
    )""", re.IGNORECASE | re.VERBOSE)

# "Supply for joinery lighting" is a circuit the designer is asking for without
# specifying what hangs on it. It is a real Lutron zone and must be built; it
# simply has no wattage, and that blank is reported rather than filled.
_SUPPLY = re.compile(r"^\s*supp(ly|lies)\s+for\b", re.IGNORECASE)

_FLOOR = re.compile(
    r"""^\s*(
          (lower|upper)\s+ground(\s+floor)?
        | ground(\s+floor)?
        | basement | sub[- ]?basement
        | (first|second|third|fourth|1st|2nd|3rd|4th)\s+floor
        | mezzanine | attic | loft(\s+floor)?
        | roof(\s+terrace)? | garden\s+level
    )\s*$""", re.IGNORECASE | re.VERBOSE)

_CONTROL = re.compile(r"\bcontrol\s*\d*\s*$", re.IGNORECASE)
_DOC_NOTE = re.compile(r"^\s*(supplier\s+codes|notes)\b", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def _to_number(text: str):
    """The number in a cell, or None.

    `tbc` is a real entry in this document and it means the designer has not
    decided. None carries that through to a blank, which is flagged; any
    number chosen here would be indistinguishable from one he wrote.
    """
    text = (text or "").strip()
    if not text or re.search(r"\d\s*[x*]\s*\d", text, re.IGNORECASE):
        return None
    match = _NUMBER.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


@dataclass
class Line:
    """One line of a circuit: a fitting, or the hardware that drives it."""

    description: str = ""
    finish: str = ""
    lref: str = ""
    load_w: float = None
    qty: float = None
    total_w: float = None
    voltage: str = ""
    dim: str = ""
    supplier: str = ""
    info: str = ""
    kind: str = "fitting"          # fitting | accessory | supply

    @property
    def is_fitting(self) -> bool:
        return self.kind in ("fitting", "supply")


@dataclass
class Circuit:
    ref: str = ""                  # "C1", "CUPBOARDS", or "" where he gave none
    room: str = ""
    floor: str = ""
    lines: list = field(default_factory=list)
    zone_name: str = ""            # filled in by zonename.py
    named_by: str = ""

    @property
    def fittings(self) -> list:
        return [ln for ln in self.lines if ln.is_fitting]

    @property
    def accessories(self) -> list:
        return [ln for ln in self.lines if ln.kind == "accessory"]

    @property
    def quantity(self):
        """Fittings on the circuit, or None if any line failed to give a count.

        None rather than a partial sum: a circuit counted from three of its
        four lines is wrong in a way that looks right.
        """
        counts = [ln.qty for ln in self.fittings]
        if not counts or any(c is None for c in counts):
            return None
        return sum(counts)

    @property
    def total_watts(self):
        watts = [ln.total_w for ln in self.fittings]
        if not watts or any(w is None for w in watts):
            return None
        return sum(watts)


@dataclass
class Spec:
    circuits: list = field(default_factory=list)
    rooms: list = field(default_factory=list)          # (floor, room)
    control_notes: dict = field(default_factory=dict)  # room -> note
    document_notes: list = field(default_factory=list)
    problems: list = field(default_factory=list)


def _classify_banner(text: str, current_room: str) -> str:
    """What a row carrying only a left-hand heading is announcing."""
    if _DOC_NOTE.match(text):
        return "document"
    if _FLOOR.match(text):
        return "floor"
    if _CONTROL.search(text):
        return "control"
    # He repeats the room's own name above its control note as often as he
    # writes "<ROOM> CONTROL", so an exact repeat is the same banner.
    if current_room and text.strip().upper() == current_room.strip().upper():
        return "control"
    return "unknown"


def parse_rows(rows: list) -> Spec:
    """Turn the table's cells into rooms, circuits and fittings.

    Everything here is reading, not inference. Where a cell is empty the value
    stays None and is reported; where a row cannot be classified at all it is
    reported too, rather than being quietly dropped -- a specification this
    silently skips half of would still produce a schedule that builds.
    """
    spec = Spec()
    floor = room = ""
    circuit = None
    seen_rooms = set()
    in_document_notes = expect_note = False

    for index, raw in enumerate(rows):
        cells = [(c or "").strip() for c in raw]
        if len(cells) < len(HEADINGS):
            cells += [""] * (len(HEADINGS) - len(cells))
        area, ref, description = cells[0], cells[1], cells[2]

        if not any(cells):
            continue
        # The headings themselves, and any repeat of them at a page break.
        if area.upper() == "AREA" and cells[1].upper().startswith("CIRCUIT"):
            continue

        # A banner -- a floor, or a room's control note -- is written across a
        # merged cell, and merged cells centre their text. So the words land
        # in whichever column happens to sit under the middle of the page
        # rather than in the area column, and the row has to be recognised by
        # what it says rather than by where it says it.
        filled = [c for c in cells if c]
        banner = (area or filled[0]) if len(filled) == 1 else ""
        kind = _classify_banner(banner, room) if banner else ""
        # Text alone in a column other than the area column is only a banner if
        # it reads like one. Anything else there is far more likely to be a
        # fitting whose figures the designer left blank, and treating that as a
        # heading would lose a circuit silently.
        if kind == "unknown" and not area:
            banner, kind = "", ""
        if banner:
            area = banner
            if kind == "floor":
                floor, in_document_notes, expect_note = area, False, False
            elif kind == "control":
                in_document_notes = False
                # "<ROOM> CONTROL" is a heading; what it is announcing is on
                # the row below it.
                expect_note = True
                spec.control_notes.setdefault(room, "")
            elif kind == "document":
                in_document_notes, expect_note = True, False
                spec.document_notes.append(area)
            elif in_document_notes:
                # The note blocks at the end of the document run on across
                # several banner rows.
                spec.document_notes.append(area)
            elif expect_note:
                spec.control_notes[room] = area
                expect_note = False
            else:
                spec.problems.append(
                    f"Row {index + 1}: '{area}' is on its own and could not be read "
                    f"as a floor, a control note or a room. It has been ignored -- "
                    f"check the specification.")
            continue

        # A room's control is described the way it is provided: "By Lutron - by
        # others" on most rooms, "Door operated switch" with a count and a
        # supplier on the wardrobe. Both follow the CONTROL heading, and
        # neither names a fitting -- which is what tells them apart from the
        # start of the next room.
        if expect_note and area and not description:
            spec.control_notes[room] = area
            expect_note = False
            continue
        expect_note = False

        if area:
            room, in_document_notes = area, False
            if (floor, room) not in seen_rooms:
                seen_rooms.add((floor, room))
                spec.rooms.append((floor, room))
            circuit = None

        if not room:
            spec.problems.append(
                f"Row {index + 1}: '{description[:40]}' appears before any room is "
                f"named, so there is nothing to attach it to. It has been ignored.")
            continue

        # A new reference opens a circuit. A blank one continues the circuit
        # above it -- that is how he writes a second fitting on the same
        # circuit, and it is also how he writes its driver.
        if ref or circuit is None:
            circuit = Circuit(ref=ref, room=room, floor=floor)
            spec.circuits.append(circuit)

        if not description:
            spec.problems.append(
                f"Row {index + 1}: circuit {ref or circuit.ref or '?'} in {room} has "
                f"figures but names no fitting. It has been ignored.")
            continue

        kind = "fitting"
        if _ACCESSORY.match(description):
            kind = "accessory"
        elif _SUPPLY.match(description):
            kind = "supply"
        circuit.lines.append(Line(
            description=description, finish=cells[3], lref=cells[4],
            load_w=_to_number(cells[5]), qty=_to_number(cells[6]),
            total_w=_to_number(cells[7]), voltage=cells[8], dim=cells[9],
            supplier=cells[10], info=cells[11], kind=kind))

    spec.circuits = [c for c in spec.circuits if c.lines]
    return spec


_PROSE = re.compile(r"^(by|all|door|to be|see|please)\b", re.IGNORECASE)


def _looks_like_prose(text: str) -> bool:
    """A sentence in the area column is a note, not a room."""
    return bool(_PROSE.match(text)) or len(text.split()) > 8


def arithmetic_problems(spec: Spec) -> list:
    """Where the designer's own LOAD x QTY does not equal his TOTAL LOAD.

    This is free and it is worth having: it catches a mis-read column and a
    typed-in total with the same check, and it needs no second source.
    """
    out = []
    for circuit in spec.circuits:
        for line in circuit.fittings:
            if None in (line.load_w, line.qty, line.total_w):
                continue
            expected = line.load_w * line.qty
            if abs(expected - line.total_w) > 0.5:
                out.append(
                    f"{circuit.room} {circuit.ref}: {line.description[:40]} is "
                    f"{_plain(line.load_w)}W x {_plain(line.qty)} = "
                    f"{_plain(expected)}W, but the specification totals it as "
                    f"{_plain(line.total_w)}W.")
    return out


def lref_problems(spec: Spec) -> list:
    """Where one legend code is used for two different fittings.

    The L REF is what ties a label on the plan back to a row in this table, so
    a code meaning two things makes that tie ambiguous -- and it is invisible
    unless something looks for it.
    """
    meanings: dict = {}
    for circuit in spec.circuits:
        for line in circuit.fittings:
            if not line.lref:
                continue
            meanings.setdefault(line.lref, {}).setdefault(
                _family(line.description), line.description)
    out = []
    for lref, families in sorted(meanings.items()):
        if len(families) > 1:
            names = ", ".join(sorted(f"'{v}'" for v in families.values()))
            out.append(f"L REF {lref} is used for {len(families)} different "
                       f"fittings: {names}. A plan label reading {lref} cannot say "
                       f"which is meant.")
    return out


def _family(description: str) -> str:
    """The product, with its beam and variant stripped, for comparison."""
    text = re.sub(r"\s*[-,]?\s*\d+\s*deg\b.*$", "", description, flags=re.IGNORECASE)
    text = re.sub(r"\s*/\s*hc\b.*$", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip().lower()


def _plain(value) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value) == int(value) else f"{value:g}"


def read(path: str) -> Spec:
    """Read a specification PDF into rooms, circuits and fittings."""
    return parse_rows(table_rows(path))
