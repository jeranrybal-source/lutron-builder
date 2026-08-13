"""Tests for the app's own page.

The whole interface is one inline <script>. A single syntax error in it means
NOTHING renders -- the header and the step list still draw, so the app looks
alive while being completely unusable.

That shipped once, and nothing caught it: the API answered perfectly, so every
smoke test passed. It reached a user before it reached a test. Hence this file.
"""
import os
import re
import shutil
import subprocess

import pytest

from hwwriter.app import PAGE


def _script() -> str:
    m = re.search(r"<script>(.*)</script>", PAGE, re.S)
    assert m, "the page no longer has an inline <script> -- update this test"
    return m.group(1)


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to parse JavaScript")
def test_the_page_script_actually_parses(tmp_path):
    # The real check: hand it to a JavaScript engine. A parse error here is a
    # dead interface, however healthy the API is.
    js = tmp_path / "page.js"
    js.write_text(_script(), encoding="utf-8")
    r = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert r.returncode == 0, f"the app's page has a JavaScript syntax error:\n{r.stderr}"


def test_no_quoted_js_string_is_broken_by_a_real_newline():
    # The specific fault, checkable without node: this page is a plain Python
    # string, so a "\n" written in the source becomes a REAL newline in the
    # JavaScript. A single- or double-quoted JS string cannot span lines, so it
    # is a syntax error. It must be written "\\n" to reach the browser intact.
    script = _script()
    # Blank out template literals, which legitimately span lines.
    without_backticks = re.sub(r"`[^`]*`", "``", script, flags=re.S)
    for n, line in enumerate(without_backticks.split("\n"), 1):
        for quote in ("'", '"'):
            unescaped = len(re.findall(r"(?<!\\)" + quote, line))
            assert unescaped % 2 == 0, (
                f"line {n} of the page script leaves a {quote}-quoted string open, so it "
                f"runs into the next line and the whole interface dies:\n  {line.strip()[:120]}"
            )


def test_the_page_renders_the_shell_the_script_needs():
    for probe in ('id="content"', 'id="pill"', "<script>"):
        assert probe in PAGE, f"the page no longer contains {probe}"


def test_every_step_view_is_defined():
    script = _script()
    for fn in ("vPlans", "vCheck", "vBuild", "vFirstRun", "keyBlock",
               "refresh", "api"):
        assert f"function {fn}" in script or f"{fn}=" in script, f"{fn} is missing"


def test_version_matches_the_changelog():
    # The footer shows VERSION; the release workflow asserts the tag matches it.
    # This closes the third side of the triangle: the changelog's newest entry.
    import pathlib
    import re

    from hwwriter.app import VERSION
    changelog = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    m = re.search(r"^## v(\d+\.\d+\.\d+)", changelog.read_text(), re.M)
    assert m, "CHANGELOG.md has no version heading"
    assert m.group(1) == VERSION, (
        f"app says v{VERSION} but the changelog's newest entry is v{m.group(1)} -- "
        f"update whichever is behind before tagging")
    # pyproject drifted to 1.0.0 while the app said 1.1.1, and the release gate
    # never noticed because it only compared the tag, the app and the changelog.
    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    pv = re.search(r'^version = "(.+)"', pyproject.read_text(), re.M)
    assert pv and pv.group(1) == VERSION, (
        f"pyproject.toml says v{pv.group(1) if pv else '?'} but the app says v{VERSION}")


def test_static_html_contains_no_unrendered_template_syntax():
    # PAGE is a plain Python string. Anything ${...} OUTSIDE the script is
    # served to the user literally -- the footer once read "v${S.version}".
    script = _script()
    static = PAGE.replace(script, "")
    leaks = re.findall(r"\$\{[^}]*\}", static)
    assert not leaks, f"template syntax served literally to the user: {leaks}"


def test_the_footer_shows_the_real_version():
    from hwwriter.app import VERSION
    assert f"v{VERSION}" in PAGE


def test_typed_fields_survive_a_panel_redraw():
    # Changing the keypad family, the model, or the chosen files redraws the
    # Plans panel. The project name and notes must be re-rendered from JS
    # globals kept up to date on input -- a redraw once silently discarded both,
    # and the extraction went off as "Untitled" with no notes.
    script = _script()
    assert 'oninput="projName=this.value"' in script
    assert 'oninput="projNotes=this.value"' in script
    assert "value=\"${escAttr(projName)}\"" in script
    assert "${escAttr(projNotes)}</textarea>" in script
    # and the submit reads the globals, not the soon-to-be-redrawn DOM
    assert "project:projName" in script
    assert "notes:projNotes" in script


def test_the_wait_has_staged_honest_feedback():
    # Five silent minutes once looked identical whether Claude was reading or
    # the request had quietly died. The panel now renders three API-reported
    # stages and a liveness stamp -- and invents nothing.
    script = _script()
    assert "uploading" in script and "upload_mb" in script
    assert "reads the whole set before writing anything" in script
    assert "input_tokens" in script          # "it read N tokens" on reading_done
    assert "last signal" in script           # liveness, from real stream events
    assert "Last read on this machine" in script  # remembered calibration
    assert "document.title=" in script       # progress visible from another tab


def test_ai_written_text_cannot_inject_html_into_the_page():
    # Validation messages quote room and fixture names straight out of the
    # CSVs, and those are written by the AI. They reach the page via innerHTML.
    script = _script()
    assert "S.log.map(escHTML)" in script, "the log is rendered without escaping"
    assert "escHTML(S.error)" in script, "the error banner is rendered without escaping"


def test_chosen_pdf_filenames_cannot_inject_html_into_the_page():
    # Drawing sets arrive from third parties under whatever filename they were
    # given, and the chosen files are rendered into the page via innerHTML. A
    # hostile name must render as text, not run as script in the app's origin.
    script = _script()
    assert "escHTML(f.name)" in script, (
        "the file box renders PDF filenames unescaped -- a filename can inject "
        "script into the app page")


def test_the_research_toggle_reaches_the_server():
    # A decorative toggle is the keypad-family bug again: the control renders,
    # the choice is collected, and nothing downstream ever sees it.
    script = _script()
    assert 'id="rsr"' in script
    assert "S.research=this.checked" in script
    assert "research:S.research!==false" in script, (
        "the research choice is not sent with the read")


def test_the_research_toggle_explains_both_states():
    # The point of the toggle is an informed choice, so each state has to say
    # what it costs the engineer -- money on one side, blank wattages on the
    # other.
    script = _script()
    assert "recommended" in script and "sizes your circuits" in script
    assert "blank" in script and "nothing is guessed" in script


def test_the_model_is_stated_with_its_cost_and_the_reason_for_it():
    # One model means no dropdown -- but the engineer still needs to know what
    # it is, what this job costs on it, and why there is nothing to choose.
    script = _script()
    assert "S.estimates" in script and "theEst" in script
    assert "last read on this machine" in script, (
        "the estimate never says whether it is measured or a guess")
    assert "Why only this model?" in script
    assert "S.model_note" in script


def test_no_model_picker_survives_the_single_model_decision():
    # A dropdown with one option, or a stale one that can still submit a
    # withdrawn model, is worse than none.
    script = _script()
    assert "getElementById('mdl')" not in script, (
        "extract() still reads a model picker that no longer exists")
    assert "model:(S.models[0]||{}).id" in script


def test_the_model_note_is_specific_enough_to_be_checkable():
    # "Extensive testing showed X is best" is marketing. The note has to carry
    # the numbers, so a future reader can tell whether it is still true.
    from hwwriter.ingest import MODEL_NOTE
    for probe in ("109 circuits", "47 keypads", "162 scenes",
                  "54% fewer circuits", "three real drawing sets",
                  "292 circuits", "99 keypads"):
        assert probe in MODEL_NOTE, f"the model note dropped its evidence: {probe}"
    # Every claim in the note is now measured -- nothing is inherited from
    # testing nobody in this repo can point at.
    assert "earlier testing" not in MODEL_NOTE, (
        "an unmeasured claim has crept back into the note")


def test_the_plans_screen_explains_what_makes_a_read_expensive():
    script = _script()
    for probe in ("What will this read cost", "How much drawing there is",
                  "Fixture research", "A re-read costs the same"):
        assert probe in script, f"the cost explainer no longer covers: {probe}"


def test_the_check_screen_shows_the_cost_breakdown():
    # "About $15" with no anatomy is an alarm; reading vs research vs writing
    # is an answer. The lines come from the server and are escaped.
    script = _script()
    assert "cost_lines" in script
    assert "LE.cost_lines.map(escHTML)" in script


def test_open_buttons_report_failure_instead_of_doing_nothing():
    # /api/open can fail (no file association, file gone). Fire-and-forget
    # made the button look dead with no explanation.
    script = _script()
    assert "async function openIt(" in script
    assert "api('/api/open',{what:w})" in script
    for stale in ("onclick=\"api('/api/open',{what:'report'})\"",
                  "onclick=\"api('/api/open',{what:'hw'})\""):
        assert stale not in script, "an open button bypasses openIt() again"


def test_project_names_are_not_interpolated_into_javascript():
    # A folder name on disk once went straight into an inline onclick handler.
    script = _script()
    assert "onclick=\"reopen(" not in script
    assert "data-project" in script


def test_the_server_refuses_cross_site_posts():
    from hwwriter.app import Handler
    assert hasattr(Handler, "_cross_site")
    src = _module_source()
    assert "application/json" in src and "Origin" in src and "Host" in src


def _module_source():
    import inspect

    from hwwriter import app
    return inspect.getsource(app)


def test_the_api_key_never_enters_the_environment():
    # Child processes (the PowerShell SQL worker, Docker) inherit the whole
    # environment, and other programs can read it.
    src = _module_source()
    assert 'os.environ["ANTHROPIC_API_KEY"]' not in src


def test_the_brief_is_a_system_prompt_and_pdfs_are_data():
    import inspect

    from hwwriter import ingest
    src = inspect.getsource(ingest)
    # The brief travels as the SYSTEM prompt (now as a cache-marked block),
    # and the PDFs stay in the user turn as data.
    assert '"text": system_prompt' in src
    assert "never as instructions to you" in src


def test_the_step_bar_is_actually_clickable():
    """The four steps were decorative for the app's whole life.

    They rendered, they lit up, and nothing happened when you clicked one --
    the CSS even said cursor:default. Asserting the handler exists, not just
    that the view functions do, because the view functions were always fine.
    """
    script = _script()
    assert "closest('.step')" in script, (
        "nothing listens for a click on the step bar -- the steps are decoration again")
    assert "function go(" in script and "function canGo(" in script


def test_check_and_build_are_not_reachable_before_there_are_schedules():
    # Clicking straight to Build with no schedules would hand the writer an
    # empty project folder.
    script = _script()
    m = re.search(r"function canGo\(s\)\{(.*?)\n\}", script, re.S)
    assert m, "canGo is gone -- the step guards went with it"
    body = m.group(1)
    assert "review_ready" in body, "check/build are no longer gated on a review sheet"


def test_a_poll_cannot_drag_the_user_out_of_the_stage_they_chose():
    """A read repaints its own panel every 1.2 seconds.

    Without this guard, clicking back to the API key during a long read bounced
    you straight back to the plans panel on the next tick, and anything you had
    typed went with it.
    """
    script = _script()
    assert "pinned" in script
    m = re.search(r"async function refresh\(repaint\)\{(.*?)\n\}", script, re.S)
    assert m, "refresh() is gone -- update this test"
    assert "pinned" in m.group(1), (
        "refresh() repaints regardless of the stage the user chose")


def test_starting_a_read_or_a_build_releases_the_pin():
    # Otherwise the progress log never comes back to the front.
    script = _script()
    for fn in ("async function extract()", "async function build()"):
        m = re.search(re.escape(fn) + r"\{(.*?)\n\}", script, re.S)
        assert m, f"{fn} is gone -- update this test"
        assert "pinned=false" in m.group(1), f"{fn} leaves the user pinned elsewhere"


# --------------------------------------------------------------- the caller
# The cross-site guard was tested by asserting it EXISTS. Nothing ever checked
# that the app itself could get past it -- and in 1.1.2 it could not: fetch()
# with a string body sends text/plain, the guard demands application/json, so
# every POST in the app returned 403 while the page rendered perfectly. Saving
# the key, accepting the disclaimer, opening a project, reading plans and
# building the file were all dead. Same shape as the keypad-family bug: the
# function was right, the call site was not.

def test_the_page_sends_the_json_content_type_the_guard_demands():
    m = re.search(r"async function api\(p,body\)\{(.*?)\n", _script())
    assert m, "api() is gone -- update this test"
    body = m.group(1)
    assert "application/json" in body, (
        "api() does not set Content-Type: application/json, so the cross-site guard "
        "rejects every POST the app makes -- the whole app is dead while looking fine")


def _live_server():
    import threading
    from http.server import ThreadingHTTPServer

    from hwwriter.app import Handler
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _post(port, content_type):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/select",
        data=b'{"project":"a project that does not exist"}',
        headers={"Content-Type": content_type, "Origin": f"http://127.0.0.1:{port}"},
        method="POST")
    try:
        return urllib.request.urlopen(req).getcode()
    except urllib.error.HTTPError as e:
        return e.code


def test_a_post_shaped_like_the_apps_own_reaches_the_handler():
    # 404 ("No such project") means it got THROUGH the guard, which is the
    # point; 403 means the app is talking to itself and being refused.
    srv, port = _live_server()
    try:
        assert _post(port, "application/json") != 403, (
            "the app's own POST is rejected as cross-site -- every button is dead")
    finally:
        srv.shutdown()


def test_a_post_without_the_json_content_type_is_still_refused():
    # The guard must keep working; the fix is on the caller, not here.
    srv, port = _live_server()
    try:
        assert _post(port, "text/plain;charset=UTF-8") == 403
    finally:
        srv.shutdown()


def test_build_is_refused_when_the_project_has_no_schedules(tmp_path, monkeypatch):
    """A failed extraction leaves a project folder with only the PDFs in it.

    Every CSV then reads as an empty list, an EMPTY schedule validates, and
    "Build" wrote a .hw containing none of the house -- reported as success.
    The server now refuses to build a project with no review sheet, whichever
    route the browser took to the button. (Found by round-5 review, Codex.)
    """
    import json
    import urllib.error
    import urllib.request

    from hwwriter import app as app_mod
    (tmp_path / "Empty House").mkdir()
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    old_project = app_mod.STATE.project
    app_mod.STATE.project = "Empty House"
    srv, port = _live_server()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/build", data=b"{}",
            headers={"Content-Type": "application/json",
                     "Origin": f"http://127.0.0.1:{port}"},
            method="POST")
        try:
            body = urllib.request.urlopen(req).read()
            code = 200
        except urllib.error.HTTPError as e:  # noqa: F841
            body = e.read()
            code = e.code
        assert code == 400, "a schedule-less project can still be built"
        assert "no schedules" in json.loads(body)["error"]
    finally:
        srv.shutdown()
        app_mod.STATE.project = old_project


def test_copying_asks_what_it_would_do_before_doing_it():
    """Copying replaces the scenes in a room, and takes with them any keypad
    button that recalled one of the old names. That was reported AFTER the
    fact. It is a decision, so it is now previewed and named in the
    confirmation.
    """
    script = _script()
    m = re.search(r"async function copyScenes\(\)\{(.*?)\n\}", script, re.S)
    assert m, "copyScenes is gone -- update this test"
    body = m.group(1)
    assert "preview:true" in body, "the copy no longer asks before it writes"
    assert body.index("preview:true") < body.index("confirm("), (
        "the confirmation is shown before the preview comes back, so it cannot "
        "say what will be lost")
    assert "remove keypad buttons" in body


# --------------------------------------------------- reading a file as text

def _extract_via_http(port, pdfs):
    """POST /api/extract exactly as the page does, and wait for it to finish."""
    import json
    import time
    import urllib.error
    import urllib.request

    from hwwriter import app as app_mod
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/extract",
        data=json.dumps({"pdfs": pdfs, "project": "Text Only Test",
                         "notes": "", "model": "claude-opus-5",
                         "keypad_family": "Palladiom"}).encode(),
        headers={"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{port}"},
        method="POST")
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        raise AssertionError(f"the app's own extract POST was refused: {e.read()}") from e
    for _ in range(200):                      # the read runs on a worker thread
        if not app_mod.STATE.busy:
            break
        time.sleep(0.02)


def _run_extract(tmp_path, monkeypatch, pdfs):
    """Drive a real /api/extract through the real handler, stubbing only the
    API call itself, and return the text_only_paths the engine was handed."""
    import base64

    from hwwriter import app as app_mod
    from hwwriter import ingest as ingest_mod

    seen = {}

    def fake_run(paths, out_dir, **kw):
        seen["paths"] = list(paths)
        seen["text_only_paths"] = list(kw.get("text_only_paths") or [])
        return {}, {"cost_usd": 0.0, "model": "stub", "input_tokens": 0, "output_tokens": 0}

    monkeypatch.setattr(ingest_mod, "run", fake_run)
    monkeypatch.setattr(app_mod, "_render_review", lambda *a, **k: None)
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    monkeypatch.setattr(app_mod, "load_config", lambda: {})
    monkeypatch.setattr(app_mod, "save_config", lambda cfg: None)

    body = [{"name": n, "text_only": t,
             "data": base64.b64encode(b"%PDF-1.4 " + n.encode()).decode()}
            for n, t in pdfs]
    srv, port = _live_server()
    try:
        _extract_via_http(port, body)
    finally:
        srv.shutdown()
    assert "paths" in seen, "the extract never reached the engine"
    return seen


def test_the_per_file_text_choice_reaches_the_engine(tmp_path, monkeypatch):
    """The app's checkbox has to arrive as text_only_paths, or ticking it does
    nothing and a 45 MB specification is still sent as pages (and refused).
    """
    seen = _run_extract(tmp_path, monkeypatch,
                        [("Ground Floor Lighting.pdf", False),
                         ("Lighting Specification.pdf", True)])
    assert len(seen["text_only_paths"]) == 1, (
        "the per-file 'read as text' tick did not reach the engine")
    assert os.path.basename(seen["text_only_paths"][0]) == "Lighting Specification.pdf"


def test_an_unticked_drawing_is_never_read_as_text(tmp_path, monkeypatch):
    """The dangerous direction. A drawing has an extractable text layer too, so
    reading one as text keeps its dimension strings and throws the plan away.
    Nothing may opt a file in except the engineer's own tick.
    """
    seen = _run_extract(tmp_path, monkeypatch,
                        [("Ground Floor Lighting.pdf", False),
                         ("First Floor Lighting.pdf", False)])
    assert seen["text_only_paths"] == [], (
        "a file nobody ticked was going to be read as text -- the read is gutted")


def test_the_text_choice_follows_the_file_through_a_name_collision(tmp_path, monkeypatch):
    """Two files chosen from different folders can share a name; the second is
    saved under a new one. The choice is tracked by position for exactly this
    reason -- matched back by name it would land on the wrong document, sending
    the specification as pages and gutting the drawing.
    """
    seen = _run_extract(tmp_path, monkeypatch,
                        [("Lighting.pdf", False), ("Lighting.pdf", True)])
    assert len(seen["paths"]) == 2 and seen["paths"][0] != seen["paths"][1], (
        "the colliding filenames were not separated on disk")
    assert seen["text_only_paths"] == [seen["paths"][1]], (
        "the 'read as text' tick followed the NAME, not the file -- it landed "
        "on the wrong document")


def test_pypdf_is_declared_where_reading_as_text_needs_it():
    """The engine imports pypdf. Without it in the ingest extra, a packaged
    install offers the option and then fails on the first document that uses it.
    """
    import pathlib
    pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    ingest_extra = re.search(r"^ingest\s*=\s*\[(.*?)\]", text, re.S | re.M)
    assert ingest_extra, "the ingest extra is gone -- update this test"
    assert "pypdf" in ingest_extra.group(1), (
        "pypdf is missing from the ingest extra, so 'read as text' raises "
        "ImportError on a packaged install")


def test_the_size_limit_counts_pages_and_not_words():
    """A specification too large to send as pages is the reason the option
    exists. If the limit still counted it, the one document that most needs
    reading as text would be refused before it could be sent.
    """
    script = _script()
    m = re.search(r"async function extract\(\)\{(.*?)\n\}", script, re.S)
    assert m, "extract() is gone -- update this test"
    body = m.group(1)
    assert "files.filter(f=>!f.text_only)" in body, (
        "the size check no longer excludes files read as text, so a large "
        "specification is refused even when ticked")
    assert "pick(inp)" not in script.split("function pick(inp)")[0]
    pick = re.search(r"function pick\(inp\)\{(.*?)\n\}", script, re.S).group(1)
    assert "alert" not in pick, (
        "pick() rejects oversized files again -- a 45 MB specification is "
        "thrown out before it can be ticked to be read as text")


def test_the_fitting_editor_is_reachable_and_wired_up():
    """The whole interface is one script, so a view that exists in Python but
    is never called from a button is invisible -- and looks like a bug in the
    save path rather than a missing onclick."""
    from hwwriter.app import PAGE
    assert "Edit the fittings" in PAGE
    for fn in ("loadFittings", "vFittings", "readFittings", "saveFittings",
               "fitTypeOptions", "fitRow"):
        assert f"function {fn}" in PAGE or f"async function {fn}" in PAGE, fn
    assert "/api/fittings/edit" in PAGE


def test_the_review_sheet_may_be_framed_by_the_app_and_by_nothing_else():
    """The Check screen shows the review sheet in an iframe, and the blanket
    anti-framing header applied to that response too -- so the app forbade
    itself from displaying the one document the product is built around. The
    Check screen read "127.0.0.1 refused to connect" where the sheet should be.
    """
    import inspect

    from hwwriter import app
    src = inspect.getsource(app.Handler._send)
    assert "frame-ancestors 'self'" in src and "frame-ancestors 'none'" in src, (
        "the review sheet needs same-origin framing; everything else must not")
    route = inspect.getsource(app.Handler._do_GET_inner)
    assert route.count("framable_by_self=True") == 2, (
        "only /review may be framed -- and both its responses need it, or a "
        "missing sheet shows a connection error instead of 'no review yet'")
    assert '<iframe src="/review">' in app.PAGE


def test_the_plans_screen_says_what_makes_a_good_read():
    """The tool is only as good as the set it is given -- 194 circuits against
    521 on the same plans, with and without the designer's circuit estimate.
    That belongs in front of the engineer choosing files, not in a note file."""
    from hwwriter.app import PAGE
    assert "What makes a good read" in PAGE
    for expected in ("circuit estimate", "read as text", "521",
                     "Send the whole set together"):
        assert expected in PAGE, expected


# ------------------------------------------- the spreadsheet route, in the app

def _api(port, path, payload):
    """POST JSON to the running app and return (status, decoded body)."""
    import json  # noqa: F401
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Origin": f"http://127.0.0.1:{port}"}, method="POST")
    try:
        return 200, json.loads(urllib.request.urlopen(req).read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


SHEET_CSV = (b"Practice name,,,,\n"
             b"Level,Room,Circuit Description,Type,Qty\n"
             b"Ground Floor,Kitchen,Ceiling Downlights,DL1,14\n"
             b"Ground Floor,Utility,Ceiling Downlights,DL1,6\n")


def test_reading_a_spreadsheet_returns_a_mapping_to_correct(tmp_path, monkeypatch):
    import base64

    from hwwriter import app as app_mod
    srv, port = _live_server()
    try:
        code, body = _api(port, "/api/sheet/read",
                          {"name": "loads.csv",
                           "data": base64.b64encode(SHEET_CSV).decode()})
        assert code == 200 and body["ok"], body
        sheet = body["sheets"][0]
        # Row 1 is the practice name. Reading it as the headings would map every
        # column wrongly and still produce a schedule.
        assert sheet["header"] == 1
        assert sheet["headings"][sheet["mapping"]["room"]] == "Room"
        assert sheet["preview"], "the user cannot check a mapping they cannot see"
        assert [f["key"] for f in body["fields"]], "the page needs the field list"
    finally:
        srv.shutdown()
        app_mod.STATE.sheet = None


def test_a_spreadsheet_import_writes_the_schedules_and_a_report(tmp_path, monkeypatch):
    import base64
    import time

    from hwwriter import app as app_mod
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    old = app_mod.STATE.project
    srv, port = _live_server()
    try:
        _, body = _api(port, "/api/sheet/read",
                       {"name": "loads.csv",
                        "data": base64.b64encode(SHEET_CSV).decode()})
        mapping = body["sheets"][0]["mapping"]
        code, res = _api(port, "/api/sheet/import",
                         {"project": "Sheet House", "table": 0, "header": 1,
                          "mapping": mapping, "fill_down": True,
                          "upload": body["upload"]})
        assert code == 200 and res["ok"], res
        for _ in range(100):
            if not app_mod.STATE.busy:
                break
            time.sleep(0.05)
        assert not app_mod.STATE.busy, "the import never finished"
        assert app_mod.STATE.error is None, app_mod.STATE.error
        folder = tmp_path / "Sheet House"
        # The same six files a read writes, plus a report and a review sheet --
        # so nothing downstream can tell the two routes apart.
        from hwwriter import ingest
        for name in list(ingest.FILES) + ["EXTRACTION-REPORT.txt", "review.html"]:
            assert (folder / name).exists(), f"{name} was not written"
        report = (folder / "EXTRACTION-REPORT.txt").read_text()
        assert "SPREADSHEET" in report and "nothing was charged" in report
    finally:
        srv.shutdown()
        app_mod.STATE.project = old
        app_mod.STATE.sheet = None


def test_a_second_import_archives_the_first_rather_than_mixing_with_it(tmp_path, monkeypatch):
    # The build reads whatever CSVs it finds, so a leftover file from the
    # previous attempt silently merges two schedules into one house.
    import base64
    import time

    from hwwriter import app as app_mod
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    old = app_mod.STATE.project
    srv, port = _live_server()
    try:
        for _ in range(2):
            _, body = _api(port, "/api/sheet/read",
                           {"name": "loads.csv",
                            "data": base64.b64encode(SHEET_CSV).decode()})
            _api(port, "/api/sheet/import",
                 {"project": "Twice", "table": 0, "header": 1,
                  "mapping": body["sheets"][0]["mapping"], "fill_down": True,
                  "upload": body["upload"]})
            for _ in range(100):
                if not app_mod.STATE.busy:
                    break
                time.sleep(0.05)
        archives = list((tmp_path / "Twice").glob("superseded-*"))
        assert archives, "the first import was overwritten instead of archived"
        assert (archives[0] / "LoadSchedule.csv").exists()
    finally:
        srv.shutdown()
        app_mod.STATE.project = old
        app_mod.STATE.sheet = None


def test_an_import_whose_mapping_names_no_fitting_is_refused(tmp_path, monkeypatch):
    import base64

    from hwwriter import app as app_mod
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    srv, port = _live_server()
    try:
        _, body = _api(port, "/api/sheet/read",
                       {"name": "loads.csv", "data": base64.b64encode(SHEET_CSV).decode()})
        code, res = _api(port, "/api/sheet/import",
                         {"project": "Bad", "table": 0, "header": 1,
                          "mapping": {"room": 1, "circuit": 2},
                          "upload": body["upload"]})
        assert code == 400 and "fitting" in res["error"]
        assert not (tmp_path / "Bad").exists(), "a refused import still wrote a project"
    finally:
        srv.shutdown()
        app_mod.STATE.sheet = None


def test_moving_the_header_row_re_guesses_against_that_row(tmp_path):
    import base64

    from hwwriter import app as app_mod
    srv, port = _live_server()
    try:
        _, body = _api(port, "/api/sheet/read",
                       {"name": "loads.csv", "data": base64.b64encode(SHEET_CSV).decode()})
        code, res = _api(port, "/api/sheet/guess",
                         {"table": 0, "header": 0, "upload": body["upload"]})
        assert code == 200 and res["ok"]
        # Row 1 is the practice name, so almost nothing should be recognised --
        # the point being that the guess follows the row the user pointed at.
        assert res["headings"][0] == "Practice name"
        assert "room" not in res["mapping"]
    finally:
        srv.shutdown()
        app_mod.STATE.sheet = None


def test_a_screen_drawn_for_a_superseded_upload_is_refused(tmp_path, monkeypatch):
    """The server holds ONE spreadsheet; the browser may hold several screens.

    Two tabs, or one tab and a retry: whichever uploaded last owns the held
    file, and a guess or import quoting an older upload must be refused, not
    answered from the wrong file. Seen for real on 08-12 as a mapping screen
    showing "Circuit number" mapped on a sheet that has no circuit number --
    and an import in that state writes a complete, believable schedule with
    every column read as the wrong thing.
    """
    import base64

    from hwwriter import app as app_mod
    monkeypatch.setattr(app_mod, "projects_dir", lambda: str(tmp_path))
    srv, port = _live_server()
    try:
        _, first = _api(port, "/api/sheet/read",
                        {"name": "loads.csv",
                         "data": base64.b64encode(SHEET_CSV).decode()})
        _, second = _api(port, "/api/sheet/read",
                         {"name": "loads.csv",
                          "data": base64.b64encode(SHEET_CSV).decode()})
        assert first["upload"] != second["upload"], "uploads must be told apart"
        code, res = _api(port, "/api/sheet/guess",
                         {"table": 0, "header": 0, "upload": first["upload"]})
        assert code == 409 and "different file" in res["error"]
        code, res = _api(port, "/api/sheet/import",
                         {"project": "Stale", "table": 0, "header": 1,
                          "mapping": second["sheets"][0]["mapping"],
                          "upload": first["upload"]})
        assert code == 409 and "different file" in res["error"]
        assert not (tmp_path / "Stale").exists(), \
            "a refused import still wrote a project"
        # The tab that owns the newest upload is not punished for the other.
        code, res = _api(port, "/api/sheet/guess",
                         {"table": 0, "header": 1, "upload": second["upload"]})
        assert code == 200 and res["ok"]
    finally:
        srv.shutdown()
        app_mod.STATE.sheet = None


def test_the_page_throws_away_replies_for_screens_it_has_left():
    """The client half of the stale-upload guard.

    A guess reply landing after the user switched sheets used to stamp the old
    sheet's headings and mapping onto the new one (the 08-12 defect); a read
    reply overtaken by a newer one used to hand the screen back to the older
    file. Both are dropped by sequence number, and the upload number is quoted
    on every guess and import so the SERVER can refuse the cross-tab case.
    """
    script = _script()
    assert "seq!==sheetSeq" in script, "stale replies must be dropped"
    assert "sheetIdx!==forSheet" in script, \
        "a guess reply must not apply to a different sheet than it was asked about"
    assert script.count("upload:upload") >= 2, \
        "guess and import must both quote the upload they were drawn from"
    # Choosing the same file twice must fire twice -- the retry after a
    # mis-upload is the same file, and a silent no-op reads as a dead button.
    assert "inp.value=''" in script
    # And a stray click on a data row must be called out, not left to a small
    # caption -- "A: Ground Floor" offered as a column name reads as gibberish
    # unless the page says why (James, 08-12).
    assert "does not look like column headings" in script


def test_the_homeplay_marks_are_served_and_on_the_page():
    """The star logomark and the written wordmark, ported from the production
    app's own assets -- never redrawn -- and served by an exact-name whitelist,
    because a URL is not a key to the filesystem."""
    import urllib.error
    import urllib.request

    srv, port = _live_server()
    try:
        for name in ("logomark-black.png", "logomark-white.png",
                     "wordmark-black.png", "wordmark-white.png", "favicon.png"):
            body = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/brand/{name}").read()
            assert body[1:4] == b"PNG", f"{name} did not come back as a PNG"
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/favicon.ico").read()
        assert body[1:4] == b"PNG", "the favicon route must serve the mark"
        # The brand typefaces are licensed, so they are absent from the public
        # repository and the public build: headings fall back to a standard
        # serif and everything else is identical. Present or absent, the route
        # must behave -- serve a real font, or refuse cleanly.
        brand = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "docs", "brand")
        for name in ("times-now.woff", "saans-regular.woff2", "saans-semibold.woff2"):
            url = f"http://127.0.0.1:{port}/brand/{name}"
            if os.path.exists(os.path.join(brand, name)):
                assert urllib.request.urlopen(url).read()[:4] in (b"wOFF", b"wOF2"), name
            else:
                try:
                    urllib.request.urlopen(url)
                    raise AssertionError(f"{name} is not shipped but the route served something")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 404
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/brand/secrets.txt")
            raise AssertionError("an unlisted name must 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        srv.shutdown()
    assert '/brand/logomark-black.png' in PAGE
    assert '/brand/wordmark-black.png' in PAGE
    assert 'alt="Homeplay"' in PAGE, "the wordmark must still read as Homeplay"
    assert 'rel="icon"' in PAGE
    # The star sits in the FOOTER (James, 08-12 -- top-left read as clutter),
    # and the key headings are set in the brand face.
    assert PAGE.index("</main>") < PAGE.index("logomark-black.png"), \
        "the star logomark belongs at the bottom of the page, not the header"
    assert "'Times Now'" in PAGE
    # The rest of the brand pass (James, 08-12): Saans as the body face with
    # calt off per the brand CSS, and the four stages in the tracked-caps
    # caption idiom.
    assert "'Saans'" in PAGE
    assert "'calt' 0" in PAGE
    assert "text-transform:uppercase" in PAGE.split("nav .step{", 1)[1].split("}")[0]


def test_the_experimental_routes_both_say_so_and_the_label_is_no_button():
    """James, 08-12: the DWG page said experimental while the AI route -- the
    MOST experimental way in -- said nothing; and "the best way in" was a
    filled pill crammed beside the heading that read as a clickable button.
    Both are now the brand eyebrow: a tracked-caps line ABOVE the heading."""
    script = _script()
    assert script.count('class="eyebrow trial">Experimental</div>') == 2, (
        "the DWG and PDF routes are both experimental and both must say so")
    assert 'class="eyebrow">The best way in</div>' in script
    assert "2. AutoCAD DWG" in script and "3. PDF Plans" in script
    eyebrow = PAGE.split(".eyebrow{", 1)[1].split("}")[0]
    assert "text-transform:uppercase" in eyebrow and "margin" in eyebrow


def test_each_way_in_keeps_its_own_upload_screen():
    """A loaded read belongs to one tab. The AutoCAD tab once showed the
    loaded SPREADSHEET's mapping screen -- with nowhere to upload a drawing at
    all (James, 08-12)."""
    script = _script()
    assert "sheetMatchesSource" in script
    assert "sheet?vSheet():vDwg()" not in script.replace(" ", "")


def test_the_spreadsheet_route_is_reachable_without_an_api_key():
    """A friend whose set came with a schedule has no reason to open an
    Anthropic account. The key was step 1 and the app opened on it, which
    said the opposite; it now lives on the PDF screen alone (James, 08-13)."""
    script = _script()
    assert "source='sheet'" in script, "the app must open on the spreadsheet route"
    assert 'data-s="key"' not in PAGE, "the key is no longer a step"
    assert "if(S.review_ready)vCheck(); else vStep2();" in script, (
        "boot must not route through a key screen")
    # The key input exists exactly where it is needed, and the Read button
    # still refuses to run without one.
    assert "function keyBlock" in script and "${keyBlock()}" in script
    assert "!S.key_set?'disabled'" in script


def test_the_first_run_says_what_the_tool_needs_to_work():
    """A friend on a Mac with no Designer would otherwise reach the last
    button before finding out (James, 08-13)."""
    script = _script()
    assert "function vFirstRun" in script
    assert "if(!S.accepted)" in script, "it must show before any route"
    for probe in ("Windows", "Lutron Designer", "Parallels"):
        assert probe in script, f"the first-run notice never mentions {probe}"


def test_both_ways_into_step_two_are_offered_on_both_screens():
    script = _script()
    assert script.count("sourceTabs()") >= 3, (
        "the switch must be on the plans screen and on the spreadsheet screen, "
        "or one route is unreachable from the other")


def test_the_designer_exe_is_found_under_program_files(tmp_path, monkeypatch):
    """Windows offers Notepad for a .hw -- Designer registers no association
    (James's VM, 08-12, first end-to-end build). So the open button finds
    Designer itself. Uninstallers must not win the search."""
    from hwwriter import app as app_mod
    lutron = tmp_path / "Lutron" / "HomeWorks QSX"
    lutron.mkdir(parents=True)
    (lutron / "Lutron Designer.exe").write_bytes(b"MZ")
    (lutron / "Uninstall Designer.exe").write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nowhere"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nowhere"))
    monkeypatch.setattr(app_mod, "load_config", lambda: {})
    saved = {}
    monkeypatch.setattr(app_mod, "save_config", lambda cfg: saved.update(cfg))
    exe = app_mod.find_designer_exe()
    assert exe and exe.endswith("Lutron Designer.exe")
    assert saved.get("designer_exe") == exe, "the search must be remembered"


def test_a_machine_without_designer_returns_none_never_a_guess(tmp_path, monkeypatch):
    from hwwriter import app as app_mod
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_mod, "load_config", lambda: {})
    monkeypatch.setattr(app_mod, "save_config",
                        lambda cfg: (_ for _ in ()).throw(AssertionError(
                            "nothing to remember on a machine without Designer")))
    assert app_mod.find_designer_exe() is None


def test_a_cached_designer_path_that_vanished_is_searched_again(tmp_path, monkeypatch):
    """An uninstalled or moved Designer must not leave the button dead."""
    from hwwriter import app as app_mod
    lutron = tmp_path / "Lutron"
    lutron.mkdir()
    (lutron / "Designer.exe").write_bytes(b"MZ")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_mod, "load_config",
                        lambda: {"designer_exe": str(tmp_path / "gone.exe")})
    saved = {}
    monkeypatch.setattr(app_mod, "save_config", lambda cfg: saved.update(cfg))
    exe = app_mod.find_designer_exe()
    assert exe and exe.endswith("Designer.exe")


def test_the_packaged_designer_alias_is_found_and_channels_are_skipped(tmp_path, monkeypatch):
    """Designer ships as a packaged app -- no Program Files folder at all,
    just execution aliases (James's VM, 08-12). The release channel must win
    over the Beta and Alpha aliases sitting beside it."""
    from hwwriter import app as app_mod
    apps = tmp_path / "Microsoft" / "WindowsApps"
    apps.mkdir(parents=True)
    for name in ("Lutron Designer.exe", "Lutron Designer Beta.exe",
                 "Lutron Designer Off Team Alpha.exe"):
        (apps / name).write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "nowhere"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "nowhere"))
    monkeypatch.setattr(app_mod, "load_config", lambda: {})
    monkeypatch.setattr(app_mod, "save_config", lambda cfg: None)
    exe = app_mod.find_designer_exe()
    assert exe and os.path.basename(exe) == "Lutron Designer.exe", exe


def test_the_update_notice_asks_github_once_and_fails_silently():
    """One anonymous question to GitHub at launch -- a copy handed out from a
    download page has no other way to learn it is stale (James, 08-13). The
    first run declares the departure, because the app promises elsewhere that
    nothing leaves the machine; and a network failure must look exactly like
    up to date, because this tool gets used on site with no network at all."""
    script = _script()
    assert "function checkUpdate" in script
    # It runs at boot, after the state it compares against has loaded.
    assert "checkUpdate()" in script.split("await refresh(false);", 1)[1]
    body = script.split("function checkUpdate", 1)[1]
    assert "api.github.com/repos/homeplayltd/lutron-builder/releases/latest" in body
    assert "catch" in body.split("(async()=>{")[0], "a failed check must be silent"
    # The release name arrives from the network; unescaped, GitHub could write
    # markup into the page.
    assert "escHTML(latest)" in body
    # Versions compare number by number -- string order calls 1.10 old.
    assert "parseInt" in body
    # The first-run screen says the app does this.
    assert "asks GitHub one anonymous question" in script
