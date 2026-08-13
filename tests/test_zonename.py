"""Naming a circuit from what the designer wrote about it.

The strings quoted here are the lighting designer's own, out of the House B
specification. What matters most in these tests is what is NOT claimed: a beam
angle is repeated, never interpreted, and two circuits that nothing separates
are said to be unresolved rather than given a purpose out of the air.
"""
from hwwriter import specpdf, zonename


def circuit(*descriptions):
    rows = []
    for i, description in enumerate(descriptions):
        rows.append(["ROOM" if not i else "", "C1" if not i else "",
                     description, "", "L1", "12", "2", "24", "230v", "Y", "", ""])
    return specpdf.parse_rows(rows).circuits[0]


def named(*descriptions):
    return zonename.name_circuit(circuit(*descriptions))


# --------------------------------------------- 1. he says what it is for

def test_a_stated_purpose_wins_outright():
    name, rule, _ = named(
        "LED strip for light to coffer (1 x11000mm, 1 x 7000mm) in corner profile (X)")
    assert name == "Coffer"
    assert rule == "purpose"


def test_a_directional_word_is_part_of_the_purpose():
    """'Above Cupboards' is a different circuit from 'Inside Cupboards'."""
    assert named("LED strip for light above cupboards (1 x6000mm) in narrow profile")[0] \
        == "Above Cupboards"


def test_dimensions_do_not_leak_into_the_name():
    assert named("LED strip for light inside tall cupboards 2 x 2000mm, (4 x 1500mm)")[0] \
        == "Inside Tall Cupboards"


def test_a_supply_is_named_exactly_as_he_asked_for_it():
    name, rule, _ = named("Supply for joinery lighting")
    assert (name, rule) == ("Joinery Lighting", "purpose")


# ------------------------------------------ 2. the fitting names the circuit

def test_a_fitting_that_describes_itself_needs_no_inference():
    for description, expected in (
            ("5 amp socket", "5A Sockets"),
            ("Decorative pendant", "Pendants"),
            ("Decorative picture light", "Picture Lights"),
            ("Decorative bedside reading lights", "Bedside Reading Lights"),
            ("Mast Light", "Mast Lights"),
            ("Decorative bathroom wall light", "Bathroom Wall Lights")):
        assert named(description)[:2] == (expected, "fitting"), description


def test_the_longest_matching_phrase_wins():
    """A bathroom wall light must not be read as a plain wall light."""
    assert named("Decorative bathroom wall light")[0] == "Bathroom Wall Lights"


# ---------------------------------------------------------- 3. the beam

def test_the_beam_is_repeated_and_never_interpreted():
    """'Accent' would be a claim about purpose, and it would be believed."""
    name, rule, _ = named("Eyeconic Trimless Pro RTRA 25deg")
    assert name == "Ceiling Downlights 25°"
    assert rule == "beam"
    assert "accent" not in name.lower()
    assert "wash" not in name.lower()


def test_honeycomb_is_carried_into_the_name():
    assert named("Eyeconic Trimless Pro RTRA 50deg/HC")[0] \
        == "Ceiling Downlights 50° Honeycomb"


def test_the_products_own_words_say_how_it_is_mounted():
    assert named("Occular Round Surface Plate Screw 30deg")[0] == "Surface Downlights 30°"
    assert named("Eyeconic Mini Trim Square Adjustable - 50deg")[0] \
        == "Adjustable Downlights 50°"


def test_one_fitting_at_two_beams_is_one_kind_of_fitting():
    """Otherwise half a specification reads as mixed when it is nothing of the sort."""
    name, _, note = named("Eyeconic Trimless Pro RTRA 25deg",
                          "Eyeconic Trimless Pro RTRA 50deg/HC")
    assert name == "Ceiling Downlights 25°/50° Honeycomb"
    assert note == ""


def test_one_family_written_at_two_levels_of_detail_is_one_family():
    name, _, note = named("Decorative bathroom wall light", "Decorative wall light")
    assert name == "Wall Lights"
    assert note == ""


# ------------------------------------------------- genuinely mixed circuits

def test_a_circuit_mixing_two_kinds_of_fitting_says_so():
    name, _, note = named("5 amp socket", "Decorative wall light")
    assert name == "5A Sockets & Wall Lights"
    assert "2 different kinds" in note


# ----------------------------------------------- 4. the honest admission

def test_a_fitting_nothing_can_be_read_from_keeps_its_product_name():
    name, rule, _ = named("Cazalla")
    assert (name, rule) == ("Cazalla", "product")


def test_two_circuits_nothing_separates_are_numbered_and_reported():
    """The Family Room case: same fitting, same beam, three groups."""
    rows = [
        ["FAMILY ROOM", "C2", "Eyeconic Trimless Pro RTRA 25deg", "", "L1a",
         "12", "8", "96", "230v", "Y", "", ""],
        ["", "", "Eyeconic Trimless Pro RTRA 50deg/HC", "", "L1d", "12", "2",
         "24", "230v", "Y", "", ""],
        ["", "C4", "Eyeconic Trimless Pro RTRA 25deg/HC", "", "L1b", "12", "4",
         "48", "230v", "Y", "", ""],
        ["", "C5", "Eyeconic Trimless Pro RTRA 25deg", "", "L1a", "12", "3",
         "36", "230v", "Y", "", ""],
        ["", "", "Eyeconic Trimless Pro RTRA 50deg/HC", "", "L1d", "12", "2",
         "24", "230v", "Y", "", ""]]
    spec = specpdf.parse_rows(rows)
    problems = zonename.apply(spec)
    names = {c.ref: c.zone_name for c in spec.circuits}
    # C4 is separated by its beam. C2 and C5 are not separated by anything.
    assert names["C4"] == "Ceiling Downlights 25° Honeycomb"
    assert names["C2"] == "Ceiling Downlights 25°/50° Honeycomb 1"
    assert names["C5"] == "Ceiling Downlights 25°/50° Honeycomb 2"
    assert {c.named_by for c in spec.circuits if c.ref in ("C2", "C5")} == {"unresolved"}
    assert any("C2, C5" in p and "need a name" in p for p in problems)


def test_a_circuit_named_after_its_product_is_reported_too():
    rows = [["LANDING", "C1", "Cazalla", "primed", "L4", "1", "5", "5",
             "700mA", "Y", "JC", ""]]
    spec = specpdf.parse_rows(rows)
    problems = zonename.apply(spec)
    assert spec.circuits[0].zone_name == "Cazalla"
    assert any("named after the product" in p for p in problems)


# ----------------------------------------------------------- presentation

def test_title_case_leaves_small_words_and_real_capitals_alone():
    assert zonename.titled("internal light in wardrobes") == "Internal Light in Wardrobes"
    assert zonename.titled("IP LED strip") == "IP LED Strip"
