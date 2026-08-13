"""The blank spreadsheet a designer or an engineer fills in.

The app already reads a schedule spreadsheet, and on a good job that is the
best data this tool will ever get. But it only helps where a schedule already
exists, and the reading half of the app exists precisely because the industry
has no standard -- which means half the time there is nothing to read. This is
the other half of that answer: rather than guess at whatever arrives, hand
somebody a sheet with the right columns already on it.

**The headings here are not chosen, they are derived.** Every one is taken from
`sheets.FIELDS` and checked against the importer's own synonym list, and a test
puts this template back through `guess_mapping` and `convert` and asserts that
every column is recognised and the worked example converts. So the template
cannot drift away from the reader that has to understand it -- which is the
whole failure mode a hand-written template would have, and it would show up as
a mapping screen full of "not used" on somebody else's machine.

The sheet ships EMPTY, with the worked example on the guidance page beside it
rather than in the data. An example row left in by accident is a circuit for a
room that does not exist, and this tool's entire promise is that nothing in the
output was invented. A blank sheet imported by mistake stops with "No circuits
were found", which is a good deal easier to understand than a phantom room.

Written with the standard library -- an .xlsx is a zip of XML, and the reader
in `sheets.py` is stdlib for the same reason. A dependency in the shipped exe
costs more than the hundred lines below.
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from .sheets import _LOAD_TYPE_WORDS, CANONICAL_HEADINGS, FIELDS, guess_mapping

FILENAME = "Lutron Builder - load schedule template.xlsx"

# TWO data sheets, in the order the work is actually done (James, 2026-08-12):
# **your fittings first, then allocate them to zones** — which is how it is done
# in Lutron Designer, and how this tool's own output is already shaped
# (`FixturesCatalog.csv` + `LoadSchedule.csv`).
#
# The first version of this template was ONE sheet with a row per circuit and
# the fitting's details on the same row. The importer tolerates that — repeat
# circuits need only the fitting code — but the sheet never said so, and every
# row of its worked example used a different fitting, so nobody could tell.
# James: *"you have to repeatedly fill in your fittings over and over again on
# the zone list."* Flattening into one sheet what the tool then splits back
# into two was the mistake; this undoes it.
#
# Every heading is still a word the importer's synonym list already knows, so
# both sheets come up mapped without a single dropdown being touched.

# Both sheets are built from `sheets.CANONICAL_HEADINGS` rather than typed out,
# so the words a designer is handed are by construction the words the importer
# recognises. Typing them here again is exactly how a template drifts.
FITTING_FIELD_ORDER = ["fixture_ref", "fixture_description", "manufacturer",
                       "model", "wattage", "wattage_per_m", "load_type",
                       "dim_range"]
ZONE_FIELD_ORDER = ["floor", "room", "circuit", "circuit_number", "fixture_ref",
                    "quantity", "run_length", "notes"]

FITTING_HEADINGS = [CANONICAL_HEADINGS[k] for k in FITTING_FIELD_ORDER]
ZONE_HEADINGS = [CANONICAL_HEADINGS[k] for k in ZONE_FIELD_ORDER]

# One row per fitting TYPE. Written once, however many circuits use it.
FITTING_EXAMPLE = [
    ["DL1", "Recessed downlight", "Phos", "Eyeconic Trimless Pro", "12", "", "DALI", "5-90"],
    ["P1", "Decorative pendant", "", "", "24", "", "Mains trailing edge", ""],
    ["T1", "LED tape in profile", "Superlites", "", "", "9.6", "DALI", ""],
    ["W1", "Decorative wall light", "", "", "8", "", "Switched", ""],
]

# One row per circuit. **DL1 appears three times and its details are never
# repeated** — that is the whole point of the split, so the example has to show
# it rather than describe it.
ZONE_EXAMPLE = [
    ["Ground Floor", "Kitchen", "Ceiling Downlights", "C1", "DL1", "14", "", "over the island"],
    ["Ground Floor", "Kitchen", "Island Pendants", "C2", "P1", "3", "", "client's own"],
    ["Ground Floor", "Kitchen", "Under Cupboard", "C3", "T1", "", "4.2", ""],
    ["Ground Floor", "Hall", "Ceiling Downlights", "C1", "DL1", "6", "", ""],
    ["First Floor", "Landing", "Ceiling Downlights", "C1", "DL1", "4", "", ""],
    ["First Floor", "Bedroom 1", "Bedside Reading Lights", "C1", "W1", "2", "", "locally switched"],
]

FITTING_WIDTHS = [13, 26, 14, 22, 10, 14, 19, 15]
ZONE_WIDTHS = [13, 18, 24, 13, 13, 17, 15, 26]

# The Dimming type dropdown (James, 2026-08-12 — "they should be dropdown menus
# to make it cleaner"). One entry per control type the importer understands,
# written the way an engineer says it rather than as a Lutron code.
#
# **Every one of these is asserted to survive `load_type_from_text` by a test.**
# A dropdown offering a word the importer then ignores would be the worst
# version of this feature: it looks authoritative, and the value lands blank
# and "assumed" without anyone typing anything wrong.
DIMMING_CHOICES = [
    "DALI",
    "DALI Emergency",
    "Mains trailing edge",
    "Mains leading edge",
    "0-10V",
    "DMX",
    "EcoSystem",
    "DSI",
    "Switched",
    "Incandescent",
]

# How many rows the dropdowns cover. Beyond this the columns still work, they
# just stop offering the list — which is why the guidance still names the words.
VALIDATED_ROWS = 400

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_DOC = "application/vnd.openxmlformats-officedocument.spreadsheetml"


def _column(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _cell(row: int, col: int, value: str, bold: bool = False) -> str:
    if value == "":
        return ""
    style = ' s="1"' if bold else ""
    return (f'<c r="{_column(col)}{row}"{style} t="inlineStr">'
            f'<is><t xml:space="preserve">{escape(str(value))}</t></is></c>')


def _sheet(rows: list, widths: list = None, bold_first: bool = True,
           validations: list = None) -> str:
    """One worksheet. `validations` are (cell range, formula) dropdown lists.

    A dropdown is advisory in the file format — the cell still accepts anything
    typed — so a reader that ignores data validation (Numbers, Google Sheets in
    places) degrades to a plain sheet rather than a broken one. That is the only
    reason it is safe to lean on them.
    """
    cols = ""
    if widths:
        cols = "<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>"
    body = []
    for r, values in enumerate(rows, start=1):
        cells = "".join(_cell(r, c, v, bold=bold_first and r == 1)
                        for c, v in enumerate(values))
        body.append(f'<row r="{r}">{cells}</row>')

    checks = ""
    if validations:
        # allowBlank: blank is a real answer everywhere in this tool — it means
        # "not stated", gets flagged, and must never be forced into a guess by
        # a validation rule.
        checks = f'<dataValidations count="{len(validations)}">' + "".join(
            f'<dataValidation type="list" allowBlank="1" showInputMessage="1" '
            f'showErrorMessage="0" sqref="{ref}">'
            f'<formula1>{escape(formula)}</formula1></dataValidation>'
            for ref, formula in validations) + "</dataValidations>"

    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<worksheet xmlns="{_MAIN}">{cols}'
            f'<sheetData>{"".join(body)}</sheetData>{checks}</worksheet>')


def _help_for(heading: str) -> str:
    """The importer's own words for the field this heading maps to.

    Looked up through `guess_mapping` rather than a hand-kept pairing, so a
    heading that stops being recognised cannot keep a friendly description
    beside it — the guidance would then describe a column the importer is
    quietly ignoring.
    """
    mapping = guess_mapping([heading])
    for key, index in mapping.items():
        if index == 0:
            field = next(f for f in FIELDS if f["key"] == key)
            return field["help"].replace("--", "—")
    raise AssertionError(f"the importer does not recognise the heading {heading!r}")


def _dimming_vocabulary() -> list:
    """[(what it becomes, the words that produce it)] — read out of the code.

    James, 2026-08-12: the guidance said "DALI, mains, 0-10V, switched" while
    the worked example used "Mains trailing edge". Both are accepted, but a
    reader cannot know that from a list of four words that does not contain the
    fifth. And the real trap is the other direction — a bare "LED" or "phase"
    is recognised by nothing, so it lands blank and is marked assumed. So the
    page now prints the ACTUAL vocabulary, grouped by what each word means,
    and it cannot drift from `_LOAD_TYPE_WORDS`.
    """
    from .schedule import load_type_name
    grouped: dict = {}
    for phrase, load_type in _LOAD_TYPE_WORDS:
        grouped.setdefault(load_type, []).append(phrase)
    ordered = sorted(grouped.items(), key=lambda kv: load_type_name(kv[0]))
    return [(load_type_name(lt), ", ".join(words)) for lt, words in ordered]


def _guidance() -> list:
    """The page beside the sheets: how they fit together, and a worked example."""
    rows = [
        ["How to fill this in"], [],
        ["Fill in the FITTINGS sheet first, then the ZONES sheet — the same "
         "order you would work in Designer."],
        ["On FITTINGS, one row per kind of fitting. Give each one a short code."],
        ["On ZONES, one row per circuit. Name the fitting by its CODE only — "
         "you never repeat its description, make or wattage."],
        ["A circuit is one thing you would switch or dim on its own."],
        [],
        ["Leave anything you do not know BLANK. It is flagged for you to check "
         "later; it is never filled in with a guess."],
        ["Room, Circuit description and Floor all need filling in. Room and "
         "Circuit description are refused without; Floor is accepted without "
         "but you will not want it — Designer puts rooms on storeys, and two "
         "rooms with the same name on different floors cannot be told apart "
         "without it, so they merge into one."],
        ["A count and a wattage are what make the load totals work."],
        [],
        ["FITTINGS sheet — what goes in each column"],
    ]
    for heading in FITTING_HEADINGS:
        rows.append([heading, _help_for(heading)])
    rows += [[], ["ZONES sheet — what goes in each column"]]
    for heading in ZONE_HEADINGS:
        rows.append([heading, _help_for(heading)])

    rows += [
        [],
        ["Dimming type — the words this understands"],
        ["Anything else is left blank and marked as assumed on the review "
         "sheet, rather than guessed at. 'LED' and 'phase' on their own mean "
         "nothing here — say which kind."],
        ["It becomes", "Write any of these"],
    ]
    rows += [[becomes, words] for becomes, words in _dimming_vocabulary()]

    rows += [
        [],
        ["Wattage is PER FITTING, not the circuit total."],
        ["If yours is the circuit total, say so on the screen after you choose "
         "the file — otherwise every load is out by the number of fittings on "
         "the circuit."],
        [],
        ["For LED tape and profile, put 'Watts per metre' on the fitting and "
         "'Run length (m)' on the circuit, and leave Wattage and the count "
         "blank."],
        [],
        ["An example, filled in"],
        ["Note DL1: written once on FITTINGS, used by three circuits."],
        [],
        ["FITTINGS"],
        list(FITTING_HEADINGS),
    ]
    rows += [list(r) for r in FITTING_EXAMPLE]
    rows += [[], ["ZONES"], list(ZONE_HEADINGS)]
    rows += [list(r) for r in ZONE_EXAMPLE]
    return rows


def _column_letter_of(headings: list, heading: str) -> str:
    return _column(headings.index(heading))


def workbook() -> bytes:
    """The template, as the bytes of an .xlsx file.

    Sheet order is deliberate and load-bearing: **Fittings, then Zones, then
    the guidance.** The app lands on the LAST sheet of a workbook by default
    ("the schedule is rarely the cover sheet"), which on the first version of
    this template meant it opened on the guidance page. Zones is the sheet a
    filled-in template is imported from, so the guidance cannot sit after it —
    and the server now names the sheet to open rather than leaving it to that
    heuristic.
    """
    dim_col = _column_letter_of(FITTING_HEADINGS, "Dimming type")
    code_col = _column_letter_of(ZONE_HEADINGS, "Fitting code")
    last = VALIDATED_ROWS

    fitting_checks = [(
        f"{dim_col}2:{dim_col}{last}",
        '"' + ",".join(DIMMING_CHOICES) + '"',
    )]
    # The zone's fitting code is chosen from the codes on the Fittings sheet,
    # so a circuit cannot name a fitting that was never described. Getting that
    # wrong is not cosmetic — it produces a circuit bound to nothing, which the
    # importer reports and refuses to build.
    zone_checks = [(
        f"{code_col}2:{code_col}{last}",
        f"Fittings!$A$2:$A${last}",
    )]

    sheets = [
        ("Fittings", _sheet([FITTING_HEADINGS], FITTING_WIDTHS,
                            validations=fitting_checks)),
        ("Zones", _sheet([ZONE_HEADINGS], ZONE_WIDTHS, validations=zone_checks)),
        ("How to fill this in", _sheet(_guidance(), [26, 78], False)),
    ]

    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="{_DOC}.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
    content_types = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Types xmlns="{_CT}">'
        f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        f'package.relationships+xml"/>'
        f'<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/xl/workbook.xml" ContentType="{_DOC}.sheet.main+xml"/>'
        f'<Override PartName="/xl/styles.xml" ContentType="{_DOC}.styles+xml"/>'
        f'{overrides}</Types>')

    root_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 f'<Relationships xmlns="{_PKG}">'
                 f'<Relationship Id="rId1" Type="{_RELS}/officeDocument" '
                 f'Target="xl/workbook.xml"/></Relationships>')

    sheet_tags = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (name, _) in enumerate(sheets, start=1))
    book = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<workbook xmlns="{_MAIN}" xmlns:r="{_RELS}">'
            f'<sheets>{sheet_tags}</sheets></workbook>')

    links = "".join(
        f'<Relationship Id="rId{i}" Type="{_RELS}/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
    book_rels = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 f'<Relationships xmlns="{_PKG}">{links}'
                 f'<Relationship Id="rId{len(sheets) + 1}" Type="{_RELS}/styles" '
                 f'Target="styles.xml"/></Relationships>')

    # Two fonts and two formats: plain, and bold for the heading row. Excel
    # wants the empty fills and borders present even when nothing uses them.
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<styleSheet xmlns="{_MAIN}">'
              f'<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              f'<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              f'<fills count="2"><fill><patternFill patternType="none"/></fill>'
              f'<fill><patternFill patternType="gray125"/></fill></fills>'
              f'<borders count="1"><border/></borders>'
              f'<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              f'<cellXfs count="2"><xf xfId="0"/>'
              f'<xf fontId="1" applyFont="1" xfId="0"/></cellXfs>'
              f'<cellStyles count="1">'
              f'<cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              f'</styleSheet>')

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", book)
        zf.writestr("xl/_rels/workbook.xml.rels", book_rels)
        zf.writestr("xl/styles.xml", styles)
        for i, (_, xml) in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buffer.getvalue()
