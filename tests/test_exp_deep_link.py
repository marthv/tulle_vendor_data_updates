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


# ── dashboard.py wiring (QA 2026-11-27: 4 planted wiring bugs went uncaught) ────────────────────────
import ast  # noqa: E402

DASH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard.py")
BASE = {"client_id": "cid", "redirect_uri": "https://dash.example/", "response_type": "code"}
LABELS = ["📈 Cohorts", "🧪 Experiments", "💰 Venue Pricing", "🔍 Google Data & Images", "📄 PDF Extraction", "🩺 Health"]


def test_auth_params_carry_the_id_in_state_only():
    p = et.auth_url_params(BASE, "EXP-2026-12-photo-first")
    assert p["state"] == "exp:EXP-2026-12-photo-first" and p["redirect_uri"] == BASE["redirect_uri"]
    assert {k: v for k, v in p.items() if k != "state"} == BASE and "state" not in BASE
    for bad in (None, "", "<x>", "../etc"):
        assert "state" not in et.auth_url_params(BASE, bad)


@pytest.mark.parametrize("qp,want", [
    ({"code": "c", "state": "exp:EXP-2026-11-a"}, "EXP-2026-11-a"),      # back from Google: from state
    ({"code": "c", "exp": "EXP-2026-11-b"}, "EXP-2026-11-b"),              # still on the URL
    ({"code": "c", "exp": "EXP-2026-11-b", "state": "exp:EXP-2026-11-a"}, "EXP-2026-11-b"),
    ({"code": "c"}, None), ({"code": "c", "state": "csrf"}, None), ({"code": "c", "state": "exp:<x>"}, None), (None, None),
])
def test_callback_keeps_the_id_past_the_clears(qp, want):
    assert et.callback_keep(qp) == {"exp": want}


def test_tab_order_unchanged_without_a_valid_exp():
    for f in (None, "", "<bad>"):
        assert et.tab_order(LABELS, f) == LABELS
    got = et.tab_order(LABELS, "EXP-2026-11-a")
    assert got[0] == "🧪 Experiments" and sorted(got) == sorted(LABELS) and len(got) == len(LABELS)
    assert [t for t in got if t != "🧪 Experiments"] == [t for t in LABELS if t != "🧪 Experiments"]


def test_resolve_focus_drops_unknown_ids():
    ids = {"EXP-a": "A"}
    assert et.resolve_focus("EXP-a", ids) == "EXP-a"
    assert et.resolve_focus("EXP-zzz", ids) is None and et.resolve_focus("<x>", ids) is None
    assert et.resolve_focus("EXP-a", None) is None


@pytest.mark.parametrize("v2", ["1", "0"])
def test_render_tab_drops_an_unknown_id_before_rendering(monkeypatch, v2):
    seen = {}
    monkeypatch.setenv("EXP_TAB_V2", v2)
    monkeypatch.setattr(et, "_load", lambda: {"experiments": [{"id": "EXP-a", "name": "A"}]})
    monkeypatch.setattr(et, "_show_focus_note", lambda f, ids: None)
    monkeypatch.setattr(et, "_render_legacy", lambda focus=None: seen.setdefault("focus", focus))
    monkeypatch.setattr(et, "_engine_snapshot", lambda u, k: (None, "engine not configured"))
    monkeypatch.setattr(render, "render_experiments_tab_v2", lambda *a, **kw: seen.setdefault("focus", kw.get("focus")))
    et.render_experiments_tab(focus="EXP-zzz")
    assert seen["focus"] is None
    seen.clear()
    et.render_experiments_tab(focus="EXP-a")
    assert seen["focus"] == "EXP-a"


def _dash_tree():
    with open(DASH, encoding="utf-8") as f:
        return ast.parse(f.read())


def _calls(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


def test_dashboard_sends_the_id_to_google():
    tree = _dash_tree()
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_build_google_auth_url")
    c = _calls(fn, "auth_url_params")
    assert c and isinstance(c[0].args[1], ast.Name) and c[0].args[1].id == "exp_id"
    assert any("params" in ast.unparse(r) for r in ast.walk(fn) if isinstance(r, ast.Return))
    login = [c for c in _calls(tree, "_build_google_auth_url") if c.args]
    assert login and all(ast.unparse(c.args[0]) == "focus_exp_id()" for c in login)
    assert not [c for c in _calls(tree, "_build_google_auth_url") if not c.args]


def test_dashboard_callback_keeps_the_id_on_every_clear():
    src = open(DASH, encoding="utf-8").read()
    tree = ast.parse(src)
    keep = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_keep" for t in n.targets)]
    assert len(keep) == 1 and ast.unparse(keep[0].value) == "callback_keep(qp)"
    cb = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "'code' in qp")
    clears = _calls(cb, "preserve_on_clear")
    assert len(clears) >= 3 and all(len(c.args) == 1 and ast.unparse(c.args[0]) == "_keep" for c in clears)
    assert not [c for c in _calls(cb, "clear")]                 # no blanket query_params.clear() in the callback


def test_dashboard_tab_order_goes_through_tab_order():
    tree = _dash_tree()
    order = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_tab_order" for t in n.targets)]
    assert len(order) == 1 and ast.unparse(order[0].value) == "tab_order(_TAB_LABELS, _exp_focus)"
    labels = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_TAB_LABELS" for t in n.targets))
    assert ast.literal_eval(labels.value) == LABELS
    tabs = _calls(tree, "tabs")
    assert len(tabs) == 1 and ast.unparse(tabs[0].args[0]) == "_tab_order"
    src = ast.unparse(tree)
    assert "_tabs_by_label[t] for t in _TAB_LABELS" in src
