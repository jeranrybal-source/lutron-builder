"""
Talking to SQL Server without a database driver.

Everything goes through `sqlcmd`, which ships inside the Microsoft SQL Server
Docker image and with SQL Server / LocalDB on Windows.  That keeps this tool
free of pyodbc / FreeTDS / ODBC-driver installation pain, which is the single
biggest setup obstacle on a fresh machine.

Three backends:

  DockerRunner      -- macOS / Linux.  Runs `docker exec <container> sqlcmd ...`.
                       Files are exchanged through a bind-mounted work directory.
  PowerShellRunner  -- Windows, and the default there.  Drives the .NET SQL
                       client built into Windows through a persistent PowerShell
                       helper, so nothing needs installing.  Works against the
                       LocalDB that ships with Lutron Designer, including on
                       ARM64 Windows where `(localdb)\\X` connection strings
                       fail (see the class docstring).
  LocalRunner       -- Runs a locally installed `sqlcmd.exe` against any
                       reachable instance.  Kept for machines that have it.

All expose the same operations the builder needs: run a script, run a query
and get rows back, and translate a host path into the path the SQL Server
process will see.

NOTE (confirmed 2026-08-01): a backup produced by SQL Server on Linux does not
restore on SQL Server on Windows ("System table sysfiles1 is corrupted"), even
at the identical build, so a .hw whose .lut was written via DockerRunner will
never open in Designer.  Files for Designer MUST be produced on Windows.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass


class SqlError(RuntimeError):
    pass


# sqlcmd's row separator.  A vertical bar would collide with engraving text and
# fixture descriptions, so use something no human will type into a schedule.
SEP = "\x1f"


@dataclass
class _Result:
    columns: list[str]
    rows: list[dict]

    def scalar(self):
        if not self.rows:
            return None
        return next(iter(self.rows[0].values()))


class BaseRunner:
    """Shared parsing / retry logic.  Subclasses provide `_exec`."""

    def _exec(self, args: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess:
        raise NotImplementedError

    def guest_path(self, host_path: str) -> str:
        """Path as the SQL Server process sees it."""
        raise NotImplementedError

    def stage(self, host_path: str) -> str:
        """Copy a file somewhere SQL Server can read it; return the guest path."""
        raise NotImplementedError

    def collect(self, guest_path: str, host_path: str) -> None:
        """Copy a file SQL Server wrote back out to the host."""
        raise NotImplementedError

    # ---------------------------------------------------------------- queries

    def query(self, sql: str, database: str | None = None) -> _Result:
        """Run a SELECT and return typed-as-text rows."""
        args = ["-d", database or "master", "-Q", sql,
                "-s", SEP, "-W", "-h", "-1", "-w", "65535"]
        proc = self._exec(args)
        out = proc.stdout.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise SqlError(f"query failed:\n{out}\n{proc.stderr.decode('utf-8', 'replace')}")
        return self._parse(out)

    def _parse(self, out: str) -> _Result:
        rows = []
        for line in out.splitlines():
            if not line.strip():
                continue
            if line.strip().endswith("rows affected)") or line.strip().endswith("row affected)"):
                continue
            rows.append(line.split(SEP))
        # `-h -1` suppresses headers, so callers index positionally.  We hand
        # back dicts keyed by position for uniformity with the header case.
        # The column list is as wide as the WIDEST row, so a short row is
        # ordinary and zip is allowed to stop at its end.
        width = max((len(r) for r in rows), default=0)
        cols = [str(i) for i in range(width)]
        return _Result(cols, [dict(zip(cols, r, strict=False)) for r in rows])

    def rows(self, sql: str, database: str | None = None) -> list[list[str]]:
        res = self.query(sql, database)
        return [[v.strip() if isinstance(v, str) else v for v in r.values()] for r in res.rows]

    def scalar(self, sql: str, database: str | None = None):
        r = self.rows(sql, database)
        return r[0][0] if r else None

    # ---------------------------------------------------------------- scripts

    def script(self, sql: str, database: str | None = None, label: str = "script") -> str:
        """Run a (possibly very large) batch.  Raises on any error."""
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(sql)
            host = fh.name
        try:
            guest = self.stage(host)
            args = ["-d", database or "master", "-i", guest,
                    "-b", "-w", "65535"]
            proc = self._exec(args)
            out = (proc.stdout + b"\n" + proc.stderr).decode("utf-8", "replace")
            if proc.returncode != 0:
                raise SqlError(f"{label} failed:\n{out[-8000:]}")
            return out
        finally:
            try:
                os.unlink(host)
            except OSError:
                pass

    def wait_ready(self, timeout: int = 180) -> None:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            try:
                if self.scalar("SELECT 1") == "1":
                    return
            except Exception as exc:  # noqa: BLE001 - surfaced below on timeout
                last = str(exc)
            time.sleep(3)
        raise SqlError(f"SQL Server did not become ready within {timeout}s. Last error:\n{last}")


class DockerRunner(BaseRunner):
    """Drives sqlcmd inside a running SQL Server container (macOS / Linux)."""

    def __init__(self, container: str, password: str, workdir: str,
                 guest_workdir: str = "/work"):
        self.container = container
        self.password = password
        self.workdir = os.path.abspath(workdir)
        self.guest_workdir = guest_workdir
        os.makedirs(self.workdir, exist_ok=True)
        self._sqlcmd = None

    def _find_sqlcmd(self) -> str:
        if self._sqlcmd:
            return self._sqlcmd
        for cand in ("/opt/mssql-tools18/bin/sqlcmd", "/opt/mssql-tools/bin/sqlcmd", "sqlcmd"):
            probe = subprocess.run(
                ["docker", "exec", self.container, "bash", "-lc", f"test -x {cand} || command -v {cand}"],
                capture_output=True)
            if probe.returncode == 0:
                self._sqlcmd = cand
                return cand
        raise SqlError(f"no sqlcmd found inside container '{self.container}'")

    def _exec(self, args, stdin=None):
        cmd = ["docker", "exec", "-i", self.container, self._find_sqlcmd(),
               "-S", "localhost", "-U", "sa", "-P", self.password, "-C"] + args
        return subprocess.run(cmd, capture_output=True, input=stdin)

    def guest_path(self, host_path: str) -> str:
        host_path = os.path.abspath(host_path)
        rel = os.path.relpath(host_path, self.workdir)
        if rel.startswith(".."):
            raise SqlError(f"{host_path} is outside the mounted work dir {self.workdir}")
        return f"{self.guest_workdir}/{rel.replace(os.sep, '/')}"

    def stage(self, host_path: str) -> str:
        host_path = os.path.abspath(host_path)
        try:
            return self.guest_path(host_path)
        except SqlError:
            dest = os.path.join(self.workdir, os.path.basename(host_path))
            shutil.copyfile(host_path, dest)
            os.chmod(dest, 0o666)
            return self.guest_path(dest)

    def collect(self, guest_path: str, host_path: str) -> None:
        rel = guest_path[len(self.guest_workdir) + 1:]
        src = os.path.join(self.workdir, rel)
        if os.path.abspath(src) != os.path.abspath(host_path):
            shutil.copyfile(src, host_path)


class LocalRunner(BaseRunner):
    """Drives a locally installed sqlcmd (Windows, or anywhere with mssql-tools)."""

    def __init__(self, server: str, password: str | None = None,
                 user: str | None = None, trusted: bool = True):
        self.server = server
        self.password = password
        self.user = user
        self.trusted = trusted
        exe = shutil.which("sqlcmd") or shutil.which("sqlcmd.exe")
        if not exe:
            raise SqlError("sqlcmd not found on PATH. Install the SQL Server command-line tools.")
        self.exe = exe

    def _exec(self, args, stdin=None):
        auth = ["-E"] if self.trusted else ["-U", self.user or "sa", "-P", self.password or ""]
        cmd = [self.exe, "-S", self.server] + auth + ["-C"] + args
        return subprocess.run(cmd, capture_output=True, input=stdin)

    def guest_path(self, host_path: str) -> str:
        return os.path.abspath(host_path)

    def stage(self, host_path: str) -> str:
        return os.path.abspath(host_path)

    def collect(self, guest_path: str, host_path: str) -> None:
        if os.path.abspath(guest_path) != os.path.abspath(host_path):
            shutil.copyfile(guest_path, host_path)


# The PowerShell worker: a tiny stdin/stdout server around the .NET SQL client.
# Requests and row data travel base64-encoded, which sidesteps every console
# code-page and quoting hazard on both sides of the pipe.
#
# Output must stay in step with what sqlcmd prints, because the rest of the
# tool parses it: NULL as the word NULL, bit as 0/1, uniqueidentifier in upper
# case, binary as 0x.., invariant-culture numbers, no headers.
_WORKER_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Data
$server = $env:HWWRITER_SERVER
if ($server -like '(localdb)\*') {
  # A native process on ARM64 Windows cannot load LocalDB's x64
  # SqlUserInstance.dll, so '(localdb)\X' fails with provider error 56.
  # Start the instance ourselves and swap in its named pipe.
  try {
    $name = $server.Split('\')[1]
    & sqllocaldb start $name 2>$null | Out-Null
    foreach ($line in (& sqllocaldb info $name)) {
      if ($line -match 'np:\\\\\.\\pipe\\\S+') { $server = $Matches[0]; break }
    }
  } catch { }
}
$user = $env:HWWRITER_USER
$pass = $env:HWWRITER_PASSWORD
$inv = [Globalization.CultureInfo]::InvariantCulture
function Get-Conn([string]$db) {
  if ($user) { $cs = "Server=$server;User ID=$user;Password=$pass;Initial Catalog=$db;TrustServerCertificate=true" }
  else       { $cs = "Server=$server;Integrated Security=true;Initial Catalog=$db;TrustServerCertificate=true" }
  $cn = New-Object System.Data.SqlClient.SqlConnection $cs
  $cn.Open()
  return $cn
}
[Console]::WriteLine('READY')
while ($true) {
  $req = [Console]::In.ReadLine()
  if ($null -eq $req) { break }
  if ($req -eq '') { continue }
  $parts = $req.Split("`t")
  $mode = $parts[0]
  $db   = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[1]))
  $sql  = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($parts[2]))
  try {
    $cn = Get-Conn $db
    try {
      $cmd = $cn.CreateCommand()
      $cmd.CommandText = $sql
      $cmd.CommandTimeout = 600
      if ($mode -eq 'Q') {
        $rd = $cmd.ExecuteReader()
        $sep = [string][char]0x1f
        while ($rd.Read()) {
          $vals = New-Object string[] $rd.FieldCount
          for ($i = 0; $i -lt $rd.FieldCount; $i++) {
            $v = $rd.GetValue($i)
            if     ($v -is [DBNull])   { $vals[$i] = 'NULL' }
            elseif ($v -is [bool])     { $vals[$i] = if ($v) { '1' } else { '0' } }
            elseif ($v -is [byte[]])   { $vals[$i] = '0x' + (($v | ForEach-Object { $_.ToString('X2') }) -join '') }
            elseif ($v -is [guid])     { $vals[$i] = $v.ToString().ToUpper() }
            elseif ($v -is [datetime]) { $vals[$i] = $v.ToString('yyyy-MM-dd HH:mm:ss.fff', $inv) }
            elseif ($v -is [decimal] -or $v -is [double] -or $v -is [single]) { $vals[$i] = $v.ToString($inv) }
            else { $vals[$i] = [string]$v }
          }
          $row = [string]::Join($sep, $vals)
          [Console]::WriteLine('R ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($row)))
        }
        $rd.Close()
      } else {
        $null = $cmd.ExecuteNonQuery()
      }
      [Console]::WriteLine('OK')
    } finally { $cn.Close() }
  } catch {
    $msg = $_.Exception.ToString()
    [Console]::WriteLine('ERR ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($msg)))
  }
  [Console]::Out.Flush()
}
"""


class PowerShellRunner(BaseRunner):
    """
    Windows without sqlcmd: drives the .NET SQL client through one persistent
    PowerShell child process, so the target machine needs nothing beyond
    Python and what Windows (plus Lutron Designer's LocalDB) already has.

    Confirmed working on ARM64 Windows (a VM on Apple Silicon), where the
    stock `(localdb)\\X` connection path fails outright: the worker resolves
    the instance to its named pipe via sqllocaldb.exe before connecting.
    """

    def __init__(self, server: str = r"(localdb)\MSSQLLocalDB",
                 password: str | None = None, user: str | None = None,
                 trusted: bool = True):
        if os.name != "nt":
            raise SqlError("the PowerShell backend only works on Windows")
        self.server = server
        env = os.environ.copy()
        env["HWWRITER_SERVER"] = server
        if user and not trusted:
            env["HWWRITER_USER"] = user
            env["HWWRITER_PASSWORD"] = password or ""
        exe = shutil.which("powershell") or shutil.which("powershell.exe") or "powershell.exe"
        encoded = base64.b64encode(_WORKER_PS.encode("utf-16-le")).decode("ascii")
        self._proc = subprocess.Popen(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        deadline = time.time() + 60
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                err = (self._proc.stderr.read() or b"").decode("utf-8", "replace")
                raise SqlError(f"the PowerShell SQL worker did not start:\n{err[-4000:]}")
            if raw.decode("utf-8", "replace").strip() == "READY":
                break
            if time.time() > deadline:
                raise SqlError("the PowerShell SQL worker did not report READY within 60s")

    # ------------------------------------------------------------- transport

    def _request(self, mode: str, database: str | None, sql: str) -> list[str]:
        p = self._proc
        if p.poll() is not None:
            raise SqlError("the PowerShell SQL worker has exited")
        db64 = base64.b64encode((database or "master").encode("utf-8")).decode("ascii")
        sql64 = base64.b64encode(sql.encode("utf-8")).decode("ascii")
        p.stdin.write(f"{mode}\t{db64}\t{sql64}\n".encode("ascii"))
        p.stdin.flush()
        rows: list[str] = []
        while True:
            raw = p.stdout.readline()
            if not raw:
                err = (p.stderr.read() or b"").decode("utf-8", "replace")
                raise SqlError(f"the PowerShell SQL worker died mid-request:\n{err[-4000:]}")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("R "):
                rows.append(base64.b64decode(line[2:]).decode("utf-8", "replace"))
            elif line == "OK":
                return rows
            elif line.startswith("ERR "):
                raise SqlError(base64.b64decode(line[4:]).decode("utf-8", "replace"))
            # anything else is stray console noise; ignore it

    # -------------------------------------------------------------- queries

    def query(self, sql: str, database: str | None = None) -> _Result:
        parsed = [r.split(SEP) for r in self._request("Q", database, sql)]
        width = max((len(r) for r in parsed), default=0)
        cols = [str(i) for i in range(width)]
        return _Result(cols, [dict(zip(cols, r, strict=False)) for r in parsed])

    def script(self, sql: str, database: str | None = None, label: str = "script") -> str:
        try:
            self._request("X", database, sql)
        except SqlError as exc:
            raise SqlError(f"{label} failed:\n{exc}") from None
        return ""

    # ---------------------------------------------------------------- paths

    def guest_path(self, host_path: str) -> str:
        return os.path.abspath(host_path)

    def stage(self, host_path: str) -> str:
        return os.path.abspath(host_path)

    def collect(self, guest_path: str, host_path: str) -> None:
        if os.path.abspath(guest_path) != os.path.abspath(host_path):
            shutil.copyfile(guest_path, host_path)

    # ------------------------------------------------------------- teardown

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def __del__(self):
        self.close()
