"""The blank workbook handed to somebody to fill in.

The point of these tests is that the template cannot drift away from the
importer that has to read it. A heading nobody recognises is not a small
cosmetic problem: it lands on somebody else's machine as a mapping screen full
of "not used", and they have to do by hand the work the template existed to
save.

Shaped in two sheets on James's instruction (2026-08-12) — *"the way that you
complete fittings in Lutron is that you add your fittings first and then you
allocate your fittings to a zone"*. The first version was one sheet with the
fitting's details on every circuit row, which meant retyping a downlight's make
and wattage once per circuit that used it.
"""
import io
import zipfile

import pytest

from hwwriter import sheets, template


@pytest.fixture(scope="module")
def book():
    return template.workbook()


def test_it_is_a_real_workbook_excel_will_open(book):
    with zipfile.ZipFile(io.BytesIO(book)) as zf:
        names = set(zf.namelist())
        assert zf.testzip() is None
    for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                 "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                 "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml",
                 "xl/worksheets/sheet3.xml"):
        assert part in names, part


def test_the_sheets_are_in_the_order_the_work_is_done(book):
    """Fittings, then zones, then the guidance -- and the guidance LAST.

    The app opens the last sheet of a workbook by default. With the guidance
    in the middle it would open on the zones sheet by luck; with it first the
    app would have opened on prose. Order is behaviour here, not taste.
    """
    tables = sheets.read_tables(template.FILENAME, book)
    assert [name for name, _ in tables] == [
        "Fittings", "Zones", "How to fill this in"]


# ------------------------------------------------- the importer must know it

def test_every_column_on_both_sheets_is_recognised():
    """A heading the importer does not know is a column mapped by hand."""
    for headings in (template.FITTING_HEADINGS, template.ZONE_HEADINGS):
        mapping = sheets.guess_mapping(headings)
        unmapped = [h for i, h in enumerate(headings) if i not in mapping.values()]
        assert unmapped == [], f"{unmapped} in {headings}"


def test_between_them_the_two_sheets_cover_every_field():
    covered = set(sheets.guess_mapping(template.FITTING_HEADINGS))
    covered |= set(sheets.guess_mapping(template.ZONE_HEADINGS))
    assert covered == set(sheets.FIELD_KEYS)


def test_the_two_sheets_are_told_apart_by_shape():
    assert sheets.looks_like_fittings(sheets.guess_mapping(template.FITTING_HEADINGS))
    assert sheets.looks_like_zones(sheets.guess_mapping(template.ZONE_HEADINGS))
    # And not each other's.
    assert not sheets.looks_like_zones(sheets.guess_mapping(template.FITTING_HEADINGS))
    assert not sheets.looks_like_fittings(sheets.guess_mapping(template.ZONE_HEADINGS))


# ------------------------------------------------------- the point of it all

def _joined():
    fit = [list(template.FITTING_HEADINGS)] + [list(r) for r in template.FITTING_EXAMPLE]
    zon = [list(template.ZONE_HEADINGS)] + [list(r) for r in template.ZONE_EXAMPLE]
    return sheets.combine(zon, 0, sheets.guess_mapping(zon[0]),
                          fit, 0, sheets.guess_mapping(fit[0]))


def test_a_fitting_is_written_once_and_used_by_many_circuits():
    """This is the whole reason for the split -- assert it, do not assume it."""
    rows, mapping, notes = _joined()
    files, _, counts = sheets.convert(rows, 0, mapping)
    # DL1 appears on three circuits in the example and once on the fittings sheet.
    assert sum(1 for r in template.ZONE_EXAMPLE if r[4] == "DL1") == 3
    assert sum(1 for r in template.FITTING_EXAMPLE if r[0] == "DL1") == 1
    assert counts["circuits"] == len(template.ZONE_EXAMPLE)
    assert counts["fixtures"] == len(template.FITTING_EXAMPLE)
    assert counts["unbound"] == 0
    assert notes == []
    # The details reached the catalogue from the fittings sheet, not the zones.
    assert "Recessed downlight" in files["FixturesCatalog.csv"]
    assert "Recessed downlight" not in files["LoadSchedule.csv"]


def test_the_trims_travel_from_the_fittings_sheet_to_the_catalogue():
    # Written once on DL1's fitting row, in force on every circuit that uses
    # it -- same promise as the wattage. James's catch, 08-12: without a trims
    # column every import wore the flagged 5%/90% default.
    rows, mapping, _ = _joined()
    files, _, _ = sheets.convert(rows, 0, mapping)
    import csv
    import io as _io
    cat = {r["FixtureRef"]: r for r in
           csv.DictReader(_io.StringIO(files["FixturesCatalog.csv"]))}
    assert cat["DL1"]["DimmingRange"] == "5-90"
    assert cat["P1"]["DimmingRange"] == ""


def test_the_worked_example_converts_with_nothing_to_report():
    rows, mapping, _ = _joined()
    _, report, _ = sheets.convert(rows, 0, mapping)
    assert report == [], f"the example should be clean, got: {report}"


def test_a_circuit_may_override_its_fittings_dimming_type():
    """The same fitting switched in one room and dimmed in another is legal."""
    fit = [list(template.FITTING_HEADINGS), ["DL1", "Downlight", "", "", "12", "", "DALI"]]
    zon = [list(template.ZONE_HEADINGS),
           ["GF", "Kitchen", "Downlights", "C1", "DL1", "6", "", ""],
           ["GF", "Store", "Downlights", "C1", "DL1", "2", "", ""]]
    zon[2].append("")
    zm = sheets.guess_mapping(zon[0])
    rows, mapping, _ = sheets.combine(zon, 0, zm, fit, 0, sheets.guess_mapping(fit[0]))
    # Both circuits inherit DALI from the fitting.
    assert all(r[mapping["load_type"]] == "DALI" for r in rows[1:])


def test_a_code_that_is_not_on_the_fittings_sheet_is_reported():
    fit = [list(template.FITTING_HEADINGS), ["DL1", "Downlight", "", "", "12", "", "DALI"]]
    zon = [list(template.ZONE_HEADINGS),
           ["GF", "Kitchen", "Downlights", "C1", "DL9", "6", "", ""]]
    rows, mapping, notes = sheets.combine(
        zon, 0, sheets.guess_mapping(zon[0]), fit, 0, sheets.guess_mapping(fit[0]))
    assert any("DL9" in n for n in notes)
    # It still builds -- blank and flagged beats a blocked import.
    _, _, counts = sheets.convert(rows, 0, mapping)
    assert counts["circuits"] == 1


def test_a_fitting_no_circuit_uses_is_reported():
    fit = [list(template.FITTING_HEADINGS),
           ["DL1", "Downlight", "", "", "12", "", "DALI"],
           ["SPARE", "Never used", "", "", "9", "", "DALI"]]
    zon = [list(template.ZONE_HEADINGS),
           ["GF", "Kitchen", "Downlights", "C1", "DL1", "6", "", ""]]
    _, _, notes = sheets.combine(
        zon, 0, sheets.guess_mapping(zon[0]), fit, 0, sheets.guess_mapping(fit[0]))
    assert any("SPARE" in n for n in notes)


# --------------------------------------------------------- the dropdowns

def test_every_dropdown_value_is_a_word_the_importer_understands():
    """A dropdown offering a word that is then ignored is the worst version.

    It looks authoritative, the user types nothing wrong, and the value lands
    blank and marked assumed.
    """
    for choice in template.DIMMING_CHOICES:
        assert sheets.load_type_from_text(choice) is not None, choice


def test_the_dropdowns_are_actually_in_the_file(book):
    with zipfile.ZipFile(io.BytesIO(book)) as zf:
        fittings = zf.read("xl/worksheets/sheet1.xml").decode()
        zones = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "dataValidation" in fittings and "DALI" in fittings
    # The zone's fitting code is chosen from the codes on the fittings sheet,
    # so a circuit cannot name a fitting nobody described.
    assert "Fittings!$A$2" in zones
    # Blank must stay allowed everywhere -- it means "not stated" and is flagged.
    assert 'allowBlank="1"' in fittings and 'allowBlank="1"' in zones


# ------------------------------------------------------------ the guidance

def test_the_guidance_explains_every_column_of_both_sheets(book):
    rows = sheets.read_tables(template.FILENAME, book)[2][1]
    text = "\n".join(" ".join(r) for r in rows)
    for heading in template.FITTING_HEADINGS + template.ZONE_HEADINGS:
        assert heading in text, heading


def test_the_guidance_says_the_floor_is_needed(book):
    """It used to say only room and circuit were needed. James, 2026-08-12."""
    rows = sheets.read_tables(template.FILENAME, book)[2][1]
    text = " ".join(" ".join(r) for r in rows)
    assert "Floor" in text and "storey" in text


def test_the_guidance_lists_the_real_dimming_vocabulary(book):
    """It listed four words while the example used a fifth. James, 2026-08-12."""
    rows = sheets.read_tables(template.FILENAME, book)[2][1]
    text = " ".join(" ".join(r) for r in rows)
    for phrase in ("trailing edge", "leading edge", "0-10", "switched", "dali"):
        assert phrase in text.lower(), phrase
    # And it warns about the words that mean nothing on their own.
    assert "'LED' and 'phase' on their own mean nothing here" in text


def test_the_data_sheets_ship_empty_so_nothing_is_invented(book):
    """An example row left in by mistake is a circuit for a room that is not there."""
    for index in (0, 1):
        rows = sheets.read_tables(template.FILENAME, book)[index][1]
        assert len([r for r in rows if any(c.strip() for c in r)]) == 1


def test_a_blank_template_is_not_mistaken_for_a_filled_one(book):
    tables = sheets.read_tables(template.FILENAME, book)
    assert sheets.detect_pair(tables) is None


# ----------------------------------------------------------------- mechanics

def test_column_letters():
    assert template._column(0) == "A"
    assert template._column(25) == "Z"
    assert template._column(26) == "AA"


def test_a_heading_with_an_ampersand_survives_the_xml():
    assert "Ceiling &amp; Wall" in template._sheet([["Ceiling & Wall"]])


# ---------------------------------------------- the vocabulary has one owner

def test_every_canonical_heading_maps_back_to_its_own_field():
    """The defect this pins, found in a browser on 2026-08-12.

    The joined sheet was headed with each field's `label` -- the words shown to
    a person -- rather than its canonical heading. Two labels are actively
    WRONG when read back: "Model / product code" and "Dimming / control type"
    both resolve to the FITTING CODE, because of the words "code" and "type".
    So the joined sheet had its fitting-code column stolen and arrived with
    every column reading "not used".
    """
    for key, heading in sheets.CANONICAL_HEADINGS.items():
        assert list(sheets.guess_mapping([heading])) == [key], f"{heading} -> {key}"


def test_the_template_is_built_from_that_one_source():
    """Typed a second time is how a template drifts from its reader."""
    assert template.FITTING_HEADINGS == [
        sheets.CANONICAL_HEADINGS[k] for k in template.FITTING_FIELD_ORDER]
    assert template.ZONE_HEADINGS == [
        sheets.CANONICAL_HEADINGS[k] for k in template.ZONE_FIELD_ORDER]


def test_the_joined_sheet_comes_back_fully_mapped():
    """End to end: join, re-guess as the app does, and expect nothing unmapped."""
    fit = [list(template.FITTING_HEADINGS)] + [list(r) for r in template.FITTING_EXAMPLE]
    zon = [list(template.ZONE_HEADINGS)] + [list(r) for r in template.ZONE_EXAMPLE]
    rows, _, _ = sheets.combine(zon, 0, sheets.guess_mapping(zon[0]),
                                fit, 0, sheets.guess_mapping(fit[0]))
    remapped = sheets.guess_mapping(rows[0])       # what the app actually does
    assert set(remapped) == set(sheets.FIELD_KEYS)
    assert sheets.missing_required(remapped) == []
    assert sheets.mapping_problems(remapped) == []
