"""
Lutron Builder -- the shareable app around the writer (v1, locked 2026-08-03).

A tiny local web app: the server is stdlib http.server bound to 127.0.0.1,
the UI is one embedded HTML page, and the browser is the window. The flow is
the CLI pipeline with buttons on it:

    API key -> plans PDF -> AI extraction -> review sheet + Excel edits -> .hw

Locked v1 decisions: named "Lutron Builder" with a quiet Homeplay credit;
editing happens in Excel on the schedule CSVs (no in-app grid); the .hw builds
locally on Designer's own LocalDB (Windows); everything lives in a per-project
folder under ~/Documents/Lutron Builder/.

The API key is stored in the user's config dir, plainly, with a note saying
so -- it is the friend's own key on their own machine.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import fittings as fittings_mod
from . import ingest
from . import scenes as scenes_mod
from ._paths import root as _root

ROOT = _root()
IS_WINDOWS = os.name == "nt"

APP_NAME = "Lutron Builder"
# Shown in the footer and /api/status, and asserted against the git tag by the
# release workflow -- so what a user sees on screen IS what they downloaded.
# A stale instance squatting on the port once served yesterday's broken build
# while a fresh download sat behind it, and nothing on screen said so.
VERSION = "1.3.2"
STARTER_SHELL = os.path.join(ROOT, "shells", "Starter Shell.hw")

DISCLAIMER = (
    "Lutron Builder is provided by Homeplay free of charge, in good faith, as is. "
    "Homeplay accepts no responsibility for errors in the schedules or the generated "
    "files, for issues with Lutron Designer or Lutron systems, or for any consequences "
    "of using them. Checking the output against the drawings — and everything that "
    "happens in Designer and on site — is entirely your responsibility.")


def config_dir() -> str:
    base = os.environ.get("APPDATA") if IS_WINDOWS else os.path.expanduser("~/Library/Application Support")
    path = os.path.join(base or os.path.expanduser("~"), "LutronBuilder")
    os.makedirs(path, exist_ok=True)
    return path


def projects_dir() -> str:
    path = os.path.join(os.path.expanduser("~"), "Documents", "Lutron Builder")
    os.makedirs(path, exist_ok=True)
    return path


def _config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def load_config() -> dict:
    try:
        return json.load(open(_config_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    with open(_config_path(), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


class State:
    """One project at a time; background work runs in a thread and logs here."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.busy = False
        self.stage = "idle"          # idle | extracting | importing | building
        self.log: list[str] = []
        self.error: str | None = None
        self.project: str | None = None   # folder name under projects_dir()
        self.hw_path: str | None = None
        self.last_extract: dict | None = None   # model/tokens/cost of last read
        self.progress: dict | None = None        # live extraction feedback
        # The spreadsheet being mapped, held between the read and the import so
        # the browser does not have to send a workbook back a second time.
        # Deliberately not persisted: it is a working step, not a project.
        self.sheet: dict | None = None
        # Counts uploads. There is ONE held spreadsheet but possibly several
        # browser tabs; a mapping drawn for one upload must never be applied
        # to another, so every read is numbered and guess/import must quote
        # the number they were drawn from. (Seen for real on 08-12: a screen
        # showing "Circuit number" mapped on a sheet that has no circuit
        # number -- the mapping belonged to a different upload.)
        self.sheet_upload = 0

    def say(self, msg: str) -> None:
        with self.lock:
            self.log.append(msg)

    def snapshot(self) -> dict:
        with self.lock:
            cfg = load_config()
            proj = self.project
            pdir = os.path.join(projects_dir(), proj) if proj else None
            return {
                "app": APP_NAME,
                "version": VERSION,
                "disclaimer": DISCLAIMER,
                "accepted": bool(cfg.get("accepted_disclaimer")),
                "key_set": bool(cfg.get("api_key")),
                "busy": self.busy,
                "stage": self.stage,
                "error": self.error,
                "log": self.log[-200:],
                "project": proj,
                "project_dir": pdir,
                "windows": IS_WINDOWS,
                "review_ready": bool(pdir and os.path.exists(os.path.join(pdir, "review.html"))),
                "report_ready": bool(pdir and os.path.exists(os.path.join(pdir, "EXTRACTION-REPORT.txt"))),
                "hw_path": self.hw_path,
                "last_extract": self.last_extract,
                "progress": self.progress,
                "model": cfg.get("model") or ingest.DEFAULT_MODEL,
                "research": cfg.get("research", True),
                # Both variants, so the toggle repricing is instant and the
                # page never has to do the arithmetic itself.
                "estimates": {
                    "on": ingest.model_estimates(cfg.get("last_profile"), True),
                    "off": ingest.model_estimates(cfg.get("last_profile"), False),
                },
                "estimates_measured": bool(cfg.get("last_profile")),
                "last_read_stats": cfg.get("last_read_stats"),
                "models": ingest.MODEL_CHOICES,
                "model_note": ingest.MODEL_NOTE,
                "keypad_families": ingest.keypad_families(),
                "projects": sorted(
                    d for d in os.listdir(projects_dir())
                    if os.path.isdir(os.path.join(projects_dir(), d))
                ),
            }


STATE = State()

# The server is threaded, so two scene saves can arrive at once -- from two
# browser tabs, or one impatient double-click. Serialise them: read, transform
# and write are one unit, and interleaving them loses an edit silently.
SCENE_LOCK = threading.Lock()


# ----------------------------------------------------------------- pipeline

def _project_dir(name: str, create: bool = True) -> str:
    """A project folder, guaranteed to sit directly inside projects_dir().

    create=False checks the name and returns the path WITHOUT making it, so
    "open this project" cannot conjure an empty folder and then report success
    for a project that never existed.

    Stripping punctuation is not enough: "." and ".." survive it, and a project
    named ".." resolved to the whole Documents folder -- which this app then
    writes into, archives from, and serves files out of.
    """
    safe = re.sub(r'[\\/:*?"<>|]+', " ", name).strip().strip(".").strip() or "Untitled"
    base = os.path.realpath(projects_dir())
    path = os.path.realpath(os.path.join(base, safe))
    if os.path.dirname(path) != base or path == base:
        raise ValueError(f"'{name}' is not a usable project name.")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _render_review(pdir: str, title: str) -> None:
    from . import review
    from .schedule import load_schedule, read_reference
    docs = os.path.join(ROOT, "docs")
    module_types = read_reference(os.path.join(docs, "ModuleTypes_REFERENCE.csv"),
                                  "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(os.path.join(docs, "KeypadModels_REFERENCE.csv"),
                                   "KeypadModel", "LutronModelInfoID")
    sched = load_schedule(pdir, module_types, keypad_models)
    defaults = read_reference(os.path.join(docs, "ModuleTypes_REFERENCE.csv"),
                              "ModuleType", "DefaultOutputCount")
    mo = {m.name: (m.output_count or defaults.get(m.model_label, 0)) for m in sched.modules}
    review.write(sched, os.path.join(pdir, "review.html"), title, "Lutron Builder", mo)


def _has_anything_to_build(pdir: str) -> bool:
    """Is there a lighting programme here, or only an empty room list?

    A circuit with no FixtureRef is not buildable -- there is nothing to put on
    it -- so a schedule made entirely of those is as empty as no schedule at
    all, however many rows it has.
    """
    from .schedule import _rows
    circuits = [r for r in _rows(os.path.join(pdir, "LoadSchedule.csv"))
                if (r.get("FixtureRef") or "").strip()]
    return bool(circuits or _rows(os.path.join(pdir, "Keypads.csv")))


def _table_state(pdir: str, name: str, default_order: list[str]) -> tuple[list[dict], list[str]]:
    """A schedule file's rows and the column order it arrived in."""
    from .schedule import _rows
    path = os.path.join(pdir, name)
    rows = _rows(path)
    order: list[str] = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            order = next(csv.reader(fh), [])
    return rows, order or default_order


def _scene_state(pdir: str) -> tuple[list[dict], list[str], dict[str, list[str]]]:
    """Scene rows, their column order, and the circuits each area has."""
    from .schedule import _rows
    rows, order = _table_state(
        pdir, "Scenes.csv",
        ["Action", "AreaName", "SceneName", "SceneNumber", "ZoneName",
         "CommandType", "Level_pct", "Fade_seconds", "Delay_seconds", "Notes"])
    loads = _rows(os.path.join(pdir, "LoadSchedule.csv"))
    return rows, order, scenes_mod.zones_by_area(loads)


def _fitting_state(pdir: str) -> tuple[list[dict], list[str], list[dict]]:
    """Fitting rows, their column order, and the circuit rows that use them."""
    from .schedule import _rows
    rows, order = _table_state(
        pdir, "FixturesCatalog.csv",
        ["Action", "FixtureRef", "Description", "LoadTypeID", "LoadTypeName",
         "FixtureWattage_W", "Wattage_W_per_m", "LampQuantity", "LampWattage_W",
         "DimmingRange", "LowEnd_pct", "HighEnd_pct", "PhaseControl",
         "ManufacturerName", "ManufacturerModel", "DataSource", "Notes"])
    return rows, order, _rows(os.path.join(pdir, "LoadSchedule.csv"))


def _retarget(pdir: str, moves: list[tuple[str, dict, set]],
              dry: bool = False) -> list[str]:
    """Keep Buttons.csv pointing at the scenes that still exist.

    Buttons reference a scene by name, across rooms. Editing Scenes.csv alone
    leaves them dangling and the BUILD refuses the whole project -- long after
    the editor said the change was saved.
    """
    from .schedule import _rows
    path = os.path.join(pdir, "Buttons.csv")
    if not os.path.exists(path):
        return []
    rows = _rows(path)
    notes: list[str] = []
    changed = False
    for area, renamed, removed in moves:
        if not renamed and not removed:
            continue
        rows, said = scenes_mod.retarget_buttons(rows, area, renamed, removed)
        notes += said
        changed = changed or bool(said)
    if not changed or dry:
        return notes
    with open(path, newline="", encoding="utf-8-sig") as fh:
        fields = next(csv.reader(fh), [])
    shutil.copy2(path, path + ".bak")
    tmp = path + ".writing"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    os.replace(tmp, path)
    return notes


def _restore_scenes(pdir: str) -> None:
    """Put Scenes.csv back from the backup _write_scenes just made.

    Used when the FOLLOWING step fails: the two files are one change, and
    leaving the scenes edited while the buttons are not is the state that makes
    a project refuse to build.
    """
    path = os.path.join(pdir, "Scenes.csv")
    backup = path + ".bak"
    if os.path.exists(backup):
        shutil.copy2(backup, path)


def _write_scenes(pdir: str, project: str, rows: list[dict], order: list[str]) -> None:
    _write_table(pdir, project, "Scenes.csv", rows, order)


def _write_table(pdir: str, project: str, name: str,
                 rows: list[dict], order: list[str]) -> None:
    """Replace one schedule file, but only if the result still loads.

    The old file is kept and put back on failure. Without that, one bad edit
    leaves the engineer with a project that will not build and no way back to
    the AI's output short of paying for the read again.
    """
    path = os.path.join(pdir, name)
    backup = path + ".bak"
    if os.path.exists(path):
        shutil.copy2(path, backup)
    # A column a row carries but the header does not would be dropped silently.
    extra = [k for r in rows for k in r if k not in order]
    fields = order + sorted(set(extra), key=extra.index)
    # Write beside the real file and swap it in one step. Writing in place
    # truncates first, so a crash or a second request mid-write leaves a
    # half-written schedule that nothing puts back.
    tmp = path + ".writing"
    try:
        with open(tmp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in fields})
        os.replace(tmp, path)
        _render_review(pdir, project)          # the real check: does it still load?
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        if os.path.exists(backup):
            shutil.copy2(backup, path)
        raise


def do_extract(pdfs: list, project: str, notes: str, model: str,
               keypad_family: str, research: bool = True) -> None:
    """Read the chosen PDFs into the schedule CSVs.

    Each entry in `pdfs` is (filename, bytes, read_as_text). The third field is
    the engineer's per-file choice and is never guessed: a drawing has an
    extractable text layer too, so auto-detecting would silently send a plan
    as a few hundred dimension strings and lose the whole picture.
    """
    pdir = _project_dir(project)
    STATE.project = os.path.basename(pdir)
    # Reopening a project clears this; starting a new one did not. Build project
    # A, go back to Plans, read project B -- and Build showed as already done
    # while "Open in Lutron Designer" opened project A. The wrong house.
    STATE.hw_path = None
    paths = []
    text_only_paths = []
    for i, (name, data, as_text) in enumerate(pdfs, 1):
        safe = re.sub(r'[\\/:*?"<>|]+', " ", name).strip().lstrip(".") or f"plans-{i:02d}.pdf"
        # Two sheets picked from different folders can share a filename
        # ("Lighting Plan.pdf"). Writing both to the same name silently loses
        # one and sends the other twice -- a whole floor or the legend can
        # vanish with no error anywhere.
        path = os.path.join(pdir, safe)
        if path in paths:
            stem, ext = os.path.splitext(safe)
            safe = f"{stem} ({i}){ext}"
            path = os.path.join(pdir, safe)
            STATE.say(f"Two chosen files are both called '{name}' -- "
                      f"the second was saved as '{safe}'.")
        with open(path, "wb") as fh:
            fh.write(data)
        paths.append(path)
        # Tracked by position, not by name: a collision above renames the file
        # on disk, and matching the choice back by name would then attach it to
        # the wrong document -- sending a specification as pages, or worse,
        # gutting a drawing down to its dimension strings.
        if as_text:
            text_only_paths.append(path)
    cfg = load_config()
    api_key = cfg.get("api_key", "")
    import time as _time
    started = _time.time()
    STATE.progress = {"started": started, "chars": 0, "phase": "uploading",
                      "signal": started}
    first_text_s = None
    def _on_event(info):
        nonlocal first_text_s
        prog = dict(STATE.progress or {})
        prog.update(info)
        prog["started"] = started
        if info.get("phase") == "writing" and first_text_s is None:
            first_text_s = round(_time.time() - started)
            prog["first_text_s"] = first_text_s
        STATE.progress = prog
    try:
        _files, info = ingest.run(paths, pdir, model=model, notes=notes,
                                  say=STATE.say, on_event=_on_event,
                                  keypad_family=keypad_family, api_key=api_key,
                                  research=research,
                                  text_only_paths=text_only_paths)
    finally:
        STATE.progress = None
    # Remember how long reads take on THIS machine, so the next wait can say
    # "last time: first text after 4m, done in 9m" instead of pure silence.
    cfg = load_config()
    cfg["last_read_stats"] = {"first_text_s": first_text_s,
                              "total_s": round(_time.time() - started),
                              "sheets": len(paths), "model": model}
    # Keep the token profile so the next read can price EVERY model against
    # this machine's own real work, rather than a guess.
    if info.get("breakdown"):
        cfg["last_profile"] = info["breakdown"]
    save_config(cfg)
    STATE.last_extract = info
    STATE.say("Rendering the review sheet...")
    _render_review(pdir, project)
    STATE.say("Done. Read the report and the review sheet against the drawings.")


def do_import_sheet(project: str, table: int, header_row: int, mapping: dict,
                    fill_down: bool, wattage_is_total: bool) -> None:
    """Write the schedules from the spreadsheet the user has just mapped.

    Deliberately the same shape as do_extract, and it lands in the same place:
    the six CSVs plus a report in the project folder, then the review sheet. So
    everything after this point -- checking, editing in Excel, building --
    cannot tell the two routes apart, which is the point. Nothing is charged
    and nothing leaves the machine.
    """
    from . import sheets as sheets_mod
    held = STATE.sheet
    if not held:
        raise RuntimeError("The spreadsheet is no longer loaded. Choose it again.")
    name, rows = held["tables"][table]
    files, notes, counts = sheets_mod.convert(
        rows, header_row, mapping, fill_down=fill_down,
        wattage_is_total=wattage_is_total)
    pdir = _project_dir(project)
    STATE.project = os.path.basename(pdir)
    # Same reason as a re-read: a previous set left in the folder merges with
    # this one and builds a house made of two different schedules.
    STATE.hw_path = None
    ingest.archive_previous(pdir, STATE.say)
    for fname, text in files.items():
        with open(os.path.join(pdir, fname), "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(os.path.join(pdir, "EXTRACTION-REPORT.txt"), "w", encoding="utf-8") as fh:
        fh.write(sheets_mod.report_text(
            held["filename"], name, header_row, rows[header_row], mapping,
            counts, notes, fill_down, wattage_is_total,
            from_drawing=bool(held.get("plan_report")),
            drawing_notes=held.get("plan_report")))
    ingest.snapshot_as_read(pdir)
    # No brief was involved -- a spreadsheet is read by code, not by a model --
    # so the record says so rather than naming a brief it never used.
    ingest.write_provenance(pdir,
                            "drawing" if held.get("plan_report") else "spreadsheet",
                            None,
                            source_file=held["filename"], sheet=name,
                            header_row=header_row + 1,
                            columns={k: rows[header_row][v]
                                     for k, v in mapping.items()
                                     if v < len(rows[header_row])},
                            circuits=counts["circuits"])
    STATE.say(f"Read {counts['circuits']} circuit(s) in {counts['areas']} room(s), "
              f"using {counts['fixtures']} fitting type(s). Nothing was charged.")
    for note in notes:
        STATE.say(f"  * {note}")
    STATE.say("Rendering the review sheet...")
    _render_review(pdir, project)
    STATE.say("Done. Read the report and the review sheet against the drawings.")


def do_build(project: str) -> None:
    import argparse

    from .cli import cmd_build
    pdir = _project_dir(project)
    # A rebuild overwrites the previous .hw. Until this one succeeds, the old
    # advertised path may be mid-overwrite -- "Open in Lutron Designer" must
    # not offer a file that is being replaced or was left truncated.
    STATE.hw_path = None
    out = os.path.join(pdir, f"{os.path.basename(pdir)}.hw")
    args = argparse.Namespace(
        server=r"(localdb)\MSSQLLocalDB" if IS_WINDOWS else None,
        user=None, password=None, work=None, container="lutron-hw-writer",
        shell=STARTER_SHELL, schedule=pdir, out=out,
        project_name=os.path.basename(pdir),
        review=os.path.join(pdir, "review.html"),
        dump_sql=None, only_new=True, keep=False)
    # cmd_build prints; reroute into the app log.
    from . import cli as _cli
    old_say = _cli._say
    _cli._say = lambda msg="": STATE.say(str(msg))
    try:
        rc = cmd_build(args)
    finally:
        _cli._say = old_say
    if rc != 0:
        raise RuntimeError("The schedules did not pass validation -- fix the "
                           "problems listed above (edit the CSVs in Excel), "
                           "reload, and build again.")
    STATE.hw_path = out
    STATE.say(f"Wrote {out}")
    if IS_WINDOWS:
        STATE.say("Open it in Lutron Designer, check it, then transfer.")
    else:
        # A .lut written by SQL Server on Linux does not restore on Windows,
        # so this file can NEVER open in Designer (confirmed 2026-08-01).
        STATE.say("This file was built with Docker on this machine, so Lutron "
                  "Designer canNOT open it -- it is for development checks only. "
                  "Build the real file on the Windows machine that has Designer.")


def _background(stage: str, fn, *args) -> bool:
    with STATE.lock:
        if STATE.busy:
            return False
        STATE.busy = True
        STATE.stage = stage
        STATE.error = None
        STATE.log = []
    def runner():
        try:
            fn(*args)
        except Exception as exc:  # noqa: BLE001 -- surfaced in the UI
            STATE.error = str(exc)
            STATE.say(f"ERROR: {exc}")
            traceback.print_exc()
        finally:
            with STATE.lock:
                STATE.busy = False
                STATE.stage = "idle"
    threading.Thread(target=runner, daemon=True).start()
    return True


def find_designer_exe() -> str | None:
    """The Lutron Designer launcher, if it can be found on this machine.

    Designer is a packaged Windows app: no Program Files folder, no .hw
    file association (Windows offers Notepad -- James's VM, 2026-08-12, on
    the very first end-to-end build), and -- measured on that VM, not
    assumed -- its execution alias silently DISCARDS a file argument: 25
    seconds of log after launching it with a .hw showed no trace of the
    file. So this finds the launcher only to bring Designer to the front;
    the file goes in through the one door that exists, Browse local, and
    open_hw() puts the path on the clipboard for it. A classic non-packaged
    install, if one ever exists, is searched for under Program Files.
    """
    import glob
    cfg = load_config()
    cached = cfg.get("designer_exe")
    if cached and os.path.exists(cached):
        return cached
    hits: list[str] = []
    alias_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                             "Microsoft", "WindowsApps")
    hits += glob.glob(os.path.join(alias_dir, "Lutron Designer*.exe"))
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        for pattern in ("Lutron/*.exe", "Lutron/*/*.exe", "Lutron/*/*/*.exe"):
            hits += glob.glob(os.path.join(root, *pattern.split("/")))
    hits = [h for h in hits if "designer" in os.path.basename(h).lower()
            and "unins" not in os.path.basename(h).lower()
            and "beta" not in os.path.basename(h).lower()
            and "alpha" not in os.path.basename(h).lower()]
    if not hits:
        return None
    exe = sorted(hits)[0]      # "Lutron Designer.exe" before channel variants
    cfg["designer_exe"] = exe
    save_config(cfg)
    return exe


def _powershell(script: str, **env: str) -> subprocess.CompletedProcess:
    """Run a PowerShell one-liner, passing values by ENVIRONMENT not quoting.

    Project names carry spaces, apostrophes and parentheses as a matter of
    course ("Lutron Builder - load schedule template (2)"), and every one of
    those breaks a quoted command line somewhere along the chain.
    """
    return subprocess.run(  # noqa: S603
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        env={**os.environ, **env}, check=False, capture_output=True, text=True)


def designer_is_running() -> bool:
    """Is Lutron Designer already open?

    It refuses to start twice: a second launch pops "Another instance of a
    Lutron application is already running" and sits there. Measured on the
    VM, 2026-08-13 -- the first version of this button did exactly that.
    """
    out = _powershell(
        "if (Get-Process -Name 'Lutron.Gulliver.QuantumResi' "
        "-ErrorAction SilentlyContinue) { 'YES' } else { 'NO' }")
    return "YES" in (out.stdout or "")


def open_hw(path: str) -> str:
    """Bring Designer up beside the built .hw, honestly.

    Returns the sentence the page shows the user. Designer cannot be handed
    a file at all (see find_designer_exe) and will not start twice (see
    designer_is_running), so this does the only three useful things: put the
    path on the clipboard, bring Designer to the front -- starting it only
    if it is not already up -- and say exactly what to click.
    """
    if IS_WINDOWS:
        exe = find_designer_exe()
        if exe:
            _powershell(
                "Set-Clipboard -Value ([Environment]::GetEnvironmentVariable"
                "('HP_HW_PATH'))", HP_HW_PATH=path)
            if designer_is_running():
                _powershell(
                    "(New-Object -ComObject WScript.Shell)"
                    ".AppActivate('Lutron Designer') | Out-Null")
                where = "Lutron Designer is already open"
            else:
                subprocess.Popen([exe])  # noqa: S603
                where = "Lutron Designer is opening"
            return (f"{where}. On its Places screen click Browse local at the "
                    f"bottom, then paste — the file's location is already on "
                    f"your clipboard.")
    open_path(os.path.dirname(path) or path)
    return ("Lutron Designer was not found, so the folder holding the file "
            "is open instead. In Designer, use Browse local on the Places "
            "screen to open it.")


def open_path(path: str) -> None:
    if IS_WINDOWS:
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        # check=False swallowed the failure, so a button that could not open
        # anything reported nothing at all.
        proc = subprocess.run(["open", path], check=False, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Could not open {path}: "
                               f"{proc.stderr.decode('utf-8', 'replace').strip()}")


# ------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence per-request stderr noise
        pass

    def _send(self, code: int, body: bytes, ctype: str,
              framable_by_self: bool = False, filename: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if filename:
            # Without this the browser opens a workbook as a wall of binary.
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        # The app must not be framable: a hostile page could otherwise overlay
        # it and trick the user into clicking Build or Open.
        #
        # The ONE exception is the review sheet, which the Check screen shows in
        # an iframe of its own. Blanket DENY applied to that response too, so
        # the app forbade itself from displaying the one document the whole
        # product is built around -- the Check screen read "127.0.0.1 refused to
        # connect" where the review sheet should be. 'self' still refuses every
        # other origin, which is the threat the header was added for.
        if framable_by_self:
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Content-Security-Policy", "frame-ancestors 'self'")
        else:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _do_GET_inner(self) -> None:
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._json(STATE.snapshot())
        elif self.path == "/api/scenes":
            if not STATE.project:
                self._json({"ok": False, "error": "No project open."}, 400)
                return
            pdir = os.path.join(projects_dir(), STATE.project)
            try:
                rows, _order, zones = _scene_state(pdir)
                self._json({"ok": True, "areas": scenes_mod.summarise(rows),
                            "zones": zones})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, 400)
        elif self.path == "/api/fittings":
            if not STATE.project:
                self._json({"ok": False, "error": "No project open."}, 400)
                return
            pdir = os.path.join(projects_dir(), STATE.project)
            try:
                rows, _order, loads = _fitting_state(pdir)
                self._json({"ok": True,
                            "fittings": fittings_mod.summarise(rows, loads),
                            "load_types": fittings_mod.load_type_choices()})
            except Exception as exc:  # noqa: BLE001
                self._json({"ok": False, "error": str(exc)}, 400)
        elif self.path == "/review" and STATE.project:
            p = os.path.join(projects_dir(), STATE.project, "review.html")
            if os.path.exists(p):
                self._send(200, open(p, "rb").read(), "text/html; charset=utf-8",
                           framable_by_self=True)
            else:
                self._send(404, b"no review yet", "text/plain",
                           framable_by_self=True)
        elif self.path == "/template.xlsx":
            from . import template as template_mod
            self._send(200, template_mod.workbook(),
                       "application/vnd.openxmlformats-officedocument."
                       "spreadsheetml.sheet",
                       filename=template_mod.FILENAME)
        elif self.path == "/favicon.ico" or self.path.startswith("/brand/"):
            # The Homeplay marks -- ported from the production app's own
            # assets (web/public/logos), shipped in docs/brand and bundled
            # into the exe with the rest of docs/. Served by an exact-name
            # whitelist: a URL is not a key to the filesystem.
            name = ("favicon.png" if self.path == "/favicon.ico"
                    else self.path[len("/brand/"):])
            if name in ("logomark-black.png", "logomark-white.png",
                        "wordmark-black.png", "wordmark-white.png",
                        "favicon.png", "times-now.woff",
                        "saans-regular.woff2", "saans-semibold.woff2"):
                p = os.path.join(ROOT, "docs", "brand", name)
                if os.path.exists(p):
                    ctype = ("font/woff2" if name.endswith(".woff2")
                             else "font/woff" if name.endswith(".woff")
                             else "image/png")
                    self._send(200, open(p, "rb").read(), ctype)
                    return
            self._send(404, b"not found", "text/plain")
        elif self.path == "/report" and STATE.project:
            p = os.path.join(projects_dir(), STATE.project, "EXTRACTION-REPORT.txt")
            if os.path.exists(p):
                self._send(200, open(p, "rb").read(), "text/plain; charset=utf-8")
            else:
                self._send(404, b"no report yet", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length)

    def _cross_site(self) -> bool:
        """Reject a POST that another website made on the user's behalf.

        The server has no authentication because it is bound to localhost --
        but any page in the same browser can still POST to it. A form or a
        text/plain fetch is exempt from preflight, so requiring JSON forces a
        preflight the server never answers. The Origin check closes the rest,
        and the Host check blocks DNS-rebinding, where a hostile name resolves
        to 127.0.0.1 and the browser treats it as same-origin.
        """
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return True
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(("http://127.0.0.1:", "http://localhost:")):
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host not in ("127.0.0.1", "localhost", "")

    def _bad_host(self) -> bool:
        # DNS-rebinding: a hostile name that resolves to 127.0.0.1 makes the
        # browser treat this app as same-origin. GET serves the review sheet
        # and the report -- the client's whole design -- so it needs the check
        # as much as POST does.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host not in ("127.0.0.1", "localhost", "")

    def do_GET(self) -> None:  # noqa: N802
        if self._bad_host():
            self._send(403, b"forbidden", "text/plain")
            return
        return self._do_GET_inner()

    def do_POST(self) -> None:  # noqa: N802
        if self._cross_site():
            self._json({"ok": False, "error": "This request did not come from the app."}, 403)
            return
        try:
            if self.path == "/api/accept":
                cfg = load_config()
                cfg["accepted_disclaimer"] = True
                save_config(cfg)
                self._json({"ok": True})
            elif self.path == "/api/key":
                data = json.loads(self._body())
                key = (data.get("key") or "").strip()
                if not key.startswith("sk-ant-"):
                    self._json({"ok": False, "error": "That doesn't look like an Anthropic "
                                "API key (they start with sk-ant-)."}, 400)
                    return
                cfg = load_config()
                cfg["api_key"] = key
                save_config(cfg)
                self._json({"ok": True})
            elif self.path == "/api/extract":
                data = json.loads(self._body())
                project = (data.get("project") or "").strip() or "Untitled"
                notes = data.get("notes") or ""
                # Validated against what the app OFFERS, not against PRICING --
                # PRICING still lists withdrawn models so an old project read on
                # one can be costed, and that must not become a back door to
                # reading a new job on a model we withdrew for reading badly.
                model = data.get("model") or ingest.DEFAULT_MODEL
                if model not in {m["id"] for m in ingest.MODEL_CHOICES}:
                    self._json({"ok": False, "error": "Unknown model."}, 400)
                    return
                import base64
                pdfs = []
                for f in data.get("pdfs") or []:
                    raw = base64.b64decode(f.get("data") or "")
                    if not raw.startswith(b"%PDF"):
                        self._json({"ok": False, "error":
                                    f"{f.get('name')} doesn't look like a PDF."}, 400)
                        return
                    # Reading a document as words rather than pages is always
                    # an explicit per-file choice -- never inferred from the
                    # file's size, name or content. A drawing yields
                    # extractable text too, so guessing would quietly throw
                    # away the picture that IS the drawing.
                    pdfs.append((f.get("name") or "plans.pdf", raw,
                                 bool(f.get("text_only"))))
                if not pdfs:
                    self._json({"ok": False, "error": "Choose at least one PDF."}, 400)
                    return
                # The keypad family is deliberately NOT remembered: drawings almost
                # never specify it, and shipping a project in the wrong hardware is
                # expensive, so the choice is made consciously every time
                # (James, 2026-08-04). "" means "let the AI choose and say which".
                family = (data.get("keypad_family") or "").strip()
                if family == "__ai__":
                    family = ""
                elif family == ingest.NO_KEYPADS:
                    pass          # place them by hand; scenes are still written
                elif family and family not in ingest.keypad_families():
                    self._json({"ok": False, "error": "Unknown keypad family."}, 400)
                    return
                elif not family and not data.get("keypad_family"):
                    self._json({"ok": False, "error":
                                "Choose a keypad family first."}, 400)
                    return
                # Fixture research defaults ON: a blank wattage makes circuit
                # loading and panel sizing worthless, so the expensive choice
                # is the safe default and turning it off is deliberate.
                research = data.get("research")
                research = True if research is None else bool(research)
                cfg = load_config()
                cfg["model"] = model      # remember the choice as the new default
                cfg["research"] = research
                save_config(cfg)
                ok = _background("extracting", do_extract, pdfs, project, notes,
                                 model, family, research)
                self._json({"ok": ok} if ok else
                           {"ok": False, "error": "Something is already running."}, 200 if ok else 409)
            elif self.path == "/api/sheet/read":
                from . import sheets as sheets_mod
                data = json.loads(self._body())
                import base64
                name = (data.get("name") or "schedule.csv").strip()
                try:
                    raw = base64.b64decode(data.get("data") or "")
                except Exception:  # noqa: BLE001 -- a bad upload is a user error
                    self._json({"ok": False, "error": "That file could not be read."}, 400)
                    return
                if not raw:
                    self._json({"ok": False, "error": "That file is empty."}, 400)
                    return
                if name.lower().endswith(".dwg"):
                    # A drawing is turned into the rows a spreadsheet would have
                    # had, and then goes through exactly the same mapping screen
                    # and conversion as every other way in. A second import path
                    # would be a second place for the counts to go wrong.
                    import tempfile

                    from . import dwgplan
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".dwg",
                                                         delete=False) as tmp:
                            tmp.write(raw)
                            temp_path = tmp.name
                        try:
                            plan = dwgplan.read(temp_path)
                        finally:
                            os.unlink(temp_path)
                    except dwgplan.PlanError as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                        return
                    with STATE.lock:
                        STATE.sheet_upload += 1
                        STATE.sheet = {"filename": name,
                                       "tables": [("Read from the drawing",
                                                   dwgplan.to_rows(plan))],
                                       "plan_report": dwgplan.report(plan),
                                       "upload": STATE.sheet_upload}
                    tables = STATE.sheet["tables"]
                else:
                    try:
                        tables = sheets_mod.read_tables(name, raw)
                    except sheets_mod.SheetError as exc:
                        self._json({"ok": False, "error": str(exc)}, 400)
                        return
                    # A workbook shaped like our own template -- fittings on one
                    # sheet, circuits on another -- is joined by the fitting
                    # code into the single table the conversion already takes,
                    # and offered as an extra sheet. The user still sees every
                    # column and can still correct the mapping; the join is not
                    # a second import path, it is a preparation step.
                    joined = sheets_mod.detect_pair(tables)
                    if joined:
                        tables = list(tables) + [joined["table"]]
                    with STATE.lock:
                        STATE.sheet_upload += 1
                        STATE.sheet = {"filename": name, "tables": tables,
                                       "join_notes": (joined or {}).get("notes", []),
                                       "suggested": (joined or {}).get("index"),
                                       "upload": STATE.sheet_upload}
                out = []
                for i, (sheet_name, rows) in enumerate(tables):
                    header = sheets_mod.find_header(rows)
                    headings = rows[header] if header < len(rows) else []
                    # 30 rows, matching the window find_header searches -- a header
                    # the user cannot see is a header they cannot click.
                    width = max((len(r) for r in rows[:30]), default=0)
                    out.append({
                        "index": i,
                        "name": sheet_name,
                        "header": header,
                        "rows": len(rows),
                        # A preview of the file as it actually parsed. It is the
                        # only way a user can tell a mis-detected header row
                        # from a correct one before committing to it.
                        "preview": [(r + [""] * width)[:width] for r in rows[:30]],
                        "width": width,
                        "headings": headings,
                        "mapping": sheets_mod.guess_mapping(headings),
                    })
                self._json({"ok": True, "filename": name, "sheets": out,
                            "upload": STATE.sheet["upload"],
                            "fields": sheets_mod.FIELDS,
                            # What a drawing could not tell us. Written on the
                            # rows as well, but a reader who trusts the table
                            # will not read 40 Notes cells to find out that the
                            # room names are the weak part.
                            "plan_report": STATE.sheet.get("plan_report", []),
                            # A two-sheet template is joined for the user; say
                            # so, and open the joined sheet rather than leaving
                            # it to the "last sheet" heuristic, which on our own
                            # template landed on the guidance page.
                            "join_notes": STATE.sheet.get("join_notes", []),
                            "suggested": STATE.sheet.get("suggested")})
            elif self.path == "/api/sheet/guess":
                # Moving the header row changes what every column is called, so
                # the guess has to be made again -- against the row the user
                # just pointed at, by the same code that made the first one.
                from . import sheets as sheets_mod
                data = json.loads(self._body())
                held = STATE.sheet
                if not held:
                    self._json({"ok": False, "error": "Choose the spreadsheet again."}, 409)
                    return
                if data.get("upload") != held.get("upload"):
                    # The screen asking was drawn for a different upload --
                    # another browser tab, or an earlier attempt. Answering
                    # from the file now held would hand back a mapping for a
                    # sheet the user is not looking at.
                    self._json({"ok": False, "error": "A different file has been "
                                "read since this screen was drawn -- choose the "
                                "spreadsheet again."}, 409)
                    return
                try:
                    table, header_row = int(data.get("table") or 0), int(data.get("header") or 0)
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "Bad sheet selection."}, 400)
                    return
                if not 0 <= table < len(held["tables"]):
                    self._json({"ok": False, "error": "No such sheet."}, 400)
                    return
                rows = held["tables"][table][1]
                if not 0 <= header_row < len(rows):
                    self._json({"ok": False, "error": "That row is not in the sheet."}, 400)
                    return
                headings = rows[header_row]
                self._json({"ok": True, "headings": headings,
                            "mapping": sheets_mod.guess_mapping(headings)})
            elif self.path == "/api/sheet/import":
                from . import sheets as sheets_mod
                data = json.loads(self._body())
                held = STATE.sheet
                if not held:
                    self._json({"ok": False, "error": "Choose the spreadsheet again -- "
                                "it is no longer loaded."}, 409)
                    return
                if data.get("upload") != held.get("upload"):
                    # Same guard as the guess: importing the held file with a
                    # mapping drawn for a different one writes a complete,
                    # believable schedule with every column read as the wrong
                    # thing -- silently.
                    self._json({"ok": False, "error": "A different file has been "
                                "read since this screen was drawn -- choose the "
                                "spreadsheet again."}, 409)
                    return
                try:
                    table = int(data.get("table") or 0)
                    header_row = int(data.get("header") or 0)
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "Bad sheet selection."}, 400)
                    return
                if not 0 <= table < len(held["tables"]):
                    self._json({"ok": False, "error": "No such sheet."}, 400)
                    return
                rows = held["tables"][table][1]
                if not 0 <= header_row < len(rows):
                    self._json({"ok": False, "error": "That header row is not in the sheet."}, 400)
                    return
                mapping = {}
                for key, col in (data.get("mapping") or {}).items():
                    if key not in sheets_mod.FIELD_KEYS or col in (None, "", -1, "-1"):
                        continue
                    try:
                        mapping[key] = int(col)
                    except (TypeError, ValueError):
                        continue
                problems = sheets_mod.mapping_problems(mapping)
                if problems:
                    self._json({"ok": False, "error": " ".join(problems)}, 400)
                    return
                project = (data.get("project") or "").strip() or "Untitled"
                ok = _background("importing", do_import_sheet, project, table,
                                 header_row, mapping, bool(data.get("fill_down")),
                                 bool(data.get("wattage_is_total")))
                self._json({"ok": ok} if ok else
                           {"ok": False, "error": "Something is already running."},
                           200 if ok else 409)
            elif self.path == "/api/select":
                data = json.loads(self._body())
                name = data.get("project") or ""
                if STATE.busy:
                    # A build in flight finishes by stamping its output onto
                    # whatever project is selected THEN -- switching mid-build
                    # hangs project A's file on project B.
                    self._json({"ok": False, "error": "Wait for the current job to "
                                "finish before opening another project."}, 409)
                    return
                try:
                    # Same confinement as creating one: "..", separators and
                    # absolute paths must not select a folder outside the
                    # projects directory, which is then served and opened.
                    candidate = _project_dir(name, create=False)
                except ValueError:
                    self._json({"ok": False, "error": "No such project."}, 404)
                    return
                if os.path.isdir(candidate):
                    STATE.project = os.path.basename(candidate)
                    STATE.hw_path = None
                    # Otherwise the Check screen shows the tokens and cost of the
                    # PREVIOUS project's read as if they belonged to this one.
                    STATE.last_extract = None
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "No such project."}, 404)
            elif self.path in ("/api/scenes/copy", "/api/scenes/edit"):
                if not STATE.project:
                    self._json({"ok": False, "error": "No project open."}, 400)
                    return
                if STATE.busy:
                    self._json({"ok": False, "error": "Wait for the current job to "
                                "finish -- it is reading or writing these files."}, 409)
                    return
                pdir = os.path.join(projects_dir(), STATE.project)
                try:
                    data = json.loads(self._body())
                    if not isinstance(data, dict):
                        raise ValueError("Malformed request.")
                    with SCENE_LOCK:      # two saves at once would race on the file
                        # The busy check is re-taken inside the lock: a build can
                        # start between the check above and getting here.
                        if STATE.busy:
                            raise ValueError("A job started just now -- try again "
                                             "when it has finished.")
                        rows, order, zones = _scene_state(pdir)
                        if self.path.endswith("copy"):
                            areas = [str(t).strip() for t in (data.get("targets") or [])]
                            before = {a: scenes_mod.names_in(rows, a) for a in areas}
                            rows, notes = scenes_mod.copy_scenes(
                                rows, str(data.get("source") or ""), areas, zones)
                            # Replacing a room's scenes strands any button that
                            # recalled one of the old names.
                            moves = [(a, {}, before[a] - scenes_mod.names_in(rows, a))
                                     for a in areas if a in before]
                        else:
                            area = str(data.get("area") or "")
                            edits = list(data.get("edits") or [])
                            rows, notes = scenes_mod.apply_edits(rows, area, edits, zones)
                            renamed, gone = scenes_mod.edit_moves(edits)
                            moves = [(area, renamed, gone)]
                        # A copy can also strip keypad buttons in the rooms it
                        # lands in. Asking first turns that from something the
                        # engineer is told about afterwards into a decision.
                        preview = bool(data.get("preview"))
                        if preview:
                            notes = notes + _retarget(pdir, moves, dry=True)
                        else:
                            # Scenes FIRST. Buttons.csv used to be committed
                            # before the scenes were validated, so a scene write
                            # that failed left the app saying "nothing changed"
                            # while buttons had already been renamed or deleted
                            # -- and the next build was unbuildable.
                            _write_scenes(pdir, STATE.project, rows, order)
                            try:
                                moved = _retarget(pdir, moves)
                            except Exception:
                                _restore_scenes(pdir)     # put the scenes back
                                # The sheet was rendered from the rows just
                                # rolled back; left alone it shows an edit
                                # that no longer exists.
                                try:
                                    _render_review(pdir, STATE.project)
                                except Exception:  # noqa: BLE001, S110
                                    pass
                                raise
                            notes = notes + moved
                            # _write_scenes rendered the sheet BEFORE the
                            # buttons were retargeted, so it still showed the
                            # old button targets -- while Build would write
                            # the new ones. Render again from the final files.
                            if moved:
                                _render_review(pdir, STATE.project)
                            # The schedules just changed, so any .hw already
                            # built from them is out of date. Leaving it
                            # advertised means "Open in Lutron Designer" opens
                            # the pre-edit file.
                            STATE.hw_path = None
                    self._json({"ok": True, "notes": notes, "preview": preview})
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
            elif self.path == "/api/fittings/edit":
                if not STATE.project:
                    self._json({"ok": False, "error": "No project open."}, 400)
                    return
                if STATE.busy:
                    self._json({"ok": False, "error": "Wait for the current job to "
                                "finish -- it is reading or writing these files."}, 409)
                    return
                pdir = os.path.join(projects_dir(), STATE.project)
                try:
                    data = json.loads(self._body())
                    if not isinstance(data, dict):
                        raise ValueError("Malformed request.")
                    # The same lock as the scene editor: both rewrite files the
                    # other one reads, and two saves at once would race.
                    with SCENE_LOCK:
                        if STATE.busy:
                            raise ValueError("A job started just now -- try again "
                                             "when it has finished.")
                        rows, order, _loads = _fitting_state(pdir)
                        rows, notes = fittings_mod.apply_edits(
                            rows, list(data.get("edits") or []))
                        _write_table(pdir, STATE.project, "FixturesCatalog.csv",
                                     rows, order)
                        # Wattage and dimming type change what gets built, so a
                        # .hw made before this edit is now the wrong file.
                        STATE.hw_path = None
                    self._json({"ok": True, "notes": notes})
                    return
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": str(exc)}, 400)
                    return
            elif self.path == "/api/reload":
                if not STATE.project:
                    self._json({"ok": False, "error": "No project open."}, 400)
                    return
                pdir = os.path.join(projects_dir(), STATE.project)
                try:
                    _render_review(pdir, STATE.project)
                    STATE.hw_path = None      # the CSVs were edited underneath it
                    self._json({"ok": True})
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": str(exc)}, 400)
            elif self.path == "/api/build":
                if not STATE.project:
                    self._json({"ok": False, "error": "No project open."}, 400)
                    return
                # A failed or missing extraction leaves a project folder with
                # no schedules. Every CSV then reads as an empty list, an empty
                # schedule validates, and the "build" succeeds -- writing a
                # file with none of the rooms in it. The review sheet is the
                # gate, so its absence means there is nothing checked to build.
                pdir_b = os.path.join(projects_dir(), STATE.project)
                if not os.path.exists(os.path.join(pdir_b, "review.html")):
                    self._json({"ok": False, "error":
                                "This project has no schedules to build -- read "
                                "some plans first, and check the review sheet."}, 400)
                    return
                # A read can also come back with rooms and NOTHING in them --
                # a real Sonnet 5 read of House A returned 49 rooms and zero
                # circuits, having failed to resolve the plan symbols. That has
                # a review sheet, so the check above passes it through.
                if not _has_anything_to_build(pdir_b):
                    self._json({"ok": False, "error":
                                "This project has no circuits and no keypads, so "
                                "there is nothing to build. Open the extraction "
                                "report -- the AI may not have been able to read "
                                "the plans."}, 400)
                    return
                ok = _background("building", do_build, STATE.project)
                self._json({"ok": ok} if ok else
                           {"ok": False, "error": "Something is already running."}, 200 if ok else 409)
            elif self.path == "/api/open":
                data = json.loads(self._body())
                what = data.get("what")
                if not STATE.project:
                    self._json({"ok": False, "error": "No project open."}, 400)
                    return
                pdir = os.path.join(projects_dir(), STATE.project)
                target = {"folder": pdir,
                          "hw": STATE.hw_path or pdir,
                          "report": os.path.join(pdir, "EXTRACTION-REPORT.txt")}.get(what, pdir)
                if what == "hw" and STATE.hw_path:
                    self._json({"ok": True, "note": open_hw(target)})
                    return
                open_path(target)
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": str(exc)}, 500)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lutron Builder</title>
<link rel="icon" type="image/png" href="/brand/favicon.png">
<style>
/* Brand tokens, from the design contract (colors_and_type.css): paper, mist,
   ink and the olive accent. Times Now for display, Saans for everything else. */
:root{
  --bg:#faf9f7; --panel:#ffffff; --ink:#111111; --muted:#6d7060;
  --accent:#707052; --accent-ink:#ffffff; --line:#e7e4dd;
  --good:#3e6b3e; --bad:#8c3a2e; --mono:ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root{
  --bg:#1c1d18; --panel:#25261f; --ink:#e8e7dd; --muted:#9b9e8c;
  --accent:#9a9a74; --accent-ink:#1c1d18; --line:#3a3b30;
  --good:#8fbf8f; --bad:#e0836f;
}}
@font-face{font-family:'Saans';src:url(/brand/saans-regular.woff2) format('woff2');
  font-weight:400;font-display:swap}
@font-face{font-family:'Saans';src:url(/brand/saans-semibold.woff2) format('woff2');
  font-weight:600;font-display:swap}
*{box-sizing:border-box;margin:0}
/* calt off per the brand CSS -- Saans ships contextual alternates the
   guidelines disable. */
body{background:var(--bg);color:var(--ink);font:15px/1.55 'Saans',system-ui,"Segoe UI",sans-serif;
     font-feature-settings:'calt' 0;min-height:100vh;display:flex;flex-direction:column}
header{display:flex;align-items:baseline;gap:12px;padding:20px 28px;border-bottom:1px solid var(--line)}
header h1{font-size:20px;font-weight:650;letter-spacing:.2px}
header .credit{color:var(--muted);font-size:12.5px}
/* The Homeplay marks, black on the light theme and white on the dark one --
   two <img>s and a display swap, because a PNG cannot recolour itself. The
   star sits bottom-left (James, 08-12: top-left read as clutter and detracted
   from the wordmark); the wordmark stays in the header. */
footer img.mark{width:60px;height:60px;object-fit:contain;flex:none}
/* The same visual gap either side of "by": the header flex gap is 12px, so
   the wordmark sits 11px off the word (James, 08-12 -- the single text space
   made "by HOMEPLAY" read as one word against the wide gap before it). */
header .credit img{height:10px;object-fit:contain;vertical-align:-1px;margin-left:11px}
.dark-mark{display:none}
@media (prefers-color-scheme: dark){
  .light-mark{display:none}
  .dark-mark{display:inline-block}
}
/* Times Now for the key headings -- the brand display face, served with the
   marks. Single semi-light weight, so headings drop the synthetic bold and
   take a size step up instead. Falls back to an ordinary serif if the font
   file is ever absent. */
@font-face{font-family:'Times Now';src:url(/brand/times-now.woff) format('woff');
  font-weight:300 700;font-display:swap}
h1,h2{font-family:'Times Now',Georgia,'Times New Roman',serif;letter-spacing:-0.02em}
header h1{font-size:27px;font-weight:400}
h2{font-size:23px;font-weight:400}
main{flex:1;display:grid;grid-template-columns:210px 1fr;gap:0;max-width:1200px;width:100%;margin:0 auto}
nav{border-right:1px solid var(--line);padding:26px 0}
/* The four stages in caps -- the caption idiom from the brand guidelines:
   Saans semibold, small, tracked wide. */
nav .step{padding:11px 22px;color:var(--muted);cursor:default;border-left:3px solid transparent;
  font-size:11px;font-weight:600;letter-spacing:.14em;text-transform:uppercase}
nav .step.go{cursor:pointer}
nav .step.go:hover{color:var(--ink)}
nav .step.on{color:var(--ink);border-left-color:var(--accent);font-weight:600}
nav .step.done{color:var(--ink)}
nav .step.done::after{content:" \\2713";color:var(--good)}
section{padding:30px 34px;max-width:760px}
h2{font-size:17px;font-weight:650;margin-bottom:6px}
p.lead{color:var(--muted);margin-bottom:18px;max-width:58ch}
label{display:block;font-size:13px;font-weight:600;margin:14px 0 4px}
input[type=text],input[type=password],textarea{width:100%;padding:9px 11px;border:1px solid var(--line);
  border-radius:6px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:74px;resize:vertical}
button{appearance:none;border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);
  padding:9px 18px;border-radius:6px;font:600 14px 'Saans',system-ui,"Segoe UI",sans-serif;cursor:pointer;margin-top:16px}
button.ghost{background:transparent;color:var(--accent)}
button:disabled{opacity:.45;cursor:default}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.log{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;
  font:12.5px/1.6 var(--mono);white-space:pre-wrap;max-height:320px;overflow:auto;margin-top:16px}
.err{color:var(--bad);font-weight:600}
.note{font-size:12.5px;color:var(--muted);margin-top:8px;max-width:58ch}
.filebox{border:1.5px dashed var(--line);border-radius:8px;padding:22px;text-align:center;
  color:var(--muted);margin-top:14px;cursor:pointer}
.filebox.has{border-color:var(--accent);color:var(--ink);text-align:left;padding:12px 14px}
.frow{display:flex;justify-content:space-between;align-items:center;gap:14px;
  padding:5px 0;cursor:default;font-size:13.5px}
.frow+.frow{border-top:1px solid var(--line)}
iframe{width:100%;height:520px;border:1px solid var(--line);border-radius:8px;background:#fff;margin-top:14px}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;
  background:var(--panel);border:1px solid var(--line);color:var(--muted)}
.pill.busy{color:var(--accent)}
/* The brand eyebrow: a tracked-caps line ABOVE the heading, not a pill
   crammed beside it (James, 08-12 -- "just looks bad... give it some space
   to breathe"). Olive for the good news, the warning colour for the
   experimental routes. */
.eyebrow{font:600 12px 'Saans',system-ui,sans-serif;letter-spacing:.18em;
  text-transform:uppercase;color:var(--accent);margin:26px 0 10px}
.eyebrow.trial{color:var(--bad)}
/* Step 1 read as a toll gate: most people use the spreadsheet route and
   never need a key at all (James, 08-13). Say so where they see it first. */
nav .step .opt{display:block;font-size:9.5px;letter-spacing:.1em;opacity:.75;
  font-weight:400;margin-top:2px}
.pill.trial{background:transparent;border-color:var(--bad);color:var(--bad)}
.prog{margin-top:16px;padding:12px 16px;border:1px solid var(--accent);border-radius:8px;
  background:var(--panel);font-size:13.5px;display:flex;align-items:center;gap:10px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);flex:none;
  animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.25;transform:scale(.8)}50%{opacity:1;transform:scale(1.1)}}
@media (prefers-reduced-motion: reduce){.dot{animation:none}}
a{color:var(--accent)}
</style></head><body>
<header>
<h1>Lutron Builder</h1>
<span class="credit">by <img class="light-mark" src="/brand/wordmark-black.png" alt="Homeplay"><img class="dark-mark" src="/brand/wordmark-white.png" alt="Homeplay"></span>
<span id="pill" class="pill" style="margin-left:auto">idle</span></header>
<main>
<nav>
  <div class="step" data-s="plans">1 &nbsp;Plans</div>
  <div class="step" data-s="check">2 &nbsp;Check</div>
  <div class="step" data-s="build">3 &nbsp;Build</div>
</nav>
<section id="content"></section>
</main>
<footer style="padding:10px 28px;border-top:1px solid var(--line);color:var(--muted);font-size:11.5px;display:flex;align-items:center;gap:12px">
<img class="mark light-mark" src="/brand/logomark-black.png" alt="Homeplay">
<img class="mark dark-mark" src="/brand/logomark-white.png" alt="Homeplay">
<span>Provided free of charge, as is &mdash; check everything against the drawings; all output and its use are your responsibility.</span>
<span style="margin-left:auto">__APP_VERSION__</span>
</footer>
<script>
let S=null, view=null, files=[], famChosen='';
// The spreadsheet route. `source` is which of the two ways into step 2 the
// user is looking at; `sheet` is the parsed workbook the server is holding.
// upload is the number the server gave that workbook: guess and import quote
// it, and the server refuses them once another upload has replaced it -- so
// a second browser tab cannot silently hijack the mapping screen here.
// sheetSeq counts the requests this page itself has made: a reply that is
// not from the newest request is thrown away, because applying it would
// stamp the mapping of one sheet onto whichever sheet is now on screen.
// Opens on the spreadsheet route: it is the one that is solid, free and needs
// no key, and the page said so in words while landing you on the AI one
// (James, 08-13).
let source='sheet', sheet=null, sheetIdx=0, headerRow=0, colMap={},
    fillDown=true, wattTotal=false, upload=null, sheetSeq=0;
// The Content-Type is NOT decoration: _cross_site() rejects any POST that is
// not application/json, because that is what forces a CORS preflight and stops
// another website posting here on behalf of whoever is logged in. A fetch with
// a plain string body
// defaults to text/plain, so omitting this header 403s every single button in
// the app while the page still renders perfectly. It shipped that way in 1.1.2.
async function api(p,body){const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json()}
// Opening the folder, the report or the .hw can fail (no association, file
// gone). Fire-and-forget made those buttons look dead; say what went wrong.
async function openIt(w){const r=await api('/api/open',{what:w});
  if(!r.ok){alert('Could not open it:\\n\\n'+r.error);return}
  // Designer cannot be handed a file, so the button reports what it did and
  // what to click next -- silence there is what made it look broken.
  if(r.note)alert(r.note)}
function escHTML(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function h(html){document.getElementById('content').innerHTML=html}
// Project names come from folder names on disk. Rather than build a JS string
// literal inside an HTML attribute inside a Python string -- three layers of
// escaping, and one bug away from an injection -- carry the value as data and
// read it back in one delegated listener.
document.addEventListener('click',function(e){
  const a=e.target.closest&&e.target.closest('a[data-project]');
  if(a){e.preventDefault();reopen(a.getAttribute('data-project'))}
});
// Which stage the user is looking at, and whether they chose it from the step
// bar. A read or a build polls every 1.2s and repaints its own panel; without
// `pinned` that repaint would drag them straight back out of the stage they
// just clicked into.
let panel=null, pinned=false;
function canGo(s){
  if(!S) return false;
  if(s==='key'||s==='plans') return true;
  return !!S.review_ready;          // check and build need schedules to exist
}
function go(s){
  panel=s;
  if(s==='plans')vStep2();
  else if(s==='check')vCheck(); else vBuild();
}
document.addEventListener('click',function(e){
  const st=e.target.closest&&e.target.closest('.step');
  if(!st) return;
  const s=st.dataset.s;
  if(!canGo(s)) return;
  pinned=true; go(s);
});
function setNav(cur){
  panel=cur;
  const done={plans:S.report_ready, check:S.review_ready, build:!!S.hw_path};
  document.querySelectorAll('.step').forEach(el=>{
    el.classList.toggle('on', el.dataset.s===cur);
    el.classList.toggle('done', !!done[el.dataset.s] && el.dataset.s!==cur);
    el.classList.toggle('go', canGo(el.dataset.s) && el.dataset.s!==cur);
  });
  document.getElementById('pill').textContent=S.busy?S.stage+'\\u2026':'idle';
  document.getElementById('pill').classList.toggle('busy',S.busy);
}
function logBlock(){
  let prog='';
  if(S.busy&&S.progress){
    const P=S.progress;
    const secs=Math.max(0,Math.round(Date.now()/1000-P.started));
    const mm=Math.floor(secs/60), ss=String(secs%60).padStart(2,'0');
    // Three real stages, reported by the API itself — never invented:
    // upload accepted -> model reading -> schedules arriving.
    let what;
    if(P.phase==='uploading'){
      what=`uploading ${P.upload_mb||'the'} MB of drawings to Anthropic\u2026`;
    }else if(P.chars>0||P.phase==='writing'||P.phase==='reading_done'){
      const toks=P.input_tokens?`${P.input_tokens.toLocaleString()} tokens read off the sheets \u2014 `:'';
      const srch=P.searches?` (${P.searches} fixture lookup${P.searches===1?'':'s'} so far)`:'';
      what=`${toks}Claude is writing the schedules${srch}: ${(P.chars||0).toLocaleString()} characters so far`;
    }else if(P.phase==='researching'){
      what=`looking up fixture data the drawings don't state \u2014 ${P.searches} search${P.searches===1?'':'es'} so far`;
    }else{
      const last=S.last_read_stats&&S.last_read_stats.first_text_s
        ?` Last read on this machine: first text after ${Math.floor(S.last_read_stats.first_text_s/60)}m ${String(S.last_read_stats.first_text_s%60).padStart(2,'0')}s, finished in ${Math.floor(S.last_read_stats.total_s/60)}m.`
        :'';
      what=`sheets uploaded and accepted \u2014 Claude reads the whole set before writing anything, so this stage is silent by nature.${last}`;
    }
    // Liveness: the stream stamps every event it receives, so silence has a
    // freshness label instead of looking like a hang.
    let alive='';
    if(P.signal){
      const ago=Math.max(0,Math.round(Date.now()/1000-P.signal));
      alive=ago<8?' <span class="sub">\u00b7 connection live</span>'
           :` <span class="sub">\u00b7 last signal ${ago}s ago</span>`;
    }
    prog=`<div class="prog"><span class="dot"></span> ${what} &nbsp;(${mm}:${ss})${alive}</div>`;
    document.title=`${mm}:${ss} \u2014 Lutron Builder`;
  } else if(S.busy){
    prog='<div class="prog"><span class="dot"></span> working&hellip;</div>';
  } else {
    document.title='Lutron Builder';
  }
  if(!S.log.length) return prog;
  const err=S.error?'<div class="err">'+escHTML(S.error)+'</div>':'';
  return prog+'<div class="log">'+S.log.map(escHTML).join('\\n')+'</div>'+err;
}
// The key stopped being step 1 (James, 08-13): it gated a tool that is free
// for the two routes most people use, and it was the reason the app opened on
// the PDF tab. It now lives on the one screen that needs it, and nothing else
// waits for it.
function keyBlock(){
  if(S.key_set)return `<p class="note">Using the API key saved on this computer.
    <a href="#" onclick="return vSettings()">Replace it</a></p>`;
  return `<div style="border:1px solid var(--accent);border-radius:8px;padding:16px 18px;margin-top:18px;max-width:60ch">
    <div class="eyebrow" style="margin-top:0">Needed for this route only</div>
    <b style="color:var(--ink)">An Anthropic API key</b>
    <p class="note" style="margin-top:6px">Reading a picture is the one job only AI can do, so this
    route runs on your own key and is billed to your own account &mdash; about $1.35 for a house.
    The spreadsheet and DWG routes need none of this.</p>
    <ol style="color:var(--muted);font-size:13px;padding-left:18px;margin-top:8px">
    <li>Create an account at <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a></li>
    <li>Add a few pounds of credit under <b>Plans &amp; Billing</b></li>
    <li>Create a key under <b>API Keys</b> and paste it here</li></ol>
    <label for="k">API key</label>
    <input id="k" type="password" placeholder="sk-ant-...">
    <p class="note">Stored only on this computer, in your user folder &mdash; treat it like a
    saved password.</p>
    <button onclick="saveKey()">Save the key</button>
  </div>`;
}
function vSettings(){
  h(`<h2>Your Anthropic API key</h2>
  <p class="lead">Only the PDF route uses this. Replacing it affects nothing else.</p>
  <label for="k">API key</label>
  <input id="k" type="password" placeholder="sk-ant-..." value="">
  <div class="row"><button onclick="saveKey()">Save the key</button>
  <button class="ghost" onclick="vStep2()">Back</button></div>`);
  return false;
}
// Shown once, on the very first run: what this needs to work at all. A
// friend on a Mac with no Designer would otherwise get all the way to the
// last button before finding out (James, 08-13).
function vFirstRun(){
  h(`<div class="eyebrow">Before you start</div>
  <h2>What this needs</h2>
  <div style="border:1px solid var(--accent);border-radius:8px;padding:16px 18px;max-width:62ch">
  <p><b style="color:var(--ink)">Windows, and Lutron Designer installed on the same machine.</b>
  Writing the .hw file uses the database engine that Designer installs, so the last step only
  works where Designer lives. On a Mac that means Windows in Parallels (or similar) with
  Designer inside it &mdash; the Mac side alone cannot write the file.</p>
  <p class="note">Everything before that &mdash; reading a schedule, checking the review sheet,
  correcting it &mdash; runs anywhere.</p>
  </div>
  <div style="border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-top:14px;font-size:12.5px;color:var(--muted);max-width:62ch"><b style="color:var(--ink)">Please read:</b> ${S.disclaimer}</div>
  <button onclick="acceptFirstRun()">I understand &mdash; continue</button>`);
}
async function acceptFirstRun(){await api('/api/accept',{});await refresh(false);vStep2()}
async function saveKey(){
  const k=document.getElementById('k').value;
  const r=await api('/api/key',{key:k});
  if(r.ok){await api('/api/accept',{});await refresh(false);vPlans()}else alert(r.error);
}
function vPlans(){
  setNav('plans');
  const busy=S.busy&&S.stage==='extracting';
  // Price every model against the SAME token profile -- the last real read on
  // this machine where there is one, so the comparison is the engineers own work.
  const est=(S.estimates&&(S.research?S.estimates.on:S.estimates.off))||[];
  const theModel=S.models[0]||{};
  const theEst=est.find(x=>x.id===theModel.id);
  const estLine=theEst?` &mdash; this job is about <b style="color:var(--ink)">$${theEst.usd.toFixed(2)}</b> on it`:'';
  // Built out here, never nested inside the template literal below: a nested
  // one defeats the page test that catches unterminated strings, and that test
  // is all that stands between a stray quote and a dead interface.
  const estSrc = S.estimates_measured
    ? 'Figures above are your <b style="color:var(--ink)">last read on this machine</b>, repriced for each model &mdash; so they reflect drawings the size of yours.'
    : 'Figures above are a rough estimate for a five-sheet house; after your first read they are replaced by your own measured figures.';
  const estNote = est.length
    ? '<p class="note">' + estSrc + ' A bigger drawing set costs more; fixture research is the other half of it.</p>'
    : '';
  // Why there is nothing to choose. Collapsed by default -- the answer to
  // "which model?" is short, and the evidence is there for anyone who asks.
  const modelNote =
    '<details style="margin-top:8px"><summary style="cursor:pointer;font-size:12.5px;color:var(--muted)">Why only this model?</summary>'
    + '<div class="note" style="margin-top:8px;max-width:62ch">'
    + (S.model_note||'').split('\\n\\n').map(p=>'<p style="margin-bottom:7px">'+escHTML(p)+'</p>').join('')
    + '</div></details>';
  const rsrNote = S.research
    ? '<p class="note"><b style="color:var(--ink)">On (recommended).</b> Drawings routinely name a fitting &mdash; "iGuzzini Laser Blade XS" &mdash; without saying what it draws. Claude looks up the manufacturer\\u2019s own figure and records the source beside it on the review sheet, so you can check every one. Wattage is what sizes your circuits and panels, so a blank there makes the load totals meaningless. <b style="color:var(--ink)">This is the expensive part of a read</b> &mdash; see below.</p>'
    : '<p class="note"><b style="color:var(--ink)">Off.</b> Only what the drawings themselves state is used. A fitting with no stated wattage arrives <b style="color:var(--ink)">blank and flagged</b> in the report for you to fill in by hand &mdash; nothing is guessed, because a wrong wattage can overload a dimmer. Cheaper and quicker: the right choice when the drawings carry a full fixture schedule already, or when you want the rooms, circuits and scenes now and will do the electrical data yourself.</p>';
  // Built here rather than inline: a template literal nested inside another one
  // defeats the page test that catches unterminated strings, and that test is
  // the only thing standing between a stray quote and a dead interface.
  // One row per chosen file, each with its own "read as text" tick. Built out
  // here rather than nested inside the big template literal below -- a nested
  // one defeats the page test that catches unterminated strings.
  //
  // stopPropagation on the row is load-bearing: the whole box is a click
  // target that re-opens the file picker, and without it ticking a box would
  // wipe the selection instead of setting the choice.
  const fileRows = files.map(function(f,i){
    return '<label class="frow" onclick="event.stopPropagation()">'
      + '<span>' + escHTML(f.name)
      + ' <span style="color:var(--muted);font-weight:400">('
      + (f.size/1048576).toFixed(1) + ' MB)</span></span>'
      + '<span style="white-space:nowrap;font-weight:400">'
      + '<input type="checkbox" ' + (f.text_only?'checked':'')
      + ' onchange="setTextOnly(' + i + ',this.checked)"> read as text</span>'
      + '</label>';
  }).join('');
  // Only shown once something is chosen: with an empty box the option has
  // nothing to apply to, and the explanation is noise on first use.
  const textNote = files.length
    ? '<p class="note"><b style="color:var(--ink)">Leave "read as text" unticked for drawings.</b> A drawing IS a picture &mdash; reading one as text would keep only its dimension strings and throw the plan away. Tick it for a document that is <i>words</i>: a lighting specification, a fixture schedule, a circuit estimate. Those cost a small fraction as text, and a specification too large to send as pages can still be read this way. This is never decided for you, because a drawing has extractable text in it too.</p>'
    : '';
  const kpNote = famChosen==='__none__'
    ? `<p class="note">No keypads are written &mdash; you place them in Designer yourself. The scenes for every room are still worked out in full, so they are there waiting to be bound to buttons.</p>`
    : `<p class="note">Used everywhere the drawings don't say otherwise. Where they DO name a different family for a room &mdash; or you say so in the notes above &mdash; that wins, and the report lists every room where it differed. The AI still picks each keypad's size from the scenes a room needs.</p>`;
  h(sourceTabs()+`<div class="eyebrow trial">Experimental</div><h2>PDF Plans &mdash; read by AI</h2>
  <p class="lead">Pick the PDF(s) of the lighting plans &mdash; several sheets are read together
  as one drawing set. Claude reads the legend, the rooms and the circuits, works out the
  scenes each room needs, and writes the schedules for you to check.</p>
  <div class="filebox${files.length?' has':''}" onclick="document.getElementById('f').click()">
    ${files.length?fileRows:'Click to choose one or more PDFs'}
  </div>
  ${textNote}
  <input id="f" type="file" accept="application/pdf" multiple style="display:none" onchange="pick(this)">
  <label for="pn">Project name</label><input id="pn" type="text" placeholder="e.g. 14 Orchard Lane" oninput="projName=this.value" value="${escAttr(projName)}">
  <label for="nt">Notes for the AI <span style="color:var(--muted);font-weight:400">(optional &mdash; anything the drawings don't say)</span></label>
  <textarea id="nt" placeholder="e.g. all downlights are mains-dimmed LED; ignore the garage" oninput="projNotes=this.value">${escAttr(projNotes)}</textarea>
  <label for="kpf">Keypad family <span style="color:var(--muted);font-weight:400">(the default &mdash; drawings rarely say)</span></label>
  <select id="kpf" onchange="famChosen=this.value;vPlans()" style="width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit">
    <option value="" ${famChosen===''?'selected':''}>Choose a family&hellip;</option>
    ${S.keypad_families.map(f=>`<option value="${f}" ${f===famChosen?'selected':''}>${f}</option>`).join('')}
    <option value="__ai__" ${famChosen==='__ai__'?'selected':''}>Let the AI choose one (it will say which)</option>
    <option value="__none__" ${famChosen==='__none__'?'selected':''}>No keypads &mdash; I'll place them myself</option>
  </select>
  ${kpNote}
  <label>Fixture research</label>
  <label style="font-weight:400;display:flex;gap:9px;align-items:flex-start;margin-top:2px">
    <input type="checkbox" id="rsr" ${S.research?'checked':''} onchange="S.research=this.checked;vPlans()" style="margin-top:3px;flex:none">
    <span>Look up fittings the drawings name but give no electrical data for</span>
  </label>
  ${rsrNote}
  <label>AI model</label>
  <p style="margin-top:2px">${escHTML(theModel.label||'')}${estLine}</p>
  ${modelNote}
  ${estNote}
  <details style="margin-top:16px;border:1px solid var(--line);border-radius:8px;padding:10px 14px">
    <summary style="cursor:pointer;font-size:13px;font-weight:600">What makes a good read &mdash; send these if you have them</summary>
    <div class="note" style="margin-top:10px;max-width:62ch">
      <p>The read is only as good as the set it is given, and the difference is
      not small. Measured on one real job: the same plans read <b style="color:var(--ink)">194
      circuits</b> on their own and <b style="color:var(--ink)">521</b> once the
      designer's circuit estimate was sent with them.</p>
      <p><b style="color:var(--ink)">Worth the most:</b></p>
      <p>&bull; <b style="color:var(--ink)">A circuit estimate or switching
      schedule</b> &mdash; anything that counts circuits per room, especially split
      into DALI / mains / switched. This is the designer counting his own scheme
      and it is the single biggest lever there is. The review sheet checks the
      result against it room by room.<br>
      &bull; <b style="color:var(--ink)">A fixture schedule or specification</b>
      &mdash; wattages and driver types from the designer beat anything found by
      searching. If it is a big document of words rather than drawings, tick
      &ldquo;read as text&rdquo; beside it: it costs a fraction and is not
      size-limited.<br>
      &bull; <b style="color:var(--ink)">Sheets with the circuits marked on them</b>
      &mdash; load boxes, leader lines, a switching schedule. A preliminary layout
      with symbols and no circuits still works, but the grouping is then proposed
      rather than read, and the report says so.</p>
      <p><b style="color:var(--ink)">What it cannot do, whatever you send:</b>
      count the fittings on a circuit where the sheet does not show which symbols
      belong to which circuit. It leaves the count blank and flags it rather than
      guessing, and the load totals exclude it. On those jobs the counts have to
      be marked up by hand.</p>
      <p><b style="color:var(--ink)">Send the whole set together</b>, not one sheet
      at a time &mdash; a legend on one sheet applies to all of them, and reading
      floors separately loses the earlier ones.</p>
    </div>
  </details>
  <details style="margin-top:10px;border:1px solid var(--line);border-radius:8px;padding:10px 14px">
    <summary style="cursor:pointer;font-size:13px;font-weight:600">What will this read cost, and why?</summary>
    <div class="note" style="margin-top:10px;max-width:62ch">
      <p>Everything is billed to your own Anthropic account. A typical house is a
      <b style="color:var(--ink)">few pounds</b> on the recommended model; turning
      fixture research off usually saves between a quarter and a half of that, and
      more on a job with many unfamiliar fittings. The figures beside each model
      above are worked out from your own last read. The exact cost of each read,
      split into reading / research / writing, is shown on the next screen when it
      finishes.</p>
      <p style="margin-top:8px">Three things drive it:</p>
      <ul style="padding-left:18px;margin-top:4px">
        <li><b style="color:var(--ink)">How much drawing there is.</b> Every sheet is
        read in full, and a single dense sheet can cost more than five sparse ones
        &mdash; it follows the drawing, not the file count. Send the lighting plans;
        leave out specification documents and general-arrangement sheets that carry
        no lighting information.</li>
        <li><b style="color:var(--ink)">Fixture research.</b> Each lookup makes Claude
        re-read the whole drawing set, so this is usually the largest share &mdash;
        it is why a read costs pounds rather than pence. A house with a dozen
        unfamiliar fittings costs a good deal more than one whose schedule already
        states every wattage.</li>
        <li><b style="color:var(--ink)">The model.</b> Opus is the recommended default.
        The figures in the dropdown price the same job on each one &mdash; but a
        cheaper model that misreads a circuit costs far more than it saves, so
        change this only if you have checked its output against your drawings.</li>
      </ul>
      <p style="margin-top:8px">A re-read costs the same as the first read &mdash; it is a
      fresh job. Correcting the CSVs in Excel and reloading is free, so prefer that to
      re-reading whenever the drawings themselves haven't changed.</p>
    </div>
  </details>
  ${keyBlock()}
  <div class="row">
    <button onclick="extract()" ${busy||!S.key_set?'disabled':''}>${busy?'Reading\\u2026':'Read the plans'}</button>
    ${S.projects.length?'<span class="note">or reopen: '+S.projects.map(p=>`<a href="#" data-project="${escAttr(p)}">${escHTML(p)}</a>`).join(' \\u00b7 ')+'</span>':''}
  </div>
  ${logBlock()}`);
}
// Every chosen file is kept, whatever its size. The ~30 MB page limit is
// enforced at Read time instead (see extract), because a document ticked
// "read as text" never becomes pages and so never counts towards it -- and a
// 45 MB specification, the exact document the option exists for, would
// otherwise be thrown out here before it could be ticked.
// Which of the two ways into step 2 is on screen. Both write the same six
// schedules into the same project folder, so everything after this point --
// checking, editing, building -- cannot tell them apart.
// The drawing and the spreadsheet share everything after the upload -- the
// same mapping screen, the same conversion, the same review -- so `vSheet`
// takes over as soon as either has been read.
// A loaded read belongs to ONE tab. Showing the mapping screen of a loaded
// SPREADSHEET under the AutoCAD tab left that tab with nowhere to upload a
// drawing at all (James, 08-12); the reverse put a drawing under the
// spreadsheet tab. A read made from a drawing is known by its plan_report.
function sheetMatchesSource(){return !!sheet&&(((sheet.plan_report||[]).length>0)===(source==='dwg'))}
function vStep2(){
  if(!S.accepted){setNav('plans');vFirstRun();return}
  if(source==='plans'){vPlans();return}
  if(sheetMatchesSource()){vSheet();return}
  if(source==='dwg')vDwg(); else vSheet();
}
function vDwg(){
  setNav('plans');
  if(S.busy&&S.stage==='importing'){h(sourceTabs()+'<h2>Reading the drawing</h2>'+logBlock());return}
  h(sourceTabs()+`<div class="eyebrow trial">Experimental</div><h2>AutoCAD DWG</h2>
  <p class="lead">If the set came with a DWG, the circuit labels on it are real counts &mdash;
  the designer drew them. This reads them straight off the drawing, with the rooms and the
  storeys, and gives you a schedule to fill in rather than a blank page.</p>
  <p class="note"><b style="color:var(--bad)">Read this before you trust it.</b> A drawing has no
  room boundaries in it, so every label is given to the nearest room name &mdash; and rooms are
  not circles. On the drawing this was built against, 13 rooms out of 22 had at least one label
  sitting as close to the room next door, and each of those is flagged for you to check.
  A drawing also never says what a fitting IS or what it draws, so every wattage comes out blank.
  Roughly a quarter of a DWG's text does not decode at all and is dropped rather than guessed at.</p>
  <div class="filebox" onclick="document.getElementById('df').click()">Click to choose a .dwg file</div>
  <input id="df" type="file" accept=".dwg" style="display:none" onchange="pickSheet(this)">
  <p class="note">Read on this computer; nothing is uploaded and nothing is charged. You are shown
  every row and every column before anything is written.</p>`+logBlock());
}
function setSource(s){source=s;pinned=true;vStep2()}
function sourceTabs(){
  const on='background:var(--accent);color:var(--accent-ink);border-color:var(--accent)';
  const off='background:transparent;color:var(--muted)';
  // Built with template literals at the top level, never nested inside the
  // big one below -- a nested literal defeats the page test that catches
  // unterminated strings, and that test is all that stands between a stray
  // quote and a dead interface.
  // Ordered best first, deliberately. The three ways in are not equals and
  // the difference between them is the difference between a schedule you can
  // build and one you have to check line by line.
  return `<div class="row" style="gap:0;margin:0 0 8px">
    <button style="margin:0;border-radius:6px 0 0 6px;${source==='sheet'?on:off}" onclick="setSource('sheet')">1. A schedule spreadsheet</button>
    <button style="margin:0;border-radius:0;border-left:none;${source==='dwg'?on:off}" onclick="setSource('dwg')">2. AutoCAD DWG</button>
    <button style="margin:0;border-radius:0 6px 6px 0;border-left:none;${source==='plans'?on:off}" onclick="setSource('plans')">3. PDF Plans</button>
  </div>
  <p class="note" style="margin:0 0 18px"><b style="color:var(--ink)">The better the input, the better
  the output</b> &mdash; and the gap between these three is large. A spreadsheet is the designer's own
  figures and needs no interpreting at all. A drawing gives real counts but cannot say what a fitting
  is or which room it is in. A plan read by AI is a machine looking at a picture.
  <b style="color:var(--ink)">Reading the input is the experimental half of this tool; only the
  spreadsheet route is solid.</b> Whichever you use, check the review sheet against the drawings
  before you build.</p>`;
}
function colLetter(i){let s='';i+=1;while(i){const r=(i-1)%26;s=String.fromCharCode(65+r)+s;i=Math.floor((i-1)/26)}return s}
function pickSheet(inp){
  const f=inp.files[0]; if(!f)return;
  // Cleared so choosing the SAME file again still fires onchange -- after a
  // mis-upload the natural retry is the same file, and a picker that silently
  // does nothing on it reads as a dead button.
  inp.value='';
  const rd=new FileReader();
  const seq=++sheetSeq;
  rd.onload=async()=>{
    const r=await api('/api/sheet/read',{name:f.name,data:rd.result.split(',')[1]});
    if(seq!==sheetSeq)return;   // a newer read or guess has superseded this one
    if(!r.ok){alert(r.error);return}
    // The server names the sheet to open when it can (a joined two-sheet
    // template); otherwise the last sheet, since the schedule is rarely the
    // cover sheet.
    sheet=r; upload=r.upload;
    sheetIdx=(r.suggested===null||r.suggested===undefined)?r.sheets.length-1:r.suggested;
    const t=sheet.sheets[sheetIdx];
    headerRow=t.header; colMap=Object.assign({},t.mapping);
    if(!projName)projName=f.name.replace(/\\.[^.]+$/,'');
    vSheet();
  };
  rd.readAsDataURL(f);
}
async function useSheet(i){
  sheetIdx=i;
  const t=sheet.sheets[i];
  headerRow=t.header; colMap=Object.assign({},t.mapping);
  vSheet();
}
async function setHeader(row){
  headerRow=row;
  // The headings changed, so the guess has to be made again -- by the server,
  // with the same code that made the first one, rather than a second opinion
  // written in JavaScript that could disagree with it.
  const forSheet=sheetIdx, seq=++sheetSeq;
  const r=await api('/api/sheet/guess',{table:forSheet,header:row,upload:upload});
  // Applied only if this is still the newest request AND the user is still on
  // the sheet it was asked about. A reply landing after a sheet switch used to
  // stamp the headings and mapping of the old sheet onto the new one -- the
  // 08-12 defect: a circuit number shown mapped on a sheet that has none.
  if(seq!==sheetSeq||sheetIdx!==forSheet)return;
  if(r.ok){sheet.sheets[forSheet].headings=r.headings;colMap=Object.assign({},r.mapping)}
  else alert(r.error);
  vSheet();
}
function setMap(key,val){
  const i=parseInt(val,10);
  if(i<0)delete colMap[key]; else colMap[key]=i;
  vSheet();
}
// A thin copy of the refusal the server makes, so the user sees it while mapping
// instead of after pressing the button. The server is still the authority --
// it checks the same things again and refuses the import outright.
function mapGaps(){
  const out=[];
  sheet.fields.forEach(f=>{if(f.required&&colMap[f.key]===undefined)out.push(f.label)});
  if(colMap.fixture_ref===undefined&&colMap.fixture_description===undefined)
    out.push('a fitting type or a fitting description');
  const seen={};
  Object.keys(colMap).forEach(k=>{const c=colMap[k];if(seen[c])out.push('two things on one column');seen[c]=1});
  return out;
}
function vSheet(){
  setNav('plans');
  if(S.busy&&S.stage==='importing'){
    h(sourceTabs()+'<h2>Reading the spreadsheet</h2>'+logBlock());
    return;
  }
  // A drawing read under the spreadsheet tab counts as "nothing here": the
  // upload screen renders, and the drawing keeps its screen on its own tab.
  if(!sheet||!sheetMatchesSource()){
    h(sourceTabs()+`<div class="eyebrow">The best way in</div><h2>A schedule spreadsheet</h2>
    <p class="lead">A CSV or an Excel file with a row per circuit. This is the designer's own
    figures, so it is exact, it costs nothing, no AI is involved, and nothing has to be guessed
    at. <b style="color:var(--ink)">If you can get one, get one</b> &mdash; everything downstream
    is only ever as good as what goes in here.</p>
    <p class="note"><b style="color:var(--ink)">Haven't got a schedule?</b> Send whoever is doing
    the lighting this sheet and ask them to fill it in. It has the right columns already on it,
    so it comes back in a form this reads perfectly.
    <a href="/template.xlsx" style="color:var(--accent-ink);background:var(--accent);padding:5px 11px;border-radius:6px;text-decoration:none;font-weight:600;display:inline-block;margin-top:7px">Download the blank template</a></p>
    <div class="filebox" onclick="document.getElementById('sf').click()">Click to choose a .csv or .xlsx file</div>
    <input id="sf" type="file" accept=".csv,.tsv,.xlsx,.xlsm" style="display:none" onchange="pickSheet(this)">
    <p class="note">The file is read on this computer. Nothing is uploaded, nothing is charged, and
    you are shown what every column was taken to mean &mdash; and can correct it &mdash; before a
    single row is written.</p>
    <p class="note"><b style="color:var(--ink)">What it needs:</b> one row per circuit, with at
    least a room, a description of the circuit, and something naming the fitting. A count and a
    wattage make the load totals work. Anything the sheet does not say is left blank and flagged
    on the review sheet, never filled in for you.</p>`+logBlock());
    return;
  }
  const t=sheet.sheets[sheetIdx];
  const heads=t.headings;
  const tabs=sheet.sheets.length<2?'':`<label>Which sheet</label>
    <div class="row" style="margin-top:2px">${sheet.sheets.map(s=>
      `<button class="ghost" style="margin:0;${s.index===sheetIdx?'background:var(--accent);color:var(--accent-ink)':''}" onclick="useSheet(${s.index})">${escHTML(s.name)}</button>`).join('')}</div>`;
  const cellStyle='padding:3px 7px;border-right:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:150px';
  const grid=t.preview.map((r,i)=>
    `<tr onclick="setHeader(${i})" style="cursor:pointer;${i===headerRow?'background:var(--panel);font-weight:600;color:var(--ink)':''}">`
    +`<td style="${cellStyle};color:var(--muted);font-weight:400">${i+1}</td>`
    +r.map(c=>`<td style="${cellStyle}">${escHTML(c)}</td>`).join('')+'</tr>').join('');
  // The options are rebuilt for EVERY field, because each one has to carry its
  // own `selected`. Building the list once and sharing it renders a screen
  // where every column reads "not used" while the guesses are in fact correct
  // -- so the user re-does work already done, or worse, believes it.
  const optionsFor=cur=>heads.map((hname,i)=>
    `<option value="${i}"${cur===i?' selected':''}>${escHTML(colLetter(i)+': '+(hname||'(blank)'))}</option>`).join('');
  const rowsOfMap=sheet.fields.map(f=>{
    const cur=colMap[f.key]===undefined?-1:colMap[f.key];
    return `<tr>
      <td style="padding:5px 10px 5px 0;font-weight:600;font-size:13px;white-space:nowrap;vertical-align:top">${escHTML(f.label)}${f.required?' <span style="color:var(--bad)">*</span>':''}</td>
      <td style="padding:5px 0;vertical-align:top"><select onchange="setMap('${f.key}',this.value)" style="padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit;font-size:13px;min-width:190px"><option value="-1"${cur===-1?' selected':''}>&mdash; not used &mdash;</option>${optionsFor(cur)}</select></td>
      <td style="padding:5px 0 5px 12px;font-size:12px;color:var(--muted);vertical-align:top;max-width:34ch">${escHTML(f.help)}</td>
    </tr>`;
  }).join('');
  const gaps=mapGaps();
  const gapNote=gaps.length
    ? '<p class="note err">Still needed: '+escHTML(gaps.join('; '))+'.</p>'
    : '';
  // One stray click on a data row turns every dropdown into gibberish --
  // "A: Ground Floor, B: Hall" offered as column names -- and the only clue
  // was the small line above (James hit this, 08-12). Headings are nearly
  // always the first real row; the app picks it, and if the row in use is not
  // that one AND recognises fewer columns, say so loudly. A user who corrected
  // a genuinely bad guess maps MORE columns, not fewer, and is left in peace.
  const headWarn=(headerRow!==t.header
      &&Object.keys(colMap).length<Object.keys(t.mapping).length)
    ? '<p class="note err">Row '+(headerRow+1)+' does not look like column headings &mdash; the lists below are showing its values as if they were. The headings look like row '+(t.header+1)+' &mdash; click that row above.</p>'
    : '';
  // A drawing is read into these same rows, so the screen has to stop calling
  // everything a spreadsheet the moment it is not looking at one.
  const isDwg=(sheet.plan_report||[]).length>0;
  const noun=isDwg?'drawing':'sheet';
  const planNote=isDwg
    ? '<div class="note err" style="border:1px solid var(--bad);border-radius:8px;padding:11px 14px;margin:0 0 16px"><b>What the drawing could not tell us</b><ul style="margin:6px 0 0;padding-left:18px">'
      +(sheet.plan_report||[]).map(l=>'<li style="margin:3px 0">'+escHTML(l)+'</li>').join('')+'</ul></div>'
    : '';
  h(sourceTabs()+`<h2>What each column means</h2>
  <p class="lead">Read <b style="color:var(--ink)">${escHTML(sheet.filename)}</b>. Check the
  guesses below against the ${noun} &mdash; a column read as the wrong thing produces a complete,
  believable schedule for a house nobody designed, and nothing later will catch it.</p>
  ${planNote}
  ${tabs}
  <label>The ${noun} as it was read <span style="color:var(--muted);font-weight:400">(click the row that holds the column headings)</span></label>
  <div style="overflow:auto;max-height:260px;border:1px solid var(--line);border-radius:8px;margin-top:4px">
    <table style="border-collapse:collapse;font:12px/1.5 var(--mono);width:100%">${grid}</table>
  </div>
  <p class="note">Row ${headerRow+1} is being used as the headings. Rows above it are ignored.</p>
  ${headWarn}
  <label style="margin-top:20px">Columns</label>
  <table style="border-collapse:collapse;margin-top:2px">${rowsOfMap}</table>
  ${gapNote}
  <label style="margin-top:20px">How to read it</label>
  <label style="font-weight:400;display:flex;gap:9px;align-items:flex-start;margin-top:2px">
    <input type="checkbox" ${fillDown?'checked':''} onchange="fillDown=this.checked;vSheet()" style="margin-top:3px;flex:none">
    <span>The room is written once for a group of circuits (merged cells) &mdash; carry it down</span>
  </label>
  <label style="font-weight:400;display:flex;gap:9px;align-items:flex-start;margin-top:8px">
    <input type="checkbox" ${wattTotal?'checked':''} onchange="wattTotal=this.checked;vSheet()" style="margin-top:3px;flex:none">
    <span>The wattage column is the whole circuit, not one fitting</span>
  </label>
  <p class="note">Get the second one wrong and every load is out by the number of fittings on the
  circuit &mdash; which is exactly the size of error that oversizes or overloads a panel. Where
  the sheet gives a circuit total, it is divided by the count and the fitting says so.</p>
  <label for="spn">Project name</label>
  <input id="spn" type="text" placeholder="e.g. 14 Orchard Lane" oninput="projName=this.value" value="${escAttr(projName)}">
  <div class="row">
    <button onclick="importSheet()" ${gaps.length?'disabled':''}>Read this ${noun}</button>
    <button class="ghost" onclick="sheet=null;vStep2()">Choose a different file</button>
  </div>
  <p class="note">No keypads and no scenes come out of a load schedule, because it does not carry
  them. Place them in Designer, or read the plans with the AI to have them worked out.</p>
  `+logBlock());
}
async function importSheet(){
  const r=await api('/api/sheet/import',{project:projName,table:sheetIdx,header:headerRow,
    mapping:colMap,fill_down:fillDown,wattage_is_total:wattTotal,upload:upload});
  if(!r.ok){alert(r.error);return}
  pinned=false; view='plans'; poll();
}
function pick(inp){
  const chosen=[...inp.files];
  files=[]; let left=chosen.length;
  if(!left){vPlans();return}
  chosen.forEach(f=>{
    const rd=new FileReader();
    rd.onload=()=>{files.push({name:f.name,size:f.size,text_only:false,data:rd.result.split(',')[1]});
                   if(--left===0){files.sort((a,b)=>a.name.localeCompare(b.name));vPlans()}};
    rd.readAsDataURL(f);
  });
}
function setTextOnly(i,on){files[i].text_only=on;vPlans()}
// What the user typed survives every re-render (family/model/file changes all
// redraw the panel). Reading these back from the DOM on redraw is not enough --
// the redraw happens BEFORE the read. Globals, like famChosen.
let projName='';
let projNotes='';
function escAttr(s){return String(s).replace(/&/g,'&amp;').replace(/\\x22/g,'&quot;').replace(/</g,'&lt;')}
async function extract(){
  if(!files.length){alert('Choose at least one PDF first.');return}
  if(!famChosen){alert('Choose a keypad family first.');return}
  // The ~30 MB ceiling is on what is sent as PAGES. A file ticked to be read
  // as text is sent as words and does not count towards it, which is the
  // whole point of the option -- so the check runs here, where the ticks are
  // known, rather than when the files were chosen.
  const LIMIT=30*1048576;
  const pages=files.filter(f=>!f.text_only);
  const big=pages.filter(f=>f.size>LIMIT);
  if(big.length){
    alert('Too big to send as pages:\\n\\n'+big.map(f=>f.name+' ('+(f.size/1048576).toFixed(1)+' MB)').join('\\n')+
          '\\n\\nThe AI accepts about 30 MB of PDFs per read. If one of these is a specification or a schedule rather than a drawing, tick "read as text" beside it — it then costs a fraction of this and the size limit no longer applies. Otherwise leave it out and send the lighting plan sheets themselves.');
    return;
  }
  const total=pages.reduce((n,f)=>n+f.size,0);
  if(total>LIMIT){
    alert('The sheets being sent as pages come to '+(total/1048576).toFixed(1)+' MB — more than the ~30 MB the AI accepts in one read. Pick fewer sheets (e.g. one floor at a time), or tick "read as text" for anything that is words rather than a drawing.');
    return;
  }
  const r=await api('/api/extract',{pdfs:files,project:projName,
                                    notes:projNotes,
                                    model:(S.models[0]||{}).id,
                                    keypad_family:famChosen,
                                    research:S.research!==false});
  if(!r.ok){alert(r.error);return}
  pinned=false; view='plans'; poll();
}
async function reopen(p){await api('/api/select',{project:p});await refresh(false);vCheck()}
function vCheck(){
  sceneOpen=false;
  setNav('check');
  checkStamp=stamp();
  // Where the money went, not just how much: reading vs research vs writing.
  const LE=S.last_extract;
  const cost=LE&&LE.cost_lines?'<p class="note">Last read ('+escHTML(LE.model)+'), billed to your Anthropic account:<br>'+LE.cost_lines.map(escHTML).join('<br>')+'</p>'
    :(LE&&LE.cost_usd?`<p class="note">Last read: ${LE.model}, ${LE.input_tokens.toLocaleString()} tokens in / ${LE.output_tokens.toLocaleString()} out &mdash; about $${LE.cost_usd.toFixed(2)} on your Anthropic account.</p>`:'');
  h(`<h2>Check the schedules</h2>
  ${cost}
  <p class="lead">Read the extraction report first &mdash; it says what the AI read, what it
  proposed, and what it could not work out. Then check the review sheet against the drawings.
  To correct anything, edit the CSV files in Excel and reload.</p>
  <div class="row">
    <button class="ghost" onclick="openIt('report')">Open the report</button>
    <button class="ghost" onclick="openIt('folder')">Open the CSVs in Explorer</button>
    <button class="ghost" onclick="reloadSheet()">Reload after edits</button>
    <button class="ghost" onclick="loadScenes()">Edit the scenes</button>
    <button class="ghost" onclick="loadFittings()">Edit the fittings</button>
    <button onclick="vBuild()">Looks right \\u2192 Build</button>
  </div>
  ${S.review_ready?'<iframe src="/review"></iframe>':'<p class="note">No review sheet yet \\u2014 read some plans first.</p>'}`);
}
async function reloadSheet(){
  const r=await api('/api/reload',{});
  if(!r.ok){alert('The schedules have a problem:\\n\\n'+r.error);return}
  vCheck();
}
// ------------------------------------------------------------- the scenes
// A scene is one row per (room, scene, CIRCUIT). Everything below edits the
// scene LIST -- name, number, and one level for the whole room -- because that
// is what an engineer wants to fix in bulk. Per-circuit tuning stays in the
// CSVs, and a scene that HAS per-circuit tuning is marked so that setting a
// level here cannot quietly throw it away.
let sceneData=null, sceneArea='', sceneProject='', sceneOpen=false;
// What the Check panel was drawn against, so Build can refuse to act on a
// review the user has not actually seen.
let checkStamp='';
async function loadScenes(){
  // A room chosen in the last project must not carry over into this one.
  if(sceneProject!==S.project){sceneArea='';sceneProject=S.project}
  sceneData=null; pinned=true; panel='check'; sceneOpen=true; vScenes();
  const r=await api('/api/scenes');
  if(!r.ok){alert(r.error);vCheck();return}
  sceneData=r; vScenes();
}
// Every room that HAS circuits, not just every room that already has scenes.
// Listing only the latter meant a room the AI gave no scenes to could never be
// copied into, and deleting the last scene in a project locked the editor shut.
function sceneRooms(){
  const named=sceneData.areas.map(a=>a.area);
  Object.keys(sceneData.zones||{}).forEach(a=>{if(!named.includes(a))named.push(a)});
  return named.sort();
}
function sceneRow(s,i){
  const warn=s.mixed?'<span class="note" style="color:var(--muted)">per-circuit levels set</span>':'';
  return `<tr data-i="${i}" data-wasn="${escAttr(s.number)}" data-wasname="${escAttr(s.name)}">
    <td><input class="s-num" value="${escAttr(s.number)}" style="width:56px"></td>
    <td><input class="s-name" value="${escAttr(s.name)}"></td>
    <td><input class="s-lvl" value="${escAttr(s.level)}" style="width:64px" placeholder="%"> ${warn}</td>
    <td style="color:var(--muted)">${s.zones}</td>
    <td><button class="ghost" onclick="dropScene(this)">Remove</button></td></tr>`;
}
function vScenes(){
  setNav('check');
  if(!sceneData){h('<h2>Scenes</h2><p class="lead">Reading the schedules&hellip;</p>');return}
  const rooms=sceneRooms();
  if(!rooms.length){h('<h2>Scenes</h2><p class="lead">This project has no rooms with circuits yet.</p><div class="row"><button class="ghost" onclick="vCheck()">Back to the check</button></div>');return}
  if(!sceneArea||!rooms.includes(sceneArea))sceneArea=rooms[0];
  const cur=sceneData.areas.find(a=>a.area===sceneArea)||{area:sceneArea,scenes:[]};
  const opts=rooms.map(a=>`<option value="${escAttr(a)}" ${a===sceneArea?'selected':''}>${escHTML(a)}</option>`).join('');
  const rows=cur.scenes.map(sceneRow).join('');
  const others=rooms.filter(a=>a!==sceneArea).map(a=>`<label style="display:block;padding:2px 0"><input type="checkbox" class="tgt" value="${escAttr(a)}"> ${escHTML(a)}</label>`).join('');
  h(`<h2>Scenes</h2>
  <p class="lead">Set up one room the way you want it, then copy it to the others. The
  scene names and numbers travel, and each scene sets its level on every circuit in the
  room it lands in &mdash; so the whole house is named and numbered the same, and you
  fine-tune from there.</p>
  <label for="sa">Room</label>
  <select id="sa" onchange="sceneArea=this.value;vScenes()" style="width:100%;padding:9px 11px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);font:inherit">${opts}</select>
  <div style="overflow-x:auto">
  <table id="stbl" style="width:100%;border-collapse:collapse;margin:14px 0;min-width:420px">
    <tr style="text-align:left;color:var(--muted);font-size:12.5px">
      <th>No.</th><th>Scene</th><th>Level</th><th>Circuits</th><th></th></tr>
    ${rows}
  </table>
  </div>
  <div class="row">
    <button class="ghost" onclick="addScene()">Add a scene</button>
    <button onclick="saveScenes()">Save this room</button>
  </div>
  <p class="note">Leaving a level blank keeps whatever the circuits are set to now. Typing
  one sets every circuit in this room to it.</p>
  <h2 style="margin-top:26px">Copy these scenes to&hellip;</h2>
  <div style="margin:8px 0 14px">${others||'<span class="note">There is only one room.</span>'}</div>
  <div class="row">
    <button class="ghost" onclick="tickAll()">Select all</button>
    <button onclick="copyScenes()">Copy</button>
    <button class="ghost" onclick="vCheck()">Back to the check</button>
  </div>
  <p class="note">Copying REPLACES the scenes in the rooms you tick. The previous
  Scenes.csv is kept alongside it as Scenes.csv.bak.</p>`);
}
function dropScene(btn){btn.closest('tr').remove()}
function addScene(){
  const t=document.getElementById('stbl');
  const tr=document.createElement('tr');
  tr.innerHTML=`<td><input class="s-num" value="" style="width:56px"></td>
    <td><input class="s-name" value=""></td>
    <td><input class="s-lvl" value="" style="width:64px" placeholder="%"></td>
    <td style="color:var(--muted)">new</td>
    <td><button class="ghost" onclick="dropScene(this)">Remove</button></td>`;
  t.appendChild(tr);
}
function tickAll(){document.querySelectorAll('.tgt').forEach(c=>{c.checked=true})}
function readScenes(){
  const cur=sceneData.areas.find(a=>a.area===sceneArea)||{scenes:[]};
  const live=[...document.querySelectorAll('#stbl tr')].filter(tr=>tr.querySelector('.s-name'));
  const edits=live.map(tr=>({
    was_number:tr.dataset.wasn||'', was_name:tr.dataset.wasname||'',
    number:tr.querySelector('.s-num').value, name:tr.querySelector('.s-name').value,
    level:tr.querySelector('.s-lvl').value}));
  // A row the engineer deleted is absent from the DOM, so it has to be asked
  // for explicitly -- otherwise a removal is simply ignored on save.
  const kept=new Set(live.map(tr=>(tr.dataset.wasn||'')+'\\u0000'+(tr.dataset.wasname||'')));
  cur.scenes.forEach(s=>{
    if(!kept.has(s.number+'\\u0000'+s.name))
      edits.push({was_number:s.number,was_name:s.name,delete:true});
  });
  return edits;
}
async function saveScenes(){
  const r=await api('/api/scenes/edit',{area:sceneArea,edits:readScenes()});
  if(!r.ok){alert('That change was not applied:\\n\\n'+r.error);return}
  await loadScenes();
  if(r.notes&&r.notes.length)alert(r.notes.join('\\n'));
}
async function copyScenes(){
  const targets=[...document.querySelectorAll('.tgt')].filter(c=>c.checked).map(c=>c.value);
  if(!targets.length){alert('Tick the rooms to copy these scenes to.');return}
  // Ask the server what this WOULD do before doing it. Copying replaces the
  // scenes in a room, and any keypad button that recalled one of the old names
  // goes with them -- which the engineer should decide about, rather than be
  // told about once it has happened.
  const pre=await api('/api/scenes/copy',{source:sceneArea,targets:targets,preview:true});
  if(!pre.ok){alert('Nothing was copied:\\n\\n'+pre.error);return}
  const losses=(pre.notes||[]).filter(n=>n.indexOf('Removed')===0);
  let msg='Replace the scenes in '+targets.length+' room(s) with the ones from '+sceneArea+'?';
  if(losses.length)msg+='\\n\\nThis will ALSO remove keypad buttons in those rooms:\\n'+losses.join('\\n');
  msg+='\\n\\n'+(pre.notes||[]).filter(n=>n.indexOf('Removed')!==0).join('\\n');
  if(!confirm(msg))return;
  const r=await api('/api/scenes/copy',{source:sceneArea,targets:targets});
  if(!r.ok){alert('Nothing was copied:\\n\\n'+r.error);return}
  await loadScenes();
  alert('Copied.\\n\\n'+(r.notes||[]).join('\\n'));
}
// ----------------------------------------------------------- the fittings
// The values most likely to have been ASSUMED rather than read all live on the
// fitting list -- the dimming type above all, then the wattage and the trims.
// Correcting one used to mean opening the CSV in Excel. The dimming type is a
// list because it is a Lutron code, not a word: typing "DALI" into LoadTypeID
// produces a schedule that will not load at all.
let fitData=null, fitProject='';
async function loadFittings(){
  if(fitProject!==S.project){fitProject=S.project}
  fitData=null; pinned=true; panel='check'; vFittings();
  const r=await api('/api/fittings');
  if(!r.ok){alert(r.error);vCheck();return}
  fitData=r; vFittings();
}
function fitTypeOptions(f){
  const sel=f.load_type_id, assumed=f.assumed.indexOf('LoadTypeID')>=0;
  const common=fitData.load_types.filter(t=>t.common);
  const rest=fitData.load_types.filter(t=>!t.common);
  const opt=t=>`<option value="${t.id}" ${!assumed&&t.id===sel?'selected':''}>${escHTML(t.name)}</option>`;
  // An assumed type has to be SELECTABLE as still-assumed. Without this option
  // the select opens showing the default, saving any other field posts it back
  // as a real value, and a guess is silently promoted to a reading -- the one
  // thing this release exists to prevent.
  const unknown=assumed
    ? `<option value="" selected>not stated \\u2014 assuming ${escHTML(f.load_type_name)}</option>` : '';
  return unknown
       + '<optgroup label="Usual">'+common.map(opt).join('')+'</optgroup>'
       + '<optgroup label="Everything else Designer knows">'+rest.map(opt).join('')+'</optgroup>';
}
function fitRow(f){
  // "Assumed" is not a judgement made here: it is the cell being BLANK in the
  // file, which is the same thing the review sheet marks.
  const flag=n=>f.assumed.indexOf(n)>=0
    ? ' <span style="color:var(--muted);font-size:11px;text-transform:uppercase">assumed</span>' : '';
  const src=f.data_source?`<div class="note" style="margin:0">${escHTML(f.data_source)}</div>`:'';
  return `<tr data-ref="${escAttr(f.ref)}">
    <td style="white-space:nowrap"><b>${escHTML(f.ref)}</b></td>
    <td><input class="f-desc" value="${escAttr(f.description)}" style="width:100%">${src}</td>
    <td><select class="f-type" style="max-width:190px">${fitTypeOptions(f)}</select>${flag('LoadTypeID')}</td>
    <td><input class="f-w" value="${escAttr(f.wattage)}" style="width:64px" placeholder="W"></td>
    <td><input class="f-wm" value="${escAttr(f.watts_per_metre)}" style="width:64px" placeholder="W/m"></td>
    <td style="white-space:nowrap"><input class="f-lo" value="${escAttr(f.low_end)}" style="width:48px" placeholder="%"><input class="f-hi" value="${escAttr(f.high_end)}" style="width:48px;margin-left:4px" placeholder="%">${flag('LowEnd_pct')}</td>
    <td style="color:var(--muted);white-space:nowrap">${f.circuits} circuit(s)</td>
    <td><input class="f-note" value="${escAttr(f.notes)}" style="width:100%"></td></tr>`;
}
function vFittings(){
  setNav('check');
  if(!fitData){h('<h2>Fittings</h2><p class="lead">Reading the schedules&hellip;</p>');return}
  if(!fitData.fittings.length){h('<h2>Fittings</h2><p class="lead">This project has no fitting types yet.</p><div class="row"><button class="ghost" onclick="vCheck()">Back to the check</button></div>');return}
  h(`<h2>Fittings</h2>
  <p class="lead">Every fitting type in this project. Anything marked <b>assumed</b> was not
  stated on the drawings &mdash; it was filled in with a sensible default so the file would
  build, and it is the first thing worth correcting. Wattage sizes the circuits and the
  panel, so a wrong one is the expensive kind of wrong.</p>
  <div style="overflow-x:auto">
  <table id="ftbl" style="width:100%;border-collapse:collapse;margin:14px 0;min-width:840px">
    <tr style="text-align:left;color:var(--muted);font-size:12.5px">
      <th>Ref</th><th>Description</th><th>Dimming type</th><th>Watts</th>
      <th>W per m</th><th>Trim low / high</th><th>Used on</th><th>Notes</th></tr>
    ${fitData.fittings.map(fitRow).join('')}
  </table>
  </div>
  <div class="row">
    <button onclick="saveFittings()">Save the fittings</button>
    <button class="ghost" onclick="vCheck()">Back to the check</button>
  </div>
  <p class="note">A fitting sold by the metre &mdash; tape, a cove profile &mdash; takes its
  wattage in <b>W per m</b>, and each circuit using it carries a run length in metres instead
  of a count. Leave one of the two blank. The reference itself is not editable here: it is the
  name every circuit uses, so renaming it is a change to two files. The previous file is kept
  alongside as FixturesCatalog.csv.bak.</p>`);
}
function readFittings(){
  return [...document.querySelectorAll('#ftbl tr')].filter(tr=>tr.dataset.ref).map(tr=>({
    ref:tr.dataset.ref,
    description:tr.querySelector('.f-desc').value,
    load_type_id:tr.querySelector('.f-type').value,
    wattage:tr.querySelector('.f-w').value,
    watts_per_metre:tr.querySelector('.f-wm').value,
    low_end:tr.querySelector('.f-lo').value,
    high_end:tr.querySelector('.f-hi').value,
    notes:tr.querySelector('.f-note').value}));
}
async function saveFittings(){
  const r=await api('/api/fittings/edit',{edits:readFittings()});
  if(!r.ok){alert('That change was not applied:\\n\\n'+r.error);return}
  await loadFittings();
  alert('Saved.'+(r.notes&&r.notes.length?'\\n\\n'+r.notes.join('\\n'):''));
}
function vBuild(){
  setNav('build');
  const busy=S.busy&&S.stage==='building';
  h(`<h2>Build the Lutron file</h2>
  <p class="lead">This writes a HomeWorks project file (.hw) on this computer, using the SQL
  engine that Lutron Designer installs. Open the result in Designer, check it, add your
  processor and panels, and assign the circuits and keypads &mdash; they arrive unassigned,
  ready to wire up.</p>
  <div class="row">
    <button onclick="build()" ${busy?'disabled':''}>${busy?'Building\\u2026':'Build the .hw file'}</button>
    ${S.hw_path?`<button class="ghost" onclick="openIt('folder')">Show the file</button>
                 <button class="ghost" onclick="openIt('hw')">Open in Lutron Designer</button>`:''}
  </div>
  ${S.windows?'':'<p class="note">Note: you are not on Windows &mdash; the build here uses Docker and the result is for development only. The real file must be built on the Windows machine that has Lutron Designer.</p>'}
  ${logBlock()}`);
}
function stamp(){return JSON.stringify([S.project,S.report_ready,S.review_ready,
    S.last_extract&&S.last_extract.output_tokens])}
async function build(){
  // Between a read finishing on the server and the next 1.2s poll, the Check
  // panel can still be showing the PREVIOUS review. Building from it would
  // build schedules nobody has looked at.
  const was=checkStamp; await refresh(false);
  if(was&&was!==stamp()){
    alert('The schedules changed since you last looked at them. Check them again before building.');
    pinned=false; vCheck(); return;
  }
  const r=await api('/api/build',{});
  if(!r.ok){alert(r.error);return}
  pinned=false; view='build'; poll();
}
async function poll(){
  await refresh(true);
  if(S.busy){setTimeout(poll,1200);return}
  // The job is over, so the pin has done its work. Releasing it matters: a
  // read that finishes while the user sits on the Check step would otherwise
  // leave the OLD review sheet on screen against the NEW schedules, and
  // "Looks right" would build something nobody had looked at.
  if(pinned){
    // Do NOT drag them off the stage they chose just because a job ended --
    // that discards a half-typed API key, or unsaved scene edits. Refresh what
    // is safe to refresh and leave the rest alone.
    pinned=false;
    if(sceneOpen){
      // Their edits were against schedules that have just been replaced, so
      // keeping them on screen would be keeping a lie.
      alert('The read has finished and the schedules were replaced, so the scene list has been reloaded.');
      loadScenes();
    }
    else if(panel==='check'){go('check')}   // the review sheet is now stale
    else{setNav(panel)}
    return;
  }
  if(view==='plans'&&S.report_ready&&!S.error){view=null;vCheck()}
  else if(view==='plans'){vStep2()}
  else if(view==='build'){view=null;vBuild()}
}
async function refresh(repaint){
  S=await api('/api/status');
  if(!repaint)return;
  if(pinned){
    // The panel that carries the progress log must keep repainting or the log
    // freezes and the app looks hung. Everything it redraws comes back from a
    // global, so nothing typed is lost. Other panels are left alone, because
    // repainting the key screen WOULD wipe a half-typed key.
    const live=(panel==='plans'&&(S.stage==='extracting'||S.stage==='importing'))||
               (panel==='build'&&S.stage==='building');
    if(live)go(panel); else setNav(panel);
    return;
  }
  if(repaint){
    if(S.stage==='extracting'||S.stage==='importing'||view==='plans')vStep2();
    else if(S.stage==='building'||view==='build')vBuild();
  }
}
(async()=>{
  await refresh(false);
  if(S.review_ready)vCheck(); else vStep2();
})();
</script></body></html>
"""

# PAGE is a plain string (not a template), so stamp the version into it once.
PAGE = PAGE.replace("__APP_VERSION__", f"v{VERSION}")


def main(port: int = 8323, open_browser: bool = True) -> int:
    if not os.path.exists(STARTER_SHELL):
        print(f"Missing {STARTER_SHELL} -- the app needs the bundled starter shell.")
        return 1
    # Python's stdlib server opts into address reuse, and on Windows that lets
    # a NEW instance bind a port an old instance still owns -- with delivery
    # split between them. A user who launches twice, or launches after a stale
    # copy was left running, then sees whichever instance wins, which once
    # meant a fresh download serving yesterday's broken page. So: never share
    # a port. If it is taken, say so and walk forward to a free one.
    class _ExclusiveServer(ThreadingHTTPServer):
        allow_reuse_address = False

    server = None
    for candidate in range(port, port + 10):
        try:
            server = _ExclusiveServer(("127.0.0.1", candidate), Handler)
        except OSError:
            print(f"Port {candidate} is taken -- another copy of {APP_NAME} "
                  f"(or an older one) may still be running. Trying {candidate + 1}.")
            continue
        port = candidate
        break
    if server is None:
        print(f"No free port in {port}..{port + 9}. Close the other copies of "
              f"{APP_NAME} (check the system tray and Task Manager) and try again.")
        return 1
    url = f"http://127.0.0.1:{port}/"
    print(f"{APP_NAME} v{VERSION} running at {url}  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
