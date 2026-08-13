"""What the shipped exe must contain.

The tests elsewhere run against a source checkout, where every library is
installed. Nothing in them can see that the BUILD forgot one.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_runtime_dependency_is_installed_by_the_exe_build():
    """pypdf was missing from the exe from v1.0.0 to v1.3.2 -- proven by
    unpacking the shipped binary on 2026-08-13. The app imports it lazily, so
    nothing failed here; the person who would have found out is a stranger
    being told to "pip install" on a machine with no Python. Whatever the app
    imports, the build must install."""
    import re
    build = open(os.path.join(ROOT, "build-exe.ps1"), encoding="utf-8").read()
    installed = set(re.search(r"pip install --quiet --upgrade ([^\r\n]+)",
                              build).group(1).split())

    # Parsed, not grepped: a docstring beginning "from the drawing..." is not
    # an import, and the first version of this test believed it was.
    imported = set()
    for name in os.listdir(os.path.join(ROOT, "hwwriter")):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(ROOT, "hwwriter", name),
                              encoding="utf-8").read())
        for node in ast.walk(tree):          # walk, so lazy imports count too
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    third_party = {m for m in imported
                   if m not in sys.stdlib_module_names
                   and m not in {"hwwriter", "__future__"}}

    missing = sorted(m for m in third_party if m not in installed)
    assert not missing, (
        f"the exe build installs {sorted(installed)} but the app imports "
        f"{missing} -- add them to build-exe.ps1, or the shipped exe cannot "
        f"do that job at all")
