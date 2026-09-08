"""
Reading the shell: what the engineer already authored in Designer.

The shell is the contract boundary.  Designer owns project-shape decisions --
the project name, the processors, the comm links, the floor tree.  This tool
owns everything that hangs below a floor: rooms, fixtures, loads, modules,
wiring, scenes, keypads and buttons.

Two things are read out of the shell rather than hard-coded, both for the same
reason: the values are Designer *catalogue* references, not database facts, and
they differ between Designer versions and installed product libraries.

  * **Module blueprints** -- how many outputs a module model has.
  * **Keypad blueprints** -- which button numbers a model exposes, and which
    LED catalogue IDs pair with them.

If a model isn't present in the shell, we cannot invent its catalogue IDs, and
we say so plainly: add one example in Designer, save, and re-run.  That is the
same instruction Lutron's own workflow gives, and it fails loudly instead of
writing a keypad that Designer will open but never light up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sqlrunner import BaseRunner, SqlError


@dataclass
class ButtonSlot:
    number: int          # tblKeypadButton.ButtonNumber (a physical position code)
    sort_order: int
    engraved: bool       # main buttons carry engraving; raise/lower do not


@dataclass
class LedSlot:
    number: int          # tblLed.LedNumber
    info_id: int         # tblLed.LedInfoId -- a Designer catalogue reference
    sort_order: int


@dataclass
class KeypadBlueprint:
    model_info_id: int
    template_device_id: int
    template_station_id: int
    buttons: list[ButtonSlot]
    leds: list[LedSlot]
    # Button groups are catalogue-shaped too: ButtonGroupInfoID and
    # ButtonGroupObjectType differ per keypad model, and some models carry two
    # groups per device rather than one.  So carry the template's group rows and
    # clone all of them, in SortOrder, rather than inventing a single group.
    button_group_ids: list[int] = field(default_factory=list)
    # One engraving style per station (the faceplate) and one per device (the
    # buttons).  The font values on the device row vary by model.
    station_engraving_style_id: int | None = None
    device_engraving_style_id: int | None = None
    # Whether a device carries a keypad controller is a per-model fact too:
    # every 2478/2482/2486/3918 device in the reference has exactly one, and
    # no 1811/4772/5029/5338 device has any.
    keypad_controller_id: int | None = None

    @property
    def engraved_buttons(self) -> list[ButtonSlot]:
        return [b for b in self.buttons if b.engraved]


@dataclass
class ModuleBlueprint:
    model_info_id: int
    template_enclosure_id: int
    template_device_id: int
    output_count: int
    link_type: int


@dataclass
class ShellIndex:
    project_name: str
    project_id: int
    master_processor_id: int
    all_processor_ids: list[int]
    next_object_id: int
    floors: dict[str, int]                 # level-3 floor name -> AreaID
    links: dict[str, tuple[int, int]]      # link name -> (LinkID, LinkInfoID)
    link_addresses: dict[int, set[int]]    # LinkID -> addresses already taken
    link_orders: dict[int, set[int]]       # LinkID -> OrderOnCommunicationLink values taken
    max_led_number: int                    # highest real tblLed.LedNumberOnLink
    max_zone_number: int
    max_integration_id: int = 0            # highest tblIntegrationID.IntegrationID in use
    designer_version: str = ""

    # template row ids, discovered lazily
    room_area_id: int = 0
    floor_area_id: int = 0
    # Floor creation (floors the schedule names that the shell lacks). A
    # floor's companion web, verified against all three level-3 floors in the
    # reference file: two tblAreaMode rows + one tblDLSetPointLevelAssignment
    # + maps on every processor -- and, unlike a room, NO tblIntegrationID and
    # no daylighting/occupancy groups.
    floor_sort_max: int = -1               # highest SortOrder among existing floors
    floor_area_mode_ids: list[int] = field(default_factory=list)
    floor_dl_setpoint_id: int = 0
    daylighting_group_id: int | None = None
    occupancy_group_id: int | None = None
    zone_id: int = 0
    switchleg_id: int = 0
    fixture_assignment_id: int = 0
    fixture_id: int = 0
    scene_controller_id: int = 0
    scene_id: int = 0
    scene_preset_assignment_id: int = 0
    button_preset_assignment_id: int = 0
    switchleg_controller_id: int = 0
    module_link_node_id: int = 0
    keypad_link_node_id: int = 0
    programming_model_id: int = 0
    preset_id: int = 0
    engraving_id: int = 0
    led_id: int = 0
    keypad_controller_id: int = 0
    # Companion rows Designer writes for every area / enclosure. Omitting these
    # produces a file that restores cleanly and is then rejected on open.
    area_mode_ids: list[int] = field(default_factory=list)
    dl_setpoint_id: int = 0
    power_panel_enclosure_id: int = 0
    switchleg_daylightable_id: int = 0
    # QSX links carry a TLink topology object, and every device on such a link
    # gets a tblTLinkNode row under it. Devices on other link kinds do not.
    tlinks: dict[int, int] = field(default_factory=dict)       # LinkID -> TLinkID
    tlink_node_id: int = 0                                     # template row to clone
    tlink_orders: dict[int, int] = field(default_factory=dict)  # TLinkID -> max SortOrder
    tlink_device_numbers: dict[int, int] = field(default_factory=dict)  # TLinkID -> max DeviceNumber

    modules: dict[int, ModuleBlueprint] = field(default_factory=dict)
    keypads: dict[int, KeypadBlueprint] = field(default_factory=dict)


def _int(v, default=None):
    """sqlcmd renders NULL as the literal text 'NULL'."""
    if v is None or v in ("NULL", ""):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _one(runner: BaseRunner, db: str, sql: str, what: str):
    rows = runner.rows(sql, db)
    if not rows:
        raise SqlError(
            f"The shell has no {what}. This tool copies the shape of rows Designer "
            f"itself wrote, so it needs at least one example to work from.")
    return rows[0]


def read_shell(runner: BaseRunner, db: str) -> ShellIndex:
    r = runner.rows
    project = _one(runner, db, "SELECT TOP 1 ProjectID, Name FROM tblProject", "project row")

    # Zero processors is a supported state (clean-shell direction, 2026-08-02):
    # Designer's own fresh projects have none until one is added, and the
    # builder simply writes no processor maps. master_processor_id 0 = none.
    procs = [int(x[0]) for x in r("SELECT ProcessorID FROM tblProcessor ORDER BY ProcessorID", db)]

    # The master is whichever processor the existing areas are mapped to.
    master_row = r("""
SELECT TOP 1 ProcessorID FROM tblObjectToProcessorMap
WHERE IsMaster = 1 GROUP BY ProcessorID ORDER BY COUNT(*) DESC;
""", db)
    master = int(master_row[0][0]) if master_row else (procs[0] if procs else 0)

    idx = ShellIndex(
        project_name=project[1],
        project_id=int(project[0]),
        master_processor_id=master,
        all_processor_ids=procs,
        next_object_id=int(r("SELECT TOP 1 NextObjectID FROM tblNextObjectID", db)[0][0]),
        floors={row[1]: int(row[0]) for row in
                r("SELECT AreaID, Name FROM tblArea WHERE HierarchyLevel = 3 AND IsLeaf = 0", db)},
        links={row[1]: (int(row[0]), int(row[2])) for row in
               r("SELECT LinkID, Name, LinkInfoID FROM tblLink", db)},
        link_addresses={},
        link_orders={},
        max_led_number=0,
        max_zone_number=int(r("SELECT ISNULL(MAX(ZoneNumber), 0) FROM tblZone", db)[0][0]),
        # Integration IDs are one project-wide sequence shared by rooms, circuits
        # and keypads, so new ones must continue past whatever is already in use.
        max_integration_id=_int(
            r("SELECT ISNULL(MAX(IntegrationID), 0) FROM tblIntegrationID", db)[0][0], 0),
    )

    for row in r("SELECT LinkAssignedToID, AddressOnLink FROM tblLinkNode WHERE AddressOnLink IS NOT NULL", db):
        try:
            link_id, addr = int(row[0]), int(row[1])
        except (ValueError, IndexError):
            continue
        idx.link_addresses.setdefault(link_id, set()).add(addr)

    # Designer keeps OrderOnCommunicationLink unique among the devices on a link.
    # Reuse a value and the file imports partway and then fails, so track what is
    # taken and carry on from there.
    for row in r("""
SELECT ln.LinkAssignedToID, ed.OrderOnCommunicationLink
FROM tblLinkNode ln JOIN tblEnclosureDevice ed
  ON ed.EnclosureDeviceID = ln.ParentDeviceID AND ln.ParentDeviceType = 30
UNION ALL
SELECT ln.LinkAssignedToID, csd.OrderOnCommunicationLink
FROM tblLinkNode ln JOIN tblControlStationDevice csd
  ON csd.ControlStationDeviceID = ln.ParentDeviceID AND ln.ParentDeviceType = 5;
""", db):
        link_id, order = _int(row[0]), _int(row[1])
        if link_id is not None and order is not None:
            idx.link_orders.setdefault(link_id, set()).add(order)

    # 65535 reads as an unset sentinel rather than a real LED number.
    led_max = _int(r("SELECT ISNULL(MAX(LedNumberOnLink), 0) FROM tblLed "
                     "WHERE LedNumberOnLink < 60000", db)[0][0], 0)
    idx.max_led_number = led_max or 0

    if not idx.floors:
        raise SqlError(
            "The shell has no floors (areas at hierarchy level 3). Create the floor tree "
            "in Designer first -- the writer places rooms under floors you have authored.")

    _read_templates(runner, db, idx)
    _read_module_blueprints(runner, db, idx)
    _read_keypad_blueprints(runner, db, idx)
    return idx


def _read_templates(runner: BaseRunner, db: str, idx: ShellIndex) -> None:
    """Pick one known-good row per table to clone from."""
    q = lambda sql, what: _one(runner, db, sql, what)  # noqa: E731

    room = q("""SELECT TOP 1 AreaID, DaylightingGroupAssignedToID, OccupancyGroupAssignedToID
                FROM tblArea WHERE HierarchyLevel = 4 AND IsLeaf = 1
                ORDER BY CASE WHEN DaylightingGroupAssignedToID IS NULL THEN 1 ELSE 0 END, AreaID""",
             "rooms (areas at hierarchy level 4)")
    idx.room_area_id = int(room[0])
    idx.daylighting_group_id = int(room[1]) if room[1] not in ("NULL", "", None) else None
    idx.occupancy_group_id = int(room[2]) if room[2] not in ("NULL", "", None) else None

    idx.floor_area_id = next(iter(idx.floors.values()))

    sm = runner.rows("SELECT ISNULL(MAX(SortOrder), -1) FROM tblArea "
                     "WHERE HierarchyLevel = 3 AND IsLeaf = 0", db)
    idx.floor_sort_max = _int(sm[0][0], -1) if sm else -1
    idx.floor_area_mode_ids = [int(row[0]) for row in runner.rows(
        f"SELECT AreaModeID FROM tblAreaMode WHERE ParentAreaID = {idx.floor_area_id} "
        "ORDER BY AreaModeType", db)]
    fdl = runner.rows("SELECT TOP 1 DLSetPointLevelAssignmentID "
                      f"FROM tblDLSetPointLevelAssignment WHERE ParentId = {idx.floor_area_id}", db)
    idx.floor_dl_setpoint_id = _int(fdl[0][0], 0) if fdl else 0

    # A load: zone + switchleg + fixture assignment + a lighting fixture.
    # Prefer a fully wired set so every cloned column carries a working value,
    # but accept an unattached example — a zero-equipment shell has nothing
    # wired at all, and NULL ControllerID is Designer's own off-link state.
    load = q("""
SELECT TOP 1 z.ZoneID, sl.SwitchLegID, fa.FixtureAssignmentID, f.FixtureID
FROM tblZone z
JOIN tblZonable zn ON zn.AssociatedZoneID = z.ZoneID AND zn.ZonableObjectType = 10
JOIN tblSwitchLeg sl ON sl.SwitchLegID = zn.ZonableID
JOIN tblFixtureAssignment fa ON fa.PARENTID = sl.SwitchLegID
JOIN tblFixture f ON f.FixtureID = fa.FixtureID
WHERE f.LoadTypePropertyType = 4
ORDER BY CASE WHEN zn.ControllerID IS NULL THEN 1 ELSE 0 END, z.ZoneID""",
             "lighting loads")
    idx.zone_id, idx.switchleg_id, idx.fixture_assignment_id, idx.fixture_id = (int(x) for x in load[:4])

    scene = q("""
SELECT TOP 1 sc.SceneControllerID, s.SceneID, pa.PresetAssignmentID
FROM tblSceneController sc
JOIN tblScene s ON s.ParentSceneControllerID = sc.SceneControllerID
JOIN tblPresetAssignment pa ON pa.ParentID = s.SceneID AND pa.ParentType = 41
                           AND pa.AssignableObjectType = 15 AND pa.AssignmentCommandType = 2
ORDER BY s.SceneID""", "scenes with per-zone level recipes")
    idx.scene_controller_id, idx.scene_id, idx.scene_preset_assignment_id = (int(x) for x in scene[:3])

    # Every area in Designer's own data carries these; a room without them
    # fails validation at open time even though the file restores fine.
    idx.area_mode_ids = [int(r[0]) for r in runner.rows(
        "SELECT TOP 1 AreaModeID FROM tblAreaMode WHERE AreaModeType = 1 ORDER BY AreaModeID", db)]
    idx.area_mode_ids += [int(r[0]) for r in runner.rows(
        "SELECT TOP 1 AreaModeID FROM tblAreaMode WHERE AreaModeType = 2 ORDER BY AreaModeID", db)]

    dl = runner.rows("SELECT TOP 1 DLSetPointLevelAssignmentID FROM tblDLSetPointLevelAssignment "
                     "ORDER BY DLSetPointLevelAssignmentID", db)
    idx.dl_setpoint_id = _int(dl[0][0], 0) if dl else 0

    # Every enclosure has a tblPowerPanel row -- 17 of 17 in the reference file.
    pp = runner.rows("SELECT TOP 1 EnclosureID FROM tblPowerPanel ORDER BY EnclosureID", db)
    idx.power_panel_enclosure_id = _int(pp[0][0], 0) if pp else 0

    # Every switch leg has a tblDaylightable row keyed by its own ID -- 38 of 38
    # in the reference file. Designer does not treat a missing one as a warning:
    # SwitchLeg.InitializeFromDatabase_Step2 reads it unconditionally, throws
    # NullReferenceException, and the project load deadlocks at 100%. Any switch
    # leg's row will do as a template; they are identical apart from the key.
    dlable = runner.rows("SELECT TOP 1 DaylightableID FROM tblDaylightable "
                         "WHERE DaylightableObjectType = 10 ORDER BY DaylightableID", db)
    idx.switchleg_daylightable_id = _int(dlable[0][0], 0) if dlable else 0

    # TLink topology: which links have one, the highest sort order and device
    # number in use on each, and one node row to clone the constants from.
    for row in runner.rows("SELECT ParentID, TLinkID FROM tblTLink", db):
        link_id, tlink_id = _int(row[0]), _int(row[1])
        if link_id is not None and tlink_id is not None:
            idx.tlinks[link_id] = tlink_id
    tn = runner.rows("SELECT TOP 1 TLinkNodeID FROM tblTLinkNode ORDER BY TLinkNodeID", db)
    idx.tlink_node_id = _int(tn[0][0], 0) if tn else 0
    for row in runner.rows("SELECT ParentID, MAX(SortOrder), MAX(DeviceNumber) "
                           "FROM tblTLinkNode GROUP BY ParentID", db):
        tlink_id = _int(row[0])
        if tlink_id is not None:
            idx.tlink_orders[tlink_id] = _int(row[1], -1)
            idx.tlink_device_numbers[tlink_id] = _int(row[2], -1)

    # Module templates are optional: a zero-equipment shell has none, and the
    # builder only needs them when the schedule actually writes modules (which
    # validation already refuses when the shell carries no module examples).
    slc = runner.rows("SELECT TOP 1 SwitchLegControllerID FROM tblSwitchLegController "
                      "WHERE ParentDeviceType = 30 ORDER BY 1", db)
    idx.switchleg_controller_id = _int(slc[0][0], 0) if slc else 0
    mln = runner.rows("SELECT TOP 1 LinkNodeID FROM tblLinkNode "
                      "WHERE ParentDeviceType = 30 ORDER BY 1", db)
    idx.module_link_node_id = _int(mln[0][0], 0) if mln else 0
    # A keypad-device link node to clone for keypads -- present whenever the
    # shell keeps keypad model examples, even unattached ones.
    kln = runner.rows("SELECT TOP 1 LinkNodeID FROM tblLinkNode "
                      "WHERE ParentDeviceType = 5 ORDER BY 1", db)
    idx.keypad_link_node_id = _int(kln[0][0], 0) if kln else 0


def _read_module_blueprints(runner: BaseRunner, db: str, idx: ShellIndex) -> None:
    rows = runner.rows("""
SELECT ed.ModelInfoID,
       MIN(e.EnclosureID), MIN(ed.EnclosureDeviceID),
       MAX(ISNULL(ln.LinkType, 28))
FROM tblEnclosureDevice ed
JOIN tblEnclosure e ON e.EnclosureID = ed.ParentEnclosureID
LEFT JOIN tblLinkNode ln ON ln.ParentDeviceID = ed.EnclosureDeviceID AND ln.ParentDeviceType = 30
GROUP BY ed.ModelInfoID;
""", db)
    for model, enc, dev, link_type in ((int(a), int(b), int(c), int(d)) for a, b, c, d in rows):
        n = runner.rows(
            f"SELECT COUNT(*) FROM tblSwitchLegController WHERE ParentDeviceID = {dev} AND ParentDeviceType = 30", db)
        idx.modules[model] = ModuleBlueprint(
            model_info_id=model, template_enclosure_id=enc, template_device_id=dev,
            output_count=int(n[0][0]) if n else 0, link_type=link_type)


def _read_keypad_blueprints(runner: BaseRunner, db: str, idx: ShellIndex) -> None:
    devices = runner.rows("""
SELECT csd.ModelInfoID, MIN(csd.ControlStationDeviceID), MIN(cs.ControlStationID)
FROM tblControlStationDevice csd
JOIN tblControlStation cs ON cs.ControlStationID = csd.ParentControlStationID
GROUP BY csd.ModelInfoID;
""", db)
    for model, dev, station in ((int(a), int(b), int(c)) for a, b, c in devices):
        btn_rows = runner.rows(f"""
SELECT b.ButtonNumber, b.SortOrder,
       CASE WHEN EXISTS (SELECT 1 FROM tblEngravingPosition ep
                         WHERE ep.ParentDeviceID = b.ButtonID) THEN 1 ELSE 0 END
FROM tblKeypadButton b WHERE b.ParentDeviceID = {dev} ORDER BY b.SortOrder;
""", db)
        led_rows = runner.rows(f"""
SELECT LedNumber, LedInfoId, SortOrder FROM tblLed
WHERE ParentDeviceID = {dev} ORDER BY SortOrder;
""", db)
        # Designer's own integrity check reports "ButtonGroupMissing" for a
        # device with no group, and the load then stalls. Take every group the
        # template device has: a model may legitimately have more than one, and
        # each carries its own catalogue reference.
        group_rows = runner.rows(f"""
SELECT ButtonGroupID FROM tblButtonGroup
WHERE ParentDeviceID = {dev} AND ParentDeviceType = 5
ORDER BY SortOrder, ButtonGroupID;
""", db)
        style_dev = runner.rows(f"""
SELECT TOP 1 EngravingStyleID FROM tblEngravingStyle
WHERE ParentID = {dev} AND ParentDeviceType = 5 ORDER BY EngravingStyleID;
""", db)
        style_station = runner.rows(f"""
SELECT TOP 1 EngravingStyleID FROM tblEngravingStyle
WHERE ParentID = {station} AND ParentDeviceType = 4 ORDER BY EngravingStyleID;
""", db)
        kc = runner.rows(f"""
SELECT TOP 1 KeypadControllerID FROM tblKeypadController
WHERE ParentDeviceID = {dev} AND ParentDeviceType = 5 ORDER BY KeypadControllerID;
""", db)
        buttons = [ButtonSlot(_int(a, 0), _int(b, 0), c == "1") for a, b, c in btn_rows
                   if _int(a) is not None]
        if not buttons:
            continue
        # A LED with no catalogue reference cannot be recreated on a new keypad;
        # skip it rather than write a row Designer will not recognise.
        leds = [LedSlot(_int(a, 0), _int(b), _int(c, 0)) for a, b, c in led_rows
                if _int(b) is not None]
        idx.keypads[model] = KeypadBlueprint(
            model_info_id=model,
            template_device_id=dev,
            template_station_id=station,
            buttons=buttons,
            leds=leds,
            button_group_ids=[_int(row[0], 0) for row in group_rows if _int(row[0])],
            station_engraving_style_id=_int(style_station[0][0]) if style_station else None,
            device_engraving_style_id=_int(style_dev[0][0]) if style_dev else None,
            keypad_controller_id=_int(kc[0][0]) if kc else None,
        )

    if idx.keypads:
        any_model = next(iter(idx.keypads.values()))
        row = runner.rows(f"""
SELECT TOP 1 b.ButtonID, b.ProgrammingModelID, p.PresetID, ep.EngravingPositionID,
       (SELECT TOP 1 LedID FROM tblLed WHERE ParentDeviceID = {any_model.template_device_id}),
       (SELECT TOP 1 KeypadControllerID FROM tblKeypadController
         WHERE ParentDeviceID = {any_model.template_device_id}),
       (SELECT TOP 1 pa.PresetAssignmentID FROM tblPresetAssignment pa
         WHERE pa.ParentID = p.PresetID AND pa.AssignableObjectType = 2
           AND pa.AssignmentCommandType = 5)
FROM tblKeypadButton b
JOIN tblProgrammingModel pm ON pm.ProgrammingModelID = b.ProgrammingModelID
JOIN tblPreset p ON p.ParentID = pm.ProgrammingModelID
JOIN tblEngravingPosition ep ON ep.ParentDeviceID = b.ButtonID
ORDER BY b.ButtonID;
""", db)
        if row:
            v = row[0]
            idx.programming_model_id = _int(v[1], 0)
            idx.preset_id = _int(v[2], 0)
            idx.engraving_id = _int(v[3], 0)
            idx.led_id = _int(v[4], 0)
            idx.keypad_controller_id = _int(v[5], 0)
            idx.button_preset_assignment_id = _int(v[6], 0)


def next_free_address(idx: ShellIndex, link_id: int, start: int = 1, limit: int = 254) -> int:
    used = idx.link_addresses.setdefault(link_id, set())
    for a in range(start, limit + 1):
        if a not in used:
            used.add(a)
            return a
    raise SqlError(f"comm link {link_id} has no free addresses between {start} and {limit}")


def next_link_order(idx: ShellIndex, link_id: int) -> int:
    """Next unused OrderOnCommunicationLink for a device joining this link."""
    used = idx.link_orders.setdefault(link_id, set())
    order = (max(used) + 1) if used else 2
    used.add(order)
    return order
