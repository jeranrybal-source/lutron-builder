# AVS Keypad Programming Patterns

Reference for how AVS programs HomeWorks QSX keypads, and what this tool must reproduce to match it. Built from a read-only survey of 12 real AVS QSX jobs (2024-2026, 3,498 programmed keypad buttons), plus Jeran Rybal's rulings on the open questions (2026-09-23).

Use this file when deciding what `Buttons.csv` needs to express and what `build.py` needs to write. Where a rule here conflicts with a generic Lutron default, this file wins for AVS jobs.

## 1. Button types

Designer stores each button's behavior as a programming model (`tblProgrammingModel`). `ObjectType` and `Name` identify the kind.

| Button type | Name | ObjectType | Share of AVS buttons | Tool today |
|---|---|---|---|---|
| Toggle (press on, press off) | `ATPM` | 74 | 73.6% (2,574) | Not built |
| Master raise/lower | `MRLPM` | 77 | 14.4% (503) | Not built |
| Single action | `SAPM` | 60 | 10.1% (354) | Built |
| Scene raise/lower | `SSRLPM` | 76 | 1.9% (65) | Not built |
| Dual action (press/release) | `DAPM` | 62 | Not on keypad buttons | n/a |
| Variable / advanced conditional | `SCPM` / `AdvanceConditionalProgrammingModel` | 171 / 231 | 2 buttons total | Skip |

Preset columns used per type:

- Toggle: `OnPresetID` + `OffPresetID`. `HoldPresetId` and `DoubleTapPresetID` rows usually exist but are empty (Designer default).
- Single action: `PresetID` only.
- The tool's current single-action writer forces `ObjectType = 60` (see STATE.md update #8). A toggle writer must write `ObjectType = 74` with both On and Off presets, never a 74 row with only `PresetID` (Designer shows that as no programming).

## 2. AVS rules

1. **Room and load buttons are toggles.** Fan, Accent, Bedroom, Bathroom, Hall, Vanity, Exterior, Kitchen, Stairs, Chandelier, Patio, Shower and similar.
2. **Toggle on** drives each target zone to its level. Default level is **100%**.
3. **Toggle off** drives the same zones to **0%**.
4. **Fade is 2 seconds** on both on and off (Designer fade value `8`, delay `0`). 88% of AVS actions use it.
5. **Evening mode is a toggle** that turns the room and its areas on to about **15%**. Same pattern for Night Mode style buttons. Built as a multi-zone toggle, not as an area scene.
6. **Single action is for one-way buttons only:** Off buttons (Suite Off, Area Off, House Off, Goodnight, Away, Goodbye) drive zones to 0%, and shade buttons drive shade groups to a preset.
7. **Area scenes are not used from keypads.** Every surveyed job still carries Designer's five default scenes per area at 0 / 100 / 75 / 50 / 25%, and no keypad button recalls one. Do not generate scene-recall buttons or custom scenes for AVS jobs.
8. **Double-tap and hold are not part of the standard.** They appear on only a few jobs and are used on rare occasions. Leave both slots empty (Designer default).
9. **Fans and switched loads** use a switched command, not a dimming level (see section 3).
10. **Bath fan, fireplace and patio heater timers** are sequences: the toggle's On starts the sequence, Off stops it.

## 3. Command types

`tblPresetAssignment.AssignmentCommandType` on each preset row, by target (`AssignableObjectType`):

| Code | Target | Meaning | Status |
|---|---|---|---|
| 2 | Zone (15/198/211/370) | Set level | Confirmed (tool source) |
| 1 | Zone | Unaffected | Confirmed (tool source) |
| 5 | Area (2) | Recall area scene | Confirmed; not used by AVS keypads |
| 5 | Shade group (133) | Shade group to preset | Seen on 134 shade buttons |
| 3 | Zone | Switched on/off | Inferred from labels (Fan, Garage, House Off). Confirm in Designer before writing |
| 49 | Zone | Fan speed | Inferred (ceiling and bath fans, 2 jobs). Confirm before writing |
| 12 | Zone | Contact closure pulse | Inferred (gate open/close, 1 job) |
| 23 / 21 | Sequence (90) | Start / stop sequence | Inferred (fan and fireplace timer toggles) |

Command parameters (`tblAssignmentCommandParameter.ParameterType`): 1 = fade (Designer enum, `8` = 2 s), 2 = delay (seconds), 3 = level (0-100), 7 = scene number, 9 = raise/lower target. Full decoder in `ParameterTypes_REFERENCE.csv`.

## 4. Automation seen on AVS jobs

- **Sequences:** 5 of 12 jobs, always run timers for bath exhaust fans, fireplaces and patio heaters. Fordyce has 33, Deer Crest 86 has 15, Nichols 14. Every job also has Designer's built-in "Security Mode" sequence.
- **Timeclocks:** 6 of 12 jobs have real events (exterior on/off, step lights, address light, vacation modes). All jobs carry Designer's pre-built vacation-mode placeholder events. Timeclocks belong in the master template per `PROGRAMMING_STANDARD_v1.md`, not in this tool.
- **Conditional logic and variables:** one instance each across 12 jobs. Out of scope.

## 5. Build order for this tool

Ranked by hand-programming removed per job.

1. **Toggle buttons** (74% of buttons, every keypad). `Buttons.csv` needs a toggle action type carrying the on level per zone; off is implied (0%, same zones). The Starter Shell's example buttons are already toggles, so the clone template exists.
2. **Switched and fan command types** so fan and relay circuits get the switched command instead of a dimming level.
3. **Master raise/lower** (14%). Research first: MRL presets are empty, so the target zones are stored somewhere not yet found.
4. **Fan and fireplace timer sequences.**
5. **Shade group presets.**

Not planned: double-tap, hold, area scenes, timeclocks, conditional logic.

## 6. Survey method

Each `.hw` was restored to SQL Server LocalDB on the Windows VM through hwwriter's own sandbox and read with ~25 read-only queries. Nothing was modified. Two files were left out as unfinished (Bargowski 25-4-14: keypads with no zones behind them; Bald Eagle 25-4-15: no keypads). The survey's Deer Crest 86 counts (184 toggle, 26 single action, 12 raise/lower) match the pilot's independent count exactly.

| Job | Keypads | Buttons | Toggle | Single | Raise/lower | Timers | Timeclocks | Shade groups |
|---|---|---|---|---|---|---|---|---|
| Fordyce Residence | 142 | 531 | 480 | 39 | 12 | 33 | 2 | 1 |
| Zubair 24-1-22 | 196 | 497 | 364 | 34 | 94 | 0 | 0 | 0 |
| Valcarce 24-2-28 | 66 | 441 | 298 | 13 | 130 | 0 | 8 | 8 |
| Henderson 2024-03-14 | 54 | 377 | 248 | 21 | 108 | 0 | 4 | 21 |
| Elvidge 3-20-24 | 39 | 308 | 219 | 11 | 78 | 0 | 0 | 0 |
| Nichols 2024-07-18 | 59 | 271 | 162 | 89 | 20 | 14 | 3 | 25 |
| Rohani 24-2-13 | 137 | 266 | 215 | 9 | 42 | 0 | 0 | 0 |
| Golder 24-4-25 | 67 | 252 | 179 | 57 | 16 | 3 | 3 | 30 |
| Deer Crest 86 | 69 | 222 | 184 | 26 | 12 | 15 | 0 | 20 |
| Bogan 24-1-2 | 45 | 184 | 133 | 17 | 34 | 0 | 4 | 0 |
| Ryan Smith Tuhaye | 58 | 82 | 72 | 0 | 10 | 0 | 0 | 0 |
| Elbrader 24-2-28 | 84 | 71 | 20 | 38 | 12 | 1 | 0 | 14 |

Keypads counts every keypad-class device, including sensors and RF devices without buttons.
