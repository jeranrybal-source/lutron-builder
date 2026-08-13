"""
Deleting rows that other rows point at.

A Lutron project carries roughly two hundred foreign keys, and a good number of
them are to tables you would never think to look in -- `tblPegasusLinkNode`,
say, holding a reference to a link node.  Enumerating the dependants by hand
means discovering each omission one failed run at a time.

So don't enumerate them.  Read the foreign-key graph out of the database and
walk it: to delete a set of rows from table T, first delete every row in every
table that references them, depth first.  What comes back is an ordered list of
DELETE statements that satisfies the constraints by construction.

Self-references (a hierarchy pointing at its own parent) and cycles are handled
by visiting each (table, path) once and letting the caller's own predicate cover
the rest.

Not every reference means ownership.  `tblArea.DaylightingGroupAssignedToID` is
an area *using* a daylighting group, not a group *owning* an area -- so removing
the group must not take the room, its panel and the processor inside it with it.
Tables named in `PROTECTED` are never deleted by the walk; if something they
merely point at is going away, the pointer is set to NULL instead.  Only a table
the caller names explicitly as a root gets deleted.
"""

from __future__ import annotations

from .sqlrunner import BaseRunner

MAX_DEPTH = 6

# Structural tables. Reaching one of these by following a reference means the
# reference was a use, not an ownership -- so null it rather than follow it.
PROTECTED = {"tblArea", "tblProcessor", "tblProject", "tblLink", "tblNextObjectID"}


class ForeignKeyGraph:
    def __init__(self, runner: BaseRunner, database: str):
        rows = runner.rows("""
SELECT  pt.name  AS parent_table,
        pc.name  AS parent_column,
        rt.name  AS child_table,
        rc.name  AS child_column,
        rc.is_nullable
FROM sys.foreign_key_columns fkc
JOIN sys.tables  rt ON rt.object_id = fkc.parent_object_id
JOIN sys.columns rc ON rc.object_id = fkc.parent_object_id
                   AND rc.column_id = fkc.parent_column_id
JOIN sys.tables  pt ON pt.object_id = fkc.referenced_object_id
JOIN sys.columns pc ON pc.object_id = fkc.referenced_object_id
                   AND pc.column_id = fkc.referenced_column_id;
""", database)
        # parent table -> list of (parent_col, child_table, child_col, nullable)
        self.dependants: dict[str, list[tuple[str, str, str, bool]]] = {}
        for parent_table, parent_col, child_table, child_col, nullable in rows:
            self.dependants.setdefault(parent_table, []).append(
                (parent_col, child_table, child_col, nullable == "1"))

    def deletes_for(self, table: str, predicate: str) -> list[str]:
        """DELETE statements for `table WHERE predicate`, dependants first."""
        out: list[str] = []
        self._walk(table, predicate, out, seen=set(), depth=0)
        out.append(f"DELETE FROM [{table}] WHERE {predicate};")
        return out

    def _walk(self, table: str, predicate: str, out: list[str],
              seen: set[tuple[str, str]], depth: int) -> None:
        if depth >= MAX_DEPTH:
            return
        for parent_col, child_table, child_col, nullable in self.dependants.get(table, []):
            if child_table == table:
                # A self-reference; the table's own predicate already covers it,
                # provided the caller deletes children before parents.
                continue
            key = (child_table, child_col)
            if key in seen:
                continue
            seen.add(key)
            child_predicate = (f"[{child_col}] IN "
                               f"(SELECT [{parent_col}] FROM [{table}] WHERE {predicate})")

            if child_table in PROTECTED:
                if nullable:
                    out.append(f"UPDATE [{child_table}] SET [{child_col}] = NULL "
                               f"WHERE {child_predicate};")
                # If it isn't nullable the row genuinely depends on what is being
                # deleted; leave it and let the constraint complain, rather than
                # quietly removing structure the caller asked us to keep.
                continue

            self._walk(child_table, child_predicate, out, seen, depth + 1)
            out.append(f"DELETE FROM [{child_table}] WHERE {child_predicate};")


def cascade_delete(graph: ForeignKeyGraph, targets: list[tuple[str, str]]) -> str:
    """Build one script deleting several (table, predicate) targets safely."""
    lines: list[str] = []
    for table, predicate in targets:
        lines.append(f"\n-- {table}")
        lines.extend(graph.deletes_for(table, predicate))
    return "\n".join(lines)
