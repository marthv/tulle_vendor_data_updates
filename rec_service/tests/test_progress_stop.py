"""Progress labels + Stop (2026-10-25): GET /rec/progress/{request_id}, POST /rec/cancel/{request_id}, and the
checkpoints inside agent.run. No network, no model: xano and the tool runner are stubbed."""
import copy
import pathlib
import re
import sys
import types
from concurrent.futures import Future

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for mod in ("anthropic",):
    sys.modules.setdefault(mod, types.ModuleType(mod))
sys.modules["anthropic"].beta_tool = getattr(sys.modules["anthropic"], "beta_tool", lambda f: f)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import progress  # noqa: E402

TODAY = "2026-10-25"
# Nothing internal may reach a label: vendor ids, tool names, snake_case, braces, prompt words.
INTERNAL = re.compile(r"V\d+|_|\{|\}|search_venues|venue_pricing|estimate_cost|present|prompt|tool", re.I)


@pytest.fixture(autouse=True)
def fresh():
    progress.reset()
    yield
    progress.reset()


# ---------------------------------------------------------------- registry

def test_lifecycle_running_then_done_and_steps_count_label_changes():
    t = progress.start("r1", 7)
    assert progress.get("r1", 7) == {"state": "running", "label": progress.START, "step": 1}
    t.set(progress.START)                      # same label: no new step
    t.set("Searching venues in Texas…")
    assert progress.get("r1", 7) == {"state": "running", "label": "Searching venues in Texas…", "step": 2}
    t.finish("done")
    assert progress.get("r1", 7)["state"] == "done"
    t.set(progress.WRITING)                    # after finishing: ignored
    assert progress.get("r1", 7)["step"] == 2


def test_no_request_id_is_not_tracked():
    assert progress.start("", 7) is None and progress.start(None, 7) is None
    assert progress.get("nope", 7) == {"state": "unknown", "label": "", "step": 0}


def test_other_user_cannot_read_cancel_or_take_over():
    t = progress.start("r1", 7)
    assert progress.get("r1", 8) is None and progress.cancel("r1", 8) is False
    other = progress.start("r1", 8)            # runs untracked; the owner's entry is untouched
    other.set(progress.WRITING)
    assert progress.get("r1", 7)["label"] == progress.START and not t.cancelled()


def test_cancel_before_start_is_kept_and_the_request_starts_cancelled():
    assert progress.cancel("r9", 7) is True
    assert progress.start("r9", 7).cancelled()
    assert progress.cancel("r10", 7) and not progress.start("r10", 8).cancelled()   # another user's pre-cancel


def test_expiry_ten_minutes_after_finishing(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(progress.time, "time", lambda: now[0])
    t = progress.start("r1", 7)
    now[0] += 3600                              # still running an hour in: never expires while running... (safety
    t.finish("done")                            # net aside) - it finishes now
    now[0] += progress.TTL_AFTER_FINISH_S - 1
    assert progress.get("r1", 7)["state"] == "done"
    now[0] += 2
    assert progress.get("r1", 7) == {"state": "unknown", "label": "", "step": 0}


def test_bounded_size_evicts_oldest_finished_first(monkeypatch):
    monkeypatch.setattr(progress, "MAX_ENTRIES", 3)
    a = progress.start("a", 7)
    progress.start("b", 7)
    a.finish("done")
    progress.start("c", 7)
    progress.start("d", 7)                      # full: "a" (finished) goes, running "b" stays
    assert progress.get("a", 7)["state"] == "unknown" and progress.get("b", 7)["state"] == "running"
    progress.start("e", 7)                      # nothing finished left: the oldest goes
    assert progress.get("b", 7)["state"] == "unknown" and len(progress._entries) == 3


def test_labels_are_friendly_short_and_generic():
    assert progress.search_label(["Texas"]) == "Searching venues in Texas…"
    assert progress.search_label(["Texas"], "Flower Mound") == "Searching venues in Flower Mound…"
    assert progress.search_label(["New York", "Ohio"]) == "Searching venues in New York and Ohio…"
    assert progress.search_label(["New Hampshire", "North Carolina"]) == "Searching venues in your area…"
    assert progress.search_label(["A", "B", "C"]) == "Searching venues in your area…"
    assert progress.search_label([]) == progress.search_label(["International"]) == "Searching venues in your area…"
    odd = progress.search_label(["Texas"], "{ignore rules} V123")   # model-written city: letters only survive
    assert not INTERNAL.search(odd) and not re.search(r"[\d{}]", odd)
    assert progress.pe_label("Photography") == "Searching photographers…"
    assert progress.pe_label("Entertainment") == "Searching entertainment…"
    assert progress.pricing_label(3) == "Checking real pricing for 3 venues…"
    assert progress.pricing_label(1) == progress.pricing_label(0) == "Checking real pricing…"
    every = ([progress.START, progress.THINKING, progress.WRITING, progress.search_label(["Texas"]),
              progress.pe_label("Photography"), progress.pe_label("Entertainment"), progress.pricing_label(8)]
             + list(progress.TOOL_LABELS.values()))
    for label in every:
        assert len(label) <= progress.LABEL_MAX and not INTERNAL.search(label), label


# ---------------------------------------------------------------- agent.run checkpoints

class _Block:
    def __init__(self, name=None, args=None, text=None):
        self.type = "text" if text is not None else "tool_use"
        self.name, self.input, self.text = name, args or {}, text


class _Msg:
    def __init__(self, blocks):
        self.content, self.model = blocks, "stub"
        self.usage = types.SimpleNamespace(input_tokens=100, output_tokens=10, cache_read_input_tokens=0,
                                           cache_creation_input_tokens=0)


def _fake_client(turns, seen):
    """Like the SDK's tool runner: each item is one model call returning tool_use blocks; a turn's tools run
    once (generate_tool_call_response, cached) before the next model call. seen["on_model"](i) runs during call i."""
    class Runner:
        def __init__(self, tools, **kw):
            # Real beta_tool objects (another test file may have loaded the real SDK) carry .name and .call.
            self.tools = {(getattr(t, "name", None) or t.__name__): t for t in tools}
            self.last, self.cached = None, None

        def generate_tool_call_response(self):
            if self.cached is None and self.last is not None:
                self.cached = []
                for b in self.last.content:
                    if b.type == "tool_use":
                        try:
                            t = self.tools[b.name]
                            self.cached.append(t.call(b.input) if hasattr(t, "call") else t(**b.input))
                        except Exception as e:   # the SDK turns a tool exception into an error result
                            self.cached.append("error: %s" % type(e).__name__)
            return self.cached

        def __iter__(self):
            for i, blocks in enumerate(turns):
                if self.last is not None:
                    self.generate_tool_call_response()
                self.cached = None
                seen["model_calls"] = seen.get("model_calls", 0) + 1
                seen.get("on_model", lambda i: None)(i)
                self.last = _Msg([_Block(*b) if isinstance(b, tuple) else b for b in blocks])
                yield self.last

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **kw: Runner(**kw)))
    return Client


def venue(vid):
    return {"vendor_id": vid, "name": "Venue %s" % vid, "state": "Texas", "address": "1 Main St, Austin, TX",
            "venue_type": "Estate", "max_capacity_seated": 200, "venue_fee_range": [5000, 5000], "food_minimum": 0,
            "description": ""}


PRICING = {"guest_minimum": 0, "verdict": "", "headline": "", "source_note": "", "cost_inputs": None,
           "lines": [{"line": "Rental fee", "source": "venue", "vs_market": "typical", "amount": 5000, "pct": None,
                      "detail": ""}]}


@pytest.fixture
def world(monkeypatch):
    import agent, config, xano
    st = {"tools": [], "labels": []}
    monkeypatch.setattr(config, "EARLY_STOP", True)
    monkeypatch.setattr(config, "COST_TOOLS_IN_CHAT", True)

    def search(token, *, states, page_size=8, **f):
        st["tools"].append("xano.search")
        if st.get("cancel_in_search"):
            st["tracker"]._e["cancel"] = True
        return {"total_matches": 3, "venues": [venue("V1"), venue("V2"), venue("V3")]}

    def price(tok, vid, paid):
        st["tools"].append("xano.pricing")
        return copy.deepcopy(PRICING)

    monkeypatch.setattr(xano, "search_venues", search)
    monkeypatch.setattr(xano, "venue_pricing", price)
    monkeypatch.setattr(xano, "venue_details", lambda vid, with_amounts=False: st["tools"].append("xano.details") or {})
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s, "metrics": {}})
    monkeypatch.setattr(xano, "saved_vendor_ids", lambda u: [])
    monkeypatch.setattr(xano, "vendor_card", lambda vid: venue(vid))
    monkeypatch.setattr(xano, "state_average_rows", lambda tok: [{"State": "Texas", "Cost_of_Average_Wedding": 24063}])
    monkeypatch.setattr(xano, "guest_modifier", lambda tok, n: 1.0)

    class Recording(progress.Tracker):
        def set(self, label):
            super().set(label)
            if label and (not st["labels"] or st["labels"][-1] != label):
                st["labels"].append(label)

    def run(turns, detail="full", on_model=None):
        seen = {"on_model": on_model} if on_model else {}
        monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client(turns, seen), raising=False)
        t = progress.start("req", 7)
        st["tracker"] = Recording(t._e)
        st["labels"][:] = [progress.START]
        res, usage = agent.run("tok", {"id": 7}, detail != "free", [{"role": "user", "content": "venues in Texas"}],
                               profile_override={"Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100},
                               detail=detail, progress=st["tracker"])
        return res, usage, seen
    st["run"] = run
    return st


PRESENT = ("present_recommendations", {"message": "Three to look at.", "vendor_ids": ["V1"], "reasons": ["r"],
                                       "chips": []})


def test_labels_follow_the_real_stages(world, monkeypatch):
    turns = [[("search_venues", {"states": ["Texas"]})],
             [("venue_pricing", {"vendor_id": "V1"}), ("venue_pricing", {"vendor_id": "V2"})],
             [("estimate_wedding_cost", {}), ("saved_venues", {})],
             [("split_budget", {})], [("compare_venues", {})], [("budget_status", {})],
             [("search_photographers_entertainment", {"category": "Photography", "states": ["Texas"]})],
             [PRESENT]]
    import xano
    monkeypatch.setattr(xano, "search_pe", lambda *a, **k: {"total_matches": 0, "vendors": []})
    monkeypatch.setattr(xano, "pe_benchmarks", lambda c, s: None)
    res, _, _ = world["run"](turns)
    assert res["text"] == "Three to look at." and not res.get("stopped")
    labels = world["labels"]
    for want in ["Reading your preferences…", "Searching venues in Texas…", "Checking real pricing for 3 venues…",
                 "Writing your answer…", "Checking real pricing for 2 venues…", "Estimating your total cost…",
                 "Looking at your saved venues…", "Splitting your budget…", "Comparing your saved venues…",
                 "Checking your budget…", "Searching photographers…"]:
        assert want in labels, (want, labels)
    for label in labels:
        assert len(label) <= progress.LABEL_MAX and not INTERNAL.search(label), label
    assert progress.get("req", 7)["step"] == len(labels)


def test_a_model_turn_without_tools_reads_thinking(world):
    world["run"]([[_Block(text="hmm")], [PRESENT]])
    assert progress.THINKING in world["labels"]


def test_stop_during_a_model_call_runs_none_of_its_tools_and_no_further_call(world):
    def on_model(i):
        if i == 0:
            world["tracker"]._e["cancel"] = True   # tapped Stop while the first model call was in flight
    res, usage, seen = world["run"]([[("search_venues", {"states": ["Texas"]})], [PRESENT]], on_model=on_model)
    assert res["stopped"] is True and res["text"] == "" and res["cards"] == [] and res["more_cards"] == []
    assert world["tools"] == [] and seen["model_calls"] == 1
    assert usage["input_tokens"] == 100 and usage["output_tokens"] == 10 and usage["stopped"] is True


def test_stop_inside_a_tool_skips_the_rest_of_the_turn_and_the_next_model_call(world):
    """Stop lands while search_venues is waiting on ep119: no pricing wave runs, the turn's second tool doesn't
    run, and the model is never called again."""
    world["cancel_in_search"] = True
    res, _, seen = world["run"]([[("search_venues", {"states": ["Texas"]}), ("venue_details", {"vendor_id": "V1"})],
                                 [PRESENT]])
    assert res["stopped"] is True
    assert world["tools"] == ["xano.search"] and seen["model_calls"] == 1


def test_stop_before_the_first_model_call_makes_no_model_call(world, monkeypatch):
    assert progress.cancel("req", 7)
    import agent
    seen = {}
    t = progress.start("req", 7)
    assert t.cancelled()
    monkeypatch.setattr(agent.anthropic, "Anthropic", _fake_client([[PRESENT]], seen), raising=False)
    res, usage = agent.run("tok", {"id": 7}, True, [{"role": "user", "content": "hi"}],
                           profile_override={"Wedding_Location_Updated": ["Texas"]}, progress=t)
    assert res["stopped"] and seen.get("model_calls", 0) == 0 and usage["input_tokens"] == 0


def test_no_progress_argument_behaves_as_before(world, monkeypatch):
    import agent
    seen = {}
    monkeypatch.setattr(agent.anthropic, "Anthropic",
                        _fake_client([[("search_venues", {"states": ["Texas"]})], [PRESENT]], seen), raising=False)
    res, usage = agent.run("tok", {"id": 7}, True, [{"role": "user", "content": "venues in Texas"}],
                           profile_override={"Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 100})
    assert res["text"] == "Three to look at." and "stopped" not in res and "stopped" not in usage


# ---------------------------------------------------------------- endpoints

class _Sync:
    """app._POOL stand-in: runs at once, so logged rows can be asserted."""
    def submit(self, f, *a, **k):
        fut = Future()
        try:
            fut.set_result(f(*a, **k))
        except Exception as e:
            fut.set_exception(e)
        return fut


@pytest.fixture
def api(monkeypatch):
    import agent, app, config, xano
    st = {"logged": [], "added": [], "created": [], "mp": [], "uid": 7, "run_kw": [], "history": []}

    def run(token, user, access, messages, **kw):
        st["run_kw"].append(kw)
        if st.get("stop_during_run") and kw.get("progress"):
            kw["progress"]._e["cancel"] = True
            return ({"stopped": True, "text": "", "cards": [], "chips": [], "more_cards": [], "widgets": []},
                    {"model": "claude-sonnet-5-5", "input_tokens": 2000, "cache_read_tokens": 0,
                     "cache_write_tokens": 0, "output_tokens": 100, "tool_calls": 1, "latency_ms": 900})
        return ({"text": "picks", "cards": [{"vendor_id": "V1", "name": "A"}], "more_cards": [], "chips": ["x"]},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    monkeypatch.setattr(app, "_POOL", _Sync())
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": st["uid"]}))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: st["created"].append(title) or {"id": 51, "title": title})
    monkeypatch.setattr(xano, "list_chats", lambda uid: [])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: copy.deepcopy(st["history"]))
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: st["logged"].append(row))
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: False)
    monkeypatch.setattr(xano, "beta_member", lambda u: False)        # a free user: the question would count
    monkeypatch.setattr(xano, "has_paid_access", lambda u: False)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: st["mp"].append(a))
    monkeypatch.setattr(xano, "today", lambda: TODAY)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    st["client"] = TestClient(app.app)
    return st


STOPPED_KEYS = {"status", "stopped", "text", "cards", "chips", "widgets", "more_available", "chat_id", "paid",
                "has_access", "tier", "price_locked", "questions_left", "free_refines_left", "paywall"}


def test_request_without_request_id_is_unchanged(api):
    r = api["client"].post("/rec/refine", json={"chat_id": 3, "message": "barns?"})
    assert r.status_code == 200 and r.json()["text"].startswith("Here are picks") and "stopped" not in r.json()
    assert "progress" not in api["run_kw"][0]
    assert api["logged"][-1]["status"] == "ok" and api["logged"][-1]["counted_as_free"] is True
    assert progress._entries == {}
    r = api["client"].post("/rec/opening")             # no body at all, as the page sends it today
    assert r.status_code == 200 and r.json()["chat_id"] == 51


def test_progress_endpoint_lifecycle_and_owner_only(api):
    c = api["client"]
    assert c.get("/rec/progress/abc").json() == {"state": "unknown", "label": "", "step": 0}
    r = c.post("/rec/refine", json={"chat_id": 3, "message": "barns?", "request_id": "abc"})
    assert r.status_code == 200 and r.json()["text"].startswith("Here are picks")
    assert "progress" in api["run_kw"][0]
    assert c.get("/rec/progress/abc").json()["state"] == "done"
    api["uid"] = 8
    assert c.get("/rec/progress/abc").status_code == 404
    assert c.post("/rec/cancel/abc").status_code == 404
    api["uid"] = 7
    assert c.post("/rec/cancel/abc").json() == {"ok": True}       # after it finished: a harmless no-op
    assert c.get("/rec/progress/abc").json()["state"] == "done"


def test_request_id_over_64_chars_is_rejected(api):
    r = api["client"].post("/rec/refine", json={"chat_id": 3, "message": "x", "request_id": "a" * 65})
    assert r.status_code == 422


def test_stopped_question_is_not_counted_logged_stopped_and_not_saved(api):
    api["stop_during_run"] = True
    r = api["client"].post("/rec/refine", json={"chat_id": 3, "message": "barns?", "request_id": "s1"})
    assert r.status_code == 200
    d = r.json()
    assert STOPPED_KEYS <= set(d)
    assert (d["status"], d["stopped"], d["text"], d["cards"], d["chips"], d["widgets"]) == ("stopped", True, "", [], [], [])
    assert d["chat_id"] == 3 and d["free_refines_left"] == 1 and d["questions_left"] == 1   # nothing spent
    row = api["logged"][-1]
    assert row["status"] == "stopped" and row["counted_as_free"] is False and row["cost_usd"] > 0
    assert row["input_tokens"] == 2000 and row["output_tokens"] == 100
    assert api["added"] == [] and api["created"] == []
    assert api["client"].get("/rec/progress/s1").json()["state"] == "stopped"
    import guards
    assert guards.free_used(api["logged"]) == 0 and guards.free_refines_left(False, api["logged"]) == 1
    assert any(a[2]["status"] == "stopped" for a in api["mp"] if len(a) > 2)


def test_stop_before_the_request_arrives_spends_no_model_call(api):
    c = api["client"]
    assert c.post("/rec/cancel/early").json() == {"ok": True}
    d = c.post("/rec/opening", json={"request_id": "early"}).json()
    assert d["stopped"] is True and d["chat_id"] is None and api["run_kw"] == []
    assert api["logged"][-1]["status"] == "stopped" and api["logged"][-1]["cost_usd"] == 0
    assert api["created"] == [] and api["added"] == []


def test_stopped_new_conversation_creates_no_chat(api):
    api["stop_during_run"] = True
    d = api["client"].post("/rec/refine", json={"message": "venues in Ohio", "request_id": "n1"}).json()
    assert d["stopped"] is True and d["chat_id"] is None and api["created"] == [] and api["added"] == []


def test_show_more_stopped_before_it_spends_the_question(api):
    api["history"] = [{"role": "assistant", "text": "picks", "cards": [{"vendor_id": "V1"}], "chips": [],
                       "more_cards": [{"vendor_id": "V%d" % i, "name": "N%d" % i} for i in range(2, 8)]}]
    c = api["client"]
    c.post("/rec/cancel/m1")
    d = c.post("/rec/more", json={"chat_id": 3, "request_id": "m1"}).json()
    assert d["stopped"] is True and d["chat_id"] == 3 and d["cards"] == []
    assert api["logged"][-1]["status"] == "stopped" and api["logged"][-1]["counted_as_free"] is False
    assert api["added"] == []
    d = c.post("/rec/more", json={"chat_id": 3, "request_id": "m2"}).json()       # not stopped: as before
    assert len(d["cards"]) == 3 and api["logged"][-1]["status"] == "ok"
    assert c.get("/rec/progress/m2").json()["state"] == "done"


def test_blocked_request_still_finishes_its_progress_entry(api, monkeypatch):
    import config
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    r = api["client"].post("/rec/refine", json={"chat_id": 3, "message": "x", "request_id": "b1"})
    assert r.status_code == 503
    assert api["client"].get("/rec/progress/b1").json()["state"] == "done"
