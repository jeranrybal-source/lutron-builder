"""Ask the REAL Designer database what type the wattage columns are.

Written 2026-08-06 after a cross-AI review pointed out that build.py writes
FixtureWattage and LampWattage into the .hw -- contradicting a commit message
of mine that said wattage is only ever displayed. If those columns are integer,
a 9.6 W/m LED tape is truncated to 9 W inside the built project while the review
sheet the engineer approved says 9.6, and the two disagree silently.

Run on the Windows VM, where Designer's LocalDB lives:
    python tools/probe_wattage_columns.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hwwriter import sandbox  # noqa: E402
from hwwriter.sqlrunner import PowerShellRunner  # noqa: E402

SHELL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "shells", "Starter Shell.hw")

QUERY = """
SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE,
       ISNULL(CONVERT(varchar(20), c.NUMERIC_PRECISION), '') AS PREC,
       ISNULL(CONVERT(varchar(20), c.NUMERIC_SCALE), '') AS SCALE
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.COLUMN_NAME IN ('FixtureWattage', 'LampWattage')
ORDER BY c.TABLE_NAME, c.COLUMN_NAME;
"""


def main() -> int:
    # Restoring evicts and REPLACEs the database called "Project", which is the
    # one Lutron Designer itself uses. Running this with Designer open would
    # kill its connection and overwrite unsaved work. Refuse instead.
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
        if "lutron" in out:
            print("REFUSED: Lutron Designer is running. This probe restores the "
                  "database Designer uses, which would evict it and overwrite "
                  "unsaved work. Close Designer and run it again.")
            return 2

    work = os.path.join(os.environ.get("TEMP", "."), "wattage-probe")
    os.makedirs(work, exist_ok=True)
    runner = PowerShellRunner()
    lut = sandbox.unpack_hw(SHELL, os.path.join(work, "in"))
    box = sandbox.Sandbox(runner)
    try:
        box.restore(lut)
        rows = runner.query(QUERY, database=box.name).rows
        if not rows:
            print("NO SUCH COLUMNS -- wattage is not stored in the project at all.")
            return 0
        print(f"{'TABLE':<28}{'COLUMN':<18}{'TYPE':<12}{'PREC':<6}{'SCALE'}")
        verdict_int = False
        for r in rows:
            table, col, typ, prec, scale = (list(r.values()) + ["", "", "", "", ""])[:5]
            print(f"{table:<28}{col:<18}{typ:<12}{prec:<6}{scale}")
            if str(typ).lower() in ("int", "smallint", "tinyint", "bigint"):
                verdict_int = True
        print()
        if verdict_int:
            print("VERDICT: at least one wattage column is an INTEGER type.")
            print("         A fractional wattage IS truncated inside the built .hw,")
            print("         so the review sheet and the project disagree.")
        else:
            print("VERDICT: the wattage columns are not integer types --")
            print("         a fractional wattage survives into the built .hw.")
    finally:
        try:
            box.drop()
        except Exception:                                        # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
