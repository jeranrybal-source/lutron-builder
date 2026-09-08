"""
Turning a validated schedule into rows in a Lutron project database.

Emission order matters, because each domain references the one before it:

    areas -> fixtures -> modules -> loads -> scenes -> keypads

Modules come before loads so that a load can be pointed at its output channel
as it is created, rather than inserted headless and patched afterwards.  Scenes
come before keypads because a scene-recall button needs a scene number to aim
at.  Nothing is written until the whole batch has been assembled, and the whole
batch commits or rolls back together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .emit import Emitter, IdAllocator, Raw, Schema, new_xid
from .schedule import (
    CMD_RECALL_SCENE,
    CMD_SET_LEVEL,
    CMD_UNAFFECTED,
    OBJ_AREA,
    OBJ_ZONE,
    PARAM_DELAY,
    PARAM_FADE,
    PARAM_LEVEL,
    PARAM_SCENE_NUMBER,
    Schedule,
    fade_raw,
    half_up,
)
from .shell import ShellIndex, next_free_address, next_link_order
from .sqlrunner import SqlError

# Polymorphic discriminators, from the observed Designer rows.
PT_AREA = 2
INTEG_AREA = 2           # tblIntegrationID.DomainControlBaseObjectType values
INTEG_KEYPAD_DEVICE = 5
INTEG_ZONE = 15
PT_SWITCHLEG = 10
PT_ENCLOSURE_DEVICE = 30
PT_SCENE = 41            # a PresetAssignment owned by a Scene
PT_BUTTON_PRESET = 43    # a PresetAssignment owned by a button Preset
PT_AREA_ALT = 49         # tblArea, in daylighting/occupancy group contexts
PT_CONTROL_STATION = 4   # tblControlStation, as an engraving-style parent
PT_KEYPAD_DEVICE = 5     # tblControlStationDevice, as a LinkNode parent
PT_BUTTON = 57           # tblKeypadButton, as an engraving/programming parent

CONTROLLER_SWITCHLEG = 3


def _quantity(fixture, load) -> int:
    """What Designer's NumberofFixtures should say for this circuit.

    Metres for a fitting sold by the metre, a count for everything else.
    Designer stores it as a whole number, so 10.4 m becomes 10 -- the review
    sheet carries the figure to the metre and says where it rounds.
    """
    if fixture.watts_per_metre and load.run_length_m is not None:
        return max(1, half_up(load.run_length_m))
    return load.count


@dataclass
class BuildResult:
    emitter: Emitter
    area_ids: dict[str, int] = field(default_factory=dict)
    zone_ids: dict[tuple[str, str], int] = field(default_factory=dict)
    fixture_ids: dict[str, int] = field(default_factory=dict)
    scene_numbers: dict[tuple[str, str], int] = field(default_factory=dict)
    module_outputs: dict[tuple[str, int], int] = field(default_factory=dict)


class Builder:
    def __init__(self, schema: Schema, shell: ShellIndex, sched: Schedule):
        self.schema = schema
        self.shell = shell
        self.sched = sched
        self.ids = IdAllocator(shell.next_object_id)
        self.em = Emitter(schema, self.ids)
        self.r = BuildResult(self.em)
        self._led_link_number = self._next_led_number()
        self._integration_id = shell.max_integration_id

    # ------------------------------------------------------------- helpers

    def _next_led_number(self) -> int:
        return self.shell.max_led_number + 1

    def _stamp(self, table: str, extra: dict) -> dict:
        """Give a cloned row its own identity: fresh Xid and Guid where present."""
        out = dict(extra)
        if self.schema.has_column(table, "Xid"):
            out.setdefault("Xid", new_xid())
        if self.schema.has_column(table, "Guid"):
            out.setdefault("Guid", Raw("NEWID()"))
        return out

    def _map(self, object_id: int, all_processors: bool = False) -> None:
        """Every domain object must be mapped to a processor to be transferred.

        A zero-equipment shell has no processor to map to -- Designer's own
        fresh projects are in exactly this state until one is added, and
        Designer back-fills the maps when that happens. Write nothing.
        """
        if not self.shell.master_processor_id:
            return
        self.em.insert("tblObjectToProcessorMap", {
            "DomainObjectID": object_id,
            "ProcessorID": self.shell.master_processor_id,
            "IsMaster": 1})
        if all_processors:
            for pid in self.shell.all_processor_ids:
                if pid != self.shell.master_processor_id:
                    self.em.insert("tblObjectToProcessorMap", {
                        "DomainObjectID": object_id, "ProcessorID": pid, "IsMaster": 0})

    def _integration(self, object_id: int, obj_type: int) -> None:
        """
        Every room, circuit and keypad Designer writes gets an integration ID --
        the number external systems use to address it. One project-wide sequence
        shared across all object kinds; 41 of 41 zones and 16 of 16 rooms carry
        one in the reference file, so continue the sequence rather than skip it.
        """
        self._integration_id += 1
        self.em.insert("tblIntegrationID", {
            "DomainControlBaseObjectID": object_id,
            "IntegrationID": self._integration_id,
            "DomainControlBaseObjectType": obj_type})

    def _tlink_node(self, link_id: int, dev_id: int, dev_type: int) -> None:
        """
        A device joining a QSX link also joins that link's TLink topology --
        Designer writes a tblTLinkNode row for every device on such a link (18
        of 18 in the reference file) and none for devices on other link kinds.
        Sort order and device number each continue the link's own sequence.
        The rows are not mapped to a processor; Designer maps none of its own.
        """
        tlink = self.shell.tlinks.get(link_id)
        if not tlink or not self.shell.tlink_node_id:
            return
        order = self.shell.tlink_orders.get(tlink, -1) + 1
        self.shell.tlink_orders[tlink] = order
        number = self.shell.tlink_device_numbers.get(tlink, -1) + 1
        self.shell.tlink_device_numbers[tlink] = number
        self.em.clone("tblTLinkNode", f"TLinkNodeID = {self.shell.tlink_node_id}",
                      {"TLinkNodeID": self.ids.take(),
                       "ParentID": tlink,
                       "SortOrder": order,
                       "DeviceObjectType": dev_type,
                       "DeviceId": dev_id,
                       "DeviceNumber": number})

    def _params(self, parent_id: int, pairs: list[tuple[int, int]]) -> None:
        """tblAssignmentCommandParameter is keyed by (ParentId, SortOrder) -- no surrogate key."""
        for i, (ptype, value) in enumerate(pairs):
            self.em.insert("tblAssignmentCommandParameter", {
                "ParentId": parent_id, "SortOrder": i,
                "ParameterType": ptype, "ParameterValue": value})

    # --------------------------------------------------------------- build

    def run(self) -> BuildResult:
        self._areas()
        self._fixtures()
        self._modules()
        self._loads()
        self._scenes()
        self._keypads()
        return self.r

    # ---- areas -----------------------------------------------------------

    def _floor(self, name: str) -> int:
        """
        Clone a new level-3 floor from an existing one. The companion web,
        verified against all three floors in the reference file: two
        tblAreaMode rows, one tblDLSetPointLevelAssignment (mapped), and maps
        on every processor. Unlike rooms, floors carry NO tblIntegrationID and
        no daylighting/occupancy groups (all three reference floors: none).
        ParentID / HierarchyLevel / IsLeaf are kept from the cloned template.
        """
        floor_id = self.ids.take()
        self.shell.floor_sort_max += 1
        self.em.comment(f"Floor: {name} (not in the shell -- created)")
        self.em.clone("tblArea", f"AreaID = {self.shell.floor_area_id}",
                      self._stamp("tblArea", {
                          "AreaID": floor_id,
                          "Name": name,
                          "SortOrder": self.shell.floor_sort_max,
                          "UserUniqueID": ""}))
        for template_id in self.shell.floor_area_mode_ids:
            self.em.clone("tblAreaMode", f"AreaModeID = {template_id}",
                          self._stamp("tblAreaMode", {
                              "AreaModeID": self.ids.take(),
                              "ParentAreaID": floor_id}))
        if self.shell.floor_dl_setpoint_id:
            dl_id = self.ids.take()
            self.em.clone("tblDLSetPointLevelAssignment",
                          f"DLSetPointLevelAssignmentID = {self.shell.floor_dl_setpoint_id}",
                          self._stamp("tblDLSetPointLevelAssignment", {
                              "DLSetPointLevelAssignmentID": dl_id,
                              "ParentId": floor_id}))
            self._map(dl_id)
        self._map(floor_id, all_processors=True)
        self.shell.floors[name] = floor_id
        return floor_id

    def _areas(self) -> None:
        if not self.sched.areas:
            return
        for name in dict.fromkeys(a.parent for a in self.sched.areas
                                  if a.parent and a.parent not in self.shell.floors):
            self._floor(name)
        self.em.comment("Areas (rooms), placed under their floors")
        for order, area in enumerate(self.sched.areas):
            area_id = self.ids.take()
            parent_id = self.shell.floors[area.parent]

            # The groups' ParentID is NOT the room -- Designer parents all of
            # them to the processor system (object 43 in the reference, every
            # one of 16+16). The room points at its group via
            # DaylightingGroupAssignedToID below; the template's ParentID is
            # kept as cloned.
            dl_id = ol_id = None
            if self.shell.daylighting_group_id:
                dl_id = self.ids.take()
                self.em.clone("tblDaylightingGroup",
                              f"DaylightingGroupID = {self.shell.daylighting_group_id}",
                              self._stamp("tblDaylightingGroup", {
                                  "DaylightingGroupID": dl_id,
                                  "Name": f"DaylightingGroup {area_id}"}))
            if self.shell.occupancy_group_id:
                ol_id = self.ids.take()
                self.em.clone("tblOccupancyGroup",
                              f"OccupancyGroupID = {self.shell.occupancy_group_id}",
                              self._stamp("tblOccupancyGroup", {
                                  "OccupancyGroupID": ol_id,
                                  "Name": f"Default{ol_id}"}))

            self.em.clone("tblArea", f"AreaID = {self.shell.room_area_id}",
                          self._stamp("tblArea", {
                              "AreaID": area_id,
                              "ParentID": parent_id,
                              "Name": area.name,
                              "HierarchyLevel": 4,
                              "IsLeaf": 1,
                              "ParentType": PT_AREA,
                              "SortOrder": order,
                              "DaylightingGroupAssignedToID": dl_id,
                              "OccupancyGroupAssignedToID": ol_id,
                              "UserUniqueID": ""}))
            # Designer gives every area two mode rows and a daylighting
            # set-point assignment. They are not optional: a room without them
            # restores fine and then fails validation when the file is opened.
            for template_id in self.shell.area_mode_ids:
                mode_id = self.ids.take()
                self.em.clone("tblAreaMode", f"AreaModeID = {template_id}",
                              self._stamp("tblAreaMode", {
                                  "AreaModeID": mode_id,
                                  "ParentAreaID": area_id}))
            if self.shell.dl_setpoint_id:
                dl_id2 = self.ids.take()
                self.em.clone("tblDLSetPointLevelAssignment",
                              f"DLSetPointLevelAssignmentID = {self.shell.dl_setpoint_id}",
                              self._stamp("tblDLSetPointLevelAssignment", {
                                  "DLSetPointLevelAssignmentID": dl_id2,
                                  "ParentId": area_id}))
                self._map(dl_id2)

            # Keyed by uid, not name: a house may have a WC on three floors,
            # and every other file refers to it by the key the schedule
            # resolved. `Name` above is still the plain room name Designer shows.
            self.r.area_ids[area.uid] = area_id
            self._map(area_id)
            self._integration(area_id, INTEG_AREA)
            if dl_id:
                self._map(dl_id)
            if ol_id:
                self._map(ol_id)

    # ---- fixtures --------------------------------------------------------

    def _fixtures(self) -> None:
        if not self.sched.fixtures:
            return
        self.em.comment("Fixture catalogue -- only the types this project actually uses")
        for order, fx in enumerate(self.sched.fixtures):
            fid = self.ids.take()
            self.em.clone("tblFixture", f"FixtureID = {self.shell.fixture_id}",
                          self._stamp("tblFixture", {
                              "FixtureID": fid,
                              "Name": fx.ref,
                              "FixtureDescription": fx.description,
                              "LoadType": fx.load_type_id,
                              # Designer stores this as int (confirmed against the real database
                              # 2026-08-06). SQL would TRUNCATE 9.6 to 9 on its own;
                              # round instead, so a 9.6 W/m tape lands at 10 not 9, and
                              # the review sheet says where that happens.
                              #
                              # A fitting sold by the metre carries its PER-METRE
                              # figure here, and its circuits carry metres where a
                              # counted fitting carries a count. Designer has no
                              # concept of a run length, so "N at X watts each" is
                              # the only shape available -- and it is the same shape
                              # an engineer writes on a schedule: 10 m at 9.6 W/m.
                              "FixtureWattage": half_up(fx.watts_per_metre or fx.wattage),
                              "PhaseControl": fx.phase_control,
                              "ManufacturerName": fx.manufacturer_name,
                              "ManufacturerModel": fx.manufacturer_model,
                              "SortOrder": order}))
            self.em.clone("tblFixtureLighting", f"FixtureID = {self.shell.fixture_id}",
                          self._stamp("tblFixtureLighting", {
                              "FixtureID": fid,
                              "LampQuantity": fx.lamp_quantity,
                              "LampWattage": half_up(fx.lamp_wattage),
                              "DimmingRange": fx.dimming_range,
                              "LowEnd": fx.low_end,
                              "HighEnd": fx.high_end}))
            self.r.fixture_ids[fx.ref] = fid
            self._map(fid, all_processors=True)

    # ---- modules ---------------------------------------------------------

    def _modules(self) -> None:
        if not self.sched.modules:
            return
        self.em.comment("Modules -- enclosure + device + link address + one channel per output")
        for order, mod in enumerate(self.sched.modules):
            bp = self.shell.modules[mod.model_info_id]
            enc_id = self.ids.take()
            dev_id = self.ids.take()

            self.em.clone("tblEnclosure", f"EnclosureID = {bp.template_enclosure_id}",
                          self._stamp("tblEnclosure", {
                              "EnclosureID": enc_id,
                              "Name": mod.name,
                              "ParentAreaID": self.r.area_ids[mod.area],
                              "ModelInfoID": mod.model_info_id,
                              "SortOrder": order}))
            self.em.clone("tblEnclosureDevice", f"EnclosureDeviceID = {bp.template_device_id}",
                          self._stamp("tblEnclosureDevice", {
                              "EnclosureDeviceID": dev_id,
                              "Name": f"Default_{dev_id}",
                              "ParentEnclosureID": enc_id,
                              "ModelInfoID": mod.model_info_id,
                              # See tblControlStationDevice below: "0", not NULL.
                              "SerialNumber": mod.serial or "0",
                              "SerialNumberState": 2 if mod.serial else 0,
                              "OrderOnCommunicationLink":
                                  next_link_order(self.shell, self.shell.links[mod.link][0])
                                  if mod.link else 0,
                              "SortOrder": 0}))
            # Every enclosure in Designer's own data has a tblPowerPanel row,
            # including plain dimmer modules. The primary key is the enclosure
            # id, so there is nothing to allocate.
            if self.shell.power_panel_enclosure_id:
                self.em.clone("tblPowerPanel",
                              f"EnclosureID = {self.shell.power_panel_enclosure_id}",
                              {"EnclosureID": enc_id})
            self._map(enc_id)
            self._map(dev_id)
            # 18 of 18 enclosure devices in the reference carry a display name;
            # for a single-device enclosure it is simply the enclosure's name.
            self.em.insert("tblDeviceDisplayNameMap", {
                "DomainObjectID": dev_id,
                "DisplayName": mod.name})
            # See the keypad note: per-model capacity limits, cloned; inserts
            # nothing when Designer itself writes nothing for this model.
            self.em.clone("tblDeviceComponentMap",
                          f"DomainObjectID = {bp.template_device_id}",
                          {"DomainObjectID": dev_id})

            if mod.link:
                link_id, _info = self.shell.links[mod.link]
                addr = mod.address if mod.address is not None else next_free_address(self.shell, link_id)
                self.shell.link_addresses.setdefault(link_id, set()).add(addr)
                node_id = self.ids.take()
                self.em.clone("tblLinkNode", f"LinkNodeID = {self.shell.module_link_node_id}",
                              self._stamp("tblLinkNode", {
                                  "LinkNodeID": node_id,
                                  "Name": f"Default{node_id}",
                                  "AddressOnLink": addr,
                                  "LinkAssignedToID": link_id,
                                  "ModelInfoID": mod.model_info_id,
                                  "LinkType": bp.link_type,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_ENCLOSURE_DEVICE,
                                  "SortOrder": 0}))
                self._map(node_id)
                self._tlink_node(link_id, dev_id, PT_ENCLOSURE_DEVICE)

            count = mod.output_count or bp.output_count
            for out_no in range(1, count + 1):
                slc_id = self.ids.take()
                self.em.clone("tblSwitchLegController",
                              f"SwitchLegControllerID = {self.shell.switchleg_controller_id}",
                              self._stamp("tblSwitchLegController", {
                                  "SwitchLegControllerID": slc_id,
                                  "Name": f"Default{slc_id}",
                                  "OutputNumber": out_no,
                                  "SortOrder": out_no - 1,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_ENCLOSURE_DEVICE}))
                self.r.module_outputs[(mod.name, out_no)] = slc_id
                self._map(slc_id)

    # ---- loads -----------------------------------------------------------

    def _loads(self) -> None:
        if not self.sched.loads:
            return
        self.em.comment("Loads -- zone + switch leg + fixture assignment, bridged by tblZonable")
        wiring = {(o.area, o.zone): (o.module, o.output) for o in self.sched.outputs}
        per_area_order: dict[str, int] = {}

        for load in self.sched.loads:
            # A circuit with no fixture type cannot be built -- there is nothing
            # to put on it. It is NOT a reason to refuse the house: an honest
            # read of a preliminary set leaves the fitting blank wherever the
            # drawings do not say, and the other 190 circuits are perfectly
            # good. Skipped here, reported on the review sheet and as a warning,
            # and it builds as soon as someone fills the ref in.
            if load.unresolved:
                continue

            # Designer numbers zones from 1 within each area, not project-wide.
            number = (load.zone_number if load.zone_number is not None
                      else per_area_order.get(load.area, 0) + 1)
            order = per_area_order.get(load.area, 0)
            per_area_order[load.area] = order + 1

            area_id = self.r.area_ids[load.area]
            fixture = next(f for f in self.sched.fixtures if f.ref == load.fixture_ref)
            zone_id = self.ids.take()
            sl_id = self.ids.take()
            fa_id = self.ids.take()

            self.em.clone("tblZone", f"ZoneID = {self.shell.zone_id}",
                          self._stamp("tblZone", {
                              "ZoneID": zone_id,
                              "ParentID": area_id,
                              "Name": load.zone,
                              "ZoneNumber": number,
                              "SortOrder": order,
                              # The colour Designer draws this circuit in. Cosmetic,
                              # but never NULL in Designer's own data -- it runs
                              # 1..19 within a room and wraps. Leaving it NULL is
                              # the same class of defect as a NULL SerialNumber.
                              "ZoneColorInfo": (order % 19) + 1}))
            self.em.clone("tblSwitchLeg", f"SwitchLegID = {self.shell.switchleg_id}",
                          self._stamp("tblSwitchLeg", {
                              "SwitchLegID": sl_id,
                              "ParentID": area_id,
                              "Name": f"{order + 1:03d}",
                              "SortOrder": order,
                              "OutputNumberOnLink": order,
                              # `or` would discard an explicit override of 0 --
                              # LoadTypeID 0 is "Default" in Lutron's own list.
                              "LoadType": load.load_type_override
                                          if load.load_type_override is not None
                                          else fixture.load_type_id,
                              "LowEnd": load.low_end_override
                                        if load.low_end_override is not None else fixture.low_end,
                              "HighEnd": load.high_end_override
                                         if load.high_end_override is not None else fixture.high_end}))
            # Every switch leg carries a tblDaylightable row keyed by its own
            # ID. Designer reads it unconditionally in the second pass of the
            # object load, so a missing one is a NullReferenceException inside
            # SwitchLeg.InitializeFromDatabase_Step2 -- which surfaces as the
            # import sitting at 100% forever rather than as any error. The table
            # has no revision or Xid columns, so there is nothing to stamp.
            if self.shell.switchleg_daylightable_id:
                self.em.clone("tblDaylightable",
                              f"DaylightableID = {self.shell.switchleg_daylightable_id}",
                              {"DaylightableID": sl_id})
            self.em.clone("tblFixtureAssignment",
                          f"FixtureAssignmentID = {self.shell.fixture_assignment_id}",
                          self._stamp("tblFixtureAssignment", {
                              "FixtureAssignmentID": fa_id,
                              "ParentID": sl_id,
                              "ParentType": PT_SWITCHLEG,
                              "Name": f"Default{fa_id}",
                              "FixtureID": self.r.fixture_ids[load.fixture_ref],
                              # Metres for linear product, a count for everything
                              # else -- paired with the per-metre wattage written on
                              # the fixture above, so the connected load Designer
                              # works out matches the review sheet. Without this the
                              # sheet said 99.84 W and the file said one fitting of
                              # nothing at all.
                              "NumberofFixtures": _quantity(fixture, load),
                              "SortOrder": 0}))

            controller = None
            wired = wiring.get((load.area, load.zone))
            if wired:
                controller = self.r.module_outputs.get(wired)
                if controller is None:
                    raise SqlError(
                        f"'{load.area} / {load.zone}' is wired to {wired[0]} output {wired[1]}, "
                        f"but that output was never created.")
            # An unwired load keeps ControllerType 3 with a NULL ControllerID --
            # that exact pair is Designer's own off-link state, proven to open
            # and hand-attach cleanly (unattached-equipment experiment,
            # 2026-08-02). ControllerType NULL is unobserved territory.
            self.em.insert("tblZonable", {
                "ZonableID": sl_id,
                "ZonableObjectType": PT_SWITCHLEG,
                "AssociatedZoneID": zone_id,
                "ControllerID": controller,
                "ControllerType": CONTROLLER_SWITCHLEG})

            self.r.zone_ids[(load.area, load.zone)] = zone_id
            for oid in (zone_id, sl_id, fa_id):
                self._map(oid)
            self._integration(zone_id, INTEG_ZONE)

    # ---- scenes ----------------------------------------------------------

    def _scenes(self) -> None:
        if not self.sched.scenes:
            return
        self.em.comment("Scenes -- one controller per area, then per-zone level recipes")
        controllers: dict[str, int] = {}

        for area_name in dict.fromkeys(sc.area for sc in self.sched.scenes):
            sc_id = self.ids.take()
            self.em.clone("tblSceneController",
                          f"SceneControllerID = {self.shell.scene_controller_id}",
                          self._stamp("tblSceneController", {
                              "SceneControllerID": sc_id,
                              "Name": f"Default{sc_id}",
                              "ParentID": self.r.area_ids[area_name],
                              "ParentType": PT_AREA,
                              "SortOrder": 0}))
            controllers[area_name] = sc_id
            self._map(sc_id)

        for scene in self.sched.scenes:
            scene_id = self.ids.take()
            self.em.clone("tblScene", f"SceneID = {self.shell.scene_id}",
                          self._stamp("tblScene", {
                              "SceneID": scene_id,
                              "Name": scene.name,
                              "Number": scene.number,
                              "SortOrder": scene.number,
                              "ParentSceneControllerID": controllers[scene.area]}))
            self.r.scene_numbers[(scene.area, scene.name)] = scene.number
            self._map(scene_id)

            for order, lv in enumerate(scene.levels):
                # The circuit this level sets was skipped for having no fixture,
                # so there is no zone to point at. Writing the preset anyway
                # would leave a preset assignment aimed at nothing.
                if (scene.area, lv.zone) not in self.r.zone_ids:
                    continue
                pa_id = self.ids.take()
                unaffected = lv.command.lower() == "unaffected" or lv.level is None
                cmd_type, cmd_group = CMD_UNAFFECTED if unaffected else CMD_SET_LEVEL
                self.em.clone("tblPresetAssignment",
                              f"PresetAssignmentID = {self.shell.scene_preset_assignment_id}",
                              self._stamp("tblPresetAssignment", {
                                  "PresetAssignmentID": pa_id,
                                  "ParentID": scene_id,
                                  "ParentType": PT_SCENE,
                                  "AssignableObjectID": self.r.zone_ids[(scene.area, lv.zone)],
                                  "AssignableObjectType": OBJ_ZONE,
                                  "AssignmentCommandType": cmd_type,
                                  "AssignmentCommandGroup": cmd_group,
                                  "SortOrder": order}))
                if not unaffected:
                    self._params(pa_id, [(PARAM_FADE, fade_raw(lv.fade)),
                                         (PARAM_DELAY, lv.delay),
                                         (PARAM_LEVEL, lv.level)])
                self._map(pa_id)

    # ---- keypads ---------------------------------------------------------

    def _keypads(self) -> None:
        if not self.sched.keypads:
            return
        self.em.comment("Keypads -- station, device, link address, LEDs, then per-button programming")
        buttons_by_keypad: dict[str, list] = {}
        for b in self.sched.buttons:
            buttons_by_keypad.setdefault(b.keypad, []).append(b)

        device_ids: dict[str, int] = {}

        for order, kp in enumerate(self.sched.keypads):
            bp = self.shell.keypads[kp.model_info_id]
            station_id = self.ids.take()
            dev_id = self.ids.take()

            self.em.clone("tblControlStation", f"ControlStationID = {bp.template_station_id}",
                          self._stamp("tblControlStation", {
                              "ControlStationID": station_id,
                              "ParentId": self.r.area_ids[kp.area],
                              "ParentType": PT_AREA,
                              "Name": kp.station or kp.name,
                              "SortOrder": order}))
            self.em.clone("tblControlStationDevice",
                          f"ControlStationDeviceID = {bp.template_device_id}",
                          self._stamp("tblControlStationDevice", {
                              "ControlStationDeviceID": dev_id,
                              "Name": kp.device,
                              "ParentControlStationID": station_id,
                              "ModelInfoID": kp.model_info_id,
                              # "Not identified yet" is the string "0" with state
                              # 0, never NULL: Designer reads this column with
                              # GetNotNullString and a NULL aborts the project
                              # load outright (it drops back to the project list
                              # with no error shown). Every device in Designer's
                              # own file is either "0"/state 0 or a real
                              # serial/state 2.
                              "SerialNumber": kp.serial or "0",
                              "SerialNumberState": 2 if kp.serial else 0,
                              "OrderOnCommunicationLink":
                                  next_link_order(self.shell, self.shell.links[kp.link][0])
                                  if kp.link else 0,
                              "SortOrder": 0}))
            device_ids[kp.name] = dev_id
            if kp.model_label == "PALLADIOM-2B":
                # AVS FIX (2026-09-03): CustomButtonKitModelNumber is never set by
                # any hwwriter code path -- it's silently inherited verbatim from
                # whatever template device this model clones from (emit.py's
                # clone()). For PALLADIOM-2B, Starter Shell.hw's own bundled example
                # device has its two real buttons wired to bulk-unit positions 0 and
                # 3 (confirmed via SQL and via shell.py's _read_keypad_blueprints(),
                # which reads bp.buttons straight from that one template device's
                # tblKeypadButton rows) -- not positions 0 and 1. Per Lutron's own
                # Button Kit spec (p/n 369881n): a kit's molded slots must physically
                # match the wired switch positions on the bulk unit, not just the
                # button count -- a "2-Button" kit's slots are cut for adjacent
                # positions 1-2 and won't line up with a switch actually sitting at
                # position 4. Jeran confirmed (2026-09-03) this matches the real
                # hardware wiring on these keypads, so PBT-4W -- not the inherited
                # PBT-2W -- is the physically correct kit for this model as this
                # shell defines it.
                self.em.raw(
                    "UPDATE tblControlStationDevice "
                    "SET CustomButtonKitModelNumber = 'PBT-4W' "
                    f"WHERE ControlStationDeviceID = {dev_id};"
                )
            self._map(station_id)
            self._map(dev_id)
            self._integration(dev_id, INTEG_KEYPAD_DEVICE)
            # A keypad device's display name is its station's name -- what the
            # user reads in transfer reports and integration lists.
            self.em.insert("tblDeviceDisplayNameMap", {
                "DomainObjectID": dev_id,
                "DisplayName": kp.station or kp.name})
            # Per-model capacity limits. Cloned rather than computed because the
            # values are catalogue facts; when the template model has no row
            # (Designer skips some models), the clone inserts nothing, which
            # matches Designer's own behaviour for that model.
            self.em.clone("tblDeviceComponentMap",
                          f"DomainObjectID = {bp.template_device_id}",
                          {"DomainObjectID": dev_id})

            # A keypad device with no button group fails Designer's own
            # integrity check ("ButtonGroupMissing") and the load stalls. The
            # group's catalogue references vary by model, and some models carry
            # two groups, so clone every group the template device has.
            for group_order, template_group in enumerate(bp.button_group_ids):
                bg_id = self.ids.take()
                self.em.clone("tblButtonGroup", f"ButtonGroupID = {template_group}",
                              self._stamp("tblButtonGroup", {
                                  "ButtonGroupID": bg_id,
                                  "Name": f"Button Group {group_order + 1:03d}",
                                  "SortOrder": group_order,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE}))
                self._map(bg_id)

            # Engraving styles: one on the station for the faceplate, one on the
            # device for the buttons. The device row's font varies by model, so
            # both are cloned rather than written from constants. Designer maps
            # neither to a processor -- 0 of 32 in the reference file.
            if bp.station_engraving_style_id:
                self.em.clone("tblEngravingStyle",
                              f"EngravingStyleID = {bp.station_engraving_style_id}",
                              self._stamp("tblEngravingStyle", {
                                  "EngravingStyleID": self.ids.take(),
                                  "ParentID": station_id,
                                  "ParentDeviceType": PT_CONTROL_STATION}))
            if bp.device_engraving_style_id:
                self.em.clone("tblEngravingStyle",
                              f"EngravingStyleID = {bp.device_engraving_style_id}",
                              self._stamp("tblEngravingStyle", {
                                  "EngravingStyleID": self.ids.take(),
                                  "ParentID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE}))

            # Per-model: some keypad models carry a controller object, some do
            # not. Clone from this model's own template so each keypad gets
            # exactly what Designer would give it.
            if bp.keypad_controller_id:
                kc_id = self.ids.take()
                self.em.clone("tblKeypadController",
                              f"KeypadControllerID = {bp.keypad_controller_id}",
                              self._stamp("tblKeypadController", {
                                  "KeypadControllerID": kc_id,
                                  "Name": f"KeypadController {kc_id}",
                                  "ModelInfoID": kp.model_info_id,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE,
                                  "SortOrder": 0}))
                self._map(kc_id)

            # Every keypad device in the reference file carries a tblLinkNode
            # row -- attached OR not. Designer's own off-link state is that row
            # with LinkAssignedToID and AddressOnLink NULL (proven 2026-08-02),
            # so a keypad with no LinkName still gets its node, unattached.
            link_template = self.shell.keypad_link_node_id or self.shell.module_link_node_id
            if kp.link:
                link_id, _info = self.shell.links[kp.link]
                addr = kp.address if kp.address is not None else next_free_address(self.shell, link_id)
                self.shell.link_addresses.setdefault(link_id, set()).add(addr)
                node_id = self.ids.take()
                self.em.clone("tblLinkNode", f"LinkNodeID = {link_template}",
                              self._stamp("tblLinkNode", {
                                  "LinkNodeID": node_id,
                                  "Name": f"Default{node_id}",
                                  "AddressOnLink": addr,
                                  "LinkAssignedToID": link_id,
                                  "ModelInfoID": kp.model_info_id,
                                  "LinkType": self.shell.links[kp.link][1],
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE,
                                  "SortOrder": 0}))
                self._map(node_id)
                self._tlink_node(link_id, dev_id, PT_KEYPAD_DEVICE)
            elif link_template:
                node_id = self.ids.take()
                # LinkType stays whatever the cloned template row carries.
                self.em.clone("tblLinkNode", f"LinkNodeID = {link_template}",
                              self._stamp("tblLinkNode", {
                                  "LinkNodeID": node_id,
                                  "Name": f"Default{node_id}",
                                  "AddressOnLink": None,
                                  "LinkAssignedToID": None,
                                  "ModelInfoID": kp.model_info_id,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE,
                                  "SortOrder": 0}))
                self._map(node_id)

            for led in bp.leds:
                if not self.shell.led_id:
                    break
                led_id = self.ids.take()
                self.em.clone("tblLed", f"LedID = {self.shell.led_id}",
                              self._stamp("tblLed", {
                                  "LedID": led_id,
                                  "Name": f"Led {led.sort_order + 1}",
                                  "LedNumber": led.number,
                                  "LedInfoId": led.info_id,
                                  "LedNumberOnLink": self._take_led_number(),
                                  "SortOrder": led.sort_order,
                                  "ParentDeviceID": dev_id,
                                  "ParentDeviceType": PT_KEYPAD_DEVICE}))
                self._map(led_id)

            self._buttons(kp, bp, dev_id, buttons_by_keypad.get(kp.name, []))

    def _take_led_number(self) -> int:
        n = self._led_link_number
        self._led_link_number += 1
        return n

    def _buttons(self, kp, bp, dev_id: int, buttons: list) -> None:
        by_index = {b.number: b for b in buttons}
        engraved = bp.engraved_buttons

        for slot in bp.buttons:
            btn_id = self.ids.take()
            pm_id = self.ids.take()

            index = engraved.index(slot) + 1 if slot in engraved else None
            spec = by_index.get(index) if index else None

            self.em.clone("tblKeypadButton",
                          f"ButtonID = (SELECT TOP 1 ButtonID FROM tblKeypadButton "
                          f"WHERE ParentDeviceID = {bp.template_device_id} "
                          f"AND ButtonNumber = {slot.number})",
                          self._stamp("tblKeypadButton", {
                              "ButtonID": btn_id,
                              "Name": f"Button {slot.number}",
                              "ButtonNumber": slot.number,
                              "SortOrder": slot.sort_order,
                              "ProgrammingModelID": pm_id,
                              "ParentDeviceID": dev_id,
                              "ParentDeviceType": PT_KEYPAD_DEVICE}))
            self._map(btn_id)

            press_id = self.ids.take()
            self.em.clone("tblProgrammingModel",
                          f"ProgrammingModelID = {self.shell.programming_model_id}",
                          self._stamp("tblProgrammingModel", {
                              "ProgrammingModelID": pm_id,
                              "Name": f"Default{pm_id}",
                              "ParentID": btn_id,
                              "ParentType": PT_BUTTON,
                              "SortOrder": 0,
                              # AVS FIX (2026-09-02): force Designer's
                              # single-action ObjectType (60) explicitly
                              # instead of leaving it to whatever button
                              # got cloned as the template. Starter
                              # Shell.hw has zero ObjectType=60 example
                              # buttons -- every example there is a
                              # Toggle (ObjectType=74, uses OnPresetID/
                              # OffPresetID) -- so every cloned row was
                              # inheriting ObjectType=74 while this code
                              # writes single-action data into PresetID
                              # only. Designer reads ObjectType to know
                              # which preset field to look at; with
                              # ObjectType=74 it looked for OnPresetID/
                              # OffPresetID (both None here) and showed
                              # the button as having no programming at
                              # all, confirmed directly in Designer even
                              # though link/orphan integrity checks
                              # (tblProgrammingModel/tblPreset/
                              # tblPresetAssignment) all passed clean.
                              "ObjectType": 60,
                              # A single-action button keeps its press preset in
                              # PresetID -- all 69 of Designer's type-60 models
                              # do, and none uses PressPresetID. Putting it in
                              # PressPresetID instead trips Designer's
                              # "SingleActionProgrammingModelMissingPreset"
                              # integrity warning on every open.
                              "PresetID": press_id,
                              "DoubleTapPresetID": None,
                              "HoldPresetId": None,
                              "PressPresetID": None,
                              "ReleasePresetID": None,
                              "OnPresetID": None,
                              "OffPresetID": None,
                              "ReferencePresetIDForLed": None}))
            self.em.clone("tblPreset", f"PresetID = {self.shell.preset_id}",
                          self._stamp("tblPreset", {
                              "PresetID": press_id,
                              "Name": "Press On",
                              "ParentID": pm_id,
                              "SortOrder": 0}))
            self._map(pm_id)
            self._map(press_id)

            if spec is None:
                continue

            if self.shell.engraving_id:
                ep_id = self.ids.take()
                self.em.clone("tblEngravingPosition",
                              f"EngravingPositionID = {self.shell.engraving_id}",
                              self._stamp("tblEngravingPosition", {
                                  "EngravingPositionID": ep_id,
                                  "Name": "EP 001",
                                  "Text": spec.label,
                                  "ParentDeviceID": btn_id,
                                  "ParentDeviceType": PT_BUTTON,
                                  "SortOrder": 0}))
                self._map(ep_id)

            for order, act in enumerate(spec.actions):
                pa_id = self.ids.take()
                if act.action_type == "RecallAreaScene":
                    target = self.r.area_ids[act.target_area]
                    obj_type = OBJ_AREA
                    cmd_type, cmd_group = CMD_RECALL_SCENE
                    params = [(PARAM_DELAY, act.delay),
                              (PARAM_SCENE_NUMBER,
                               self.r.scene_numbers[(act.target_area, act.target_scene)])]
                else:  # DirectZoneLevel
                    if (act.target_area, act.target_zone) not in self.r.zone_ids:
                        continue        # the circuit had no fixture and was skipped
                    target = self.r.zone_ids[(act.target_area, act.target_zone)]
                    obj_type = OBJ_ZONE
                    cmd_type, cmd_group = CMD_SET_LEVEL
                    params = [(PARAM_DELAY, act.delay),
                              (PARAM_FADE, fade_raw(act.fade)),
                              (PARAM_LEVEL, act.level)]

                template = (self.shell.button_preset_assignment_id
                            or self.shell.scene_preset_assignment_id)
                self.em.clone("tblPresetAssignment", f"PresetAssignmentID = {template}",
                              self._stamp("tblPresetAssignment", {
                                  "PresetAssignmentID": pa_id,
                                  "ParentID": press_id,
                                  "ParentType": PT_BUTTON_PRESET,
                                  "AssignableObjectID": target,
                                  "AssignableObjectType": obj_type,
                                  "AssignmentCommandType": cmd_type,
                                  "AssignmentCommandGroup": cmd_group,
                                  "SortOrder": order}))
                self._params(pa_id, params)
                self._map(pa_id)
