"""Whole-wedding proposal ("Plan my wedding", whole_wedding.py, 2026-11-15). No network, no model: Xano is a small
in-memory fake (the same xano.* functions the flow calls), and agent.run raises if anything reaches it.

Mocked priced data (deliberately odd numbers, so a leak is findable in a response):
  venues, most popular first   V2 Grand Hall  rental 9350 + 157 a head -> 33,828 all-in for 120 guests
                               V1 Hackett House      5150 + 103        -> 21,012
                               V3 Rosewood Barn      3250 +  83        -> 15,852
  photographers                P1 from 4550, P2 from 3050;   entertainers  E1 from 7150, E2 from 2550
  florists                     F1 typical total 4150 (from its PDF), F2 a listing with no pricing
  listings only                Beauty B1, caterer C1, no wedding planner
  budget split (table 19)      Venue 20, Food 20, Photography 10, Entertainment 10, Planner 10, Flowers 9, Beauty 2,
                               six other categories 19 -> at $60,000: venue+food share 24,000, "everything else" 11,400
"""
import concurrent.futures
import datetime as dt
import hashlib
import json

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient

import agent
import app
import config
import estimate
import free_mode
import guards
import progress
import wedding
import whole_wedding as ww
import xano

CATS = [{"id": 1, "Category": "Venue", "Budget_Focus_Normal": 20}, {"id": 2, "Category": "Food and Beverage", "Budget_Focus_Normal": 20},
        {"id": 6, "Category": "Flowers", "Budget_Focus_Normal": 9}, {"id": 4, "Category": "Wedding Planner", "Budget_Focus_Normal": 10},
        {"id": 9, "Category": "Photography", "Budget_Focus_Normal": 10}, {"id": 8, "Category": "Entertainment", "Budget_Focus_Normal": 10},
        {"id": 10, "Category": "Attire", "Budget_Focus_Normal": 7}, {"id": 5, "Category": "Rentals", "Budget_Focus_Normal": 5},
        {"id": 11, "Category": "Additional Items", "Budget_Focus_Normal": 3}, {"id": 12, "Category": "Transportation", "Budget_Focus_Normal": 2},
        {"id": 3, "Category": "Beauty", "Budget_Focus_Normal": 2}, {"id": 7, "Category": "Day of Stationery", "Budget_Focus_Normal": 1},
        {"id": 13, "Category": "Invites", "Budget_Focus_Normal": 1}]
VENUES = [{"vendor_id": "V2", "vendor_idx": 2, "name": "Grand Hall", "state": "Texas", "max_capacity_seated": 300, "food_minimum": 0,
           "venue_fee_range": [9350, 9350], "address": "1 Main St, Dallas, TX"},
          {"vendor_id": "V1", "vendor_idx": 1, "name": "Hackett House", "state": "Texas", "max_capacity_seated": 160, "food_minimum": 0,
           "venue_fee_range": [5150, 5150], "address": "2 Oak St, Dallas, TX"},
          {"vendor_id": "V3", "vendor_idx": 3, "name": "Rosewood Barn", "state": "Texas", "max_capacity_seated": 140, "food_minimum": 0,
           "venue_fee_range": [3250, 3250], "address": "3 Elm St, Denton, TX"}]
INPUTS = {"V1": (5150, 103), "V2": (9350, 157), "V3": (3250, 83)}
ALL_IN = {"V1": 21012, "V2": 33828, "V3": 15852}
PE = {"Photography": [{"vendor_id": "P1", "vendor_idx": 4, "name": "Jane Doe Photo", "state": "Texas", "category": "Photography",
                       "from_price": 4550, "price_range": [4550, 8000], "package_types": ["Wedding Day"], "hours_range": [8, 8]},
                      {"vendor_id": "P2", "vendor_idx": 5, "name": "Lone Star Lens", "state": "Texas", "category": "Photography",
                       "from_price": 3050, "price_range": [3050, 5000], "package_types": ["Wedding Day"], "hours_range": [6, 6]}],
      "Entertainment": [{"vendor_id": "E1", "vendor_idx": 6, "name": "Big Band Co", "state": "Texas", "category": "Entertainment",
                         "from_price": 7150, "price_range": [7150, 9000], "package_types": ["Live Band"]},
                        {"vendor_id": "E2", "vendor_idx": 7, "name": "DJ Spin", "state": "Texas", "category": "Entertainment",
                         "from_price": 2550, "price_range": [2550, 2600], "package_types": ["DJ"]}]}
LISTINGS = {"Flowers": [{"vendor_id": "F1", "vendor_idx": 8, "name": "Bloom & Co", "state": "Texas", "category": "Flowers",
                         "florist_pricing": {"tier": "Mid", "typical_total": 4150}},
                        {"vendor_id": "F2", "vendor_idx": 9, "name": "Petal Works", "state": "Texas", "category": "Flowers"}],
            "Beauty": [{"vendor_id": "B1", "vendor_idx": 10, "name": "Glow Studio", "state": "Texas", "category": "Beauty"}],
            "Food and Beverage": [{"vendor_id": "C1", "vendor_idx": 11, "name": "Fork & Flame", "state": "Texas", "category": "Food and Beverage"}],
            "Wedding Planner": []}
SECRET = [21012, 33828, 15852, 5150, 9350, 3250, 4550, 3050, 7150, 2550, 4150]   # every figure that came from a vendor
FULL = {"id": 7001, "forever_access_purchased": True, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
        "Wedding_Budget": 60000, "Wedding_Date": "2027-06-12", "first_name": "A"}
FREE = dict(FULL, id=7002, forever_access_purchased=False)


class SyncPool:
    def submit(self, fn, *a, **k):
        f = concurrent.futures.Future()
        try:
            f.set_result(fn(*a, **k))
        except Exception as e:      # the real pool keeps the exception for .result() too
            f.set_exception(e)
        return f


class World:
    """The fake Xano + what the flow wrote to it."""

    def __init__(self):
        self.usage, self.mp, self.packages, self.calls = [], [], [], []
        self.chats, self.messages, self.memory, self.profile_writes, self.pref_writes, self.notes = {}, {}, {"notes": []}, [], [], []
        self.users = {}
        self.venues, self.pe, self.listings = list(VENUES), {k: list(v) for k, v in PE.items()}, {k: list(v) for k, v in LISTINGS.items()}
        self.near = None            # set to a list to answer venues_near (a couple with a saved area)

    def pricing(self, token, vid, paid, user=None, stats=None):
        self.calls.append(("pricing", vid, paid))
        rental, pp = INPUTS[vid]
        return {"vendor_id": vid, "guest_minimum": 50, "verdict": "", "access_reason": "forever" if paid else None,
                "lines": [{"line": "Rental fee", "source": "venue", "vs_market": "typical", "amount": rental if paid else None}],
                "cost_inputs": ({"space": "Main", "guest_floor": 50, "rental": rental, "ceremony": None, "pp_fb": pp,
                                 "fb_min": None, "fb_min_per_person": False, "service_pct": 20, "tax_pct": None,
                                 "estimates": {}} if paid else None)}

    def add_message(self, chat_id, role, content, cards=None, chips=None):
        rows = self.messages.setdefault(chat_id, [])
        rows.append({"id": len(rows) + 1, "role": role, "content": content, "cards": cards or [], "chips": chips or []})

    def package_add(self, row):
        self.packages.append(dict(row, id=100 + len(self.packages), created_at=1000 + len(self.packages)))


@pytest.fixture
def w(monkeypatch):
    wd = World()
    monkeypatch.setattr(config, "WHOLE_WEDDING", True)
    monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", 100)
    monkeypatch.setattr(config, "WHOLE_WEDDING_USER_IDS", frozenset())
    monkeypatch.setattr(config, "WHOLE_WEDDING_STEP_PCT", 15)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FREE_DAILY_MODE", True)
    monkeypatch.setattr(config, "FREE_PRICING", "light")
    monkeypatch.setattr(config, "FREE_INITIAL_REFINES", 3)
    monkeypatch.setattr(config, "GLOBAL_DAILY_USD", 40.0)
    monkeypatch.setattr(config, "LIGHT_PRICE_BANDS", True)
    monkeypatch.setattr(wedding, "ENABLED", True)
    monkeypatch.setattr(app, "_POOL", SyncPool())
    monkeypatch.setattr(app, "_submit_usage", lambda row: xano.log_usage(dict(row)))
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    monkeypatch.setattr(agent, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model must not be called")))
    app._WW_LAST.clear()
    app._WW_PLANS.clear()
    ww._PDFS.clear()
    m = monkeypatch.setattr
    # the vendor page's PDF list (ep93): every mocked vendor has one pricing PDF unless a test says otherwise (wd.pdfs)
    wd.pdfs = {}
    m(xano, "pdf_rows", lambda tok, vid: wd.calls.append(("pdf_rows", vid)) or [
        {"id": i + 1, "PDF_Link": "https://x/%s-%d.pdf" % (vid, i)} for i in range(wd.pdfs.get(vid, 1))])
    m(xano, "user_usage", lambda uid: [r for r in wd.usage if r.get("user_id") == uid])
    m(xano, "spend_today", lambda: (0.0, 0.0))
    m(xano, "log_usage", lambda row: wd.usage.append(dict(row, usage_day=xano.today())))
    m(xano, "mp_track", lambda uid, ev, props: wd.mp.append((ev, dict(props))))
    m(xano, "budget_categories", lambda tok: wd.calls.append(("split",)) or CATS)

    def search_venues(token, *, states, page_size=8, search_log=None, **f):
        wd.calls.append(("search_venues", dict(f, states=states)))
        g = f.get("guests") or 0
        vt = f.get("venue_types")
        out = [v for v in wd.venues if (v["max_capacity_seated"] or 0) >= g]
        if vt:
            out = [v for v in out if set(vt) & set(v.get("types") or [])]
        return {"total_matches": len(out), "venues": out}
    m(xano, "search_venues", search_venues)
    m(xano, "geocode_city", lambda city, states=(): wd.calls.append(("geocode", city)) or ((32.7, -96.8, "Dallas, Texas") if wd.near is not None else None))
    m(xano, "venues_near", lambda lat, lng, radius, **f: wd.calls.append(("venues_near", f)) or {"venues": [dict(v, distance_miles=4.2) for v in wd.near]})
    m(xano, "cached_venue_pricing", wd.pricing)

    def search_pe(token, category, *, states, **k):
        wd.calls.append(("search_pe", category, k.get("types")))
        return {"total_matches": len(wd.pe[category]), "vendors": list(wd.pe[category])}
    m(xano, "search_pe", search_pe)

    def search_listings(token, category, *, states, **k):
        wd.calls.append(("search_listings", category))
        return {"total_matches": len(wd.listings[category]), "vendors": list(wd.listings[category])}
    m(xano, "search_listings", search_listings)
    m(xano, "user_packages", lambda tok, uid: [dict(p) for p in wd.packages if p.get("user_id") == uid])
    m(xano, "package_add", wd.package_add)
    m(xano, "get_memory", lambda uid: wd.memory)
    m(xano, "add_memory_note", lambda uid, note, cap=20: wd.notes.append(note))

    def merge_preferences(uid, answers, src="chat"):
        wd.pref_writes.append(dict(answers))
        new = [dict(n, src=src) for n in app.prefs.to_notes(answers)]
        wd.memory["notes"] = new + [n for n in wd.memory["notes"] if n.get("key") not in {x["key"] for x in new}]
        return new
    m(xano, "merge_preferences", merge_preferences)

    def update_profile(token, fields):
        wd.profile_writes.append(dict(fields))
        wd.users[token].update(fields)
        return {}
    m(xano, "update_profile", update_profile)
    m(xano, "verify_user", lambda token: dict(wd.users[token]) if token in wd.users else None)
    m(xano, "plan_label", lambda u: "Forever" if u.get("forever_access_purchased") else "free")

    def create_chat(uid, title):
        cid = 500 + len(wd.chats)
        wd.chats[cid] = {"id": cid, "user_id": uid, "title": title}
        return wd.chats[cid]
    m(xano, "create_chat", create_chat)
    m(xano, "get_chat", lambda uid, cid: wd.chats.get(cid) if (wd.chats.get(cid) or {}).get("user_id") == uid else None)
    m(xano, "get_messages", lambda cid: [xano.parse_message_row(r) for r in wd.messages.get(cid, [])])
    m(xano, "add_message", wd.add_message)
    m(xano, "touch_chat", lambda chat: None)
    return wd


def ask(wd, user, message="", ww_body=None, chat_id=0, token=None, tracker=None, omit_ww=False):
    """One POST /rec/refine through app._refine, background tasks run (the chat turns get saved)."""
    token = token or "tok%d" % user["id"]
    wd.users.setdefault(token, dict(user))
    body = app.RefineBody(message=message, chat_id=chat_id, ww=None if omit_ww else dict({"v": 1}, **(ww_body or {})))
    bg = BackgroundTasks()
    out = app._refine(token, dict(wd.users[token]), body, bg, tracker)
    for t in bg.tasks:
        t.func(*t.args, **t.kwargs)
    return out


def proposal_of(out):
    return next(x for x in out["widgets"] if x["type"] == ww.PROPOSAL)


def rows_of(out):
    return {r["slot"]: r for r in proposal_of(out)["rows"]}


def leaks(obj):
    """The vendor figures (SECRET) that appear anywhere in a response, with or without thousands separators."""
    blob = json.dumps(obj)
    return [n for n in SECRET if str(n) in blob or format(n, ",") in blob]


# ---------------------------------------------------------------- 1. flag off = today's Planner

def test_flag_off_nobody_is_assigned_and_the_flow_never_runs(monkeypatch, w):
    monkeypatch.setattr(config, "WHOLE_WEDDING", False)
    monkeypatch.setattr(config, "WHOLE_WEDDING_USER_IDS", frozenset({7001}))     # even the override list is ignored
    assert ww.arm(7001) == "off" and not ww.assigned(7001) and app._ww_props(FULL) == {}
    monkeypatch.setattr(app, "_ww_refine", lambda *a, **k: pytest.fail("flow ran with the flag off"))
    seen = {}
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: None)
    monkeypatch.setattr(app, "_run_guarded", lambda kind, token, user, msgs, **k: seen.update(kind=kind, msgs=msgs) or {
        "text": "normal answer", "cards": [], "chips": ["a", "b"], "widgets": [], "notes_saved": []})
    out = ask(w, FULL, "plan my wedding", {"action": "start"})
    assert out["text"] == "normal answer" and seen["kind"] == "refine" and seen["msgs"][-1]["content"] == "plan my wedding"
    assert "wedding_status" not in out and "ww" not in out                      # no new key on a normal answer
    assert not [r for r in w.usage if r.get("kind") == ww.KIND] and not w.packages


def test_flag_off_status_and_endpoints(monkeypatch, w):
    monkeypatch.setattr(config, "WHOLE_WEDDING", False)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(FULL)))
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    c = TestClient(app.app)
    s = c.get("/rec/status").json()
    assert s["whole_wedding"] is False and s["ww_arm"] == "off"
    assert c.post("/rec/ww/add", json={"chat_id": 1, "all": True}).status_code == 404
    assert c.get("/rec/ww/status").json() == {"whole_wedding": False, "wedding_status": None, "rows_added": {}}
    assert not w.packages


def test_a_page_that_does_not_send_ww_never_gets_the_flow(monkeypatch, w):
    """Flag on and assigned, but an older component (no `ww` in the body) can't draw the cards: normal answer."""
    monkeypatch.setattr(app, "_ww_refine", lambda *a, **k: pytest.fail("flow ran for a page that can't draw it"))
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: None)
    monkeypatch.setattr(app, "_run_guarded", lambda *a, **k: {"text": "normal", "cards": [], "chips": [], "widgets": []})
    assert ask(w, FULL, "plan my wedding", omit_ww=True)["text"] == "normal"


def test_an_ordinary_question_falls_through_to_the_normal_answer(monkeypatch, w):
    monkeypatch.setattr(app, "_lookup_fast", lambda *a, **k: None)
    monkeypatch.setattr(app, "_run_guarded", lambda *a, **k: {"text": "normal", "cards": [], "chips": [], "widgets": []})
    assert ask(w, FULL, "what about parking at venues in Texas?")["text"] == "normal"
    assert ask(w, FULL, "make it cheaper", {"active": True})["text"] == "normal"      # no proposal in this chat to react to


# ---------------------------------------------------------------- 2. assignment

def test_assignment_is_the_documented_hash_stable_and_respects_pct_and_override(monkeypatch):
    monkeypatch.setattr(config, "WHOLE_WEDDING", True)
    monkeypatch.setattr(config, "WHOLE_WEDDING_USER_IDS", frozenset({42}))
    for pct in (0, 1, 37, 50, 99, 100):
        monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", pct)
        for uid in list(range(1, 400)) + [14998, 35757]:
            want = int(hashlib.sha256(("wholewedding:" + str(uid)).encode()).hexdigest(), 16) % 100 < pct
            if uid == 42:
                assert ww.arm(uid) == "forced" and ww.assigned(uid)
            else:
                assert ww.assigned(uid) is want and ww.arm(uid) == ("ww" if want else "control")
            assert ww.arm(uid) == ww.arm(str(uid)) == ww.arm(uid)                 # stable, and "12" == 12
    monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", 50)
    share = sum(ww.assigned(u) for u in range(1000, 3000)) / 2000.0
    assert 0.44 < share < 0.56
    monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", 0)
    assert not any(ww.assigned(u) for u in range(1, 300) if u != 42) and ww.arm(0) == "control" and ww.arm(None) == "control"
    monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", 100)
    assert all(ww.assigned(u) for u in range(1, 300))


def test_status_says_who_is_assigned(monkeypatch, w):
    monkeypatch.setattr(config, "WHOLE_WEDDING_PCT", 0)
    monkeypatch.setattr(config, "WHOLE_WEDDING_USER_IDS", frozenset({7001}))
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    c = TestClient(app.app)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(FULL)))
    s = c.get("/rec/status").json()
    assert s["whole_wedding"] is True and s["ww_arm"] == "forced"
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(FREE)))
    s = c.get("/rec/status").json()
    assert s["whole_wedding"] is False and s["ww_arm"] == "control"


# ---------------------------------------------------------------- 3. what the couple typed

def test_detect_start_and_reactions():
    yes = ["plan my wedding", "Plan our whole wedding for 120 guests in Texas, $60k", "build me a wedding", "can you plan my wedding?",
           "Build my wedding", "put together our wedding"]
    no = ["plan my wedding budget", "find me a wedding planner", "make our wedding cheaper", "what does a wedding cost",
          "plan my wedding photographer", "", "x" * 300 + " plan my wedding"]
    assert all(ww.detect_start(t) for t in yes) and not any(ww.detect_start(t) for t in no)
    assert ww.typed_context("Plan our whole wedding for 120 guests in West Virginia, $60k") == {
        "guests": 120, "budget": 60000, "state": "West Virginia"}
    r = ww.reaction
    assert r("we already booked our venue: Hackett House for $12,000") == {"action": "booked", "category": "Venue", "name": "Hackett House", "price": 12000}
    assert r("We booked Hackett House as our venue for $12k")["name"] == "Hackett House"
    assert r("our photographer is booked - Jane Doe, $4,500") == {"action": "booked", "category": "Photography", "name": "Jane Doe", "price": 4500}
    assert r("we booked our venue") == {"action": "booked", "category": "Venue", "name": "", "price": 0}
    assert r("too expensive") == r("Make it cheaper") == {"action": "cheaper"} and r("Make it nicer") == {"action": "nicer"}
    assert r("we want a barn") == {"action": "style", "style": "Barn / Ranch"}
    assert r("swap the photographer") == {"action": "swap", "category": "Photography"}
    assert r("we don't like the florist") == {"action": "swap", "category": "Flowers"}
    assert r("our budget is actually 50k") == {"action": "budget", "budget": 50000}
    assert r("what about parking?") is None and r("") is None


# ---------------------------------------------------------------- 4. context first

def test_context_asks_only_for_what_is_missing():
    thin = {"id": 1, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120}
    ctx = ww.context_of(thin, {})
    assert ww.missing(ctx) == ["budget", "area", "date"] and ww.blocked_by(ww.missing(ctx)) == ["budget"]
    card = ww.context_card(ctx, ww.missing(ctx))
    assert [f["id"] for f in card["fields"]] == ["budget", "area", "date"] and card["type"] == ww.CONTEXT
    assert [o["value"] for o in card["fields"][1]["options"]][:2] == ["Dallas, Texas", "Austin, Texas"] and "areas" not in card
    assert card["have"] == ["Texas", "120 guests"]
    full = ww.context_of(FULL, {"area": ["Dallas, Texas"]})
    assert ww.missing(full) == [] and full["city"] == "Dallas" and full["date"] == "Jun 2027"
    assert ww.missing(ww.context_of(FULL, {})) == ["area", "date"][:1]            # only the optional area
    assert ww.missing(ww.context_of(FULL, {}), skip=("area",)) == []
    empty = ww.context_of({"id": 2}, {})
    assert ww.missing(empty) == ["state", "guests", "budget", "area", "date"]
    assert set(ww.context_card(empty, ww.missing(empty))["areas"]) >= {"Texas", "New York"}   # state picked on the card
    assert ww.context_of(FULL, {"area": ["Austin, Texas"], "guests": ["150"]})["guests"] == 120   # the profile wins
    assert ww.context_of(dict(FULL, Wedding_Location_Updated=["Ohio"]), {"area": ["Dallas, Texas"]})["area"] == ""


def test_context_card_is_not_a_question_and_answers_are_saved_like_the_profile_edit(w):
    thin = {"id": 7003, "forever_access_purchased": False, "Wedding_Location_Updated": ["Texas"]}
    out = ask(w, thin, "Plan my wedding", {"action": "start"})
    card = out["widgets"][0]
    assert card["type"] == ww.CONTEXT and [f["id"] for f in card["fields"]] == ["guests", "budget", "area", "date"]
    assert out["ww"] == dict(out["ww"], step="context", counted=False, model_calls=0) and out["free_refines_left"] == 3
    assert w.usage == [] and not [c for c in w.calls if c[0] != "split"]          # nothing counted, nothing searched
    assert [e for e, p in w.mp if p.get("kind") == "ww_context"] and w.mp[-1][1]["ww_arm"] == "ww"
    # the card's answers: profile columns through ep21 (the quick profile edit's own validation), the area as a pref
    out = ask(w, thin, "120 guests, $60,000, near Dallas", {"action": "context", "answers": {
        "guests": 120, "budget": "60k", "area": "Dallas, Texas", "date": "2027-06"}}, chat_id=out["chat_id"])
    assert w.profile_writes == [{"Wedding_Guest_Count": 120, "Wedding_Budget": 60000, "Wedding_Date": "2027-06-01"}]
    assert w.pref_writes == [{"area": ["Dallas, Texas"]}] and "Wants to stay near: Dallas, Texas" in out["notes_saved"]
    assert proposal_of(out)["guests"] == 120 and proposal_of(out)["budget"] == 60000 and proposal_of(out)["when"] == "Jun 2027"
    # ONE counted question in total: the proposal. The card and its answers were free.
    assert [(r["kind"], r["counted_as_free"]) for r in w.usage] == [(ww.KIND, True)] and out["free_refines_left"] == 2
    assert guards.free_used(w.usage) == 1


def test_bad_answers_are_refused_and_nothing_is_written(w):
    thin = {"id": 7004, "Wedding_Location_Updated": ["Texas"]}
    with pytest.raises(HTTPException) as e:
        ask(w, thin, "x", {"action": "context", "answers": {"guests": 99999, "budget": 5, "area": "Atlantis, Texas"}})
    assert e.value.status_code == 400 and e.value.detail["fields"] == ["area", "budget", "guests"]
    assert not w.profile_writes and not w.pref_writes and not w.usage


def test_typed_details_are_used_for_this_plan_only_and_never_written(w):
    out = ask(w, {"id": 7005, "forever_access_purchased": True}, "Plan our whole wedding for 120 guests in Texas, $60k")
    p = proposal_of(out)
    assert (p["guests"], p["budget"], p["states"]) == (120, 60000, ["Texas"]) and not w.profile_writes and not w.pref_writes


# ---------------------------------------------------------------- 5. the proposal: full members

def test_full_proposal_structure_amounts_and_labels(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    p, rows = proposal_of(out), rows_of(out)
    assert [r["slot"] for r in p["rows"]] == ww.SLOT_KEYS and p["detail"] == "full" and p["target"] == 60000
    v = rows["Venue"]
    # the venue page's own all-in (estimate.compute), not a new calculation; V2 is over the venue+food share, V1 fits
    e = estimate.compute(w.pricing("t", "V1", True)["cost_inputs"], 120)
    assert v["name"] == "Hackett House" and v["amount"] == e["total_before_tax_and_gratuity"] == 21012
    assert v["basis"] == "real" and v["badge"] == ww.REAL and "rental" in v["based_on"] and "food and drink" in v["based_on"]
    assert "Estimated by Tulle: service" not in v["based_on"] and "all-in for 120 guests before tax and tip" in v["why"]
    assert (rows["Photography"]["name"], rows["Photography"]["amount"], rows["Photography"]["basis"]) == ("Jane Doe Photo", 4550, "real")
    assert (rows["Entertainment"]["name"], rows["Entertainment"]["amount"]) == ("DJ Spin", 2550)     # the band is over the share
    # a florist's "typical total" is Tulle's working from their sheet, not a price on it: an estimate (fix 3, 11-16)
    assert (rows["Flowers"]["name"], rows["Flowers"]["amount"], rows["Flowers"]["basis"]) == ("Bloom & Co", 4150, "estimate")
    fb = rows["Food and Beverage"]                                                # inside the venue's all-in: never counted twice
    assert fb["basis"] == "included" and fb["amount"] is None and "$12,360" in fb["why"] and fb["badge"] == ww.REAL
    pl, be = rows["Wedding Planner"], rows["Beauty"]
    assert (pl["basis"], pl["amount"], pl["name"], pl["badge"]) == ("estimate", 6000, "", ww.ESTIMATE)
    assert (be["basis"], be["amount"], be["name"]) == ("estimate", 1200, "Glow Studio")
    assert "typical budget split: 10% of a $60,000 budget" in pl["based_on"] and "Not a quote" in be["based_on"]
    assert p["other"]["amount"] == 11400 and p["other"]["basis"] == "estimate" and "Attire" in p["other"]["why"]
    assert p["total"] == 21012 + 4550 + 2550 + 4150 + 6000 + 1200 + 11400 == 50862
    assert p["over_under"] == -9138 and p["verdict"] == "under" and "$9,138 under your $60,000 budget" in p["total_note"]
    assert [a["action"] for a in p["alternates"]] == ["cheaper", "nicer"]
    # every line says where its number comes from
    for r in p["rows"]:
        assert r["badge"] in (ww.REAL, ww.ESTIMATE) and r["based_on"], r
    assert "$50,862" in out["text"] and out["text"].count(".") <= 5 and out["cards"] == [] and out["chips"] == []
    assert out["ww"]["model_calls"] == 0 and out["ww"]["counted"] is True


def test_lookups_per_proposal_and_timings(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    kinds = [c[0] for c in w.calls]
    # 1 split + 1 venue search + 3 venue pricing + 2 P/E + 4 listing searches + 1 PDF-list check (the one estimate row
    # that names a vendor: Glow Studio) = 12 lookups, 0 model calls
    assert sorted(kinds) == sorted(["split", "search_venues"] + ["pricing"] * 3 + ["search_pe"] * 2 + ["search_listings"] * 4
                                   + ["pdf_rows"])
    assert ("pdf_rows", "B1") in w.calls
    assert out["ww"]["tool_calls"] == 12 and w.usage[0]["tool_calls"] == 12 and w.usage[0]["model"] == "whole_wedding"
    assert w.usage[0]["chat_id"] == out["chat_id"] and out["chat_id"] > 0     # the conversation it was delivered in, not 0
    assert ("search_pe", "Photography", ["Wedding Day"]) in w.calls
    ev = [p for e, p in w.mp if p.get("kind") == ww.KIND][0]
    assert ev["ww_arm"] == "ww" and ev["model_calls"] == 0 and ev["tool_calls"] == 12 and ev["counted"] is True
    assert {"ww_split_ms", "ww_venue_search_ms", "ww_venue_pricing_ms", "ww_lookups_ms", "ww_total_ms"} <= set(ev)
    assert w.usage[0]["tools"] == "ww:start,arm:ww" and w.usage[0]["ww_arm"] == "ww" and w.usage[0]["cost_usd"] == 0
    assert w.usage[0]["categories"] == "venue,photography,food and beverage,entertainment,flowers,wedding planner,beauty"
    assert "search_engine" not in w.usage[0]              # kept out of the Railway-search comparison


def test_no_venue_fits_says_so_and_shows_the_closest_with_what_breaks(w):
    out = ask(w, dict(FULL, Wedding_Budget=30000), "Plan my wedding", {"action": "start", "skip": ["area"]})
    v = rows_of(out)["Venue"]                                # venue+food share of $30,000 = $12,000; the cheapest is $15,852
    assert v["name"] == "Rosewood Barn" and v["amount"] == 15852
    assert v["breaks"] == "Over your venue and food share ($12,000) by $3,852"
    assert "is the closest" in out["text"] and "over your venue and food share" in out["text"]
    out = ask(w, dict(FULL, id=7010, Wedding_Guest_Count=250), "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert rows_of(out)["Venue"]["name"] == "Grand Hall"     # the only one that seats 250
    w.venues[:] = [v for v in w.venues if v["vendor_id"] != "V2"]
    out = ask(w, dict(FULL, id=7011, Wedding_Guest_Count=250), "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert "Seats 160 and you have 250 guests" in rows_of(out)["Venue"]["breaks"]
    w.venues[:] = []
    out = ask(w, dict(FULL, id=7012), "Plan my wedding", {"action": "start", "skip": ["area"]})
    v = rows_of(out)["Venue"]
    assert v["name"] == "" and v["basis"] == "none" and "could not find a venue there" in out["text"] and v["badge"] == "No venue found"
    assert rows_of(out)["Food and Beverage"]["basis"] == "estimate" and rows_of(out)["Food and Beverage"]["name"] == "Fork & Flame"


def test_a_saved_area_searches_near_it(w):
    w.memory["notes"] = [{"kind": "pref", "key": "area", "values": ["Dallas, Texas"], "note": "Wants to stay near: Near Dallas"}]
    w.near = [VENUES[1]]
    out = ask(w, FULL, "Plan my wedding", {"action": "start"})
    assert ("geocode", "Dallas") in w.calls and proposal_of(out)["where"] == "near Dallas, Texas"
    assert "4.2 miles from Dallas" in rows_of(out)["Venue"]["why"]


def test_a_venue_with_no_all_in_is_an_estimate_not_a_price(w, monkeypatch):
    monkeypatch.setattr(xano, "cached_venue_pricing", lambda *a, **k: {"vendor_id": a[1], "guest_minimum": 0, "lines": [], "cost_inputs": None})
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    v, fb = rows_of(out)["Venue"], rows_of(out)["Food and Beverage"]
    assert (v["name"], v["basis"], v["badge"], v["amount"]) == ("Grand Hall", "estimate", ww.ESTIMATE, 12000)
    assert "Not a quote" in v["based_on"] and (fb["basis"], fb["amount"], fb["name"]) == ("estimate", 12000, "Fork & Flame")


# ---------------------------------------------------------------- 6. free and light: the shape, never the figures

@pytest.mark.parametrize("pricing,detail", [("light", "light"), ("none", "free")])
def test_no_vendor_figure_reaches_a_couple_without_a_plan(w, monkeypatch, pricing, detail):
    monkeypatch.setattr(config, "FREE_PRICING", pricing)
    out = ask(w, FREE, "Plan my wedding", {"action": "start", "skip": ["area"]})
    p, rows = proposal_of(out), rows_of(out)
    assert leaks(out) == [] and p["detail"] == detail and out["price_locked"] is True
    assert not any(c[0] == "pricing" and c[2] for c in w.calls)                # the paid pricing endpoint is never asked
    for s in ("Venue", "Photography", "Entertainment", "Flowers"):
        r = rows[s]
        # only a package price is certainly "real"; a locked venue all-in / florist total may be part estimate (fix 3)
        badge = ww.REAL_LOCKED if s in ("Photography", "Entertainment") else ww.PRICED_LOCKED
        assert r["name"] and r["amount"] is None and r["locked"] is True and r["badge"] == badge, r
    assert rows["Venue"]["name"] == "Grand Hall" and rows["Photography"]["name"] == "Jane Doe Photo"
    assert (rows["Venue"]["band"] == "rental about $9K-$10K") if detail == "light" else (rows["Venue"]["band"] == "")
    # estimates are a share of the couple's own budget: shown to everyone, labelled
    assert (rows["Beauty"]["amount"], rows["Beauty"]["badge"]) == (1200, ww.ESTIMATE)
    assert (rows["Food and Beverage"]["basis"], rows["Food and Beverage"]["amount"]) == ("estimate", 12000)
    assert p["total"] is None and p["over_under"] is None and p["total_locked"] is True
    assert "$" not in out["text"] and not free_mode._FIGURE.search(out["text"])
    stored = xano.get_messages(out["chat_id"])[-1]
    assert leaks(stored) == []                                                 # nor into the saved chat
    # adding a locked row copies no price out
    monkeypatch.setattr(app, "_auth", lambda a: ("tok7002", dict(FREE)))
    d = TestClient(app.app).post("/rec/ww/add", json={"chat_id": out["chat_id"], "all": True}).json()
    assert leaks(d) == [] and leaks(w.packages) == []
    assert {p_["vendor_name"]: p_["total_cost"] for p_ in w.packages}["Grand Hall"] == 0


def test_cheaper_or_nicer_without_full_figures_is_the_upgrade_card_and_costs_nothing(w):
    out = ask(w, FREE, "Plan my wedding", {"action": "start", "skip": ["area"]})
    n_calls, cid = len(w.calls), out["chat_id"]
    out = ask(w, FREE, "Make it cheaper", {"action": "cheaper"}, chat_id=cid)
    assert out["upsell_prompt"]["reason"] == "price_question" and out["widgets"] == [] and out["ww"]["step"] == "upsell"
    assert len(w.calls) == n_calls and len(w.usage) == 1 and out["free_refines_left"] == 2 and leaks(out) == []
    out = ask(w, FREE, "too expensive", {"active": True}, chat_id=cid)         # the same, typed
    assert out["ww"]["step"] == "upsell" and len(w.usage) == 1


def test_a_stored_full_proposal_is_relocked_for_a_lapsed_member(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    turns = free_mode.sanitize_turns(xano.get_messages(out["chat_id"]))
    p = turns[-1]["widgets"][0]
    assert leaks(turns) == [] and p["total"] is None and p["detail"] == "free"
    by = {r["slot"]: r for r in p["rows"]}
    assert by["Venue"]["locked"] and by["Venue"]["amount"] is None and by["Food and Beverage"]["amount"] is None
    assert by["Beauty"]["amount"] == 1200 and "12,360" not in json.dumps(p)


# ---------------------------------------------------------------- 7. reactions

def test_cheaper_then_nicer_rerun_with_an_adjusted_target_and_say_what_changed(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    cid = out["chat_id"]
    out = ask(w, FULL, "Make it cheaper", {"action": "cheaper"}, chat_id=cid)
    p, rows = proposal_of(out), rows_of(out)
    assert p["target"] == 51000 and p["budget"] == 60000 and p["mode"] == "cheaper"
    assert (rows["Venue"]["name"], rows["Venue"]["amount"]) == ("Rosewood Barn", 15852)
    assert (rows["Photography"]["name"], rows["Photography"]["amount"]) == ("Lone Star Lens", 3050)
    assert rows["Entertainment"]["name"] == "DJ Spin" and rows["Flowers"]["name"] == "Bloom & Co"      # nothing cheaper: kept
    assert rows["Wedding Planner"]["amount"] == 5100 and rows["Beauty"]["amount"] == 1020 and p["other"]["amount"] == 9690
    assert p["total"] == 15852 + 3050 + 2550 + 4150 + 5100 + 1020 + 9690 == 41412
    ch = " | ".join(p["changes"])
    assert "Aimed 15% lower: a $51,000 target instead of $60,000" in ch
    assert "Venue: Rosewood Barn ($15,852) instead of Hackett House ($21,012)" in ch
    assert "Music: I found nothing cheaper here, so I kept DJ Spin" in ch
    assert out["text"].startswith("Made it cheaper.") and "$41,412" in out["text"] and out["ww"]["counted"] is True
    out = ask(w, FULL, "something nicer please", {"active": True}, chat_id=cid)       # typed, reacts to the cheaper plan
    p, rows = proposal_of(out), rows_of(out)
    assert p["target"] == 58700 and rows["Venue"]["name"] == "Hackett House" and rows["Photography"]["name"] == "Jane Doe Photo"
    assert [r["kind"] for r in w.usage] == [ww.KIND] * 3 and "Asked for a cheaper version of their whole-wedding plan" in w.notes


def test_swap_changes_one_category_and_keeps_the_rest(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    before, cid = rows_of(out), out["chat_id"]
    w.calls.clear()
    out = ask(w, FULL, "swap the photographer", {"active": True}, chat_id=cid)
    after = rows_of(out)
    assert after["Photography"]["name"] == "Lone Star Lens" and after["Photography"]["changed"] is True
    for s in ww.SLOT_KEYS:
        if s != "Photography":
            assert {k: v for k, v in after[s].items() if k != "changed"} == {k: v for k, v in before[s].items() if k != "changed"}
    assert [c[0] for c in w.calls] == ["split", "search_pe"]                    # only that category was looked up
    assert proposal_of(out)["exclude"] == {"Photography": ["P1"]} and "Passed on Jane Doe Photo for their photographer" in w.notes
    out = ask(w, FULL, "Swap this one", {"action": "swap", "category": "Photography"}, chat_id=cid)
    assert rows_of(out)["Photography"]["name"] == "Lone Star Lens" and out["ww"]["counted"] is False     # nothing left to swap in
    assert "I found no other match here, so I kept Lone Star Lens" in out["text"] and len(w.usage) == 2  # ...and not charged
    out = ask(w, FULL, "Swap this one", {"action": "swap", "category": "Food and Beverage"}, chat_id=cid)
    assert "swap the venue to change it" in out["text"] and rows_of(out)["Food and Beverage"]["basis"] == "included"
    out = ask(w, FULL, "a different venue please", {"active": True}, chat_id=cid)
    r = rows_of(out)
    assert r["Venue"]["name"] == "Rosewood Barn" and "$9,960" in r["Food and Beverage"]["why"]     # the food line follows the venue


def test_style_reaction_saves_the_preference_and_only_moves_the_venue(w):
    w.venues[2]["types"] = ["Barn / Ranch"]
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    out = ask(w, FULL, "we want a barn", {"active": True}, chat_id=out["chat_id"])
    assert rows_of(out)["Venue"]["name"] == "Rosewood Barn" and w.pref_writes == [{"style": ["Barn / Ranch"]}]
    assert "Style: Barn / Ranch" in out["notes_saved"] and rows_of(out)["Photography"]["name"] == "Jane Doe Photo"
    assert ("search_venues", {"guests": 120, "venue_types": ["Barn / Ranch"], "states": ["Texas"]}) in w.calls


def test_budget_reaction_replans_to_the_new_number(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    out = ask(w, FULL, "our budget is actually 40k", {"active": True}, chat_id=out["chat_id"])
    p = proposal_of(out)
    assert p["budget"] == 40000 and p["target"] == 40000 and rows_of(out)["Venue"]["name"] == "Rosewood Barn"
    assert "Planned to $40,000 instead of $60,000" in p["changes"][0] and "your $40,000 budget" in p["total_note"]


# ---------------------------------------------------------------- 8. My wedding: the store the screen reads

def view_of(w, user):
    return wedding.view(user, xano.user_packages("t", user["id"]), [], show_prices=xano.has_paid_access(user))


def test_adds_land_in_table_33_as_considering_and_the_strip_matches_the_screen(w, monkeypatch):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert out["wedding_status"]["label"] == "My wedding: nothing added yet" and out["wedding_status"]["started"] == 0
    monkeypatch.setattr(app, "_auth", lambda a: ("tok7001", dict(FULL)))
    c = TestClient(app.app)
    d = c.post("/rec/ww/add", json={"chat_id": out["chat_id"], "slots": ["Venue"]}).json()
    assert d["added"] == ["Venue"] and d["rows_added"]["Venue"] is True and d["rows_added"]["Photography"] is False
    row = w.packages[0]
    assert row == dict(row, user_id=7001, vendor_name="Hackett House", total_cost=21012, Balance_Paid=0, Balance_Left=21012,
                       Category="Venue", Selected=False, Notes="", vendor_id="V1",
                       Package_Name="Tulle Planner proposal · real price from Tulle data · tulle:V1 · " + ww._stamp("Hackett House", 21012))
    v = view_of(w, FULL)                                  # the My wedding screen's own function
    venue = v["categories"][0]
    assert venue["key"] == "Venue" and venue["booked"] is None and v["booked_total"] == 0     # considering, NOT booked
    assert [(q["name"], q["price"], q["vendor_id"]) for q in venue["quotes"]] == [("Hackett House", 21012, "V1")]
    assert venue["quotes"][0]["package"] == "Tulle Planner proposal · real price from Tulle data"
    s = d["wedding_status"]
    assert s["label"] == "In My wedding so far: 1 of 7 categories · $21,012 of your $60,000 budget"
    assert s["changed"] == ["Added: Venue - Hackett House"] and s["action"] == "Open my wedding"
    assert (s["booked_total"], s["budget"]) == (v["booked_total"], v["budget"]) and s == dict(ww.strip(v), changed=s["changed"])
    # add all: every other row with a pick or an estimate; never the same vendor twice
    d = c.post("/rec/ww/add", json={"chat_id": out["chat_id"], "all": True}).json()
    assert d["added"] == ["Photography", "Entertainment", "Flowers", "Wedding Planner", "Beauty"]     # catering is inside the venue
    assert c.post("/rec/ww/add", json={"chat_id": out["chat_id"], "all": True}).json()["added"] == []
    v = view_of(w, FULL)
    assert len(w.packages) == 6 and all(p["Selected"] is False for p in w.packages) and v["booked_total"] == 0
    by = {p["Category"]: p for p in w.packages}
    assert by["Beauty"]["vendor_name"] == "Glow Studio" and by["Beauty"]["total_cost"] == 1200
    assert by["Beauty"]["Package_Name"] == "Tulle Planner proposal · estimate, not a quote · tulle:B1 · " + ww._stamp("Glow Studio", 1200)
    assert by["Flowers"]["Package_Name"].startswith("Tulle Planner proposal · estimate, not a quote · tulle:F1")
    assert by["Wedding Planner"]["vendor_name"] == "Planner (not chosen yet)" and "tulle:manual" in by["Wedding Planner"]["Package_Name"]
    assert [q["name"] for q in v["considering"]] and {q["category"] for q in v["considering"]} == {"Wedding Planner", "Beauty"}
    s = d["wedding_status"]
    assert s["started"] == 6 and s["planned"] == 21012 + 4550 + 2550 + 4150 + 6000 + 1200
    assert s == dict(ww.strip(view_of(w, FULL)), changed=s["changed"])
    # the chat's own status call (a reopened chat) says the same thing
    st = c.get("/rec/ww/status", params={"chat_id": out["chat_id"]}).json()
    assert st["wedding_status"] == ww.strip(view_of(w, FULL)) and st["rows_added"]["Flowers"] is True
    assert st["rows_added"]["Food and Beverage"] is False
    # no usage row for an add, and the next proposal shows what is already in My wedding
    assert [r["kind"] for r in w.usage] == [ww.KIND] and [p for e, p in w.mp if p.get("kind") == "ww_add"][0]["ww_arm"] == "ww"
    out2 = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]}, chat_id=out["chat_id"])
    assert rows_of(out2)["Venue"]["added"] is True and out2["wedding_status"]["started"] == 6


def test_add_needs_a_proposal_and_respects_the_kill_switch(w, monkeypatch):
    monkeypatch.setattr(app, "_auth", lambda a: ("tok7001", dict(FULL)))
    c = TestClient(app.app)
    w.chats[900] = {"id": 900, "user_id": 7001, "title": "x"}
    assert c.post("/rec/ww/add", json={"chat_id": 900, "all": True}).status_code == 409
    assert c.post("/rec/ww/add", json={"chat_id": 12345, "all": True}).status_code == 404      # not their chat
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    assert c.post("/rec/ww/add", json={"chat_id": 900, "all": True}).status_code == 503
    assert not w.packages


def test_already_booked_is_stored_as_booked_with_the_couples_own_number(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    n_usage = len(w.usage)
    out = ask(w, FULL, "we already booked our venue: The Old Mill for $18,500", {"active": True}, chat_id=out["chat_id"])
    row = w.packages[-1]
    assert row == dict(row, vendor_name="The Old Mill", total_cost=18500, Balance_Left=18500, Balance_Paid=0, Selected=True,
                       Category="Venue", Package_Name="Booked in My wedding · tulle:manual")
    v = view_of(w, FULL)
    assert v["categories"][0]["booked"]["name"] == "The Old Mill" and v["categories"][0]["booked"]["price"] == 18500
    assert v["booked_total"] == 18500 and v["left"] == 41500
    r = rows_of(out)
    assert (r["Venue"]["basis"], r["Venue"]["name"], r["Venue"]["amount"], r["Venue"]["badge"]) == ("yours", "The Old Mill", 18500, ww.YOURS)
    assert r["Food and Beverage"]["basis"] == "none"            # the proposed venue's food line no longer applies
    assert proposal_of(out)["total"] == 18500 + 4550 + 2550 + 4150 + 6000 + 1200 + 11400
    s = out["wedding_status"]
    assert s["label"] == "In My wedding so far: 1 of 7 categories · $18,500 of your $60,000 budget"
    assert s["changed"] == ["Booked: Venue - The Old Mill"] and s["booked_total"] == v["booked_total"]
    assert len(w.usage) == n_usage and out["ww"]["counted"] is False            # telling us is not a question
    # later re-runs never replace what they booked
    out = ask(w, FULL, "Make it cheaper", {"action": "cheaper"}, chat_id=out["chat_id"])
    assert rows_of(out)["Venue"]["name"] == "The Old Mill" and rows_of(out)["Venue"]["basis"] == "yours"
    assert not any(c[0] == "search_venues" for c in w.calls[-8:])
    # said twice: one row
    ask(w, FULL, "we already booked our venue: The Old Mill for $18,500", {"active": True}, chat_id=out["chat_id"])
    assert sum(1 for p in w.packages if p["vendor_name"] == "The Old Mill") == 1


def test_booked_without_a_name_or_price_asks_and_writes_nothing(w):
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    out = ask(w, FULL, "we booked our venue", {"active": True}, chat_id=out["chat_id"])
    assert out["ww"]["step"] == "booked_ask" and "the price you agreed" in out["text"] and not w.packages


def test_strip_is_derived_only_from_the_view():
    user = {"Wedding_Budget": 60000}
    packs = [{"id": 1, "user_id": 1, "vendor_name": "The Old Mill", "total_cost": 18500, "Selected": True, "Category": "Venue", "created_at": 1},
             {"id": 2, "user_id": 1, "vendor_name": "Jane", "total_cost": 4000, "Selected": False, "Category": "Photography", "created_at": 2},
             {"id": 3, "user_id": 1, "vendor_name": "Other Jane", "total_cost": 9000, "Selected": False, "Category": "Photography", "created_at": 3},
             {"id": 4, "user_id": 1, "vendor_name": "Glow", "total_cost": 900, "Selected": False, "Category": "Beauty", "created_at": 4},
             {"id": 5, "user_id": 1, "vendor_name": "Cake", "total_cost": 700, "Selected": True, "Category": "Cake", "created_at": 5},
             {"id": 6, "user_id": 1, "vendor_name": "Example 1: Venue", "total_cost": 99999, "Selected": True, "Category": "Venue"}]
    v = wedding.view(user, packs, [], True)
    s = ww.strip(v)
    assert s["booked_total"] == v["booked_total"] == 19200 and s["budget"] == v["budget"] == 60000
    # planned = everything booked + the NEWEST still-deciding item of each open slot (never both photographers)
    assert s["planned"] == 19200 + 9000 + 900 and s["started"] == 3 and s["of"] == 7
    assert s["label"] == "In My wedding so far: 3 of 7 categories · $29,100 of your $60,000 budget"
    assert [x["state"] for x in s["slots"]] == ["booked", "considering", "open", "open", "open", "open", "considering"]
    assert ww.strip({"enabled": False}) is None and ww.strip(None) is None
    assert ww.strip(wedding.view({}, [], [], True))["label"] == "My wedding: nothing added yet"


# ---------------------------------------------------------------- 9. the guard: one proposal = one question

def test_a_blocked_couple_gets_the_usual_refusal_before_any_card_or_lookup(w, monkeypatch):
    monkeypatch.setattr(config, "FREE_INITIAL_REFINES", 0)
    monkeypatch.setattr(config, "FREE_DAILY_AFTER_INITIAL", 1)
    w.usage.append({"user_id": 7002, "kind": "refine", "status": "ok", "counted_as_free": True, "usage_day": xano.today()})
    for body in ({"action": "start"}, {"action": "context", "answers": {"guests": 100}}):
        with pytest.raises(HTTPException) as e:
            ask(w, FREE, "Plan my wedding", body)
        assert e.value.status_code == 429 and e.value.detail["status"] == "blocked_user_cap"
    assert not w.calls and not [r for r in w.usage if r.get("kind") == ww.KIND]
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    with pytest.raises(HTTPException) as e:
        ask(w, FULL, "Plan my wedding", {"action": "start"})
    assert e.value.status_code == 503


def test_a_proposal_is_the_free_daily_question(w, monkeypatch):
    """Daily phase: the proposal uses today's one question (guards count kind ww_proposal), so the next ask is refused."""
    monkeypatch.setattr(config, "FREE_INITIAL_REFINES", 0)
    monkeypatch.setattr(config, "FREE_DAILY_AFTER_INITIAL", 1)
    out = ask(w, FREE, "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert w.usage[-1]["kind"] == ww.KIND and w.usage[-1]["counted_as_free"] is True and w.usage[-1]["tier"] == "free"
    assert guards.free_questions_today(w.usage, xano.today()) == 1 and guards.free_refines_left(False, w.usage, today=xano.today()) == 0
    with pytest.raises(HTTPException) as e:
        ask(w, FREE, "swap the photographer", {"active": True}, chat_id=out["chat_id"])
    assert e.value.status_code == 429
    assert guards.used_today(w.usage, xano.today()) == 1                       # and it is a prompt for the paid caps too


def test_paid_daily_cap_counts_proposals(w, monkeypatch):
    monkeypatch.setattr(config, "PAID_DAILY_REFINES", 1)
    ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    with pytest.raises(HTTPException) as e:
        ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert e.value.status_code == 429


def test_our_own_failure_is_never_charged(w, monkeypatch):
    monkeypatch.setattr(xano, "budget_categories", lambda tok: (_ for _ in ()).throw(RuntimeError("xano down")))
    with pytest.raises(HTTPException) as e:
        ask(w, FREE, "Plan my wedding", {"action": "start", "skip": ["area"]})
    assert e.value.status_code == 502
    assert [(r["kind"], r["status"]) for r in w.usage] == [(ww.KIND, "error")] and guards.free_used(w.usage) == 0 and not w.chats


def test_one_category_failing_becomes_an_estimate_line(w, monkeypatch):
    monkeypatch.setattr(xano, "search_pe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ep119 down")))
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]})
    r = rows_of(out)
    assert (r["Photography"]["basis"], r["Photography"]["amount"], r["Photography"]["name"]) == ("estimate", 6000, "")
    assert r["Venue"]["name"] == "Hackett House"


# ---------------------------------------------------------------- 10. progress labels and Stop

def test_progress_labels_and_stop(w):
    labels = []

    class T:
        stop_at = None

        def set(self, label):
            labels.append(label)

        def check(self):
            if self.stop_at and self.stop_at in labels:
                raise progress.Stopped()

        def cancelled(self):
            return False

        def text(self, t):
            pass
    out = ask(w, FULL, "Plan my wedding", {"action": "start", "skip": ["area"]}, tracker=T())
    assert labels == ["Finding venues that fit 120 guests…", "Pricing the day…", "Checking your budget…"]
    assert all(len(l) <= progress.LABEL_MAX for l in labels) and proposal_of(out)
    t = T()
    t.stop_at = "Finding venues that fit 120 guests…"
    n = len(w.usage)
    out = ask(w, dict(FULL, id=7020), "Plan my wedding", {"action": "start", "skip": ["area"]}, tracker=t)
    assert out["stopped"] is True and out["widgets"] == []
    new = w.usage[n:]
    assert [(r["kind"], r["status"]) for r in new] == [(ww.KIND, "stopped")] and 7020 not in [c["user_id"] for c in w.chats.values()]
