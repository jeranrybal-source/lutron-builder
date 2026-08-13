"""Reading a lighting plan out of an AutoCAD drawing.

The strings and coordinates here are the lighting designer's, off the House B
drawing. The drawing itself is a client file and is not in this repository.

This is the weakest of the three ways in, and most of what is tested is
therefore what it refuses to claim: which text is not a room, which counts it
will not stand behind, and the fact that it never invents a fitting.
"""
from hwwriter import dwgplan, sheets

# Room labels, legend headings, title-block text and plan annotations, exactly
# as they come off the drawing -- they are indistinguishable by shape.
TEXTS = [
    ("FAMILY ROOM CH:2.4/2.6M", 40483.0, 85551.0),
    ("KITCHEN CH:2.6M", 31128.0, 86599.0),
    ("GROUND FLOOR", 36000.0, 90000.0),
    ("CEILING RECESSED", 5000.0, 60000.0),        # a legend heading
    ("ALEX FRY", 4000.0, 59000.0),                # the designer, in the title block
    ("GROUND FLOOR LIGHTING PLAN", 36000.0, 91000.0),
    ("TO LIGHT GLAZED KITCHEN CUPBOARD", 79580.0, 74711.0),
    ("THIS IS NOT TO SCALE", 4000.0, 58000.0),
    ("C1", 40500.0, 85400.0),
    ("C1", 40600.0, 85300.0),
    ("C2/M", 40700.0, 85200.0),
    ("C2/W/HC", 40800.0, 85100.0),
    ("C1/W x2", 31200.0, 86500.0),
    ("C4/M/HC", 31300.0, 86400.0),
]


def read_like_a_drawing(texts=None):
    found = texts if texts is not None else TEXTS
    labels = [(m, x, y) for body, x, y in found
              if (m := dwgplan.LABEL.match(body))]
    return dwgplan.rooms_in(found, labels), labels


# ---------------------------------------------------------- what is a room

def test_a_legend_heading_is_not_a_room():
    """'CEILING RECESSED' is a fine room name in every respect but one."""
    (rooms, _), _ = read_like_a_drawing()
    names = {name for name, _, _ in rooms}
    assert "CEILING RECESSED" not in names
    assert "FAMILY ROOM" in names and "KITCHEN" in names


def test_the_designers_name_in_the_title_block_is_not_a_room():
    """Nothing about 'ALEX FRY' reads differently from 'TV ROOM'.

    What separates them is that no circuit label is anywhere near it, which is
    a test that needs no list of words and works on a practice nobody has seen.
    """
    (rooms, _), _ = read_like_a_drawing()
    assert "ALEX FRY" not in {name for name, _, _ in rooms}


def test_a_sheet_title_and_an_annotation_are_not_rooms():
    (rooms, _), _ = read_like_a_drawing()
    names = {name for name, _, _ in rooms}
    assert "GROUND FLOOR LIGHTING PLAN" not in names
    assert "TO LIGHT GLAZED KITCHEN CUPBOARD" not in names
    assert "THIS IS NOT TO SCALE" not in names


def test_a_storey_is_kept_as_a_floor_and_not_as_a_room():
    (rooms, floors), _ = read_like_a_drawing()
    assert "GROUND FLOOR" not in {name for name, _, _ in rooms}
    assert "GROUND FLOOR" in {name for name, _, _ in floors}


# ------------------------------------------------------------ the labels

def test_the_label_carries_a_circuit_a_beam_and_sometimes_a_multiplier():
    match = dwgplan.LABEL.match("C2/W/HC")
    assert match.group("ref") == "C2" and match.group("times") is None
    match = dwgplan.LABEL.match("C1/W x2")
    assert match.group("ref") == "C1" and match.group("times") == "2"
    assert dwgplan.LABEL.match("CH:2.4M") is None


def test_a_multiplier_counts_as_that_many_fittings():
    """'C1/W x2' is one label and two fittings; counting labels loses one."""
    (rooms, _), labels = read_like_a_drawing()
    counts = {}
    for match, x, y in labels:
        room, _ = dwgplan.nearest(rooms, x, y)
        key = (room, match.group("ref").upper())
        counts[key] = counts.get(key, 0) + int(match.group("times") or 1)
    assert counts[("KITCHEN", "C1")] == 2


# ------------------------------------------- what it will not stand behind

def test_a_label_between_two_rooms_is_reported_as_doubtful():
    rooms = [("KITCHEN", 0.0, 0.0), ("UTILITY", 100.0, 0.0)]
    _, sure = dwgplan.nearest(rooms, 48.0, 0.0)     # almost exactly between
    assert sure is False
    _, sure = dwgplan.nearest(rooms, 2.0, 0.0)      # unmistakably the kitchen
    assert sure is True


def test_mojibake_is_dropped_rather_than_used():
    """A string that parses is not a string that is right."""
    assert dwgplan.readable("FAMILY ROOM") is True
    assert dwgplan.readable("吀刀䤀䌀䤀䄀一 吀伀 匀伀唀刀䌀䔀") is False


def test_autocad_formatting_codes_are_stripped():
    assert dwgplan.clean("{\\fArial|b1|i0|c0|p34;FAMILY ROOM}") == "FAMILY ROOM"


# ------------------------------------------------------------- the output

def _plan():
    (rooms, floors), labels = read_like_a_drawing()
    circuits, doubtful = {}, {}
    for match, x, y in labels:
        room, sure = dwgplan.nearest(rooms, x, y)
        if not sure:
            doubtful[room] = doubtful.get(room, 0) + 1
        entry = circuits.setdefault((room, match.group("ref").upper()),
                                    {"count": 0, "beams": set()})
        entry["count"] += int(match.group("times") or 1)
    return {"rooms": sorted({r for r, _, _ in rooms}), "circuits": circuits,
            "annotations": {("FAMILY ROOM", "C2"): "IN CORNER PROFILE"},
            "doubtful": doubtful, "labels": len(labels),
            "floors": {"FAMILY ROOM": "GROUND FLOOR"}}


def test_a_circuit_the_drawing_does_not_name_says_so_in_the_cell():
    """A plausible invented name would be believed; this one cannot be."""
    rows = dwgplan.to_rows(_plan())
    descriptions = [r[2] for r in rows[1:]]
    assert "Circuit C1 -- needs a name" in descriptions
    # Where the designer annotated it, his own words win.
    assert "In Corner Profile" in descriptions


def test_no_fitting_and_no_wattage_are_ever_invented():
    rows = dwgplan.to_rows(_plan())
    for row in rows[1:]:
        assert row[4] == "" and row[5] == ""      # fitting type, description
        assert row[9] == "" and row[10] == ""     # wattage, watts per metre


def test_the_rows_go_through_the_spreadsheet_importer_unchanged():
    """One import path, so there is only one place the counts can go wrong."""
    rows = dwgplan.to_rows(_plan())
    mapping = sheets.guess_mapping(rows[0])
    assert sheets.missing_required(mapping) == []
    files, report, counts = sheets.convert(rows, 0, mapping)
    assert counts["circuits"] == len(rows) - 1
    # Every circuit is unbound, because a drawing names no fitting. That is
    # reported rather than papered over.
    assert counts["unbound"] == counts["circuits"]
    assert any("name no fitting" in line for line in report)


def test_the_floor_comes_from_the_banner_over_the_plan():
    rows = dwgplan.to_rows(_plan())
    family = [r for r in rows[1:] if r[1] == "FAMILY ROOM"]
    assert family and all(r[0] == "GROUND FLOOR" for r in family)


def test_the_report_says_what_cannot_be_trusted():
    lines = dwgplan.report(_plan())
    text = " ".join(lines)
    assert "needs a name" in text
    assert "No fitting and no wattage" in text


def test_the_room_warning_is_earned_rather_than_printed_every_time():
    """A warning on every job is a warning nobody reads by the third one."""
    plan = _plan()
    assert plan["doubtful"] == {}
    assert not any("no room boundaries" in line for line in dwgplan.report(plan))
    plan["doubtful"] = {"KITCHEN": 6}
    warning = [line for line in dwgplan.report(plan) if "no room boundaries" in line]
    assert len(warning) == 1
    assert "KITCHEN (6)" in warning[0]


# ------------------------------------------- the record left in the folder

def _report(**kw):
    rows = dwgplan.to_rows(_plan())
    mapping = sheets.guess_mapping(rows[0])
    _, notes, counts = sheets.convert(rows, 0, mapping)
    return sheets.report_text("plan.dwg", "Read from the drawing", 0, rows[0],
                              mapping, counts, notes, True, False, **kw)


def test_the_report_does_not_call_a_drawing_a_spreadsheet():
    """Six months on, this file is the only thing that says where these came from."""
    text = _report(from_drawing=True, drawing_notes=["A drawing has no room boundaries."])
    assert "READ FROM AN AUTOCAD DRAWING" in text
    assert "SPREADSHEET" not in text
    assert "circuit labels" in text


def test_the_drawings_warnings_are_written_into_the_report():
    text = _report(from_drawing=True,
                   drawing_notes=["A drawing has no room boundaries, so each "
                                  "label is given to the nearest room name."])
    lines = text.splitlines()
    start = lines.index("WHAT THE DRAWING COULD NOT TELL US")
    section = lines[start + 1:lines.index("", start)]
    assert any("no room boundaries" in line for line in section)
    # Folded, so it reads in a plain text window rather than as one long line.
    assert max(len(line) for line in section) < 80


def test_a_spreadsheet_still_says_spreadsheet():
    text = _report()
    assert "READ FROM A SPREADSHEET" in text
    assert "WHAT THE DRAWING COULD NOT TELL US" not in text


# ------------------------------------------------------- refusing to start

def test_it_says_why_it_cannot_run_rather_than_failing_oddly(monkeypatch):
    monkeypatch.setattr(dwgplan.sys, "version_info", (3, 9, 6))
    assert dwgplan.available() is False
    try:
        dwgplan._require()
    except dwgplan.PlanError as exc:
        assert "3.10" in str(exc)
        assert "Lutron Builder.exe" in str(exc)
    else:                                          # pragma: no cover
        raise AssertionError("it should have refused")
