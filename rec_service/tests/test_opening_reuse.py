"""Reuse today's opening (2026-10-27, opening_reuse.py): same couple + same ET day + unchanged inputs -> the stored
answer with no model run and no rec_usage row; anything that shapes the answer changing -> a fresh opening. The gate
still runs first, counters are fresh and nothing is counted. No network, no model."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import opening_reuse as orr  # noqa: E402

T = "2026-10-27"
DAY0 = 1793059200                    # 2026-10-27 00:00 UTC
NOON_ET = DAY0 + 16 * 3600           # 16:00 UTC = 12:00 EDT


# ---------------------------------------------------------------- store unit tests

def ok_result(**kw):
    return dict({"text": "Three picks.", "cards": [{"vendor_id": "V1"}], "chips": [], "notes_saved": ["x"]}, **kw)


def test_et_day_boundary():
    assert orr.et_day(NOON_ET) == "2026-10-27"
    assert orr.et_day(DAY0 + 3 * 3600) == "2026-10-26"     # 03:00 UTC = 23:00 EDT the day before
    assert orr.et_day(DAY0 + 4 * 3600 + 1) == "2026-10-27"


def test_fingerprint_is_order_independent_and_sensitive():
    a = orr.fingerprint({"a": 1, "b": [1, 2]})
    assert a == orr.fingerprint({"b": [1, 2], "a": 1})
    assert a != orr.fingerprint({"a": 1, "b": [2, 1]})


def test_store_rules(monkeypatch):
    import config
    monkeypatch.setattr(config, "OPENING_REUSE_MAX_AGE_S", 6 * 3600)
    s = orr.Store()
    assert s.put(7, "fp", ok_result(), NOON_ET)
    got = s.get(7, "fp", NOON_ET + 60)
    assert got["text"] == "Three picks." and got["notes_saved"] == []      # a reuse never claims it saved notes
    got["cards"].append("mutated")
    assert len(s.get(7, "fp", NOON_ET + 60)["cards"]) == 1                 # deep copies
    assert s.get(7, "other-fp", NOON_ET + 60) is None                      # inputs changed
    assert s.get(8, "fp", NOON_ET + 60) is None                            # other user
    assert s.get(7, "fp", NOON_ET + 6 * 3600 + 1) is None                  # too old
    assert s.get(7, "fp", NOON_ET + 13 * 3600) is None                     # next ET day (01:00 EDT)


@pytest.mark.parametrize("bad", [{"stopped": True}, {"cards": []}, {"text": "  "}])
def test_only_normal_answers_are_stored(bad):
    s = orr.Store()
    assert not s.put(7, "fp", ok_result(**bad), NOON_ET)
    assert s.get(7, "fp", NOON_ET) is None


# ---------------------------------------------------------------- endpoint

@pytest.fixture
def api(monkeypatch):
    import agent, app, config, xano
    from fastapi.testclient import TestClient
    st = {"runs": 0, "logged": [], "mp": [], "chats": 0, "paid": True, "access": True, "rows": [],
          "memory": {"id": 1, "user_id": 7, "notes": [], "user_context": ""},
          "user": {"id": 7, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
                   "wedding_vibes": ["Rustic"], "favorited_vendors": ["S1"], "FreeViewsRemaining": 2,
                   "forever_access_purchased": True}}

    def run(token, user, access, messages, **kw):
        st["runs"] += 1
        return ({"text": "Run %d picks." % st["runs"], "cards": [{"vendor_id": "V1", "name": "The Barn"}],
                 "more_cards": [], "chips": ["Waterfront venues"], "notes_saved": []},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    def create_chat(uid, title):
        st["chats"] += 1
        return {"id": 50 + st["chats"], "title": title}

    monkeypatch.setattr(config, "OPENING_REUSE", True)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "PROFILE_QUESTIONS", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", st["user"]))
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
    return st


def opening(api):
    return api["c"].post("/rec/opening").json()


def test_second_visit_reuses_without_model_or_usage_row(api):
    a = opening(api)
    b = opening(api)
    assert api["runs"] == 1 and a["text"] == b["text"] == "Run 1 picks." and b["cards"] == a["cards"]
    assert b["chat_id"] != a["chat_id"]                        # still a fresh chat on every visit, as the page expects
    assert [r["status"] for r in api["logged"]] == ["ok"]      # the reuse wrote no rec_usage row
    assert [p.get("reused") for p in api["mp"] if p.get("kind") == "opening"] == [False, True]
    assert api["mp"][-1]["model"] == "reuse" and api["mp"][-1]["cost_usd"] == 0
    assert b["forever_left_today"] == 40      # the stub usage rows are empty: the reuse counted nothing


@pytest.mark.parametrize("change", [
    lambda st: st["user"].update(Wedding_Guest_Count=200),                       # profile field
    lambda st: st["user"].update(favorited_vendors=["S1", "S2"]),                 # saved a venue
    lambda st: st["user"].update(FreeViewsRemaining=1),                          # used a free PDF view
    lambda st: st.update(access=False, paid=False),                              # tier changed
    lambda st: st["user"].update(total_amount_paid=59),                          # entitlement input
    lambda st: st["memory"].update(notes=[{"note": "Wants a lake view"}]),        # memory
    lambda st: st["memory"].update(user_context="We're vegan"),                  # what they asked us to keep in mind
])
def test_anything_that_shapes_the_answer_regenerates(api, change):
    opening(api)
    change(api)
    j = opening(api)
    assert api["runs"] == 2 and j["cards"]


def test_prompt_or_version_change_regenerates(api, monkeypatch):
    import opening_reuse
    opening(api)
    monkeypatch.setattr(opening_reuse, "VERSION", "next")
    opening(api)
    assert api["runs"] == 2


def test_gate_still_runs_first(api, monkeypatch):
    import config
    opening(api)
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    r = api["c"].post("/rec/opening")
    assert r.status_code == 503 and api["runs"] == 1


def test_off_switch(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "OPENING_REUSE", False)
    opening(api)
    opening(api)
    assert api["runs"] == 2


def test_reuse_does_not_count_toward_paid_daily_cap(api):
    opening(api)
    api["rows"] = [{"status": "ok", "kind": "opening", "usage_day": T}]   # the first opening's row
    j = opening(api)
    assert j["forever_left_today"] == 39   # only the real opening counted


def test_free_user_reuse_keeps_free_counters(api):
    api.update(paid=False, access=False)
    api["user"]["forever_access_purchased"] = False
    a = opening(api)
    b = opening(api)
    assert api["runs"] == 1 and b["free_refines_left"] == a["free_refines_left"] and b["price_locked"] is True
