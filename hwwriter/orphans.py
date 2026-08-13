"""
Clearing rows whose parent has gone.

Much of this schema is polymorphic: `tblKeypadButton.ParentDeviceID` holds an
ID whose *table* is decided by `ParentDeviceType`.  A column like that cannot
carry a foreign key, so the database will happily let the parent be deleted and
leave the child pointing at nothing -- and the foreign-key walk in `cascade.py`
is blind to the relationship entirely.

These rules spell out those parent links by hand.  Each is "rows of this table
whose parent no longer exists", swept repeatedly until a pass changes nothing,
because deleting one generation orphans the next.
"""

from __future__ import annotations

from .cascade import ForeignKeyGraph, cascade_delete
from .sqlrunner import BaseRunner

# (table, predicate matching rows whose parent is gone)
RULES: list[tuple[str, str]] = [
    ("tblEnclosure",
     "[ParentAreaID] IS NOT NULL AND NOT EXISTS "
     "(SELECT 1 FROM tblArea a WHERE a.AreaID = tblEnclosure.ParentAreaID)"),
    ("tblEnclosureDevice",
     "NOT EXISTS (SELECT 1 FROM tblEnclosure e "
     "WHERE e.EnclosureID = tblEnclosureDevice.ParentEnclosureID)"),
    ("tblSwitchLegController",
     "[ParentDeviceType] = 30 AND NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d "
     "WHERE d.EnclosureDeviceID = tblSwitchLegController.ParentDeviceID)"),

    ("tblControlStation",
     "NOT EXISTS (SELECT 1 FROM tblArea a WHERE a.AreaID = tblControlStation.ParentId)"),
    ("tblControlStationDevice",
     "NOT EXISTS (SELECT 1 FROM tblControlStation cs "
     "WHERE cs.ControlStationID = tblControlStationDevice.ParentControlStationID)"),
    ("tblKeypadController",
     "[ParentDeviceType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblKeypadController.ParentDeviceID)"),
    ("tblKeypadButton",
     "[ParentDeviceType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblKeypadButton.ParentDeviceID)"),
    ("tblLed",
     "[ParentDeviceType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblLed.ParentDeviceID)"),
    ("tblEngravingPosition",
     "[ParentDeviceType] = 57 AND NOT EXISTS (SELECT 1 FROM tblKeypadButton b "
     "WHERE b.ButtonID = tblEngravingPosition.ParentDeviceID)"),
    ("tblBacklightComponent",
     "NOT EXISTS (SELECT 1 FROM tblKeypadButton b "
     "WHERE b.ButtonID = tblBacklightComponent.ParentID)"),

    ("tblLinkNode",
     "NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d "
     "WHERE d.EnclosureDeviceID = tblLinkNode.ParentDeviceID) "
     "AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblLinkNode.ParentDeviceID) "
     "AND NOT EXISTS (SELECT 1 FROM tblProcessor p "
     "WHERE p.ProcessorID = tblLinkNode.ParentDeviceID)"),

    ("tblZone", "NOT EXISTS (SELECT 1 FROM tblArea a WHERE a.AreaID = tblZone.ParentID)"),
    ("tblSwitchLeg",
     "NOT EXISTS (SELECT 1 FROM tblArea a WHERE a.AreaID = tblSwitchLeg.ParentID)"),
    # A shade leg (ObjectType 192) whose tblShadeSwitchLeg companion has gone
    # cannot load: sel_SwitchLegAll LEFT JOINs the companion, Designer reads
    # ShadeType with GetByte, and the NULL kills the loader thread with the
    # project stuck at "Importing... 100%" (the WG freeze, 2026-08-01).
    ("tblSwitchLeg",
     "[ObjectType] = 192 AND NOT EXISTS (SELECT 1 FROM tblShadeSwitchLeg x "
     "WHERE x.SwitchLegID = tblSwitchLeg.SwitchLegID)"),
    # Every switch leg in the reference has a zonable (38/38). A leg whose
    # zonable died -- e.g. through the FK cascade when the panel hosting its
    # controller was deleted -- NullReferences the loader the same way (the
    # second WG freeze, 2026-08-01). The real fix is sparing the feeding
    # panels (example_kit); this is the safety net.
    ("tblSwitchLeg",
     "NOT EXISTS (SELECT 1 FROM tblZonable zn "
     "WHERE zn.ZonableID = tblSwitchLeg.SwitchLegID)"),
    ("tblZonable",
     "NOT EXISTS (SELECT 1 FROM tblSwitchLeg sl WHERE sl.SwitchLegID = tblZonable.ZonableID)"),
    ("tblFixtureAssignment",
     "[ParentType] = 10 AND NOT EXISTS (SELECT 1 FROM tblSwitchLeg sl "
     "WHERE sl.SwitchLegID = tblFixtureAssignment.PARENTID)"),

    ("tblSceneController",
     "NOT EXISTS (SELECT 1 FROM tblArea a WHERE a.AreaID = tblSceneController.ParentID)"),
    ("tblScene",
     "NOT EXISTS (SELECT 1 FROM tblSceneController sc "
     "WHERE sc.SceneControllerID = tblScene.ParentSceneControllerID)"),

    ("tblProgrammingModel",
     "[ParentType] = 57 AND NOT EXISTS (SELECT 1 FROM tblKeypadButton b "
     "WHERE b.ButtonID = tblProgrammingModel.ParentID)"),
    ("tblPreset",
     "NOT EXISTS (SELECT 1 FROM tblProgrammingModel pm "
     "WHERE pm.ProgrammingModelID = tblPreset.ParentID)"),
    ("tblPresetAssignment",
     "([ParentType] = 41 AND NOT EXISTS (SELECT 1 FROM tblScene s "
     "WHERE s.SceneID = tblPresetAssignment.ParentID)) "
     "OR ([ParentType] = 43 AND NOT EXISTS (SELECT 1 FROM tblPreset p "
     "WHERE p.PresetID = tblPresetAssignment.ParentID))"),
    ("tblAssignmentCommandParameter",
     "NOT EXISTS (SELECT 1 FROM tblPresetAssignment pa "
     "WHERE pa.PresetAssignmentID = tblAssignmentCommandParameter.ParentId)"),

    # Daylighting/occupancy groups are parented to the PROCESSOR SYSTEM (43),
    # not to a room -- the room points at its group via
    # DaylightingGroupAssignedToID. The old parent-must-be-an-area version of
    # these rules deleted every group in every strip, kept rooms included,
    # which is why stripped shells never had one to clone.
    ("tblDaylightingGroup",
     "NOT EXISTS (SELECT 1 FROM tblArea a "
     "WHERE a.DaylightingGroupAssignedToID = tblDaylightingGroup.DaylightingGroupID)"),
    ("tblOccupancyGroup",
     "NOT EXISTS (SELECT 1 FROM tblArea a "
     "WHERE a.OccupancyGroupAssignedToID = tblOccupancyGroup.OccupancyGroupID)"),

    # Companion rows keyed by (or pointing at) an object's own ID. Cleaning
    # these here matters twice over: Designer's own repair code crashes on some
    # of them (a tblTLinkNode pointing at a deleted device hung the load), and
    # any leftover masks the deletion from the dangling sweep, because these
    # tables' primary keys ARE the object IDs and so keep them "alive" in the
    # before/after diff.
    ("tblButtonGroup",
     "[ParentDeviceType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblButtonGroup.ParentDeviceID)"),
    ("tblEngravingStyle",
     "([ParentDeviceType] = 4 AND NOT EXISTS (SELECT 1 FROM tblControlStation cs "
     "WHERE cs.ControlStationID = tblEngravingStyle.ParentID)) "
     "OR ([ParentDeviceType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblEngravingStyle.ParentID))"),
    ("tblTLinkNode",
     "([DeviceObjectType] = 30 AND NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d "
     "WHERE d.EnclosureDeviceID = tblTLinkNode.DeviceId)) "
     "OR ([DeviceObjectType] = 5 AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblTLinkNode.DeviceId))"),
    # A daylightable is 1:1 with a zonable of the same ID, whatever the object
    # kind -- switch leg (10), shade drive (192) or Ketra emitter (363). The
    # zonable-based check covers all three; the earlier type-10-only version
    # left shade and emitter daylightables dangling, which is one of the things
    # behind Designer's "problem with Shade Group programming" warning.
    ("tblDaylightable",
     "NOT EXISTS (SELECT 1 FROM tblZonable zn "
     "WHERE zn.ZonableID = tblDaylightable.DaylightableID)"),
    # Every zone in the reference carries a zonable (41 of 41); one whose
    # circuit died with a deleted shade or lamp system must not linger.
    ("tblZone",
     "NOT EXISTS (SELECT 1 FROM tblZonable zn "
     "WHERE zn.AssociatedZoneID = tblZone.ZoneID)"),
    # These two are keyed by a bare object ID with no type discriminator, so the
    # liveness check is the union of every table such an ID can belong to --
    # processors and Ketra smart lamps carry rows here too, not just the two
    # device tables.
    ("tblDeviceComponentMap",
     "NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d "
     "WHERE d.EnclosureDeviceID = tblDeviceComponentMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblDeviceComponentMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblProcessor p "
     "WHERE p.ProcessorID = tblDeviceComponentMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblSmartLamp sm "
     "WHERE sm.SmartLampID = tblDeviceComponentMap.DomainObjectID)"),
    ("tblDeviceDisplayNameMap",
     "NOT EXISTS (SELECT 1 FROM tblEnclosureDevice d "
     "WHERE d.EnclosureDeviceID = tblDeviceDisplayNameMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblDeviceDisplayNameMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblProcessor p "
     "WHERE p.ProcessorID = tblDeviceDisplayNameMap.DomainObjectID) "
     "AND NOT EXISTS (SELECT 1 FROM tblSmartLamp sm "
     "WHERE sm.SmartLampID = tblDeviceDisplayNameMap.DomainObjectID)"),
    # Integration IDs, per object kind we delete. Kinds the strip never touches
    # (processors, gateways, ...) are left alone.
    ("tblIntegrationID",
     "([DomainControlBaseObjectType] = 2 AND NOT EXISTS (SELECT 1 FROM tblArea a "
     "WHERE a.AreaID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] = 5 AND NOT EXISTS "
     "(SELECT 1 FROM tblControlStationDevice csd "
     "WHERE csd.ControlStationDeviceID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] IN (15, 198, 211, 370) AND NOT EXISTS "
     "(SELECT 1 FROM tblZone z WHERE z.ZoneID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] = 133 AND NOT EXISTS "
     "(SELECT 1 FROM tblShadeGroup sg "
     "WHERE sg.ShadeGroupID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] = 169 AND NOT EXISTS "
     "(SELECT 1 FROM tblVariable v "
     "WHERE v.VariableID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] = 90 AND NOT EXISTS "
     "(SELECT 1 FROM tblSequence sq "
     "WHERE sq.SequenceID = tblIntegrationID.DomainControlBaseObjectID)) "
     "OR ([DomainControlBaseObjectType] = 19 AND NOT EXISTS "
     "(SELECT 1 FROM tblTimeClock tc "
     "WHERE tc.TimeClockID = tblIntegrationID.DomainControlBaseObjectID))"),
    # An assignment whose TARGET is gone, even though its parent survives --
    # timeclock automation aiming at a deleted circuit is the classic case.
    # Designer's loader dereferences the target during sel_PresetAssignmentAllSP
    # and a dead one hangs the project open, exactly like a missing
    # tblDaylightable row.
    ("tblPresetAssignment",
     "([AssignableObjectType] IN (15, 198, 211, 370) AND NOT EXISTS "
     "(SELECT 1 FROM tblZone z WHERE z.ZoneID = tblPresetAssignment.AssignableObjectID)) "
     "OR ([AssignableObjectType] = 2 AND NOT EXISTS "
     "(SELECT 1 FROM tblArea a WHERE a.AreaID = tblPresetAssignment.AssignableObjectID)) "
     # A preset assignment aiming at a deleted shade group crashed
     # PresetAssignment.InitializeFromDatabase and hung the WD load -- the
     # tblIntegrationID leftovers for the same shade groups masked their
     # deletion from the dangling sweep, so nothing else caught it.
     "OR ([AssignableObjectType] = 133 AND NOT EXISTS "
     "(SELECT 1 FROM tblShadeGroup sg "
     "WHERE sg.ShadeGroupID = tblPresetAssignment.AssignableObjectID)) "
     "OR ([AssignableObjectType] = 169 AND NOT EXISTS "
     "(SELECT 1 FROM tblVariable v "
     "WHERE v.VariableID = tblPresetAssignment.AssignableObjectID)) "
     "OR ([AssignableObjectType] = 90 AND NOT EXISTS "
     "(SELECT 1 FROM tblSequence sq "
     "WHERE sq.SequenceID = tblPresetAssignment.AssignableObjectID)) "
     "OR ([AssignableObjectType] = 19 AND NOT EXISTS "
     "(SELECT 1 FROM tblTimeClock tc "
     "WHERE tc.TimeClockID = tblPresetAssignment.AssignableObjectID))"),
    # Sequence steps orphaned when the dangling sweep removes their parent
    # sequence (second-generation debris; there is no foreign key).
    ("tblSequenceStep",
     "NOT EXISTS (SELECT 1 FROM tblSequence s "
     "WHERE s.SequenceID = tblSequenceStep.ParentSequenceID)"),
]

MAX_PASSES = 6


def sweep(runner: BaseRunner, database: str, graph: ForeignKeyGraph | None = None) -> int:
    """Delete orphans until a pass finds none. Returns the number of passes run."""
    graph = graph or ForeignKeyGraph(runner, database)
    tables = {r[0] for r in runner.rows("SELECT name FROM sys.tables", database)}
    rules = [(t, p) for t, p in RULES if t in tables]

    for pass_no in range(1, MAX_PASSES + 1):
        remaining = runner.scalar(_count_sql(rules), database)
        if remaining in (None, "0", 0):
            return pass_no - 1
        script = ("SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
                  + cascade_delete(graph, rules)
                  + "\nCOMMIT TRANSACTION;\n")
        runner.script(script, database, label=f"orphan sweep {pass_no}")
    return MAX_PASSES


def _count_sql(rules: list[tuple[str, str]]) -> str:
    parts = [f"(SELECT COUNT(*) FROM [{t}] WHERE {p})" for t, p in rules]
    return "SELECT " + " + ".join(parts)
