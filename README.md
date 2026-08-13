# Lutron Builder

[![check](https://github.com/homeplayltd/lutron-builder/actions/workflows/check.yml/badge.svg)](https://github.com/homeplayltd/lutron-builder/actions/workflows/check.yml)
[![release](https://img.shields.io/github/v/release/homeplayltd/lutron-builder?display_name=tag)](https://github.com/homeplayltd/lutron-builder/releases/latest)
[![licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

Turns a lighting designer's PDF plans into a Lutron HomeWorks project file you can
open in Designer — rooms, fixture catalogue, circuits, scenes, keypads and button
programming, read off the drawings by AI and written straight into a `.hw`.

It does the slow, boring half of a first pass: counting symbols, naming circuits,
laying out scenes, and typing it all in. You do the half that needs judgement.

**Provided free of charge, in good faith, as is.** Check everything against the
drawings. See *What it will get wrong* below — that section is the important one.

---

## What you need

> **Windows, with Lutron Designer installed on the same machine.** Writing the `.hw`
> uses the database engine Designer installs, so the last step only works where
> Designer lives. On a Mac that means Windows in Parallels (or similar) with Designer
> inside it — the Mac side alone cannot write the file. Everything before that step
> (reading a schedule, checking the review sheet, correcting it) runs anywhere.

- **An Anthropic API key — only if you use the PDF route.** A schedule spreadsheet and
  an AutoCAD DWG are both read on your own machine, free, with no AI involved. Reading
  PDF plans uses Claude and costs roughly $1–3 for a house, billed to your own account.
  The key is only ever stored on your own machine.

## Getting it

Download `Lutron Builder.exe` from the **Releases** page on the right, and run it. No
installer — it is a single file. Windows may warn you about an unrecognised app the
first time; choose *More info* → *Run anyway*.

## Using it

1. **Start with a schedule spreadsheet if you have one** — it is the designer's own
   figures, it costs nothing, and no AI is involved. There is a blank template in the
   app to send whoever is doing the lighting. Only reach for the PDF route when there
   is no schedule to be had.
2. **Choose your plans.** Several PDFs at a time is fine and usually better — the
   legend on one sheet and the plans on another get read together.
3. **Choose a keypad family.** This is the default for the job; where the drawings
   specify a different family for particular rooms, those are honoured and listed.
4. **Read the report and the review sheet.** This is the gate. The report tells you
   what it read, what it proposed, and what it could not work out. The review sheet
   lists every room, circuit, scene and button.
5. **Correct the CSVs** in Excel if anything is wrong, and reload.
6. **Build**, then open the result in Designer.

## What arrives, and what doesn't

You get rooms, floors, the fixture catalogue, circuits, scenes, keypads and all the
button programming.

You do **not** get processors, dimmer panels, modules or comm links. That is
deliberate: circuits and keypads arrive marked *Not Assigned*, and you attach them to
real hardware in Designer, which takes minutes and is work you'd want to control
anyway. The report tells you how many circuits there are and how many panels that
implies.

## What it will get wrong

Read this before trusting anything:

- **Circuit groupings are usually invented.** Most drawing sets have no switching or
  panel schedule, so the grouping of fixtures into circuits is a reconstruction. The
  report says so explicitly when it is.
- **Fixture counts per room need checking symbol by symbol.** Tags are read reliably;
  tying them to the right room is the weakest part.
- **Button labels are engraving text, and are usually proposed, not read.** If the
  drawings carry no engraving schedule, every label is the AI's suggestion. The
  review sheet marks these "proposed". **Engraved faceplates are etched to order and
  cannot be returned — approve every label before ordering anything.**
- **Scene levels are proposed** unless the designer supplied a scene matrix.
- **Anything a keypad family cannot physically do, it will tell you about** rather
  than silently substitute — for example Palladiom tops out at four buttons, so a
  room wanting six scenes gets fewer scenes or a second keypad, and the report says
  which.

A blank it has flagged is worth more than a guess presented as fact, and it is
written to work that way.

## Running from source instead

If you'd rather not run an exe:

```
python -m hwwriter.cli --server "(localdb)\MSSQLLocalDB" doctor
python -m hwwriter.cli --server "(localdb)\MSSQLLocalDB" app
```

Python 3.10+. `pip install anthropic` for the plan-reading step and `pip install
ezdwg` to read an AutoCAD drawing; everything else is standard library, and the
part that writes the Lutron file has no dependencies at all. `docs/INSTRUCTIONS.md` documents every column of the schedule CSVs
if you want to write them by hand and skip the AI entirely.

---

## Contributing

Improvements are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). The most valuable
thing you can send is not code: it is a drawing set it read badly, with a note on what
it got wrong.

Built at [Homeplay](https://homeplay.tv). Released under the [MIT licence](LICENSE);
see [SECURITY.md](SECURITY.md) for where your data goes and how to work entirely
offline.


## Licence, and the Homeplay name

The code is MIT — use it, change it, ship it, commercially or otherwise. See
[LICENSE](LICENSE).

**The Homeplay name, the star logomark and the wordmark are not covered by that
licence.** They are Homeplay's marks. Fork the code freely; please put your own name
on your fork rather than ours, so nobody is misled about who stands behind a build.

The brand typefaces are deliberately absent from this repository for licensing
reasons, so headings fall back to a standard serif. Everything works exactly the same.

## Contributing

The most valuable thing you can send is **a drawing set it read badly** — see
[CONTRIBUTING.md](CONTRIBUTING.md). Issues and pull requests both welcome; there is a
Discussions tab for questions that are not bugs.
