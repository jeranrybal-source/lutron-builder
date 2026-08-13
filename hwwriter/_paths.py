"""Where the package's data files live -- source checkout or frozen app.

Under PyInstaller (the packaged Lutron Builder exe), data files are unpacked
to a temp dir exposed as sys._MEIPASS; in a source checkout they sit next to
the hwwriter/ package. Everything that reads shells/ or docs/ resolves through
here so both layouts work.
"""

from __future__ import annotations

import os
import sys


def root() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def docs() -> str:
    return os.path.join(root(), "docs")
