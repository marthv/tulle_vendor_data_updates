"""Prewarm (owner 2026-11-02, "time-to-value needs to decrease a lot"): POST /rec/prewarm generates the couple's
opening in the background into the reuse store; /rec/opening then serves it with no model run, or waits for it while
it is still running. Prewarm rows never count toward an allowance; the opening served from one writes the opening
row a normal opening would. Also: why an opening was not reused (reuse_miss), and two concurrent openings for the
same couple run the model once. No network, no model."""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import opening_reuse as orr  # noqa: E402

T = "2026-11-02"
DAY0 = 1793577600                    # 2026-11-02 00:00 UTC
NOON_ET = DAY0 + 17 * 3600           # 17:00 UTC = 12:00 EST


class SyncPool:
    """Runs submitted work at once, so a test can read what the prewarm did right after the call."""

    def submit(self, fn, *a, **k):
        fn(*a, **k)


@pytest.fixture
def api(monkeypatch):
    import agent, app, config, xano
    from fastapi.testclient import TestClient
    st = {"runs": 0, "logged": [], "mp": [], "chats": 0, "paid": True, "access": True, "rows": [],
          "gate": None, "release": None,
          "memory": {"id": 1, "user_id": 7, "notes": [], "user_context": ""},
          "user": {"id": 7, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
                   "wedding_vibes": ["Rustic"], "favorited_vendors": ["S1"], "FreeViewsRemaining": 2,
                   "forever_access_purchased": True}}
    lock = threading.Lock()

    def run(token, user, access, messages, **kw):
        with lock:
            st["runs"] += 1
            n = st["runs"]
        if st["gate"] is not None:          # a test holding the model "mid-run"
            st["gate"].set()
            st["release"].wait(5)
        return ({"text": "Run %d picks." % n, "cards": [{"vendor_id": "V1", "name": "The Barn"}],
                 "more_cards": [], "chips": ["Waterfront venues"], "notes_saved": []},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    def create_chat(uid, title):
        with lock:
            st["chats"] += 1
            return {"id": 50 + st["chats"], "title": title}

    orr.INFLIGHT.clear()
    monkeypatch.setattr(config, "OPENING_REUSE", True)
    monkeypatch.setattr(config, "PREWARM", True)
    monkeypatch.setattr(config, "PREWARM_MAX_PER_DAY", 3)
    monkeypatch.setattr(config, "PREWARM_JOIN_S", 5)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "PROFILE_QUESTIONS", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", st["user"]))
    monkeypatch.setattr(app, "_PREWARM_POOL", SyncPool())
    app._PREWARM_RUNS.clear()
    # The app's own pool writes usage rows; run them inline so the assertions see them.
    monkeypatch.setattr(app, "_POOL", _InlineFuturePool())
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: st["memory"])
    monkeypatch.setattr(xano, "mark_questions_asked", lambda uid, ids: None)
    monkeypatch.setattr(xano, "create_chat", create_chat)
    monkeypatch.setattr(xano, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "user_usage", lambda uid: list(st["rows"]))
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: st["paid"])
    monkeypatch.setattr(xano, "beta_member", lambda u: st["paid"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: st["mp"].append(props))
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    st["c"] = TestClient(app.app)
    yield st
    orr.INFLIGHT.clear()


class _Done:
    def __init__(self, v):
        self._v = v

    def result(self, timeout=None):
        return self._v


class _InlineFuturePool:
    def submit(self, fn, *a, **k):
        return _Done(fn(*a, **k))


def prewarm(api):
    return api["c"].post("/rec/prewarm").json()


def opening(api):
    return api["c"].post("/rec/opening").json()


def kinds(api):
    return [(r.get("kind"), r.get("status"), r.get("model")) for r in api["logged"]]


# ---------------------------------------------------------------- the main path

def test_prewarm_then_opening_runs_the_model_once(api):
    assert prewarm(api) == {"status": "started"}
    assert api["runs"] == 1
    j = opening(api)
    assert api["runs"] == 1 and j["text"] == "Run 1 picks." and j["cards"] and j["chat_id"]
    # the prewarm row carries the cost and is never counted; the served opening writes the usual opening row
    assert kinds(api) == [("prewarm", "ok", "m"), ("opening", "ok", "prewarm")]
    pre, served = api["logged"]
    assert pre["counted_as_free"] is False and served["counted_as_free"] is False and served["cost_usd"] == 0
    ev = [p for p in api["mp"] if p.get("kind") == "opening"][-1]
    assert ev["reused"] is True and ev["model"] == "prewarm"
    assert [p["kind"] for p in api["mp"]][0] == "prewarm"
    # counted like a normal opening on the paid daily allowance (40 a day in the suite's config)
    assert j["forever_left_today"] == 39


def test_after_the_prewarmed_opening_a_repeat_visit_is_a_plain_reuse(api):
    prewarm(api)
    opening(api)
    api["rows"] = [{"status": "ok", "kind": "prewarm", "usage_day": T},
                   {"status": "ok", "kind": "opening", "usage_day": T}]
    j = opening(api)
    assert api["runs"] == 1 and j["forever_left_today"] == 39   # only the served opening counts; prewarm never
    assert kinds(api)[-1] == ("opening", "ok", "prewarm")         # the repeat wrote no new row


def test_second_prewarm_with_same_inputs_runs_nothing(api):
    prewarm(api)
    prewarm(api)
    assert api["runs"] == 1
    import app
    assert app._PREWARM_RUNS[("7", orr.et_day())] == 1   # the no-op gave its slot back


def test_changed_inputs_after_prewarm_regenerate(api):
    prewarm(api)
    api["user"]["FreeViewsRemaining"] = 1          # used a free PDF view in between
    j = opening(api)
    assert api["runs"] == 2 and j["text"] == "Run 2 picks."
    ev = [p for p in api["mp"] if p.get("kind") == "opening"][-1]
    assert ev["reused"] is False and ev["reuse_miss"] == "fp:free_views"
    assert api["logged"][-1]["reuse_miss"] == "fp:free_views"


def test_daily_limit(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "PREWARM_MAX_PER_DAY", 1)
    assert prewarm(api)["status"] == "started"
    api["user"]["Wedding_Guest_Count"] = 150
    assert prewarm(api) == {"status": "limit"}
    assert api["runs"] == 1


def test_off_switch(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "PREWARM", False)
    assert prewarm(api) == {"status": "off"} and api["runs"] == 0
    monkeypatch.setattr(config, "PREWARM", True)
    monkeypatch.setattr(config, "OPENING_REUSE", False)
    assert prewarm(api) == {"status": "off"} and api["runs"] == 0


def test_gate_still_applies_to_prewarm(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert prewarm(api) == {"status": "started"}          # the page never waits on it...
    assert api["runs"] == 0                                # ...but the gate refused the run
    assert kinds(api) == [("prewarm", "killed", config.MODEL)]


def test_free_couple_prewarm_counts_nothing(api):
    api.update(paid=False, access=False)
    api["user"]["forever_access_purchased"] = False
    a = opening(api)                    # baseline counters for a normal opening
    orr.STORE.clear()
    api["logged"].clear()
    prewarm(api)
    b = opening(api)
    assert api["runs"] == 2 and b["free_refines_left"] == a["free_refines_left"] and b["price_locked"] is True
    assert all(r["counted_as_free"] is False for r in api["logged"])


def test_prewarm_rows_never_count_as_prompts():
    import guards
    rows = [{"status": "ok", "kind": "prewarm", "usage_day": T}, {"status": "ok", "kind": "opening", "usage_day": T}]
    assert guards.used_today(rows, T) == 1
    assert guards.forever_left_today(True, rows, T) == guards.forever_left_today(True, rows[1:], T)


# ---------------------------------------------------------------- waiting on a run in flight

def _hold(api):
    api["gate"], api["release"] = threading.Event(), threading.Event()


def test_opening_waits_for_a_running_prewarm(api):
    import app
    _hold(api)
    t = threading.Thread(target=lambda: app._prewarm_job("tok", api["user"]))
    t.start()
    assert api["gate"].wait(5)          # the prewarm is mid-model-run
    out = {}
    o = threading.Thread(target=lambda: out.update(opening(api)))
    o.start()
    time.sleep(0.4)
    assert api["runs"] == 1             # the opening did not start a second run
    api["release"].set()
    t.join(5)
    o.join(5)
    assert out["text"] == "Run 1 picks." and api["runs"] == 1
    assert kinds(api) == [("prewarm", "ok", "m"), ("opening", "ok", "prewarm")]


def test_two_concurrent_openings_run_the_model_once(api):
    _hold(api)
    res = []
    a = threading.Thread(target=lambda: res.append(opening(api)))
    a.start()
    assert api["gate"].wait(5)
    b = threading.Thread(target=lambda: res.append(opening(api)))
    b.start()
    time.sleep(0.4)
    api["release"].set()
    a.join(5)
    b.join(5)
    assert api["runs"] == 1 and [r["text"] for r in res] == ["Run 1 picks.", "Run 1 picks."]
    # the waiting one is a plain reuse: no second usage row
    assert kinds(api) == [("opening", "ok", "m")]


def test_wait_gives_up_after_the_join_timeout(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "PREWARM_JOIN_S", 0.3)
    _hold(api)
    res = []
    a = threading.Thread(target=lambda: res.append(opening(api)))
    a.start()
    assert api["gate"].wait(5)
    api["gate"] = None                  # the second run is not held
    j = opening(api)
    api["release"].set()
    a.join(5)
    assert api["runs"] == 2 and j["text"] == "Run 2 picks."
    assert [p for p in api["mp"] if p.get("kind") == "opening"][0]["reuse_miss"] == "join_timeout"


def test_a_failed_run_releases_waiters(api, monkeypatch):
    import agent
    h = orr.INFLIGHT.begin(7, "fp")

    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(agent, "run", boom)
    assert api["c"].post("/rec/opening").status_code == 502
    orr.INFLIGHT.end(7, h)
    assert orr.INFLIGHT.running(7, "fp") is None


# ---------------------------------------------------------------- miss reasons (store unit tests)

def ok_result():
    return {"text": "Three picks.", "cards": [{"vendor_id": "V1"}], "chips": []}


def test_lookup_reasons(monkeypatch):
    import config
    monkeypatch.setattr(config, "OPENING_REUSE_MAX_AGE_S", 6 * 3600)
    s = orr.Store()
    parts = {"profile": {"guests": 120}, "saved": ["S1"], "free_views": 2}
    fp = orr.fingerprint(parts)
    assert s.lookup(7, fp, parts, NOON_ET) == (None, "", "none")
    s.put(7, fp, ok_result(), NOON_ET, parts=parts, src="prewarm")
    got, src, miss = s.lookup(7, fp, parts, NOON_ET + 60)
    assert got["text"] == "Three picks." and src == "prewarm" and miss == ""
    p2 = dict(parts, saved=["S1", "S2"], free_views=1)
    assert s.lookup(7, orr.fingerprint(p2), p2, NOON_ET + 60)[2] == "fp:free_views,saved"
    assert s.lookup(7, "other", None, NOON_ET + 60)[2] == "fp:?"
    assert s.lookup(7, fp, parts, NOON_ET + 6 * 3600 + 1)[2] == "age"
    assert s.lookup(7, fp, parts, NOON_ET + 13 * 3600)[2] == "day"
    assert s.get(7, fp, NOON_ET + 60)["text"] == "Three picks."     # get() unchanged for existing callers


def test_inflight_wait_rules():
    f = orr.Inflight()
    assert f.wait(7, "fp", 1) is False                   # nothing running
    h = f.begin(7, "fp")
    assert f.running(7, "other") is None                 # different inputs never wait
    assert f.wait(7, "fp", 0.2) is False                 # timed out
    assert f.wait(7, "fp", 5, cancelled=lambda: True) is False   # Stop
    threading.Timer(0.2, lambda: f.end(7, h)).start()
    assert f.wait(7, "fp", 5) is True
    assert f.running(7, "fp") is None
