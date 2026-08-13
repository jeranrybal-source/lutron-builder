# Contributing

This is a small tool shared with a handful of people who programme Lutron systems for
a living. The most valuable thing you can send is not code — it is **a drawing set that
it read badly**, with a note on what it got wrong.

## Reporting something it got wrong

Open an issue and include:

1. **What you expected and what you got.** "It put the pendants and the downlights on
   one circuit" is perfect.
2. **The relevant sheet**, or a crop of it. If the drawings are confidential, a redacted
   crop of the area in question is enough — please don't send a client's full set.
3. **The `EXTRACTION-REPORT.txt`** from the project folder. It records what the AI
   thought it was doing, which is usually where the answer is.
4. **The model you used** (shown in the app) and roughly how many sheets.

Do **not** attach a `.hw` file or a full set of schedule CSVs from a live job — they
carry the client's whole design, including room names and fittings.

## Adding a keypad model — and why it benefits everyone

This is the most useful contribution after a bad drawing set, because Lutron's
catalogue references live only inside Designer and cannot be invented. Add a model
once and every user of the app can specify it from then on.

It is **two edits that must agree**, in two different places:

1. **Put an example in the shell.** In Designer, open the *current*
   `shells/Starter Shell.hw` — not one of your own projects, or you will drop every
   model somebody else added — place one keypad of each model you want, save, and
   export. Then rebuild:
   `python -m hwwriter.cli blank --shell "Exported.hw" --out "shells/Starter Shell.hw" --keep-examples --strip-equipment`
2. **Add the catalogue row** to `docs/KeypadModels_REFERENCE.csv`, with
   `Placeable=yes`.
3. **Regenerate the manifest** — `shells/Starter Shell.models.txt`, the list of model
   IDs the shell can build. Run `inspect --shell "shells/Starter Shell.hw"` and copy
   the ModelInfoID numbers in. CI reads this, because the shell is a binary database
   that Linux cannot open; without it nothing could check the two edits agree.

CI fails if the catalogue and the manifest disagree in either direction, so you
cannot half-add a model and have it surface later as "the shell contains no keypad
of that model" on somebody's real job.

**Verify the ID against Designer, don't read it off by eye.** Build a test project
using the new model, open it, and run **Reports → Bill of Materials**: it prints the
real Lutron part number and quantity. A mis-typed `LutronModelInfoID` builds the
*wrong hardware, silently* — every "Palladiom" project once came out as Alisse
because two families' IDs were swapped, and nothing caught it because the substituted
keypads happened to have enough buttons. `inspect` reports only the ID and button
count and cannot catch this.

`Placeable=no` marks a row that is documented for reference but has no example in the
shell — a tabletop Pico, a plug-in dimmer, the virtual keypad. Those are hidden from
the AI entirely, because suggesting a keypad that cannot be built is worse than never
suggesting it.

## Changing how the plans are read

The entire extraction brief is `docs/FROM-PLANS-TO-CSV.md` — it is handed to the model
verbatim, so editing that file changes the behaviour with no code change. Keep it
concrete and keep the rule that a flagged blank beats a confident guess.

## Working on it

Standard library only, except `anthropic` for the plan-reading step and `ezdwg` for
reading an AutoCAD drawing. Python 3.10+ -- that floor is set by `ezdwg`, which has
no wheel below it. No framework, no build step for the code itself.

```
git clone https://github.com/homeplayltd/lutron-builder
cd lutron-builder
pip install anthropic                              # only needed for reading plans
python -m hwwriter.cli --server "(localdb)\MSSQLLocalDB" doctor
python -m hwwriter.cli --server "(localdb)\MSSQLLocalDB" app
```

`--server "(localdb)\MSSQLLocalDB"` points at the database engine Lutron Designer
installs; add it to every command. On ARM64 Windows the runner resolves LocalDB to its
named pipe automatically, because `(localdb)\X` connection strings fail there.

The `review` step needs no database at all, so on a Mac or Linux box you can still edit
schedules and render the sheet:

```
python3 -m hwwriter.cli review --schedule examples/test-house --out review.html
```

**Rebuilding the exe** — on Windows, from the repository root:

```
powershell -ExecutionPolicy Bypass -File .\build-exe.ps1
```

It lands in `dist\`. Build on the architecture your users run: an ARM64 Windows machine
silently produces an ARM64 exe that most people cannot start, and PyInstaller does not
warn you. Check it before sharing.

## Before you open a pull request

CI runs on every push: everything imports, the runtime data files are present, the
extraction brief still assembles, the worked example still validates and renders, and no
client `.hw` has been committed. It is quick and it is not a substitute for the real
check:

**Open the app and use it**, and **build a `.hw` and open it in Designer.** Neither is
optional, and a green run means very little on its own:

- The interface is one inline script. A syntax error in it means *nothing* renders — but
  the header, step list and footer are static HTML, so the app still looks alive while
  doing nothing at all. That shipped once and reached a user, because every check went
  through the HTTP API and the API was perfectly healthy throughout. CI now parses the
  script with `node`, but load the page anyway.
- The other failure that matters is a `.hw` Designer freezes on at "Importing… 100%".
  Only opening it finds that.

## Code style

Match the surrounding style: comments explain *why*, particularly where a line encodes
something learned the hard way about Designer's database. Those comments are
load-bearing — for example, `tblControlStationDevice` must never carry a NULL serial
number, because Designer reads that column with `GetNotNullString` and a NULL aborts the
project load with no error shown. Please don't tidy those away.
