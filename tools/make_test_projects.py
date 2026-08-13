"""Install resettable test projects — so the app can be exercised for free.

Reading plans is the only step that costs money. Everything after it is free:
the review sheet, the scene editor, copying scenes between rooms, reloading
after an Excel edit, and building the .hw. This installs projects that are
already past the read, so all of that can be driven without spending anything.

Run it again at any time to reset them: each project is wiped back to its
pristine copy, so you can edit, break, and rebuild as roughly as you like.

    python3 tools/make_test_projects.py            # install / reset
    python3 tools/make_test_projects.py --list     # show what is installed
    python3 tools/make_test_projects.py --remove   # take them away again

Sources: the worked example bundled with the writer, plus any extra folders of
schedule CSVs passed on the command line.

    python3 tools/make_test_projects.py "~/Documents/Lutron Builder/House B Test"
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwwriter import ingest  # noqa: E402
from hwwriter.app import projects_dir  # noqa: E402
from hwwriter.schedule import load_schedule, read_reference  # noqa: E402

# Obvious at a glance in the reopen list, and sorts to the bottom.
PREFIX = "ZZ TEST - "
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "test-house")


def _render(pdir: str, title: str) -> str:
    """Give the project a review sheet, so the Check screen opens straight away."""
    from hwwriter import review
    docs = ingest.DOCS
    module_types = read_reference(os.path.join(docs, "ModuleTypes_REFERENCE.csv"),
                                  "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(os.path.join(docs, "KeypadModels_REFERENCE.csv"),
                                   "KeypadModel", "LutronModelInfoID")
    sched = load_schedule(pdir, module_types, keypad_models)
    defaults = read_reference(os.path.join(docs, "ModuleTypes_REFERENCE.csv"),
                              "ModuleType", "DefaultOutputCount")
    mo = {m.name: (m.output_count or defaults.get(m.model_label, 0))
          for m in sched.modules}
    review.write(sched, os.path.join(pdir, "review.html"), title, "Lutron Builder", mo)
    return (f"{len(sched.areas)} rooms, {len(sched.loads)} circuits, "
            f"{len(sched.keypads)} keypads, {len(sched.scenes)} scenes")


def install(sources: list, quiet: bool = False) -> list:
    made = []
    for src in sources:
        src = os.path.expanduser(src)
        if not os.path.isdir(src):
            print(f"  skipped (not a folder): {src}")
            continue
        csvs = [f for f in os.listdir(src) if f.lower().endswith(".csv")]
        if not csvs:
            print(f"  skipped (no schedule CSVs): {src}")
            continue
        name = PREFIX + os.path.basename(src.rstrip(os.sep))
        dest = os.path.join(projects_dir(), name)
        # Reset: the whole point is that a mangled test project can be put back.
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.makedirs(dest)
        for f in csvs:
            shutil.copy2(os.path.join(src, f), dest)
        report = os.path.join(src, "EXTRACTION-REPORT.txt")
        if os.path.exists(report):
            shutil.copy2(report, dest)
        else:
            with open(os.path.join(dest, "EXTRACTION-REPORT.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write("Test project installed by tools/make_test_projects.py.\n"
                         "No AI read was involved, so there is no extraction "
                         "report. Everything else in the app works normally.\n")
        try:
            summary = _render(dest, name)
        except Exception as exc:  # noqa: BLE001 -- report, do not abort the batch
            summary = f"review sheet FAILED to render: {exc}"
        made.append((name, summary))
        if not quiet:
            print(f"  {name}\n      {summary}")
    return made


def installed() -> list:
    base = projects_dir()
    return sorted(d for d in os.listdir(base)
                  if d.startswith(PREFIX) and os.path.isdir(os.path.join(base, d)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extra", nargs="*", help="extra folders of schedule CSVs to install")
    ap.add_argument("--list", action="store_true", help="show what is installed")
    ap.add_argument("--remove", action="store_true", help="remove the test projects")
    args = ap.parse_args(argv)

    if args.list:
        found = installed()
        print("\n".join("  " + f for f in found) if found
              else "  (no test projects installed)")
        return 0

    if args.remove:
        for name in installed():
            shutil.rmtree(os.path.join(projects_dir(), name))
            print(f"  removed {name}")
        return 0

    print(f"Installing test projects into {projects_dir()}")
    made = install([EXAMPLE] + list(args.extra))
    print(f"\n{len(made)} test project(s) ready. Open Lutron Builder and pick one "
          f"from 'or reopen'.")
    print("Reading plans is the only step that costs money -- the review sheet, "
          "the scene editor and Build are all free.")
    print("Re-run this to reset them after you have edited or broken one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
