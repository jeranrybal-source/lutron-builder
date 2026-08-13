"""The catalogue and the shell must not drift apart.

Adding a keypad is two edits in different places: a row in
KeypadModels_REFERENCE.csv, and an example placed in the shell in Designer. Do
one without the other and nothing complains until a user picks that model and
the build says "the shell contains no keypad of that model".

Since the shell is a binary Lutron database that nothing on CI can open, the
shell side is represented by shells/Starter Shell.models.txt, regenerated
whenever the shell is rebuilt.
"""
import csv
import os

from hwwriter import ingest

CATALOGUE = os.path.join(ingest.DOCS, "KeypadModels_REFERENCE.csv")


def _rows():
    with open(CATALOGUE, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_the_shell_manifest_exists_and_is_not_empty():
    models = ingest.shell_models()
    assert models, ("shells/Starter Shell.models.txt is missing or empty -- regenerate it "
                    "with `inspect --shell` after rebuilding the shell")
    assert all(m.isdigit() for m in models), f"non-numeric ModelInfoID in the manifest: {models}"


def test_every_placeable_model_is_actually_in_the_shell():
    shell = ingest.shell_models()
    orphans = [(r["KeypadModel"], r["LutronModelInfoID"])
               for r in ingest.placeable_keypads()
               if r["LutronModelInfoID"] not in shell]
    assert not orphans, (
        "these are offered to users but the shell cannot build them -- either place an "
        f"example in the shell and regenerate the manifest, or mark Placeable=no: {orphans}")


def test_every_model_in_the_shell_is_catalogued():
    # The other direction: a model sitting in the shell that nothing can name is
    # dead weight, and usually means a catalogue row was dropped.
    catalogued = {r["LutronModelInfoID"] for r in _rows()}
    unnamed = sorted(ingest.shell_models() - catalogued)
    assert not unnamed, (
        f"the shell carries models with no catalogue row, so nothing can ask for them: {unnamed}")


def test_placeable_column_is_yes_or_no():
    for r in _rows():
        v = (r.get("Placeable") or "").strip().lower()
        assert v in ("yes", "no"), f"{r['KeypadModel']} has Placeable={v!r}"


def test_the_ai_is_never_offered_a_model_it_cannot_build():
    prompt = ingest.build_prompt(keypad_family="Palladiom")
    unbuildable = [r["KeypadModel"] for r in _rows()
                   if (r.get("Placeable") or "yes").strip().lower() == "no"]
    for name in unbuildable:
        assert name not in prompt, (
            f"{name} cannot be built but is being offered to the AI in the catalogue")


def test_families_offered_only_come_from_placeable_models():
    placeable_families = {r["Family"] for r in ingest.placeable_keypads()}
    for fam in ingest.keypad_families():
        assert fam in placeable_families
