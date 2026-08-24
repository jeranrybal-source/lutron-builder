"""Tests for reading and validating schedules.

These cover the failures that have actually happened, not the easy paths.
"""
import os

import pytest

from hwwriter import ingest
from hwwriter.schedule import _dimming, load_schedule, read_reference

DOCS = ingest.DOCS
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "test-house")


def _references():
    return (
        read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                       "ModuleType", "LutronModelInfoID"),
        read_reference(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"),
                       "KeypadModel", "LutronModelInfoID"),
    )


# --- DimmingRange -------------------------------------------------------
# An extraction reads the column name literally and writes the human range
# ("5-90") into a numeric field, duplicating LowEnd/HighEnd beside it. That
# once discarded an entire paid extraction at the review step.

@pytest.mark.parametrize("row,expected", [
    ({"DimmingRange": "5-90", "LowEnd_pct": "5", "HighEnd_pct": "90"}, (0, 5, 90, False)),
    ({"DimmingRange": "1-100", "LowEnd_pct": "1", "HighEnd_pct": "100"}, (0, 1, 100, False)),
    ({"DimmingRange": "5-90", "LowEnd_pct": "", "HighEnd_pct": ""}, (0, 5, 90, False)),
    # Nothing stated anywhere: the same 5/90 comes out, but flagged as a
    # default rather than a reading, which is what the sheet has to say.
    ({"DimmingRange": "", "LowEnd_pct": "", "HighEnd_pct": ""}, (0, 5, 90, True)),
    ({"DimmingRange": "0", "LowEnd_pct": "10", "HighEnd_pct": "80"}, (0, 10, 80, False)),
])
def test_written_dimming_range_is_absorbed(row, expected):
    assert _dimming(row) == expected


def test_dimming_range_contradicting_low_high_still_raises():
    with pytest.raises(ValueError, match="disagrees"):
        _dimming({"DimmingRange": "5-90", "LowEnd_pct": "20", "HighEnd_pct": "90"})


# --- The keypad catalogue -----------------------------------------------
# The IDs of two families were once swapped, so every "Palladiom" project
# silently built Alisse hardware. Nothing caught it: the names were right and
# the substituted keypads happened to have enough buttons.

def test_no_model_id_is_claimed_by_two_families():
    import csv
    rows = list(csv.DictReader(open(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"))))
    by_id = {}
    for r in rows:
        mid, fam = r["LutronModelInfoID"], r["Family"]
        assert mid not in by_id or by_id[mid] == fam, (
            f"ModelInfoID {mid} is claimed by both {by_id.get(mid)!r} and {fam!r}")
        by_id[mid] = fam


def test_every_catalogue_model_has_a_sane_button_count():
    import csv
    for r in csv.DictReader(open(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"))):
        n = int(r["DefaultButtonCount"])
        assert 0 <= n <= 24, f"{r['KeypadModel']} claims {n} buttons"


def test_families_offered_are_real_ranges():
    fams = ingest.keypad_families()
    # A family is only a choice if it offers several sizes; one-offs (a
    # tabletop Pico, a plug-in dimmer, the virtual keypad) are not.
    assert len(fams) >= 2
    assert "Palladiom" in fams and "HomeWorks QS Wired Designer (seeTouch)" in fams
    assert "Virtual" not in fams


# --- The worked example --------------------------------------------------

def test_example_schedule_loads_and_validates():
    """Loads AND validates. For most of this file's life the test only called
    load_schedule(), so its name promised a validation that never ran -- a
    cross-file defect in the example would have sailed through CI. The shell
    blueprints CI cannot restore are stood in for by the reference catalogues.
    """
    import csv as _csv
    import types

    from hwwriter.schedule import validate
    module_types, keypad_models = _references()
    sched = load_schedule(EXAMPLE, module_types, keypad_models)
    assert sched.areas and sched.loads and sched.keypads and sched.scenes
    for kp in sched.keypads:
        assert kp.model_info_id > 0, f"{kp.name} has an unresolved model"

    outputs = read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                             "ModuleType", "DefaultOutputCount")
    module_bp = {mid: types.SimpleNamespace(output_count=outputs.get(lbl, 0))
                 for lbl, mid in module_types.items()}
    counts = {r["KeypadModel"]: int(r["DefaultButtonCount"])
              for r in _csv.DictReader(
                  open(os.path.join(DOCS, "KeypadModels_REFERENCE.csv")))}
    keypad_bp = {mid: types.SimpleNamespace(
                     engraved_buttons=list(range(counts.get(lbl, 0))))
                 for lbl, mid in keypad_models.items()}
    floors = {a.parent for a in sched.areas if a.parent}
    links = ({m.link for m in sched.modules if m.link}
             | {k.link for k in sched.keypads if k.link})
    validate(sched, floors, links, module_bp, keypad_bp)   # raises on problems
    assert not sched.warnings, f"the worked example carries warnings: {sched.warnings}"


def test_button_labels_carry_their_provenance():
    module_types, keypad_models = _references()
    sched = load_schedule(EXAMPLE, module_types, keypad_models)
    # `label` IS the engraving text, so whether it was read or proposed has to
    # survive into the review sheet -- engraved faceplates cannot be returned.
    assert all(hasattr(b, "label_proposed") for b in sched.buttons)


def test_proposed_engraving_is_flagged():
    from hwwriter.schedule import Button
    assert Button("K1", 1, "Bright", notes="Proposed").label_proposed
    assert Button("K1", 1, "Bright", notes="read from LT001").label_proposed is False
    assert Button("K1", 1, "Bright").label_proposed is False


def test_a_stale_file_from_an_earlier_read_is_flagged(tmp_path):
    """Two reads in one folder must not merge silently.

    Real incident (House A, 2026-08-05): a re-read wrote six fresh CSVs
    beside a Modules.csv from the previous day. The build read both and failed
    with 175 errors -- and a *compatible* leftover would instead have produced a
    wrong project that looked entirely valid.
    """
    import os
    import shutil
    import time

    for name in os.listdir(EXAMPLE):
        if name.endswith(".csv"):
            shutil.copy(os.path.join(EXAMPLE, name), tmp_path)
    stale = tmp_path / "Modules.csv"
    old = time.time() - 2 * 86400
    os.utime(stale, (old, old))

    module_types, keypad_models = _references()
    sched = load_schedule(str(tmp_path), module_types, keypad_models)
    assert any("two different reads" in w for w in sched.warnings), (
        "a schedule file two days older than the rest was not flagged")


def test_a_consistent_folder_is_not_flagged(tmp_path):
    import os
    import shutil

    for name in os.listdir(EXAMPLE):
        if name.endswith(".csv"):
            shutil.copy(os.path.join(EXAMPLE, name), tmp_path)
    module_types, keypad_models = _references()
    sched = load_schedule(str(tmp_path), module_types, keypad_models)
    assert not any("two different reads" in w for w in sched.warnings)


def test_a_newline_in_a_name_cannot_escape_a_sql_comment():
    # A CSV field may legally contain a line break, and a line break ends a
    # "--" comment -- so anything after it would execute as SQL.
    from hwwriter.emit import Emitter
    em = Emitter.__new__(Emitter)
    em.statements = []
    Emitter.comment(em, "Ground Floor\nDROP TABLE tblArea; --")
    emitted = em.statements[-1]
    assert "\n" not in emitted.strip(), f"comment still spans lines: {emitted!r}"
    assert emitted.strip().startswith("--")


# --- findings from the pre-release review, each fixed and pinned here -------

def test_a_misspelt_action_is_refused_not_dropped():
    # "NEWW" used to drop the row silently: a circuit or keypad simply absent
    # from the finished project, with nothing said anywhere.
    import csv as _csv
    import os as _os
    import tempfile

    from hwwriter.schedule import _new_rows
    d = tempfile.mkdtemp()
    path = _os.path.join(d, "Areas.csv")
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["Action", "AreaName", "ParentArea", "Notes"])
        w.writerow(["NEWW", "Kitchen", "Ground Floor", ""])
    with pytest.raises(ValueError, match="not.*understood"):
        _new_rows(path)


def test_a_fractional_count_is_refused_not_truncated():
    with pytest.raises(ValueError, match="whole number"):
        _int_probe = None
        from hwwriter.schedule import _int
        _int({"NumberOfFixtures": "1.9"}, "NumberOfFixtures", 1)


def test_duplicate_fixture_refs_are_rejected():
    # The review sheet keeps the LAST duplicate; the builder took the FIRST
    # one's control properties. What was approved and what was written were
    # different fixtures, and the file looked entirely valid.
    from hwwriter.schedule import Fixture, Schedule, ValidationError, validate
    s = Schedule()
    def _fx(desc):
        return Fixture(ref="L1", description=desc, load_type_id=119, wattage=10,
                       lamp_quantity=1, lamp_wattage=10, low_end=5, high_end=90,
                       phase_control=1)
    s.fixtures = [_fx("first"), _fx("second")]
    with pytest.raises(ValidationError) as caught:
        validate(s, set(), set(), {}, {})
    assert any("appears more than once" in x for x in caught.value.problems)


# --- wrong-house defects found by the second cross-AI review, 2026-08-05 ----
# All five silently produced incorrect hardware or programming: each one
# validated, built, and gave you a house that does not match the drawings.
# Each is exercised against the real Schedule objects, not a mock.

from hwwriter.schedule import (  # noqa: E402
    Area,
    Keypad,
    Load,
    Module,
    OutputAssignment,
    Scene,
    SceneLevel,
    Schedule,
    ValidationError,
    validate,
)


def _problems(s, module_blueprints=None, keypad_blueprints=None):
    try:
        validate(s, set(), set(), module_blueprints or {}, keypad_blueprints or {})
        return []
    except ValidationError as e:
        return e.problems


def _base():
    s = Schedule()
    s.areas.append(Area("Kitchen", "", ""))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=1))
    return s


def test_a_misspelt_unaffected_is_refused_not_treated_as_a_brightness():
    """The build treats everything except 'Unaffected' as a brightness.

    So 'Unaffectd' switches a circuit the scene was written to leave alone --
    and nothing anywhere said a word.
    """
    s = _base()
    sc = Scene(area="Kitchen", name="Relax", number=2)
    sc.levels.append(SceneLevel(zone="Downlights", command="Unaffectd", level=0))
    s.scenes.append(sc)
    assert any("not understood" in x for x in _problems(s))


def test_a_level_by_another_name_is_accepted_and_flagged():
    """Models write the same idea four different ways.

    Measured across six real reads (2026-08-07): Opus writes `Level`, Sonnet
    writes `GotoLevel`/`GoToLevel`, Fable writes `SetLevel` -- and the real
    House A job on disk carries `Level` on all 436 of its scene rows.
    Refusing them turned a fine schedule into 436 "problems" that were purely
    a vocabulary mismatch; accepting them took that project from 562 problems
    to 126.
    """
    for spelling in ("Level", "GotoLevel", "GoToLevel", "go to level", "LEVEL"):
        s = _base()
        sc = Scene(area="Kitchen", name="Relax", number=2)
        sc.levels.append(SceneLevel(zone="Downlights", command=spelling, level=40))
        s.scenes.append(sc)
        probs = _problems(s)
        assert not [x for x in probs if "CommandType" in x], (
            f"{spelling!r} was refused, though it plainly means a brightness")
        assert any("says SetLevel" in x for x in s.warnings), (
            f"{spelling!r} was accepted silently -- it should be flagged")


def test_a_misspelt_unaffected_is_still_refused_after_the_synonyms():
    """The round-2 defect must not come back through the new tolerance.

    Everything except Unaffected is built as a brightness, so `Unaffectd` on a
    circuit the scene meant to leave alone switches it on. Tolerating level
    synonyms must not become "anything unrecognised is a level".
    """
    for bad in ("Unaffectd", "Unaffcted", "Unffected", "Levl", "SetLevelish", "Toggle"):
        s = _base()
        sc = Scene(area="Kitchen", name="Relax", number=2)
        sc.levels.append(SceneLevel(zone="Downlights", command=bad, level=0))
        s.scenes.append(sc)
        assert any("not understood" in x for x in _problems(s)), (
            f"{bad!r} slipped through as a level")


def test_a_colour_scene_is_refused_because_this_writer_cannot_build_one():
    # INSTRUCTIONS.md advertises DMXColor and Spectrum; build.py implements
    # neither, so a colour preset index of 2 becomes 2% brightness.
    s = _base()
    sc = Scene(area="Kitchen", name="Colour", number=3)
    sc.levels.append(SceneLevel(zone="Downlights", command="DMXColor", level=2))
    s.scenes.append(sc)
    assert any("colour" in x for x in _problems(s))


def test_the_dialects_the_writer_really_does_build_are_accepted():
    for cmd in ("SetLevel", "ZoneLevel", "Unaffected", ""):
        s = _base()
        sc = Scene(area="Kitchen", name="Relax", number=2)
        sc.levels.append(SceneLevel(zone="Downlights", command=cmd, level=50))
        s.scenes.append(sc)
        assert not [x for x in _problems(s) if "CommandType" in x], f"{cmd!r} refused"


def test_two_keypads_cannot_share_a_name():
    # Buttons are matched to a keypad by name, so both receive the same
    # programming and one of them is wrong.
    s = _base()
    for _ in range(2):
        s.keypads.append(Keypad(name="Kitchen > Door", area="Kitchen", station="",
                                device="", model_info_id=1, model_label="X",
                                link="", address=None))
    assert any("appears more than once" in x for x in _problems(s))


def test_one_circuit_cannot_be_wired_to_two_module_outputs():
    # The builder keeps the last, so the other output silently drives nothing.
    s = _base()
    for name in ("M1", "M2"):
        s.modules.append(Module(name=name, model_info_id=1, model_label="DPM",
                                area="Kitchen", link="", address=None))
    s.outputs.append(OutputAssignment(area="Kitchen", zone="Downlights",
                                      module="M1", output=1))
    s.outputs.append(OutputAssignment(area="Kitchen", zone="Downlights",
                                      module="M2", output=1))
    assert any("wired to both" in x for x in _problems(s))


def test_a_module_cannot_claim_more_outputs_than_the_real_unit_has():
    """The CSV used to win over the physical module.

    OutputCount=99 on a four-output unit validated, and the file was built
    with channels the hardware does not have.
    """
    import types
    s = _base()
    s.modules.append(Module(name="M1", model_info_id=7, model_label="DPM-4",
                            area="Kitchen", link="", address=None, output_count=99))
    s.outputs.append(OutputAssignment(area="Kitchen", zone="Downlights",
                                      module="M1", output=99))
    blueprints = {7: types.SimpleNamespace(output_count=4)}
    probs = _problems(s, module_blueprints=blueprints)
    assert any("physically has 4" in x for x in probs)
    assert any("exceeds its 4 outputs" in x for x in probs), (
        "the assignment was still measured against the claimed capacity")


def test_two_scenes_sharing_a_number_are_reported_not_silently_merged(tmp_path):
    """validate() had a check for this that could never fire.

    load_schedule keys scenes by (area, number), so the second scene merged
    into the first -- taking its name and contributing its levels -- before
    validation ever looked. The result is a scene that is neither of the two
    the engineer wrote.
    """
    import shutil
    for name in os.listdir(EXAMPLE):
        shutil.copy2(os.path.join(EXAMPLE, name), tmp_path)
    scenes_csv = tmp_path / "Scenes.csv"
    rows = scenes_csv.read_text(encoding="utf-8").rstrip("\n").split("\n")
    header = rows[0].split(",")
    first = dict(zip(header, rows[1].split(","), strict=True))
    clash = dict(first, SceneName="Something Else")
    rows.append(",".join(clash.get(h, "") for h in header))
    scenes_csv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    mt, km = _references()
    sched = load_schedule(str(tmp_path), mt, km)
    assert sched.merged_scenes, "the merge went unrecorded, as it always had"
    assert any("would merge into one scene" in x for x in _problems(sched))


def test_a_fractional_wattage_is_accepted_but_a_fractional_count_is_not():
    """LED tape is specified per metre and 9.6 W/m is an ordinary figure.

    The whole-number rule added in 1.1.2 was right for a COUNT -- truncating
    1.9 loses a fixture -- but it was applied to every number, including
    wattage. That made the paid House A extraction of 2026-08-04
    unloadable: it carries 'Contour HD27 linear LED 9.6W per metre'.
    """
    from hwwriter.schedule import _int, _num
    assert _num({"W": "9.6"}, "W") == 9.6
    assert _num({"W": "20"}, "W") == 20 and isinstance(_num({"W": "20"}, "W"), int)
    assert _num({"W": ""}, "W", 0) == 0
    with pytest.raises(ValueError):
        _int({"Count": "1.9"}, "Count")


def test_the_real_house_a_extraction_still_loads():
    # The regression that made this test necessary was only visible on a real
    # paid read, not on any example in this repo.
    ref = os.path.join(EXAMPLE, "FixturesCatalog.csv")
    assert os.path.exists(ref)
    # Only the first eight columns of the catalogue are supplied here, so zip
    # is meant to stop at the end of them.
    rows = [dict(zip(open(ref, encoding="utf-8-sig").readline().strip().split(","),
                     ["NEW", "L79", "Contour HD27 linear LED 9.6W per metre",
                      "16", "DALI", "9.6", "1", "9.6"], strict=False))]
    from hwwriter.schedule import _num
    assert _num(rows[0], "FixtureWattage_W", 0) == 9.6


def test_a_fractional_wattage_reads_cleanly_on_the_review_sheet():
    """9.6 W/m x 3 rendered as "28.799999999999997W" on the sheet.

    That sheet is what the engineer checks against the drawings, so the number
    has to look like a number. Thousands separators must survive.
    """
    from hwwriter.review import _w
    assert _w(9.6 * 3) == "28.8"
    assert _w(20.0) == "20" and _w(20) == "20"
    assert _w(12500) == "12,500"
    assert _w(12500.5) == "12,500.5"


def test_one_scene_number_clash_is_reported_once_not_once_per_circuit(tmp_path):
    """The append sat in the per-ROW loop.

    A ten-circuit scene colliding with another produced the identical message
    ten times, telling the engineer they had ten problems instead of one.
    Driven through the real load_schedule, not through a copy of its logic.
    """
    from hwwriter.schedule import load_schedule
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,FixtureCount\n"
        + "".join(f"NEW,Kitchen,Z{i},F1,1\n" for i in range(10)), encoding="utf-8")
    (tmp_path / "Scenes.csv").write_text(
        "Action,AreaName,SceneName,SceneNumber,ZoneName,CommandType,Level_pct\n"
        + "".join(f"NEW,Kitchen,Relax,2,Z{i},ZoneLevel,30\n" for i in range(10))
        + "".join(f"NEW,Kitchen,Movie,2,Z{i},ZoneLevel,10\n" for i in range(10)),
        encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert len(sched.merged_scenes) == 1, (
        f"one clash reported {len(sched.merged_scenes)} times")
    assert "Relax" in sched.merged_scenes[0] and "Movie" in sched.merged_scenes[0]


def test_wattage_rounds_half_up_not_to_even():
    """Designer stores wattage as int, so something rounds. Python's built-in
    round() is ties-to-even: 0.5 -> 0, so a genuine half-watt fitting became
    "no wattage" in the built file.
    """
    from hwwriter.schedule import half_up
    assert half_up(9.6) == 10
    assert half_up(0.5) == 1 and half_up(1.5) == 2 and half_up(2.5) == 3
    assert half_up(0.4) == 0
    assert half_up(20) == 20 and half_up(None) == 0


def test_the_sheet_and_the_builder_round_by_the_SAME_rule():
    """The whole point is that the sheet states the figure the file carries.

    Two separate rounding expressions would drift apart the first time either
    changed, and the disagreement would be invisible.
    """
    import inspect as _inspect

    from hwwriter import build, review
    bsrc, rsrc = _inspect.getsource(build), _inspect.getsource(review)
    assert ("half_up(fx.watts_per_metre or fx.wattage)" in bsrc
            and "half_up(fx.lamp_wattage)" in bsrc), (
        "the builder no longer uses the shared rounding -- SQL will truncate")
    assert "_half_up(f.wattage)" in rsrc, (
        "the review sheet states a rounding the builder does not perform")


def test_the_review_sheet_says_where_a_wattage_will_be_rounded():
    from hwwriter import review
    from hwwriter.schedule import Area, Fixture, Load, Schedule
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture(ref="L79", description="Contour HD27 tape",
                              load_type_id=16, wattage=9.6, lamp_quantity=1,
                              lamp_wattage=9.6, low_end=0, high_end=100,
                              phase_control=1))
    s.loads.append(Load("Kitchen", "Tape", "L79", 3))
    html = review.render(s, "T", "Lutron Builder", {}) if hasattr(review, "render") else None
    if html is None:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            review.write(s, out, "T", "Lutron Builder", {})
            html = open(out, encoding="utf-8").read()
    assert "whole number" in html and "9.6" in html and "10" in html


def test_a_duplicate_keypad_name_is_told_how_to_fix_it():
    # "Refused" without "name it for the room" is a dead end for a
    # non-technical engineer.
    from hwwriter.schedule import Keypad, Schedule, ValidationError, validate
    s = Schedule()
    s.areas.append(__import__("hwwriter.schedule", fromlist=["Area"]).Area("Hall", "GF"))
    for area in ("Hall", "Hall"):
        s.keypads.append(Keypad(name="Door", area=area, station="Door", device="",
                                model_info_id=1, model_label="X", link="", address=None))
    try:
        validate(s, set(), set(), {}, {1: object()})
        problems = []
    except ValidationError as e:
        problems = e.problems
    dup = [x for x in problems if "appears more than once" in x]
    assert dup, "duplicate keypad names are no longer refused"
    assert "room" in dup[0] and ">" in dup[0]


def test_two_scenes_in_a_room_sharing_a_NAME_are_refused_at_build(tmp_path):
    """The web editor caught this; the Excel route the app recommends did not.

    A button recalls a scene by name, and the builder keeps one number per
    (area, name) -- so the second "Relax" silently steals every button pointing
    at the first, and the build reported no problem at all.
    """
    from hwwriter.schedule import ValidationError, load_schedule, validate
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\nNEW,Kitchen,Ground Floor\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,FixtureCount\nNEW,Kitchen,Z1,F1,1\n",
        encoding="utf-8")
    (tmp_path / "Scenes.csv").write_text(
        "Action,AreaName,SceneName,SceneNumber,ZoneName,CommandType,Level_pct\n"
        "NEW,Kitchen,Relax,1,Z1,ZoneLevel,30\n"
        "NEW,Kitchen,Relax,2,Z1,ZoneLevel,60\n", encoding="utf-8")
    try:
        validate(load_schedule(str(tmp_path), {}, {}), set(), set(), {}, {})
        problems = []
    except ValidationError as e:
        problems = e.problems
    clash = [x for x in problems if "two scenes called" in x]
    assert clash, "duplicate scene NAMES still build silently"
    assert "1" in clash[0] and "2" in clash[0]


def test_a_case_only_scene_name_difference_still_clashes():
    # The builder collapses them; the check must too.
    from hwwriter.schedule import Area, Scene, Schedule, ValidationError, validate
    s = Schedule()
    s.areas.append(Area("Kitchen", "GF"))
    s.scenes.append(Scene(area="Kitchen", name="Relax", number=1))
    s.scenes.append(Scene(area="Kitchen", name="RELAX", number=2))
    try:
        validate(s, set(), set(), {}, {})
        problems = []
    except ValidationError as e:
        problems = e.problems
    assert any("two scenes called" in x for x in problems)


def test_a_drawings_own_circuit_reference_does_not_destroy_the_read(tmp_path):
    """Lighting drawings number circuits 001A, 002B, 003C.

    A real House C read (2026-08-07, Opus 5) carried 106 such references
    across 151 correctly-read circuits. `_int` threw ValueError out of
    load_schedule, so the whole paid extraction could not be opened at all --
    same shape as the 9.6 W wattage bug, a different column.

    They are NOT parsed down to leading digits: 002A/002B/002C would collapse
    to 2 and collide. Designer numbers the room; the reference stays in the CSV.
    """
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,001A,F1,6\n"
        "NEW,Kitchen,Island,002A,F1,3\n"
        "NEW,Kitchen,Perimeter,002B,F1,4\n"
        "NEW,Study,Desk,7,F1,2\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert len(sched.loads) == 4, "the read survived"
    lettered = [ld for ld in sched.loads if ld.zone in ("Downlights", "Island", "Perimeter")]
    assert all(ld.zone_number is None for ld in lettered), (
        "a drawing reference was coerced into a zone number")
    assert [ld.zone_number for ld in sched.loads if ld.zone == "Desk"] == [7], (
        "a genuine whole number must still be honoured")
    warn = [w for w in sched.warnings if "circuit references" in w]
    assert warn, "the set-aside references were not reported"
    assert "001A" in warn[0]
    # Worded to lead with what was KEPT -- the old sentence led with what was
    # not used and James read it as "no circuit numbers were available".
    assert "kept exactly as written" in warn[0]
    assert "Nothing is missing" in warn[0]

    # And for a spreadsheet import there is no remark at all: the column is
    # offered as "the designer's own reference" and the template's worked
    # example itself uses C1, C2, C3 -- James copied them from the guidance
    # page and was warned about it. Following the instructions = silence.
    (tmp_path / "read-with.json").write_text('{"source": "spreadsheet"}',
                                             encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert sched.source == "spreadsheet"
    assert not [w for w in sched.warnings if "circuit references" in w]
    assert all(ld.zone_number is None for ld in sched.loads
               if ld.zone in ("Downlights", "Island", "Perimeter")), (
        "suppressing the sentence must not start coercing references")


def test_rooms_with_nothing_in_them_are_not_buildable(tmp_path):
    """A read can return a full room list and nothing else.

    Real case: a Sonnet 5 read of House A (2026-08-07) returned 49
    rooms, 31 fixture types and ZERO circuits, keypads, buttons and scenes --
    it could not resolve the plan symbols and correctly refused to invent
    them. That validates clean and renders a review sheet, so the earlier
    "no rooms AND no circuits" guard passed it straight through to a build of
    49 empty rooms.
    """
    from hwwriter.app import _has_anything_to_build
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\n"
        + "".join(f"NEW,Room {i},Ground Floor\n" for i in range(49)), encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n", encoding="utf-8")
    (tmp_path / "Keypads.csv").write_text(
        "Action,KeypadName,AreaName,StationName,DeviceName,KeypadModel\n",
        encoding="utf-8")
    assert _has_anything_to_build(str(tmp_path)) is False, (
        "49 rooms with no circuits and no keypads is still nothing to build")

    # One circuit is enough to make it a real project again.
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
        "NEW,Room 0,Downlights,F1,4\n", encoding="utf-8")
    assert _has_anything_to_build(str(tmp_path)) is True

    # So is a keypad-only project (scenes bound by hand in Designer).
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n", encoding="utf-8")
    (tmp_path / "Keypads.csv").write_text(
        "Action,KeypadName,AreaName,StationName,DeviceName,KeypadModel\n"
        "NEW,Hall > Door,Room 0,Door,Device 1,X\n", encoding="utf-8")
    assert _has_anything_to_build(str(tmp_path)) is True


def test_button_zero_is_refused_not_silently_dropped_from_the_file(tmp_path):
    """Button rows are matched to physical buttons numbered from 1.

    A row numbered 0 passed validation, appeared on the review sheet, and was
    then never written into the .hw at all -- shown as programmed, built as
    nothing. Found by round-5 review (Codex), reproduced before fixing.
    """
    from hwwriter.schedule import load_schedule, validate
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\nNEW,Kitchen,Ground Floor\n", encoding="utf-8")
    (tmp_path / "Keypads.csv").write_text(
        "Action,KeypadName,AreaName,StationName,DeviceName,KeypadModel\n"
        "NEW,Kitchen > Door,Kitchen,Door,Device 1,X\n", encoding="utf-8")
    (tmp_path / "Buttons.csv").write_text(
        "Action,KeypadName,ButtonNumber,ButtonLabel,ActionType,TargetArea,TargetScene\n"
        "NEW,Kitchen > Door,0,Bright,RecallAreaScene,Kitchen,Bright\n", encoding="utf-8")
    (tmp_path / "Scenes.csv").write_text(
        "Action,AreaName,SceneName,SceneNumber,ZoneName,CommandType,Level_pct\n",
        encoding="utf-8")
    import types
    sched = load_schedule(str(tmp_path), {}, {"X": 1})
    try:
        validate(sched, set(), set(), {},
                 {1: types.SimpleNamespace(engraved_buttons=[1, 2])})
        problems = []
    except ValidationError as e:
        problems = e.problems
    assert any("numbered from 1" in x for x in problems), (
        "button 0 still validates, and would silently vanish from the built file")


def test_a_blank_button_number_is_refused_not_defaulted_to_one(tmp_path):
    # Blank used to default to 1, silently merging two different buttons into
    # button 1 -- which then fired both sets of actions.
    from hwwriter.schedule import load_schedule
    (tmp_path / "Buttons.csv").write_text(
        "Action,KeypadName,ButtonNumber,ButtonLabel,ActionType,TargetArea,TargetScene\n"
        "NEW,Kitchen > Door,,Bright,RecallAreaScene,Kitchen,Bright\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ButtonNumber"):
        load_schedule(str(tmp_path), {}, {})


def test_two_circuits_claiming_one_zone_number_are_warned_about(tmp_path):
    from hwwriter.schedule import load_schedule, validate
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\nNEW,Kitchen,Ground Floor\n", encoding="utf-8")
    (tmp_path / "FixturesCatalog.csv").write_text(
        "Action,FixtureRef,Description,LoadTypeID,FixtureWattage_W,LampQuantity,"
        "LampWattage_W,LowEnd_pct,HighEnd_pct,PhaseControl\n"
        "NEW,F1,Downlight,119,10,1,10,5,90,1\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,3,F1,4\n"
        "NEW,Kitchen,Island,3,F1,2\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    validate(sched, {"Ground Floor"}, set(), {}, {})
    assert any("ZoneNumber 3" in w for w in sched.warnings), (
        "two circuits sharing an explicit ZoneNumber pass with nothing said")


def test_an_explicit_load_type_override_of_zero_is_not_discarded():
    """LoadTypeID 0 is 'Default' in Lutron's own reference list.

    build.py used `override or fixture.load_type_id`, and 0 is falsy -- so an
    explicit 0 silently became the fixture's own type in the .hw while the
    review sheet showed nothing either way.
    """
    import inspect as _inspect

    from hwwriter import build
    src = _inspect.getsource(build)
    assert "load.load_type_override or fixture.load_type_id" not in src, (
        "the falsy-zero override bug is back: `or` discards an explicit 0")
    assert "load.load_type_override\n" in src or "is not None" in src


def test_the_duplicate_keypad_hint_never_suggests_the_name_it_is_rejecting():
    """'Hall > Hall > Door', and ' > Door' when the area was blank.

    A suggestion that is not unique, or is nonsense, is worse than none.
    """
    from hwwriter.schedule import Area, Keypad, Schedule, ValidationError, validate

    def problems_for(name, area, station):
        s = Schedule()
        s.areas.append(Area(area or "Hall", "GF"))
        for _ in range(2):
            s.keypads.append(Keypad(name=name, area=area, station=station, device="",
                                    model_info_id=1, model_label="X", link="",
                                    address=None))
        try:
            validate(s, set(), set(), {}, {1: object()})
            return []
        except ValidationError as e:
            return [x for x in e.problems if "appears more than once" in x]

    blank = problems_for("Door", "", "")
    assert blank and " > Door'" not in blank[0], "suggested a name with a blank room"

    already = problems_for("Hall > Door", "Hall", "Door")
    assert already
    assert "Hall > Hall > Door" not in already[0], "suggested a doubled-up room name"

    useful = problems_for("Door", "Hall", "By Stair")
    assert useful and "Hall > By Stair" in useful[0]


# --- What the extraction could not read ---------------------------------
# Everything below defends the same rule: a value nobody read must not arrive
# looking like a value somebody read, and must not take the rest of the house
# down with it either.

def test_the_notes_survive_the_read(tmp_path):
    """The AI's explanation used to be parsed and thrown away.

    Every Notes column on every file was read into a dict and dropped on the
    floor, so the review sheet -- the one thing an engineer checks against the
    drawings -- could not show why a cell was blank. On the real House A read
    109 of 109 circuits carried a note explaining a missing count, and not one
    of them was ever shown to anybody.
    """
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea,Notes\nNEW,Kitchen,Ground Floor,room note\n",
        encoding="utf-8")
    (tmp_path / "FixturesCatalog.csv").write_text(
        "Action,FixtureRef,Description,FixtureWattage_W,Notes\n"
        "NEW,F1,Downlight,9,wattage from the spec sheet\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures,Notes\n"
        "NEW,Kitchen,Downlights,F1,,count not legible on sheet L-101\n",
        encoding="utf-8")
    (tmp_path / "Keypads.csv").write_text(
        "Action,KeypadName,AreaName,StationName,DeviceName,KeypadModel,Notes\n"
        "NEW,Kitchen > Door,Kitchen,Door,Device 1,seeTouch 5,model assumed\n",
        encoding="utf-8")
    (tmp_path / "Scenes.csv").write_text(
        "Action,AreaName,SceneName,SceneNumber,ZoneName,CommandType,Level_pct,Notes\n"
        "NEW,Kitchen,Bright,1,Downlights,SetLevel,100,levels proposed\n",
        encoding="utf-8")

    s = load_schedule(str(tmp_path), {}, {})
    assert s.areas[0].notes == "room note"
    assert s.fixtures[0].notes == "wattage from the spec sheet"
    assert s.loads[0].notes == "count not legible on sheet L-101"
    assert s.keypads[0].notes == "model assumed"
    assert s.scenes[0].levels[0].notes == "levels proposed"
    assert s.scenes[0].notes == "levels proposed", "the scene's own note"


def test_a_blank_count_is_recorded_as_assumed_not_as_one(tmp_path):
    """1 x 9W read on the sheet exactly like a count somebody had made."""
    (tmp_path / "FixturesCatalog.csv").write_text(
        "Action,FixtureRef,Description,LoadTypeID,FixtureWattage_W,LowEnd_pct,HighEnd_pct\n"
        "NEW,F1,Downlight,119,9,5,90\n"
        "NEW,F2,Uplight,,12,,\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,F1,6\n"
        "NEW,Kitchen,Uplights,F1,\n", encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})

    read, guessed = s.loads
    assert read.count == 6 and "count" not in read.assumed
    assert guessed.count == 1 and "count" in guessed.assumed, (
        "a blank count arrived as a fact")

    stated, silent = s.fixtures
    assert stated.assumed == {"lamp_quantity"}, (
        "only the column this row really left blank")
    assert silent.load_type_id == 16 and "load_type_id" in silent.assumed, (
        "D2: the default still builds, but is marked as assumed")
    assert "trims" in silent.assumed

    from hwwriter.schedule import circuit_watts
    by_ref = {f.ref: f for f in s.fixtures}
    assert circuit_watts(read, by_ref) == 54
    assert circuit_watts(guessed, by_ref) is None, (
        "D1: a load total worked out from a guessed count is not a load total")


def test_one_unreadable_circuit_does_not_refuse_the_whole_house(tmp_path):
    """The 2026-08-08 House A re-read: 194 circuits, every FixtureRef blank.

    Honest, and unbuildable -- 194 problems and nothing written, while the
    earlier read that guessed produced a file that built. Neither is right: a
    blank ref is a circuit that cannot be built, not a house that cannot be.
    """
    from hwwriter.schedule import Fixture

    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
    s.loads.append(Load(area="Kitchen", zone="Cove", fixture_ref="", count=1))

    assert _problems(s) == [], "a blank fixture ref refused the whole build"
    assert any("no fixture type" in x for x in s.warnings), "and said nothing about it"

    # A ref that names something absent is a different thing -- a typo, fixable,
    # and still a problem.
    s.warnings.clear()
    s.loads[1].fixture_ref = "F9"
    assert any("not in FixturesCatalog" in x for x in _problems(s))


def test_a_room_name_may_repeat_on_another_floor(tmp_path):
    """A WC on three floors is an ordinary house.

    Rooms were keyed on name alone, so it refused unless the extraction invented
    suffixes the drawings do not carry. Keyed on floor + name it is allowed --
    and a bare 'WC' in another file is then ambiguous, which is said plainly
    rather than quietly taken to mean the first one.
    """
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\n"
        "NEW,WC,Ground Floor\nNEW,WC,First Floor\nNEW,Kitchen,Ground Floor\n",
        encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
        "NEW,Ground Floor > WC,Downlights,F1,2\n"
        "NEW,Kitchen,Downlights,F1,6\n", encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})
    assert [a.uid for a in s.areas] == ["Ground Floor > WC", "First Floor > WC", "Kitchen"]
    assert s.areas[0].name == "WC", "Designer still gets the room's own name"
    assert s.loads[0].area == "Ground Floor > WC"
    assert s.loads[1].area == "Kitchen", "an unambiguous name needs no floor"
    assert not any("is the name of" in x for x in _problems(s))

    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
        "NEW,WC,Downlights,F1,2\n", encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})
    said = [x for x in _problems(s) if "is the name of" in x]
    assert said and "Ground Floor > WC" in said[0]


def test_two_rooms_on_one_floor_still_cannot_share_a_name():
    s = Schedule()
    s.areas.append(Area("WC", "Ground Floor"))
    s.areas.append(Area("WC", "Ground Floor"))
    assert any("appears twice on 'Ground Floor'" in x for x in _problems(s))


def test_linear_product_is_measured_not_counted(tmp_path):
    """10 m of 9.6 W/m tape read as one symbol at 9.6 W is out by a factor of ten.

    The brief only ever said "count the symbols", and undersizing a dimmer is
    the sort of mistake that is found by a hot module, not by a review sheet.
    """
    (tmp_path / "FixturesCatalog.csv").write_text(
        "Action,FixtureRef,Description,LoadTypeID,FixtureWattage_W,Wattage_W_per_m,"
        "LowEnd_pct,HighEnd_pct\n"
        "NEW,T1,Cove tape,119,,9.6,5,90\n"
        "NEW,F1,Downlight,119,9,,5,90\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures,RunLength_m\n"
        "NEW,Kitchen,Cove,T1,1,10.4\n"
        "NEW,Kitchen,Second Cove,T1,1,\n"
        "NEW,Kitchen,Downlights,F1,6,\n", encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})
    from hwwriter.schedule import circuit_watts
    by_ref = {f.ref: f for f in s.fixtures}

    assert s.loads[0].run_length_m == 10.4, "fractional metres must survive"
    assert round(circuit_watts(s.loads[0], by_ref), 2) == 99.84
    assert circuit_watts(s.loads[1], by_ref) is None, "no run length, no honest total"
    assert circuit_watts(s.loads[2], by_ref) == 54, "a counted fitting is unaffected"

    _problems(s)
    assert any("no RunLength_m" in x for x in s.warnings)


def test_a_run_length_on_a_fitting_that_is_not_linear_is_refused():
    from hwwriter.schedule import Fixture
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1",
                        count=1, run_length_m=6))
    assert any("has no per-metre wattage" in x for x in _problems(s))


def test_one_circuit_may_light_several_rooms(tmp_path):
    """Stairs, landings, corridors and open plan (D8). Counted once."""
    (tmp_path / "Areas.csv").write_text(
        "Action,AreaName,ParentArea\n"
        "NEW,Landing,First Floor\nNEW,Stairs,First Floor\n", encoding="utf-8")
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures,AdditionalAreas\n"
        "NEW,Landing,Downlights,F1,8,Stairs\n", encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})
    assert len(s.loads) == 1, "one circuit, counted once"
    assert s.loads[0].additional_areas == ["Stairs"]

    s.loads[0].additional_areas = ["Attic"]
    assert any("also serves 'Attic', which is not in Areas.csv" in x for x in _problems(s))


def test_the_phase_load_ceiling_is_a_phase_ceiling_only():
    """D9/D10. 500 W fits any output; 500-800 W fits the first only; DALI has
    no per-zone ceiling at all, because 100 m of tape can sit on one address."""
    from hwwriter.schedule import Fixture

    def circuit(watts, load_type):
        s = Schedule()
        s.areas.append(Area("Kitchen", "Ground Floor"))
        s.fixtures.append(Fixture("F1", "Downlight", load_type, watts, 1, 0, 5, 90, 1))
        s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=1))
        return s, _problems(s)

    s, probs = circuit(400, 119)
    assert not probs and not [w for w in s.warnings if "OUTPUT ONLY" in w]

    s, probs = circuit(650, 119)
    assert not probs, "500-800 W is allowed -- it is a note to the panel designer"
    assert any("FIRST OUTPUT ONLY" in w for w in s.warnings)

    s, probs = circuit(900, 119)
    assert any("no output can drive this circuit" in x for x in probs)

    for load_type, what in ((16, "DALI"), (118, "a switched circuit")):
        s, probs = circuit(900, load_type)
        assert not probs, f"{what} has no per-zone load ceiling"
        assert not [w for w in s.warnings if "OUTPUT ONLY" in w], what


def test_a_fitting_may_be_switched_but_not_dimmed_another_way():
    """The same product goes on a relay in a store room and on DALI in the hall.

    19 House A circuits do exactly that and are correct. Swapping one DIMMING
    method for another is the impossible one -- a DALI driver cannot be
    phase-dimmed.
    """
    from hwwriter.schedule import Fixture

    def override(fixture_type, to):
        s = Schedule()
        s.areas.append(Area("Kitchen", "Ground Floor"))
        s.fixtures.append(Fixture("F1", "Downlight", fixture_type, 9, 1, 0, 5, 90, 1))
        s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1",
                            count=6, load_type_override=to))
        return _problems(s)

    assert override(16, 118) == [], "DALI fitting, switched circuit -- ordinary"
    assert any("different drivers" in x for x in override(16, 119)), "DALI on phase"
    assert any("different drivers" in x for x in override(118, 119)), "non-dim, dimmed"


# --- From the cross-AI review of this release ----------------------------
# Codex found each of these against the working tree; every one is reproduced
# here before it was fixed.

def test_a_load_type_that_is_not_a_load_type_is_refused():
    """A mistyped 999 was written straight into the switch leg, and slipped
    past every check below it because an unrecognised code has no family."""
    from hwwriter.schedule import Fixture
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 999, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
    assert any("not in LoadTypes_REFERENCE" in x for x in _problems(s))

    s.fixtures[0].load_type_id = 119
    s.loads[0].load_type_override = 999
    assert any("LoadTypeID_Override 999" in x for x in _problems(s))


def test_only_a_recognised_dimming_method_counts_as_phase():
    """The reference file's 118 entries include fans, motors, shades, DMX and
    contact closures. Treating "anything unrecognised is phase" refused a
    perfectly good 900 W DMX circuit for exceeding a phase limit."""
    from hwwriter.schedule import control_family
    assert control_family(119) == "phase"        # LED Reverse Phase
    assert control_family(1) == "phase"          # Incandescent
    assert control_family(16) == "dali"
    assert control_family(118) == "switched"
    assert control_family(112) == "0-10v"
    assert control_family(12) == "0-10v", "'Zero to 10 Volt' is not phase dimming"
    assert control_family(43) == "", "DMX is not phase"
    assert control_family(7) == "", "a fan is not phase"
    assert control_family(133) == "switched", "a relay is switched"


def test_a_scene_row_with_no_level_is_refused_not_built_as_off():
    """A blank Level_pct arrived as 0 and was built as "switch this circuit
    off in this scene" -- an assertion made out of a cell nobody filled in."""
    from hwwriter.schedule import Fixture
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
    sc = Scene(area="Kitchen", name="Bright", number=1)
    sc.levels.append(SceneLevel(zone="Downlights", command="SetLevel", level=0,
                                level_stated=False))
    s.scenes.append(sc)
    said = [x for x in _problems(s) if "no Level_pct" in x]
    assert said and "Unaffected" in said[0], "and it must say what to write instead"

    # A level of 0 that somebody actually wrote is a real instruction.
    sc.levels[0].level_stated = True
    assert not [x for x in _problems(s) if "no Level_pct" in x]


def test_a_run_length_reaches_the_built_file(tmp_path):
    """The sheet said 99.84 W and the file said one fitting of nothing at all:
    RunLength_m and the per-metre wattage never reached the builder."""
    from hwwriter.build import _quantity
    from hwwriter.schedule import Fixture, half_up
    tape = Fixture("T1", "Cove tape", 119, 0, 1, 0, 5, 90, 1, watts_per_metre=9.6)
    spot = Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1)
    run = Load(area="K", zone="Cove", fixture_ref="T1", count=1, run_length_m=10.4)
    counted = Load(area="K", zone="Downlights", fixture_ref="F1", count=6)

    assert _quantity(tape, run) == 10, "metres, rounded the way Designer stores them"
    assert half_up(tape.watts_per_metre or tape.wattage) == 10
    assert _quantity(spot, counted) == 6, "a counted fitting is untouched"

    # A linear fitting with no length still builds as one, rather than as zero.
    assert _quantity(tape, Load(area="K", zone="X", fixture_ref="T1", count=1)) == 1


def test_two_rooms_cannot_resolve_to_one_key():
    """The qualified form of a repeated name is itself a name a room could be
    called, and two rooms sharing a key silently share an area in the file."""
    s = Schedule()
    s.areas.append(Area("WC", "Ground Floor"))
    s.areas.append(Area("WC", "First Floor"))
    s.areas.append(Area("First Floor > WC", "Ground Floor"))
    assert any("both resolve to" in x for x in _problems(s))


def test_a_button_level_is_range_checked_and_its_fade_is_reported():
    from hwwriter.schedule import Button, ButtonAction, Fixture, Keypad
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
    s.keypads.append(Keypad(name="K1", area="Kitchen", station="Door", device="",
                            model_info_id=1, model_label="X", link="L1", address=1))
    b = Button(keypad="K1", number=1, label="Bright")
    b.actions.append(ButtonAction(action_type="DirectZoneLevel", target_area="Kitchen",
                                  target_zone="Downlights", level=150, fade=4))
    s.buttons.append(b)
    probs = _problems(s, keypad_blueprints={1: type("BP", (), {"engraved_buttons": [0]})()})
    assert any("to 150%" in x for x in probs)
    assert any("button 1 is not a calibrated value" in x or
               "on 'K1' button 1" in x for x in s.warnings)


def test_an_unstated_driver_is_assumed_DALI_unless_the_fitting_takes_a_lamp():
    """James, 2026-08-08: virtually every architectural fitting made today is
    available in DALI and that is the house preference. Mains reverse phase is
    for things that take a LAMP -- table lamps, decorative pendants, 5A
    circuits. Defaulting everything to reverse phase was wrong on most fittings
    of most jobs, and wrong in the direction that needs rewiring.
    """
    from hwwriter.schedule import default_load_type

    for architectural in ("LED spotlight - 30deg", "Ceiling downlight",
                          "Linear cove profile", "Wall washer", "Step light",
                          "5w/m 2400k LED strip", "Floor recessed uplight"):
        assert default_load_type(architectural) == 16, architectural

    for lamp_based in ("Table lamp circuit", "Decorative pendant",
                       "Chandelier", "5A socket circuit", "Wall sconce",
                       "Festoon lighting"):
        assert default_load_type(lamp_based) == 119, lamp_based


def test_a_stated_driver_is_never_overridden_by_the_default(tmp_path):
    """The rule is a fallback for silence, not an opinion about the drawings."""
    (tmp_path / "FixturesCatalog.csv").write_text(
        "Action,FixtureRef,Description,LoadTypeID\n"
        "NEW,F1,Ceiling downlight,119\n"          # stated as phase, stays phase
        "NEW,F2,Decorative pendant,16\n"          # stated as DALI, stays DALI
        "NEW,F3,Ceiling downlight,\n"             # silent -> DALI
        "NEW,F4,Decorative pendant,\n",           # silent -> reverse phase
        encoding="utf-8")
    s = load_schedule(str(tmp_path), {}, {})
    assert [f.load_type_id for f in s.fixtures] == [119, 16, 16, 119]
    assert [bool(f.assumed & {"load_type_id"}) for f in s.fixtures] == \
        [False, False, True, True]


def test_a_command_type_that_is_only_punctuation_is_refused():
    """normalise_command strips every non-alphanumeric character, so "-" and
    "?" folded to "" and slipped past the check that refuses an unknown
    command -- then built as an ordinary brightness, switching on a circuit
    whose instruction nobody could read."""
    from hwwriter.schedule import Fixture
    for command in ("-", "?", "!!"):
        s = Schedule()
        s.areas.append(Area("Kitchen", "Ground Floor"))
        s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
        s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
        sc = Scene(area="Kitchen", name="Bright", number=1)
        sc.levels.append(SceneLevel(zone="Downlights", command=command, level=80))
        s.scenes.append(sc)
        assert any("is not a word" in x for x in _problems(s)), command

    # A blank CommandType still means SetLevel, as it always has.
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor"))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    s.loads.append(Load(area="Kitchen", zone="Downlights", fixture_ref="F1", count=6))
    sc = Scene(area="Kitchen", name="Bright", number=1)
    sc.levels.append(SceneLevel(zone="Downlights", command="", level=80))
    s.scenes.append(sc)
    assert not [x for x in _problems(s) if "is not a word" in x]


def test_a_room_with_fewer_circuits_than_the_documents_say_is_reported():
    """The strongest check there is on whether a room was read WHOLE, and the
    one nobody was making."""
    from hwwriter.schedule import Fixture
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor", stated_circuits=21))
    s.areas.append(Area("Hall", "Ground Floor", stated_circuits=2))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    for i in range(8):
        s.loads.append(Load(area="Kitchen", zone=f"C{i}", fixture_ref="F1", count=2))
    for i in range(2):
        s.loads.append(Load(area="Hall", zone=f"H{i}", fixture_ref="F1", count=2))
    _problems(s)
    said = [w for w in s.warnings if "FEWER circuits than the documents say" in w]
    assert said, "a room 13 circuits short of its own estimate said nothing"
    assert "Kitchen (8 of 21)" in said[0]
    assert "Hall" not in said[0], "a room that matches its count is not a problem"
    assert "documents say 23 circuits and this schedule has 10" in said[0]


def test_more_circuits_than_stated_is_flagged_but_not_alarming():
    from hwwriter.schedule import Fixture
    s = Schedule()
    s.areas.append(Area("Kitchen", "Ground Floor", stated_circuits=2))
    s.fixtures.append(Fixture("F1", "Downlight", 119, 9, 1, 0, 5, 90, 1))
    for i in range(4):
        s.loads.append(Load(area="Kitchen", zone=f"C{i}", fixture_ref="F1", count=2))
    _problems(s)
    assert any("MORE circuits than the documents say" in w for w in s.warnings)


def test_letter_prefixed_references_become_the_rooms_own_numbering(tmp_path):
    """C1, C2, C3 restarting in every room IS Designer's numbering -- James
    filled the column exactly as the template teaches (08-12) and the numbers
    went nowhere. Letters-then-number, unique within the room: the number is
    used. The reference survives on the load either way, for the review."""
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,C1,F1,6\n"
        "NEW,Kitchen,Pendants,C2,F1,3\n"
        "NEW,Hall,Downlights,C1,F1,4\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    by_zone = {(ld.area, ld.zone): ld for ld in sched.loads}
    assert by_zone[("Kitchen", "Downlights")].zone_number == 1
    assert by_zone[("Kitchen", "Pendants")].zone_number == 2
    assert by_zone[("Hall", "Downlights")].zone_number == 1, (
        "numbering restarts per room, exactly as Designer numbers")
    assert by_zone[("Kitchen", "Downlights")].zone_ref == "C1"
    assert not [w for w in sched.warnings if "circuit references" in w], (
        "a derived number is not a set-aside reference")


def test_clashing_or_suffix_references_are_kept_not_parsed(tmp_path):
    """002A/002B share their digits -- parsing them collapses two circuits
    onto one number. All or nothing, per room."""
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,C1,F1,6\n"
        "NEW,Kitchen,Island,C 1,F1,3\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert all(ld.zone_number is None for ld in sched.loads), (
        "C1 and C 1 both mean 1 -- deriving would collide them")
    assert [ld.zone_ref for ld in sched.loads] == ["C1", "C 1"]


def test_a_room_with_one_unreferenced_circuit_is_left_entirely_to_designer(tmp_path):
    """All of them or none. Two numbered by the designer and one by Designer
    is two numbering schemes in one room -- the exact thing the rule exists to
    prevent, and the first version of it did precisely that (found in the
    pre-release review, 2026-08-13)."""
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,C1,F1,6\n"
        "NEW,Kitchen,Pendants,C2,F1,3\n"
        "NEW,Kitchen,Plinth,,F1,4\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert all(ld.zone_number is None for ld in sched.loads), (
        "one circuit had no reference, so the whole room must go to Designer")
    # A room where every circuit IS referenced still gets its numbering.
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Hall,Downlights,C1,F1,6\n"
        "NEW,Hall,Wall lights,C2,F1,3\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    assert [ld.zone_number for ld in sched.loads] == [1, 2]


def test_a_room_numbered_only_in_part_is_left_to_designer(tmp_path):
    """Codex, pre-release review: a room whose only stated number is 1 gets a
    second circuit auto-numbered 1 as well -- two circuits, both circuit 1,
    written into the .hw with no duplicate warning, because the validator
    compares only stated numbers."""
    (tmp_path / "LoadSchedule.csv").write_text(
        "Action,AreaName,ZoneName,ZoneNumber,FixtureRef,NumberOfFixtures\n"
        "NEW,Kitchen,Downlights,,F1,6\n"
        "NEW,Kitchen,Pendants,1,F1,3\n"
        "NEW,Hall,Downlights,1,F1,4\n"
        "NEW,Hall,Wall,2,F1,2\n", encoding="utf-8")
    sched = load_schedule(str(tmp_path), {}, {})
    kitchen = [ld.zone_number for ld in sched.loads if ld.area == "Kitchen"]
    assert kitchen == [None, None], "a part-numbered room must go to Designer whole"
    hall = [ld.zone_number for ld in sched.loads if ld.area == "Hall"]
    assert hall == [1, 2], "a fully numbered room keeps the designer's numbers"
    assert [w for w in sched.warnings if "number every circuit in a room" in w], (
        "dropping the numbers must be said out loud, not done quietly")
