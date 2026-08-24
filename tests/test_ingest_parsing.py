"""Parsing the model's response into schedule files.

The response arrives as marker-delimited sections, possibly across several
continuations after max_tokens cuts. The traps here are all silent: a section
lost, a section half-replaced, and the read still "succeeds".
"""
from hwwriter.ingest import parse_sections


def test_sections_split_on_markers():
    out = parse_sections(
        "===== Areas.csv =====\nAction,AreaName\nNEW,Kitchen\n"
        "===== REPORT =====\nAll read.\n")
    assert out["Areas.csv"] == "Action,AreaName\nNEW,Kitchen\n"
    assert out["REPORT"] == "All read.\n"


def test_a_fenced_section_is_unwrapped():
    out = parse_sections("===== Areas.csv =====\n```csv\nAction,AreaName\n```\n")
    assert out["Areas.csv"] == "Action,AreaName\n"


def test_a_repeated_marker_concatenates_instead_of_discarding():
    """A continuation that repeats the marker it was cut off inside used to
    OVERWRITE the first half -- everything before the repeat silently vanished,
    and the truncated schedule could still validate. A wrong-but-valid house.
    Found independently by this round's own read and by Codex's probe.
    """
    out = parse_sections(
        "===== Scenes.csv =====\nheader\nrow1\n"
        "===== REPORT =====\npart one\n"
        "===== Scenes.csv =====\nrow2\n")
    assert out["Scenes.csv"] == "header\nrow1\nrow2\n", (
        "the rows before a repeated marker were thrown away")


def test_a_restarted_section_fails_loudly_downstream_rather_than_silently():
    # If the model restarts a section from scratch (header again) instead of
    # continuing it, concatenation leaves a header row in the data -- which
    # _new_rows refuses as an unknown Action. Loud beats silent.
    import os
    import tempfile

    import pytest

    from hwwriter.schedule import _new_rows
    out = parse_sections(
        "===== Areas.csv =====\nAction,AreaName\nNEW,Kitchen\n"
        "===== Areas.csv =====\nAction,AreaName\nNEW,Hall\n")
    d = tempfile.mkdtemp()
    path = os.path.join(d, "Areas.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out["Areas.csv"])
    with pytest.raises(ValueError, match="not.*understood"):
        _new_rows(path)


def test_a_wrongly_cased_section_label_does_not_discard_a_paid_read():
    """A real five-sheet House A read (2026-08-07) was thrown away whole
    because the response lacked a byte-exact 'Areas.csv' marker. An unambiguous
    near-name must be accepted, with a note.
    """
    from hwwriter.ingest import absorb_near_named_sections
    said = []
    sections = {"AREAS.CSV": "Action,AreaName\nNEW,Kitchen\n", "REPORT": "ok\n"}
    still = absorb_near_named_sections(sections, ["Areas.csv"], say=said.append)
    assert still == []
    assert sections["Areas.csv"] == "Action,AreaName\nNEW,Kitchen\n"
    assert said and "AREAS.CSV" in said[0]

    # 'Areas' without the extension is also unambiguous.
    sections = {"Areas": "x\n"}
    assert absorb_near_named_sections(sections, ["Areas.csv"], say=said.append) == []
    assert sections["Areas.csv"] == "x\n"


def test_an_ambiguous_near_name_is_left_missing():
    from hwwriter.ingest import absorb_near_named_sections
    sections = {"AREAS.CSV": "a\n", "Areas": "b\n"}
    still = absorb_near_named_sections(sections, ["Areas.csv"], say=lambda *_: None)
    assert still == ["Areas.csv"], "two candidate sections must not be guessed between"
    assert "Areas.csv" not in sections


def test_a_genuinely_absent_section_stays_missing():
    from hwwriter.ingest import absorb_near_named_sections
    sections = {"REPORT": "ok\n"}
    assert absorb_near_named_sections(sections, ["Areas.csv"],
                                      say=lambda *_: None) == ["Areas.csv"]


def test_a_failed_parse_saves_the_raw_response_for_salvage(tmp_path):
    # The read is paid for by the time parsing fails. The raw response used to
    # be discarded -- no diagnosis, no recovery, and the error told the user to
    # "extract manually" from a response that no longer existed.
    from hwwriter.ingest import save_failed_response
    raw = "===== FixturesCatalog.csv =====\nAction,FixtureRef\nNEW,L1\n"
    path = save_failed_response(str(tmp_path), raw)
    assert open(path, encoding="utf-8").read() == raw
    import os
    name = os.path.basename(path)
    assert name.startswith("FAILED-RESPONSE-") and name.endswith(".txt")
    from hwwriter.ingest import _SCHEDULE_FILES
    assert name not in _SCHEDULE_FILES, (
        "the salvage file would be archived as a schedule on the next read")


def test_a_rejected_key_is_explained_in_english_not_dumped_as_a_traceback(tmp_path):
    """A rotated or revoked key is ordinary, not a crash.

    It reached the engineer as `anthropic.AuthenticationError: Error code: 401
    - {'type': 'error', ...}` -- true, unreadable, and silent on what to do.
    Reproduced against the real code path with a key the API rejects.
    """
    import pytest

    # The writer is stdlib-only by design; the SDK is an optional extra, so CI
    # runs without it. Skip rather than fail there -- the guard still runs
    # everywhere the SDK IS installed, which is everywhere a read can happen.
    anthropic = pytest.importorskip("anthropic")

    from hwwriter.ingest import IngestError, run
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%stub\n")

    def _boom(*a, **k):
        raise anthropic.AuthenticationError(
            "Error code: 401", response=_FakeResponse(401), body=None)

    real = anthropic.Anthropic
    try:
        anthropic.Anthropic = lambda *a, **k: _FakeClient(_boom)
        with pytest.raises(IngestError) as caught:
            run([str(pdf)], str(tmp_path / "out"), say=lambda *_: None,
                api_key="sk-ant-not-a-real-key")
    finally:
        anthropic.Anthropic = real
    msg = str(caught.value)
    assert "rejected your API key" in msg
    assert "nothing was charged" in msg
    assert "console.anthropic.com" in msg, "the message does not say how to fix it"
    assert "401" not in msg and "Error code" not in msg, (
        "the raw SDK error is still being shown to the engineer")


class _FakeResponse:
    """The shape anthropic's exception constructor reads off a response."""

    def __init__(self, status):
        import httpx
        self.status_code = status
        self.headers = {}
        self.request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    def json(self):
        return {}


class _FakeClient:
    """Just enough Anthropic client to reach the streaming call and fail."""

    def __init__(self, boom):
        self.messages = type("M", (), {"stream": staticmethod(boom)})()


def test_pdf_names_in_the_system_prompt_are_stripped_to_plain_labels():
    """Filenames are document-controlled metadata that lands in the SYSTEM
    prompt (the multi-sheet ordering note). A name may not smuggle marker or
    instruction punctuation in at system authority.
    """
    import inspect

    from hwwriter import ingest
    src = inspect.getsource(ingest.run)
    assert "_label" in src, "the drawing-set note interpolates raw basenames again"
    import re as _re
    stripped = _re.sub(r"[^\w .()\[\]-]", " ", "plan=====evil<inject>.pdf")[:80].strip()
    assert "=" not in stripped and "<" not in stripped


def test_hwi_is_not_offered_as_a_keypad_range():
    """HWI is the wired in-wall PREFIX, not a range you would specify.

    Alisse, Palladiom and seeTouch all sit *inside* HomeWorks wired in-wall; it
    is a category, not an alternative to them, and an HWIS-5BRL is not a keypad
    Homeplay would put on a wall (James, 2026-08-07). Offering it beside the
    real ranges invited the AI to pick it, and its 5-10 button models made that
    look attractive next to Palladiom's four.
    """
    from hwwriter import ingest
    fams = ingest.keypad_families()
    assert not any("HWI" in f or "Wired In-Wall" in f for f in fams), (
        f"the wired in-wall category is being offered as a range: {fams}")
    # The real ranges must survive -- this is a narrow exclusion, not a purge.
    for real in ("Palladiom", "HomeWorks QS Wired Designer (seeTouch)"):
        assert real in fams, f"{real} is no longer offered"


def test_a_document_can_be_read_as_text_instead_of_pages():
    """A specification is words; a drawing is a picture.

    Measured on a real 88-page specification: 45 MB as pages -- over the API's
    ~32 MB ceiling, so unsendable -- against ~79k tokens of text, about 40p.
    """
    import inspect

    from hwwriter import cli, ingest
    src = inspect.getsource(ingest.run)
    assert "text_only_paths" in inspect.signature(ingest.run).parameters
    assert "pdf_text(path)" in src, "the text-only branch is not wired into run()"
    assert '"type": "text"' in src, "text is not sent as a text block"
    # and it must be reachable from the command line, not just internally
    assert "--text-only" in inspect.getsource(cli.main)
    assert "text_only_paths=args.text_only" in inspect.getsource(cli.cmd_ingest)


def test_a_pdf_with_no_text_layer_says_so_rather_than_sending_nothing(tmp_path):
    # A scan has no text layer. Sending an empty string would be a silent,
    # expensive no-op; the engineer needs to know to send it as pages.
    import pytest
    pypdf = pytest.importorskip("pypdf")
    from hwwriter.ingest import IngestError, pdf_text
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    blank = tmp_path / "scan.pdf"
    with open(blank, "wb") as fh:
        w.write(fh)
    with pytest.raises(IngestError) as caught:
        pdf_text(str(blank))
    assert "no text in it" in str(caught.value)
    assert "scan" in str(caught.value).lower()
