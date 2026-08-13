"""Tests for the fitting editor.

The fitting list is where the ASSUMED values live -- the dimming type above
all -- so this is the screen that turns "the sheet says this was a guess" into
"and here is where you fix it". These cover the ways an edit could quietly do
the wrong thing.
"""
import pytest

from hwwriter import fittings


def _rows():
    return [
        {"Action": "NEW", "FixtureRef": "F1", "Description": "Downlight",
         "LoadTypeID": "119", "LoadTypeName": "LED Reverse Phase",
         "FixtureWattage_W": "9", "LowEnd_pct": "5", "HighEnd_pct": "90",
         "PhaseControl": "1", "DataSource": "drawings", "Notes": ""},
        {"Action": "NEW", "FixtureRef": "T1", "Description": "Cove tape",
         "LoadTypeID": "", "LoadTypeName": "", "FixtureWattage_W": "",
         "LowEnd_pct": "", "HighEnd_pct": "", "PhaseControl": "",
         "DataSource": "", "Notes": "driver not stated"},
    ]


def _loads():
    return [{"AreaName": "Kitchen", "ZoneName": "Downlights", "FixtureRef": "F1"},
            {"AreaName": "Hall", "ZoneName": "Downlights", "FixtureRef": "F1"}]


def test_a_blank_cell_is_reported_as_assumed():
    """The same judgement the review sheet makes: blank in the file means
    nobody read it, however confidently the schedule then defaults it."""
    stated, silent = fittings.summarise(_rows(), _loads())
    assert stated["assumed"] == []
    assert stated["circuits"] == 2
    assert stated["load_type_name"] == "LED Reverse Phase"
    assert set(silent["assumed"]) == {"LoadTypeID", "LowEnd_pct", "HighEnd_pct"}
    assert silent["circuits"] == 0
    assert silent["notes"] == "driver not stated"


def test_the_usual_dimming_types_are_offered_first():
    choices = fittings.load_type_choices()
    common = [c for c in choices if c["common"]]
    assert common and common[0]["name"] == "DALI", (
        "DALI is the house default and belongs at the top of the list")
    assert {c["id"] for c in common} <= set(fittings.COMMON_LOAD_TYPES)
    # And everything else Designer knows is still reachable, so the list does
    # not quietly become our opinion of what a lighting job may contain.
    assert len(choices) > 100
    assert 16 in {c["id"] for c in choices}


def test_an_invalid_dimming_type_is_refused():
    """Typing 'DALI' into LoadTypeID produces a schedule that will not load,
    which is the whole reason this is a list and not a text box."""
    with pytest.raises(ValueError, match="not in LoadTypes_REFERENCE"):
        fittings.apply_edits(_rows(), [{"ref": "F1", "load_type_id": "9999"}])
    with pytest.raises(ValueError, match="not a load type"):
        fittings.apply_edits(_rows(), [{"ref": "F1", "load_type_id": "DALI"}])


def test_changing_the_dimming_type_fixes_the_phase_flag_with_it():
    """Designer wants PhaseControl 1 for reverse phase. Leaving yesterday's
    value behind makes the two halves of one fitting disagree."""
    rows, notes = fittings.apply_edits(_rows(), [{"ref": "F1", "load_type_id": "16"}])
    assert rows[0]["LoadTypeID"] == "16"
    assert rows[0]["LoadTypeName"] == "DALI"
    assert rows[0]["PhaseControl"] == "0"
    assert any("dimming type set to DALI" in n for n in notes)

    rows, _ = fittings.apply_edits(_rows(), [{"ref": "T1", "load_type_id": "119"}])
    assert rows[1]["PhaseControl"] == "1"


def test_a_wattage_typed_by_hand_says_so():
    """Provenance. The sheet distinguishes read, researched and now typed --
    a hand-entered figure must not go on looking like the designer's own."""
    rows, notes = fittings.apply_edits(_rows(), [{"ref": "T1", "wattage": "12.5"}])
    assert rows[1]["FixtureWattage_W"] == "12.5"
    assert rows[1]["DataSource"] == fittings.BY_HAND
    assert any("by hand" in n for n in notes)

    # Unchanged wattage leaves the provenance alone: re-saving the screen must
    # not relabel a figure that came off the drawings.
    rows, _ = fittings.apply_edits(_rows(), [{"ref": "F1", "wattage": "9"}])
    assert rows[0]["DataSource"] == "drawings"


def test_a_wattage_that_is_not_a_number_is_refused():
    with pytest.raises(ValueError, match="must be a number"):
        fittings.apply_edits(_rows(), [{"ref": "F1", "wattage": "about 9"}])


def test_editing_a_fitting_that_does_not_exist_is_refused_not_created():
    with pytest.raises(ValueError, match="not a fitting in this project"):
        fittings.apply_edits(_rows(), [{"ref": "ZZ", "description": "?"}])


def test_a_per_metre_wattage_can_be_set_on_a_file_that_never_had_the_column():
    rows, _ = fittings.apply_edits(_rows(), [{"ref": "T1", "watts_per_metre": "9.6"}])
    assert rows[1]["Wattage_W_per_m"] == "9.6"


def test_a_column_this_module_never_heard_of_survives_an_edit():
    rows = _rows()
    rows[0]["SomeFutureColumn"] = "keep me"
    out, _ = fittings.apply_edits(rows, [{"ref": "F1", "description": "Changed"}])
    assert out[0]["SomeFutureColumn"] == "keep me"
    assert out[0]["Description"] == "Changed"


def test_the_editor_shows_the_same_assumption_as_the_review_sheet():
    """Two different fallbacks would mean the sheet says DALI and the editor
    opens on reverse phase -- and saving would then silently pick the editor's."""
    rows = [
        {"FixtureRef": "F1", "Description": "Ceiling downlight", "LoadTypeID": ""},
        {"FixtureRef": "F2", "Description": "Decorative pendant", "LoadTypeID": ""},
        {"FixtureRef": "F3", "Description": "Ceiling downlight", "LoadTypeID": "119"},
    ]
    out = fittings.summarise(rows, [])
    assert [f["load_type_id"] for f in out] == [16, 119, 119]
    assert [("LoadTypeID" in f["assumed"]) for f in out] == [True, True, False]
