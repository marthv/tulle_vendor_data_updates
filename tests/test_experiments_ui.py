"""Experiments tab page (experiments_ui): note hand-off from the page, template injection, JS syntax."""
import json
import os
import re
import shutil
import subprocess

import pytest

from experiments_ui import render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _note(**kw):
    v = {"type": "note", "note_id": "nabc123", "exp_id": "EXP-2026-11-x", "kind": "note", "text": "hello"}
    v.update(kw)
    return v


def test_clean_note_valid_and_author_never_from_page():
    exp_id, nid, body = render.clean_note(_note(author="Spoofed"))
    assert (exp_id, nid) == ("EXP-2026-11-x", "nabc123")
    assert body == {"kind": "note", "text": "hello", "client_id": "nabc123"}


@pytest.mark.parametrize("bad", [
    None, {}, _note(type="other"), _note(text="   "), _note(note_id=""), _note(exp_id=""),
    _note(kind="decision"),               # a decision needs a call
    _note(kind="decision", call="nuke"),  # unknown call
])
def test_clean_note_rejects(bad):
    assert render.clean_note(bad) is None


def test_clean_note_trims_and_defaults():
    _e, _n, body = render.clean_note(_note(kind="weird", text="x" * 5000))
    assert body["kind"] == "note" and len(body["text"]) == render.NOTE_MAX_CHARS
    _e, _n, body = render.clean_note(_note(kind="decision", call="shelve"))
    assert body["call"] == "shelve"


def test_handle_note_posts_once_per_note_id():
    calls, state = [], {}

    def on_note(exp_id, body):
        calls.append((exp_id, body))
        return True, None

    assert render.handle_note_value(_note(), on_note, "  Des \n Smith ", state) is True
    # the same component value comes back on every later rerun: never posted twice
    assert render.handle_note_value(_note(), on_note, "Des", state) is False
    assert len(calls) == 1
    assert calls[0][1]["author"] == "Des Smith"
    assert state["nabc123"] == {"ok": True, "error": None}


def test_handle_note_failure_and_read_only():
    state = {}
    assert render.handle_note_value(_note(), lambda e, b: (False, "engine said 422: bad"), "Des", state)
    assert state["nabc123"]["ok"] is False and "422" in state["nabc123"]["error"]
    state2 = {}
    assert render.handle_note_value(_note(note_id="n2"), None, "Des", state2)
    assert state2["n2"] == {"ok": False, "error": "notes are read-only here"}

    def boom(e, b):
        raise ConnectionError("down")
    state3 = {}
    render.handle_note_value(_note(note_id="n3"), boom, "Des", state3)
    assert state3["n3"]["ok"] is False and state3["n3"]["error"] == "ConnectionError"


def test_handle_note_ignores_nothing_and_flags_invalid():
    state = {}
    assert render.handle_note_value(None, None, "x", state) is False
    assert render.handle_note_value(_note(text=""), lambda e, b: (True, None), "x", state) is True
    assert state["nabc123"]["ok"] is False


def test_note_state_is_capped():
    state = {}
    for i in range(40):
        render.handle_note_value(_note(note_id=f"n{i}"), lambda e, b: (True, None), "x", state)
    assert len(state) == 30 and "n39" in state and "n0" not in state


def test_template_lives_in_component_dir_and_injects():
    assert os.path.basename(render.TEMPLATE_PATH) == "index.html"
    assert os.path.dirname(render.TEMPLATE_PATH) == render.COMPONENT_DIR
    html = render.build_html({"experiments": [], "x": "</script><b>"}, "dark")
    assert "</script><b>" not in html and "\\u003c/script\\u003e" in html
    assert 'window.__THEME__ = "dark"' in html


def test_snapshot_sig_changes_with_content():
    a = render.snapshot_sig({"a": 1})
    assert a and a == render.snapshot_sig({"a": 1}) and a != render.snapshot_sig({"a": 2})


def test_page_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    with open(render.TEMPLATE_PATH, encoding="utf-8") as f:
        script = re.findall(r"<script>(.*?)</script>", f.read(), re.S)[-1]
    tmp = os.path.join(ROOT, "tests", "_page_check.js")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(script)
        r = subprocess.run([node, "--check", tmp], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def test_fixture_has_the_v21_examples():
    with open(os.path.join(ROOT, "experiments_ui", "fixture_snapshot.json"), encoding="utf-8") as f:
        snap = json.load(f)
    assert snap.get("fixture") is True
    rows = {r["id"]: r for r in snap["experiments"]}
    fdq = rows["EXP-2026-11-free-daily-question"]
    assert fdq.get("in_rotation") and (fdq.get("verdict") or {}).get("tldr")
    assert (fdq["verdict"].get("how_we_call") or {}).get("ship_if")
    tx = (rows["EXP-2026-11-texas-only-forever-89"].get("verdict") or {}).get("numbers") or {}
    assert tx.get("before") and tx.get("after"), tx      # geo row has a starting point
    assert any(r.get("decision_pending") for r in snap["experiments"])
    assert snap.get("notes_by_id")


def _page():
    with open(render.TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def test_one_view_no_tab_bar():
    # owner 2026-11-19: "too many tabs" - one view, changes and rules behind "More"
    html = _page()
    assert 'role="tablist"' not in html and 'data-tab=' not in html
    assert 'data-view="changes"' in html and 'data-view="rules"' in html


def test_toolbar_search_status_bucket_rotation_sort():
    html = _page()
    assert 'id="xq"' in html and 'type="search"' in html
    for key in ("all", "call", "running", "planned", "decided"):
        assert '["%s", ' % key in html
    assert 'id="xb"' in html and 'id="xs"' in html and "data-rot" in html
    for key in ("needs", "ending", "newest", "oldest", "chance", "bucket", "az"):
        assert '["%s", ' % key in html
    assert "localStorage" in html and "try {" in html


def test_card_per_experiment_and_calendar_filters_grid():
    html = _page()
    assert 'class="xc s-' in html and "list.map(card)" in html
    assert "data-calexp" in html and "data-calweek" in html and "data-calclear" in html
    assert "Showing: " in html and "calActive(e, ST.cal)" in html


def test_drawer_why_and_how_sections():
    html = _page()
    assert "Why it is / isn’t working" in html and "How we’ll make the call" in html
    for k in ("bar", "ship_if", "revert_if", "otherwise", "now"):
        assert "hw.%s" % k in html
    # notes protocol unchanged
    assert '"streamlit:setComponentValue"' in html and 'type: "note"' in html


def test_planner_scorecard_strip_and_measuring():
    html = _page()
    assert "S.scorecards" in html and "scorecardStrip()" in html and '<details class="sc"' in html
    assert "' of ' + fmtInt(a.n)" in html          # counts shown with every rate
    assert "isMeasuring(e)" in html and "Measuring" in html
    with open(os.path.join(ROOT, "experiments_ui", "fixture_snapshot.json"), encoding="utf-8") as f:
        snap = json.load(f)
    keys = [t["key"] for t in snap["scorecards"]["planner"]]
    assert keys == ["free_pdf_open", "forever_return", "forever_second_question"]
