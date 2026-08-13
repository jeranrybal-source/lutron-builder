"""The supported Python version, which is stated in five separate places.

Raising the floor to 3.10 was a decision with consequences -- it is what lets
the tool read an AutoCAD drawing, because `ezdwg` has no wheel below it. The
risk with a fact spelled out in five files is that four of them get updated.
The symptoms are all quiet ones: CI still testing a version nobody supports, a
wheel that will not install, or a person on an old Python getting a TypeError
about zip() from somewhere deep in a build.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLOOR = (3, 10)
FLOOR_TEXT = f"{FLOOR[0]}.{FLOOR[1]}"


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_the_package_requires_it():
    m = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', read("pyproject.toml"))
    assert m and m.group(1) == FLOOR_TEXT


def test_the_linter_targets_it():
    """A lower target hides real findings -- B905 only exists from 3.10."""
    m = re.search(r'target-version\s*=\s*"py(\d)(\d+)"', read("pyproject.toml"))
    assert m and (int(m.group(1)), int(m.group(2))) == FLOOR


def test_ci_tests_the_floor_itself():
    """Testing only 3.12 would let 3.10-incompatible code through."""
    m = re.search(r'python-version:\s*\[([^\]]+)\]', read(".github/workflows/check.yml"))
    assert m
    versions = re.findall(r'"([\d.]+)"', m.group(1))
    assert FLOOR_TEXT in versions, f"CI does not test the floor: {versions}"
    assert min(tuple(int(n) for n in v.split(".")) for v in versions) == FLOOR


def test_the_entry_point_refuses_an_older_python_by_name():
    """Otherwise the failure is a TypeError about zip(), which explains nothing."""
    source = read("lutron_builder.py")
    assert f"sys.version_info < ({FLOOR[0]}, {FLOOR[1]})" in source
    assert f"Python {FLOOR_TEXT} or newer" in source
    # Before any import of the package, or it fails while importing instead.
    assert source.index("sys.version_info") < source.index("import hwwriter")


def test_the_drawing_reader_agrees_with_the_floor():
    from hwwriter import dwgplan
    assert dwgplan.MIN_PYTHON == FLOOR


@pytest.mark.parametrize("doc", ["README.md", "CONTRIBUTING.md"])
def test_the_docs_do_not_still_promise_an_older_python(doc):
    text = read(doc)
    assert f"Python {FLOOR_TEXT}+" in text
    assert "Python 3.9" not in text


def test_the_exe_build_installs_and_bundles_the_drawing_reader():
    """Dropped from either line, the exe builds and then cannot open a drawing."""
    spec = read("build-exe.ps1")
    assert "pyinstaller ezdwg" in spec
    assert "--collect-all ezdwg" in spec
    assert f"Python {FLOOR_TEXT}+" in spec
