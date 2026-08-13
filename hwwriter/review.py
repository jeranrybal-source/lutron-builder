"""
The review sheet -- the checkpoint between reading the plans and writing Lutron.

Extracting a lighting schedule from a designer's PDF is the step most likely to
be quietly wrong: a circuit read off the wrong room, a fixture count misread, a
keypad missed at the far end of a corridor. Those mistakes are cheap to catch
here and expensive to catch after a processor transfer.

So this renders the whole schedule as one self-contained HTML page -- room by
room, circuit by circuit, panel by panel, button by button -- for a human to
read before anything is written. It shows totals worth sanity-checking (circuits
per room, load in watts, spare outputs) and flags the things that are legal but
usually mean something was missed.

It never touches a database and never writes a .hw. Open it, read it, fix the
CSVs, run it again.
"""

from __future__ import annotations

import html
import os
from collections import defaultdict

from .schedule import (
    FAMILY_LABELS,
    PHASE_OUTPUT_W,
    UNREAD_WARNING_MARKERS,
    Schedule,
    circuit_watts,
    control_family,
    load_type_name,
)
from .schedule import half_up as _half_up

# A value nobody read. It must never be a number (D1): a 1 that means "we could
# not tell" reads exactly like a 1 somebody counted.
UNREAD = "—"


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


class _Safe(str):
    """Markup this module built itself, and which must not be escaped again.

    Trust used to be inferred from a leading "<", which meant any AI-written
    field starting with a tag -- a fixture Description of
    `<img src=x onerror=...>` -- was emitted as live HTML into a sheet the
    engineer opens. Trust is now carried by the type, so a string can only be
    trusted by the code that constructed it.
    """


def _w(value) -> str:
    """A wattage as an engineer writes it.

    Wattages became fractional in 1.1.9 (LED tape is specified per metre), and
    Python then rendered 9.6 x 3 as "28.799999999999997W" on the sheet the
    engineer checks against the drawings. Trailing zeros go too: 20.0 is 20.
    """
    n = round(float(value), 2)
    return f"{int(n):,}" if n == int(n) else f"{n:,}"


def _family(load, fixture) -> str:
    """How this circuit is driven -- the override wins, as it does in the build."""
    if load.load_type_override is not None:
        return control_family(load.load_type_override)
    return control_family(fixture.load_type_id) if fixture else ""


def _note(text: str) -> str:
    """The extraction's own words about this row.

    Every file carries a Notes column and every one of them used to be read and
    thrown away, so the sheet could show a blank cell but never why it was
    blank. On a real read, 109 of 109 circuits explained themselves and nobody
    ever saw it.
    """
    text = (text or "").strip()
    return _Safe(f'<span class="rownote">{_esc(text)}</span>') if text else ""


def _fades(scene) -> str:
    """The fade times in a scene, and what will really be written.

    Only 0s and 2s are calibrated against Designer's own output; anything else
    is written as the 2s encoding. That happens silently, and until now the
    sheet showed no fade at all, so a designer's 4s fade disappeared with
    nothing to see.
    """
    from .schedule import FADE_RAW_CONFIRMED
    asked = sorted({lv.fade for lv in scene.levels if lv.level is not None})
    if not asked:
        return ""
    shown = []
    for f in asked:
        shown.append(f"{f}s" if f in FADE_RAW_CONFIRMED else f"{f}s → 2s")
    return " / ".join(shown)


# Which heading a validation warning belongs under. Warnings are plain
# sentences shared with the command line, so they are sorted by a phrase from
# the message itself; the table is ordered, first match wins, and anything
# unmatched falls to "Other" rather than being dropped.
_TOPICS = (
    ("Circuits", ("output assignment", "ZoneNumber", "drawing's own reference",
                  "W on phase dimming", "OUTPUT ONLY", "per metre", "RunLength",
                  "circuits is wired")),
    ("Keypads and buttons", ("keypad", "button", "engraving", "comm link")),
    ("Scenes", ("scene", "Fade of", "SetLevel")),
    ("Fittings and wattages", ("fitting", "Fixture", "wattage", "PhaseControl",
                               "LoadType", "trims")),
    ("Panels", ("module", "outputs")),
    ("Rooms", ("floor '", "room")),
)
TOPIC_ORDER = ("Circuits", "Fittings and wattages", "Rooms", "Scenes",
               "Keypads and buttons", "Panels", "Other")


def _topic(message: str) -> str:
    for topic, phrases in _TOPICS:
        if any(phrase.lower() in message.lower() for phrase in phrases):
            return topic
    return "Other"


def _table(headers: list[str], rows: list[list], classes: str = "") -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c if isinstance(c, _Safe) else _esc(c)}</td>"
                         for c in r) + "</tr>"
        for r in rows)
    return f'<table class="{classes}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render(sched: Schedule, project_name: str, shell_name: str,
           module_outputs: dict[str, int] | None = None) -> str:
    module_outputs = module_outputs or {}

    fixtures = {f.ref: f for f in sched.fixtures}
    loads_by_area: dict[str, list] = defaultdict(list)
    for load in sched.loads:
        loads_by_area[load.area].append(load)

    wiring = {(o.area, o.zone): (o.module, o.output) for o in sched.outputs}
    keypads_by_area: dict[str, list] = defaultdict(list)
    for kp in sched.keypads:
        keypads_by_area[kp.area].append(kp)
    buttons_by_keypad: dict[str, list] = defaultdict(list)
    for b in sched.buttons:
        buttons_by_keypad[b.keypad].append(b)
    scenes_by_area: dict[str, list] = defaultdict(list)
    for sc in sched.scenes:
        scenes_by_area[sc.area].append(sc)

    # A circuit whose count was assumed, or whose fitting has no wattage, has no
    # honest load -- so it is left out of the total rather than counted as the
    # guess. The tile says how many are missing, or the figure looks complete.
    # Circuits that will not reach the built file, and therefore neither will
    # the scene levels and button actions aimed at them.
    unbuilt = {(ld.area, ld.zone) for ld in sched.loads if ld.unresolved}
    watts_by_load = {(ld.area, ld.zone): circuit_watts(ld, fixtures) for ld in sched.loads}
    total_watts = sum(v for v in watts_by_load.values() if v is not None)
    no_total = [ld for ld in sched.loads if watts_by_load[(ld.area, ld.zone)] is None]

    parts: list[str] = []

    # A load schedule cannot carry modules, keypads or scenes. For a project
    # read from one, everything about them leaves this sheet entirely --
    # zero-count tiles, an empty panel section and a column of red "not
    # wired" read as a half-done job, when Designer is simply where that
    # work is done by hand (James, 2026-08-12). The moment the project
    # actually has any -- added in the app, or a plans read -- it all comes
    # back, because then it is real work to check.
    loads_only = (sched.source in ("spreadsheet", "drawing")
                  and not sched.keypads and not sched.scenes
                  and not sched.modules and not sched.outputs)

    # ---- headline numbers ------------------------------------------------
    tiles = [
        ("Rooms", len(sched.areas)),
        ("Circuits", len(sched.loads)),
        ("Fixture types", len(sched.fixtures)),
        ("Connected load", f"{_w(total_watts)} W"
         + (f" + {len(no_total)} unknown" if no_total else "")),
    ]
    if not loads_only:
        tiles += [
            ("Modules", len(sched.modules)),
            ("Keypads", len(sched.keypads)),
            ("Scenes", len(sched.scenes)),
        ]
    parts.append('<div class="tiles">' + "".join(
        f'<div class="tile"><div class="n">{_esc(v)}</div><div class="l">{_esc(k)}</div></div>'
        for k, v in tiles) + "</div>")

    # ---- things worth a second look --------------------------------------
    # Grouped by KIND, and every per-room and per-keypad observation collapsed
    # to one line naming the rooms. On a 49-room job the flat list ran to
    # hundreds of near-identical sentences, which is the same as having no list
    # at all -- the four things that mattered were somewhere in the middle of it.
    flags: list[tuple[str, str]] = [
        (_topic(f), f) for f in sched.warnings
        if not any(m in f for m in UNREAD_WARNING_MARKERS)]

    def _collapse(topic: str, names: list[str], one: str, many: str) -> None:
        if not names:
            return
        flags.append((topic, (one if len(names) == 1 else many).format(
            n=len(names), names=", ".join(names[:10]),
            more="..." if len(names) > 10 else "")))

    # A room lit by a circuit that belongs to another room -- a landing off the
    # stair, a corridor off the hall -- is not a room with no light.
    lit_elsewhere = {extra for ld in sched.loads for extra in ld.additional_areas}
    dark, scene_less, keypad_less = [], [], []
    for area in sched.areas:
        if not loads_by_area.get(area.uid):
            if area.uid not in lit_elsewhere:
                dark.append(area.name)
        elif not scenes_by_area.get(area.uid):
            scene_less.append(area.name)
        if not keypads_by_area.get(area.uid) and loads_by_area.get(area.uid):
            keypad_less.append(area.name)
    _collapse("Rooms", dark,
              "{names} has no circuits at all.",
              "{n} rooms have no circuits at all: {names}{more}")
    # A load schedule CANNOT carry keypads or scenes -- the upload screen says
    # so going in. Asking room by room "did you forget a keypad?" about a
    # source that never had one reads as an accusation of forgetting (James,
    # 2026-08-12), and a warning that fires on correct input teaches people to
    # ignore warnings. Say it once, as a fact. The room-by-room questions
    # return the moment the project actually has any -- added in the app or
    # read from plans -- because from then on a bare room IS worth asking about.
    if loads_only:
        if scene_less or keypad_less:
            flags.append(("Rooms",
                          "Modules, keypads and scenes are added by hand in "
                          "Designer — a load schedule does not carry them, so "
                          "nothing has been forgotten. Homeplay's full "
                          "engineering system designs the keypads, scenes and "
                          "the rest of the control system; this free tool "
                          "deliberately stops at the loads. If the demand is "
                          "there, a CSV way in for keypads and scenes may "
                          "follow."))
    else:
        _collapse("Rooms", scene_less,
                  "{names} has circuits but no scenes.",
                  "{n} rooms have circuits but no scenes: {names}{more}")
        _collapse("Rooms", keypad_less,
                  "{names} has circuits but no keypad — is it controlled from elsewhere?",
                  "{n} rooms have circuits but no keypad — are they controlled from "
                  "elsewhere? {names}{more}")
    _collapse("Keypads and buttons",
              [kp.name for kp in sched.keypads if not buttons_by_keypad.get(kp.name)],
              "Keypad {names} has no buttons programmed.",
              "{n} keypads have no buttons programmed: {names}{more}")

    spare = []
    for mod in sched.modules:
        used = sum(1 for o in sched.outputs if o.module == mod.name)
        cap = mod.output_count or module_outputs.get(mod.name, 0)
        if cap and used < cap:
            spare.append(f"{mod.name} ({cap - used} of {cap})")
    _collapse("Panels", spare,
              "{names} outputs unused.",
              "{n} modules have unused outputs: {names}{more}")
    # Engraved faceplates are etched to order and cannot be returned, and a
    # proposed button function silently proposes its engraving too. Say so once,
    # loudly, rather than leaving it to a Notes column nobody opens.
    # Designer stores wattage as a whole number, so a fitting specified per
    # metre (9.6 W/m tape is ordinary) does NOT reach the built project as
    # written. Saying it here is the difference between a known rounding and
    # the sheet disagreeing with the file for reasons nobody can see.
    def _frac(v):
        return bool(v) and float(v) != int(float(v))

    # BOTH stored columns are int, so a whole FixtureWattage with a fractional
    # LampWattage is rounded in the file with nothing said.
    rounded = [f for f in sched.fixtures
               if _frac(f.wattage) or _frac(f.lamp_wattage)]
    if rounded:
        # Name the figure that is actually fractional: a fitting whose FIXTURE
        # wattage is whole but whose LAMP wattage is not used to read
        # "(20W → 20W)" -- listed, but saying nothing changed.
        def _which(f):
            if _frac(f.wattage):
                return f"{f.ref} ({_w(f.wattage)}W → {_half_up(f.wattage)}W)"
            return (f"{f.ref} (lamp {_w(f.lamp_wattage)}W → "
                    f"{_half_up(f.lamp_wattage)}W)")
        flags.append(("Fittings and wattages",
            "Lutron stores wattage as a whole number, so "
            + ", ".join(_which(f) for f in rounded)
            + " will be rounded in the built project. The figures on this sheet are "
              "the ones you specified; circuit loading is worked out from these."))

    # `wattage or watts_per_metre`: a researched per-metre figure is exactly as
    # much a researched figure as a per-fitting one, and looked identical to a
    # drawing-specified value while this said `f.wattage` alone.
    researched = [f for f in sched.fixtures
                  if (f.wattage or f.watts_per_metre)
                  and (f.data_source or "").strip().startswith("http")]
    if researched:
        flags.append(("Fittings and wattages",
            f"{len(researched)} fixture wattage(s) were RESEARCHED online, not read from "
            f"the drawings ({', '.join(f.ref for f in researched[:8])}"
            f"{'...' if len(researched) > 8 else ''}). Wattage sizes circuits and panels "
            f"— check each against the specification before ordering or loading a panel. "
            f"Every one links to its source in the fixture schedule below."))
    unstated = [f.ref for f in sched.fixtures if not (f.wattage or f.watts_per_metre)]
    if unstated:
        flags.append(("Fittings and wattages",
            f"{len(unstated)} fixture type(s) have NO wattage at all "
            f"({', '.join(unstated[:8])}{'...' if len(unstated) > 8 else ''}) — the drawings "
            f"did not state it and it could not be found. Connected-load totals and any "
            f"panel sizing below exclude them."))

    proposed = sum(1 for b in sched.buttons if b.label_proposed)
    if proposed:
        flags.append(("Keypads and buttons",
            f"{proposed} of {len(sched.buttons)} button engravings are PROPOSED, not read "
            f"from the drawings. Engraving text is etched to order and cannot be returned "
            f"— approve every label against the client's wishes before anything is ordered."))

    # ---- what nobody actually read ---------------------------------------
    # These go FIRST and in their own list. An engineer should not have to read
    # 200 circuit rows to find the four the AI could not make out -- the point
    # of the sheet is to put the uncertain in front of him (D3).
    def _some(items, n=8):
        return (", ".join(items[:n]) + ("..." if len(items) > n else ""))

    unknown: list[str] = []
    skipped = [ld for ld in sched.loads if ld.unresolved]
    if skipped and len(skipped) > len(sched.loads) / 2:
        # Not a flag on a few circuits: a read where most circuits name no
        # fitting produces a Lutron file with most of the house missing from
        # it. That is a failed read, and saying so plainly is kinder than
        # letting someone work through a sheet whose every row says "not built".
        unknown.append(
            f"<strong>This read did not work.</strong> {len(skipped)} of "
            f"{len(sched.loads)} circuits name no fitting, so the project that "
            f"would be built is mostly empty — the rooms, circuit names and "
            f"scenes are here, but almost nothing can be written. The fitting "
            f"catalogue below has {len(sched.fixtures)} type(s) in it that no "
            f"circuit uses. Read the extraction report for what stopped it, and "
            f"read the plans again rather than building this.")
    if skipped:
        unknown.append(
            f"<strong>{len(skipped)} circuit(s) have no fixture type and are NOT in the "
            f"built project</strong> ({_esc(_some([f'{sched.area_name(ld.area)} / {ld.zone}' for ld in skipped]))}). "
            f"The drawings did not say what is on them. Everything else is built as "
            f"normal; fill in FixtureRef and build again to add these.")
        # A circuit that is not built takes its scene levels and any keypad
        # button aimed at it with it. Both are shown below, so both have to say
        # they will not be written -- otherwise the sheet promises programming
        # the file does not contain.
        gone = {(ld.area, ld.zone) for ld in skipped}
        lost_levels = sum(1 for sc in sched.scenes for lv in sc.levels
                          if (sc.area, lv.zone) in gone)
        lost_buttons = sum(1 for b in sched.buttons for a in b.actions
                           if a.action_type == "DirectZoneLevel"
                           and (a.target_area, a.target_zone) in gone)
        if lost_levels or lost_buttons:
            unknown.append(
                f"Because of those circuits, {lost_levels} scene level(s) and "
                f"{lost_buttons} keypad button action(s) below cannot be written "
                f"either — they are marked “not built”. A scene whose every circuit "
                f"is missing arrives in Designer empty.")

    no_count = [ld for ld in sched.loads
                if "count" in ld.assumed and not ld.unresolved
                and watts_by_load[(ld.area, ld.zone)] is None]
    if no_count:
        unknown.append(
            f"{len(no_count)} circuit(s) have no fixture count "
            f"({_esc(_some([f'{sched.area_name(ld.area)} / {ld.zone}' for ld in no_count]))}). "
            f"Their load is shown as “{UNREAD}” and is excluded from every total on this "
            f"sheet — count them off the drawing and put the number in "
            f"NumberOfFixtures.")

    short_rooms = [(a, a.stated_circuits, sum(1 for ld in sched.loads if ld.area == a.uid))
                   for a in sched.areas if a.stated_circuits is not None]
    short_rooms = [x for x in short_rooms if x[2] < x[1]]
    if short_rooms:
        said = sum(n for _, n, _ in short_rooms)
        got = sum(n for _, _, n in short_rooms)
        worst = _some([f"{a.name} ({g} of {s2})" for a, s2, g in
                       sorted(short_rooms, key=lambda x: x[2] - x[1])], 6)
        unknown.append(
            f"<strong>{len(short_rooms)} room(s) have fewer circuits than the "
            f"documents say</strong> — {said} stated, {got} here ({_esc(worst)}). "
            f"A circuit that is not on this sheet is not built, not wired and on "
            f"no panel. Where a circuit estimate or switching schedule states a "
            f"count, it is the designer counting his own scheme: check these "
            f"rooms against it before building.")

    no_type = [f for f in sched.fixtures if "load_type_id" in f.assumed]
    if no_type:
        # Grouped by what each was assumed to BE, because the two assumptions
        # mean different hardware: an architectural fitting goes on a DALI bus,
        # something that takes a lamp on a phase dimmer.
        by_type: dict[int, list] = {}
        for f in no_type:
            by_type.setdefault(f.load_type_id, []).append(f.ref)
        detail = "; ".join(
            f"<strong>{_esc(load_type_name(t))}</strong>: {_esc(_some(refs))}"
            for t, refs in sorted(by_type.items()))
        unknown.append(
            f"{len(no_type)} fixture type(s) have an ASSUMED dimming type — the "
            f"drawings did not state a driver, so each was taken from what the "
            f"fitting is ({detail}). Anything that takes a lamp is assumed to be "
            f"mains reverse phase; everything else is assumed DALI. Check each one "
            f"— the fitting list in the app changes it in two clicks.")

    no_trims = [f for f in sched.fixtures if "trims" in f.assumed]
    if no_trims:
        unknown.append(
            f"{len(no_trims)} fixture type(s) use the default 5%/90% dimming trims "
            f"({_esc(_some([f.ref for f in no_trims]))}) rather than a range read off "
            f"the schedule.")

    linear_unmeasured = [ld for ld in sched.loads
                         if (fx := fixtures.get(ld.fixture_ref)) is not None
                         and fx.watts_per_metre and ld.run_length_m is None]
    if linear_unmeasured:
        unknown.append(
            f"{len(linear_unmeasured)} circuit(s) carry linear product specified per "
            f"metre with no run length "
            f"({_esc(_some([f'{sched.area_name(ld.area)} / {ld.zone}' for ld in linear_unmeasured]))}), "
            f"so their load cannot be worked out. Measure the run off the drawings.")

    if unknown:
        parts.append('<section><h2>Read this first — what the drawings did not say</h2>'
                     '<ul class="flags unread">'
                     + "".join(f"<li>{u}</li>" for u in unknown) + "</ul></section>")

    if flags:
        grouped: dict[str, list[str]] = {}
        for topic, text in flags:
            grouped.setdefault(topic, []).append(text)
        body = "".join(
            f'<h3>{_esc(topic)} <span class="sub">{len(grouped[topic])}</span></h3>'
            f'<ul class="flags">'
            + "".join(f"<li>{_esc(t)}</li>" for t in grouped[topic]) + "</ul>"
            for topic in TOPIC_ORDER if topic in grouped)
        parts.append(f"<section><h2>Worth a second look</h2>{body}</section>")

    # ---- fixture catalogue -----------------------------------------------
    def _watts(f):
        # A wattage looked up on the internet must never look identical to one
        # read off the drawings: it sizes circuits and panels.
        src = (f.data_source or "").strip()
        if f.watts_per_metre:
            return _Safe(f'{_esc(_w(f.watts_per_metre))} <span class="sub">W per metre</span>')
        if not f.wattage:
            return _Safe('<span class="bad">not stated</span>')
        # Only ever link an http(s) source: DataSource is AI-supplied, and a
        # "javascript:" or "data:" value would otherwise become a live link in
        # a sheet people open and click.
        if src.startswith(("http://", "https://")) and '"' not in src:
            return _Safe(f'{_esc(_w(f.wattage))} <span class="sub">researched \u00b7 '
                         f'<a href="{_esc(src)}" target="_blank" rel="noreferrer noopener">source</a></span>')
        if src and src.lower() not in ("drawings", "drawing"):
            return _Safe(f'{_esc(_w(f.wattage))} <span class="sub">{_esc(src)}</span>')
        return _w(f.wattage)

    def _load_type(f):
        """What drives this fitting -- and whether anyone actually said so (D2).

        The default is kept, because it is right far more often than not and a
        blank would not build. It just may not be shown as though the designer
        specified it.
        """
        name = load_type_name(f.load_type_id) or str(f.load_type_id)
        if "load_type_id" in f.assumed:
            return _Safe(f'{_esc(name)} <span class="assumed">assumed</span>')
        return name

    def _trims(f):
        text = f"{f.low_end}–{f.high_end}%"
        if "trims" in f.assumed:
            return _Safe(f'{_esc(text)} <span class="assumed">default</span>')
        return text

    parts.append("<section><h2>Fixture schedule</h2>" + _table(
        ["Ref", "Description", "Load type", "Watts", "Lamps", "Trim", "Used on", "Notes"],
        [[f.ref, f.description, _load_type(f), _watts(f),
          UNREAD if "lamp_quantity" in f.assumed else f.lamp_quantity, _trims(f),
          f"{sum(1 for ld in sched.loads if ld.fixture_ref == f.ref)} circuit(s)",
          _note(f.notes)]
         for f in sched.fixtures]) + "</section>")

    # ---- room by room ----------------------------------------------------
    parts.append("<section><h2>Rooms and circuits</h2>")
    for area in sched.areas:
        loads = loads_by_area.get(area.uid, [])
        known = [watts_by_load[(ld.area, ld.zone)] for ld in loads]
        watts = sum(v for v in known if v is not None)
        missing = sum(1 for v in known if v is None)
        rows = []
        for ld in loads:
            wired = wiring.get((area.uid, ld.zone))
            fx = fixtures.get(ld.fixture_ref)
            total = watts_by_load[(ld.area, ld.zone)]
            if ld.unresolved:
                fixture_cell = _Safe('<span class="bad">no fixture type — not built</span>')
            else:
                fixture_cell = fx.description if fx else "?"
            # D1: a load worked out from a guessed count is not a load. An em
            # dash and the note beside it, not a plausible number.
            if total is None:
                load_cell = _Safe(f'<span class="bad">{UNREAD}</span>')
            elif fx.watts_per_metre:
                load_cell = (f"{_w(ld.run_length_m)} m x {_w(fx.watts_per_metre)} W/m "
                             f"= {_w(total)}W")
            else:
                load_cell = f"{ld.count} x {_w(fx.wattage)}W = {_w(total)}W"
            # D10: a phase circuit between 500 W and 800 W fits the first output
            # of a module and nothing else. The writer deliberately does not lay
            # out panels, so the constraint has to travel to whoever does.
            if total is not None and total > PHASE_OUTPUT_W and _family(ld, fx) == "phase":
                load_cell = _Safe(f'{_esc(load_cell)} '
                                  f'<span class="assumed">first output only</span>')
            zone_cell = ld.zone
            if ld.additional_areas:
                zone_cell = _Safe(
                    f'{_esc(ld.zone)} <span class="sub">also lights '
                    f'{_esc(", ".join(sched.area_name(a) for a in ld.additional_areas))}'
                    f'</span>')
            # The circuit number, with the designer's own reference beside it
            # when the number was read out of one (C1 -> 1). A reference that
            # is kept but never shown is indistinguishable from one that was
            # ignored -- which is exactly how James read it (2026-08-12).
            if ld.zone_number is not None and ld.zone_ref:
                number_cell = _Safe(f'{ld.zone_number} <span class="sub">'
                                    f'{_esc(ld.zone_ref)}</span>')
            elif ld.zone_number is not None:
                number_cell = str(ld.zone_number)
            else:
                number_cell = ld.zone_ref
            row = [
                zone_cell, number_cell, ld.fixture_ref or UNREAD, fixture_cell,
                load_cell,
                f"{wired[0]} output {wired[1]}" if wired
                else _Safe('<span class="bad">not wired</span>'),
                _note(ld.notes),
            ]
            if loads_only:
                # No panels exist to wire to; a full column of red "not
                # wired" reads as a fault when it is simply Designer's job.
                del row[5]
            rows.append(row)
        # How the room is driven, so the grouping can be checked at a glance:
        # DALI, phase, 0-10V and switched never share a circuit, and a room
        # showing three of them is either a real mix or a misread.
        families = sorted({FAMILY_LABELS[_family(ld, fixtures.get(ld.fixture_ref))]
                           for ld in loads if not ld.unresolved})
        stated = ""
        if area.stated_circuits is not None and area.stated_circuits != len(loads):
            # The designer's own count against ours, on the row where someone
            # can act on it.
            stated = (f' <span class="assumed">documents say '
                      f'{area.stated_circuits}</span>')
        parts.append(
            f'<h3>{_esc(area.name)}{stated} <span class="sub">{_esc(area.parent)} &middot; '
            f'{len(loads)} circuit(s) &middot; {_w(watts)} W'
            + (f' + {missing} unknown' if missing else '')
            + (f' &middot; {_esc(", ".join(families))}' if families else '')
            + '</span></h3>'
            + (f'<p class="note">{_esc(area.notes)}</p>' if area.notes else ''))
        heads = ["Circuit", "No.", "Ref", "Fixture", "Load", "Wired to", "Notes"]
        if loads_only:
            heads.remove("Wired to")
        parts.append(_table(heads, rows)
                     if rows else '<p class="empty">No circuits.</p>')

        for kp in keypads_by_area.get(area.uid, []):
            btn_rows = []
            for b in sorted(buttons_by_keypad.get(kp.name, []), key=lambda x: x.number):
                # The engraving is what gets etched on a real faceplate, so a
                # PROPOSED label must not read like a specified one.
                engraving = (_Safe(f'<span>{_esc(b.label)}</span> '
                                   f'<span class="sub">proposed</span>')
                             if b.label_proposed else b.label)
                for act in b.actions:
                    if act.action_type == "RecallAreaScene":
                        target = f"{sched.area_name(act.target_area)} / {act.target_scene}"
                    else:
                        target = f"{act.target_zone} to {act.level}%"
                        if (act.target_area, act.target_zone) in unbuilt:
                            # The circuit has no fixture and is not written, so
                            # neither is this. Shown as programmed, it promised
                            # a button that would do nothing.
                            target = _Safe(f'{_esc(target)} '
                                           f'<span class="assumed">not built</span>')
                    btn_rows.append([
                        b.number, engraving, act.action_type, target, _note(b.notes)])
            parts.append(f'<h4>Keypad: {_esc(kp.station or kp.name)} '
                         f'<span class="sub">{_esc(kp.model_label)} &middot; '
                         f'{_esc(kp.link)} address {_esc(kp.address)}</span></h4>'
                         + (f'<p class="note">{_esc(kp.notes)}</p>' if kp.notes else ''))
            parts.append(_table(["Button", "Engraving", "Action", "Target", "Notes"], btn_rows)
                         if btn_rows else '<p class="empty">No buttons programmed.</p>')

        scenes = scenes_by_area.get(area.uid, [])
        if scenes and loads:
            zone_names = [ld.zone for ld in loads]
            head = ["Scene", "#"] + zone_names + ["Fade", "Notes"]
            rows = []
            for sc in sorted(scenes, key=lambda x: x.number):
                by_zone = {lv.zone: lv for lv in sc.levels}
                row = [sc.name, sc.number]
                for zn in zone_names:
                    lv = by_zone.get(zn)
                    if lv is None:
                        row.append(UNREAD)
                    elif (area.uid, zn) in unbuilt:
                        row.append(_Safe('<span class="assumed">not built</span>'))
                    else:
                        row.append("unaffected" if lv.level is None else f"{lv.level}%")
                # A fade the designer specified is silently rewritten to the one
                # calibrated value, and with no fade shown anywhere on the sheet
                # that loss was invisible. Say what the scene asks for, and what
                # will actually be written where they differ.
                row.append(_fades(sc))
                row.append(_note(sc.notes))
                rows.append(row)
            parts.append("<h4>Scenes</h4>" + _table(head, rows, "scenes"))
    parts.append("</section>")

    # ---- panel ------------------------------------------------------------
    if loads_only:
        # Panels are laid out in Designer; an empty "Panel schedule" heading
        # on a loads-only project is a promise this sheet never made.
        return _PAGE.format(project=_esc(project_name), shell=_esc(shell_name),
                            body="".join(parts))
    parts.append("<section><h2>Panel schedule</h2>")
    for mod in sched.modules:
        cap = mod.output_count or module_outputs.get(mod.name, 0)
        assigned = {o.output: o for o in sched.outputs if o.module == mod.name}
        rows = []
        for n in range(1, (cap or max(assigned or [0])) + 1):
            o = assigned.get(n)
            if o:
                load = next((ld for ld in sched.loads
                             if ld.area == o.area and ld.zone == o.zone), None)
                fx = fixtures.get(load.fixture_ref) if load else None
                watts = watts_by_load.get((o.area, o.zone)) if load else None
                rows.append([n, sched.area_name(o.area), o.zone,
                             (f"{_w(load.run_length_m)} m of {fx.ref}"
                              if load and fx and fx.watts_per_metre
                              else f"{load.count} x {fx.ref}" if load and fx else ""),
                             f"{_w(watts)} W" if watts is not None
                             else (UNREAD if load else "")])
            else:
                # _Safe, or _table escapes it and the engineer reads the raw
                # markup as text -- which shipped, on every spare output row.
                rows.append([n, _Safe('<span class="spare">spare</span>'), "", "", ""])
        parts.append(f'<h3>{_esc(mod.name)} <span class="sub">{_esc(mod.model_label)} &middot; '
                     f'{_esc(mod.area)} &middot; {_esc(mod.link)} address {_esc(mod.address)}'
                     f'</span></h3>')
        parts.append(_table(["Output", "Room", "Circuit", "Fixtures", "Load"], rows))
    parts.append("</section>")

    return _PAGE.format(
        project=_esc(project_name), shell=_esc(shell_name), body="".join(parts))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{project} — lighting schedule review</title>
<style>
  /* The homeplay.tv design tokens, same as the app shell: warm paper, ink,
     the olive-grey accent, Times Now for display. The fonts are served by
     the app at /brand/ when the sheet is viewed there; opened offline (it
     gets emailed) they fall back to Georgia / the system face, and the
     layout is designed to hold on the fallbacks. */
  :root {{ color-scheme: light dark;
    --fg:#111111; --bg:#f6ebe4; --panel:#fdf9f4; --mut:#6f6a5c;
    --line:#dcd2c6; --accent:#8d897c; --deep:#707052; --bad:#8c3a2e; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#f2efe8; --bg:#111111; --panel:#1d1c19; --mut:#9c978a;
             --line:#2f2d29; --accent:#8d897c; --deep:#a5a184; --bad:#e0846c; }}
  }}
  @font-face {{ font-family:'Times Now'; src:url(/brand/times-now.woff) format('woff');
    font-weight:300 700; font-display:swap; }}
  @font-face {{ font-family:'Saans'; src:url(/brand/saans-regular.woff2) format('woff2');
    font-weight:400; font-display:swap; }}
  @font-face {{ font-family:'Saans'; src:url(/brand/saans-semibold.woff2) format('woff2');
    font-weight:600; font-display:swap; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:2.25rem 1.5rem 5rem; background:var(--bg); color:var(--fg);
         font:15px/1.55 'Saans',system-ui,-apple-system,"Segoe UI",sans-serif;
         font-feature-settings:'calt' 0; }}
  main {{ max-width:70rem; margin:0 auto; }}
  h1 {{ font-family:'Times Now',Georgia,'Times New Roman',serif; font-weight:300;
        font-size:2.1rem; margin:0 0 .3rem; letter-spacing:-.02em; }}
  .lede {{ color:var(--mut); margin:0 0 2.25rem; max-width:62ch; }}
  /* Section headings are the site's caption idiom: tracked caps over a
     hairline, with the content carrying the size. */
  h2 {{ font-size:.8rem; font-weight:400; margin:3rem 0 .9rem; padding-bottom:.5rem;
        border-bottom:1px solid var(--line); text-transform:uppercase;
        letter-spacing:.12em; }}
  h3 {{ font-size:1rem; margin:1.75rem 0 .5rem; font-weight:600; }}
  h4 {{ font-size:.78rem; margin:1.4rem 0 .4rem; color:var(--mut); font-weight:400;
        text-transform:uppercase; letter-spacing:.1em; }}
  .sub {{ font-weight:400; color:var(--mut); font-size:.85rem; }}
  table {{ width:100%; border-collapse:collapse; margin:.35rem 0 1rem; font-size:.88rem; }}
  th,td {{ text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ font-weight:400; font-size:.72rem; text-transform:uppercase;
        letter-spacing:.1em; color:var(--mut); }}
  tbody tr:hover {{ background:color-mix(in srgb, var(--accent) 10%, transparent); }}
  .scroll {{ overflow-x:auto; }}
  .tiles {{ display:flex; flex-wrap:wrap; gap:.75rem; margin:0 0 1rem; }}
  .tile {{ flex:1 1 8rem; padding:.9rem 1.1rem; border:1px solid var(--line);
           border-radius:.5rem; background:var(--panel); }}
  .tile .n {{ font-family:'Times Now',Georgia,serif; font-weight:300; font-size:1.9rem;
              letter-spacing:-.02em; }}
  .tile .l {{ font-size:.72rem; color:var(--mut); text-transform:uppercase;
              letter-spacing:.1em; }}
  .flags {{ margin:.5rem 0; padding-left:1.2rem; }}
  .flags li {{ margin:.25rem 0; }}
  .bad {{ color:var(--bad); font-weight:600; }}
  .spare {{ color:var(--mut); font-style:italic; }}
  .flags.unread li {{ margin:.4rem 0; }}
  .flags.unread {{ padding:.75rem 1rem .75rem 2.2rem; border:1px solid var(--bad);
                   border-radius:.5rem;
                   background:color-mix(in srgb,var(--bad) 7%,transparent); }}
  .rownote {{ color:var(--mut); font-size:.85rem; }}
  .assumed {{ color:var(--bad); font-size:.72rem; text-transform:uppercase;
              letter-spacing:.08em; font-weight:600; margin-left:.3rem; }}
  p.note {{ color:var(--mut); font-size:.85rem; margin:.2rem 0 .4rem; }}
  .empty {{ color:var(--mut); font-style:italic; margin:.35rem 0 1rem; }}
  table.scenes {{ font-variant-numeric:tabular-nums; }}
  a {{ color:var(--deep); }}
  ::selection {{ background:#858567; color:#fff; }}
  @media print {{ body {{ padding:0; background:#fff; }} h2 {{ break-after:avoid; }}
                  table {{ break-inside:auto; }} }}
</style></head>
<body><main>
<h1>{project}</h1>
<p class="lede">Lighting schedule review &middot; built from shell <strong>{shell}</strong>.
Check this before writing the Lutron programme — every circuit, wiring assignment,
keypad button and scene level below is exactly what will be written.</p>
{body}
</main></body></html>
"""


def write(sched: Schedule, path: str, project_name: str, shell_name: str,
          module_outputs: dict[str, int] | None = None) -> str:
    out = render(sched, project_name, shell_name, module_outputs)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return path
