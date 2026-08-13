"""Editing and copying scenes.

The trap this file exists for: a scene is one row per (area, scene, CIRCUIT).
Every operation here has to fan out over the target room's own circuits, and
several of them can quietly destroy hand-tuned levels if written carelessly.
"""
import pytest

from hwwriter import scenes

LOADS = [
    {"AreaName": "Kitchen", "ZoneName": "Kitchen Downlights"},
    {"AreaName": "Kitchen", "ZoneName": "Kitchen Island"},
    {"AreaName": "Kitchen", "ZoneName": "Kitchen Downlights"},   # repeat
    {"AreaName": "Study", "ZoneName": "Study Downlights"},
    {"AreaName": "Study", "ZoneName": "Study Wall Lights"},
    {"AreaName": "Study", "ZoneName": "Study Desk"},
    {"AreaName": "Cupboard", "ZoneName": ""},                     # no circuits
]


def _scene(area, name, number, zone, level, command="SetLevel"):
    return {"Action": "NEW", "AreaName": area, "SceneName": name,
            "SceneNumber": number, "ZoneName": zone, "CommandType": command,
            "Level_pct": level, "Fade_seconds": "2", "Delay_seconds": "0"}


KITCHEN = [
    _scene("Kitchen", "Bright", "1", "Kitchen Downlights", "100"),
    _scene("Kitchen", "Bright", "1", "Kitchen Island", "100"),
    _scene("Kitchen", "Soft", "2", "Kitchen Downlights", "30"),
    _scene("Kitchen", "Soft", "2", "Kitchen Island", "30"),
    _scene("Kitchen", "Off Scene", "0", "Kitchen Downlights", "0"),
    _scene("Kitchen", "Off Scene", "0", "Kitchen Island", "0"),
]


def test_zones_are_listed_once_per_area_in_order():
    z = scenes.zones_by_area(LOADS)
    assert z["Kitchen"] == ["Kitchen Downlights", "Kitchen Island"]
    assert len(z["Study"]) == 3
    assert "Cupboard" not in z


def test_the_representative_level_ignores_circuits_the_scene_leaves_alone():
    # Unaffected is not a brightness. Letting it vote makes a scene that skips
    # two circuits summarise as blank.
    rows = [_scene("Kitchen", "Soft", "2", "A", "30"),
            _scene("Kitchen", "Soft", "2", "B", "30"),
            _scene("Kitchen", "Soft", "2", "C", "unaffected", command="Unaffected")]
    assert scenes.representative_level(rows) == "30"


def test_a_hand_tuned_scene_is_flagged_as_mixed():
    rows = KITCHEN + [_scene("Kitchen", "Late", "3", "Kitchen Downlights", "10"),
                      _scene("Kitchen", "Late", "3", "Kitchen Island", "45")]
    late = [s for s in scenes.summarise(rows)[0]["scenes"] if s["name"] == "Late"][0]
    assert late["mixed"] is True, "a scene with different levels per circuit must warn"
    bright = [s for s in scenes.summarise(rows)[0]["scenes"] if s["name"] == "Bright"][0]
    assert bright["mixed"] is False


def test_scenes_are_listed_in_scene_number_order():
    names = [s["name"] for s in scenes.summarise(KITCHEN)[0]["scenes"]]
    assert names == ["Off Scene", "Bright", "Soft"]


# ------------------------------------------------------------------ copying

def test_copying_fans_the_scene_list_out_over_the_targets_own_circuits():
    z = scenes.zones_by_area(LOADS)
    out, notes = scenes.copy_scenes(KITCHEN, "Kitchen", ["Study"], z)
    study = [r for r in out if r["AreaName"] == "Study"]
    # 3 scenes x 3 Study circuits -- NOT the Kitchen's 2 circuits.
    assert len(study) == 9
    assert {r["ZoneName"] for r in study} == {"Study Downlights", "Study Wall Lights",
                                             "Study Desk"}
    assert {r["SceneName"] for r in study} == {"Bright", "Soft", "Off Scene"}
    assert notes


def test_copying_carries_the_level_of_each_scene_not_one_level_for_all():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.copy_scenes(KITCHEN, "Kitchen", ["Study"], z)
    got = {(r["SceneName"], r["Level_pct"]) for r in out if r["AreaName"] == "Study"}
    assert got == {("Bright", "100"), ("Soft", "30"), ("Off Scene", "0")}


def test_copying_keeps_scene_numbers_so_the_house_is_consistent():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.copy_scenes(KITCHEN, "Kitchen", ["Study"], z)
    pairs = {(r["SceneName"], r["SceneNumber"]) for r in out if r["AreaName"] == "Study"}
    assert pairs == {("Bright", "1"), ("Soft", "2"), ("Off Scene", "0")}


def test_copying_replaces_the_targets_old_scenes_rather_than_doubling_them():
    z = scenes.zones_by_area(LOADS)
    rows = KITCHEN + [_scene("Study", "Whatever", "7", "Study Desk", "50")]
    out, _ = scenes.copy_scenes(rows, "Kitchen", ["Study"], z)
    assert "Whatever" not in {r["SceneName"] for r in out if r["AreaName"] == "Study"}


def test_copying_never_touches_the_source_or_an_unrelated_room():
    z = scenes.zones_by_area(LOADS)
    rows = KITCHEN + [_scene("Hall", "Night", "3", "Hall Downlights", "5")]
    out, _ = scenes.copy_scenes(rows, "Kitchen", ["Study"], z)
    assert [r for r in out if r["AreaName"] == "Kitchen"] == KITCHEN
    assert len([r for r in out if r["AreaName"] == "Hall"]) == 1


def test_a_room_with_no_circuits_is_skipped_and_said_so():
    """Writing scenes against circuits that do not exist builds a broken file.

    It has to be reported, not silently dropped -- the engineer picked that
    room deliberately.
    """
    z = scenes.zones_by_area(LOADS)
    out, notes = scenes.copy_scenes(KITCHEN, "Kitchen", ["Study", "Cupboard"], z)
    assert not [r for r in out if r["AreaName"] == "Cupboard"]
    assert any("Cupboard" in n and "skipped" in n for n in notes)


def test_copying_a_room_onto_itself_is_not_destructive():
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError):
        scenes.copy_scenes(KITCHEN, "Kitchen", ["Kitchen"], z)
    out, _ = scenes.copy_scenes(KITCHEN, "Kitchen", ["Kitchen", "Study"], z)
    assert [r for r in out if r["AreaName"] == "Kitchen"] == KITCHEN


def test_unknown_columns_survive_a_copy():
    # The CSV contract can grow. An edit must not quietly drop a column.
    z = scenes.zones_by_area(LOADS)
    rows = [dict(r, Notes="check on site") for r in KITCHEN]
    out, _ = scenes.copy_scenes(rows, "Kitchen", ["Study"], z)
    assert all("Notes" in r for r in out if r["AreaName"] == "Study")


# ------------------------------------------------------------------- edits

def test_renaming_a_scene_does_not_flatten_its_hand_tuned_levels():
    """The one that would quietly ruin a job.

    Rewriting Level_pct on every save means renaming "Late" to "Evening"
    silently sets every circuit to the commonest level and throws the tuning
    away, with nothing on screen to say it happened.
    """
    z = scenes.zones_by_area(LOADS)
    rows = [_scene("Kitchen", "Late", "3", "Kitchen Downlights", "10"),
            _scene("Kitchen", "Late", "3", "Kitchen Island", "45")]
    out, _ = scenes.apply_edits(rows, "Kitchen", [
        {"was_number": "3", "was_name": "Late", "number": "3", "name": "Evening",
         "level": "10"}], z)
    assert {r["Level_pct"] for r in out} == {"10", "45"}
    assert {r["SceneName"] for r in out} == {"Evening"}


def test_changing_the_level_sets_it_on_every_circuit_and_says_so():
    z = scenes.zones_by_area(LOADS)
    out, notes = scenes.apply_edits(KITCHEN, "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Soft",
         "level": "45"}], z)
    soft = [r for r in out if r["SceneName"] == "Soft"]
    assert {r["Level_pct"] for r in soft} == {"45"}
    assert any("45" in n for n in notes)


def test_a_scene_not_mentioned_in_the_edit_is_left_exactly_alone():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.apply_edits(KITCHEN, "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Soft",
         "level": "45"}], z)
    bright = [r for r in out if r["SceneName"] == "Bright"]
    assert len(bright) == 2 and {r["Level_pct"] for r in bright} == {"100"}


def test_deleting_removes_every_row_of_that_scene():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.apply_edits(KITCHEN, "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "delete": True}], z)
    assert not [r for r in out if r["SceneName"] == "Soft"]
    assert len([r for r in out if r["SceneName"] == "Bright"]) == 2


def test_adding_a_scene_covers_every_circuit_in_the_room():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.apply_edits(KITCHEN, "Kitchen", [
        {"number": "4", "name": "Cooking", "level": "80"}], z)
    cooking = [r for r in out if r["SceneName"] == "Cooking"]
    assert len(cooking) == 2
    assert {r["Level_pct"] for r in cooking} == {"80"}


def test_an_edit_never_leaks_into_another_room():
    z = scenes.zones_by_area(LOADS)
    rows = KITCHEN + [_scene("Study", "Soft", "2", "Study Desk", "30")]
    out, _ = scenes.apply_edits(rows, "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Dim",
         "level": "30"}], z)
    assert [r["SceneName"] for r in out if r["AreaName"] == "Study"] == ["Soft"]


@pytest.mark.parametrize("bad", ["", "31", "-1", "two", "1.5"])
def test_a_bad_scene_number_is_refused_not_silently_coerced(bad):
    # SceneNumber is 0-30 in the contract; anything else builds a broken file.
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"was_number": "2", "was_name": "Soft", "number": bad, "name": "Soft"}], z)


@pytest.mark.parametrize("bad", ["101", "-5", "half"])
def test_a_bad_level_is_refused(bad):
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Soft",
             "level": bad}], z)


def test_a_scene_with_no_name_is_refused():
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"was_number": "2", "was_name": "Soft", "number": "2", "name": "  "}], z)


def test_a_copy_does_not_delete_rows_the_shell_already_has():
    """EXISTING means "already in the Starter Shell", not "ours to replace".

    Dropping those rows changes what the built file means, and the engineer
    would have no way to know it happened.
    """
    z = scenes.zones_by_area(LOADS)
    existing = dict(_scene("Study", "Shell Scene", "9", "Study Desk", "20"),
                    Action="EXISTING")
    out, _ = scenes.copy_scenes(KITCHEN + [existing], "Kitchen", ["Study"], z)
    assert existing in out, "an EXISTING row was destroyed by a copy"


# ------------------------------------------- the dialect the AI actually uses

ZONELEVEL = [
    _scene("Kitchen", "Off", "0", "Ceiling Downlights", "0", command="ZoneLevel"),
    _scene("Kitchen", "Off", "0", "Worktop Downlights", "0", command="ZoneLevel"),
    _scene("Kitchen", "Bright", "1", "Ceiling Downlights", "100", command="ZoneLevel"),
    _scene("Kitchen", "Bright", "1", "Worktop Downlights", "100", command="ZoneLevel"),
]


def test_zonelevel_rows_count_as_levels():
    """The documented vocabulary says SetLevel; the AI writes ZoneLevel.

    The writer accepts both -- it treats everything except Unaffected as a
    level. Matching on SetLevel found NOTHING on a real House B project, so
    every scene read as blank and a copy would have carried empty levels into
    every room in the house. Caught only by running it against real output.
    """
    assert scenes.representative_level(ZONELEVEL[:2]) == "0"
    assert scenes.representative_level(ZONELEVEL[2:]) == "100"


def test_a_copy_keeps_the_projects_own_command_dialect():
    z = {"Kitchen": ["Ceiling Downlights", "Worktop Downlights"],
         "Study": ["Study Downlights"]}
    out, _ = scenes.copy_scenes(ZONELEVEL, "Kitchen", ["Study"], z)
    study = [r for r in out if r["AreaName"] == "Study"]
    assert {r["CommandType"] for r in study} == {"ZoneLevel"}, (
        "a copy introduced a second dialect into the file")
    assert {(r["SceneName"], r["Level_pct"]) for r in study} == {("Off", "0"),
                                                                ("Bright", "100")}


def test_a_new_scene_uses_the_dialect_already_in_the_file():
    z = {"Kitchen": ["Ceiling Downlights", "Worktop Downlights"]}
    out, _ = scenes.apply_edits(ZONELEVEL, "Kitchen", [
        {"number": "2", "name": "Soft", "level": "30"}], z)
    soft = [r for r in out if r["SceneName"] == "Soft"]
    assert {r["CommandType"] for r in soft} == {"ZoneLevel"}


def test_setting_a_level_never_touches_an_unaffected_circuit():
    z = {"Kitchen": ["A", "B"]}
    rows = [_scene("Kitchen", "Soft", "2", "A", "30", command="ZoneLevel"),
            _scene("Kitchen", "Soft", "2", "B", "unaffected", command="Unaffected")]
    out, _ = scenes.apply_edits(rows, "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Soft",
         "level": "60"}], z)
    by_zone = {r["ZoneName"]: r["Level_pct"] for r in out}
    assert by_zone["A"] == "60"
    assert by_zone["B"] == "unaffected", "a circuit the scene skips was given a level"


# ------------------------- defects found by cross-AI review, 2026-08-05
# Codex and Gemini reviewed v1.1.5 independently. Each of these was verified
# by running it before being fixed; every one of them saved cleanly, built,
# and produced the wrong result in a real house.

def test_two_scenes_in_a_room_cannot_share_a_number():
    # They merge into one scene when the schedule is loaded. Nothing downstream
    # complains: _write_scenes renders a review, it never runs validate().
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError, match="numbered"):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"was_number": "2", "was_name": "Soft", "number": "1", "name": "Soft"}], z)


def test_two_scenes_in_a_room_cannot_share_a_name():
    """A keypad button recalls a scene BY NAME.

    Add a second "Relax" and the builder maps (area, name) to whichever number
    comes last -- so an existing Relax button silently starts recalling the
    other scene. Saves, builds, wrong house.
    """
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError, match="two scenes called"):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"number": "7", "name": "Soft", "level": "20"}], z)


def test_a_shell_scene_cannot_be_edited_through_the_editor():
    # The builder ignores EXISTING rows, so the edit is written to the CSV and
    # then has no effect -- the editor would report success and change nothing.
    z = scenes.zones_by_area(LOADS)
    shell = dict(_scene("Kitchen", "Shell", "5", "Kitchen Island", "10"),
                 Action="EXISTING")
    with pytest.raises(ValueError, match="already in the shell"):
        scenes.apply_edits([shell], "Kitchen", [
            {"was_number": "5", "was_name": "Shell", "number": "5",
             "name": "Renamed"}], z)


def test_a_shell_scene_survives_an_edit_to_its_neighbours():
    z = scenes.zones_by_area(LOADS)
    shell = dict(_scene("Kitchen", "Shell", "5", "Kitchen Island", "10"),
                 Action="EXISTING")
    out, _ = scenes.apply_edits(KITCHEN + [shell], "Kitchen", [
        {"was_number": "2", "was_name": "Soft", "number": "2", "name": "Dim"}], z)
    assert shell in out


def test_the_same_room_ticked_twice_is_copied_once():
    z = scenes.zones_by_area(LOADS)
    out, _ = scenes.copy_scenes(KITCHEN, "Kitchen", ["Study", "Study"], z)
    assert len([r for r in out if r["AreaName"] == "Study"]) == 9


def test_a_source_room_with_a_clash_refuses_to_propagate_it():
    z = scenes.zones_by_area(LOADS)
    dirty = KITCHEN + [_scene("Kitchen", "Bright", "6", "Kitchen Island", "90")]
    with pytest.raises(ValueError):
        scenes.copy_scenes(dirty, "Kitchen", ["Study"], z)


# ------------------------- second review round, 2026-08-05
# Gemini found the big one and Codex missed it: the editor rewrote Scenes.csv
# and never touched Buttons.csv. Reproduced on the real House B project --
# renaming ONE scene orphaned two buttons, one of them on a keypad in a
# DIFFERENT room. The save reported success; the build then refused the whole
# project. The editor's headline feature broke any job that has keypads.

BUTTONS = [
    {"Action": "NEW", "KeypadName": "Kitchen > By Rear Door", "ButtonNumber": "2",
     "ButtonLabel": "Relax", "ActionType": "RecallAreaScene", "TargetArea": "Kitchen",
     "TargetScene": "Relax"},
    {"Action": "NEW", "KeypadName": "Family Room > By Entrance", "ButtonNumber": "4",
     "ButtonLabel": "Kitchen", "ActionType": "RecallAreaScene", "TargetArea": "Kitchen",
     "TargetScene": "Relax"},
    {"Action": "NEW", "KeypadName": "Study > Door", "ButtonNumber": "1",
     "ButtonLabel": "Bright", "ActionType": "RecallAreaScene", "TargetArea": "Study",
     "TargetScene": "Bright"},
]


def test_a_rename_follows_the_scene_onto_every_button_that_recalls_it():
    renamed, removed = scenes.edit_moves([
        {"was_number": "2", "was_name": "Relax", "number": "2", "name": "Evening"}])
    out, notes = scenes.retarget_buttons(BUTTONS, "Kitchen", renamed, removed)
    targets = [(r["KeypadName"], r["TargetScene"]) for r in out]
    assert ("Kitchen > By Rear Door", "Evening") in targets
    # The cross-room one is the trap: the button lives on a Family Room keypad
    # but recalls a Kitchen scene.
    assert ("Family Room > By Entrance", "Evening") in targets
    assert ("Study > Door", "Bright") in targets, "another room was touched"
    assert notes


def test_deleting_a_scene_removes_the_buttons_that_recalled_it_and_says_so():
    renamed, removed = scenes.edit_moves([
        {"was_number": "2", "was_name": "Relax", "delete": True}])
    out, notes = scenes.retarget_buttons(BUTTONS, "Kitchen", renamed, removed)
    assert [r["TargetScene"] for r in out] == ["Bright"]
    assert any("Removed 2 button" in n for n in notes)


def test_a_button_in_an_untouched_room_is_never_moved():
    renamed, removed = scenes.edit_moves([
        {"was_number": "1", "was_name": "Bright", "number": "1", "name": "Full"}])
    out, _ = scenes.retarget_buttons(BUTTONS, "Kitchen", renamed, removed)
    study = [r for r in out if r["TargetArea"] == "Study"][0]
    assert study["TargetScene"] == "Bright"


def test_edit_moves_reads_renames_and_deletions_but_not_additions():
    renamed, removed = scenes.edit_moves([
        {"was_number": "1", "was_name": "Bright", "number": "1", "name": "Full"},
        {"was_number": "2", "was_name": "Relax", "delete": True},
        {"number": "4", "name": "Cooking", "level": "80"},          # an addition
        {"was_number": "3", "was_name": "Night", "number": "3", "name": "Night"}])
    assert renamed == {"Bright": "Full"}
    assert removed == {"Relax"}


def test_scene_names_clash_case_insensitively():
    """The builder maps scene names in a way that "Relax" and "relax" collide.

    Raw string comparison waved it straight through.
    """
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError, match="two scenes called"):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"number": "7", "name": "soft", "level": "20"}], z)


def test_scene_numbers_clash_after_leading_zeros_are_parsed():
    # "01" and "1" are both scene 1 once the schedule is loaded.
    z = scenes.zones_by_area(LOADS)
    with pytest.raises(ValueError, match="numbered"):
        scenes.apply_edits(KITCHEN, "Kitchen", [
            {"was_number": "2", "was_name": "Soft", "number": "01", "name": "Soft"}], z)
