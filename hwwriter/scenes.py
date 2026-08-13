"""Editing the scenes in Scenes.csv, and copying a room's scenes to others.

Everything here is a pure function over the raw CSV rows: no file I/O, no
schedule objects. The row dicts keep every column they arrived with, so a
column this module has never heard of survives an edit untouched.

The thing to understand before changing any of it: a scene is not a name, it
is one row per (area, scene, ZONE). "Relax" in the Kitchen is a brightness for
each of the Kitchen's circuits. So copying Relax to the Study cannot copy the
levels across -- the Study has different circuits. What copies is the scene
LIST (names and numbers) plus one representative level per scene, applied to
every circuit in the target room (James, 2026-08-05). Consistent naming and
numbering across the house, sensible starting levels, fine-tune afterwards.
"""

from __future__ import annotations

from collections import Counter

SET_LEVEL = "SetLevel"
DEFAULT_FADE = "2"
DEFAULT_DELAY = "0"


def is_level(row: dict) -> bool:
    """Does this row set a brightness?

    NOT "is it SetLevel". The documented vocabulary says SetLevel, but the AI
    writes ZoneLevel and the writer accepts both -- it treats everything except
    Unaffected as a level. Testing for SetLevel therefore matched NOTHING on a
    real project, so every scene summarised as blank and a copy would have
    carried empty levels into every room in the house.
    """
    if (row.get("CommandType") or "").strip().lower() == "unaffected":
        return False
    return (row.get("Level_pct") or "").strip().lower() != "unaffected"


def prevailing_command(rows: list[dict]) -> str:
    """The CommandType this project actually uses, for rows we create."""
    used = [(r.get("CommandType") or "").strip() for r in rows if is_level(r)]
    used = [c for c in used if c]
    return Counter(used).most_common(1)[0][0] if used else SET_LEVEL


def _key(row: dict) -> tuple:
    return ((row.get("SceneNumber") or "").strip(), (row.get("SceneName") or "").strip())


def zones_by_area(load_rows: list[dict]) -> dict[str, list[str]]:
    """Circuit names per area, in LoadSchedule order, de-duplicated."""
    out: dict[str, list[str]] = {}
    for row in load_rows:
        area = (row.get("AreaName") or "").strip()
        zone = (row.get("ZoneName") or "").strip()
        if not area or not zone:
            continue
        seen = out.setdefault(area, [])
        if zone not in seen:
            seen.append(zone)
    return out


def representative_level(rows: list[dict]) -> str:
    """The level a scene 'mostly' sets.

    Most rooms set most circuits to the same value in a given scene, so the
    commonest level is a fair summary and a fair thing to carry to another
    room. Rows the scene deliberately leaves alone (Unaffected) are not levels
    and must not vote, or a scene that skips two circuits reads as blank.
    """
    levels = [(r.get("Level_pct") or "").strip() for r in rows if is_level(r)]
    levels = [x for x in levels if x != ""]
    if not levels:
        return ""
    return Counter(levels).most_common(1)[0][0]


def summarise(scene_rows: list[dict]) -> list[dict]:
    """Every area's scene list, for display and for the copy source picker."""
    by_area: dict[str, dict[tuple, list[dict]]] = {}
    for row in scene_rows:
        area = (row.get("AreaName") or "").strip()
        if not area:
            continue
        by_area.setdefault(area, {}).setdefault(_key(row), []).append(row)
    out = []
    for area, scenes in by_area.items():
        out.append({
            "area": area,
            "scenes": [{
                "number": num,
                "name": name,
                "level": representative_level(rows),
                "zones": len(rows),
                # A scene with more than one level in it is hand-tuned. Say so,
                # because setting a level here flattens exactly that work.
                "mixed": len({(r.get("Level_pct") or "").strip()
                              for r in rows if is_level(r)}) > 1,
            } for (num, name), rows in scenes.items()],
        })
    out.sort(key=lambda a: a["area"])
    for area in out:
        area["scenes"].sort(key=lambda s: (_as_int(s["number"]), s["name"]))
    return out


def _as_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 999


def _template(rows: list[dict]) -> dict:
    """A row of the source scene to inherit fade/delay and any extra columns."""
    for row in rows:
        if is_level(row):
            return row
    return rows[0]


def copy_scenes(scene_rows: list[dict], source: str, targets: list[str],
                zones: dict[str, list[str]]) -> tuple[list[dict], list[str]]:
    """Give each target area exactly the source area's scene list.

    Returns (new rows, notes). A target with no circuits in LoadSchedule is
    skipped and said so -- writing scenes against zone names that do not exist
    is how a project builds and then fails to open.
    """
    source = source.strip()
    src_scenes: dict[tuple, list[dict]] = {}
    for row in scene_rows:
        if (row.get("AreaName") or "").strip() == source:
            src_scenes.setdefault(_key(row), []).append(row)
    if not src_scenes:
        raise ValueError(f"'{source}' has no scenes to copy.")

    notes: list[str] = []
    # The same room ticked twice wrote every row twice, which builds as
    # duplicate circuit assignments.
    wanted: list[str] = []
    for raw in targets:
        t = raw.strip()
        if t and t != source and t not in wanted:
            wanted.append(t)
    usable = []
    for target in wanted:
        if zones.get(target):
            usable.append(target)
        else:
            notes.append(f"{target}: skipped -- it has no circuits in LoadSchedule.")
    if not usable:
        raise ValueError("None of the chosen rooms have circuits to put scenes on.")

    # EXISTING rows describe what the shell ALREADY has -- they are not ours to
    # replace, and dropping them changes what the built file means.
    kept = [r for r in scene_rows
            if (r.get("AreaName") or "").strip() not in usable
            or (r.get("Action") or "NEW").strip().upper() == "EXISTING"]
    made: list[dict] = []
    for target in usable:
        for (number, name), rows in src_scenes.items():
            level = representative_level(rows)
            tmpl = _template(rows)
            for zone in zones[target]:
                new = dict(tmpl)
                new["Action"] = "NEW"
                new["AreaName"] = target
                new["SceneName"] = name
                new["SceneNumber"] = number
                new["ZoneName"] = zone
                # Inherit the source row's CommandType -- do NOT force SetLevel,
                # or a ZoneLevel project gets rows in a second dialect.
                new["Level_pct"] = level
                new.setdefault("Fade_seconds", DEFAULT_FADE)
                new.setdefault("Delay_seconds", DEFAULT_DELAY)
                made.append(new)
        notes.append(f"{target}: {len(src_scenes)} scenes across "
                     f"{len(zones[target])} circuits.")
    out = kept + made
    # A source room carrying two scenes with the same name or number would
    # propagate that fault into every room it is copied to.
    check_unique(out, source)
    for target in usable:
        check_unique(out, target)
    return out, notes


def apply_edits(scene_rows: list[dict], area: str, edits: list[dict],
                zones: dict[str, list[str]]) -> tuple[list[dict], list[str]]:
    """Rename, renumber, re-level, delete and add scenes in one area.

    Each edit carries the scene's ORIGINAL number and name so the rows can be
    found, plus what it should become. A level is only written when it actually
    changed: rewriting it unconditionally would silently flatten a hand-tuned
    scene every time someone renamed it.
    """
    area = area.strip()
    mine = [r for r in scene_rows if (r.get("AreaName") or "").strip() == area]
    others = [r for r in scene_rows if (r.get("AreaName") or "").strip() != area]
    if not mine and not edits:
        raise ValueError(f"'{area}' has no scenes.")

    # EXISTING rows describe scenes the Starter Shell already has. The builder
    # ignores them, so editing one writes a change to the CSV that can never
    # reach the built file -- the editor would report success and do nothing.
    shell = [r for r in mine if (r.get("Action") or "NEW").strip().upper() == "EXISTING"]
    shell_keys = {_key(r) for r in shell}
    grouped: dict[tuple, list[dict]] = {}
    for row in mine:
        if (row.get("Action") or "NEW").strip().upper() == "EXISTING":
            continue
        grouped.setdefault(_key(row), []).append(row)

    notes: list[str] = []
    touched: set[tuple] = set()
    made: list[dict] = []

    for edit in edits:
        was = ((edit.get("was_number") or "").strip(), (edit.get("was_name") or "").strip())
        if was in shell_keys:
            raise ValueError(
                f"'{was[1]}' is already in the shell, so changing it here would "
                f"have no effect on the file that gets built. Leave it as it is.")
        rows = grouped.get(was)
        if edit.get("delete"):
            if rows:
                touched.add(was)
                notes.append(f"Removed '{was[1]}'.")
            continue
        name = (edit.get("name") or "").strip()
        number = str(edit.get("number") or "").strip()
        level = str(edit.get("level") or "").strip()
        if not name:
            raise ValueError("A scene needs a name.")
        if not _valid_number(number):
            raise ValueError(f"Scene number '{number}' for '{name}' must be a whole "
                             f"number from 0 to 30.")
        if level and not _valid_level(level):
            raise ValueError(f"Level '{level}' for '{name}' must be 0-100.")

        if rows is None:                      # a scene the engineer added
            if not zones.get(area):
                raise ValueError(f"'{area}' has no circuits to put '{name}' on.")
            command = prevailing_command(scene_rows)
            for zone in zones[area]:
                made.append({"Action": "NEW", "AreaName": area, "SceneName": name,
                             "SceneNumber": number, "ZoneName": zone,
                             "CommandType": command, "Level_pct": level or "0",
                             "Fade_seconds": DEFAULT_FADE, "Delay_seconds": DEFAULT_DELAY})
            notes.append(f"Added '{name}' on {len(zones[area])} circuits.")
            continue

        touched.add(was)
        before = representative_level(rows)
        for row in rows:
            new = dict(row)
            new["SceneName"] = name
            new["SceneNumber"] = number
            # A circuit the scene deliberately leaves alone stays left alone.
            if level and level != before and is_level(new):
                new["Level_pct"] = level
            made.append(new)
        if level and level != before:
            notes.append(f"'{name}' set to {level}% on every circuit.")

    # Scenes the engineer did not mention are left exactly as they were.
    untouched = [r for k, rows in grouped.items() if k not in touched for r in rows]
    out = others + shell + untouched + made
    check_unique(out, area)
    return out, notes


def edit_moves(edits: list[dict]) -> tuple[dict[str, str], set[str]]:
    """What an edit does to scene NAMES: {old: new} and the ones removed."""
    renamed: dict[str, str] = {}
    removed: set[str] = set()
    for edit in edits:
        was = (edit.get("was_name") or "").strip()
        if not was:
            continue                                  # a scene being added
        if edit.get("delete"):
            removed.add(was)
            continue
        now = (edit.get("name") or "").strip()
        if now and now != was:
            renamed[was] = now
    return renamed, removed


def names_in(rows: list[dict], area: str) -> set[str]:
    return {(r.get("SceneName") or "").strip() for r in rows
            if (r.get("AreaName") or "").strip() == area}


def retarget_buttons(button_rows: list[dict], area: str, renamed: dict[str, str],
                     removed: set[str]) -> tuple[list[dict], list[str]]:
    """Follow scene renames into Buttons.csv, and drop buttons left dangling.

    A keypad button recalls a scene by NAME, and the button can be on a keypad
    in a completely different room. Renaming a scene here and not there leaves
    the button pointing at a scene that no longer exists: the save succeeds,
    the review sheet renders, and the BUILD then refuses the whole project.
    Renaming one scene in a real House B job orphaned two buttons, one of
    them on a keypad in another room.
    """
    out: list[dict] = []
    notes: list[str] = []
    followed = 0
    dropped: list[str] = []
    for row in button_rows:
        if (row.get("TargetArea") or "").strip() != area:
            out.append(row)
            continue
        target = (row.get("TargetScene") or "").strip()
        if target in renamed:
            row = dict(row)
            row["TargetScene"] = renamed[target]
            followed += 1
        elif target and target in removed:
            dropped.append(f"{(row.get('KeypadName') or '').strip()} "
                           f"button {(row.get('ButtonNumber') or '').strip()}")
            continue                       # the scene is gone; so is the action
        out.append(row)
    if followed:
        notes.append(f"Followed the rename onto {followed} keypad button(s).")
    if dropped:
        notes.append(f"Removed {len(dropped)} button action(s) that recalled a "
                     f"deleted scene: {', '.join(dropped[:4])}"
                     + (" and others." if len(dropped) > 4 else "."))
    return out, notes


def check_unique(rows: list[dict], area: str) -> None:
    """No two scenes in a room may share a name, or share a number.

    Neither is caught downstream. A keypad button targets a scene by NAME, and
    the builder maps (area, name) to a number -- so a second scene with the
    same name silently retargets an existing button to the wrong scene. Two
    scenes sharing a NUMBER merge into one when the schedule is loaded. Both
    save cleanly, both build, and both are wrong in a real house.
    """
    names: dict[str, str] = {}
    numbers: dict[str, str] = {}
    for row in rows:
        if (row.get("AreaName") or "").strip() != area:
            continue
        num, name = _key(row)
        # Compare the way the BUILDER will, not the way the strings look.
        # "Relax" and "relax" collide in the scene-name map; "1" and "01" are
        # both scene 1 once parsed. Raw string comparison waves both through.
        name, num = name.casefold(), str(_as_int(num))
        if names.setdefault(name, num) != num:
            raise ValueError(
                f"'{area}' has two scenes called '{name}' (numbers "
                f"{names[name]} and {num}). A keypad button recalls a scene by "
                f"name, so it would be ambiguous which one it means.")
        if numbers.setdefault(num, name) != name:
            raise ValueError(
                f"'{area}' has two scenes numbered {num} ('{numbers[num]}' and "
                f"'{name}'). They would merge into one scene when the file is built.")


def _valid_number(value: str) -> bool:
    try:
        return 0 <= int(value) <= 30
    except (TypeError, ValueError):
        return False


def _valid_level(value: str) -> bool:
    try:
        return 0 <= int(value) <= 100
    except (TypeError, ValueError):
        return False
