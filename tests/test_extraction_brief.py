"""Tests for the extraction brief -- the instructions handed to the AI.

The brief is a document, not code, so nothing else would notice it regressing.
Each assertion here corresponds to a defect that reached a real drawing set.
"""
from hwwriter import ingest


def test_brief_does_not_ask_for_equipment():
    # The shell carries no processors, modules or comm links: a proposed panel
    # layout fails validation on every row. This once produced 130 problems and
    # wrote nothing on an otherwise good extraction.
    p = ingest.build_prompt(keypad_family="Palladiom")
    for banned in ("===== Modules.csv =====", "===== OutputAssignments.csv ====="):
        assert banned not in p, f"the brief is proposing equipment again: {banned}"
    assert "Modules.csv" not in ingest.FILES
    assert "OutputAssignments.csv" not in ingest.FILES


def test_brief_requests_every_file_the_reader_expects():
    p = ingest.build_prompt(keypad_family="Palladiom")
    for name in ingest.FILES:
        assert f"===== {name} =====" in p, f"the brief never asks for {name}"
    assert "===== REPORT =====" in p


def test_brief_carries_the_reference_catalogues():
    # Without these the AI invents load types and keypad model names.
    p = ingest.build_prompt(keypad_family="Palladiom")
    for name in ingest.REFERENCES:
        assert name in p


def test_chosen_family_is_a_default_not_an_absolute():
    # A real specification mixes: one range generally, a smarter range in the
    # principal rooms. Flattening that to one family overrides the drawings.
    p = ingest.build_prompt(keypad_family="Palladiom")
    assert "Palladiom" in p
    low = p.lower()
    assert "default" in low
    assert "explicit instruction wins" in low


def test_brief_treats_keypad_sizes_as_hard_limits():
    # Palladiom tops out at 4 buttons. A catalogue that wrongly claimed 6 let
    # the AI programme keypads that do not exist.
    p = ingest.build_prompt(keypad_family="Palladiom")
    low = p.lower()
    assert "hard limits" in low
    assert "second keypad" in low


def test_brief_says_dimming_range_is_not_a_range():
    p = ingest.build_prompt(keypad_family="Palladiom")
    assert "DimmingRange" in p
    assert "leave `dimmingrange` blank" in p.lower()


def test_brief_budgets_the_fixture_research():
    # A live probe measured EIGHT searches satisfying one fitting, and a real
    # read burned its whole 60-search allowance -- each search re-reading the
    # entire context. The brief now sets the norm at about two per fitting,
    # with blank-and-noted as the stopping rule.
    p = ingest.build_prompt(keypad_family="Palladiom")
    low = p.lower()
    assert "budget your searches" in low
    assert "two per fitting" in low


def test_research_off_revokes_the_brief_s_instruction_to_search():
    """The brief tells the model it has web search and must look fittings up.

    With research off it has no such tool, so the instruction must be revoked
    out loud -- left standing, the model either fills a wattage from memory
    (the exact thing the brief forbids, and a wrong one can overload a dimmer)
    or wastes the read reaching for a tool that is not there.
    """
    p = ingest.build_prompt(keypad_family="Palladiom", research=False)
    low = p.lower()
    assert "fixture research is off" in low
    assert "overrides the fixture-research instructions in the brief" in low
    assert "no web search tool" in low
    assert "never fill a wattage from memory" in low
    # and the blank must be flagged, not silently empty
    assert "not stated on drawings" in low


def test_research_on_is_the_default_and_leaves_the_brief_intact():
    on = ingest.build_prompt(keypad_family="Palladiom")
    assert ingest.build_prompt(keypad_family="Palladiom", research=True) == on
    assert "fixture research is off" not in on.lower()
    assert "you have web search" in ingest.extraction_brief().lower(), (
        "the brief no longer offers search -- the research-off override may be stale")


def test_run_passes_the_research_choice_into_the_prompt():
    # Guards the call site, not just the function: the keypad-family bug was
    # exactly this shape -- right function, argument dropped on the last step.
    import inspect
    src = inspect.getsource(ingest.run)
    assert "build_prompt(notes, keypad_family, research, brief)" in src, (
        "run() builds the prompt without the research choice or the project's "
        "own brief -- turning research off would leave the model still being "
        "told to search, and a pinned project would silently be re-read with "
        "whatever the brief has since become")


def test_brief_says_button_labels_are_engraving():
    p = ingest.build_prompt(keypad_family="Palladiom")
    assert "engraving" in p.lower()


def test_no_family_chosen_still_asks_for_one_family_and_a_reason():
    p = ingest.build_prompt()
    low = p.lower()
    assert "has not chosen" in low
    assert "which you chose" in low or "which one you chose" in low


def test_engineer_notes_reach_the_model():
    p = ingest.build_prompt(notes="Ignore the garage entirely")
    assert "Ignore the garage entirely" in p


def test_the_chosen_family_actually_reaches_the_model():
    """The dropdown must not be decorative.

    From the first release until 2026-08-05 the call site was
    build_prompt(notes) -- the family argument was simply dropped. The app
    collected the choice, validated it, passed it to run(), and then it fell
    off the last step, so EVERY read told the model "the engineer has not
    chosen a default" and let it pick. Jobs looked correct whenever the
    drawings happened to name the same family, which is how it survived so
    long.
    """
    chosen = ingest.build_prompt(notes="", keypad_family="Palladiom")
    assert "**Palladiom**" in chosen
    assert "has not chosen" not in chosen, (
        "the family was supplied but the model is still being told to pick one")


def test_run_passes_the_family_through_to_the_prompt(monkeypatch):
    # Guards the actual call site, not just the function it calls.
    import inspect
    src = inspect.getsource(ingest.run)
    assert "build_prompt(notes, keypad_family" in src, (
        "run() builds the prompt without the family -- the choice is lost")


def test_omitting_the_family_still_asks_the_model_to_choose_and_say_so():
    free = ingest.build_prompt(notes="")
    assert "has not chosen" in free
    assert "which you chose" in free or "which one you chose" in free


def test_no_keypads_writes_none_but_keeps_the_scenes():
    """"No keypads" must not quietly cost the engineer the scenes too.

    Scenes are the expensive half to do by hand -- deciding and setting levels
    for every scene in every room. Placing a keypad in Designer is quick. So
    the choice suppresses Keypads.csv and Buttons.csv and nothing else.
    """
    p = ingest.build_prompt(keypad_family=ingest.NO_KEYPADS)
    low = p.lower()
    assert "no rows at all" in low
    assert "keypads.csv" in low and "buttons.csv" in low
    assert "still write `scenes.csv` in full" in low, (
        "the scenes must survive -- they are the reason the read is worth paying for")


def test_no_keypads_does_not_also_ask_the_model_to_choose_a_family():
    # Asking for a family in the same breath invites the model to propose
    # keypads anyway.
    p = ingest.build_prompt(keypad_family=ingest.NO_KEYPADS)
    assert "has not chosen" not in p.lower()
    assert "_family_rules" not in p
    assert "In the report, state the default family" not in p


def test_no_keypads_explicitly_overrides_the_brief():
    """The brief itself tells the model to size keypads to a room's scenes.

    It is prepended to every prompt, so "write no keypads" lands as a
    contradiction unless it says out loud which instruction wins. Left
    implicit, the model is free to resolve it either way.
    """
    p = ingest.build_prompt(keypad_family=ingest.NO_KEYPADS)
    assert "sizes are hard limits" in ingest.extraction_brief().lower(), (
        "the brief no longer sizes keypads -- this override may be redundant now")
    assert "overrides the keypad instructions in the brief" in p.lower()


def test_no_keypads_is_a_distinct_choice_from_letting_the_ai_pick():
    # "" already means "choose one and say which". If NO_KEYPADS ever collapses
    # to "", the app silently starts inventing keypads again.
    assert ingest.NO_KEYPADS
    assert ingest.NO_KEYPADS != ""
    assert ingest.build_prompt(keypad_family=ingest.NO_KEYPADS) != ingest.build_prompt()


def test_no_keypads_still_asks_for_both_sections_to_be_emitted():
    """Found by Gemini in review, verified in the code.

    run() requires a section for every file in FILES, Keypads.csv and
    Buttons.csv among them, and raises if one is absent. Telling the model to
    write no rows invites it to drop the section entirely -- which would throw
    away the WHOLE read, schedules included, after the engineer has paid for it.
    """
    p = ingest.build_prompt(keypad_family=ingest.NO_KEYPADS)
    assert "Keypads.csv" in ingest.FILES and "Buttons.csv" in ingest.FILES
    assert "still emit both sections" in p.lower()


def test_a_missing_keypad_section_has_a_header_to_fall_back_on():
    # The belt to the prompt's braces: if the model drops the section anyway,
    # run() writes these headers instead of discarding the read.
    for name in ("Keypads.csv", "Buttons.csv"):
        assert name in ingest.KEYPAD_HEADERS
        assert ingest.KEYPAD_HEADERS[name].startswith("Action,")
        assert ingest.KEYPAD_HEADERS[name].endswith("\n")
        assert len(ingest.KEYPAD_HEADERS[name].strip().split("\n")) == 1, (
            "the fallback must be a header and no rows")


def test_the_fallback_headers_match_what_the_writer_reads():
    # A header that does not match the real schema builds a broken project.
    import csv
    import os
    for name in ("Keypads.csv", "Buttons.csv"):
        sample = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "examples", "test-house", name)
        with open(sample, newline="", encoding="utf-8-sig") as fh:
            real = next(csv.reader(fh))
        assert ingest.KEYPAD_HEADERS[name].strip().split(",") == real, (
            f"the {name} fallback header has drifted from the real schema")


# --- "any designer's plans" ----------------------------------------------
# Each of these is a gap measured against real jobs, not a hypothetical. The
# brief is sent verbatim as the system prompt, so a rule that is not in it does
# not exist.

def _brief():
    # Whitespace-collapsed: the brief is wrapped markdown, so a phrase that
    # reads as one sentence is often split across two lines in the file.
    import re
    return re.sub(r"\s+", " ", ingest.extraction_brief().lower())


def test_the_brief_says_which_source_wins():
    """Documents first, standing rules where they are silent, a flagged blank
    where neither answers it. Without this the rules below read as overrides of
    the drawings rather than fallbacks for them."""
    low = _brief()
    assert "which source wins" in low
    assert "what the documents state wins" in low
    assert "where they are silent" in low


def test_the_brief_teaches_unaffected():
    """Used ZERO times across 1,204 real scene rows on three jobs, because the
    brief never mentioned it -- so every circuit got a level and lights the
    designer meant to leave alone were switched."""
    low = _brief()
    assert "unaffected" in low
    assert "does not touch this circuit" in low
    assert "built as a brightness" in low, (
        "the warning that everything else IS a brightness is the point")


def test_the_brief_covers_linear_product():
    """"Count the symbols" turns 10 m of 9.6 W/m tape into 9.6 W."""
    low = _brief()
    assert "wattage_w_per_m" in low and "runlength_m" in low
    assert "per metre" in low
    # D13: no fallback maximum run. That belongs to a different system.
    assert "no maximum run length" in low


def test_the_brief_says_how_to_group_circuits_when_none_are_drawn():
    """A preliminary set with no circuits is normal from any designer, and the
    stated default -- leave the cell blank -- produced a house with no
    circuits at all."""
    low = _brief()
    assert "how to group fittings into circuits" in low
    assert "not drawn at all" in low
    assert "inferred" in low
    assert "produce that many circuits in that room" in low, (
        "a stated circuit count is an instruction")


def test_the_brief_states_the_two_never_mix_rules():
    low = _brief()
    assert "never mix fitting types" in low
    assert "never mix control types" in low
    assert "lighting design rule" in low, (
        "one fitting type per circuit must read as the rule it is, or a future "
        "reviewer will 'fix' it as a limitation")


def test_the_brief_states_the_phase_load_ceiling_and_its_scope():
    low = _brief()
    assert "800 w" in low and "500 w" in low
    assert "first output" in low
    assert "no per-circuit load ceiling" in low, (
        "the ceiling must be scoped to phase -- DALI and switched have none")


def test_the_brief_says_a_supplied_specification_beats_a_search():
    """The 2026-08-08 House A read only used the 45 MB the lighting designer
    specification because a note was written by hand telling it to."""
    low = _brief()
    assert "spec sheet" in low
    assert "beats anything you could find by searching" in low
    assert "partial" in low


def test_the_brief_handles_a_legend_that_does_not_cover_the_whole_set():
    """Legends are declared to apply across all supplied PDFs while fixture
    refs must be globally unique: two packages both starting at 'A' either
    merge unlike fittings or fail."""
    low = _brief()
    assert "legends that do not cover the whole set" in low
    assert "ext-a" in low


def test_the_brief_preserves_a_designer_fade():
    low = _brief()
    assert "where the designer states a fade time" in low
    assert "their figure" in low


def test_the_brief_allows_a_room_name_to_repeat_on_another_floor():
    low = _brief()
    assert "may repeat on another floor" in low
    assert "first floor > wc" in low


def test_the_brief_allows_a_blank_fixture_and_a_shared_circuit():
    low = _brief()
    assert "leave `fixtureref` blank" in low
    assert "additionalareas" in low
    assert "it is one dimmer output" in low


def test_the_brief_tells_the_model_a_blank_is_safe():
    """The 'assumed' marking on the review sheet is driven by a cell being
    BLANK. A model that helpfully fills in the usual 119 / 5 / 90 to make the
    row look complete destroys the distinction the sheet is built on."""
    low = _brief()
    assert "leave those cells blank" in low
    assert "marked on the review sheet as assumed" in low
    assert "indistinguishable from one the designer specified" in low


def test_the_brief_says_the_default_is_dali_not_phase():
    """James, 2026-08-08. Defaulting everything to mains reverse phase was
    wrong on most fittings of most jobs, and wrong in the direction that needs
    rewiring: an architectural circuit on a phase dimmer."""
    low = _brief()
    assert "the default applied to a blank is **dali**" in low
    assert "fittings that take a lamp" in low
    assert "table and floor lamps, decorative pendants" in low
    assert "says plainly what the fitting is" in low, (
        "the description decides the assumption, so the brief must ask for a "
        "description that can carry it")


def test_the_brief_insists_every_circuit_names_a_fitting():
    """The 2026-08-08 House A read catalogued 31 fittings and then bound NONE
    of its 194 circuits to any of them -- across all 47 rooms, principal
    bedroom and drawing room included, not just back-of-house. The brief
    offered "leave it blank" as an ordinary option and the model took it
    everywhere, producing a file with the whole house missing from it."""
    low = _brief()
    assert "every circuit must name a fitting" in low
    assert "only leave `fixtureref` blank as a last resort" in low
    assert "stop and say so at the top of your report" in low
    # And the case that has no legend entry to point at in the first place.
    assert "not in the legend still gets a catalogue row" in low
    assert "5a socket circuit" in low


def test_the_brief_treats_a_stated_circuit_count_as_an_instruction():
    """The the lighting designer estimate for House A states 521 circuits room by room,
    split by control type. The read produced 194 and matched the stated count
    in 3 rooms of 47 -- and consolidated twenty switched cupboard circuits into
    one. Every missing circuit is a dimmer channel found on site instead."""
    low = _brief()
    assert "a stated circuit count is an instruction, not a hint" in low
    assert "produce that many circuits in that room" in low
    assert "honour the split by control type" in low
    assert "never consolidate" in low
    assert "statedcircuits" in low, "and it must be recorded so the sheet can check it"


# ---------------------------------------------- which brief read which project

def test_the_brief_declares_a_version_and_it_is_readable():
    # Without a declared version, "which brief read this?" can only be answered
    # by diffing two documents -- which nobody does.
    assert ingest.brief_version() >= 1, (
        "docs/FROM-PLANS-TO-CSV.md no longer declares <!-- brief-version: N -->")


def test_the_fingerprint_changes_when_the_brief_does():
    # The version catches a deliberate change; the fingerprint catches an edit
    # that forgot to bump it. Both are recorded because they answer different
    # questions.
    a = ingest.brief_fingerprint("one")
    assert a != ingest.brief_fingerprint("one ")
    assert a == ingest.brief_fingerprint("one")
    assert len(a) == 12


def test_a_project_reads_with_the_current_brief_until_it_is_pinned(tmp_path):
    text, pinned = ingest.brief_for_project(str(tmp_path))
    assert not pinned
    assert text == ingest.extraction_brief()


def test_a_pinned_project_keeps_reading_with_the_brief_that_first_read_it(tmp_path):
    # The point of the whole mechanism: improvements reach everyone quickly,
    # and a job somebody has already checked and issued does not silently
    # change meaning underneath them.
    ingest.pin_brief(str(tmp_path), "## The extraction brief\nolder wording")
    text, pinned = ingest.brief_for_project(str(tmp_path))
    assert pinned and text == "## The extraction brief\nolder wording"
    assert text != ingest.extraction_brief()


def test_pinning_never_overwrites_the_brief_already_pinned(tmp_path):
    # A second read must not re-pin to today's brief -- that would quietly undo
    # the pin on exactly the jobs it exists to protect.
    ingest.pin_brief(str(tmp_path), "first")
    ingest.pin_brief(str(tmp_path), "second")
    assert ingest.brief_for_project(str(tmp_path))[0] == "first"


def test_a_re_read_does_not_archive_the_pin(tmp_path):
    # archive_previous moves the whole previous read aside. If it took the pin
    # with it, every re-read would silently unpin the project.
    ingest.pin_brief(str(tmp_path), "pinned text")
    (tmp_path / "Areas.csv").write_text("Action,AreaName\n")
    ingest.archive_previous(str(tmp_path), say=lambda *a: None)
    assert ingest.brief_for_project(str(tmp_path))[1] is True


def test_the_provenance_record_names_the_brief_and_not_the_project(tmp_path):
    import json
    brief = "## The extraction brief\n<!-- brief-version: 7 -->\nwords"
    ingest.write_provenance(str(tmp_path), "plans", brief, model="claude-opus-5")
    record = json.loads((tmp_path / ingest.PROVENANCE).read_text())
    assert record["brief_version"] == 7
    assert record["brief_fingerprint"] == ingest.brief_fingerprint(brief)
    assert record["source"] == "plans" and record["model"] == "claude-opus-5"
    assert record["read_at"].endswith("+00:00")
    # Nothing about the house. This record is the shape a contribution would
    # take, and no room, client or file name may ever be in it.
    assert "project" not in record and "client" not in record


def test_a_failure_to_write_provenance_never_loses_the_read(tmp_path):
    # It is a record ABOUT the work, not the work. A read already paid for must
    # survive a read-only folder.
    missing = tmp_path / "does" / "not" / "exist"
    ingest.write_provenance(str(missing), "plans", None)     # must not raise


# --------------------------------------- what the read said, before it was edited

def _write(folder, name, text):
    (folder / name).write_text(text)


def test_the_read_is_snapshotted_before_anyone_edits_it(tmp_path):
    _write(tmp_path, "LoadSchedule.csv",
           "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
           "New,Kitchen,Downlights,DL1,14\n")
    assert ingest.snapshot_as_read(str(tmp_path)) == 1
    assert (tmp_path / ingest.AS_READ / "LoadSchedule.csv").exists()


def test_an_edit_is_reported_as_a_field_name_and_never_as_a_value(tmp_path):
    # This is the shape the design note fixes for anything shareable. Building
    # it this way from the start means the values are not sitting in a
    # structure waiting for somebody to decide to send them.
    _write(tmp_path, "LoadSchedule.csv",
           "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
           "New,Kitchen,Downlights,DL1,\n")
    ingest.snapshot_as_read(str(tmp_path))
    _write(tmp_path, "LoadSchedule.csv",
           "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
           "New,Kitchen,Downlights,DL1,14\n")
    edits = ingest.edits_since_read(str(tmp_path))["LoadSchedule.csv"]
    assert edits["changed"] == {"NumberOfFixtures": 1}
    assert "14" not in repr(edits) and "Kitchen" not in repr(edits)


def test_rows_are_matched_on_identity_not_on_position(tmp_path):
    # A spreadsheet round-trip reorders rows. Comparing by position would call
    # every row changed and the signal would be worthless.
    head = "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
    _write(tmp_path, "LoadSchedule.csv",
           head + "New,Kitchen,Downlights,DL1,14\nNew,Hall,Downlights,DL1,4\n")
    ingest.snapshot_as_read(str(tmp_path))
    _write(tmp_path, "LoadSchedule.csv",
           head + "New,Hall,Downlights,DL1,4\nNew,Kitchen,Downlights,DL1,14\n")
    edits = ingest.edits_since_read(str(tmp_path))["LoadSchedule.csv"]
    assert edits == {"added": 0, "removed": 0, "changed": {}}


def test_added_and_removed_circuits_are_counted(tmp_path):
    head = "Action,AreaName,ZoneName,FixtureRef,NumberOfFixtures\n"
    _write(tmp_path, "LoadSchedule.csv", head + "New,Kitchen,Downlights,DL1,14\n")
    ingest.snapshot_as_read(str(tmp_path))
    _write(tmp_path, "LoadSchedule.csv", head + "New,Hall,Downlights,DL1,4\n")
    edits = ingest.edits_since_read(str(tmp_path))["LoadSchedule.csv"]
    assert edits["added"] == 1 and edits["removed"] == 1


def test_no_snapshot_means_no_claim_about_edits(tmp_path):
    _write(tmp_path, "LoadSchedule.csv", "Action,AreaName,ZoneName\nNew,K,D\n")
    assert ingest.edits_since_read(str(tmp_path)) == {}
