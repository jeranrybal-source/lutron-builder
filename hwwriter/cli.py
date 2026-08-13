"""
Command line: `hwwriter build`, `hwwriter inspect`, `hwwriter doctor`.

The tool needs a SQL Server to restore the project into.  On Windows that is
usually the LocalDB that ships with Designer.  Everywhere else it is a SQL
Server container, which this CLI will start for you -- including on Apple
Silicon, where the amd64 image runs under Docker Desktop's emulation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from . import blank, ingest, review, sandbox
from .build import Builder
from .emit import Schema
from .schedule import ValidationError, load_schedule, read_reference, validate
from .shell import read_shell
from .sqlrunner import DockerRunner, LocalRunner, PowerShellRunner, SqlError

IMAGE = "mcr.microsoft.com/mssql/server:2022-latest"
CONTAINER = "lutron-hw-writer"
PASSWORD = os.environ.get("HWWRITER_SA_PASSWORD", "LutronHwWriter!2026")
from ._paths import docs as _docs  # noqa: E402 -- must follow the constants above

DOCS = _docs()


def _say(msg: str = "") -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------ backends

def ensure_container(workdir: str, container: str = CONTAINER) -> DockerRunner:
    """Start the SQL Server container if it isn't already up."""
    if not shutil.which("docker"):
        raise SqlError(
            "Docker is not installed. Either install Docker Desktop, or run this on Windows "
            "against the LocalDB that ships with Lutron Designer using --server.")

    os.makedirs(workdir, exist_ok=True)
    os.chmod(workdir, 0o777)

    state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", container],
                           capture_output=True, text=True)
    running = state.returncode == 0 and state.stdout.strip() == "true"

    if state.returncode == 0 and not running:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        running = False

    if not running:
        _say(f"Starting SQL Server container '{container}' (first run pulls ~600 MB)...")
        proc = subprocess.run([
            "docker", "run", "-d", "--name", container, "--platform", "linux/amd64",
            "-e", "ACCEPT_EULA=Y", "-e", f"MSSQL_SA_PASSWORD={PASSWORD}",
            "-e", "MSSQL_PID=Developer",
            "-v", f"{os.path.abspath(workdir)}:/work",
            IMAGE], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SqlError(f"could not start the container:\n{proc.stderr}")

    runner = DockerRunner(container, PASSWORD, workdir)
    runner.wait_ready()
    return runner


def make_runner(args, workdir: str):
    if args.server:
        # On Windows the PowerShell backend is the default: it needs nothing
        # installed and handles LocalDB on ARM64, where sqlcmd's connection
        # path fails. LocalRunner remains for a non-Windows host reaching a
        # real server over the network.
        if os.name == "nt":
            return PowerShellRunner(args.server, password=args.password,
                                    user=args.user, trusted=not args.user)
        return LocalRunner(args.server, password=args.password,
                           user=args.user, trusted=not args.user)
    if os.name == "nt":
        raise SqlError(
            "On Windows, point the tool at Designer's own LocalDB with "
            "--server \"(localdb)\\MSSQLLocalDB\". (Files built by the Docker "
            "backend cannot open in Designer: a backup made by SQL Server on "
            "Linux does not restore on Windows.)")
    return ensure_container(workdir, args.container)


# --------------------------------------------------------------------- build

def cmd_build(args) -> int:
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="hwwriter-"))
    os.makedirs(work, exist_ok=True)

    runner = make_runner(args, work)
    dropped = sandbox.sweep_stale(runner)
    if dropped:
        _say(f"Cleared {len(dropped)} stale sandbox database(s) from an earlier run.")

    _say(f"Opening shell:    {args.shell}")
    lut = sandbox.unpack_hw(args.shell, os.path.join(work, "in"))

    box = sandbox.Sandbox(runner)
    try:
        box.restore(lut)
        schema = Schema(runner, box.name)
        shell = read_shell(runner, box.name)

        _say(f"Shell project:    {shell.project_name}")
        _say(f"Floors:           {', '.join(sorted(shell.floors)) or '(none)'}")
        _say(f"Comm links:       {', '.join(sorted(shell.links)) or '(none)'}")
        _say(f"Module types:     {', '.join(str(m) for m in sorted(shell.modules)) or '(none)'}")
        _say(f"Keypad models:    {', '.join(str(k) for k in sorted(shell.keypads)) or '(none)'}")

        # Designer gives every room a daylighting group and an occupancy group.
        # Both are cloned from a room in the shell, so a shell that has none
        # (a stripped one, typically) silently produces rooms without them.
        # Say so rather than let it pass unnoticed.
        missing = [n for n, v in (("daylighting", shell.daylighting_group_id),
                                  ("occupancy", shell.occupancy_group_id)) if not v]
        if missing:
            _say()
            _say(f"  note: this shell has no {' or '.join(missing)} group to copy, so the "
                 f"rooms written will not get one.")
            _say("        Designer's own files give every room both. If the result misbehaves, "
                 "build from a shell")
            _say("        that still has one worked room (`blank --keep-examples` from a "
                 "Designer-written file).")
        _say()

        module_types = read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                                      "ModuleType", "LutronModelInfoID")
        keypad_models = read_reference(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"),
                                       "KeypadModel", "LutronModelInfoID")

        _say(f"Reading schedule: {args.schedule}")
        sched = load_schedule(args.schedule, module_types, keypad_models)
        # Circuits with no fixture type cannot be built, so a schedule made
        # entirely of those has nothing in it either -- count only what would
        # actually reach the file.
        buildable = [ld for ld in sched.loads if not ld.unresolved]
        if not buildable and not sched.keypads:
            # Missing CSVs read as empty lists and an empty schedule validates,
            # so a folder with no schedules "built" successfully -- a .hw with
            # none of the house in it, reported as a success.
            #
            # The check is loads-and-keypads, NOT areas: a read can produce a
            # full room list and nothing else. A real Sonnet 5 read of House A
            # Manor (2026-08-07) returned 49 rooms, 31 fixture types and ZERO
            # circuits, keypads, buttons and scenes -- it could not resolve the
            # plan symbols and correctly refused to invent them. That validates
            # clean, renders a review sheet, and would have built 49 empty rooms.
            _say()
            _say(f"{args.schedule} has no circuits and no keypads, so there is "
                 f"nothing to build"
                 + (f" (it has {len(sched.areas)} room(s) and nothing in them)."
                    if sched.areas else ".")
                 + (f" All {len(sched.loads)} circuit(s) are missing a fixture "
                    f"type, so none of them can be built."
                    if sched.loads else "")
                 + " Check the extraction report -- the AI may have been unable "
                   "to read the plans. Re-read, or fix the CSVs, before building.")
            return 2
        try:
            validate(sched, set(shell.floors), set(shell.links), shell.modules, shell.keypads)
        except ValidationError as err:
            _say()
            _say(f"The schedule has {len(err.problems)} problem(s). Nothing was written.")
            _say()
            for prob in err.problems:
                _say(f"  * {prob}")
            return 2

        for warn in sched.warnings:
            _say(f"  note: {warn}")
        if sched.warnings:
            _say()

        builder = Builder(schema, shell, sched)
        builder.run()
        script = builder.em.build_script(guard_project_name=shell.project_name)

        if args.dump_sql:
            with open(args.dump_sql, "w", encoding="utf-8") as fh:
                fh.write(script)
            _say(f"Wrote SQL:        {args.dump_sql}")

        if args.review:
            mo = {m.name: (m.output_count or shell.modules[m.model_info_id].output_count)
                  for m in sched.modules if m.model_info_id in shell.modules}
            review.write(sched, args.review, args.project_name or shell.project_name,
                         os.path.basename(args.shell), mo)
            _say(f"Review sheet:     {args.review}")

        _say("Writing programme into the sandbox...")
        runner.script(script, box.name, label="build")

        _say()
        _say("Rows written:")
        _say(builder.em.summary())
        _say()
        _say(f"Object IDs used:  {builder.ids.used} "
             f"({builder.ids.start} -> {builder.ids.next - 1})")

        if args.only_new:
            # Runs after the build, not before: the writer clones rows Designer
            # wrote, so the shell's own programming has to survive long enough
            # to be copied from.
            _say("Removing the shell's own programming, keeping only what we just wrote...")
            blank.strip(runner, box.name, keep_from=builder.ids.start)

        if args.project_name:
            safe = args.project_name.replace("'", "''")
            # The site area carries the project name too, and it is the one the
            # engineer actually sees at the top of Designer's tree.
            # PlaceFirstSystemName is the "Place" the file belongs to; leaving
            # the old name there makes Designer open with a "Project name and
            # Place name do not match" question.
            runner.script(
                f"UPDATE tblProject SET Name = N'{safe}', PlaceFirstSystemName = N'{safe}';\n"
                f"UPDATE tblArea SET Name = N'{safe}' WHERE HierarchyLevel = 1;",
                box.name, label="rename")
            _say(f"Project renamed:  {args.project_name}")

        out_lut = os.path.join(work, "out.lut")
        box.backup_to(out_lut)
        sandbox.pack_hw(out_lut, args.out)
        size = os.path.getsize(args.out) / 1_048_576
        _say()
        _say(f"Wrote {args.out} ({size:.1f} MB)")
        _say("Open it in Lutron Designer to check it before transferring to a processor.")
        return 0
    finally:
        if not args.keep:
            box.drop()


# --------------------------------------------------------------------- blank

def cmd_blank(args) -> int:
    """Strip a finished project down to a reusable shell."""
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="hwwriter-"))
    os.makedirs(work, exist_ok=True)
    runner = make_runner(args, work)

    _say(f"Opening:          {args.shell}")
    lut = sandbox.unpack_hw(args.shell, os.path.join(work, "in"))
    box = sandbox.Sandbox(runner)
    try:
        box.restore(lut)
        shell = read_shell(runner, box.name)
        _say(f"Project:          {shell.project_name}")

        spare = None
        if args.keep_examples:
            spare = blank.example_kit(runner, box.name,
                                      include_modules=not args.strip_equipment)
            _say(f"Keeping one example of each model: "
                 f"{len(spare.enclosure_ids)} module(s), {len(spare.station_ids)} keypad(s), "
                 f"in {len(spare.area_ids)} room(s).")
        if args.strip_equipment:
            _say("Removing ALL equipment (processors, modules, comm links); "
                 "kept examples become unattached...")
        _say("Removing all rooms, loads, keypads and scenes...")
        blank.strip(runner, box.name, keep_from=shell.next_object_id, spare=spare,
                    strip_equipment=args.strip_equipment)

        if args.project_name:
            safe = args.project_name.replace("'", "''")
            # See cmd_build: PlaceFirstSystemName must follow the rename or
            # Designer asks about a project/place name mismatch on open.
            runner.script(f"UPDATE tblProject SET Name = N'{safe}', PlaceFirstSystemName = N'{safe}';\n"
                          f"UPDATE tblArea SET Name = N'{safe}' WHERE HierarchyLevel = 1;",
                          box.name, label="rename")

        left = runner.rows("""
SELECT 'floors', COUNT(*) FROM tblArea WHERE HierarchyLevel = 3
UNION ALL SELECT 'rooms', COUNT(*) FROM tblArea WHERE HierarchyLevel >= 4
UNION ALL SELECT 'links', COUNT(*) FROM tblLink
UNION ALL SELECT 'processors', COUNT(*) FROM tblProcessor
UNION ALL SELECT 'loads', COUNT(*) FROM tblZone
UNION ALL SELECT 'keypads', COUNT(*) FROM tblControlStationDevice;
""", box.name)
        _say()
        for name, count in ((r[0], r[1]) for r in left):
            _say(f"  {name:12s} {count:>5}")

        out_lut = os.path.join(work, "blank.lut")
        box.backup_to(out_lut)
        sandbox.pack_hw(out_lut, args.out)
        _say()
        _say(f"Wrote {args.out}")
        _say("Open it in Designer to confirm the floors and comm links you want are present.")
        return 0
    finally:
        box.drop()


# -------------------------------------------------------------------- review

def cmd_review(args) -> int:
    """Render the schedule for a human to check. Touches no database."""
    module_types = read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                                  "ModuleType", "LutronModelInfoID")
    keypad_models = read_reference(os.path.join(DOCS, "KeypadModels_REFERENCE.csv"),
                                   "KeypadModel", "LutronModelInfoID")
    sched = load_schedule(args.schedule, module_types, keypad_models)
    defaults = read_reference(os.path.join(DOCS, "ModuleTypes_REFERENCE.csv"),
                              "ModuleType", "DefaultOutputCount")
    mo = {m.name: (m.output_count or defaults.get(m.model_label, 0)) for m in sched.modules}
    review.write(sched, args.out, args.project_name or "Lighting schedule",
                 os.path.basename(args.schedule), mo)
    _say(f"Wrote {args.out}")
    _say(f"  {len(sched.areas)} rooms, {len(sched.loads)} circuits, "
         f"{len(sched.modules)} modules, {len(sched.keypads)} keypads, "
         f"{len(sched.scenes)} scenes.")
    _say("Open it, check it, then run `build`.")
    return 0


# -------------------------------------------------------------------- ingest

def cmd_ingest(args) -> int:
    """PDF -> the schedule CSVs (one Claude API call) -> review sheet. No database."""
    if os.path.isdir(args.out) and os.listdir(args.out) and not args.force:
        _say(f"Error: {args.out} is not empty. The CSVs there are the engineering "
             f"record -- pass --force to overwrite them.")
        return 2
    try:
        ingest.run(args.pdf, args.out, model=args.model,
                   notes=args.notes or "", say=_say,
                   keypad_family=args.keypad_family,
                   research=not args.no_research,
                   text_only_paths=args.text_only)
    except ingest.IngestError as exc:
        _say(f"\nError: {exc}")
        return 1

    _say()
    _say("Rendering the review sheet...")
    review_path = os.path.join(args.out, "review.html")
    ns = argparse.Namespace(schedule=args.out, out=review_path,
                            project_name=args.project_name)
    cmd_review(ns)
    _say()
    _say("Next: read EXTRACTION-REPORT.txt and the review sheet against the "
         "drawings, edit the CSVs until they are right, then run `build`.")
    return 0


# ------------------------------------------------------------------- inspect

def cmd_inspect(args) -> int:
    work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="hwwriter-"))
    runner = make_runner(args, work)
    lut = sandbox.unpack_hw(args.shell, os.path.join(work, "in"))
    box = sandbox.Sandbox(runner)
    try:
        box.restore(lut)
        shell = read_shell(runner, box.name)
        _say(f"Project:          {shell.project_name}")
        _say(f"Processors:       {shell.all_processor_ids} (master {shell.master_processor_id})")
        _say(f"Next object ID:   {shell.next_object_id}")
        _say()
        _say("Floors (rooms are placed under these):")
        for name, aid in sorted(shell.floors.items()):
            _say(f"  {name}  (AreaID {aid})")
        _say()
        _say("Comm links:")
        for name, (lid, info) in sorted(shell.links.items()):
            used = sorted(shell.link_addresses.get(lid, set()))
            free = [a for a in range(1, 60) if a not in set(used)][:6]
            _say(f"  {name}  (LinkID {lid}, LinkInfoID {info})")
            _say(f"      addresses used: {used or '(none)'}")
            _say(f"      next free:      {free}")
        _say()
        _say("Module types present in this shell (these are the ones you can write):")
        for model, bp in sorted(shell.modules.items()):
            _say(f"  ModelInfoID {model}  -> {bp.output_count} output(s)")
        _say()
        _say("Keypad models present in this shell:")
        for model, bp in sorted(shell.keypads.items()):
            _say(f"  ModelInfoID {model}  -> {len(bp.engraved_buttons)} engraved button(s), "
                 f"{len(bp.leds)} LED(s)")
        if args.sql:
            _say()
            _say(f"--- {args.sql} ---")
            for row in runner.rows(args.sql, box.name):
                _say("  " + " | ".join(row))
        return 0
    finally:
        box.drop()


# ----------------------------------------------------------------------- app

def cmd_app(args) -> int:
    from . import app as app_mod  # imported here so the bare CLI stays light
    return app_mod.main(port=args.port, open_browser=not args.no_browser)


# -------------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    ok = True
    _say(f"Python:           {sys.version.split()[0]}")

    docker = shutil.which("docker")
    _say(f"docker:           {docker or 'NOT FOUND'}")
    if docker:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True)
        if info.returncode == 0:
            _say(f"docker daemon:    running ({info.stdout.strip()})")
        else:
            _say("docker daemon:    NOT RUNNING -- start Docker Desktop")
            ok = False

    sqlcmd = shutil.which("sqlcmd") or shutil.which("sqlcmd.exe")
    _say(f"local sqlcmd:     {sqlcmd or 'not found (only needed with --server)'}")

    for name in ("ModuleTypes_REFERENCE.csv", "KeypadModels_REFERENCE.csv",
                 "LoadTypes_REFERENCE.csv"):
        path = os.path.join(DOCS, name)
        _say(f"{name:34s} {'ok' if os.path.exists(path) else 'MISSING'}")
        ok = ok and os.path.exists(path)

    if docker and not args.no_start:
        work = os.path.abspath(args.work or tempfile.mkdtemp(prefix="hwwriter-"))
        try:
            runner = ensure_container(work, args.container)
            version = runner.scalar("SELECT @@VERSION")
            _say(f"SQL Server:       {str(version).splitlines()[0]}")
        except Exception as exc:  # noqa: BLE001 - doctor reports rather than raises
            _say(f"SQL Server:       FAILED -- {exc}")
            ok = False

    _say()
    _say("All good." if ok else "Some checks failed -- see above.")
    return 0 if ok else 1


# ----------------------------------------------------------------------- cli

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="hwwriter",
        description="Write a Lutron HomeWorks .hw programme from engineering schedules.")
    ap.add_argument("--container", default=CONTAINER, help="Docker container name")
    ap.add_argument("--server", help="use a local SQL Server instead of Docker, "
                                     r"e.g. (localdb)\MSSQLLocalDB")
    ap.add_argument("--user", help="SQL login (omit for Windows authentication)")
    ap.add_argument("--password", help="SQL password")
    ap.add_argument("--work", help="working directory (default: a temp dir)")

    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a .hw from a shell plus schedule CSVs")
    b.add_argument("--shell", required=True, help="the empty .hw authored in Designer")
    b.add_argument("--schedule", required=True, help="folder holding the schedule CSVs")
    b.add_argument("--out", required=True, help="the .hw to write")
    b.add_argument("--project-name", help="rename the project inside the output file")
    b.add_argument("--review", help="also write an HTML review sheet here")
    b.add_argument("--dump-sql", help="also write the generated SQL here, for review")
    b.add_argument("--only-new", action="store_true",
                   help="strip the shell's own rooms, loads and keypads after building, "
                        "leaving a file that contains only this schedule")
    b.add_argument("--keep", action="store_true", help="leave the sandbox database in place")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("review", help="render the schedule as an HTML sheet to check")
    v.add_argument("--schedule", required=True, help="folder holding the schedule CSVs")
    v.add_argument("--out", required=True, help="the .html file to write")
    v.add_argument("--project-name", help="title for the sheet")
    v.set_defaults(func=cmd_review)

    k = sub.add_parser("blank", help="strip a finished project down to a reusable shell")
    k.add_argument("--shell", required=True, help="the finished .hw to strip")
    k.add_argument("--out", required=True, help="the shell .hw to write")
    k.add_argument("--project-name", help="rename the project inside the output file")
    k.add_argument("--keep-examples", action="store_true",
                   help="keep one example of each module and keypad model, so the shell can "
                        "still be used to write those models")
    k.add_argument("--strip-equipment", action="store_true",
                   help="also remove ALL processors, module enclosures and comm links -- the "
                        "zero-equipment shell. Kept example loads/keypads become unattached "
                        "(Designer's own off-link state); with --keep-examples only keypad "
                        "model examples are kept, since modules cannot exist without enclosures")
    k.set_defaults(func=cmd_blank)

    g = sub.add_parser("ingest", help="read a lighting-plans PDF into the eight "
                                      "schedule CSVs (one Claude API call)")
    g.add_argument("--pdf", required=True, nargs="+", help="the lighting plans PDF(s) -- several sheets go to the AI as one set")
    g.add_argument("--out", required=True, help="folder to write the schedule CSVs into")
    g.add_argument("--project-name", help="title for the review sheet")
    g.add_argument("--notes", help="extra context for the extraction (site quirks, "
                                   "known circuit conventions, what to ignore)")
    g.add_argument("--keypad-family", default="",
                   help="keypad family to use throughout (drawings rarely specify); "
                        "omit to let the AI choose one and say which")
    g.add_argument("--model", default=ingest.DEFAULT_MODEL,
                   help=f"Claude model to read the plans with (default {ingest.DEFAULT_MODEL})")
    g.add_argument("--text-only", nargs="+", default=[], metavar="PDF",
                   help="read these PDFs as TEXT rather than as pages -- for a "
                        "specification or schedule document, where the information "
                        "is the words. Far cheaper, and the only way to send a file "
                        "over the API's ~32 MB limit. NEVER use it on a drawing: a "
                        "drawing's content is the picture")
    g.add_argument("--no-research", action="store_true",
                   help="do NOT look up fittings the drawings name but give no "
                        "electrical data for. Cheaper and quicker; wattages the "
                        "drawings do not state arrive blank and flagged, never guessed")
    g.add_argument("--force", action="store_true",
                   help="overwrite an existing non-empty schedules folder")
    g.set_defaults(func=cmd_ingest)

    i = sub.add_parser("inspect", help="report what a .hw contains")
    i.add_argument("--shell", required=True)
    i.add_argument("--sql", help="also run this query against the restored project")
    i.set_defaults(func=cmd_inspect)

    a = sub.add_parser("app", help="run Lutron Builder -- the local web app around "
                                   "the writer (key -> PDF -> check -> .hw)")
    a.add_argument("--port", type=int, default=8323)
    a.add_argument("--no-browser", action="store_true", help="don't open the browser")
    a.set_defaults(func=cmd_app)

    d = sub.add_parser("doctor", help="check this machine can run the writer")
    d.add_argument("--no-start", action="store_true", help="skip starting the container")
    d.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (SqlError, FileNotFoundError) as exc:
        _say()
        _say(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
