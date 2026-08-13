"""
Finding references left pointing at rows that were deleted.

`cascade.py` follows foreign keys downwards, and `orphans.py` cleans children
whose polymorphic parent has gone.  Neither catches the third case: a row that
*survives* while holding a plain integer pointer to a row that did not.

Lutron's schema is full of these. `tblSlcToPowerLookupFuncMap` points at switch
leg controllers, `tblSequenceStep` at presets, `tblEngravingStyle` at devices,
`tblDeviceLookup` at devices, `TblDomainObjectQuoteProperties` at anything at
all -- none with a foreign key to enforce it. Designer restores such a file
without complaint and then refuses it during validation, which is what a
"[10681] error encountered while opening this file" at 100% looks like.

Rather than enumerate those tables (the list is long and version-dependent),
this works by difference: record every object ID before the delete, record them
again after, and treat the set that vanished as poison. Any row anywhere still
holding one of those IDs is removed.

Two guards keep that from over-reaching:

  * Columns naming Designer's product catalogue (`ModelInfoID`, `LedInfoId`,
    `LoadTypeID` …) are excluded. Those ID spaces overlap with object IDs -- a
    `ModelInfoID` of 2472 is a real Lutron part, not object 2472 -- so sweeping
    them would delete good rows.
  * A column already holding unresolvable values *before* the delete is left
    alone. It was never an object reference to begin with.
"""

from __future__ import annotations

from .cascade import ForeignKeyGraph, cascade_delete
from .sqlrunner import BaseRunner

BEFORE = "_hwwriter_ids_before"
AFTER = "_hwwriter_ids_after"

# Column-name patterns that reference Designer's installed product library
# rather than objects in this file.
_CATALOGUE_EXCLUSIONS = """
    AND c.name NOT LIKE '%InfoID' AND c.name NOT LIKE '%InfoId'
    AND c.name NOT LIKE 'Template%'
    AND c.name NOT IN ('LoadTypeID','ObjectType','WhereUsedId','SizeID','OptionsID',
                       'MountingTypeID','DefaultControlsID','EngravingStyleID',
                       'VariableId','ProgrammingID','DomainObjectID')
"""

_SNAPSHOT = """
SET NOCOUNT ON;
IF OBJECT_ID('{table}') IS NOT NULL DROP TABLE [{table}];
CREATE TABLE [{table}] (id bigint PRIMARY KEY);

DECLARE @t sysname, @c sysname, @sql nvarchar(max);
DECLARE pk CURSOR LOCAL FAST_FORWARD FOR
  SELECT t.name, c.name
  FROM sys.tables t
  JOIN sys.indexes i ON i.object_id = t.object_id AND i.is_primary_key = 1
  JOIN sys.index_columns ic ON ic.object_id = t.object_id AND ic.index_id = i.index_id
  JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = ic.column_id
  JOIN sys.types ty ON ty.user_type_id = c.user_type_id
  WHERE ty.name IN ('int','bigint')
    AND t.name NOT LIKE '_hwwriter%'
    AND (SELECT COUNT(*) FROM sys.index_columns x
         WHERE x.object_id = t.object_id AND x.index_id = i.index_id) = 1;
OPEN pk;
FETCH NEXT FROM pk INTO @t, @c;
WHILE @@FETCH_STATUS = 0
BEGIN
  SET @sql = N'INSERT INTO [{table}](id) SELECT DISTINCT [' + @c + N'] FROM [' + @t + N']
               WHERE [' + @c + N'] IS NOT NULL
                 AND [' + @c + N'] NOT IN (SELECT id FROM [{table}])';
  BEGIN TRY EXEC sp_executesql @sql; END TRY BEGIN CATCH END CATCH;
  FETCH NEXT FROM pk INTO @t, @c;
END
CLOSE pk; DEALLOCATE pk;
"""

# Report (table, column, count) of rows referencing an ID that does not exist.
_SCAN = """
SET NOCOUNT ON;
DECLARE @t sysname, @c sysname, @sql nvarchar(max);
IF OBJECT_ID('tempdb..#found') IS NOT NULL DROP TABLE #found;
CREATE TABLE #found (tbl sysname, col sysname, n int);

DECLARE cc CURSOR LOCAL FAST_FORWARD FOR
  SELECT t.name, c.name
  FROM sys.tables t
  JOIN sys.columns c ON c.object_id = t.object_id
  JOIN sys.types ty ON ty.user_type_id = c.user_type_id
  WHERE ty.name IN ('int','bigint')
    AND (c.name LIKE '%ID' OR c.name LIKE '%Id')
    AND t.name NOT LIKE '_hwwriter%'
    {exclusions};
OPEN cc;
FETCH NEXT FROM cc INTO @t, @c;
WHILE @@FETCH_STATUS = 0
BEGIN
  SET @sql = N'INSERT INTO #found
    SELECT ''' + @t + N''',''' + @c + N''', COUNT(*)
    FROM [' + @t + N'] WHERE [' + @c + N'] IS NOT NULL AND [' + @c + N'] > 0
      AND [' + @c + N'] IN (SELECT id FROM [{source}])
    HAVING COUNT(*) > 0';
  BEGIN TRY EXEC sp_executesql @sql; END TRY BEGIN CATCH END CATCH;
  FETCH NEXT FROM cc INTO @t, @c;
END
CLOSE cc; DEALLOCATE cc;
SELECT tbl + '|' + col + '|' + CAST(n AS varchar(20)) FROM #found ORDER BY n DESC;
"""


def snapshot(runner: BaseRunner, database: str, table: str = BEFORE) -> None:
    """Record every object ID currently in the file."""
    runner.script(_SNAPSHOT.format(table=table), database, label=f"snapshot {table}")


def sweep(runner: BaseRunner, database: str,
          graph: ForeignKeyGraph | None = None) -> list[tuple[str, str, int]]:
    """
    Delete rows still pointing at IDs the strip removed.

    Call `snapshot()` before deleting anything; this works out what vanished.
    Returns the (table, column, rows removed) it acted on.
    """
    # What no longer exists?
    snapshot(runner, database, AFTER)
    runner.script(f"""
SET NOCOUNT ON;
IF OBJECT_ID('_hwwriter_gone') IS NOT NULL DROP TABLE [_hwwriter_gone];
SELECT b.id INTO [_hwwriter_gone]
FROM [{BEFORE}] b
WHERE NOT EXISTS (SELECT 1 FROM [{AFTER}] a WHERE a.id = b.id);
CREATE UNIQUE CLUSTERED INDEX ix ON [_hwwriter_gone](id);
""", database, label="diff ids")

    rows = runner.rows(_SCAN.format(source="_hwwriter_gone",
                                    exclusions=_CATALOGUE_EXCLUSIONS), database)
    targets: list[tuple[str, str, int]] = []
    for row in rows:
        parts = "".join(row).split("|")
        if len(parts) == 3 and parts[2].strip().isdigit():
            targets.append((parts[0].strip(), parts[1].strip(), int(parts[2])))

    if targets:
        # A row being deleted here can itself be the parent of foreign-keyed
        # children (an HVAC schedule and its events, say), so each delete goes
        # through the same FK-graph walk the strip uses rather than raw.
        graph = graph or ForeignKeyGraph(runner, database)
        deletes = cascade_delete(
            graph, [(t, f"[{c}] IN (SELECT id FROM [_hwwriter_gone])") for t, c, _ in targets])
        runner.script("SET NOCOUNT ON;\nSET XACT_ABORT ON;\nBEGIN TRANSACTION;\n"
                      + deletes + "\nCOMMIT TRANSACTION;", database, label="dangling sweep")

    # Leave a FRESH baseline behind instead of none: strips run several
    # delete->sweep cycles (equipment pass, main blank, empty floors), and a
    # second sweep with no [BEFORE] table dies on "Invalid object name".
    cleanup(runner, database)
    snapshot(runner, database, BEFORE)
    return targets


def cleanup(runner: BaseRunner, database: str) -> None:
    runner.script(f"""
IF OBJECT_ID('{BEFORE}') IS NOT NULL DROP TABLE [{BEFORE}];
IF OBJECT_ID('{AFTER}') IS NOT NULL DROP TABLE [{AFTER}];
IF OBJECT_ID('_hwwriter_gone') IS NOT NULL DROP TABLE [_hwwriter_gone];
""", database, label="cleanup")
