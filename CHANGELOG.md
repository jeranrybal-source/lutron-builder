# Changelog

## v1.3.2 — 2026-08-12

**"Open in Lutron Designer" now actually gets you there.** Found on James's
VM on the very first end-to-end build — which otherwise passed completely:
filled template in, six CSVs, review sheet, and a 3.79 MB .hw written. The
one sour note was the last click: Windows offered Notepad.

Three facts, each measured on the VM rather than assumed, and each one killed
an obvious "fix":

1. Designer is a packaged Windows app — there is no `Program Files\Lutron` to
   search, only an execution alias.
2. It registers no `.hw` association, and its alias **silently discards a file
   argument**: 25 seconds of debug log after launching it with a project
   showed no trace of the file. There is no command-line way in.
3. It refuses to start twice — a second launch pops "Another instance of a
   Lutron application is already running" and waits, which is worse than doing
   nothing.

So the button now does the only three useful things: it puts the file's path
on the clipboard, brings Designer to the front (starting it only if it is not
already running), and says in plain words to click Browse local and paste.
Proven end to end by driving the VM: Designer opened James's own test project,
and its area tree shows Ground Floor → Kitchen, Hall — the rooms off his
spreadsheet.

## v1.3.1 — 2026-08-12

Two review-sheet sentences, both found by James on the first end-to-end run of
v1.3.0, both the same defect: **the review told the truth in a way that read
as an accusation.**

**Circuit references are no longer remarked on for a spreadsheet import.** The
template's own guidance page shows C1, C2, C3 in the circuit-number column;
James copied them exactly and the review then warned him about it, in a
sentence he read as "no circuit numbers were available". Nothing was ever
missing — the references are kept beside each circuit, and Designer numbers
circuits itself. Following the instructions now produces silence; the sentence
survives only for AI plan reads, where the references arrive without a human
choosing a column, and it now leads with what was kept. The column's help says
letters are fine.

**The Homeplay marks.** The star logomark anchors the footer, "by Homeplay"
is the written wordmark, the key headings are set in Times Now, and the
browser tab carries the favicon — all ported from the production app's own
assets, never redrawn, in black for the light theme and white for the dark
one, and bundled into the exe with the docs.

**Your circuit numbers are used, and shown.** C1, C2, C3 restarting in every
room is Designer's own numbering — so where a room's references are letters-
then-number and don't clash, the number now reaches Designer, and the review's
new No. column shows it with your reference beside it. Suffix styles (002A,
002B) stay kept references, because their digits are shared and parsing them
would collapse two circuits onto one number.

**The keypads fact now says what the full system does.** Homeplay's complete
engineering system designs keypads, scenes and the rest of the control system;
this free tool deliberately stops at the loads — and if the demand is there, a
CSV way in for keypads and scenes may follow.

**A loads-only review sheet is all loads.** For a project read from a load
schedule, everything about modules, keypads and scenes leaves the sheet —
zero-count tiles, the empty panel section, the column of red "not wired" —
replaced with one fact: they are added by hand in Designer. A plans read
keeps all of it, because there the counts are real work to check.

**The full brand pass.** The four stages are tracked caps, the body face is
Saans with the headings a size up in Times Now, and the palette sits on the
brand paper and mist — all taken from the design contract's own tokens, not
approximated. The footer star is three times the size, front and centre for
the brand.

**The three ways in, named plainly and honestly badged.** The tabs read
"AutoCAD DWG" and "PDF Plans"; both experimental routes now say
"experimental" — the AI read said nothing while being the most experimental
of the three. "The best way in" and "Experimental" sit ABOVE their headings
as proper eyebrow lines in the brand caps, with space to breathe, instead of
pills crammed beside the title that looked clickable. The header gap either
side of "by" now matches. And the PDF blurb no longer claims Claude reads
the switch positions, which left the scope some time ago — it reads the
legend, the rooms and the circuits, and works out the scenes.

**Each way in keeps its own upload screen.** With a spreadsheet loaded, the
AutoCAD tab showed that spreadsheet's mapping screen — with nowhere to upload
a drawing at all. A loaded read now belongs to the tab it came from; the other
tabs offer their own upload boxes, and switching back keeps your mapping.

**The model page names the models it benchmarked.** "The cheaper model" and
"the most expensive model" are now Claude Sonnet 5 and Claude Fable 5 by name
— the numbers were always theirs, but James read the page as though Fable had
never been tried, and a benchmark you cannot attribute is a claim, not
evidence.

**A load schedule is no longer asked whether it forgot its keypads.** A
spreadsheet cannot carry keypads or scenes and the upload screen says so going
in — then the review asked, room by room, "no keypad — is it controlled from
elsewhere?". It now says once, as a fact: nothing has been forgotten, a load
schedule does not carry them. The room-by-room questions return the moment the
project actually has any, because from then on a bare room is worth asking
about.

## v1.3.0 — 2026-08-12

**Three ways in, ranked, and honest about which one to use.** The reading half
of this tool is the experimental half, and the three routes into it are not
equals. Step 2 now says so on every screen, in order: a schedule spreadsheet
first, the AutoCAD drawing second, the lighting plans read by AI third. The
better the input, the better the output, and only the spreadsheet route is
solid.

**A blank template to send the lighting designer.** If a job has no schedule,
there is now something to ask for one with: `Download the blank template` on
the spreadsheet screen writes an .xlsx with the right columns already on it and
a page explaining each one. The headings are not chosen by hand -- they are
derived from the importer's own field list, and a test puts the template back
through the reader and asserts every column is recognised and the worked
example converts. So it cannot drift away from the thing that has to read it,
which is exactly how a hand-written template goes wrong: as a mapping screen
full of "not used" on somebody else's machine.

The sheet ships empty, with the example on the guidance page rather than in the
data. An example row left in by accident is a circuit for a room that does not
exist, and the whole promise of this tool is that nothing in the output was
invented.

**The AutoCAD drawing, as a way in — experimental.** A DWG is often the only
thing a job has. This reads the circuit labels off it, with the rooms and the
storeys, and turns them into the same rows a spreadsheet would have produced --
so it goes through the same mapping screen, the same conversion and the same
review as everything else. On the drawing it was built against it reads 118
circuit labels into 40 circuits across 22 rooms, and the Family Room's counts
come out at 9, 10, 4 and 5, matching the designer's own specification exactly.

It is shipped with its limits written on the screen, because they are large. A
drawing has no room boundaries in it, so every label is given to the nearest
room name, and rooms are not circles: 13 rooms out of 22 had at least one label
sitting as close to the room next door, and each of those is flagged on the row
and in the report. A drawing never says what a fitting IS or what it draws, so
every wattage comes out blank. Roughly a quarter of a DWG's text does not
decode at all and is dropped rather than guessed at. Where the designer
annotated a circuit on the drawing, his own words become its name.

**The project now needs Python 3.10**, and that is what makes the drawing route
real rather than inert. `ezdwg` has no wheel below it, so the floor moves for
everything: the package, the linter, CI, the exe build and the docs. The exe was
already built with 3.12, so nothing changes for anyone using it -- this is about
running from a source checkout, where an older Python now stops at the front
door with a sentence rather than a TypeError from deep in a build.

Raising the target also turned on a linter rule that only exists from 3.10, and
it found five real `zip()` calls with no stated behaviour for mismatched
lengths. Each is now explicit: the template pairs its headings with the
importer's fields **strictly**, so a column with no field fails loudly instead of
quietly describing the shorter of the two; the ragged database rows keep
truncating, on purpose, and say so.

**The report no longer calls a drawing a spreadsheet.** It now names which of
the two produced the numbers, and writes the drawing's own warnings into the
project folder -- that file is the only thing that says, six months later,
where a schedule came from and how much to trust it.

**The mapping screen can no longer show a mapping that belongs to a different
sheet.** Two holes closed, both reproduced in a browser first: a re-guess reply
landing after a sheet switch stamped the old sheet's mapping onto the new one,
and the server's single held spreadsheet let an upload in a second browser tab
silently hijack the first tab's screen -- whose import would then have written
a complete, believable schedule with every column read as the wrong thing.
Every upload is now numbered; a guess or import quoting a superseded number is
refused, and the page throws away replies for screens the user has left. And a
stray click that sets a data row as the headings row -- which turned every
dropdown into gibberish with only a small caption as the clue -- is now called
out loudly, naming the row that does look like headings.

**Somewhere to put the trims** (James's catch, on the review of a filled
template: every fitting wore the flagged 5%/90% default with no way to say
otherwise). The Fittings sheet now has a Dimming range column, written the way
a designer writes it -- `5-90` -- and read by the same pattern the schedule
reader already parses, so the two cannot disagree. Written once on the
fitting, in force on every circuit that uses it; blank still means the
default, flagged, never guessed; prose in the cell is reported, not misread.

## v1.2.0 — 2026-08-08

**Any lighting designer's plans, not just the three we happen to have.** The
requirement, in James's words: *"I need to be able to upload any plans from any
lighting designer and have this work."* House A, House B and House C are
a test corpus chosen for how differently they are drawn — two of them are the
same designer and still disagree about almost everything: where the wattages
live, whether circuits are drawn at all, whether the job is DALI or phase, one
file or seven.

**The thing this release is really about: a value nobody read must never look
like a value somebody read.** The tool was creating uncertainty and then
destroying it three ways over. A blank fixture count became `1`. A blank driver
became LED reverse phase. Blank trims became 5% and 90%. All of them then
appeared on the review sheet — the one thing an engineer checks against the
drawings — indistinguishable from something the designer had specified. And the
extraction's own explanation for each blank was read out of the CSV and thrown
away, so it never reached anybody: on the real House A read, **109 of 109
circuits carried a note saying why the count was missing and not one was ever
shown**.

So:

- **The notes survive**, on fittings, circuits, keypads and scenes, and appear
  beside the row they belong to.
- **An unread value shows an em dash**, and that circuit's load is left out of
  every total rather than worked out from the guess.
- **Assumed values are labelled** wherever they appear. The defaults still
  apply, so files still build — they just no longer pretend to be readings.
- **A new panel at the top of the sheet** lists what the drawings did not say,
  so nobody has to read two hundred rows to find the four that need attention.

**One circuit that cannot be read no longer refuses the whole house.** An
honest re-read of House A left every fixture blank and produced 194 problems
and nothing buildable, while an earlier read that guessed produced a file that
built perfectly. Neither is right. Circuits that resolve are now built and the
rest are reported by room and name.

**The fitting list is editable in the app**, next to the scenes — dimming type
as a list, because it is a Lutron code and not a word. The values most likely
to have been assumed are now the easiest to correct, and a wattage typed by
hand is recorded as such rather than passing as the designer's own.

**The brief has learned what any-designer work needs.** How to group fittings
into circuits when none are drawn — a preliminary set is normal, and the old
instruction produced a house with no circuits at all. That fitting types and
control types never mix on one circuit. The phase load ceiling and its scope
(500 W to fit any output, 500–800 W marked first-output-only, and no ceiling at
all on DALI or switched). `Unaffected`, which had been used **zero times across
1,204 real scene rows** because nothing ever told the model it existed. Linear
product measured in metres rather than counted as symbols — 10 m of 9.6 W/m
tape read as one symbol is out by a factor of ten and undersizes the dimmer.
That a supplied specification beats a web search. Rooms that repeat on another
floor. Legends that do not cover the whole set.

**Smaller things that matter on a real sheet.** Scenes show their fade, and say
where a designer's fade will be rewritten. Each room says how it is driven.
Three warnings that fired once per row — every unwired circuit, every off-link
keypad, every fitting without a phase flag — now fire once: the honest House A
sheet went from 79 warnings to 5 without losing a fact.

**The dimming type we assume is DALI, not phase.** Virtually every
architectural fitting made today is available with a DALI driver and that is
the house preference; mains reverse phase is for things that take a lamp —
table and floor lamps, decorative pendants, chandeliers, 5A circuits. The old
blanket assumption of reverse phase was wrong on most fittings of most jobs,
and wrong in the direction that needs rewiring. The schedule, the review sheet
and the fitting editor all apply the same rule from one place, and the value is
still recorded and labelled as an assumption.

**"Worth a second look" is readable again.** Everything per-room and per-keypad
is collapsed to one line naming the rooms, and what is left is grouped under
headings. The honest House A sheet went from 79 lines to seven under five
headings.

**The app can display its own review sheet.** The anti-framing headers added in
an earlier security review applied to the review sheet too, so the Check screen
showed "127.0.0.1 refused to connect" where the sheet should be. It is now
framable by this app and by nothing else.

**Reviewed before release.** Codex reviewed the whole diff and found eight real
defects, including linear runs that reached the review sheet but never reached
the built file, and a blank scene level that was built as "switch this circuit
off". Gemini found three more: a false alarm about linear
circuits, a CommandType of "-" slipping past validation and being built as a
brightness, and a button engraving escaped twice. All fixed, each with a test.
The three baseline projects are unmoved at 85, 26 and 12 problems.

**Validated on two real reads before release.**

*House C* — a different designer (another design house), a different drawing tool, wattages
on the sheet: 151 circuits, **all 151 bound to a fitting**, zero validation
problems. It read both keypad families off the control note, took the dimming
method from the schedule's own column, and converted six per-foot tape types into
the new per-metre column. The same house read on 2026-08-07 also found 151
circuits — but bound none of them.

*House A*, re-read with the designer's prelim circuit estimate supplied
alongside the plans: **521 circuits, all 521 bound, 47 of 49 rooms matching their
stated count** — against 194 circuits, none bound, and three rooms matching on the
read before this release. The control split came through as 257 DALI / 165 phase /
99 switched against the estimate's 257 / 167 / 97.

**What this release still cannot do.** Neither read could count the fittings on a
circuit where the PDF does not show which symbol belongs to which load box:
House B managed all 27, House C 11 of 151, House A none of 521. Every one
is flagged and excluded from the load totals rather than guessed, but circuit
loading on those jobs still has to be marked up by hand. The fix is almost
certainly to read the DWG rather than the PDF — noted for a later version.

## v1.1.19 — 2026-08-07

**Hundreds of "problems" on real jobs were the writer refusing its own idea
spelled differently.** Scene rows say what a circuit should do, and the writer
accepted only `SetLevel`, `ZoneLevel` and `Unaffected`. Across nine real reads
the models wrote `Level`, `GotoLevel` and `GoToLevel` just as often — and the
real House A job on disk uses `Level` on all 436 of its scene rows. Each
one was reported as an error on a schedule that was otherwise fine.

Those spellings are now accepted, built as the ordinary brightness they plainly
mean, and noted in the warnings. **On the real House A project this takes
562 problems down to 126**; House B is unchanged at 38, because it happened to
use a word already on the list.

This is an explicit allowlist, not a free-for-all: `Unaffectd`, `Levl` and
`Toggle` are all still refused. Everything except `Unaffected` is built as a
brightness, so a misspelt `Unaffected` would switch on a circuit the scene meant
to leave alone — the round-2 defect stays fixed, with a test for each spelling.

**Also: the model note now carries measured figures for the expensive model
too.** It was tested on the same three houses and read the same amount — 292
circuits against Opus's 293, 99 keypads against 100, the same 116 rooms — for
1.9x the price. The previous claim that it read *fewer* circuits was inherited
from testing nobody could point at, and was wrong. Every figure in that note is
now something this repo measured.

## v1.1.18 — 2026-08-07

**One model, chosen on evidence — and two defects the testing uncovered.**

Six real reads: Opus 5 against Claude Sonnet 5, three houses (8 sheets),
identical settings, fixture research off on both so the comparison was about
reading drawings rather than searching the web. Across all three, Sonnet found
**54% fewer circuits, 51% fewer keypads and 54% fewer scenes**. On the
five-sheet house it read the room names off the one clean spreadsheet and
nothing at all off the four plan sheets — it could not resolve the fixture
symbols, said so in its report, and correctly refused to invent them.

- **Sonnet and Fable are withdrawn; Opus 5 is the only model.** The dropdown is
  replaced by a statement of the model, what this job costs on it, and a
  "Why only this model?" note carrying the actual figures. The withdrawn models
  are refused by the server too, so a stale browser tab cannot submit one — but
  they stay in the pricing table, so a project read on one still shows its real
  cost instead of £0.
- **A drawing's own circuit reference no longer destroys the read.** Lighting
  drawings number circuits `001A`, `002B`, `003C`; the loader demanded a whole
  number and threw, so a **correct 151-circuit read of a real house could not
  be opened at all** — the 9.6 W bug again, in a different column. Those
  references are now left to Designer's own room numbering, reported in the
  warnings, and untouched in the CSV. They are NOT parsed down to leading
  digits: `002A`, `002B` and `002C` would collapse to 2 and collide.
- **Rooms with nothing in them are no longer buildable.** The v1.1.16 guard
  refused a project with no rooms *and* no circuits; 49 rooms containing zero
  circuits sailed past it, validated clean, and would have built a .hw of empty
  rooms. It now refuses when there are no circuits and no keypads, pointing at
  the extraction report.
- The cost explainer no longer says sheet count drives the bill: measured
  across six reads, a single dense 7.7 MB sheet cost more than a five-sheet
  5.6 MB set. It follows how much drawing there is, not how many files.

## v1.1.17 — 2026-08-07

**A key your account no longer accepts now says so in English.** Keys get
rotated and revoked; that is ordinary, not a crash. It arrived as a raw
`AuthenticationError: Error code: 401 - {'type': 'error', ...}` — accurate,
unreadable, and silent on what to do. It now says the key was rejected, that
**nothing was charged**, and where to get a current one. The same applies to a
key that is not permitted to use the chosen model, and to being rate-limited
before the read starts.

**And a way to exercise the app without paying for a read.** Reading plans is
the only step that costs money — the review sheet, the scene editor, copying
scenes, reloading after an Excel edit and Build are all free. `python3
tools/make_test_projects.py` installs ready-made projects (already past the
read) that you can drive through all of it; run it again to reset one you have
edited or broken, `--remove` to take them away.

## v1.1.16 — 2026-08-07

**Fixture research is now a choice you make with the facts in front of you,
and the app explains what a read will cost before you pay for it.**

- **A fixture-research switch on the Plans screen**, on by default, with each
  state explained in place: on, Claude looks up fittings the drawings name but
  give no wattage for, and records the source beside each figure on the review
  sheet; off, only what the drawings state is used, and anything missing
  arrives **blank and flagged in the report** rather than guessed — a wrong
  wattage can overload a dimmer. Off is the right choice when the drawings
  already carry a full fixture schedule, or when you want the rooms, circuits
  and scenes now and will do the electrical data yourself. (`--no-research` at
  the command line.)
- **Each model in the dropdown now shows what this job would cost on it** —
  worked out by repricing your own last read on this machine, so it reflects
  drawing sets the size of yours rather than a number we made up. Until you
  have read anything, it shows a labelled estimate instead.
- **A "what will this read cost, and why?" explainer** covering the three
  things that actually drive the bill: how many sheets you send, fixture
  research (the largest share, and why), and the model — with the warning that
  a cheaper model which misreads a circuit costs far more than it saves.
- Turning research off also **revokes the brief's instruction to search**.
  Left standing, the model would have been told it had a search tool it did
  not have, and the likeliest outcome is the one the brief exists to prevent:
  a wattage filled in from memory.

## v1.1.15 — 2026-08-07

**Why a read cost ~$15, fixed — and every read now shows where its money
went.** Fixture research runs as a server-side tool: every web search the
model makes is another full pass over the entire context, and each pass was
billed for ALL of it again — the drawings, the brief, the catalogues — at
full price. A real read burned its whole 60-search allowance that way:
roughly $10 of re-reading before the schedules were even written. Three
changes:

- **The drawings and the brief are now cache-marked**, so every research pass
  after the first re-reads them at a tenth of the price. The same read should
  land around $3–5 instead of $15.
- **The brief now budgets the research: about two searches per fitting**, then
  blank-with-a-note — a live probe once measured eight searches satisfying one
  fitting, and the tenth result is no more trustworthy than the second.
- **After every read, the app and the extraction report show the cost split
  the way the work actually happened**: reading the drawings, fixture research
  (with the search count and what the re-reading cost), and writing the
  schedules. The figures include the cached rates, so what is shown is what
  is billed.

Also: the live "fixture lookups so far" counter could double-count (start and
end of each search both ticked it on some SDK versions), so "60 lookups" may
have been 30 — it now counts starts only, and the final figure comes from the
API's own count. The first-run screen no longer claims "about a pound per
sheet"; it points at the per-read breakdown instead.

## v1.1.14 — 2026-08-07

**A paid read was thrown away whole because one section label didn't match
byte-for-byte — and the response was destroyed with it.** A real five-sheet
House A read came back without an exact `Areas.csv` marker; the app
refused everything (correct) but also discarded the model's response
(indefensible), so there was nothing to recover the five good schedules from,
nothing to diagnose with, and the error advised "extract manually" from a
response that no longer existed.

- **The raw response is now saved on any parse failure**, as a timestamped
  `FAILED-RESPONSE-*.txt` in the project folder, and the error says so, with
  the approximate cost of the read it preserved. The build never touches it
  and a later successful re-read never archives it as a schedule.
- **An unambiguously mislabelled section no longer sinks the read.**
  `AREAS.CSV`, `Areas` and ` areas.csv ` are all accepted as `Areas.csv`,
  with a note saying the label was tidied. Two candidate sections that could
  both mean the same file are never guessed between — that stays a refusal,
  now a recoverable one.

We still don't know which of the two this particular failure was — that is
precisely what the salvage file will answer next time.

## v1.1.13 — 2026-08-07

**Round five reviewed the whole product, not the latest diff — Codex, Gemini
and Claude independently, reconciled on code evidence.** Every fix below was
reproduced before it was written, and both real projects were re-checked
afterwards: identical problem counts (562 / 38), so no new refusal complains
about a correct schedule.

The ones that could have built the wrong house:

- **A "project" left behind by a failed read could be built successfully — as
  an empty house.** The folder appeared under "reopen", Check said "no review
  sheet yet", and Build then wrote a .hw with no rooms in it and reported
  success. A project with no schedules is now refused, in the app and at the
  command line. (Codex)
- **A button numbered 0 validated, appeared on the review sheet, and was
  silently missing from the built file** — shown as programmed, built as
  nothing. Refused now. A BLANK button number is refused too, instead of
  quietly becoming button 1 and merging two different buttons' actions into
  one. (Codex; the blank case from our own pass)
- **When a long answer is continued after a length cut, a repeated section
  header threw away everything before it.** The surviving half could still
  validate — a valid-looking file missing rooms. The parts are joined now; a
  model that restarts the section instead fails loudly on the duplicated
  header. (our pass and Codex, independently)
- **After a scene rename, the review sheet still showed keypad buttons
  pointing at the old name — while Build would write the new one.** The sheet
  says "exactly what will be written", and it wasn't. It is re-rendered after
  the buttons are retargeted, and after a failed save is rolled back. (Codex
  found the success half, Gemini the failure half)
- **An explicit load-type override of 0 — "Default", a real Lutron load type —
  was silently swapped for the fixture's own type** in the built file. (Codex)

Also fixed:

- **A PDF whose filename carried HTML ran as script inside the app when
  chosen.** Drawings arrive from third parties under names we don't control.
  Filenames are now escaped on the page, and stripped to plain labels before
  they reach the AI prompt. (our pass; Codex flagged the prompt half)
- **Every spare output on the panel schedule read as the literal text
  `<span class="spare">spare</span>`** — shipped in a real review sheet.
- **A rebuild that died mid-write could truncate the previous good .hw while
  "Open in Lutron Designer" still offered it.** The file is now assembled
  beside the target and swapped in whole, and the button is withdrawn while a
  build runs. (Codex)
- The Open buttons said nothing when opening failed; a missing report from the
  AI looked like a clean read; a Mac build's final message implied Designer
  could open the result (it never can); two circuits claiming the same zone
  number are now warned about; and the rounding notice named the wrong figure
  for a fitting whose LAMP wattage was the fractional one.
- The instructions no longer advertise button actions the writer refuses
  (`MasterRaiseLower`, `Toggle`, `Macro`). (Gemini)
- **Two tests asserted things the product does not do**: the "loads and
  validates" example test never validated, and the "self-contained sheet" test
  banned links the sheet deliberately carries. Both now test what is true, and
  eighteen tests were added, each driving the real code path.

Known and deliberately left open: blank cells the AI flags in Notes still
become concrete defaults (1 fixture, LED load type, 0%) with those Notes
invisible on the review sheet — the honest fix changes what the sheet shows
and needs a product decision first; re-reading a project archives the old set
file-by-file rather than atomically; and there is still no startup check that
Designer's database engine is present, so a missing Designer surfaces late
and as a raw error.

## v1.1.12 — 2026-08-06

**A final review round before wider release, and it was worth doing — both
reviewers said don't publish v1.1.11.** Everything below was reproduced in the
code first.

The two that could have built the wrong house:

- **Two scenes in a room could share a NAME and still build.** A button recalls
  a scene by name, so a second "Relax" quietly took over every button pointing
  at the first. The in-app scene editor already refused this — but the app tells
  you to edit the schedules in Excel, and that route went nowhere near the
  check. Now refused when the file is built, whichever way you edited it.
- **A half-watt fitting became no watts.** Rounding used Python's default, which
  rounds a half to the nearest EVEN number: 0.5 became 0. Now rounds up, and the
  review sheet and the builder share one rounding rule so they cannot drift
  apart.

And the fix I claimed last release but only half delivered:

- **`28.799999999999997 W` was still on the panel schedule.** I fixed the room
  tables and missed the panel one. Both reviewers caught it.
- **The rounding notice ignored lamp wattage**, so a fitting whose lamp figure
  was fractional was rounded with nothing said. It also now lists every affected
  fitting rather than the first eight.

Also:

- **A failed scene save could leave the keypad buttons changed anyway.** The
  buttons file was written before the scenes were checked, so a failure said
  "nothing changed" while buttons had already been renamed or removed — and the
  next build refused. The scenes are written first now, and put back if the
  button step then fails.
- **The duplicate-keypad message could suggest a nonsense name** — "Hall > Hall
  > Door", or "> Door" with no room. It now only suggests a name when that name
  is genuinely different and genuinely unique.

Checked against both real projects: the new refusals raise no complaint on
either, so a correct schedule is unaffected.

## v1.1.11 — 2026-08-06

**A fitting measured in decimal watts was being quietly rounded down inside the
built project.** Last release let wattages be fractional, because LED tape is
specified per metre — but Lutron stores wattage as a whole number, which was
confirmed by querying Designer's own database rather than assumed. Left alone,
9.6 W/m became 9 W in the file while the review sheet you approved said 9.6.

Now it rounds rather than truncates, so 9.6 lands at 10 — and **the review
sheet tells you which fittings that applies to**, with the before and after.
The figures on the sheet stay the ones you specified, and circuit loading is
worked out from those.

Also from the same review round:

- **`3 x 9.6W = 28.799999999999997W` on the review sheet.** Fractional
  wattages arrived last release and nothing formatted them — on the very sheet
  you check against the drawings. Large numbers keep their commas.
- **One scene numbering clash was reported once per circuit**, so a ten-circuit
  scene told you that you had ten problems. Reported once now.
- **A module claiming more outputs than it physically has** was reported once
  per wired output, and was missed entirely on a module not wired up yet.
- **Two keypads with the same name** are still refused — a button names its
  keypad and nothing else, so they would both get the same programming — but
  the message now tells you the fix: name them for the room they are in.

## v1.1.10 — 2026-08-06

**Copying scenes now tells you what it will cost before it does it.** Copying
replaces the scenes in the rooms you tick — and any keypad button in those
rooms that recalled one of the old scene names goes with them. That was
reported afterwards. The confirmation now names each button that would be
removed, so you can cancel.

## v1.1.9 — 2026-08-06

**A fitting measured in decimal watts made a whole project unopenable.** Last
week's tightening was meant to stop a fixture COUNT being silently rounded —
1.9 fittings quietly becoming 1 loses a light. It was applied to every number,
including wattage, and LED tape is specified per metre: 9.6 W/m is an entirely
normal figure.

The effect was that a real, paid extraction could not be opened again at all.
Wattages may now be fractional; counts, addresses and button numbers still must
not be.

## v1.1.8 — 2026-08-06

**Five ways the writer could build a house that does not match the drawings.**
These are older faults in the engine that produces the Lutron file, not in the
app around it — the second review round went looking in code the first round
never reached. Each one validated cleanly, built without complaint, and gave
you the wrong result.

- **A misspelt scene command switched a circuit the scene meant to leave
  alone.** Everything except `Unaffected` is built as an ordinary brightness,
  so `Unaffectd` quietly became "set this to 0%". Any command it does not
  understand is now refused.
- **Colour and colour-temperature scenes were built as brightness.** The
  instructions advertised `DMXColor` and `Spectrum`; the writer implements
  neither, so a colour preset of 2 would have been written as 2% brightness.
  Both are now refused, and the instructions say so.
- **A module could claim more outputs than it physically has.** A schedule
  saying a four-output module had 99 was believed over the real hardware, and
  the file was built with channels that do not exist.
- **One circuit could be wired to two module outputs.** Only the last one
  actually drove it; the other silently did nothing.
- **Two keypads could share a name.** Buttons are matched to a keypad by name,
  so both received the same programming and one of them was wrong.

Also: **two scenes in a room sharing a number were merged in silence.** There
was a check for it, but it could never run — the two had already collapsed into
a single scene, carrying one name and both sets of levels, before anything
looked. It is now reported.

None of these affect a correct schedule: both real House B projects were
checked and neither gained a single new complaint.

## v1.1.7 — 2026-08-05

**A second review round, by the same two independent AI systems, on the fixes
themselves.** Two of the six were confirmed sound. Four were only partly fixed,
and one much bigger problem was found that the first round missed entirely.

**The big one: editing a scene broke the build whenever the project has
keypads.** A keypad button recalls a scene by its name — and the button can be
on a keypad in a completely different room. The editor rewrote the scenes and
never touched the buttons, so renaming a scene left those buttons pointing at
something that no longer existed. The app said "saved", the review sheet
rendered normally, and the build then refused the whole project.

Renaming one scene in a real job orphaned two buttons, one of them on a keypad
in another room. Now a rename follows the scene onto every button that recalls
it, wherever that button lives, and says how many it moved. Deleting a scene
removes the button actions that recalled it and names them.

Also fixed, from the same round:

- **"Relax" and "relax" no longer count as different scenes**, and nor do
  scene 1 and scene 01. Both collide once the file is built; the check was
  comparing the text rather than the meaning.
- **Finishing a read no longer throws away what you were doing.** It could
  discard a half-typed API key, or unsaved scene edits, by jumping you to the
  Check screen. It now leaves you where you are — and if you were editing
  scenes when the read replaced them, it says so and reloads rather than
  showing you edits against schedules that no longer exist.
- **Build refuses to run against a review you have not seen.** There was a
  gap of about a second between a read finishing and the screen catching up,
  in which "Looks right" would build the new schedules.
- **Editing scenes no longer keeps offering the file built before the edit.**
- **Opening another project mid-build is refused** — the build finishes by
  stamping its output onto whichever project is selected at the time.
- **Opening a project that does not exist no longer creates it** and reports
  success.
- The cost and token count of the previous project's read no longer appear
  against the project you just opened.
- A scene save can no longer slip past the "a job is running" check.

## v1.1.6 — 2026-08-05

**Fixes from an independent review of v1.1.5 by two other AI systems, each
asked to find where this quietly does the wrong thing.** Every finding below
was reproduced in the code before being fixed. If you are on v1.1.5, update.

Wrong programming in a real house:

- **Two scenes in one room could share a name.** A keypad button recalls a
  scene by its name, so adding a second "Relax" silently changed which scene
  an existing button recalled. It saved cleanly and it built. Now refused,
  with the clash named.
- **Two scenes in one room could share a number.** They merge into one scene
  when the file is built. Also refused.
- **Starting a new project kept the previous project's built file.** Build a
  house, go back to the plans, read a different house — and the Build step
  showed as already done, with "Open in Lutron Designer" opening the previous
  house. Reopening a project always cleared this; starting a new one did not.
- **Ticking the same room twice when copying wrote its scenes twice.**
- **Scenes belonging to the Starter Shell could be edited.** The change was
  written but the shell ignores it, so the app reported success and nothing
  happened. Now refused, and they are protected during a copy.

Losing work:

- **"No keypads" could throw away a whole read you had paid for.** Every
  section is required in the AI's answer, and telling it to write no keypads
  invited it to leave the section out altogether — which failed the run and
  discarded the schedules it got right. It is now asked for an empty section,
  and if one is missing anyway it is filled in rather than failing.
- **Saving a scene rewrote the file in place.** A crash or a second save
  part-way through left a half-written schedule. It now writes alongside and
  swaps in one step, and two saves can no longer overlap.
- **Scene edits are refused while a read or build is running** rather than
  changing files being used.

Getting stuck:

- **The progress log froze if you clicked a step while a read was running.**
- **A read finishing while you sat on the Check step left the old review sheet
  on screen** against the new schedules — so "Looks right" could build
  something nobody had looked at.
- **A room the AI gave no scenes to could not be copied into**, and removing
  the last scene in a project locked the editor shut.
- A room chosen in one project no longer carries over into the next.

## v1.1.5 — 2026-08-05

**Scene editing, on the Check screen.** Press "Edit the scenes" and you get a
room at a time: the scene list with its number, name and level, which you can
rename, renumber, re-level, remove or add to.

Then **"Copy these scenes to…"** — tick the other rooms and the whole list
goes across. The names and numbers travel unchanged, so the house is named and
numbered consistently, and each scene sets its level on every circuit in the
room it lands in. Set one room up the way you want it and the rest follow.

Details that matter on a real job:

- **Renaming a scene never touches its levels.** If you have tuned a room
  circuit by circuit, that survives; those scenes are marked "per-circuit
  levels set" so you can see which ones they are. A level is only written when
  you actually change it, and it then says how many circuits it changed.
- **A circuit a scene deliberately leaves alone stays left alone.**
- **Copying tells you what it did** — how many scenes across how many circuits,
  per room — and skips any room with no circuits rather than writing scenes
  onto names that do not exist.
- **Nothing is saved unless the result still loads.** If an edit would leave
  the project unbuildable it is refused and the file put back as it was. The
  previous version is kept alongside as `Scenes.csv.bak`.

## v1.1.4 — 2026-08-05

**v1.1.2 and v1.1.3 do not work at all. Please update.** The app opens, the
page renders, every stage is there — and every button silently fails. Saving
your API key, accepting the terms, opening a project, reading plans and
building the file were all refused.

Last release added a protection that stops another website in your browser
posting to this app behind your back. It works. What no one noticed is that
it also blocked the app talking to itself: the check demands a particular
label on each request, and the app was not attaching it. Nothing on screen
said so, because the page itself is served by a route the check does not
cover.

It was tested — but only that the protection existed, never that the app
could still get through it. There are now three tests, including one that
starts a real server and posts to it exactly as the app does.

Two things you asked for, both on the plans screen:

- **"No keypads — I'll place them myself"** is a new choice in the keypad
  family list, for when you would rather put the keypads in yourself in
  Designer. The scenes for every room are still worked out in full, so they
  are waiting to be bound to buttons when you add them.
- **The four steps down the left are now clickable.** Go back to your API key
  or to the plans at any time — including while a read is running, without
  disturbing it. Check and Build unlock once there are schedules to look at.
  Anything typed is kept when you move around.

## v1.1.3 — 2026-08-05

**The keypad family you chose never reached the AI.** Found by James: a job
read with "Palladiom" selected came back entirely seeTouch.

The app collected your choice, validated it, and passed it all the way down —
and then the last step built the instructions without it. Every read since the
first release therefore told the model "the engineer has not chosen a default,
pick one and say which". It looked correct whenever the drawings happened to
name the same family, which is exactly why it went unnoticed.

It was fixed in v1.1.1 as an accidental side effect of a security change, with
nothing testing it. There are now three tests covering the whole chain,
including one that guards the call site itself rather than the function it
calls — that is where the argument was being dropped.

If you read a job before v1.1.1 and the keypads look wrong, re-read it: the
choice will be honoured now.

## v1.1.2 — 2026-08-05

A second independent review, more thorough than the first. Every finding was
verified in the code before being fixed; it also confirmed there is **no SQL
injection path** and that the previous round's fixes landed.

Highest severity first:

- **The review sheet could run scripts.** It treated any value starting with
  `<` as trusted markup, so an AI-written fixture description could execute
  JavaScript in the page you open to check the job. Trust is now carried by
  type, so only markup this app builds itself is ever trusted.
- **A project called `..` escaped its folder** — it resolved to the whole
  Documents folder, which the app then writes into and serves files from.
  Project folders are now confined, on both creating and reopening.
- **Two sheets with the same filename silently overwrote each other**, so a
  whole floor or the legend could vanish with no error. They are now saved
  under distinct names and the log says so.
- **A misspelt `Action` silently dropped the row** — a circuit or keypad
  simply absent from the finished project. Now refused.
- **A fractional count was silently truncated** (`1.9` fixtures became `1`).
  Now refused.
- **Duplicate fixture references or scene levels** made the review sheet and
  the generated file disagree about what was built. Now refused.
- The app cannot be framed by another page, and the review sheet and report
  are protected from DNS-rebinding, not just the write endpoints.
- One version number for the whole project, checked by the test suite: the
  packaging metadata had drifted to 1.0.0 while the app said 1.1.1.

## v1.1.1 — 2026-08-05

Fixture research verified against the live API, and the search ceiling raised
from 20 to 60 — a probe looking up a single fitting used 8 searches, so the old
cap would have covered barely two fixtures of a thirty-fixture house.

Security hardening from an independent review, before sharing more widely.
Every finding below was verified in the code, not taken on trust.

- **SQL comments could be escaped with a newline.** A room or floor name is
  written into a `--` comment in the generated SQL; a CSV field may legally
  contain a line break, and anything after it would have run as SQL. Comments
  are now flattened to one line.
- **Cross-site requests are refused.** The server has no password because it
  listens only on this machine — but any page in your browser could still post
  to it. It now requires a JSON content type and checks the request's origin
  and host, which also blocks DNS-rebinding.
- **Names from the drawings can no longer inject HTML** into the app page.
  Validation messages quote room and fixture names straight out of the CSVs,
  and those are AI-written.
- **The API key no longer enters the process environment**, where every child
  process the app starts would inherit it and other programs could read it. It
  is passed directly to the AI client instead.
- **The drawings are now treated as data, not instructions.** The brief moved
  to the system prompt, and the model is told that text inside a PDF telling it
  what to do is to be reported as suspicious, not obeyed.
- **The release pipeline no longer interpolates a tag name into a script**,
  which could have run arbitrary commands on the build machine.
- A researched source link is restricted to http(s), so a `javascript:` value
  cannot become a live link in the review sheet.

## v1.1.0 — 2026-08-05

**Fixture research, built into the read.** Drawings routinely name a fitting
("iGuzzini Laser Blade XS 5 module") without its electrical data, and a blank
wattage makes circuit loading and panel sizing worthless. The read now looks
missing data up as part of its normal work — no extra step, no button.

The discipline matters more than the feature:

- Every researched value records **where it came from** in a new `DataSource`
  column, and the review sheet shows researched wattages with a link to the
  source, never looking like a value read off the drawing.
- **Nothing is filled from memory.** Searched-and-cited, or left blank. A
  confident wrong wattage can overload a dimmer module.
- Where a fitting comes in several wattages and the drawings don't say which,
  it stays blank and says so rather than picking one.
- The review sheet flags both counts: how many wattages were researched, and
  how many are still unknown (with the note that load totals exclude them).
- Searches are capped per read, shown live while they happen, and if the API
  refuses the tool the read continues without research rather than failing.

## v1.0.7 — 2026-08-05

Two reads of the same project can no longer merge into one wrong build.

- Re-reading a project archives the previous read's files into a timestamped
  `superseded-.../` folder first. Found on a real job: a fresh read wrote six
  CSVs beside an equipment file from the day before, the build read both, and
  175 validation errors followed. A *compatible* leftover would have been worse
  — a wrong project that looked entirely valid.
- The build now also warns when one schedule file is much older than the rest,
  which catches projects created before this version and hand-assembled folders.

## v1.0.6 — 2026-08-05

The long silence while Claude reads a big drawing set now has honest feedback,
after a six-minute wait that was indistinguishable from a hang.

- The wait shows its real stages, reported by the API rather than invented:
  uploading (with size), reading (silent by nature, and the panel says so),
  then writing — including how many tokens were read off the sheets.
- A liveness stamp: every event on the stream refreshes "connection live /
  last signal Ns ago", so a healthy silence and a dead connection no longer
  look the same.
- The app remembers how long reads take on your machine and says, during the
  silence, when the last one produced its first text and how long it took
  overall.
- The elapsed time shows in the browser tab title, so you can switch away.

## v1.0.5 — 2026-08-05

Fixes a data-losing interface bug found by James on a real job.

- Typing a project name and notes, then changing the keypad family (or model,
  or the chosen files) silently discarded both — the panel redraws on those
  changes and rebuilt the fields empty. The read then went off as "Untitled"
  with no notes. Typed values now survive every redraw, and the Read button
  sends what you typed, always.
- The version stamp in the footer rendered literally as `v${S.version}`
  instead of the version. Now stamped correctly, with a test that no template
  syntax can ever be served to the user again.

## v1.0.4 — 2026-08-05

Two guards born of a real support case: a fresh download appeared broken because
a stale copy of the old app still owned the port and was answering instead.

- The app never shares a port. If its port is taken — an old copy still
  running, or a double launch — it says so and walks forward to the next free
  one, so what opens is always the copy you just started.
- The version is now visible: bottom-right of the page and in `/api/status`.
  The release pipeline refuses to build if the tag and the displayed version
  disagree, and a test ties both to the changelog.

## v1.0.3 — 2026-08-05

Housekeeping from a critical review; nothing changes in normal use.

- A leftover instruction in the multi-PDF prompt still told the AI to produce
  "eight CSVs", contradicting the six-file contract. Wording is now count-free.
- The worked example gained a README explaining that its two equipment files
  must be deleted before building against the bundled (equipment-free) shell.
- The `Placeable` column and the shell manifest are now documented where
  schedule authors actually look.

## v1.0.2 — 2026-08-05

**Fixes a dead interface. v1.0.0 and v1.0.1 are unusable — use this build.**

The whole app is one inline script, and it contained a JavaScript string broken
by a real newline, which is a syntax error. Nothing ran: the header, the step
list and the footer still drew, so the app looked alive while showing an empty
panel and responding to nothing.

It shipped because every test went through the HTTP API, which answered
perfectly throughout. The page itself was never loaded by anything automated.
There are now four tests that do exactly that, including handing the script to a
JavaScript engine to parse — they fail on the old code and pass on this one.

## v1.0.1 — 2026-08-05

No change to what the app does. First build produced by the automated release
pipeline rather than by hand, and the first with a test suite behind it.

- 26 tests covering the failures that have actually happened, a `ruff` clean bill,
  and CI on Python 3.9 and 3.12.
- Pushing a version tag now builds the exe on a clean Windows runner, asserts from
  the PE header that it is 64-bit, smoke-tests that it starts and answers, and
  attaches it to the release.
- Internal tidying only: an ambiguous loop variable renamed, one bare re-raise now
  names the underlying error so a bad cell in a CSV no longer reads as a fault in
  the tool.

## v1.0.0 — 2026-08-04

First release outside Homeplay.

### What it does

- Reads a set of lighting-plan PDFs in one pass and writes six schedule CSVs plus a
  written report of what was read, what was proposed, and what could not be worked out.
- Renders a review sheet — every room, circuit, scene and button — as the gate a human
  checks before anything is built.
- Builds a Lutron HomeWorks `.hw` from those schedules, on the LocalDB that ships with
  Lutron Designer.
- Ships a zero-equipment Starter Shell carrying **15 keypad models** across four
  families: seeTouch/HWI (5, 7, 8, 10 button), Alisse, Palladiom and Aviena.

### Notable behaviour

- **Keypad family is chosen per project, and is never remembered between projects.**
  Shipping a house in the wrong hardware is expensive, so the choice is conscious every
  time. The choice is a *default*: where the drawings specify a different family for
  particular rooms, that is honoured and every difference is listed in the report.
- **Keypad sizes are hard limits.** If a room's scenes will not fit the largest keypad
  in the family that applies there, the schedule reduces the scenes or adds a second
  keypad and says which — it never assumes a keypad that does not exist.
- **No equipment is proposed.** Circuits and keypads arrive in Designer marked
  *Not Assigned*; you attach them to real hardware there. Panel-count advice goes in the
  report instead.
- **Proposed engraving is marked as proposed** on the review sheet, with a warning,
  because engraved faceplates are etched to order and cannot be returned.
- Large drawing sets are handled by continuing and stitching the model's output, proven
  on a 523-circuit set.
- Cost of each read is shown after it completes, billed to your own Anthropic account.

### Known limitations

- Windows only, and Lutron Designer must be installed — the build uses its database engine.
- Circuit groupings are a reconstruction wherever the drawings carry no switching or
  panel schedule, which is most sets.
- Per-room fixture counts need checking symbol by symbol against the plans.
- Linear LED runs are counted as runs, not metres; run lengths are not dimensioned on
  most drawings.
