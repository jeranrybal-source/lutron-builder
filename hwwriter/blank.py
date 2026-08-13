"""
Stripping a project back to a blank shell.

Normal use does not need this: an engineer makes a new project in Designer,
adds the floors and comm links, and that *is* the shell.  This exists for the
case where the only file to hand is a finished project and you want its
scaffolding -- processors, links, floor tree -- without its programming.

What is kept:  tblProject, tblProcessor and the enclosures hosting them,
               tblLink, and areas at hierarchy levels 1-3 (site, wrapper, floors).
What is removed: every room and everything hanging off one.

The strip runs *after* the build, not before, and spares everything with an
object ID at or above `keep_from`.  That ordering matters: this writer works by
cloning rows Designer wrote, so the originals have to still be there while the
new programme is being assembled.  Strip first and there is nothing left to copy.

The delete order is not hand-written -- it is derived from the database's own
foreign-key graph (see `cascade.py`), because the interesting dependants are
the ones nobody thinks to look for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import dangling, orphans
from .cascade import ForeignKeyGraph, cascade_delete
from .sqlrunner import BaseRunner


@dataclass
class Spared:
    """Rows to keep even though the strip would otherwise remove them.

    Used to leave one example of each module and keypad model behind, so the
    stripped file is still usable as a writing shell -- the writer copies model
    catalogue references off a real row and cannot invent them.
    """

    enclosure_ids: set[int] = field(default_factory=set)
    station_ids: set[int] = field(default_factory=set)
    area_ids: set[int] = field(default_factory=set)
    fixture_ids: set[int] = field(default_factory=set)
    # The one room whose loads and scenes are kept as worked examples. Other
    # rooms in `area_ids` survive only because a model example sits in them,
    # and are emptied.
    template_area_ids: set[int] = field(default_factory=set)

    def _clause(self, column: str, ids: set[int]) -> str:
        if not ids:
            return ""
        return f" AND [{column}] NOT IN ({', '.join(str(i) for i in sorted(ids))})"

    @property
    def enclosures(self) -> str:
        return self._clause("EnclosureID", self.enclosure_ids)

    @property
    def stations(self) -> str:
        return self._clause("ControlStationID", self.station_ids)

    @property
    def areas(self) -> str:
        return self._clause("AreaID", self.area_ids)

    @property
    def fixtures(self) -> str:
        return self._clause("FixtureID", self.fixture_ids)

    @property
    def in_kept_area(self) -> str:
        """Exclude rows whose parent area is the worked-example room."""
        return self._clause("ParentID", self.template_area_ids)


# Roots of the strip, parents last. Each is a table plus the predicate picking
# the rows that belong to the shell's own programming rather than ours.
def _targets(keep_from: int, spare: Spared | None = None) -> list[tuple[str, str]]:
    spare = spare or Spared()
    # The room that houses a processor stays: deleting it would cascade into
    # tblProcessor and leave the project with no processor at all.
    doomed_area = (f"HierarchyLevel >= 4 AND AreaID < {keep_from} "
                   f"AND NOT EXISTS (SELECT 1 FROM tblEnclosure e "
                   f"JOIN tblProcessor p ON p.ParentEnclosureID = e.EnclosureID "
                   f"WHERE e.ParentAreaID = tblArea.AreaID)"
                   f"{spare.areas}")
    return [
        # Control stations and scene controllers hang off areas at any level,
        # not just rooms -- a floor or the site itself can own scenes.
        ("tblControlStation", f"[ParentId] < {keep_from}{spare.stations}"),
        ("tblSceneController", f"[ParentID] < {keep_from}{spare.in_kept_area}"),

        # Shade (curtain/blind) groups and Ketra smart-lamp systems go
        # entirely, worked examples included. The writer cannot create either
        # from a schedule, and keeping half of one -- the devices survive as
        # model examples while their circuits die with the emptied rooms --
        # makes Designer open the shell with "a problem has been found with
        # Shade Group programming" warnings. James chose removal over
        # preserving the full chains (2026-08-01).
        ("tblShadeGroup", f"[ShadeGroupID] < {keep_from}"),
        # Shade legs are ObjectType 192 -- the marker Designer's own loader
        # classifies by. The predicate must NOT test tblShadeSwitchLeg: the
        # cascade deletes child rows first with this predicate inlined, so an
        # EXISTS on the child table empties itself before the parent DELETE
        # runs, leaving the legs alive with no companion. Designer then reads
        # ShadeType through sel_SwitchLegAll's LEFT JOIN, gets NULL, and the
        # project load dies (the WG freeze, 2026-08-01).
        ("tblSwitchLeg", f"[SwitchLegID] < {keep_from} AND [ObjectType] = 192"),
        ("tblSmartLamp", f"[SmartLampID] < {keep_from}"),
        ("tblEmitter", f"[EmitterID] < {keep_from}"),

        # Loads go from every pre-existing area, including the rooms we are
        # keeping only because a processor lives in them.
        ("tblSwitchLeg", f"[ParentID] < {keep_from}{spare.in_kept_area}"),
        ("tblZone", f"[ParentID] < {keep_from}{spare.in_kept_area}"),

        # Modules -- never an enclosure that houses a processor.
        ("tblEnclosure",
         f"[EnclosureID] < {keep_from} AND NOT EXISTS "
         f"(SELECT 1 FROM tblProcessor p WHERE p.ParentEnclosureID = tblEnclosure.EnclosureID)"
         f"{spare.enclosures}"),

        ("tblFixture", f"[FixtureID] < {keep_from}{spare.fixtures}"),

        # Finally the rooms themselves.
        ("tblArea", doomed_area),
    ]


_ORPHAN_MAP_CLEANUP = """
-- Objects that no longer exist must not keep a processor mapping.
DELETE m FROM tblObjectToProcessorMap m
WHERE NOT EXISTS (SELECT 1 FROM tblArea a WHERE a.AreaID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblZone z WHERE z.ZoneID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblSwitchLeg s WHERE s.SwitchLegID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblFixture f WHERE f.FixtureID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblFixtureAssignment fa
                  WHERE fa.FixtureAssignmentID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblEnclosure e WHERE e.EnclosureID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d WHERE d.EnclosureDeviceID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblProcessor p WHERE p.ProcessorID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblLink l WHERE l.LinkID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblLinkNode n WHERE n.LinkNodeID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblScene sc WHERE sc.SceneID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblSceneController scc
                  WHERE scc.SceneControllerID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblPresetAssignment pa
                  WHERE pa.PresetAssignmentID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblPreset pr WHERE pr.PresetID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblProgrammingModel pm
                  WHERE pm.ProgrammingModelID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblKeypadButton kb WHERE kb.ButtonID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblControlStation cs
                  WHERE cs.ControlStationID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd
                  WHERE csd.ControlStationDeviceID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblSwitchLegController c
                  WHERE c.SwitchLegControllerID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblEngravingPosition ep
                  WHERE ep.EngravingPositionID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblDaylightingGroup dg
                  WHERE dg.DaylightingGroupID = m.DomainObjectID)
  AND NOT EXISTS (SELECT 1 FROM tblOccupancyGroup og
                  WHERE og.OccupancyGroupID = m.DomainObjectID);
"""


def blank_script(graph: ForeignKeyGraph, keep_from: int,
                 spare: Spared | None = None) -> str:
    return ("SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
            + cascade_delete(graph, _targets(keep_from, spare))
            + "\n" + _ORPHAN_MAP_CLEANUP
            + "\nCOMMIT TRANSACTION;\n")


def scrub_cloud_identity(runner: BaseRunner, database: str) -> None:
    """Give a stripped project a fresh identity, severing the cloud link.

    A stripped shell otherwise keeps the source project's cloud fingerprint --
    the tblPlaceIntegrationDetails row naming its Lutron cloud Place, and
    tblProject's GUID and Xid. Designer then matches any file built on the
    shell to the SOURCE's cloud Place and asks, on every open, whether it
    should adopt the source project's name ("Project name and Place name do
    not match"). A generated project must never claim someone's real house.
    """
    import base64
    import uuid

    xid = base64.urlsafe_b64encode(uuid.uuid4().bytes).rstrip(b"=").decode("ascii")
    runner.script(f"""
DELETE FROM tblPlaceIntegrationDetails;
UPDATE tblProject SET [GUID] = NEWID(), [Xid] = N'{xid}', [UserID] = N'';
""", database, label="scrub cloud identity")


def _shade_drive_enclosures(runner: BaseRunner, database: str,
                            keep_from: int) -> list[int]:
    """Enclosures whose devices exist to drive shade legs (QS wallboxes).

    When the shade legs go, these devices are left half-orphaned and Designer
    opens the shell with "A problem has been found with an enclosure device --
    Recommended fix: Delete ...\\QS Shade Group 001\\Enclosure Device 001".
    Identified by data (a controller feeding an ObjectType-192 leg), never by
    model number or name.
    """
    rows = runner.rows(f"""
SELECT DISTINCT e.EnclosureID
FROM tblEnclosure e
JOIN tblEnclosureDevice d ON d.ParentEnclosureID = e.EnclosureID
JOIN tblSwitchLegController c ON c.ParentDeviceID = d.EnclosureDeviceID
                             AND c.ParentDeviceType = 30
JOIN tblZonable zn ON zn.ControllerID = c.SwitchLegControllerID
JOIN tblSwitchLeg sl ON sl.SwitchLegID = zn.ZonableID AND sl.ObjectType = 192
WHERE e.EnclosureID < {keep_from}
  AND NOT EXISTS (SELECT 1 FROM tblProcessor p
                  WHERE p.ParentEnclosureID = e.EnclosureID);
""", database)
    return [int(r[0]) for r in rows if r and r[0] not in ("NULL", "")]


def _strip_equipment(runner: BaseRunner, database: str, keep_from: int,
                     graph: ForeignKeyGraph) -> None:
    """Remove ALL equipment: processors, module enclosures, comm links.

    The clean-shell direction (James, 2026-08-02): shells carry no equipment
    at all -- adding processors and modules in Designer is quick manual work.
    Everything that stays (example loads, keypads) is detached first into
    Designer's own off-link state, proven to open and hand-attach cleanly:

      * every tblZonable keeps its row with ControllerID NULL (type stays);
      * every tblLinkNode keeps its row with LinkAssignedToID/AddressOnLink
        NULL -- module link nodes then die with their devices via the sweeps.

    Detaching BEFORE the cascade is what protects the kept examples: with the
    references NULLed there is no FK path from the dying equipment into them.
    """
    # tblProcessorSystem (object 43) is the SYSTEM/topology object, not a
    # physical processor: it is the polymorphic parent of the level-2 "Floor
    # 001" wrapper area, i.e. the root of the whole area tree, and a fresh
    # Designer project carries it before any processor exists. It holds no FK
    # to tblProcessor, but its GatewayProcessorID pointer would get the row
    # deleted by the dangling sweep once the processors are gone -- and with
    # it the area tree's root, which breaks Designer's load (Area 44
    # NullReference, "get_RootAreaParent index out of range"; found 2026-08-03).
    # NULL the pointer so the row survives the sweep.
    runner.script("""
UPDATE tblZonable SET ControllerID = NULL WHERE ControllerID IS NOT NULL;
UPDATE tblLinkNode SET LinkAssignedToID = NULL, AddressOnLink = NULL
WHERE LinkAssignedToID IS NOT NULL OR AddressOnLink IS NOT NULL;
UPDATE tblProcessorSystem SET GatewayProcessorID = NULL
WHERE GatewayProcessorID IS NOT NULL;
""", database, label="detach equipment references")
    # Sensor connections hang off the equipment being removed, and their
    # programming models parent to them polymorphically (ParentType 66) --
    # a pairing the orphan sweep does not map, so Designer would find and
    # auto-delete the leftovers on every open. Remove the pair explicitly.
    runner.script(
        "SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
        + cascade_delete(graph, [
            ("tblProgrammingModel",
             f"[ParentType] = 66 AND [ParentID] IN "
             f"(SELECT SensorConnectionID FROM tblSensorConnection "
             f"WHERE SensorConnectionID < {int(keep_from)})"),
            ("tblSensorConnection", f"[SensorConnectionID] < {int(keep_from)}"),
            ("tblProcessor", f"[ProcessorID] < {int(keep_from)}"),
            ("tblEnclosure", f"[EnclosureID] < {int(keep_from)}"),
            ("tblTLinkNode", f"[TLinkNodeID] < {int(keep_from)}"),
            ("tblTLink", f"[TLinkID] < {int(keep_from)}"),
            ("tblLink", f"[LinkID] < {int(keep_from)}"),
        ])
        + "\nCOMMIT TRANSACTION;\n",
        database, label="strip equipment")
    orphans.sweep(runner, database, graph)
    dangling.sweep(runner, database, graph)
    orphans.sweep(runner, database, graph)


def strip(runner: BaseRunner, database: str, keep_from: int,
          spare: Spared | None = None, strip_equipment: bool = False) -> str:
    """Remove the shell's own programming, sparing objects with ID >= keep_from."""
    graph = ForeignKeyGraph(runner, database)
    # Record what exists now, so anything left pointing at a deleted row can be
    # found afterwards by difference.
    dangling.snapshot(runner, database)
    if strip_equipment:
        _strip_equipment(runner, database, keep_from, graph)
    # Shade-drive enclosures must go with the shade legs they feed. This runs
    # as a separate pass with the IDs inlined as literals: a predicate that
    # walked to the legs through the enclosure's own child rows would delete
    # those children first and then match nothing -- the self-defeating DELETE
    # that caused the first WG freeze.
    doomed = [] if strip_equipment else _shade_drive_enclosures(runner, database, keep_from)
    if doomed:
        ids = ", ".join(str(i) for i in doomed)
        runner.script(
            "SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
            + cascade_delete(graph, [("tblEnclosure", f"[EnclosureID] IN ({ids})")])
            + "\nCOMMIT TRANSACTION;\n",
            database, label="shade drive enclosures")
    script = blank_script(graph, keep_from, spare)
    runner.script(script, database, label="blank")
    # Polymorphic parent columns carry no foreign key, so the cascade above
    # cannot see them; sweep the rows they orphaned.
    orphans.sweep(runner, database, graph)
    # And sweep rows that survived while still pointing at something deleted.
    dangling.sweep(runner, database, graph)
    # The dangling sweep deletes whole rows (a sequence whose terminate preset
    # died, say), which orphans their children in turn. One more orphan pass
    # picks those up.
    orphans.sweep(runner, database, graph)
    # Floors the strip has emptied (every room they held is gone) are source-
    # project hangover in the output -- "External" in the reference shell.
    # Same literal-ID pattern as the shade enclosures: compute AFTER the room
    # strip (a predicate evaluated before it would see the rooms still there),
    # then run the sweeps once more to clean the floors' companion rows.
    empty = [int(r0[0]) for r0 in runner.rows(f"""
SELECT f.AreaID FROM tblArea f
WHERE f.HierarchyLevel = 3 AND f.IsLeaf = 0 AND f.AreaID < {int(keep_from)}
  AND NOT EXISTS (SELECT 1 FROM tblArea r WHERE r.ParentID = f.AreaID)""", database)]
    if empty:
        ids = ", ".join(str(i) for i in empty)
        runner.script(
            "SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
            + cascade_delete(graph, [("tblArea", f"[AreaID] IN ({ids})")])
            + "\nCOMMIT TRANSACTION;\n",
            database, label="empty floors")
        orphans.sweep(runner, database, graph)
        dangling.sweep(runner, database, graph)
    # The re-baselined snapshot table must not ship inside the .hw.
    dangling.cleanup(runner, database)
    # Any stripped output is a NEW project, never the source one -- give it a
    # fresh identity so it cannot claim the source's cloud Place.
    scrub_cloud_identity(runner, database)
    return script


def example_kit(runner: BaseRunner, database: str,
                include_modules: bool = True) -> Spared:
    """
    Choose what a stripped shell must keep to still be usable for writing.

    The writer clones a row Designer wrote for every kind of object it creates,
    so a shell needs one worked example of each: a room with loads wired to a
    module, a scene with level recipes, a keypad with programmed buttons -- plus
    one example of every module and keypad *model* anyone will want to write.

    With include_modules=False (the zero-equipment shell), no enclosures are
    spared at all: the equipment strip removes every module and detaches the
    kept room's loads into the proven off-link state, so the feeding-chain
    rule (keep the panel or the loads half-die) no longer applies.
    """
    spare = Spared()

    # The richest room: the most wired loads, and ideally scenes and a keypad
    # too, so one room supplies most of the templates.
    best = runner.rows("""
SELECT TOP 1 a.AreaID
FROM tblArea a
JOIN tblZone z ON z.ParentID = a.AreaID
JOIN tblZonable zn ON zn.AssociatedZoneID = z.ZoneID
WHERE a.HierarchyLevel >= 4
GROUP BY a.AreaID
ORDER BY
  -- Prefer a wired room, but accept an unattached one: a shell rebuilt from a
  -- clean-shell project has NO wired loads at all, and requiring one kept
  -- nothing (found 2026-08-04 rebuilding the shell for new keypad families).
  CASE WHEN MAX(CASE WHEN zn.ControllerID IS NOT NULL THEN 1 ELSE 0 END) = 1
       THEN 0 ELSE 1 END,
  CASE WHEN EXISTS (SELECT 1 FROM tblSceneController sc WHERE sc.ParentID = a.AreaID)
       THEN 0 ELSE 1 END,
  CASE WHEN EXISTS (SELECT 1 FROM tblControlStation cs WHERE cs.ParentId = a.AreaID)
       THEN 0 ELSE 1 END,
  COUNT(*) DESC;
""", database)
    if best and best[0][0] not in ("NULL", ""):
        spare.template_area_ids.add(int(best[0][0]))
        spare.area_ids.add(int(best[0][0]))

    # Fixtures used by the kept room, so its loads still resolve.
    if spare.template_area_ids:
        ids = ", ".join(str(i) for i in sorted(spare.template_area_ids))
        for row in runner.rows(f"""
SELECT DISTINCT fa.FixtureID
FROM tblFixtureAssignment fa
JOIN tblSwitchLeg sl ON sl.SwitchLegID = fa.PARENTID
WHERE sl.ParentID IN ({ids}) AND fa.FixtureID IS NOT NULL;
""", database):
            if row and row[0] not in ("NULL", ""):
                spare.fixture_ids.add(int(row[0]))

    # The enclosures feeding the kept room's loads, so the wiring chain
    # (zonable -> switch-leg controller -> module device -> enclosure) stays
    # intact. Keeping the room but not the panel that dims it half-kills the
    # loads: the controllers die with the panel, the FK cascade takes the
    # zonables, the orphan rules then take the zones and daylightables, and
    # Designer NullReferences loading the surviving legs (the second WG
    # freeze, 2026-08-01). Same principle as the fixtures above.
    if include_modules and spare.template_area_ids:
        ids = ", ".join(str(i) for i in sorted(spare.template_area_ids))
        for row in runner.rows(f"""
SELECT DISTINCT e.EnclosureID
FROM tblZonable zn
JOIN tblSwitchLeg sl ON sl.SwitchLegID = zn.ZonableID AND sl.ObjectType = 10
JOIN tblSwitchLegController c ON c.SwitchLegControllerID = zn.ControllerID
JOIN tblEnclosureDevice d ON d.EnclosureDeviceID = c.ParentDeviceID
JOIN tblEnclosure e ON e.EnclosureID = d.ParentEnclosureID
WHERE sl.ParentID IN ({ids});
""", database):
            if row and row[0] not in ("NULL", ""):
                spare.enclosure_ids.add(int(row[0]))

    # One enclosure per module model, and one station per keypad model.
    if include_modules:
        for row in runner.rows("""
SELECT MIN(e.EnclosureID)
FROM tblEnclosureDevice ed
JOIN tblEnclosure e ON e.EnclosureID = ed.ParentEnclosureID
WHERE NOT EXISTS (SELECT 1 FROM tblProcessor p WHERE p.ParentEnclosureID = e.EnclosureID)
  -- never keep a shade-drive device as a module example: the writer cannot
  -- write shades, and the strip removes these enclosures outright.
  AND NOT EXISTS (SELECT 1 FROM tblSwitchLegController c
                  JOIN tblZonable zn ON zn.ControllerID = c.SwitchLegControllerID
                  JOIN tblSwitchLeg sl ON sl.SwitchLegID = zn.ZonableID
                                      AND sl.ObjectType = 192
                  WHERE c.ParentDeviceID = ed.EnclosureDeviceID
                    AND c.ParentDeviceType = 30)
GROUP BY ed.ModelInfoID;
""", database):
            if row and row[0] not in ("NULL", ""):
                spare.enclosure_ids.add(int(row[0]))

    for row in runner.rows("""
SELECT MIN(cs.ControlStationID)
FROM tblControlStationDevice csd
JOIN tblControlStation cs ON cs.ControlStationID = csd.ParentControlStationID
GROUP BY csd.ModelInfoID;
""", database):
        if row and row[0] not in ("NULL", ""):
            spare.station_ids.add(int(row[0]))

    # Whatever rooms those examples live in have to survive too.
    encs = ", ".join(str(i) for i in sorted(spare.enclosure_ids)) or "-1"
    stns = ", ".join(str(i) for i in sorted(spare.station_ids)) or "-1"
    for row in runner.rows(f"""
SELECT ParentAreaID FROM tblEnclosure WHERE EnclosureID IN ({encs})
UNION
SELECT ParentId FROM tblControlStation WHERE ControlStationID IN ({stns});
""", database):
        if row and row[0] not in ("NULL", ""):
            spare.area_ids.add(int(row[0]))

    return spare
