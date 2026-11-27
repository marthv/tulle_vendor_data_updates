"""Experiments tab: keyword search + category filter (owner 2026-11-27).

* the pure JS block (/* exp-search:start */ ... /* exp-search:end */ in component/index.html) runs in node against
  planted cases, and two planted mutants of it (any-word instead of all-words; category ignored) must FAIL those cases,
  so the cases can catch a broken filter;
* every registry entry carries exactly one known category, validate.py requires it (planted bad registries fail);
* the category list reaches the page from experiments_engine/categories.py, and rows get their category;
* in a real browser (skips without playwright): typing / picking categories narrows the four-item cards, the
  More-detail grid combines with its status chips, and a ?exp= link clears the filters and opens that experiment.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from experiments_engine.categories import CATEGORIES, problem  # noqa: E402
from experiments_ui import render  # noqa: E402

REGISTRY = os.path.join(ROOT, "experiments", "experiment_log.json")


def _registry():
    with open(REGISTRY, encoding="utf-8") as f:
        return json.load(f)


def _block():
    with open(render.TEMPLATE_PATH, encoding="utf-8") as f:
        page = f.read()
    m = re.search(r"/\* exp-search:start.*?/\* exp-search:end \*/", page, re.S)
    assert m, "exp-search block markers missing from component/index.html"
    return m.group(0)


# ── planted cases ───────────────────────────────────────────────────────────────────────────
ROWS = [
    {"id": "EXP-a-price-report", "name": "Tulle Price Report replaces the PI card", "category": "Price Report & PI card"},
    {"id": "EXP-b-only-tests", "name": "Alpha", "tests": "Does the zebra banner lift unlocks?", "category": "Paywall & PDF limit"},
    {"id": "EXP-c-only-hyp", "name": "Beta", "hypothesis": "If couples see a giraffe, they stay", "category": "Assistant"},
    {"id": "EXP-d-only-goal", "name": "Gamma", "goal": "More Okapi opens", "category": "Onboarding"},
    {"id": "EXP-e-only-tldr", "name": "Delta", "tldr": "narwhal wins", "category": "Assistant"},
    {"id": "EXP-f-verdict-tldr", "name": "Epsilon", "verdict": {"tldr": "Quokka looks better"}, "category": "Assistant"},
    {"id": "EXP-g-unsearched", "name": "Zeta", "what_changed": "pangolin", "decision": "pangolin", "category": "Assistant"},
    {"id": "EXP-h-no-category", "name": "Planner photo test"},
    {"id": "EXP-2026-11-search-box-fit", "name": "Discovery search box", "category": "Search & Discovery"},
    {"id": "EXP-i-nulls", "name": None, "tests": None, "category": None},
]
# (query, categories, expected ids)
CASES = [
    ("", [], [r["id"] for r in ROWS]),                                   # nothing set: everything
    ("   ", [], [r["id"] for r in ROWS]),                                # blank query: everything
    ("price report", [], ["EXP-a-price-report"]),
    ("REPORT price", [], ["EXP-a-price-report"]),                        # case-insensitive, any word order
    ("price zebra", [], []),                                             # ALL words must match
    ("zebra", [], ["EXP-b-only-tests"]),                                 # tests text
    ("giraffe", [], ["EXP-c-only-hyp"]),                                 # hypothesis text
    ("okapi", [], ["EXP-d-only-goal"]),                                  # goal text
    ("narwhal", [], ["EXP-e-only-tldr"]),                                # tldr
    ("quokka", [], ["EXP-f-verdict-tldr"]),                              # engine verdict tldr
    ("pangolin", [], []),                                                # what_changed / decision are not searched
    ("search-box", [], ["EXP-2026-11-search-box-fit"]),                  # id text
    ("exp-2026-11-search", [], ["EXP-2026-11-search-box-fit"]),
    ("", ["Assistant"], ["EXP-c-only-hyp", "EXP-e-only-tldr", "EXP-f-verdict-tldr", "EXP-g-unsearched"]),
    ("", ["Assistant", "Onboarding"], ["EXP-c-only-hyp", "EXP-d-only-goal", "EXP-e-only-tldr", "EXP-f-verdict-tldr",
                                     "EXP-g-unsearched"]),               # multi-select = either category
    ("", ["Other"], ["EXP-h-no-category", "EXP-i-nulls"]),               # no category counts as Other
    ("planner", ["Assistant"], []),                                        # 'Planner photo test' is Other: AND, not OR
    ("planner", ["Other"], ["EXP-h-no-category"]),
    ("giraffe", ["Onboarding"], []),
    ("", ["Growth & ads"], []),
]

DRIVER = """
const rows = %s, cases = %s;
const out = cases.map(([q, cats]) => filterExps(rows, q, cats).map(r => r.id));
// never throws on junk
filterExps([null, 3, "x", {}], "a", []); filterExps(null, null, null); matchesSearch(undefined, "a", ["Other"]);
out.push([filterActive({q: " ", cats: []}), filterActive({q: "a", cats: []}), filterActive({q: "", cats: ["Assistant"]}), filterActive(null)]);
console.log(JSON.stringify(out));
"""


def _run(block, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    p = tmp_path / "search_check.js"
    p.write_text(block + "\n" + DRIVER % (json.dumps(ROWS), json.dumps([[q, c] for q, c, _ in CASES])), encoding="utf-8")
    r = subprocess.run([node, str(p)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _failures(got):
    return [(q, c, want, g) for (q, c, want), g in zip(CASES, got) if sorted(g) != sorted(want)]


def test_search_and_category_cases(tmp_path):
    got = _run(_block(), tmp_path)
    assert not _failures(got[:-1]), _failures(got[:-1])
    assert got[-1] == [False, True, True, False]


@pytest.mark.parametrize("old,new", [
    # any word instead of all words
    ("for (var i = 0; i < words.length; i++) if (t.indexOf(words[i]) < 0) return false;",
     "if (!words.some(function(w){ return t.indexOf(w) >= 0; })) return false;"),
    # category filter ignored
    ("if (cats && cats.length && cats.indexOf(categoryOf(e)) < 0) return false;", ""),
])
def test_planted_mutants_are_caught(tmp_path, old, new):
    block = _block()
    assert old in block
    got = _run(block.replace(old, new), tmp_path)
    assert _failures(got[:-1]), "the planted cases did not catch a broken filter"


# ── registry + validate.py ──────────────────────────────────────────────────────────────────
def test_every_entry_has_one_known_category():
    exps = _registry()["experiments"]
    assert len(exps) >= 70
    bad = [(e.get("id"), e.get("category")) for e in exps if e.get("category") not in CATEGORIES]
    assert not bad, bad
    # the older fields are untouched
    assert all("themes" in e for e in exps)


def test_category_problem():
    assert problem({"category": "Assistant"}) is None
    assert "No category" in problem({})
    assert "No category" in problem({"category": ""})
    assert "Unknown category" in problem({"category": "assistant"})       # exact label, case matters
    assert problem(None)


def _validate(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "validate.py"), *args],
                          capture_output=True, text=True, timeout=300, cwd=ROOT)


def test_validate_passes_on_the_registry():
    r = _validate("--all")
    assert r.returncode == 0, r.stdout[-2000:]
    assert "No category" not in r.stdout and "Unknown category" not in r.stdout


@pytest.mark.parametrize("value", [None, "Assistant"])
def test_validate_fails_without_a_known_category(tmp_path, value):
    reg = _registry()
    # a decided entry: the category rule applies to every entry, not only live/planned ones
    target = next(e for e in reg["experiments"] if e.get("status") in ("shipped", "reverted", "shelved"))
    if value is None:
        target.pop("category")
    else:
        target["category"] = value
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    r = _validate("--registry", str(p))
    assert r.returncode == 1, r.stdout[-1500:]
    assert target["id"] in r.stdout.split("FAILED:")[-1]
    r2 = _validate("--registry", str(p), "--id", target["id"])
    assert r2.returncode == 1 and "category" in r2.stdout


# ── the list and the rows reach the page ───────────────────────────────────────────────────
def test_attach_categories_and_row_fill():
    snap = render.attach_categories({"experiments": [{"id": "A"}], "decided": [{"id": "B", "category": "Other"}]})
    assert snap["categories"] == list(CATEGORIES)
    snap = render.attach_plain_fields(snap, {"experiments": [{"id": "A", "category": "Assistant", "tldr": "t"},
                                                             {"id": "B", "category": "Assistant"}]})
    assert snap["experiments"][0]["category"] == "Assistant" and snap["experiments"][0]["tldr"] == "t"
    assert snap["decided"][0]["category"] == "Other"                    # never overrides the engine
    assert render.attach_categories(None) is None


def test_engine_snapshot_rows_carry_category():
    from experiments_engine.snapshot import build_snapshot
    snap = build_snapshot(_registry(), metric_runner=None, history_store=None)
    rows = [r for r in snap["experiments"] + snap["decided"] if not r.get("folded_into")]
    assert rows and all(r.get("category") in CATEGORIES for r in rows), [r.get("id") for r in rows if not r.get("category")]
    mini = render._minimal_snapshot(_registry(), render._date("2026-11-27"))
    assert all(r.get("category") in CATEGORIES for r in mini["experiments"] + mini["decided"])


# ── browser ─────────────────────────────────────────────────────────────────────────────────
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
    d = tmp_path_factory.mktemp("xs")
    errors = []

    def make(focus=None, width=1280):
        with open(os.path.join(ROOT, "experiments_ui", "fixture_snapshot.json"), encoding="utf-8") as f:
            snap = json.load(f)
        snap = render.attach_categories(render.attach_plain_fields(snap, _registry()))
        p = d / f"p{len(os.listdir(d))}.html"
        p.write_text(render.build_html(snap, theme_mode="light", focus=focus), encoding="utf-8")
        pg = b.new_page(viewport={"width": width, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(p.as_uri())
        pg.wait_for_timeout(400)
        return pg, snap
    make.errors = errors
    yield make
    b.close()
    pw.stop()


def _main_ids(pg):
    return pg.eval_on_selector_all("#app > section article.f4", "els => els.map(a => a.getAttribute('data-open'))")


def test_browser_search_and_categories(page_for):
    pg, snap = page_for()
    reg = {e["id"]: e for e in _registry()["experiments"]}
    every = _main_ids(pg)
    assert len(every) > 20
    pg.fill("#fq", "price report")
    pg.wait_for_timeout(150)
    hits = _main_ids(pg)
    assert hits and len(hits) < len(every)
    for i in hits:
        e = reg[i]
        text = " ".join(str(e.get(k) or "") for k in ("id", "name", "tldr", "tests", "hypothesis", "goal")).lower()
        assert "price" in text and "report" in text, i
    assert pg.evaluate("document.activeElement.id") == "fq"            # typing never loses the box
    assert "match" in pg.inner_text("#fcount")
    pg.fill("#fq", "")
    pg.click('[data-fcat="Growth & ads"]')
    pg.click('[data-fcat="Onboarding"]')
    pg.wait_for_timeout(150)
    got = set(_main_ids(pg))
    want = {i for i in every if reg.get(i, {}).get("category") in ("Growth & ads", "Onboarding")}
    assert got == want and got
    # the More-detail grid (with its status chips) combines with the categories
    grid = pg.eval_on_selector_all("#results [data-open]", "els => [...new Set(els.map(a => a.getAttribute('data-open')))]")
    assert set(grid) <= want
    pg.fill("#fq", "zzzz-no-such-thing")
    pg.wait_for_timeout(150)
    assert not _main_ids(pg) and pg.query_selector("[data-f4].fnone")
    pg.click("[data-f4].fnone [data-fclear]")
    pg.wait_for_timeout(150)
    assert len(_main_ids(pg)) == len(every) and pg.input_value("#fq") == ""
    # cards still open the side panel after a filter redraw
    pg.fill("#fq", "planner")
    pg.wait_for_timeout(150)
    pg.click("#app > section article.f4")
    pg.wait_for_timeout(300)
    assert pg.eval_on_selector("#drawer", "d => d.classList.contains('on')")
    assert not page_for.errors, page_for.errors
    pg.close()


def test_deep_link_opens_regardless_of_filters(page_for):
    pg, snap = page_for()
    target = snap["decided"][0]["id"]
    pg.close()
    # a ?exp= boot starts with no filter (KEEP.F reset), so the linked card is on the page and the panel is open
    pg, _ = page_for(focus=target)
    assert pg.eval_on_selector("#drawer", "d => d.classList.contains('on')")
    assert target in _main_ids(pg) and pg.input_value("#fq") == ""
    pg.close()


def test_phone_width_with_the_bar(page_for):
    pg, _ = page_for(width=390)
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    pg.close()
