"""Venue name lookup fast path (owner decision 2026-10-26): "oheka castle", "Khol mansion", "is X listed?" are answered
from table 11 in code - no model. Detection is conservative (searches and follow-ups still go to the model), typos are
found by rescoring candidates, free / LIGHT users still get no figures, and a missing venue is reported honestly with a
venue_request for the page and a notice for the data team. Real names below were checked in table 11 on 2026-10-26."""
import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import agent  # noqa: E402
import app  # noqa: E402
import config  # noqa: E402
import lookup  # noqa: E402
import notify  # noqa: E402
import xano  # noqa: E402

# ---------------------------------------------------------------- detection

LOOKUPS = {
    "oheka castle": ("oheka castle", ""),
    "royal palm mansion": ("royal palm mansion", ""),
    "bacchus, brooklyn": ("bacchus", "brooklyn"),
    "74 wythe": ("74 wythe", ""),
    "bowery hotel": ("bowery hotel", ""),
    "villa d'este": ("villa d'este", ""),
    "cresthollow": ("cresthollow", ""),
    "Ashton gardens": ("Ashton gardens", ""),
    "prospect park boathouse": ("prospect park boathouse", ""),
    "the orchards of molino": ("the orchards of molino", ""),
    "The Harold Pratt House and Peterson Hall": ("The Harold Pratt House and Peterson Hall", ""),
    "Jungle island pricing": ("Jungle island", ""),
    "the Astoria at Houston for a less than 100 guest wedding": ("the Astoria at Houston", ""),
    "Is Il Sereno, the hotel in Torno on Lake Como, Italy, listed in Tulle's vendor directory? What is its pricing?":
        ("Il Sereno", "Torno on Lake Como, Italy"),
    "Khols": ("Khols", ""),
    "Khol mansion": ("Khol mansion", ""),
    "How much does Oheka Castle cost?": ("Oheka Castle", ""),
    "Do you have Bacchus in Brooklyn?": ("Bacchus", "Brooklyn"),
}
NOT_LOOKUPS = [
    "barn venues in Georgia", "Show 3 more", "venues under $50k in catskills", "Cheaper options",
    "Outdoor ceremony space", "Try a different style", "Is a venue I like realistic?", "What will our wedding cost?",
    "Compare my saved venues", "Similar venues near Kohl Mansion", "Cost breakdown for Kohl Mansion", "thanks",
    "Brooklyn", "Texas", "yes please", "Waterfront venues", "All-inclusive venues", "Change my guest count",
    "Find a photographer", "DJs with a photo booth", "something rustic in vermont", "we have 120 guests",
    "Hudson Valley", "Napa", "Split our budget by category", "Open my budget", "Historic Architecture venues",
    "what about something cheaper", "Garden weddings in the fall", "Is Kohl Mansion a good fit for us?",
    "Compare Kohl Mansion with my saved venues", "Find venues within my venue budget",
]


@pytest.mark.parametrize("text", list(LOOKUPS))
def test_real_name_requests_are_detected(text):
    q = lookup.detect(text)
    assert q and (q["name"], q["location"]) == LOOKUPS[text]


@pytest.mark.parametrize("text", NOT_LOOKUPS)
def test_searches_chips_and_follow_ups_are_not(text):
    assert lookup.detect(text) is None


def test_a_reply_to_our_own_question_is_not_a_lookup():
    assert lookup.detect("Austin Golf Club", last_assistant="Which city or area of Texas do you want?") is None
    assert lookup.detect("Austin Golf Club pricing", last_assistant="Which area?")   # an explicit cue still is


def test_the_astoria_at_houston_is_also_read_as_name_plus_place():
    q = lookup.detect("the Astoria at Houston for a less than 100 guest wedding")
    assert (q["alt_name"], q["alt_location"]) == ("the Astoria", "Houston")


def test_state_of_place():
    assert lookup.state_of("Brooklyn, NY") == "New York"
    assert lookup.state_of("brooklyn") == "New York"
    assert lookup.state_of("Torno on Lake Como, Italy") == "International"
    assert lookup.state_of("texas") == "Texas"
    assert lookup.state_of("Houston") == ""


# ---------------------------------------------------------------- retrieval + ranking (offline table 11)

def row(vid, name, state, address="", google=""):
    return {"vendor_id": vid, "name": name, "state": state, "address": address, "google_name": google or name}


TABLE = [
    row("V1675", "Kohl Mansion", "California", "2750 Adeline Dr, Burlingame, CA 94010"),
    row("V2173", "Oheka Castle", "New York", "135 W Gate Dr, Huntington, NY 11743"),
    row("V17034", "Royal Palm", "Louisiana", "1901 Manhattan Blvd, Harvey, LA 70058"),
    row("V7137", "Royal Palm Manor", "Arizona", "1545 W Northern Ave, Phoenix, AZ 85021"),
    row("V6406", "Royal Palms Resort & Spa Phoenix", "Arizona", "5200 E Camelback Rd, Phoenix, AZ 85018",
        "Royal Palms Resort and Spa -The Unbound Collection by Hyatt"),
    row("V7272", "Ashton Gardens | Atlanta", "Georgia", "260 Peachtree Industrial Blvd, Sugar Hill, GA 30518"),
    row("V7300", "Ashton Gardens | Houston", "Texas", "1 Main St, Houston, TX 77001"),
    row("V7301", "Ashton Gardens | Dallas", "Texas", "1 Main St, Dallas, TX 75201"),
    row("V1325", "Harold Pratt House", "New York", "58 E 68th St, New York, NY 10065",
        "Harold Pratt House and Peterson Hall"),
    row("V5654", "Tres Molinos Hill Country Ranch & Resort", "Texas", "4260 N Ranch Rd 783, Harper, TX 78631"),
    row("V34", "74Wythe", "New York", "74 Wythe Ave, Brooklyn, NY 11249"),
    row("V3478", "The Prospect Park Boathouse", "New York", "1 Lakeside Dr, Brooklyn, NY 11225"),
    row("V16941", "Villa d'Este", "International", "Via Regina 40, 22012 Cernobbio (CO), Lake Como, Italy"),
    row("V233", "Bacchus", "New York", "409-411 Atlantic Ave, Brooklyn, NY 11217"),
    row("V425", "Bowery Hotel", "New York", "335 Bowery, New York, NY 10003", "The Bowery Hotel"),
    row("V1596", "Jungle Island", "Florida", "1111 Parrot Jungle Trail Miami, Florida 33132",
        "Bloom Events at Jungle Island"),
    row("V9001", "The Astoria", "Texas", "1 Astoria Way, Houston, TX 77002"),
    row("V9002", "Astoria Ballroom", "New Jersey", "2 Main St, Newark, NJ 07102"),
]


def fake_search(term, calls=None):
    """Mirrors xano.name_search (2026-10-26: the venue search box): every WORD of the term is a word of the name."""
    if calls is not None:
        calls.append(term)
    words = term.split()
    return [dict(r) for r in TABLE
            if all(w in lookup.tokens(r["name"]) or w in lookup.key(r["name"]) for w in words)]


def look(text):
    return lookup.find(lookup.detect(text), fake_search)


@pytest.mark.parametrize("text,vid", [
    ("Khol mansion", "V1675"),                 # the typo from the brief: kohl <-> khol
    ("oheka castle", "V2173"),
    ("bacchus, brooklyn", "V233"),
    ("74 wythe", "V34"),
    ("bowery hotel", "V425"),
    ("villa d'este", "V16941"),
    ("prospect park boathouse", "V3478"),
    ("The Harold Pratt House and Peterson Hall", "V1325"),   # matched on the Google name
    ("Jungle island pricing", "V1596"),
    ("Ashton Gardens, Atlanta", "V7272"),      # the place breaks the tie
    ("the Astoria at Houston for a less than 100 guest wedding", "V9001"),
])
def test_found(text, vid):
    res = look(text)
    assert res["status"] == "found", res
    assert res["matches"][0]["vendor_id"] == vid


def test_several_close_matches_ask_which_one():
    res = look("Ashton gardens")
    assert res["status"] == "choices" and {m["vendor_id"] for m in res["matches"]} == {"V7272", "V7300", "V7301"}
    res = look("royal palm mansion")         # no Royal Palm MANSION: the three Royal Palm(s) venues, not one guess
    assert res["status"] == "choices" and res["matches"][0]["vendor_id"] in {"V7137", "V17034"}
    assert len(res["matches"]) <= 3


def test_one_word_typo_offers_the_closest_match():
    res = look("Khols")
    assert res["status"] in ("found", "choices") and res["matches"][0]["vendor_id"] == "V1675"


@pytest.mark.parametrize("text", ["the orchards of molino", "cresthollow",
                                  "Is Il Sereno, the hotel in Torno on Lake Como, Italy, listed in Tulle's vendor "
                                  "directory? What is its pricing?"])
def test_not_in_our_data(text):
    assert look(text)["status"] == "not_found"


def test_a_correct_name_costs_one_round_typos_a_second():
    calls = []
    lookup.find(lookup.detect("oheka castle"), lambda t: fake_search(t, calls))
    assert calls == ["oheka castle", "oheka"]
    calls.clear()
    lookup.find(lookup.detect("Khol mansion"), lambda t: fake_search(t, calls))
    assert "kohl" in calls and len(calls) <= 6 + 8


def test_xano_down_is_an_error_not_a_missing_venue():
    def down(term):
        raise xano.XanoError("503")
    with pytest.raises(lookup.LookupFailed):
        lookup.find(lookup.detect("oheka castle"), down)


def test_scores():
    assert lookup.score("74 wythe", "74Wythe") == 1.0
    assert lookup.score("khol mansion", "Kohl Mansion") >= lookup.HIGH
    assert lookup.score("royal palm mansion", "Royal Palm Manor") < 0.9      # mansion != manor costs something
    assert lookup.score("the orchards of molino", "Tres Molinos Hill Country Ranch & Resort") < lookup.MID


# ---------------------------------------------------------------- the endpoint: no model, same rules

TODAY = "2026-10-26"
PRICING = {"vendor_id": "V1675", "headline": "", "guest_minimum": 0, "verdict": "Below typical for California",
           "lines": [{"line": "Rental fee", "detail": "x", "source": "venue", "vs_market": "Below", "amount": 19000,
                      "pct": None}], "source_note": "", "cost_inputs": None}


@pytest.fixture
def env(monkeypatch):
    st = {"logged": [], "added": [], "mp": [], "slack": [], "model": 0, "forever": True, "access": True, "rows": [],
          "search": fake_search, "history": []}

    def run(token, user, access, messages, **kw):
        st["model"] += 1
        return ({"text": "model picks", "cards": [], "more_cards": [], "chips": ["x"]},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})

    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7, "first_name": "Sam"}))
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(xano, "get_chat", lambda uid, cid: {"id": cid, "title": "Venue picks"} if cid == 9 else None)
    monkeypatch.setattr(xano, "get_messages", lambda cid: [dict(t) for t in st["history"]])
    monkeypatch.setattr(xano, "add_message", lambda *a: st["added"].append(a))
    monkeypatch.setattr(xano, "touch_chat", lambda c: None)
    monkeypatch.setattr(xano, "log_usage", lambda r: st["logged"].append(r))
    monkeypatch.setattr(xano, "user_usage", lambda uid: list(st["rows"]))
    monkeypatch.setattr(xano, "spend_today", lambda: (0.0, 0.0))
    monkeypatch.setattr(xano, "has_forever", lambda u: st["forever"])
    monkeypatch.setattr(xano, "beta_member", lambda u: st["forever"])
    monkeypatch.setattr(xano, "has_paid_access", lambda u: st["access"])
    monkeypatch.setattr(xano, "mp_track", lambda uid, ev, props: st["mp"].append((ev, props)))
    monkeypatch.setattr(xano, "today", lambda: TODAY)
    monkeypatch.setattr(xano, "plan_label", lambda u: "Forever")
    monkeypatch.setattr(xano, "name_search", lambda t, token="": st["search"](t))
    monkeypatch.setattr(xano, "venue_pricing", lambda tok, vid, full: dict(PRICING, vendor_id=vid))
    monkeypatch.setattr(xano, "search_venues", lambda tok, **kw: {"total_matches": 2, "venues": [
        {"vendor_id": "V50", "name": "Nearby One", "state": "New York", "venue_fee_range": [5000, 9000]},
        {"vendor_id": "V51", "name": "Nearby Two", "state": "New York", "venue_fee_range": [7000, 7000]}]})
    monkeypatch.setattr(notify, "venue_request", lambda *a, **k: st["slack"].append(a))
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)
    monkeypatch.setattr(config, "NAME_LOOKUP", True)
    return st


def ask(text):
    r = TestClient(app.app).post("/rec/refine", json={"chat_id": 9, "message": text})
    assert r.status_code == 200, r.text
    return r.json()


def settle(st, key, n=1):
    for _ in range(100):           # usage + Mixpanel rows are written from the pool, after the response
        if len(st[key]) >= n:
            return
        time.sleep(0.02)


def test_paid_found_answers_without_the_model(env):
    t = time.time()
    j = ask("Khol mansion")
    assert time.time() - t < 1.5          # offline: the fast path itself adds next to nothing
    assert env["model"] == 0
    assert j["lookup"] == {"status": "found", "query": "Khol mansion"} and j["venue_request"] is None
    c = j["cards"][0]
    assert c["vendor_id"] == "V1675" and c["venue_fee_range"] == [19000, 19000]   # FULL: the panel's rental
    assert "Kohl Mansion" in j["text"] and "Burlingame" in j["text"]
    assert any(ch.startswith("Cost breakdown for Kohl Mansion") for ch in j["chips"])
    settle(env, "logged")
    ok = [r for r in env["logged"] if r["status"] == "ok"]
    assert len(ok) == 1 and ok[0]["kind"] == "refine" and ok[0]["model"] == "lookup" and ok[0]["cost_usd"] == 0
    settle(env, "mp")
    assert any(ev == "rec_server_request" and p["kind"] == "lookup" for ev, p in env["mp"])
    assert [a[1:3] for a in env["added"]][:2] == [("user", "Khol mansion"), ("assistant", j["text"])]


def test_free_user_gets_no_price_of_any_kind(env):
    env["forever"], env["access"] = False, False
    j = ask("oheka castle")
    assert env["model"] == 0 and j["price_locked"] is True
    c = j["cards"][0]
    assert c["vendor_id"] == "V2173" and "venue_fee_range" not in c and c.get("price_locked")
    assert "$" not in j["text"] and "Below typical" not in j["text"]
    assert not any("breakdown" in ch.lower() or "cost" in ch.lower() for ch in j["chips"])
    assert j["free_refines_left"] == config.FREE_REFINES - 1        # spends a question like any typed one


def test_light_member_gets_fit_not_figures(env):
    env["forever"], env["access"] = False, True        # a 1-week / 4-week plan: DETAIL LIGHT
    j = ask("oheka castle pricing")
    c = j["cards"][0]
    assert "venue_fee_range" not in c and "estimate_label" not in c and "$" not in j["text"]


def test_several_matches_ask_which_one_with_a_chip_each(env):
    j = ask("Ashton gardens")
    assert j["lookup"]["status"] == "choices" and len(j["cards"]) == 3 and "which one" in j["text"]
    assert all(any(c["name"] in ch for ch in j["chips"]) for c in j["cards"])


def test_missing_venue_is_honest_and_requested(env):
    j = ask("Do you have Cresthollow in Long Island, NY?")
    assert env["model"] == 0
    assert j["lookup"]["status"] == "not_found" and j["text"].startswith("We don't have Cresthollow in Tulle yet")
    assert j["venue_request"] == {"name": "Cresthollow", "location": "Long Island, NY"}
    assert [c["vendor_id"] for c in j["cards"]] == ["V50", "V51"]    # real venues in New York
    settle(env, "slack")
    assert env["slack"] and env["slack"][0][1] == "Cresthollow"
    assert any(ev == "rec_venue_request" and p["venue_name"] == "Cresthollow" for ev, p in env["mp"])


def test_generic_search_still_goes_to_the_model(env):
    j = ask("barn venues in Georgia")
    assert env["model"] == 1 and j["text"] == "model picks" and j["lookup"] is None


def test_lookup_failure_falls_back_to_the_model(env):
    def down(term):
        raise xano.XanoError("503")
    env["search"] = down
    j = ask("oheka castle")
    assert env["model"] == 1 and j["text"] == "model picks"


def test_one_unplaceable_word_goes_to_the_model(env):
    j = ask("Zzyzxville")
    assert env["model"] == 1


def test_flag_off_is_the_old_path(env, monkeypatch):
    monkeypatch.setattr(config, "NAME_LOOKUP", False)
    ask("oheka castle")
    assert env["model"] == 1


def test_free_user_at_the_limit_gets_the_paywall_not_a_lookup(env):
    env["forever"], env["access"] = False, False
    env["rows"] = [{"status": "ok", "kind": "refine", "counted_as_free": True}] * config.FREE_REFINES
    monkeypatch_fb = True
    r = TestClient(app.app).post("/rec/refine", json={"chat_id": 9, "message": "oheka castle"})
    assert r.status_code in (402, 403) and env["model"] == 0 and env["added"] == []
    assert monkeypatch_fb
