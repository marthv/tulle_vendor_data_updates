"""Four-item Experiments view (owner 2026-11-27): every card on the main page shows only
1) what's being tested 2) timeline 3) before vs after 4) conclusion; the rest is in the side panel
or under "More detail". Rendered in a real browser from the committed fixture (skips without one)."""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from experiments_ui import render  # noqa: E402

LABELS = ["What’s being tested", "Timeline", "Before vs after", "Conclusion"]


def _fixture():
    with open(os.path.join(ROOT, "experiments_ui", "fixture_snapshot.json"), encoding="utf-8") as f:
        return json.load(f)


def _registry():
    with open(os.path.join(ROOT, "experiments", "experiment_log.json"), encoding="utf-8") as f:
        return json.load(f)


# ── attach_plain_fields (pure python) ──────────────────────────────────────────────────────
def test_plain_fields_fill_decided_rows_from_registry():
    snap = {"experiments": [{"id": "A", "tests": "engine wording"}], "decided": [{"id": "B"}, {"id": "Z"}]}
    reg = {"experiments": [{"id": "A", "tests": "registry wording", "goal": "g"},
                           {"id": "B", "tests": "Does B work?", "hypothesis": "If B then C", "what_changed": " "}]}
    out = render.attach_plain_fields(snap, reg)
    assert out["experiments"][0]["tests"] == "engine wording"          # never overrides the engine
    assert out["experiments"][0]["goal"] == "g"
    assert out["decided"][0]["tests"] == "Does B work?" and out["decided"][0]["hypothesis"] == "If B then C"
    assert "what_changed" not in out["decided"][0]                       # blank strings are not copied
    assert out["decided"][1] == {"id": "Z"}                              # unknown id untouched


@pytest.mark.parametrize("snap,reg", [(None, {}), ({}, None), ({"decided": [None, 3]}, {"experiments": [None]})])
def test_plain_fields_never_raise(snap, reg):
    render.attach_plain_fields(snap, reg)


def test_every_registry_entry_has_a_tested_line():
    # the "What's being tested" row reads tests > hypothesis > goal > what_changed
    for e in _registry()["experiments"]:
        assert any(str(e.get(k) or "").strip() for k in ("tests", "hypothesis", "goal", "what_changed")), e.get("id")


# ── browser render ──────────────────────────────────────────────────────────────────────────
def _browser():
    pw_mod = pytest.importorskip("playwright.sync_api")
    pw = pw_mod.sync_playwright().start()
    for kw in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
        try:
            return pw, pw.chromium.launch(**kw)
        except Exception:  # noqa: BLE001
            continue
    pw.stop()
    pytest.skip("no browser available for playwright")


@pytest.fixture(scope="module")
def page_for(tmp_path_factory):
    pw, b = _browser()
    d = tmp_path_factory.mktemp("f4")
    errors = []

    def make(snap, focus=None, width=1280):
        snap = render.attach_plain_fields(json.loads(json.dumps(snap)), _registry())
        p = d / f"p{len(errors)}_{width}.html"
        p.write_text(render.build_html(snap, theme_mode="light", focus=focus), encoding="utf-8")
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(p.as_uri())
        pg.wait_for_timeout(500)
        return pg
    make.errors = errors
    yield make
    b.close()
    pw.stop()


def _cards(pg):
    return pg.eval_on_selector_all("#app > section article.f4", """els => els.map(a => ({
        id: a.getAttribute('data-open'),
        labels: [...a.querySelectorAll(':scope > dl > dt')].map(x => x.textContent),
        values: [...a.querySelectorAll(':scope > dl > dd')].map(x => x.textContent),
        kids: [...a.children].map(x => x.tagName + '.' + x.className)}))""")


def test_every_main_card_has_exactly_the_four_items(page_for):
    snap = _fixture()
    pg = page_for(snap)
    cards = _cards(pg)
    ids = {r["id"] for r in snap["experiments"]} | {d["id"] for d in snap["decided"]}
    assert len(cards) == len(ids) and {c["id"] for c in cards} == ids    # every experiment, once
    for c in cards:
        assert c["labels"] == LABELS, c
        assert all(v.strip() for v in c["values"]), c                     # never blank
        assert c["kids"] == ["H3.", "DL.f4-l", "DIV.f4-more"], c          # nothing else on the card
    assert not page_for.errors, page_for.errors
    pg.close()


def test_card_wording_per_state(page_for):
    snap = _fixture()
    pg = page_for(snap)
    by = {c["id"]: dict(zip(c["labels"], c["values"])) for c in _cards(pg)}
    live = [r for r in snap["experiments"] if r.get("health")]
    planned = [r for r in snap["experiments"] if not r.get("health") and r.get("lifecycle") in ("planned", "draft")]
    pending = {r["id"] for r in snap["experiments"] if r.get("decision_pending")}
    for r in live:
        c = by[r["id"]]
        assert c["Timeline"].startswith("Launched "), c
        want = "Decided in the dash: " if r["id"] in pending else "Not called yet - "
        assert c["Conclusion"].startswith(want), c
    for r in planned:
        assert by[r["id"]]["Timeline"].startswith("Not launched yet"), by[r["id"]]
    for d in snap["decided"]:
        c = by[d["id"]]
        assert c["Conclusion"].startswith("Decided: ") and " decided " in c["Timeline"], c
        assert c["What’s being tested"] != "Not written down yet.", d["id"]   # registry fill worked
    # a measured before/after carries its sample sizes
    tx = by["EXP-2026-11-texas-only-forever-89"]["Before vs after"]
    assert "Before" in tx and "After" in tx and "(" in tx, tx
    pg.close()


def test_canaries_no_data_hidden_and_overdue(page_for):
    # planted rows the view must not dress up: no numbers, a read-locked split, an overdue call
    base = {"generated_at": "2026-11-27T12:00:00Z", "today": "2026-11-27", "decided": [], "changes": []}
    rows = [
        {"id": "EXP-x-nodata", "name": "No data", "health": "on_track", "tests": "Does X?", "start_date": "2026-11-20",
         "review_date": "2026-12-04", "reporting": "manual"},
        {"id": "EXP-x-hidden", "name": "Hidden", "health": "on_track", "tests": "Does Y?", "start_date": "2026-11-20",
         "review_date": "2026-12-04", "result_hidden": True, "result_text": "Result: hidden until its read date",
         "verdict": {"numbers": {"before_rate": 0.1, "after_rate": 0.9, "before": "1 of 10", "after": "9 of 10"}}},
        {"id": "EXP-x-late", "name": "Late", "health": "needs_call", "tests": "Does Z?", "start_date": "2026-11-01",
         "review_date": "2026-11-20"},
    ]
    pg = page_for(dict(base, experiments=rows))
    by = {c["id"]: dict(zip(c["labels"], c["values"])) for c in _cards(pg)}
    assert by["EXP-x-nodata"]["Before vs after"].startswith("No data yet")
    assert "90" not in by["EXP-x-hidden"]["Before vs after"] and "hidden" in by["EXP-x-hidden"]["Before vs after"]
    assert by["EXP-x-late"]["Conclusion"] == "Not called yet - decision was due Nov 20, 2026"
    assert "7 days past the decision date" in by["EXP-x-late"]["Timeline"]
    assert "Not called yet - decide on Dec 4, 2026" == by["EXP-x-nodata"]["Conclusion"]
    assert "7 days left" in by["EXP-x-nodata"]["Timeline"]
    pg.close()


def test_more_detail_keeps_everything_and_panel_opens(page_for):
    snap = _fixture()
    pg = page_for(snap)
    more = pg.query_selector("#nwmore")
    assert more and not more.evaluate("d => d.open")
    for sel in ("#nw-run-h", "#nw-tl-h", "#nw-dec-h", "#nwdet", "#results"):
        assert pg.query_selector("#nwmore " + sel), sel
    pg.click("#app > section article.f4")
    pg.wait_for_timeout(400)
    assert pg.eval_on_selector("#drawer", "d => d.classList.contains('on')")
    txt = pg.eval_on_selector("#drawer", "d => d.textContent")
    assert "How we’ll make the call" in txt and "The seven questions" in txt
    pg.close()


def test_deep_link_still_opens_that_experiment(page_for):
    snap = _fixture()
    target = snap["decided"][0]["id"]
    pg = page_for(snap, focus=target)
    assert pg.eval_on_selector("#drawer", "d => d.classList.contains('on')")
    assert target in pg.inner_text("#drawer") or (snap["decided"][0].get("name") or "")[:20] in pg.inner_text("#drawer")
    pg.close()


def test_phone_width_no_horizontal_scroll(page_for):
    pg = page_for(_fixture(), width=390)
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.close()
