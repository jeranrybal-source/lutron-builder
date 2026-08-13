"""Editing the fitting list in FixturesCatalog.csv.

Same shape as `scenes.py`: pure functions over the raw CSV rows, no file I/O
and no schedule objects, so a column this module has never heard of survives an
edit untouched.

Why this exists at all: the values most likely to be *assumed* rather than read
all live on this one file -- the dimming type, the wattage, the trims. An
engineer who spots a wrong one on the review sheet had no way to correct it
short of opening the CSV in Excel, and the app tells him to do exactly that,
which is how a one-cell fix becomes a spreadsheet session. The dimming type is
offered as a list because it is a Lutron code, not a word: typing "DALI" into
LoadTypeID produces a schedule that will not load.

The fitting REF is deliberately not editable here. It is the join key to every
circuit in LoadSchedule.csv, so renaming it is a rename across two files, not
an edit of one.
"""

from __future__ import annotations

from .schedule import (
    control_family,
    default_load_type,
    load_type_name,
    load_type_names,
)

# The types a domestic lighting job actually uses, offered first. Everything
# else in the reference file is still offered below them -- the point of the
# list is that an invalid code cannot be typed, not that the choice is ours.
COMMON_LOAD_TYPES = (16, 119, 117, 112, 118, 1)

# Written into DataSource when someone types a wattage here. A figure entered
# by hand is neither read off the drawings nor researched, and the review sheet
# says which of the three every wattage is.
BY_HAND = "entered by hand"

EDITABLE = {
    "description": "Description",
    "wattage": "FixtureWattage_W",
    "watts_per_metre": "Wattage_W_per_m",
    "load_type_id": "LoadTypeID",
    "low_end": "LowEnd_pct",
    "high_end": "HighEnd_pct",
    "notes": "Notes",
}


def load_type_choices() -> list[dict]:
    """Every load type Designer knows, common ones first."""
    names = load_type_names()
    common = [{"id": i, "name": names.get(i, str(i)), "common": True}
              for i in COMMON_LOAD_TYPES if i in names]
    rest = [{"id": i, "name": names[i], "common": False}
            for i in sorted(names) if i not in COMMON_LOAD_TYPES]
    return common + rest


def summarise(rows: list[dict], load_rows: list[dict]) -> list[dict]:
    """One entry per fitting, with what it is used on and what was assumed.

    `assumed` repeats the review sheet's judgement rather than inventing a
    second one: a cell that is BLANK in the file is a cell nobody read, however
    confidently the loaded schedule then defaults it.
    """
    used: dict[str, int] = {}
    for r in load_rows:
        ref = (r.get("FixtureRef") or "").strip()
        if ref:
            used[ref] = used.get(ref, 0) + 1

    out = []
    for r in rows:
        ref = (r.get("FixtureRef") or "").strip()
        blank = [k for k in ("LoadTypeID", "LowEnd_pct", "HighEnd_pct")
                 if not (r.get(k) or "").strip()]
        # The same fallback the schedule applies, so the editor never shows a
        # different assumption from the review sheet: DALI for an architectural
        # fitting, mains reverse phase for anything that takes a lamp.
        fallback = default_load_type(r.get("Description", ""))
        try:
            type_id = int(float((r.get("LoadTypeID") or "").strip() or fallback))
        except ValueError:
            type_id = fallback
        out.append({
            "ref": ref,
            "description": (r.get("Description") or "").strip(),
            "wattage": (r.get("FixtureWattage_W") or "").strip(),
            "watts_per_metre": (r.get("Wattage_W_per_m") or "").strip(),
            "load_type_id": type_id,
            "load_type_name": load_type_name(type_id),
            "family": control_family(type_id),
            "low_end": (r.get("LowEnd_pct") or "").strip(),
            "high_end": (r.get("HighEnd_pct") or "").strip(),
            "data_source": (r.get("DataSource") or "").strip(),
            "notes": (r.get("Notes") or "").strip(),
            "circuits": used.get(ref, 0),
            "assumed": blank,
        })
    return out


def _clean_number(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        float(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number, not '{value}'.") from exc
    return value


def apply_edits(rows: list[dict], edits: list[dict]) -> tuple[list[dict], list[str]]:
    """Write the engineer's changes back onto the raw rows.

    Rows are matched on FixtureRef, and a ref the file does not carry is
    refused rather than appended: silently creating a fitting nothing uses is
    not what anybody meant by editing one.
    """
    by_ref = {(r.get("FixtureRef") or "").strip(): r for r in rows}
    names = load_type_names()
    notes: list[str] = []

    for edit in edits:
        ref = str(edit.get("ref") or "").strip()
        row = by_ref.get(ref)
        if row is None:
            raise ValueError(f"'{ref}' is not a fitting in this project.")

        if "load_type_id" in edit:
            raw = str(edit["load_type_id"]).strip()
            if raw:
                try:
                    type_id = int(float(raw))
                except ValueError as exc:
                    raise ValueError(f"'{raw}' is not a load type.") from exc
                if type_id not in names:
                    raise ValueError(
                        f"Load type {type_id} is not in LoadTypes_REFERENCE.csv. "
                        f"A code Designer does not know builds a fitting nothing "
                        f"can drive.")
                if (row.get("LoadTypeID") or "").strip() != str(type_id):
                    notes.append(f"{ref}: dimming type set to "
                                 f"{names[type_id]}.")
                    # Designer wants this flag set for reverse-phase dimming,
                    # and leaving yesterday's value behind makes the pair
                    # disagree with itself.
                    row["PhaseControl"] = "1" if type_id == 119 else "0"
                row["LoadTypeID"] = str(type_id)
                if "LoadTypeName" in row:
                    row["LoadTypeName"] = names[type_id]

        for key in ("wattage", "watts_per_metre", "low_end", "high_end"):
            if key not in edit:
                continue
            column = EDITABLE[key]
            value = _clean_number(str(edit[key]), column)
            if key == "wattage" and value != (row.get("FixtureWattage_W") or "").strip():
                # Provenance: the sheet must not go on showing a hand-typed
                # figure as one read off the drawings or found on a datasheet.
                row["DataSource"] = BY_HAND if value else ""
                notes.append(f"{ref}: wattage set to {value or 'blank'} by hand.")
            row[column] = value

        for key in ("description", "notes"):
            if key in edit:
                row[EDITABLE[key]] = str(edit[key]).strip()

    return rows, notes
