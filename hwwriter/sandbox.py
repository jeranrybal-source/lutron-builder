"""
The `.hw` container, and the sandbox database we open it into.

A `.hw` file is a ZIP holding exactly one `.lut`, and a `.lut` is an ordinary
SQL Server backup.  So the round trip is:

    .hw  --unzip-->  .lut  --RESTORE-->  sandbox DB  --BACKUP-->  .lut  --zip-->  .hw

The source `.hw` is opened read-only and never written to.  Every build creates
a fresh, uniquely named sandbox database and drops it afterwards, so a crashed
run leaves at most one stale database behind (and `sweep_stale` clears those).
"""

from __future__ import annotations

import os
import re
import struct
import time
import uuid
import zipfile

from .sqlrunner import BaseRunner, SqlError


def unpack_hw(hw_path: str, dest_dir: str) -> str:
    """Extract the single .lut out of a .hw. Returns the extracted path."""
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(hw_path) as zf:
        luts = [n for n in zf.namelist() if n.lower().endswith(".lut")]
        if len(luts) != 1:
            raise SqlError(
                f"{os.path.basename(hw_path)} contains {len(luts)} .lut files; expected exactly 1. "
                "Is this really a Lutron Designer .hw archive?")
        out = zf.extract(luts[0], dest_dir)
    os.chmod(out, 0o666)
    return out


def pack_hw(lut_path: str, hw_path: str, inner_name: str | None = None) -> str:
    """
    Zip a .lut into a .hw, matching how Designer writes the archive.

    Designer's own `.hw` records the entry as MS-DOS/NT in origin with zero
    external attributes. Python zips on macOS and Linux instead announce Unix
    origin and stamp Unix permission bits in, which is a legitimate ZIP either
    way but is not what Designer produces -- and this format has already proved
    to be fussier than its documentation suggests. Matching it costs nothing.
    """
    inner = inner_name or f"{uuid.uuid4()}.lut"
    os.makedirs(os.path.dirname(os.path.abspath(hw_path)) or ".", exist_ok=True)

    with open(lut_path, "rb") as fh:
        data = fh.read()

    zi = zipfile.ZipInfo(inner, date_time=time.localtime(os.path.getmtime(lut_path))[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 0        # MS-DOS / NT FAT, as Designer writes
    zi.create_version = 20
    zi.extract_version = 20
    # Assemble beside the target and swap in one step: writing the final path
    # directly means a rebuild that dies mid-write leaves the PREVIOUS .hw
    # truncated -- while the app still advertises it as openable.
    tmp = hw_path + ".writing"
    try:
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr(zi, data)

        # CPython stamps 0o600 << 16 into external_attr whenever it is zero;
        # Designer leaves it at zero. Patch the central-directory record back.
        raw = bytearray(open(tmp, "rb").read())
        at = raw.find(b"PK\x01\x02")
        if at > 0:
            struct.pack_into("<I", raw, at + 38, 0)
            with open(tmp, "wb") as fh:
                fh.write(raw)
        os.replace(tmp, hw_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return hw_path


class Sandbox:
    """
    A restored copy of a Lutron project we can safely write to.

    The database is called `Project`, and its files `Project.mdf` /
    `Project_log.ldf`, because that is what Designer's own backups carry -- the
    database name is recorded in the backup header, and a `.lut` whose header
    names some working database rather than `Project` is not what Designer
    expects to be handed back.

    The consequence is that only one build can run at a time on a given SQL
    Server. That is fine for a desktop tool and cheap next to producing a file
    Designer refuses.
    """

    NAME = "Project"

    def __init__(self, runner: BaseRunner, name: str | None = None):
        self.runner = runner
        self.name = name or self.NAME
        self._restored = False

    # ------------------------------------------------------------------ setup

    def restore(self, lut_host_path: str) -> None:
        guest = self.runner.stage(lut_host_path)

        rows = self.runner.rows(f"RESTORE FILELISTONLY FROM DISK = N'{_esc(guest)}'")
        data = log = None
        physical: list[str] = []
        for r in rows:
            logical, _physical, kind = r[0], r[1], r[2]
            physical.append(_physical)
            if kind == "D" and data is None:
                data = logical
            elif kind == "L" and log is None:
                log = logical

        # A backup taken by SQL Server on Linux records POSIX paths, and Windows
        # SQL Server refuses it at the door with "System table sysfiles1 is
        # corrupted" -- an error that says nothing about the real cause. Catch it
        # here, where we can say what actually happened.
        if os.name == "nt" and any(p.startswith("/") for p in physical):
            raise SqlError(
                f"{os.path.basename(lut_host_path)} was built by SQL Server on Linux "
                f"(it records the path {physical[0]}).\n"
                "Windows SQL Server cannot restore such a backup, and neither can "
                "Lutron Designer.\n"
                "Rebuild this file on Windows -- for a shell, run the `blank` command "
                "here against a .hw that Designer itself wrote.")
        if not data or not log:
            raise SqlError(
                f"RESTORE FILELISTONLY on {os.path.basename(lut_host_path)} did not return "
                "both a data and a log file. The .lut may be truncated.")

        target_dir = self._data_dir()
        # Take the database offline first if a previous run left it behind;
        # REPLACE alone will not evict an open connection.
        #
        # This must not be fatal. A run that died mid-RESTORE leaves the
        # database in RECOVERY_PENDING, where ALTER DATABASE fails outright
        # ("System table sysfiles1 is corrupted") -- and without the fallback
        # every later build dies here instead of simply overwriting the wreck.
        # DROP clears that state where ALTER cannot, and RESTORE ... REPLACE
        # below recreates the database from scratch either way.
        try:
            self.runner.script(f"""
IF DB_ID(N'{self.name}') IS NOT NULL
  ALTER DATABASE [{self.name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
""", label="evict")
        except SqlError:
            try:
                self.runner.script(
                    f"IF DB_ID(N'{self.name}') IS NOT NULL DROP DATABASE [{self.name}];",
                    label="evict (drop)")
            except SqlError:
                pass  # let RESTORE ... REPLACE report the real problem
        self.runner.script(f"""
RESTORE DATABASE [{self.name}]
  FROM DISK = N'{_esc(guest)}'
  WITH MOVE N'{_esc(data)}' TO N'{target_dir}{self.name}.mdf',
       MOVE N'{_esc(log)}'  TO N'{target_dir}{self.name}_log.ldf',
       REPLACE, RECOVERY;
ALTER DATABASE [{self.name}] SET MULTI_USER;
""", label="restore")
        self._restored = True

    def _data_dir(self) -> str:
        d = self.runner.scalar(
            "SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000))")
        if not d:
            raise SqlError("could not determine the SQL Server default data path")
        return d

    # ----------------------------------------------------------------- output

    def backup_to(self, lut_host_path: str) -> None:
        """BACKUP the sandbox out to a .lut. Must run outside a transaction."""
        guest = self.runner.guest_path(lut_host_path)
        self.runner.script(f"""
BACKUP DATABASE [{self.name}]
  TO DISK = N'{_esc(guest)}'
  WITH FORMAT, INIT, SKIP;
""", label="backup")
        self.runner.collect(guest, lut_host_path)

    # ---------------------------------------------------------------- teardown

    def drop(self) -> None:
        if not self._restored:
            return
        try:
            self.runner.script(f"""
USE [master];
IF DB_ID(N'{self.name}') IS NOT NULL
BEGIN
  ALTER DATABASE [{self.name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
  DROP DATABASE [{self.name}];
END
""", label="drop")
        except SqlError:
            # A wedged sandbox is not worth failing an otherwise good build
            # over; sweep_stale() will clear it on the next run.
            pass
        self._restored = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.drop()
        return False


def sweep_stale(runner: BaseRunner, older_than_hours: int = 2) -> list[str]:
    """Drop HwBuild_* sandboxes left behind by crashed runs."""
    rows = runner.rows(f"""
SELECT name FROM sys.databases
WHERE name LIKE 'HwBuild[_]%'
  AND create_date < DATEADD(HOUR, -{int(older_than_hours)}, GETUTCDATE());
""")
    dropped = []
    for (name,) in (tuple(r) for r in rows):
        if not re.fullmatch(r"HwBuild_\d+", name):
            continue
        try:
            runner.script(f"""
USE [master];
ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
DROP DATABASE [{name}];
""", label="sweep")
            dropped.append(name)
        except SqlError:
            pass
    return dropped


def _esc(s: str) -> str:
    return s.replace("'", "''")
