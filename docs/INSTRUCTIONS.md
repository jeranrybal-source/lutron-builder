# Lutron HomeWorks Engineering Pipeline — CSV Contract

This package is what your engineering tool ingests to fully program a Lutron HomeWorks project from CAD-derived data. It covers all six domains (areas, fixtures, loads, modules, output assignments, keypads, scenes, button programming) so the only thing that needs doing in Designer is:

1. Create a new project shell (project name, processor IP, area-tree skeleton)
2. Run the engineering tool with the CSVs below
3. Open the resulting `.hw` to verify
4. Transfer to processor

---

## 1. The CSV pipeline at a glance

```
                                    ┌─────────────────────┐
                                    │ Engineering tool    │
                                    │  (your CAD system)  │
                                    └──────────┬──────────┘
                                               │ CSV exports
                ┌──────────────────────────────┼──────────────────────────────┐
                │                              │                              │
   1. FixturesCatalog.csv          3. Modules.csv                  5. Keypads.csv
   2. LoadSchedule.csv             4. OutputAssignments.csv        6. Buttons.csv
                                                                   7. Scenes.csv
                                               │
                                               ▼
                           ┌───────────────────────────────────────┐
                           │ Import script                         │
                           │  - restores latest .hw to LocalDB     │
                           │  - validates CSV cross-references     │
                           │  - emits new domain rows via the same │
                           │    stored procs Designer uses         │
                           │  - re-zips .lut into a new .hw        │
                           └─────────────────┬─────────────────────┘
                                             │
                                             ▼
                                  New `.hw` file → open in
                                  Designer → transfer to processor
```

The CSVs use **human-readable string keys** so the data is reviewable as ordinary engineering schedules:

| File | Joins to | Via |
|---|---|---|
| FixturesCatalog | LoadSchedule | `FixtureRef` |
| LoadSchedule | OutputAssignments | `AreaName` + `ZoneName` |
| Modules | OutputAssignments | `ModuleName` |
| Keypads | Buttons | `KeypadName` |
| Buttons | Scenes | `TargetArea` + `TargetScene` (when ActionType = RecallAreaScene) |
| Scenes | LoadSchedule | `AreaName` + `ZoneName` |

Misspelled or missing join keys make the import fail loudly for that row; nothing is silently dropped.

Every CSV is pre-populated with this project's existing data marked `Action = EXISTING`. Use those rows as worked examples; **do not modify them**. Add new rows at the bottom marked `Action = NEW`.

---

## 2. Domain reference

### 2.1 Areas

Areas are Designer's room hierarchy. New `AreaName` values appear in `LoadSchedule`, `Modules`, `Keypads`, and `Scenes` — the import creates them on demand. Existing top-level structure in this project: `Your Project > Ground Floor | First Floor | External`. New areas land under their floor (the engineering tool can override this by passing a `ParentArea` column if needed).

### 2.2 Fixtures (`FixturesCatalog_TEMPLATE.csv`)

The catalogue of fixture *types* — one row per "A", "B", "C" you'd see on a lighting schedule.

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| FixtureRef | yes | Short reference used by `LoadSchedule` |
| Description | yes | Free text — "Indox Square Single", "5A Socket" |
| LoadTypeID | yes | Numeric — see `LoadTypes_REFERENCE.csv` |
| LoadTypeName | no | Just for readability |
| FixtureWattage_W | yes | Watts of one fixture |
| LampQuantity | yes | Lamps per fixture |
| LampWattage_W | yes | Per single lamp |
| DimmingRange | no | Leave 0 for default |
| LowEnd_pct | yes | Low-trim % (5 for LED, 1 for DMX) |
| HighEnd_pct | yes | High-trim % (90 for LED, 99 for DMX) |
| PhaseControl | yes | 0 = none, 1 = trailing-edge LED |
| ManufacturerName | no | Optional |
| ManufacturerModel | no | Optional |
| Notes | no | Optional |

### 2.3 Loads (`LoadSchedule_TEMPLATE.csv`)

The schedule of switched/dimmed circuits in each room. One row per zone.

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| AreaName | yes | Exact area name |
| ZoneName | yes | Circuit name, e.g. "Door/Wall Spots" |
| ZoneNumber | no | Auto-assign if blank |
| FixtureRef | yes | Must exist in `FixturesCatalog` |
| NumberOfFixtures | yes | Count of fixtures on this circuit |
| LoadTypeID_Override | no | Inherit from fixture if blank |
| LowEnd_pct_Override | no | Inherit if blank |
| HighEnd_pct_Override | no | Inherit if blank |
| Notes | no | Optional |

### 2.4 Modules (`Modules_TEMPLATE.csv`)

Physical hardware modules in panels (dimmers, DALI, DMX, ballasts, shade controllers).

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| ModuleName | yes | Globally unique label, e.g. "DPM Adaptive 006" |
| ModuleType | yes | Must match a row in `ModuleTypes_REFERENCE.csv` |
| AreaName | yes | Where the module physically lives |
| LinkName | yes | Comm link (e.g. `Default3890` = main HW link) |
| AddressOnLink | yes | Numeric, must be unique per link |
| OutputCount | no | Defaults from ModuleType |
| SerialNumber | no | Filled at commissioning |
| Notes | no | Optional |

### 2.5 Output assignments (`OutputAssignments_TEMPLATE.csv`)

Wiring map: which load lands on which terminal of which module.

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| AreaName | yes | Must match a row in `LoadSchedule` |
| ZoneName | yes | Must match a row in `LoadSchedule` |
| ModuleName | yes | Must match a row in `Modules` |
| OutputNumber | yes | 1-based output number on the module |
| Notes | no | Optional |

### 2.6 Keypads (`Keypads_TEMPLATE.csv`)

Each physical control station (or virtual phantom keypad).

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| KeypadName | yes | Globally unique label, format `AreaName > StationName` |
| AreaName | yes | Room the keypad lives in |
| StationName | yes | Station label on the wall (e.g. "By Door") |
| DeviceName | yes | Per-gang device label (usually "Device 1") |
| KeypadModel | yes | Must match a row in `KeypadModels_REFERENCE.csv` |
| LinkName | yes | Comm link the keypad is on (blank for Phantom Keypads) |
| AddressOnLink | yes | Numeric, blank for Phantom |
| ButtonCount | no | Defaults from KeypadModel |
| SerialNumber | no | Filled at commissioning |
| Notes | no | Optional — note Phantom Keypads are software-only and used for Control4 / touchpanel integration |

### 2.7 Buttons (`Buttons_TEMPLATE.csv`)

Per-button programming. The dominant pattern is `RecallAreaScene` (a labelled button recalls a named scene in an area).

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| KeypadName | yes | Must match a row in `Keypads` |
| ButtonNumber | yes | 1-based button index on the keypad |
| ButtonLabel | yes | The engraving text — "Bright", "Off", "Movie" |
| ActionType | yes | `RecallAreaScene` (most common) or `DirectZoneLevel` — the only two this writer builds. Anything else (`MasterRaiseLower`, `Toggle`, `Macro`, …) is refused at build time. |
| TargetArea | conditional | Required for `RecallAreaScene` — can be repeated across multiple rows for one button to recall scenes in multiple areas at once |
| TargetScene | conditional | Required for `RecallAreaScene` — must match a `SceneName` for that area |
| TargetZone | conditional | Required for `DirectZoneLevel` — must match a `ZoneName` |
| Level_pct | conditional | Required for `DirectZoneLevel` — 0-100 |
| Fade_seconds | no | Default 2s |
| Delay_seconds | no | Default 0s |
| Notes | no | Optional |

**Multi-area buttons** (e.g. "Goodnight" on the Front Door keypad recalling Night in Entrance Hall + Off in Family Room + Night in Kitchen + Night in Living/Dining) are expressed as multiple rows with the same `KeypadName + ButtonNumber + ButtonLabel`, each with a different `TargetArea + TargetScene`.

### 2.8 Scenes (`Scenes_TEMPLATE.csv`)

Per-area scene definitions. One row per (area, scene, zone) tuple — the actual brightness recipe.

| Column | Required | Notes |
|---|---|---|
| Action | yes | NEW for new rows |
| AreaName | yes | Existing or new area |
| SceneName | yes | "Bright", "Relax", "Night", "Off Scene", "Movie", etc. |
| SceneNumber | yes | 0–30. By convention: 0 = Off Scene, 1 = Bright, 2 = Relax, 3 = Night, 4-N = custom, 30 = Close (shades) |
| ZoneName | yes | Must match a `ZoneName` in `LoadSchedule` |
| CommandType | yes | `SetLevel` or `ZoneLevel` (a standard zone — both accepted, they mean the same thing), or `Unaffected` (leave this zone alone in this scene). Obvious synonyms for a brightness (`Level`, `GotoLevel`) are also accepted and noted in the warnings, because they unambiguously mean the same thing — but the spelling is not a free-for-all, and anything else is still refused. **`DMXColor` and `Spectrum` are NOT supported by this writer** and are refused: it builds every command except `Unaffected` as an ordinary brightness, so a colour preset index would be written as a percentage. Anything else is refused too — a misspelt `Unaffected` would otherwise switch a circuit the scene meant to leave alone. |
| Level_pct | conditional | 0-100. For Unaffected use the literal `unaffected` |
| Fade_seconds | no | Default 2s |
| Delay_seconds | no | Default 0s |
| Notes | no | Optional — flag rows needing manual review |

The CAD engineer can leave entire scenes blank if they want them programmed at commissioning instead.

---

## 3. Reference files

| File | Purpose |
|---|---|
| `LoadTypes_REFERENCE.csv` | Every Lutron LoadTypeID with description (117 rows) |
| `ModuleTypes_REFERENCE.csv` | Friendly module-type names ↔ ModelInfoIDs known to this project |
| `KeypadModels_REFERENCE.csv` | Friendly keypad model names ↔ ModelInfoIDs known to this project. `Placeable=yes` means the Starter Shell carries an example and the model can be built; `Placeable=no` rows are documented for reference only and are hidden from the AI. `shells/Starter Shell.models.txt` is the manifest CI uses to keep this column honest — regenerate it whenever the shell is rebuilt. |
| `ParameterTypes_REFERENCE.csv` | Decoder for the polymorphic `tblAssignmentCommandParameter` rows |

Module and keypad model catalogues are project-scoped — only models *already used* somewhere in this project are guaranteed to map. To introduce a brand-new model type, add one example via Designer first so its `ModelInfoID` becomes known, then the reference can be extended.

---

## 4. End-to-end pipeline

**Engineer (CAD side)**

1. Take the latest `.hw` from `Backups/Lighting/Lutron Programs/`.
2. Refresh the CSVs from it (the import script can do this on demand) so `EXISTING` rows reflect the live state.
3. Pull new fixtures, loads, modules, output assignments, keypads, buttons, and scene definitions out of the CAD model.
4. Append them as `NEW` rows. Keep join keys consistent across files.
5. Hand the CSVs back.

**Import script (programmatic side)**

1. Restore the latest `.hw` to a sandbox SQL Server LocalDB.
2. Validate every `NEW` row:
   - All cross-CSV join keys resolve
   - `LoadTypeID`, `ModuleType`, `KeypadModel` are recognised
   - `AddressOnLink` doesn't clash on the same link
   - `OutputNumber` is in range and unused
   - Every button's `TargetArea + TargetScene` resolves to a real scene
   - Every scene's `ZoneName` resolves to a real load
3. Insert the new rows in dependency order via the same `ins_*` stored procs Designer uses (so `tblNextObjectID` allocation and FKs stay consistent):
   - Areas → Fixtures → Loads → Modules → Output assignments → Keypads → Scenes → Buttons
4. Re-pack the `.lut` into a new `.hw` with a date-stamped filename.
5. Hand the new `.hw` back for opening in Designer for a sanity check before transferring to the processor.

The original `.hw` is never written to.

---

## 5. CAD-attribute → CSV-column mapping

A practical minimum mapping the CAD environment can be configured to export:

| CAD object | CSV row goes in | CAD attribute → CSV column |
|---|---|---|
| Lighting fixture symbol (per type) | `FixturesCatalog` | TypeCode → FixtureRef; Description → Description; Wattage → FixtureWattage_W; DriverType → LoadTypeID |
| Lighting circuit / homerun | `LoadSchedule` | Room → AreaName; CircuitName → ZoneName; TypeCode → FixtureRef; CountOnCircuit → NumberOfFixtures |
| Panel module on schematic | `Modules` | ModuleLabel → ModuleName; ModelTag → ModuleType; Panel → AreaName; CommBus → LinkName; ModuleAddress → AddressOnLink |
| Wire from circuit to terminal | `OutputAssignments` | CircuitRoom/Name → AreaName/ZoneName; ModuleLabel → ModuleName; TerminalNumber → OutputNumber |
| Keypad symbol in plan | `Keypads` | StationLabel → StationName; ModelTag → KeypadModel; Room → AreaName; Bus → LinkName; KeypadAddress → AddressOnLink |
| Button label in keypad layout | `Buttons` | StationLabel → KeypadName; ButtonIndex → ButtonNumber; EngravingText → ButtonLabel; SceneTarget → TargetScene |
| Scene matrix from interior designer | `Scenes` | Area → AreaName; SceneName → SceneName; Zone → ZoneName; LevelPct → Level_pct |

If the CAD system can export attributed schedules, the CSVs above are the target schema. Easiest path: one CSV export per domain from CAD, headers matching exactly.

---

## 6. Validation via the integration report

After every build, regenerate Designer's **Integration Report** and diff the output URIs against the engineering tool's intended state:

- `/zone/N` for every load
- `/areascene/N` for every scene
- `/button/N` for every keypad button
- `/area/N` for every area
- `/led/N` for every keypad LED

Any drift (missing zone, mislabelled scene, untriggered button) shows up immediately. The integration report is also the export your downstream Control4 / Crestron / RTI integration needs anyway — the engineering tool can produce the same CSV directly from its own data without anyone opening Designer.

---

## 7. Quick reference

| What you want to add | Append `NEW` rows to |
|---|---|
| A new fixture type (e.g. "Q = Linear Strip 12W LED") | `FixturesCatalog_TEMPLATE.csv` |
| A new circuit / load | `LoadSchedule_TEMPLATE.csv` |
| A new dimming / DALI / DMX / shade module | `Modules_TEMPLATE.csv` |
| A wire from a load to a module output | `OutputAssignments_TEMPLATE.csv` |
| A new keypad on the wall (or a Phantom Keypad for Control4) | `Keypads_TEMPLATE.csv` |
| Programming for a button | `Buttons_TEMPLATE.csv` |
| A new scene (or a level recipe for an existing scene) | `Scenes_TEMPLATE.csv` |

Hand all seven back; the import side produces a new `.hw` (or a list of validation errors).

---

## 8. Limitations and gotchas

- **Module and keypad model IDs come from Designer's installed product catalogue**, not the database. The reference CSVs only list models already used in this project. To introduce a brand-new model, add one example via Designer first, then refresh.
- **Comm link addresses are link-specific**. Address `4` on `Default3890` is a different slot from address `4` on `Link 001`. The validator checks per-link uniqueness.
- **DMX module outputs** map to DMX channels 1-512. A 3-channel RGB load consumes 3 consecutive channels; the import assigns the higher channels automatically based on the load's `LoadTypeID`.
- **DALI ballasts** are a two-level case: a DALI Module is the bus host, then each DALI Ballast is a separate module entry.
- **Phantom Keypads** are virtual — used by Control4, Crestron, touchpanels, and the iOS / Android Lutron app to fire button presses against the processor. They have no physical address but still need to be defined to be addressable from integration.
- **Multi-area scene buttons** (one button recalling scenes in multiple rooms simultaneously) are represented as multiple rows in `Buttons.csv` sharing the same `KeypadName + ButtonNumber + ButtonLabel`.
- **Fade-time encoding** is currently confirmed only for 2 seconds (raw value 8 in the database). Other fade times need a calibration pass — the import tool should round-trip a value through Designer to learn its encoding before relying on it.
- **Scene `Unaffected` rows** mean "this scene leaves the zone at its current level". They're stored as a separate command type — don't conflate with `Level_pct = 0` which means "drive the zone to off".
- **Spectrum scenes** (CCT/intensity for tunable LED tape) use a wider parameter set than standard zones — see `ParameterTypes_REFERENCE.csv` and `DataModel.md`.

---

## 9. For the developer building the import tool

See `DataModel.md` in this folder for the full database schema, table relationships, polymorphic ParentType codes, and the SQL patterns the import side needs to implement.
