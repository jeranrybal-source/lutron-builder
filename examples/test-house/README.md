# Test house — a complete worked schedule

Five rooms, sixteen circuits, four keypads, twenty scenes. Copy this folder and
edit it to write a schedule by hand; `docs/INSTRUCTIONS.md` documents every
column.

**Before building it against the bundled Starter Shell, delete `Modules.csv`
and `OutputAssignments.csv`.** Those two files describe dimmer panels and the
wiring of circuits to their outputs — and the Starter Shell deliberately
carries no equipment, so a build that names modules fails with "the shell
contains no module of that type". They are kept here because they are the only
worked example of those columns, for anyone using a shell of their own that
does carry equipment (authored in Designer with modules and comm links in it).

Without them, every circuit and keypad arrives in Designer marked
*Not Assigned*, and you attach them to real hardware there. That is the
intended state — it is also exactly what the AI extraction produces.
