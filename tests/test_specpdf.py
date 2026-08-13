"""Reading a designer's specification table out of a ruled PDF.

The rows below are the shapes the lighting designer's House B specification
actually contains, transcribed cell for cell -- a circuit carrying two fittings
and a driver, a circuit named with a word instead of a number, a fitting with
no circuit at all, a supply with no fitting behind it, and the floor and
control banners that separate them. The specification itself is a client
document and is not in this repository, so these stand in for it.

What is tested is mostly refusal and provenance: what is counted as a fitting,
what is counted as hardware, what is left blank, and what is said about a row
that cannot be read at all.
"""
from hwwriter import specpdf

# AREA | CIRCUIT | FITTING | FINISH | L REF | LOAD | QTY | TOTAL | VOLTAGE | DIM | SUPPLIER | INFO
HOUSE_B_SPEC = [
    ["AREA", "CIRCUIT NUMBER", "FITTING", "FINISH", "L REF", "LOAD", "QTY",
     "TOTAL LOAD", "VOLTAGE", "DIM", "SUPPLIER", "INFO"],
    ["", "", "GROUND FLOOR", "", "", "", "", "", "", "", "", ""],
    ["FAMILY ROOM", "C1", "5 amp socket", "", "", "8", "7", "56", "", "Y", "", ""],
    ["", "", "5 amp floor socket", "", "", "8", "2", "16", "", "Y", "", ""],
    ["", "C2", "Eyeconic Trimless Pro RTRA 25deg", "black", "L1a", "12", "8",
     "96", "230v", "Y", "PH", "2700-1800k"],
    ["", "", "Eyeconic Trimless Pro RTRA 50deg/HC", "black", "L1d", "12", "2",
     "24", "230v", "Y", "PH", "2700-1800k"],
    ["", "", "Dali driver for above", "", "", "", "10", "", "", "", "PH", ""],
    ["", "C3", "LED strip for light to coffer (1 x11000mm) in corner profile (X)",
     "aluminium", "L6", "10", "30", "300", "24v", "Y", "SL", "2400K DETAIL 1"],
    ["", "", "Dali drivers for above", "", "", "", "tbc", "", "", "", "SL",
     "REMOTE DRIVER"],
    ["FAMILY ROOM CONTROL", "", "", "", "", "", "", "", "", "", "", ""],
    ["By Lutron - by others", "", "", "", "", "", "", "", "", "", "", ""],
    ["KITCHEN", "CUPBOARDS", "LED strip for light inside tall cupboards",
     "aluminium", "L6", "10", "10", "100", "24v", "Y", "SL", "2400K DETAIL 3"],
    ["", "", "On/off driver for above 30w", "", "", "", "2", "", "", "", "SL",
     "REMOTE DRIVER"],
    ["", "C1", "5 amp socket", "", "", "8", "1", "8", "", "Y", "", ""],
    ["", "", "Decorative wall light", "", "", "8", "3", "24", "230v", "Y",
     "client", "Height tbc"],
    ["KITCHEN CONTROL", "", "", "", "", "", "", "", "", "", "", ""],
    ["By Lutron - by others", "", "", "", "", "", "", "", "", "", "", ""],
    ["", "", "BASEMENT", "", "", "", "", "", "", "", "", ""],
    ["WINE CELLAR", "C1", "Supply for joinery lighting", "", "", "", "1", "",
     "230v", "Y", "", ""],
    ["WINE CELLAR CONTROL", "", "", "", "", "", "", "", "", "", "", ""],
    ["By Lutron - by others", "", "", "", "", "", "", "", "", "", "", ""],
    ["MASTER BEDROOM", "", "Decorative bedside reading lights", "", "", "1",
     "2", "2", "230v", "N", "client", "Locally switched"],
    ["", "C1", "Decorative pendant", "", "", "24", "1", "24", "230v", "Y",
     "client", ""],
    ["MASTER BEDROOM CONTROL", "", "", "", "", "", "", "", "", "", "", ""],
    ["Door operated switch", "", "", "", "", "", "1", "", "", "", "contractor", ""],
]


def parsed():
    return specpdf.parse_rows(HOUSE_B_SPEC)


# ------------------------------------------------------------------ shape

def test_rooms_carry_the_floor_banner_above_them():
    spec = parsed()
    assert spec.rooms == [
        ("GROUND FLOOR", "FAMILY ROOM"),
        ("GROUND FLOOR", "KITCHEN"),
        ("BASEMENT", "WINE CELLAR"),
        ("BASEMENT", "MASTER BEDROOM"),
    ]


def test_a_blank_circuit_reference_continues_the_circuit_above_it():
    """Two fittings on one circuit, which is how he writes a mixed group."""
    family = [c for c in parsed().circuits if c.room == "FAMILY ROOM"]
    assert [c.ref for c in family] == ["C1", "C2", "C3"]
    assert [ln.description for ln in family[0].fittings] == [
        "5 amp socket", "5 amp floor socket"]


def test_a_circuit_can_be_named_rather_than_numbered():
    """'CUPBOARDS' sits in the circuit column, not the room or the fitting."""
    refs = [c.ref for c in parsed().circuits if c.room == "KITCHEN"]
    assert refs == ["CUPBOARDS", "C1"]


def test_a_fitting_before_any_circuit_reference_still_gets_a_circuit():
    """The bedside lights are locally switched and he numbers them nothing."""
    bedroom = [c for c in parsed().circuits if c.room == "MASTER BEDROOM"]
    assert bedroom[0].ref == ""
    assert bedroom[0].fittings[0].dim == "N"


# --------------------------------------------------- fittings vs hardware

def test_a_driver_is_hardware_and_is_not_counted_as_a_fitting():
    """The count and the load both double if this is got wrong."""
    c2 = next(c for c in parsed().circuits if c.ref == "C2")
    assert [ln.description for ln in c2.accessories] == ["Dali driver for above"]
    assert c2.quantity == 10          # 8 + 2, NOT 8 + 2 + 10 drivers
    assert c2.total_watts == 120


def test_transformers_and_sleeves_are_hardware_too():
    rows = [["ROOM", "C1", "Mast Light", "", "L8", "7", "2", "14", "12v", "Y", "SL", ""],
            ["", "", "Transformer for above", "", "", "", "1", "", "", "", "SL", ""],
            ["", "", "Installation sleeve", "", "", "", "4", "", "", "", "PH", ""]]
    circuit = specpdf.parse_rows(rows).circuits[0]
    assert circuit.quantity == 2
    assert len(circuit.accessories) == 2


def test_a_fitting_whose_name_merely_contains_driver_is_still_a_fitting():
    rows = [["ROOM", "C1", "Driverless downlight", "", "L1", "9", "3", "27",
             "230v", "Y", "PH", ""]]
    circuit = specpdf.parse_rows(rows).circuits[0]
    assert circuit.accessories == []
    assert circuit.quantity == 3


def test_a_supply_is_a_circuit_with_no_fitting_and_no_wattage():
    """A real Lutron zone the designer has asked for and not specified."""
    cellar = next(c for c in parsed().circuits if c.room == "WINE CELLAR")
    assert cellar.fittings[0].kind == "supply"
    assert cellar.quantity == 1
    assert cellar.total_watts is None      # blank, and flagged -- never guessed


# ------------------------------------------------------------- never invent

def test_a_missing_count_makes_the_whole_circuit_count_unknown():
    """Three lines of four is wrong in a way that looks right."""
    rows = [["ROOM", "C1", "Downlight", "", "L1", "9", "4", "36", "230v", "Y", "", ""],
            ["", "", "Wall light", "", "L2", "8", "", "", "230v", "Y", "", ""]]
    assert specpdf.parse_rows(rows).circuits[0].quantity is None


def test_tbc_is_not_read_as_a_number():
    c3 = next(c for c in parsed().circuits if c.ref == "C3")
    assert c3.accessories[0].qty is None


def test_a_multiplied_cell_is_refused_rather_than_read_as_its_first_number():
    assert specpdf._to_number("3 x 8W") is None
    assert specpdf._to_number("12 no.") == 12
    assert specpdf._to_number("1,200") == 1200


# ------------------------------------------------------------ the banners

def test_control_notes_attach_to_the_room_they_follow():
    spec = parsed()
    assert spec.control_notes["FAMILY ROOM"] == "By Lutron - by others"
    # Not every room is on Lutron, and the wardrobe's switch is not a room.
    assert spec.control_notes["MASTER BEDROOM"] == "Door operated switch"
    assert "Door operated switch" not in [r for _, r in spec.rooms]


def test_an_unreadable_banner_is_reported_rather_than_dropped():
    rows = [["ROOM", "C1", "Downlight", "", "L1", "9", "4", "36", "230v", "Y", "", ""],
            ["MEZZANINE LOBBY AREA", "", "", "", "", "", "", "", "", "", "", ""]]
    spec = specpdf.parse_rows(rows)
    assert len(spec.problems) == 1
    assert "MEZZANINE LOBBY AREA" in spec.problems[0]


def test_text_alone_in_the_fitting_column_is_a_fitting_not_a_heading():
    """Losing this row would lose a circuit without saying so."""
    rows = [["ROOM", "C1", "Downlight", "", "", "", "", "", "", "", "", ""]]
    spec = specpdf.parse_rows(rows)
    assert spec.circuits[0].fittings[0].description == "Downlight"
    assert spec.problems == []


# ------------------------------------------------------------- free checks

def test_load_times_quantity_is_checked_against_the_designers_own_total():
    assert specpdf.arithmetic_problems(parsed()) == []
    rows = [["ROOM", "C1", "Downlight", "", "L1", "9", "4", "360", "230v", "Y", "", ""]]
    problems = specpdf.arithmetic_problems(specpdf.parse_rows(rows))
    assert len(problems) == 1
    assert "9W x 4 = 36W" in problems[0]
    assert "360W" in problems[0]


def test_one_legend_code_meaning_two_fittings_is_reported():
    """The L REF is what ties a label on the plan back to this table."""
    problems = specpdf.lref_problems(parsed())
    assert len(problems) == 1
    assert "L6" in problems[0]
    assert "Mast Light" not in problems[0]      # this fixture has strips only


def test_a_beam_variant_is_not_a_different_fitting():
    """25deg and 50deg/HC of one product share a legend family, not a code."""
    rows = [["ROOM", "C1", "Eyeconic Pro 25deg", "", "L1", "12", "2", "24",
             "230v", "Y", "", ""],
            ["", "C2", "Eyeconic Pro 50deg/HC", "", "L1", "12", "2", "24",
             "230v", "Y", "", ""]]
    assert specpdf.lref_problems(specpdf.parse_rows(rows)) == []


# ---------------------------------------------------------- the PDF itself

def test_a_string_literal_decodes_its_escapes():
    assert specpdf._literal(rb"(Family \(Room\))") == "Family (Room)"
    assert specpdf._literal(rb"(caf\351)") == "café"


def test_rectangles_are_placed_by_the_transform_in_force():
    """A rule drawn inside a scaled block is not where its numbers say."""
    plain = b"10 20 100 2 re"
    rects, _ = specpdf.walk(plain)
    assert rects == [(10.0, 20.0, 110.0, 22.0)]
    scaled = b"q 2 0 0 2 5 5 cm 10 20 100 2 re Q 10 20 100 2 re"
    rects, _ = specpdf.walk(scaled)
    assert rects[0] == (25.0, 45.0, 225.0, 49.0)
    assert rects[1] == (10.0, 20.0, 110.0, 22.0)


def test_the_grid_comes_from_the_thin_rules_not_the_cells():
    rules = [(10.0, 10.0, 10.5, 90.0),      # vertical
             (60.0, 10.0, 60.5, 90.0),      # vertical
             (10.0, 10.0, 60.0, 10.5),      # horizontal
             (10.0, 89.5, 60.0, 90.0),      # horizontal
             (11.0, 11.0, 59.0, 89.0)]      # a filled cell, which is neither
    columns, rows = specpdf.grid(rules)
    assert [round(c, 2) for c in columns] == [10.25, 60.25]
    assert [round(r, 2) for r in rows] == [10.25, 89.75]


def test_text_is_paired_with_the_place_it_was_drawn():
    stream = (b"BT 1 0 0 1 100 200 Tm [(Family)-5( )-6(Room)] TJ ET "
              b"BT 1 0 0 1 300 200 Tm (C2) Tj ET")
    _, texts = specpdf.walk(stream)
    assert texts == [(100.0, 200.0, "Family Room"), (300.0, 200.0, "C2")]
