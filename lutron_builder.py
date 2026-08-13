"""Lutron Builder -- packaged entry point (PyInstaller onefile).

Runs the `app` subcommand of the writer CLI, so `Lutron Builder.exe` opens the
local app in the default browser, and flags still work
(`Lutron Builder.exe --port 9000 --no-browser`).
"""
import sys

# Checked before anything is imported, because the failure otherwise is a
# TypeError about zip() from somewhere deep in a build, which tells the person
# reading it nothing at all. The shipped exe carries its own interpreter and
# never sees this; it is here for anyone running from a source checkout.
# noqa: UP036 is deliberate. The linter reads this as dead code because the
# project declares 3.10, but `requires-python` is enforced by pip and nothing
# enforces it on `python lutron_builder.py` in a checkout -- which is exactly
# the case this exists for.
if sys.version_info < (3, 10):  # noqa: UP036
    have = ".".join(str(n) for n in sys.version_info[:3])
    sys.exit(
        f"Lutron Builder needs Python 3.10 or newer, and this is Python {have}.\n"
        f"Install a newer Python and run it with that, or use the packaged\n"
        f"Lutron Builder.exe, which brings its own.")

import hwwriter.app  # noqa: E402, F401 -- static import so PyInstaller bundles it
from hwwriter.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["app", *sys.argv[1:]]))
