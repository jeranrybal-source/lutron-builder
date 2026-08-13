"""
Writing rows into a Lutron project database.

Lutron's own `ins_*` stored procedures take up to 149 parameters each, with
meanings that are undocumented outside Lutron.  Filling them in by hand means
guessing at ~140 values per fixture and hoping Designer tolerates the result.

This module takes a different route: **clone a row Designer itself wrote**, and
override only the handful of columns that carry our meaning.  Every other
column keeps a value that is known-good because Designer produced it in a
project that opens cleanly.

    INSERT INTO tblZone (ZoneID, ParentID, Name, ...all other columns...)
    SELECT 116050, 116041, N'Kitchen Downlights', ...all other columns...
    FROM tblZone WHERE ZoneID = 722;

The column list is read from the live database at build time, so a Designer
version that adds or drops a column keeps working without a code change --
which matters, because the file format is not a published contract.

The one thing the procs did for us that we now do ourselves is ID allocation:
every object in a Lutron project draws a unique ID from `tblNextObjectID`.
`IdAllocator` reserves a block up front and writes the counter back at the end.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass, field

from .sqlrunner import BaseRunner, SqlError

# --------------------------------------------------------------------- values

class Raw(str):
    """A SQL expression to emit verbatim rather than quote (e.g. NEWID())."""


def lit(v) -> str:
    """Render a Python value as a T-SQL literal."""
    if isinstance(v, Raw):
        return str(v)
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "N'" + str(v).replace("'", "''") + "'"


def new_xid() -> str:
    """Designer stamps most objects with a 22-char url-safe base64 GUID."""
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")


# ------------------------------------------------------------------ id supply

class IdAllocator:
    """Hands out object IDs from the project's own counter."""

    def __init__(self, start: int):
        self.start = int(start)
        self.next = int(start)

    def take(self) -> int:
        v = self.next
        self.next += 1
        return v

    @property
    def used(self) -> int:
        return self.next - self.start


# -------------------------------------------------------------------- schema

class Schema:
    """Column lists for the tables we write, read from the live database."""

    def __init__(self, runner: BaseRunner, database: str):
        self.runner = runner
        self.database = database
        self._cols: dict[str, list[str]] = {}

    def columns(self, table: str) -> list[str]:
        if table not in self._cols:
            rows = self.runner.rows(f"""
SELECT c.name FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
WHERE t.name = '{table}' ORDER BY c.column_id;
""", self.database)
            if not rows:
                raise SqlError(f"table {table} not found in the shell database")
            self._cols[table] = [r[0] for r in rows]
        return self._cols[table]

    def has_column(self, table: str, column: str) -> bool:
        return column in self.columns(table)


# ------------------------------------------------------------------- emitter

@dataclass
class Emitter:
    """Accumulates the INSERT statements that make up one build."""

    schema: Schema
    ids: IdAllocator
    statements: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    # -- the workhorse ----------------------------------------------------

    def clone(self, table: str, template_where: str, overrides: dict) -> None:
        """
        Copy one row of `table` (selected by `template_where`), replacing the
        named columns.  Unknown column names are a hard error rather than a
        silent no-op -- a typo here would otherwise produce a row that looks
        written but carries the template's value.
        """
        cols = self.schema.columns(table)
        unknown = set(overrides) - set(cols)
        if unknown:
            raise SqlError(f"{table}: no such column(s): {', '.join(sorted(unknown))}")

        select = [lit(overrides[c]) if c in overrides else f"[{c}]" for c in cols]
        collist = ", ".join(f"[{c}]" for c in cols)
        self.statements.append(
            f"INSERT INTO [{table}] ({collist})\n"
            f"SELECT {', '.join(select)}\n"
            f"FROM [{table}] WHERE {template_where};")
        self.counts[table] = self.counts.get(table, 0) + 1

    def insert(self, table: str, values: dict) -> None:
        """Insert a row with no template -- for narrow tables we fully specify."""
        cols = self.schema.columns(table)
        unknown = set(values) - set(cols)
        if unknown:
            raise SqlError(f"{table}: no such column(s): {', '.join(sorted(unknown))}")
        present = [c for c in cols if c in values]
        collist = ", ".join(f"[{c}]" for c in present)
        vals = ", ".join(lit(values[c]) for c in present)
        self.statements.append(f"INSERT INTO [{table}] ({collist}) VALUES ({vals});")
        self.counts[table] = self.counts.get(table, 0) + 1

    def raw(self, sql: str) -> None:
        self.statements.append(sql)

    def comment(self, text: str) -> None:
        # A comment is the one place a raw name reaches SQL unquoted. A CSV
        # field may legally contain a newline (quoted), and a newline ends a
        # `--` comment -- so anything after it would execute as SQL. Flatten
        # every line break before emitting.
        flat = " ".join(str(text).splitlines())
        self.statements.append(f"\n-- {flat}")

    # -- assembly ---------------------------------------------------------

    def build_script(self, guard_project_name: str | None = None) -> str:
        """
        Wrap the accumulated statements in one transaction.

        XACT_ABORT ON means any error rolls the whole build back rather than
        leaving the project half-written -- which for a lighting programme is
        the difference between "try again" and "silently missing six circuits".
        """
        head = [
            "SET NOCOUNT ON;",
            "SET XACT_ABORT ON;",
            "BEGIN TRANSACTION;",
        ]
        if guard_project_name is not None:
            head.append(f"""
IF NOT EXISTS (SELECT 1 FROM tblProject WHERE Name = {lit(guard_project_name)})
BEGIN
  RAISERROR('Shell project name changed under us -- aborting build.', 16, 1);
END
""")
        tail = [
            f"\nUPDATE tblNextObjectID SET NextObjectID = {self.ids.next};",
            "COMMIT TRANSACTION;",
        ]
        return "\n".join(head + self.statements + tail)

    def summary(self) -> str:
        width = max((len(t) for t in self.counts), default=0)
        lines = [f"  {t.ljust(width)}  {n:>5}" for t, n in sorted(self.counts.items())]
        return "\n".join(lines)
