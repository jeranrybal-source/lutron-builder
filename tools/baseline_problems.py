"""Count the problems and warnings a real project's schedules produce.

Rebuilt 2026-08-09. The reason it exists: on 2026-08-08 a bracket error turned
House A from 85 problems into 332 with the whole test suite still green,
and nothing but a re-measurement against real schedules would have caught it.
So this is the check that runs after every change to the reading or validating
side, and every movement in the numbers has to be accounted for.

The shell is a binary Lutron database that nothing on macOS can open, so this
uses the same stand-in the test suite uses: the keypad models listed in
`shells/Starter Shell.models.txt` count as present, and floors, comm links and
module blueprints are all empty. That makes the numbers reproducible on this
machine and comparable between runs, which is the only thing a baseline needs
to be. They are NOT the numbers a real build on Windows would print -- a real
shell has the comm link and the modules, so it prints far fewer.

Usage:
    python3 tools/baseline_problems.py                  # the three baselines
    python3 tools/baseline_problems.py <folder> ...     # any schedule folders
    python3 tools/baseline_problems.py --list           # show each problem
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwwriter import ingest  # noqa: E402
from hwwriter._paths import docs as _docs  # noqa: E402
from hwwriter._paths import root as _root  # noqa: E402
from hwwriter.schedule import (  # noqa: E402
    ValidationError,
    load_schedule,
    read_reference,
    validate,
)

DOCS = _docs()
ROOT = _root()

# The worked example ships with the repo and everyone has it. Real drawing
# sets do not ship -- they are somebody's house -- so their locations and
# expected counts live in an untracked file beside this one. Copy
# `baselines.example.json` to `baselines.local.json` and point it at yours.
# A folder that IS named but missing is reported, never skipped silently.
LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "baselines.local.json")
BASELINES = [("worked example", os.path.join(ROOT, "examples", "test-house"), 12)]
if os.path.exists(LOCAL):
    import json
    with open(LOCAL, encoding="utf-8") as fh:
        BASELINES = [(row["label"], os.path.expanduser(row["path"]),
                      row["expected"]) for row in json.load(fh)] + BASELINES


def measure(folder: str) -> tuple[int, int, list[str], list[str]]:
    """Return (problems, warnings, problem texts, warning texts) for a folder."""
    module_types = read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                                  "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"),
                                   "KeypadModel", "LutronModelInfoID")
    sched = load_schedule(folder, module_types, keypad_models)
    # The stand-in: a model listed in the manifest is one the shell can place.
    # A None value is enough for the "is this model in the shell" check, which
    # is what the manifest actually knows. It is NOT enough for the check that
    # a button number fits the keypad's real button count -- validate() skips
    # that when the blueprint is None, so this baseline cannot see that one
    # class of problem. Only a Windows build against the real shell can.
    keypad_blueprints = {int(m): None for m in ingest.shell_models()}
    try:
        sched = validate(sched, set(), set(), {}, keypad_blueprints)
        problems: list[str] = []
    except ValidationError as exc:
        problems = list(getattr(exc, "problems", None) or [str(exc)])
    warnings = list(sched.warnings)
    return len(problems), len(warnings), problems, warnings


def main(argv: list[str]) -> int:
    show = "--list" in argv
    args = [a for a in argv if not a.startswith("--")]
    targets = ([(os.path.basename(os.path.abspath(a)), a, None) for a in args]
               if args else BASELINES)

    drifted = False
    missing = False
    for name, folder, expected in targets:
        if not os.path.isdir(folder):
            print(f"{name:<28} MISSING  {folder}")
            missing = True
            continue
        try:
            n_p, n_w, problems, warnings = measure(folder)
        except Exception as exc:  # noqa: BLE001 -- a failure to read IS the result
            print(f"{name:<28} FAILED TO READ: {exc}")
            drifted = True
            continue
        mark = ""
        if expected is not None and n_p != expected:
            mark = f"  <-- DRIFT, baseline is {expected}"
            drifted = True
        print(f"{name:<28} {n_p:>4} problems  {n_w:>4} warnings{mark}")
        if show:
            for line in problems:
                print(f"    P  {line}")
            for line in warnings:
                print(f"    W  {line}")

    if missing:
        print("\nA missing folder is not a pass. Those projects live outside the "
              "repo; re-read them or measure on a machine that has them.")
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
