"""Reading a designer's own load schedule.

The importer's whole job is to be wrong LOUDLY rather than plausibly, because
a mis-mapped column produces a complete, valid, buildable schedule for a house
nobody designed. So most of what is tested here is refusal: what it declines to
read, what it leaves blank, and what it says about it.
"""
import csv
import io
import zipfile

import pytest

from hwwriter import sheets

# A schedule with everything real ones have wrong with them: a title block
# above the headings, the room merged down a group, a totals line, a blank
# spacer, a room name used on two floors, a dimming word nobody has heard of,
# and two circuits in one room sharing a name.
MESSY = '''"BRIGHT SPARK LIGHTING DESIGN",,,,,,,,,
"Project: 14 Orchard Lane","Rev C",,,,,,,,
,,,,,,,,,
"Level","Room","Ckt No.","Circuit Description","Type","Luminaire","Qty","Load (W)","Dimming","Comments"
"Ground Floor","Kitchen","1","Ceiling Downlights","DL1","Recessed downlight","14","6.5","DALI","over island"
,,"2","Island Pendants","P1","Decorative pendant","3","8","Mains trailing edge",
"Ground Floor","WC","3","Ceiling Downlights","DL2","Small downlight","2","4","DALI",
,,,,,,,,,
"TOTAL",,,,,,"19",,,
"First Floor","WC","4","Ceiling Downlights","DL2","Small downlight","2","4","DALI",
"First Floor","Bedroom 1","5","Ceiling Downlights","DL1","Recessed downlight","8","6.5","Sparkle-dim",
"First Floor","Bedroom 1","6","Ceiling Downlights","DL1","Recessed downlight","4","6.5","DALI",
'''


def _load(text=MESSY, name="schedule.csv"):
    rows = sheets.read_tables(name, text.encode())[0][1]
    header = sheets.find_header(rows)
    return rows, header, sheets.guess_mapping(rows[header])


def _convert(**kw):
    rows, header, mapping = _load()
    return sheets.convert(rows, header, mapping, **kw)


def _rows_of(files, name):
    return list(csv.DictReader(io.StringIO(files[name])))


# ----------------------------------------------------------------- reading

def test_the_header_row_is_found_below_a_title_block():
    rows, header, _ = _load()
    # Row 1 is the practice name. Taking it would map every column wrongly and
    # still produce a schedule, which is the failure this guards.
    assert header == 3
    assert rows[header][0] == "Level"


def test_a_semicolon_export_reads_as_columns_not_as_one_column():
    text = "Room;Circuit;Type;Qty\nKitchen;Downlights;DL1;6\n"
    rows = sheets.read_tables("euro.csv", text.encode())[0][1]
    assert rows[0] == ["Room", "Circuit", "Type", "Qty"]


def test_a_cp1252_pound_sign_does_not_lose_the_file():
    text = 'Room,Circuit,Type,Notes\nKitchen,Downlights,DL1,"\xa3120 each"\n'
    rows = sheets.read_tables("x.csv", text.encode("cp1252"))[0][1]
    assert rows[1][3] == "£120 each"


def test_the_old_binary_xls_is_named_not_mangled():
    with pytest.raises(sheets.SheetError) as exc:
        sheets.read_tables("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    assert ".xlsx" in str(exc.value)


def _xlsx(rows: list[list[str]], sheet_names=("Load Schedule",)) -> bytes:
    """A real .xlsx, built the way Excel builds one: shared strings, and no
    <c> element at all for an empty cell."""
    strings: list[str] = []

    def si(v):
        if v not in strings:
            strings.append(v)
        return strings.index(v)

    def ref(i):
        s, i = "", i + 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, v in enumerate(row):
            if v == "":
                continue
            try:
                cells.append(f'<c r="{ref(c)}{r}"><v>{float(v)!r}</v></c>')
            except ValueError:
                cells.append(f'<c r="{ref(c)}{r}" t="s"><v>{si(v)}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ss = (f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    tabs = "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>'
                   for i, n in enumerate(sheet_names, 1))
    wb = (f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/'
          f'officeDocument/2006/relationships"><sheets>{tabs}</sheets></workbook>')
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships">' + "".join(
                f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>'
                for i in range(1, len(sheet_names) + 1)) + "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", ss)
        for i in range(1, len(sheet_names) + 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       f'<worksheet {ns}><sheetData>'
                       f'{"".join(body) if i == len(sheet_names) else ""}'
                       f"</sheetData></worksheet>")
    return buf.getvalue()


def test_an_xlsx_reads_the_same_as_the_same_sheet_as_csv():
    grid = [r for r in csv.reader(io.StringIO(MESSY))]
    name, rows = sheets.read_tables("s.xlsx", _xlsx(grid))[0]
    header = sheets.find_header(rows)
    a, _, counts_x = sheets.convert(rows, header, sheets.guess_mapping(rows[header]))
    b, _, counts_c = _convert()
    assert counts_x == counts_c
    assert a["LoadSchedule.csv"] == b["LoadSchedule.csv"]


def test_an_empty_cell_does_not_shift_the_columns_left():
    # Excel writes no <c> for an empty cell. Reading cells in order rather than
    # by their address puts every later value in the wrong column -- the count
    # under the wattage, silently.
    name, rows = sheets.read_tables("s.xlsx", _xlsx([
        ["Room", "Circuit", "Type", "Qty", "W"],
        ["Kitchen", "Downlights", "", "6", "6.5"]]))[0]
    assert rows[1] == ["Kitchen", "Downlights", "", "6", "6.5"]


def test_every_worksheet_is_offered_in_tab_order():
    data = _xlsx([["Room", "Circuit"], ["Kitchen", "Downlights"]],
                 sheet_names=("Cover", "Revisions", "Load Schedule"))
    assert [n for n, _ in sheets.read_tables("s.xlsx", data)] == [
        "Cover", "Revisions", "Load Schedule"]


# ----------------------------------------------------------------- mapping

def test_the_obvious_columns_are_guessed():
    rows, header, mapping = _load()
    heads = rows[header]
    assert heads[mapping["room"]] == "Room"
    assert heads[mapping["floor"]] == "Level"
    assert heads[mapping["circuit"]] == "Circuit Description"
    assert heads[mapping["quantity"]] == "Qty"
    assert heads[mapping["wattage"]] == "Load (W)"
    assert heads[mapping["fixture_ref"]] == "Type"
    assert sheets.mapping_problems(mapping) == []


def test_a_load_description_column_is_not_taken_for_the_wattage():
    # "Load Description" and "Load (W)" both start with "load". Claiming the
    # first as the wattage puts words where a number belongs and leaves the
    # real wattage unread, on a very ordinary sheet.
    rows = sheets.read_tables("x.csv", b"Room,Load Description,Load (W),Qty\n"
                                       b"Kitchen,Downlights,6.5,6\n")[0][1]
    mapping = sheets.guess_mapping(rows[0])
    assert rows[0][mapping["wattage"]] == "Load (W)"
    assert rows[0][mapping["circuit"]] == "Load Description"


def test_a_bracketed_unit_is_what_tells_load_w_from_load():
    # 'Load' is an ordinary heading for the circuit description on a UK
    # schedule. Ignore the '(W)' and the circuit column lands on the watts:
    # a column of numbers read as circuit names, every wattage lost, and a
    # schedule that looks perfectly reasonable.
    heads = ["Room", "Description", "Load (W)", "Qty"]
    mapping = sheets.guess_mapping(heads)
    assert heads[mapping["wattage"]] == "Load (W)"
    assert mapping.get("circuit") is None      # and it says so, rather than guessing


def test_an_exactly_named_column_beats_a_loosely_matching_one_before_it():
    # 'Circuit Loading' comes first and starts with 'circuit'. Matching column
    # by column in one pass gives it the circuit field, and the column actually
    # called 'Circuit' then has nowhere to go.
    heads = ["Circuit Loading", "Circuit", "Room", "Type"]
    mapping = sheets.guess_mapping(heads)
    assert heads[mapping["circuit"]] == "Circuit"
    assert heads[mapping["wattage"]] == "Circuit Loading"


def test_the_headings_a_real_schedule_uses_are_recognised():
    heads = ["Room", "Zone Description", "Watts", "No. off", "Length (m)", "W/m"]
    mapping = sheets.guess_mapping(heads)
    assert [heads[mapping[k]] for k in
            ("room", "circuit", "wattage", "quantity", "run_length", "wattage_per_m")] == heads


def test_a_missing_room_column_is_refused_before_anything_is_written():
    mapping = {"circuit": 0, "fixture_ref": 1}
    assert any("Room" in p for p in sheets.mapping_problems(mapping))


def test_a_schedule_that_names_no_fitting_at_all_is_refused():
    mapping = {"room": 0, "circuit": 1}
    assert any("names no fitting" in p for p in sheets.mapping_problems(mapping))


def test_one_column_mapped_to_two_things_is_refused():
    mapping = {"room": 0, "circuit": 1, "fixture_ref": 1}
    assert any("more than one thing" in p for p in sheets.mapping_problems(mapping))


# -------------------------------------------------------------- converting

def test_the_room_is_carried_down_a_merged_group():
    files, _, _ = _convert()
    loads = _rows_of(files, "LoadSchedule.csv")
    # The pendants row leaves Room blank because the cell is merged upward.
    pendants = next(r for r in loads if r["ZoneName"] == "Island Pendants")
    assert pendants["AreaName"] == "Kitchen"


def test_the_room_is_not_carried_down_when_that_is_turned_off():
    files, _, _ = _convert(fill_down=False)
    assert not [r for r in _rows_of(files, "LoadSchedule.csv")
                if r["ZoneName"] == "Island Pendants"]


def test_a_totals_line_never_becomes_a_circuit():
    files, report, _ = _convert()
    zones = [r["ZoneName"] for r in _rows_of(files, "LoadSchedule.csv")]
    assert not any("TOTAL" in z.upper() for z in zones)
    assert any("total" in n for n in report)


def test_a_room_name_on_two_floors_is_written_as_floor_then_room():
    files, _, _ = _convert()
    areas = {r["AreaName"] for r in _rows_of(files, "LoadSchedule.csv")}
    assert "Ground Floor > WC" in areas and "First Floor > WC" in areas
    # A name used on ONE floor is left plain -- suffixing everything would
    # rename rooms the drawings never renamed.
    assert "Kitchen" in areas


def test_two_circuits_in_one_room_with_the_same_name_are_told_apart():
    files, report, _ = _convert()
    zones = [r["ZoneName"] for r in _rows_of(files, "LoadSchedule.csv")
             if r["AreaName"] == "Bedroom 1"]
    assert len(zones) == len(set(zones)), "duplicate area+zone cannot be built"
    assert any("suffix" in n for n in report)


def test_a_dimming_word_it_does_not_know_is_left_blank_and_reported():
    files, report, _ = _convert()
    # "Sparkle-dim" is on a DL1 row, but DL1's first row said DALI. The
    # catalogue keeps the first, and the unknown word is still reported.
    assert any("Sparkle-dim" in n for n in report)


def test_a_written_dimming_range_reaches_the_catalogue():
    """James, 08-12: the template had nowhere to put the trims, so every sheet
    import landed on the flagged 5%/90% default. The column exists now, in the
    one written form the schedule reader already parses."""
    rows = sheets.read_tables(
        "x.csv", b"Room,Circuit,Type,Fitting,Qty,Dimming range\n"
                 b"Kitchen,Downlights,DL1,Recessed downlight,6,5-90\n")[0][1]
    mapping = sheets.guess_mapping(rows[0])
    assert mapping["dim_range"] == 5
    files, report, _ = sheets.convert(rows, 0, mapping)
    assert _rows_of(files, "FixturesCatalog.csv")[0]["DimmingRange"] == "5-90"
    assert not any("range" in n for n in report), report


def test_a_dimming_range_written_as_prose_is_left_blank_and_reported():
    # "quite dim" cannot become numbers without inventing them. Blank keeps the
    # flagged default; the report says which cell to fix.
    rows = sheets.read_tables(
        "x.csv", b"Room,Circuit,Type,Fitting,Qty,Trims\n"
                 b"Kitchen,Downlights,DL1,Recessed downlight,6,quite dim\n")[0][1]
    mapping = sheets.guess_mapping(rows[0])
    assert mapping["dim_range"] == 5
    files, report, _ = sheets.convert(rows, 0, mapping)
    assert _rows_of(files, "FixturesCatalog.csv")[0]["DimmingRange"] == ""
    assert any("quite dim" in n for n in report)


def test_a_column_headed_range_alone_is_not_read_as_the_trims():
    # On a lighting schedule "Range" is as likely the PRODUCT range. A column
    # of product names read as trims would be a believable wrong schedule.
    assert "dim_range" not in sheets.guess_mapping(
        ["Room", "Circuit", "Type", "Range"])


def test_a_recognised_dimming_word_becomes_its_lutron_load_type():
    files, _, _ = _convert()
    cat = {r["FixtureRef"]: r for r in _rows_of(files, "FixturesCatalog.csv")}
    assert cat["DL1"]["LoadTypeID"] == "16"           # DALI
    assert cat["P1"]["LoadTypeID"] == "119"           # mains trailing edge


def test_an_unstated_dimming_type_is_left_blank_for_the_fitting_to_decide():
    # Blank is not a gap to be filled. The schedule defaults a blank by what
    # the fitting IS and marks it assumed on the review sheet; a number written
    # here would be indistinguishable from one the designer specified.
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Type,Fitting,Qty\n"
                                       b"Kitchen,Downlights,DL1,Recessed downlight,6\n")[0][1]
    files, _, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    assert _rows_of(files, "FixturesCatalog.csv")[0]["LoadTypeID"] == ""


def test_a_blank_count_stays_blank_rather_than_becoming_one():
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Type,Qty\n"
                                       b"Kitchen,Cove Lighting,T1,\n")[0][1]
    files, report, counts = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    assert _rows_of(files, "LoadSchedule.csv")[0]["NumberOfFixtures"] == ""
    assert counts["no_count"] == 1
    assert any("no fitting count" in n for n in report)


@pytest.mark.parametrize("cell,expected", [
    ("14", "14"), ("14 no.", "14"), ("~20", "20"), ("1,200", "1200"),
    ("9.6W/m", "9.6"), ("", ""),
    # "3 x 8W" means three of eight watts. Reading the first number gives 3
    # where the column asked for watts, so it is refused outright.
    ("3 x 8W", ""),
])
def test_numbers_are_read_out_of_the_units_designers_write(cell, expected):
    body = f'Room,Circuit,Type,Qty\nKitchen,Downlights,DL1,"{cell}"\n'.encode()
    rows = sheets.read_tables("x.csv", body)[0][1]
    files, _, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    assert _rows_of(files, "LoadSchedule.csv")[0]["NumberOfFixtures"] == expected


def test_a_circuit_total_wattage_is_divided_by_the_count_and_says_so():
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Type,Qty,Load (W)\n"
                                       b"Kitchen,Downlights,DL1,10,65\n")[0][1]
    files, _, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]),
                                 wattage_is_total=True)
    fixture = _rows_of(files, "FixturesCatalog.csv")[0]
    assert fixture["FixtureWattage_W"] == "6.5"
    assert "divided" in fixture["Notes"]


def test_a_circuit_total_with_no_count_divides_by_nothing_and_stays_blank():
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Type,Qty,Load (W)\n"
                                       b"Kitchen,Downlights,DL1,,65\n")[0][1]
    files, _, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]),
                                 wattage_is_total=True)
    assert _rows_of(files, "FixturesCatalog.csv")[0]["FixtureWattage_W"] == ""


def test_a_fitting_with_no_code_gets_one_from_its_description():
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Fitting,Qty\n"
                                       b"Kitchen,Downlights,Recessed downlight,6\n")[0][1]
    files, report, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    load = _rows_of(files, "LoadSchedule.csv")[0]
    assert load["FixtureRef"] == "Recessed downlight"
    assert any("no fitting code" in n for n in report)


def test_a_fitting_given_two_different_wattages_keeps_the_first_and_says_so():
    body = (b"Room,Circuit,Type,Qty,Load (W)\n"
            b"Kitchen,Downlights,DL1,6,6.5\n"
            b"Hall,Downlights,DL1,4,9\n")
    rows = sheets.read_tables("x.csv", body)[0][1]
    files, report, _ = sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    cat = _rows_of(files, "FixturesCatalog.csv")
    assert len(cat) == 1 and cat[0]["FixtureWattage_W"] == "6.5"
    assert any("different wattages" in n for n in report)


def test_keypads_and_scenes_are_written_empty_rather_than_left_out():
    # A missing file is indistinguishable from a failed write. An empty one
    # with its header says plainly that a load schedule carries no keypads.
    files, _, _ = _convert()
    for name in ("Keypads.csv", "Buttons.csv", "Scenes.csv"):
        assert files[name].strip() == ",".join(sheets.HEADERS[name])


def test_a_sheet_with_no_circuits_in_it_is_refused_with_a_reason():
    rows = sheets.read_tables("x.csv", b"Room,Circuit,Type\n,,\n")[0][1]
    with pytest.raises(sheets.SheetError) as exc:
        sheets.convert(rows, 0, sheets.guess_mapping(rows[0]))
    assert "header row" in str(exc.value)


def test_every_written_file_is_one_the_schedule_loader_expects():
    files, _, _ = _convert()
    from hwwriter import ingest
    assert sorted(files) == sorted(ingest.FILES)


def test_the_report_names_the_source_and_every_column_it_used():
    rows, header, mapping = _load()
    files, notes, counts = sheets.convert(rows, header, mapping)
    text = sheets.report_text("orchard.xlsx", "Load Schedule", header, rows[header],
                              mapping, counts, notes, True, False)
    assert "orchard.xlsx" in text and "Load Schedule" in text
    assert "row 4" in text                    # 1-based, as the user sees it
    assert "'Load (W)'" in text
    assert "NO KEYPADS AND NO SCENES" in text


def test_the_imported_schedule_loads_and_validates_clean(tmp_path):
    # The end of the whole point: what comes out is a schedule the rest of the
    # tool reads without complaint, not merely well-formed CSV.
    import os

    from hwwriter.schedule import load_schedule, read_reference, validate
    files, _, _ = _convert()
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    docs = sheets.os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(sheets.__file__))), "docs")
    module_types = read_reference(os.path.join(docs, "ModuleTypes_REFERENCE.csv"),
                                  "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(os.path.join(docs, "KeypadModels_REFERENCE.csv"),
                                   "KeypadModel", "LutronModelInfoID")
    sched = load_schedule(str(tmp_path), module_types, keypad_models)
    validate(sched, set(), set(), {}, {})     # raises ValidationError if not
    assert len(sched.loads) == 6
