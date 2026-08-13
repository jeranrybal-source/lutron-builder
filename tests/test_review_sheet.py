"""Tests for the review sheet -- the gate a human reads before building."""
import os

from hwwriter import ingest, review
from hwwriter.schedule import load_schedule, read_reference

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "test-house")


def _sched():
    module_types = read_reference(
        os.path.join(ingest.DOCS, "ModuleTypes_REFERENCE.csv"),
        "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(
        os.path.join(ingest.DOCS, "KeypadModels_REFERENCE.csv"),
        "KeypadModel", "LutronModelInfoID")
    return load_schedule(EXAMPLE, module_types, keypad_models)


def test_sheet_renders_every_room_and_keypad():
    sched = _sched()
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert "Test House" in html
    for area in sched.areas:
        assert area.name in html, f"{area.name} missing from the review sheet"
    for kp in sched.keypads:
        assert (kp.station or kp.name) in html


def test_sheet_labels_the_engraving_column():
    html = review.render(_sched(), "Test House", "Starter Shell.hw")
    assert "Engraving" in html


def test_proposed_engraving_is_called_out_loudly():
    # Engraved faceplates are etched to order and cannot be returned, so a
    # proposed label must never render identically to a specified one.
    sched = _sched()
    for b in sched.buttons:
        b.notes = "Proposed"
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert "proposed" in html
    assert "cannot be returned" in html


def test_specified_engraving_is_not_flagged():
    sched = _sched()
    for b in sched.buttons:
        b.notes = "read from the engraving schedule on LT001"
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert "cannot be returned" not in html


def test_sheet_is_self_contained_html():
    # It gets emailed and opened offline; no external stylesheet or script.
    # Plain LINKS to researched-wattage sources are allowed -- the old blanket
    # `href="http` assertion claimed a property the sheet deliberately does
    # not have, and passed only because this fixture has no researched values.
    html = review.render(_sched(), "Test House", "Starter Shell.hw")
    assert html.lstrip().lower().startswith("<!doctype html")
    assert "<style" in html
    assert "<link" not in html.lower() and "src=\"http" not in html


def test_a_researched_wattage_links_to_its_source():
    from hwwriter.schedule import Fixture
    sched = _sched()
    sched.fixtures.append(Fixture(
        ref="R1", description="Researched fitting", load_type_id=119, wattage=7.5,
        lamp_quantity=1, lamp_wattage=7.5, low_end=5, high_end=90, phase_control=1,
        data_source="https://example.com/datasheet"))
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert 'href="https://example.com/datasheet"' in html
    assert "RESEARCHED online" in html


def test_spare_outputs_render_as_markup_not_as_escaped_text():
    """The panel schedule wrote the spare cell as a plain string, so _table
    escaped it and the engineer read the literal text
    '<span class="spare">spare</span>' on every unused output -- shipped in the
    real House A review sheet.
    """
    from hwwriter.schedule import Module
    sched = _sched()
    sched.modules.append(Module(name="M1", model_info_id=1, model_label="DPM",
                                area=sched.areas[0].name, link="", address=None,
                                output_count=4))
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert '<span class="spare">spare</span>' in html
    assert "&lt;span" not in html


def test_a_lamp_only_fractional_wattage_names_the_lamp_figure():
    # The rounding flag used to print "(20W -> 20W)" for a fitting whose
    # fixture wattage was whole and lamp wattage fractional -- listed, but
    # saying nothing changed.
    from hwwriter.schedule import Fixture
    sched = _sched()
    sched.fixtures.append(Fixture(
        ref="L9", description="Twin lamp", load_type_id=1, wattage=20,
        lamp_quantity=2, lamp_wattage=9.6, low_end=1, high_end=100,
        phase_control=0))
    html = review.render(sched, "Test House", "Starter Shell.hw")
    assert "lamp 9.6W" in html
    assert "(20W → 20W)" not in html


# --- Showing what nobody read -------------------------------------------
# The sheet is the only place an engineer sees the extraction's own account of
# itself. Until now it showed neither the notes nor which values were guesses.

def _one_room_schedule(**load_kw):
    from hwwriter.schedule import Area, Fixture, Load, Schedule
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1",
                        count=load_kw.pop("count", 6), **load_kw))
    return s


def test_a_note_appears_beside_the_row_it_belongs_to():
    s = _one_room_schedule(notes="count not legible on sheet L-101")
    s.fixtures[0].notes = "wattage from the spec sheet"
    html = review.render(s, "P", "Shell")
    assert "count not legible on sheet L-101" in html
    assert "wattage from the spec sheet" in html
    assert html.count("<th>Notes</th>") >= 2, "every table needs its notes column"


def test_a_guessed_count_shows_an_em_dash_and_no_load():
    """D1. 1 x 9W = 9W is indistinguishable from a count somebody made."""
    s = _one_room_schedule(count=1)
    s.loads[0].assumed.add("count")
    html = review.render(s, "P", "Shell")
    assert "1 x 9W" not in html, "a guessed count was presented as a load"
    assert "0 W + 1 unknown" in html, "and was quietly excluded from the total"


def test_an_assumed_dimming_type_is_marked_as_assumed():
    """D2. The default still builds; it just may not look like a reading."""
    s = _one_room_schedule()
    s.fixtures[0].assumed.add("load_type_id")
    html = review.render(s, "P", "Shell")
    assert "LED Reverse Phase" in html
    assert '<span class="assumed">assumed</span>' in html
    assert "ASSUMED dimming type" in html, "and is called out, not left in a table"


def test_a_circuit_with_no_fixture_is_shown_as_not_built():
    s = _one_room_schedule()
    s.loads[0].fixture_ref = ""
    html = review.render(s, "P", "Shell")
    assert "not built" in html
    assert "Read this first" in html


def test_linear_product_is_shown_in_metres():
    s = _one_room_schedule(count=1, run_length_m=10.4)
    s.fixtures[0].wattage = 0
    s.fixtures[0].watts_per_metre = 9.6
    html = review.render(s, "P", "Shell")
    assert "10.4 m x 9.6 W/m = 99.84W" in html
    assert "W per metre" in html


def test_a_designer_fade_that_will_be_rewritten_is_visible():
    """The scene matrix showed no fade at all, so an overwritten 4s vanished."""
    from hwwriter.schedule import Scene, SceneLevel
    s = _one_room_schedule()
    sc = Scene(area="Kitchen", name="Bright", number=1)
    sc.levels.append(SceneLevel(zone="Downlights", command="SetLevel", level=80, fade=4))
    s.scenes.append(sc)
    html = review.render(s, "P", "Shell")
    assert "4s → 2s" in html, "the fade that will really be written is not shown"


def test_a_room_lit_from_the_landing_is_not_reported_as_dark():
    """D8: one circuit may light the stairs and the landing both."""
    from hwwriter.schedule import Area
    s = _one_room_schedule()
    s.areas.append(Area("Stairs", "Ground Floor"))
    s.loads[0].additional_areas = ["Stairs"]
    html = review.render(s, "P", "Shell")
    assert "Stairs has no circuits at all" not in html
    assert "also lights Stairs" in html


def test_a_phase_circuit_over_500W_says_first_output_only():
    s = _one_room_schedule(count=1)
    s.fixtures[0].wattage = 650
    assert "first output only" in review.render(s, "P", "Shell")
    s.fixtures[0].load_type_id = 16      # DALI has no per-zone ceiling
    assert "first output only" not in review.render(s, "P", "Shell")


def test_a_scene_level_on_an_unbuilt_circuit_says_so():
    """A circuit with no fitting is not written, and neither are the scene
    levels and buttons aimed at it -- but the sheet showed them as programmed,
    promising a scene the file does not contain."""
    from hwwriter.schedule import (
        Button,
        ButtonAction,
        Keypad,
        Scene,
        SceneLevel,
    )
    s = _one_room_schedule()
    s.loads[0].fixture_ref = ""
    sc = Scene(area="Kitchen", name="Bright", number=1)
    sc.levels.append(SceneLevel(zone="Downlights", command="SetLevel", level=80))
    s.scenes.append(sc)
    s.keypads.append(Keypad(name="K1", area="Kitchen", station="Door", device="",
                            model_info_id=1, model_label="X", link="L1", address=1))
    b = Button(keypad="K1", number=1, label="Bright")
    b.actions.append(ButtonAction(action_type="DirectZoneLevel", target_area="Kitchen",
                                  target_zone="Downlights", level=80))
    s.buttons.append(b)

    html = review.render(s, "P", "Shell")
    assert html.count("not built") >= 3, "the circuit, its scene level and its button"
    assert "scene level(s) and 1 keypad button action(s)" in html
    matrix = html.split("<h4>Scenes</h4>")[1]
    assert "80%" not in matrix, (
        "the scene matrix showed a level that will never be written")
    assert 'to 80% <span class="assumed">not built</span>' in html, (
        "the button keeps what was intended, and says it will not be written")


def test_a_researched_per_metre_wattage_keeps_its_provenance():
    s = _one_room_schedule(count=1, run_length_m=5)
    s.fixtures[0].wattage = 0
    s.fixtures[0].watts_per_metre = 9.6
    s.fixtures[0].data_source = "https://example.com/tape"
    html = review.render(s, "P", "Shell")
    assert "RESEARCHED online" in html
    assert "NO wattage at all" not in html, (
        "a fitting with a per-metre figure has a wattage")


def test_an_unread_lamp_quantity_is_not_shown_as_one():
    s = _one_room_schedule()
    s.fixtures[0].assumed.add("lamp_quantity")
    html = review.render(s, "P", "Shell")
    rows = html.split("<h2>Fixture schedule</h2>")[1].split("</section>")[0]
    assert "<td>1</td>" not in rows, "a lamp count nobody read was shown as a fact"


def test_a_measured_linear_circuit_is_not_reported_as_a_missing_count():
    """Linear product leaves NumberOfFixtures blank BY DESIGN -- it is measured
    in metres. Reporting it in the red box, and claiming its load is excluded
    from the totals when the totals include it, is a false alarm about a
    circuit that is entirely correct."""
    s = _one_room_schedule(count=1, run_length_m=8)
    s.loads[0].assumed.add("count")
    s.fixtures[0].wattage = 0
    s.fixtures[0].watts_per_metre = 9.6
    html = review.render(s, "P", "Shell")
    assert "have no fixture count" not in html
    assert "76.8" in html, "and its load is worked out and counted"


def test_an_engraving_is_escaped_once_and_only_once():
    """A label with an ampersand was escaped by hand and then again by the
    table, so the sheet showed the literal &amp; on a real faceplate label."""
    from hwwriter.schedule import Button, ButtonAction, Keypad
    s = _one_room_schedule()
    s.keypads.append(Keypad(name="K1", area="Kitchen", station="Door", device="",
                            model_info_id=1, model_label="X", link="L1", address=1))
    b = Button(keypad="K1", number=1, label="Bright & Off")
    b.actions.append(ButtonAction(action_type="DirectZoneLevel", target_area="Kitchen",
                                  target_zone="Downlights", level=80))
    s.buttons.append(b)
    html = review.render(s, "P", "Shell")
    assert "&amp;amp;" not in html
    assert "Bright &amp; Off" in html


def test_a_read_that_bound_almost_nothing_says_it_failed():
    """A sheet whose every row reads "not built" is a failed read, and saying
    so at the top is kinder than letting someone work down 194 rows to find
    that out."""
    from hwwriter.schedule import Load
    s = _one_room_schedule()
    for i in range(4):
        s.loads.append(Load(area="Kitchen", zone=f"Circuit {i}", fixture_ref="", count=1))
    html = review.render(s, "P", "Shell")
    assert "This read did not work" in html
    assert "4 of 5 circuits name no fitting" in html

    # One unreadable circuit among many good ones is NOT a failed read.
    s2 = _one_room_schedule()
    s2.loads.append(Load(area="Kitchen", zone="Odd one", fixture_ref="", count=1))
    assert "This read did not work" not in review.render(s2, "P", "Shell")


def test_a_load_schedule_is_not_accused_of_forgetting_keypads():
    """James, 08-12: "it makes it look like we forgot keypads, even though the
    template doesn't include anything to do with keypads." A load schedule
    cannot carry keypads or scenes, so the room-by-room questions are replaced
    by one plain fact -- and they return the moment the project has any."""
    s = _one_room_schedule()
    s.source = "spreadsheet"
    html = review.render(s, "P", "Shell")
    assert "controlled from elsewhere" not in html
    assert "has circuits but no scenes" not in html
    assert "nothing has been forgotten" in html

    # A plans read CAN carry keypads, so there the questions are real.
    s2 = _one_room_schedule()
    s2.source = "plans"
    html2 = review.render(s2, "P", "Shell")
    assert "controlled from elsewhere" in html2
    assert "nothing has been forgotten" not in html2

    # And a spreadsheet project the user then added a keypad to is asked the
    # room-by-room questions again -- from then on a bare room IS worth asking
    # about.
    from hwwriter.schedule import Area, Keypad
    s3 = _one_room_schedule()
    s3.source = "spreadsheet"
    s3.areas.append(Area("Study", "Ground Floor"))
    s3.keypads.append(Keypad(name="K1", area="Kitchen", station="S1",
                             device="D1", model_info_id=0, model_label="",
                             link="", address=None))
    html3 = review.render(s3, "P", "Shell")
    assert "nothing has been forgotten" not in html3


def test_the_circuit_number_column_shows_the_designers_reference():
    """A reference that is kept but never shown is indistinguishable from one
    that was ignored -- which is exactly how James read it (08-12)."""
    s = _one_room_schedule()
    s.loads[0].zone_number = 1
    s.loads[0].zone_ref = "C1"
    html = review.render(s, "P", "Shell")
    assert "<th>No.</th>" in html
    assert "C1" in html


def test_the_keypad_fact_carries_the_full_system_teaser():
    s = _one_room_schedule()
    s.source = "spreadsheet"
    html = review.render(s, "P", "Shell")
    assert "full engineering system" in html
    assert "a CSV way in for keypads and scenes may follow" in html


def test_a_loads_only_sheet_drops_the_module_keypad_scene_furniture():
    """James, 08-12: "delete everything to do with modules, keypads and
    scenes" for a load-schedule project. Zero-count tiles, an empty panel
    section and a column of red "not wired" read as a half-done job, when
    Designer is simply where that work is done by hand."""
    s = _one_room_schedule()
    s.source = "spreadsheet"
    html = review.render(s, "P", "Shell")
    for gone in (">Modules<", ">Keypads<", ">Scenes<", "Panel schedule",
                 "not wired", "Wired to"):
        assert gone not in html, f"{gone} should not appear on a loads-only sheet"
    assert "added by hand in Designer" in html
    # A plans read keeps all of it -- there the counts are real work to check.
    s2 = _one_room_schedule()
    s2.source = "plans"
    html2 = review.render(s2, "P", "Shell")
    for kept in (">Modules<", ">Keypads<", ">Scenes<", "Panel schedule"):
        assert kept in html2, f"{kept} must stay for a plans read"
