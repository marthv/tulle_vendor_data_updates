"""?exp=<EXPERIMENT_ID> deep link (2026-11-27): id cleaning, OAuth state round trip, the note, and each render path."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import experiments_tab as et  # noqa: E402
from experiments_ui import cards, render  # noqa: E402
from test_exp_tab_v2 import entry, ro, snap_for  # noqa: E402


@pytest.mark.parametrize("raw,want", [
    ("EXP-2026-12-photo-first", "EXP-2026-12-photo-first"),
    ("  EXP-2026-11-planner-compare ", "EXP-2026-11-planner-compare"),
    (["EXP-2026-11-x", "other"], "EXP-2026-11-x"),
    ("", None), (None, None), ([], None),
    ("<script>", None), ("EXP 2026", None), ("../etc", None), ("x" * 101, None), ("-lead", None),
])
def test_clean_exp_id(raw, want):
    assert et.clean_exp_id(raw) == want


def test_state_round_trip_and_rejects():
    s = et.exp_to_state("EXP-2026-12-photo-first")
    assert s == "exp:EXP-2026-12-photo-first"
    assert et.exp_from_state(s) == "EXP-2026-12-photo-first"
    assert et.exp_to_state("<bad>") is None and et.exp_to_state(None) is None
    assert et.exp_from_state(None) is None and et.exp_from_state("csrf123") is None
    assert et.exp_from_state("exp:<script>") is None


def test_known_ids_and_note():
    reg = {"experiments": [{"id": "EXP-a", "name": "A test"}]}
    snap = {"experiments": [{"id": "EXP-b", "name": "B"}], "decided": [{"id": "EXP-c"}]}
    ids = et.known_ids(reg, snap)
    assert ids == {"EXP-a": "A test", "EXP-b": "B", "EXP-c": "EXP-c"}
    assert et.focus_note(None, ids) is None
    kind, text = et.focus_note("EXP-a", ids)
    assert kind == "ok" and "A test" in text
    kind, text = et.focus_note("EXP-zzz", ids)
    assert kind == "missing" and "EXP-zzz" in text


def test_every_registry_id_is_linkable():
    ids = et.known_ids(et._load())
    assert ids and all(et.clean_exp_id(i) == i for i in ids)


def test_cards_focus_row_first_and_open():
    a, b = entry(id="EXP-2026-11-a", name="Alpha"), entry(id="EXP-2026-11-b", name="Beta")
    s = snap_for([a, b], ro(), "2026-11-17")
    plain = cards.build_html(s)
    assert "<h2>Linked</h2>" not in plain and " open><summary>" not in plain
    page = cards.build_html(s, focus="EXP-2026-11-b")
    body = page.split("</style>")[1]
    assert body.index("<h2>Linked</h2>") < body.index("<h2>Running</h2>")
    linked = body.split("<h2>Linked</h2>")[1].split("<h2>Running</h2>")[0]
    assert 'id="EXP-2026-11-b" open>' in linked and "EXP-2026-11-a" not in linked
    assert "<h2>Linked</h2>" not in cards.build_html(s, focus="EXP-unknown")


def test_component_template_carries_focus():
    html = render.build_html({"experiments": []}, "light", focus="EXP-2026-12-photo-first")
    assert 'window.__FOCUS__ = "EXP-2026-12-photo-first"' in html
    assert "window.__FOCUS__ = /*__FOCUS__*/null" in render.build_html({"experiments": []}, "light")
    with open(render.TEMPLATE_PATH, encoding="utf-8") as f:
        t = f.read()
    assert "CTX.focus = a.focus" in t and "openDrawer(H.exp || CTX.focus" in t


def test_preserve_on_clear_keeps_exp(monkeypatch):
    import theme

    class QP(dict):
        def get(self, k, d=None):
            return dict.get(self, k, d)
    qp = QP(code="abc", state="exp:EXP-a", theme="dark")
    monkeypatch.setattr(theme.st, "query_params", qp)
    theme.preserve_on_clear({"exp": et.exp_from_state(qp.get("state"))})
    assert dict(qp) == {"theme": "dark", "exp": "EXP-a"}
    qp.update(code="x")
    theme.preserve_on_clear({"exp": None})
    assert dict(qp) == {"theme": "dark"}
