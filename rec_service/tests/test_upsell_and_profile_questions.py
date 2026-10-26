"""Owner decisions 2026-10-26:
A. a price question from someone not on Forever -> answer on fit + one Forever line + `upsell_prompt` (once per
   chat, never for paid members) instead of a refusal;
B. a thin profile -> the opening asks at most 2 questions with tap answers (`questions` + chips) and saves the
   answer to the couple's memory so the next opening uses it and doesn't re-ask.
No network, no model: xano and agent.run / the Anthropic client are faked."""
import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402

import prefs  # noqa: E402
import profile_questions as pq  # noqa: E402
import upsell  # noqa: E402

T = "2026-10-26"


# ---------------------------------------------------------------- A. price-question detector

@pytest.mark.parametrize("msg", [
    "Show me all-inclusive venues for 120 guests under $30k in Brooklyn",
    "What will our wedding cost overall",
    "Best prices?",
    "Estimate my cost",
    "how much is a barn in Georgia",
    "venues under 30k near Austin",
    "Which one is cheapest?",
    "Is The Grand Hall affordable?",
    "what's the per person price for catering",
    "Can we do it on a 25,000 budget",
])
def test_price_questions_trigger(msg):
    assert upsell.is_price_question(msg)


@pytest.mark.parametrize("msg", [
    "barn venues in Georgia", "Show 3 more", "Find a photographer", "All-inclusive venues",
    "Outdoor ceremony space", "Change my guest count", "Try a different style", "Waterfront venues",
    "Venues for 120 guests in Austin", "Is a venue I like a good fit?", "Bring-your-own-caterer venues",
    "We have 1,200 guests", "Near Dallas", "100-150 guests", "a DJ with a photo booth",
])
def test_non_price_questions_do_not_trigger(msg):
    assert not upsell.is_price_question(msg)


def test_apply_text_drops_refusals_and_adds_one_forever_line():
    out = upsell.apply_text("I can't give cost numbers here. The Barn at Oak Hill seats 150 and has a lawn.")
    assert "can't" not in out and "Oak Hill seats 150" in out and out.endswith(upsell.NUDGE)
    again = upsell.apply_text("Two great fits. Forever unlocks exact prices here.")
    assert again == "Two great fits. Forever unlocks exact prices here."          # already nudged: unchanged


def test_nudge_survives_the_free_price_guard():
    import free_mode
    assert not free_mode.has_price_talk(upsell.NUDGE)
    assert upsell.NUDGE in free_mode.sanitize_text("The Barn seats 150 with a big lawn. " + upsell.NUDGE)[0]


def test_prompt_shape():
    p = upsell.prompt()
    assert set(p) == {"reason", "plan", "headline", "body", "cta"} and p["reason"] == "price_question"
    assert p["cta"] == "See plans" and upsell.prompt(has_plan=True)["body"] != p["body"]


# ---------------------------------------------------------------- B. thin profile

TX = {"Wedding_Location_Updated": ["Texas"]}


def test_thin_profile_gaps_and_two_questions():
    assert pq.gaps(TX, {}) == ["guests", "area", "style"]
    qs = pq.questions(TX)
    assert [q["id"] for q in qs] == ["guests", "area"]
    assert qs[1]["text"] == "Which part of Texas should we look near?"
    assert [o["label"] for o in qs[1]["options"]] == ["Near Dallas", "Near Austin", "Near Houston", "Near San Antonio"]
    assert qs[1]["options"][0]["value"] == "Dallas, Texas"


def test_full_profile_is_not_thin():
    full = dict(TX, Wedding_Guest_Count=150, wedding_vibes=["Rustic"])
    notes = [{"note": "Wants to stay near: Flower Mound, Texas"}]
    assert pq.questions(full, notes) == []
    assert pq.gaps(dict(TX, Wedding_Guest_Count=150), {}, active_filters={"city": "Austin"}) == ["style"]


def test_no_state_skips_the_area_question_and_two_states_split_chips():
    assert pq.gaps({}, {}) == ["guests", "style"]
    assert pq.gaps({"Wedding_Location_Updated": ["International"]}, {}) == ["guests", "style"]
    two = pq.area_options(["Texas", "Georgia"])
    assert [o["label"] for o in two] == ["Near Dallas", "Near Austin", "Near Atlanta", "Near Savannah"]


def test_saved_answers_and_ask_cap_stop_the_questions():
    notes = prefs.to_notes({"guests": ["150"], "area": ["Dallas, Texas"]})
    assert [q["id"] for q in pq.questions(TX, notes)] == ["style"]
    asked_twice = [{"kind": "pref_asked", "key": "guests"}] * pq.MAX_ASKS
    assert [q["id"] for q in pq.questions(TX, asked_twice)] == ["area", "style"]


@pytest.mark.parametrize("qid,text,want", [
    ("guests", "100-150 guests", "150"), ("guests", "about 120", "120"), ("guests", "120 guests", "120"),
    ("guests", "Show 3 more", None), ("guests", "Top 5 venues", None), ("guests", "under $30k", None),
    ("area", "Near Dallas", "Dallas, Texas"), ("area", "near austin please", "Austin, Texas"),
    ("area", "Flower Mound", None), ("style", "Barn or ranch", "Barn / Ranch"), ("style", "a rustic barn", "Barn / Ranch"),
    ("style", "Find a photographer", None),
    ("guests", "Show me all-inclusive venues for 120 guests under $30k in Brooklyn please", None),   # a new request
])
def test_match_answer(qid, text, want):
    assert pq.match_answer(pq.question(qid, TX), text) == want


def test_inline_answers_are_validated_stored_and_searched():
    a = prefs.clean({"guests": ["150"], "area": ["Dallas, Texas"]})
    assert a == {"guests": ["150"], "area": ["Dallas, Texas"]}
    assert prefs.clean({"guests": ["lots"], "area": ["<script>"]}) == {}
    notes = prefs.to_notes(a)
    assert {n["note"] for n in notes} == {"Wants to stay near: Dallas, Texas", "Guest count: about 150"}
    h = prefs.search_hint(a)
    assert "city='Dallas'" in h and "guests=150" in h
    assert prefs.search_hint({"area": ["the Hudson Valley, New York"]}).count("city='Hudson Valley'") == 1
    # answers from the chat don't hide the full questionnaire
    assert not prefs.asked([dict(n, src="chat") for n in notes]) and prefs.asked(notes)


def test_merge_preferences_keeps_everything_else_and_questionnaire_keeps_inline(monkeypatch):
    import xano
    store = {"row": {"id": 9, "user_id": 5, "notes": [
        {"kind": "pref", "key": "style", "values": ["Barn / Ranch"], "note": "Style: Barn / Ranch"},
        {"note": "Venue budget about $8,000"}, {"kind": "pref_asked", "key": "guests"}]}}
    monkeypatch.setattr(xano, "get_memory", lambda uid: store["row"])
    monkeypatch.setattr(xano, "_put_full", lambda t, row, ch: store["row"].update(ch))
    monkeypatch.setattr(xano, "today", lambda: T)
    added = xano.merge_preferences(5, {"guests": ["150"]})
    assert [n["note"] for n in added] == ["Guest count: about 150"] and added[0]["src"] == "chat"
    assert prefs.from_notes(store["row"]["notes"]) == {"guests": ["150"], "style": ["Barn / Ranch"]}
    assert {"kind": "pref_asked", "key": "guests"} in store["row"]["notes"]
    xano.set_preferences(5, {"setting": ["Waterfront"]})          # the questionnaire replaces ITS answers only
    assert prefs.from_notes(store["row"]["notes"]) == {"guests": ["150"], "setting": ["Waterfront"]}
    xano.mark_questions_asked(5, ["area"])
    assert pq.asked_counts(store["row"]["notes"]) == {"guests": 1, "area": 1}
    xano.add_memory_note(5, "Loves string lights", cap=1)        # the note cap never trims the markers
    assert pq.asked_counts(store["row"]["notes"]) == {"guests": 1, "area": 1}


def test_message_row_reads_the_hidden_questions_and_upsell_entries():
    import xano
    q = pq.question("guests", TX)
    t = xano.parse_message_row({"role": "assistant", "content": "x", "cards": [
        {"vendor_id": "V1"}, {"meta": "questions", "questions": [q]}, {"meta": "upsell", "reason": "price_question"}]})
    assert t["questions"] == [q] and t["upsell_shown"] is True and t["cards"] == [{"vendor_id": "V1"}]
    t = xano.parse_message_row({"role": "assistant", "content": "x", "cards": []})
    assert t["questions"] == [] and t["upsell_shown"] is False


# ---------------------------------------------------------------- the model's context

class _Msg:
    def __init__(self):
        self.content, self.model = [], "fake"
        self.usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)


def _context(monkeypatch, **kw):
    import agent, xano
    cap = {}

    class Runner:
        def __init__(self, tools, **k):
            cap.update(k)
            self.tools = {(getattr(t, "name", None) or getattr(t, "__name__", None)): t for t in tools}

        def __iter__(self):
            t = self.tools["present_recommendations"]
            args = {"message": "m", "vendor_ids": [], "reasons": [], "chips": []}
            t.call(args) if hasattr(t, "call") else t(**args)
            yield _Msg()

    class Client:
        def __init__(self):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(tool_runner=lambda **k: Runner(**k)))
    monkeypatch.setattr(agent.anthropic, "Anthropic", Client, raising=False)
    monkeypatch.setattr(xano, "market_benchmarks", lambda s, g=0: {"state": s})
    agent.run("tok", {"id": 0}, False, [{"role": "user", "content": "hi"}], profile_override=dict(TX), **kw)
    return cap["messages"][0]["content"]


def test_context_profile_questions_replace_the_state_only_question(monkeypatch):
    ctx = _context(monkeypatch, detail="free", profile_questions=pq.questions(TX))
    assert "PROFILE QUESTIONS" in ctx and "Which part of Texas" in ctx and "STATE ONLY" not in ctx
    assert "STATE ONLY" in _context(monkeypatch, detail="free", profile_questions=[pq.question("guests", TX)])


def test_context_known_area_guests_and_price_question(monkeypatch):
    ctx = _context(monkeypatch, detail="free", known_area="Dallas, Texas", profile_extra={"Wedding_Guest_Count": 150},
                   price_question=True)
    assert "LOCATION AREA" in ctx and "Dallas, Texas" in ctx and "STATE ONLY" not in ctx
    assert '"Wedding_Guest_Count": 150' in ctx and "PRICE QUESTION" in ctx
    assert "PRICE QUESTION" not in _context(monkeypatch, detail="full", price_question=True)   # Forever: never


# ---------------------------------------------------------------- endpoints

@pytest.fixture
def api(monkeypatch):
    import agent, app, config, xano
    from fastapi.testclient import TestClient
    st = {"runs": [], "added": [], "history": {}, "memory": {"id": 1, "user_id": 7, "notes": []}, "paid": False,
          "access": False, "user": {"id": 7, "Wedding_Location_Updated": ["Texas"]}, "asked": [],
          "text": "I can't give cost numbers here. The Barn seats 150 and has a lawn."}

    def run(token, user, access, messages, **kw):
        st["runs"].append(kw)
        return ({"text": st["text"], "cards": [{"vendor_id": "V1", "name": "The Barn"}], "more_cards": [],
                 "chips": ["Waterfront venues"], "notes_saved": []},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    def merge(uid, answers):
        new = [dict(n, src="chat") for n in prefs.to_notes(answers)]
        keys = {n["key"] for n in new}
        st["memory"]["notes"] = new + [n for n in st["memory"]["notes"] if n.get("key") not in keys]
        return new

    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FREE_REFINES", 5)
    monkeypatch.setattr(config, "PROFILE_QUESTIONS", True)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", st["user"]))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: st["memory"])
    monkeypatch.setattr(xano, "merge_preferences", merge)
    monkeypatch.setattr(xano, "mark_questions_asked", lambda uid, ids: st["asked"].append(ids))
    monkeypatch.setattr(xano, "create_chat", lambda uid, title: {"id": 51, "title": title})
    monkeypatch.setattr(xano, "list_chats", lambda uid: [])
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "t"})
    monkeypatch.setattr(xano, "get_messages", lambda cid: [xano.parse_message_row(
        {"role": r, "content": c, "cards": cards, "chips": ch}) for r, c, cards, ch in st["history"].get(cid, [])])

    def add_message(cid, role, content, cards=None, chips=None):
        st["added"].append((cid, role, content, cards, chips))
        st["history"].setdefault(cid, []).append((role, content, cards or [], chips or []))
    monkeypatch.setattr(xano, "add_message", add_message)
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", lambda row: None)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: st["paid"])
    monkeypatch.setattr(xano, "beta_member", lambda u: st["paid"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "today", lambda: T)
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    st["c"] = TestClient(app.app)
    return st


def test_thin_opening_asks_two_questions_then_saves_the_tapped_answer(api):
    c = api["c"]
    j = c.post("/rec/opening").json()
    assert [q["id"] for q in j["questions"]] == ["guests", "area"] and j["upsell_prompt"] is None
    assert j["chips"] == ["Under 50 guests", "50-100 guests", "100-150 guests", "150+ guests"]
    assert j["cards"] and api["asked"] == [["guests", "area"]]
    assert [q["id"] for q in api["runs"][-1]["profile_questions"]] == ["guests", "area"]
    stored = api["added"][-1][3]
    assert {"meta": "questions", "questions": j["questions"]} in stored
    # the couple taps "100-150 guests": saved before the model runs, the next question comes up
    j = c.post("/rec/refine", json={"chat_id": 51, "message": "100-150 guests"}).json()
    assert prefs.from_notes(api["memory"]["notes"]) == {"guests": ["150"]}
    assert "Guest count: about 150" in j["notes_saved"]
    assert api["runs"][-1]["profile_extra"] == {"Wedding_Guest_Count": 150}
    assert [q["id"] for q in j["questions"]] == ["area"] and j["chips"][0] == "Near Dallas"
    # then types an area
    j = c.post("/rec/refine", json={"chat_id": 51, "message": "near houston"}).json()
    assert prefs.from_notes(api["memory"]["notes"]) == {"guests": ["150"], "area": ["Houston, Texas"]}
    assert j["questions"] == [] and api["runs"][-1]["known_area"] == "Houston, Texas"
    # next opening: nothing re-asked except the style gap
    j = c.post("/rec/opening").json()
    assert [q["id"] for q in j["questions"]] == ["style"]
    assert "city='Houston'" in api["runs"][-1]["preferences"]


def test_full_profile_opening_is_unchanged(api):
    api["user"] = {"id": 7, "Wedding_Location_Updated": ["Texas"], "Wedding_Guest_Count": 120,
                   "wedding_vibes": ["Rustic"]}
    api["memory"]["notes"] = [{"note": "Wants to stay near: Flower Mound, Texas"}]
    j = api["c"].post("/rec/opening").json()
    assert j["questions"] == [] and "Under 50 guests" not in j["chips"] and api["asked"] == []


def test_free_price_question_gets_fit_answer_and_upsell_once_per_chat(api):
    c = api["c"]
    j = c.post("/rec/refine", json={"chat_id": 60, "message": "What will our wedding cost overall"}).json()
    assert j["upsell_prompt"]["reason"] == "price_question" and j["upsell_prompt"]["cta"] == "See plans"
    assert "can't" not in j["text"] and "Forever" in j["text"] and "The Barn seats 150" in j["text"]
    assert api["runs"][-1]["price_question"] is True
    assert {"meta": "upsell", "reason": "price_question"} in api["added"][-1][3]
    j = c.post("/rec/refine", json={"chat_id": 60, "message": "Best prices?"}).json()
    assert j["upsell_prompt"] is None and api["runs"][-1]["price_question"] is False
    j = c.post("/rec/refine", json={"chat_id": 61, "message": "barn venues in Georgia"}).json()
    assert j["upsell_prompt"] is None and api["runs"][-1]["price_question"] is False
    turns = c.get("/rec/chats/60").json()["turns"]
    assert all("upsell_shown" not in t for t in turns)


def test_light_plan_gets_upsell_but_forever_never(api):
    api["access"] = True        # an active non-Forever plan: detail LIGHT
    j = api["c"].post("/rec/refine", json={"chat_id": 62, "message": "Estimate my cost"}).json()
    assert j["upsell_prompt"] and j["upsell_prompt"]["body"].startswith("Forever adds")
    api["paid"] = True          # Forever / full member
    api["text"] = "The Barn is $9,000 for 150 guests."
    j = api["c"].post("/rec/refine", json={"chat_id": 63, "message": "Estimate my cost"}).json()
    assert j["upsell_prompt"] is None and j["text"] == "The Barn is $9,000 for 150 guests."
    assert api["runs"][-1]["price_question"] is False


def test_preferences_merge_endpoint_saves_one_answer_without_a_question(api):
    r = api["c"].post("/rec/preferences", json={"answers": {"area": ["Austin, Texas"]}, "merge": True})
    assert r.status_code == 200 and r.json()["added"] == ["Wants to stay near: Austin, Texas"]
    g = api["c"].get("/rec/preferences").json()
    assert g["profile_answers"] == {"area": ["Austin, Texas"]} and g["answers"] == {} and g["asked"] is False
    assert api["runs"] == []
