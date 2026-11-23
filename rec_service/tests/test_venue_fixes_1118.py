"""Planner venue fixes (2026-11-18) from the owner's 14-day Planner review (11-04..11-17, 674 typed messages, 269
couples): REC_VENUE_LOG, REC_DID_YOU_MEAN, REC_LOOKUP_GUARD, REC_PAGE_VENUE, REC_LOOKUP_NO_LIMIT, REC_FREE_BUDGET_FIT.
Every flag defaults OFF, and every "flag off" test pins today's behaviour.

Typed messages are the couples' own words from the review. The table-11 rows below are CONSTRUCTED for the tests (the
names the review says we hold, e.g. "The Skylands Manor at the NJ State Botanical Garden"; addresses invented)."""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import agent  # noqa: E402
import config  # noqa: E402
import free_mode  # noqa: E402
import guards  # noqa: E402
import lookup  # noqa: E402
import venue_log  # noqa: E402
import xano  # noqa: E402
from test_name_lookup import env, ask, settle, row  # noqa: E402,F401
from test_repeats_and_filters import world, present, _search_result, pricing  # noqa: E402,F401

FLAGS = ("VENUE_LOG", "FREE_BUDGET_FIT", "DID_YOU_MEAN", "LOOKUP_GUARD", "PAGE_VENUE", "LOOKUP_NO_LIMIT")


@pytest.fixture(autouse=True)
def _all_off(monkeypatch):
    """Each test starts with every flag of this build off (also when the suite runs with them on in the env) and turns
    on only what it tests."""
    for f in FLAGS:
        monkeypatch.setattr(config, f, False)


T = [
    row("A1", "The Skylands Manor at the NJ State Botanical Garden", "New Jersey", "2 Morris Rd, Ringwood, NJ 07456"),
    row("A2", "Lightner Museum St. Augustine", "Florida", "75 King St, St. Augustine, FL 32084"),
    row("A3b", "Fox Theatre", "Michigan", "2211 Woodward Ave, Detroit, MI 48201"),
    row("A3", "Fox Theatre", "Georgia", "660 Peachtree St NE, Atlanta, GA 30308"),
    row("A4", "Ryland Inn", "New Jersey", "115 Old Hwy 28, Whitehouse Station, NJ 08889"),
    row("A5", "Duxbury Bay Maritime Weddings", "Massachusetts", "457 Washington St, Duxbury, MA 02332"),
    row("A6", "The Sanctuary", "New York", "1 Main St, Brooklyn, NY 11201"),
    row("A7", "The Sanctuary at Woods Hill", "Rhode Island", "1 Hill Rd, Newport, RI 02840"),
    row("A8", "Central Park Pavilion", "Minnesota", "1 Park Ave, Roseville, MN 55113"),
    row("A9", "The Sapphire Estate", "Massachusetts", "1 Main St, Sharon, MA 02067"),
    row("A10", "Oheka Castle", "New York", "135 W Gate Dr, Huntington, NY 11743"),
    row("A11", "Bistro Romano", "Pennsylvania", "120 Lombard St, Philadelphia, PA 19147"),
    row("A12", "Woodmont Manor", "Maryland", "1 Manor Rd, Rockville, MD 20850"),
    row("A13", "Elkins Estate", "Pennsylvania", "1 Ogontz Ave, Elkins Park, PA 19027"),
    row("A14", "Westmount Country Club", "New Jersey", "728 Rifle Camp Rd, Woodland Park, NJ 07424"),
]


def ws(table=T):
    """Like xano.name_search: every word of the term is a whole word of the name (or the joined name key)."""
    def search(term):
        return [dict(r) for r in table if all(w in lookup.tokens(r["name"]) or w in lookup.key(r["name"])
                                              for w in term.split())]
    return search


def look(text, on, prefer=(), table=T):
    """The lookup as app._find_and_price runs it (venue table only)."""
    q = lookup.detect(text, v2=True, guard=on, dym=on)
    if not q:
        return None
    res = lookup.resolve(q, lookup.find(q, ws(table)), prefer)
    if q.get("dym"):
        res = lookup.did_you_mean(q, res, prefer) if res["status"] == "not_found" else lookup.place_fix(q, res, prefer)
    return res


# ================================================================ REC_DID_YOU_MEAN: "have it but the name didn't match"

HAVE_IT = [   # (typed, vendor we hold)
    ("Skylands manor", "A1"),
    ("Lightner Meusuem", "A2"),
    ("Rhyland", "A4"),
    ("Rhyland pricing", "A4"),
    ("Duxbury Bar Maritime School", "A5"),
    ("can you show me the price of the duxbury bar maritime school", "A5"),
    ("show me the pricing for the sapphire estate in sharon massachusetts", "A9"),
    ("Bristro Romano", "A11"),
    ("Woodmount manor", "A12"),
    ("Elkin estate", "A13"),
]


@pytest.mark.parametrize("text,vid", [p for p in HAVE_IT if p[0] not in ("Bristro Romano", "Elkin estate")])
def test_flag_off_reproduces_not_found(text, vid):
    assert look(text, on=False)["status"] == "not_found"


@pytest.mark.parametrize("text,vid", HAVE_IT)
def test_flag_on_offers_or_finds_the_venue_we_have(text, vid):
    res = look(text, on=True)
    assert res["status"] in ("found", "best", "dym"), res
    assert res["matches"][0]["vendor_id"] == vid


def test_fox_theatre_atl_reads_the_city_and_picks_atlanta():
    off = look("fox theatre atl", on=False)
    on = look("fox theatre atl", on=True)
    assert off["status"] == "not_found"
    assert on["status"] in ("found", "best") and on["matches"][0]["vendor_id"] == "A3"
    # only the Detroit one in the table: shown, and the answer says it isn't in Atlanta
    only_detroit = [r for r in T if r["vendor_id"] != "A3"]
    res = look("Fox Theatre atl", on=True, table=only_detroit)
    assert res["status"] == "elsewhere" and res["matches"][0]["vendor_id"] == "A3b" and res["typed_place"]


def test_westmount_joined_name_still_found():
    assert look("West mount country club", on=True)["matches"][0]["vendor_id"] == "A14"


# ---------------------------------------------------------------- wrong venue: the state / city hint was ignored

def test_the_sanctuary_ri_picks_rhode_island_not_new_york():
    assert look("The Sanctuary RI", on=False)["matches"][0]["vendor_id"] == "A6"       # today: the NY one
    res = look("The Sanctuary RI", on=True)
    assert res["status"] == "best" and [m["vendor_id"] for m in res["matches"]] == ["A7", "A6"]


def test_central_park_during_a_new_york_chat_is_not_silently_minnesota():
    assert look("Central park", on=False, prefer=["New York"])["status"] == "found"   # today: Minnesota, no word
    res = look("Central park", on=True, prefer=["New York"])
    assert res["status"] == "elsewhere" and res["elsewhere_of"] == "New York" and not res["typed_place"]


def test_in_state_venues_are_untouched():
    assert look("oheka castle", on=True, prefer=["New York"])["status"] == "found"
    assert look("oheka castle", on=True)["status"] == "found"


def test_place_hints():
    assert lookup.place_hint("sharon massachusetts") == "sharon, Massachusetts"
    assert lookup.place_hint("atl") == "Atlanta, Georgia"
    assert lookup.place_hint("Brooklyn, NY") == "Brooklyn, NY"
    assert lookup.city_tail("Public nyc") == ("Public", "New York, New York")
    assert lookup.city_tail("Hotel Houston") == ("", "")           # nothing distinctive would be left


def test_did_you_mean_is_not_a_free_for_all():
    # one shared generic word, or a short distinctive word that only partly matches, is never "did you mean"
    assert lookup.dym_score("Royal Manor", "Oheka Castle") == 0
    assert lookup.dym_score("Greg stone mansion", "Greystone Mansion") == 0
    assert lookup.dym_score("The Barn", "The Barn at Forest Hill") == lookup.dym_score("The Barn", "The Barn at Forest Hill")
    assert look("cresthollow", on=True)["status"] == "not_found"
    assert look("the orchards of molino", on=True)["status"] == "not_found"


def test_flag_off_detect_and_find_unchanged():
    for text, _ in HAVE_IT + [("The Sanctuary RI", ""), ("oheka castle", ""), ("Pricing", "")]:
        a = lookup.detect(text, v2=True)
        assert a == lookup.detect(text, v2=True, guard=False, dym=False)
        if a:
            r = lookup.find(a, ws())
            assert "pool" not in r and r["status"] == lookup.find(dict(a), ws())["status"]


# ================================================================ REC_LOOKUP_GUARD + the Slack rule

NOT_VENUES = ["Pricing", "pricing", "im looking for a bar tender", "bar tender", "houston and serounding areas",
              "Texas city", "estate multi day", "It�s stupid", "It’s stupid", "is that inclusive?",
              "Nevermind - Disregard", "Ceremony only", "What about hotel buyout?", "Can it hold 200?"]


@pytest.mark.parametrize("text", NOT_VENUES)
def test_guard_sends_non_venue_text_to_the_model(text):
    assert lookup.detect(text, v2=True, guard=True) is None


def test_guard_flag_off_keeps_todays_reading():
    for text in ("Pricing", "im looking for a bar tender", "Texas city", "estate multi day"):
        assert lookup.detect(text, v2=True) is not None      # today: read as a venue name


REAL = ["madonna inn", "Gorge beer co", "The ivory peacock", "Crossed keys", "how about gustavinos?",
        "What about the Seville in chicago", "Tell me about Oheka Castle", "Do you have Holly Hedge Estate?",
        "Rhyland pricing", "any info on surrogates court house", "virgin hotels NYC pricing", "Lamb hill",
        "Water works", "Is Il Sereno, the hotel in Torno on Lake Como, Italy, listed in Tulle's vendor directory?",
        "can you show me the price of the duxbury bar maritime school", "bohemia manor farm", "Bacchus, Brooklyn"]


@pytest.mark.parametrize("text", REAL)
def test_guard_keeps_real_lookups(text):
    assert lookup.detect(text, v2=True, guard=True, dym=True) is not None


@pytest.mark.parametrize("name,text,ok", [
    ("Madonna Inn", "madonna inn", True), ("Lamb Hill", "Lamb hill", True),
    ("Duxbury Bar Maritime School", "can you show me the price of the duxbury bar maritime school", True),
    ("Pricing", "pricing", False), ("Rhyland", "Rhyland pricing", False), ("Oheka", "oheka", False),
    ("It's Stupid", "It’s stupid", False), ("Is That Inclusive", "is that inclusive?", False),
    ("Nevermind - Disregard", "nevermind - disregard", False), ("Texas City", "Texas city", False),
    ("Ceremony Only", "Ceremony only", False), ("Bar Tender", "bar tender", False)])
def test_slack_worthy(name, text, ok):
    assert lookup.slack_worthy(name, text) is ok


# ================================================================ the endpoint

@pytest.fixture
def table(env):
    env["search"] = ws()
    return env


def test_endpoint_did_you_mean_card_no_slack_no_question_spent(table, monkeypatch):
    monkeypatch.setattr(config, "DID_YOU_MEAN", True)
    table["forever"], table["access"] = False, False
    j = ask("Skylands manor")
    assert table["model"] == 0 and j["lookup"]["status"] == "dym" and j["venue_request"] is None
    assert "did you mean The Skylands Manor at the NJ State Botanical Garden" in j["text"]
    assert j["cards"][0]["vendor_id"] == "A1" and "$" not in j["text"]
    assert j["chips"][0] == "The Skylands Manor at the NJ State Botanical Garden"
    settle(table, "logged")
    row_ = [r for r in table["logged"] if r["status"] == "ok"][0]
    assert row_["tools"] == "lookup:dym" and guards.free_used([dict(row_, usage_day=xano.today())]) == 0
    assert table["slack"] == []


def test_endpoint_did_you_mean_off_is_todays_not_found(table):
    j = ask("Skylands manor")
    assert j["lookup"]["status"] == "not_found" and j["venue_request"]["name"] == "Skylands manor"


def test_endpoint_elsewhere_says_where(table, monkeypatch):
    monkeypatch.setattr(config, "DID_YOU_MEAN", True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Location_Updated": ["New York"]})
    j = ask("Central park")
    assert j["lookup"]["status"] == "elsewhere" and "outside New York" in j["text"]
    assert j["cards"][0]["vendor_id"] == "A8"


def test_endpoint_chat_state_breaks_the_tie(table, monkeypatch):
    monkeypatch.setattr(config, "DID_YOU_MEAN", True)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    table["history"] = [{"role": "user", "text": "rooftops in Rhode Island"},
                        {"role": "assistant", "text": "Here are three.", "filters": {"states": ["Rhode Island"]}}]
    j = ask("The Sanctuary")
    assert j["cards"][0]["vendor_id"] == "A7"


def test_endpoint_guard_goes_to_the_model(table, monkeypatch):
    monkeypatch.setattr(config, "LOOKUP_GUARD", True)
    for text in ("Pricing", "im looking for a bar tender"):
        j = ask(text)
        assert j["lookup"] is None
    assert table["model"] == 2 and table["slack"] == []


def test_endpoint_slack_rule_with_the_venue_log(table, monkeypatch):
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 0)          # nothing written; the Slack rule still applies
    ask("Pricing")                                                  # guard off: still a lookup, but no notice
    ask("madonna inn")
    settle(table, "slack")
    assert [a[1] for a in table["slack"]] == ["Madonna Inn"]


def test_endpoint_slack_rule_off_posts_as_today(table):
    ask("Pricing")
    settle(table, "slack")
    assert [a[1] for a in table["slack"]] == ["Pricing"]


# ---------------------------------------------------------------- REC_LOOKUP_NO_LIMIT

AT_LIMIT = [{"status": "ok", "kind": "refine", "counted_as_free": True, "usage_day": "2026-10-26", "model": "m"}] * 3


def test_no_limit_free_couple_at_the_limit_still_gets_a_named_lookup(table, monkeypatch):
    monkeypatch.setattr(config, "LOOKUP_NO_LIMIT", True)
    table["forever"], table["access"] = False, False
    table["rows"] = list(AT_LIMIT) + [{"status": "ok", "kind": "refine", "counted_as_free": True,
                                       "usage_day": "2026-10-26", "model": "m"}] * 2
    j = ask("oheka castle")
    assert table["model"] == 0 and j["lookup"]["status"] == "found"
    settle(table, "logged")
    ok = [r for r in table["logged"] if r["status"] == "ok"]
    assert len(ok) == 1 and ok[0]["counted_as_free"] is False and ok[0]["model"] == "lookup"


def test_no_limit_off_blocks_as_today(table):
    table["forever"], table["access"] = False, False
    table["rows"] = list(AT_LIMIT) * 3
    r = TestClient(app.app).post("/rec/refine", json={"chat_id": 9, "message": "oheka castle"})
    assert r.status_code in (402, 403, 429) and table["model"] == 0


def test_no_limit_does_not_open_the_model(table, monkeypatch):
    monkeypatch.setattr(config, "LOOKUP_NO_LIMIT", True)
    table["forever"], table["access"] = False, False
    table["rows"] = list(AT_LIMIT) * 3
    r = TestClient(app.app).post("/rec/refine", json={"chat_id": 9, "message": "barn venues in Georgia"})
    assert r.status_code in (402, 403, 429) and table["model"] == 0


def test_no_limit_lookup_rows_never_count(monkeypatch):
    rows = [{"status": "ok", "kind": "refine", "counted_as_free": True, "usage_day": "2026-11-18", "model": "lookup",
             "tools": "lookup:found"}] * 5
    assert guards.free_used(rows) == 5 and guards.used_today(rows, "2026-11-18") == 5        # flag off: as today
    monkeypatch.setattr(config, "LOOKUP_NO_LIMIT", True)
    assert guards.free_used(rows) == 0 and guards.used_today(rows, "2026-11-18") == 0
    assert guards.free_asked(rows) == 5          # the rating card still sees them as asked


def test_no_limit_kill_switch_still_wins(table, monkeypatch):
    monkeypatch.setattr(config, "LOOKUP_NO_LIMIT", True)
    monkeypatch.setattr(config, "KILL_SWITCH", True)
    r = TestClient(app.app).post("/rec/refine", json={"chat_id": 9, "message": "oheka castle"})
    assert r.status_code == 503


# ---------------------------------------------------------------- REC_PAGE_VENUE

BEEKMAN = {"vendor_id": "V88", "vendor_idx": 4321, "name": "The Beekman", "state": "New York",
           "address": "123 Nassau St, New York, NY 10038"}


@pytest.fixture
def page(table, monkeypatch):
    seen = {"msgs": [], "idx": []}

    def run(token, user, access, messages, **kw):
        table["model"] += 1
        seen["msgs"].append(messages[-1]["content"])
        return ({"text": "model answer", "cards": [], "more_cards": [], "chips": []},
                {"model": "m", "input_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "output_tokens": 1, "tool_calls": 0, "latency_ms": 1})
    monkeypatch.setattr(agent, "run", run)
    monkeypatch.setattr(xano, "vendor_card_by_idx", lambda i: seen["idx"].append(i) or (BEEKMAN if str(i) == "4321" else None))
    return seen


def bubble(text, surface="bubble:/vendors/undefined?v=4321", chat_id=9):
    r = TestClient(app.app).post("/rec/refine", params={"surface": surface}, json={"chat_id": chat_id, "message": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_page_venue_names_the_vendor_for_the_model(page, monkeypatch):
    monkeypatch.setattr(config, "PAGE_VENUE", True)
    bubble("Tell me about this venue and whether it fits my wedding")
    assert page["msgs"][-1] == "Tell me about The Beekman in New York and whether it fits my wedding"
    bubble("Can we fit 150 guests here?")
    assert page["msgs"][-1] == "Can we fit 150 guests at The Beekman in New York?"


def test_page_venue_off_or_no_v_or_named_venue_unchanged(page, monkeypatch):
    bubble("Tell me about this venue and whether it fits my wedding")             # flag off
    assert page["msgs"][-1] == "Tell me about this venue and whether it fits my wedding"
    monkeypatch.setattr(config, "PAGE_VENUE", True)
    bubble("Tell me about this venue", surface="bubble:/vendors/undefined")       # no v=
    bubble("Tell me about this venue", surface="planner")
    assert page["msgs"][-2:] == ["Tell me about this venue"] * 2
    bubble("Does this one fit 100?")                                               # mid-chat "this one": a card shown
    assert page["msgs"][-1] == "Does this one fit 100?"


def test_page_venue_unknown_idx_leaves_the_text(page, monkeypatch):
    monkeypatch.setattr(config, "PAGE_VENUE", True)
    bubble("Tell me about this venue", surface="bubble:/vendors/undefined?v=999")
    assert page["msgs"][-1] == "Tell me about this venue"


# ================================================================ REC_VENUE_LOG

@pytest.fixture
def vlog(monkeypatch):
    writes = []

    def meta(method, path, **kw):
        writes.append((method, path, kw.get("json")))
        return {}
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 99)
    monkeypatch.setattr(xano, "_meta", meta)
    monkeypatch.setattr(venue_log, "WRONG_WINDOW_S", 0)
    monkeypatch.setitem(venue_log._STATE, "disabled", False)
    venue_log._PENDING.clear()
    yield writes
    venue_log.flush_all()


def test_lookup_writes_one_row(table, vlog):
    j = ask("oheka castle")
    assert j["lookup"]["status"] == "found"
    settle({"w": vlog}, "w")
    method, path, r = vlog[0]
    assert (method, path) == ("POST", "/table/99/content")
    assert r["source"] == "lookup" and r["outcome"] == "found" and r["best_vendor_id"] == "A10"
    assert r["typed"] == "oheka castle" and r["normalized"] == "oheka castle" and r["user_id"] == 7
    assert r["plan"] == "paid" and r["chat_id"] == 9 and isinstance(json.loads(r["alternatives"]), list)


def test_not_found_row_keeps_place_hints(table, vlog):
    ask("Do you have Cresthollow in Long Island, NY?")
    settle({"w": vlog}, "w")
    r = next(x[2] for x in vlog if isinstance(x[2], dict) and "outcome" in x[2])   # the miss text also reads benchmarks
    assert r["outcome"] == "not_found" and r["state_hint"] == "New York" and r["city_hint"] == "Long Island"


def test_log_off_writes_nothing(table, monkeypatch):
    writes = []
    monkeypatch.setattr(xano, "_meta", lambda *a, **k: writes.append(a))
    ask("oheka castle")
    assert writes == []


def test_missing_table_is_a_quiet_no_op(monkeypatch, capsys):
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 99)
    monkeypatch.setattr(venue_log, "WRONG_WINDOW_S", 0)
    monkeypatch.setitem(venue_log._STATE, "disabled", False)
    monkeypatch.setitem(venue_log._STATE, "said", set())
    calls = []

    def meta(*a, **k):
        calls.append(a)
        raise xano.XanoError("meta POST /table/99/content: 404 not found")
    monkeypatch.setattr(xano, "_meta", meta)
    for i in range(3):
        venue_log.record({"user_id": i, "typed": "x", "outcome": "found"})
    assert len(calls) == 1 and capsys.readouterr().out.count("not found") == 1


def test_no_table_id_writes_nothing(monkeypatch):
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 0)
    monkeypatch.setattr(venue_log, "WRONG_WINDOW_S", 0)
    monkeypatch.setattr(xano, "_meta", lambda *a, **k: pytest.fail("no write without a table id"))
    venue_log.record({"user_id": 1, "typed": "x", "outcome": "found"})


def test_a_retype_within_two_minutes_marks_the_first_answer_wrong(monkeypatch):
    writes = []
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 99)
    monkeypatch.setitem(venue_log._STATE, "disabled", False)
    monkeypatch.setattr(xano, "_meta", lambda m, p, **k: writes.append(k["json"]))
    venue_log._PENDING.clear()
    venue_log.record({"user_id": 5, "typed": "The Sanctuary", "outcome": "found"}, now=1000)
    venue_log.record({"user_id": 5, "typed": "The Sanctuary RI", "outcome": "best"}, now=1060)
    assert writes == [{"user_id": 5, "typed": "The Sanctuary", "outcome": "wrong", "answered_as": "found"}]
    venue_log.record({"user_id": 5, "typed": "Oheka Castle", "outcome": "found"}, now=1090)   # a different venue
    venue_log.record({"user_id": 6, "typed": "Crossed keys", "outcome": "choices"}, now=1000)
    venue_log.record({"user_id": 6, "typed": "Crossed Keys Estate", "outcome": "found"}, now=1010)  # picked from choices
    venue_log.flush_all()
    assert [w["outcome"] for w in writes] == ["wrong", "best", "found", "choices", "found"] or \
        sorted(w["outcome"] for w in writes) == sorted(["wrong", "best", "found", "choices", "found"])
    assert sum(1 for w in writes if w["outcome"] == "wrong") == 1


def test_searches_inside_one_answer_are_not_a_retype(monkeypatch):
    writes = []
    monkeypatch.setattr(config, "VENUE_LOG", True)
    monkeypatch.setattr(config, "VENUE_LOG_TABLE_ID", 99)
    monkeypatch.setitem(venue_log._STATE, "disabled", False)
    monkeypatch.setattr(xano, "_meta", lambda m, p, **k: writes.append(k["json"]))
    venue_log._PENDING.clear()
    venue_log.record({"user_id": 8, "typed": "Fox Theatre", "outcome": "found"}, now=1, req="r1")
    venue_log.record({"user_id": 8, "typed": "Fox Theatre", "outcome": "not_found"}, now=2, req="r1")
    venue_log.flush_all()
    assert "wrong" not in [w["outcome"] for w in writes]


def test_model_search_row():
    hit = {"keyword": "Kohl Mansion", "state": "California", "city": "",
           "results": [{"vendor_id": "V1675", "name": "Kohl Mansion", "state": "California"},
                       {"vendor_id": "V9", "name": "Kohl Gardens", "state": "California"}]}
    r = venue_log.from_model_search({"id": 3}, False, False, 0, "planner", "is kohl mansion nice?", hit)
    assert r["source"] == "model_search" and r["outcome"] == "found" and r["best_vendor_id"] == "V1675"
    assert r["plan"] == "free" and json.loads(r["alternatives"])[0]["vendor_id"] == "V9"
    miss = venue_log.from_model_search({"id": 3}, False, False, 0, "", "x", dict(hit, keyword="Zzyzx Hall"))
    assert miss["outcome"] == "not_found"


def test_model_search_with_a_name_is_recorded_by_the_agent(world, monkeypatch):
    monkeypatch.setattr(config, "VENUE_LOG", True)
    world["venues"] = [{"vendor_id": "V1", "name": "Tower Club Dallas", "state": "Texas", "address": "x",
                        "venue_fee_range": [1, 1]}]
    _, usage = world["run"]([("search_venues", {"states": ["Texas"], "keyword": "Tower Club"}), present(["V1"])])
    assert usage["venue_keywords"][0]["keyword"] == "Tower Club"
    _, usage = world["run"]([("search_venues", {"states": ["Texas"], "keyword": "Dallas"}), present(["V1"])])
    assert "venue_keywords" not in usage                      # a place is not a venue name
    monkeypatch.setattr(config, "VENUE_LOG", False)
    _, usage = world["run"]([("search_venues", {"states": ["Texas"], "keyword": "Tower Club"}), present(["V1"])])
    assert "venue_keywords" not in usage


# ================================================================ REC_FREE_BUDGET_FIT

@pytest.mark.parametrize("text,profile,want", [
    ("Anything under $10,000?", 0, 10000), ("anything under 10k", 0, 10000), ("fits my $30k budget", 0, 30000),
    ("Which of these fit our budget?", 45000, 45000), ("venues under 10000", 0, 10000),
    ("Which venues seat 150 guests?", 45000, 0), ("barn venues in Georgia", 45000, 0),
    ("under 150 guests please", 0, 0), ("a venue with a rental fee below $8,500", 0, 8500)])
def test_budget_target(text, profile, want):
    assert free_mode.budget_target(text, profile) == want


def test_budget_verdicts():
    v = free_mode.budget_verdict
    assert v(8000, 10000) == "in_budget" and v(10500, 10000) == "about_at_budget" and v(12000, 10000) == "over_budget"
    assert v(0, 10000) is None and v(9000, 0) is None
    assert free_mode.budget_basis("rental fee under $8k") == "rental"
    assert free_mode.budget_basis("Anything under $10,000?") == "all_in"


def test_free_text_keeps_verdicts_and_never_a_figure():
    text = ("The Beekman is in your budget. Grand Hall is over your budget. Oheka Castle is over your budget at "
            "$42,000. Ashton Gardens is about at your budget. Kohl Mansion fits your budget nicely. Open each pricing "
            "PDF for the exact prices.")
    off, _ = free_mode.sanitize_text(text)
    assert "Grand Hall" not in off                               # flag off: "over your budget" goes, as today
    on, removed = free_mode.sanitize_text(text, allow_verdicts=True)
    assert "Grand Hall is over your budget." in on and "Ashton Gardens is about at your budget." in on
    assert "The Beekman is in your budget." in on
    assert "$" not in on and "42" not in on and "fits your budget" not in on and removed == 2


def test_free_cards_and_tool_rows_carry_only_the_verdict():
    c = free_mode.card({"vendor_id": "V1", "name": "X", "venue_fee_range": [9000, 9000], "estimate_total": 31100,
                        "budget_fit": "in_budget"})
    assert c["budget_fit"] == "in_budget" and "venue_fee_range" not in c and "estimate_total" not in c
    assert "budget_fit" not in free_mode.card({"vendor_id": "V1", "budget_fit": "$9,000"})
    raw = json.dumps({"venues": [{"vendor_id": "V1", "name": "X", "venue_fee_range": [9000, 9000],
                                  "pricing": {"lines": [{"amount": 9000}]}, "budget_fit": "over_budget"}]})
    out = free_mode.venue_result(raw)
    assert json.loads(out)["venues"][0]["budget_fit"] == "over_budget" and "9000" not in out


def _free_run(world, monkeypatch, ask_text, rental, flag=True, detail="free"):
    monkeypatch.setattr(config, "FREE_BUDGET_FIT", flag)
    world["venues"] = [{"vendor_id": "V1", "name": "Cheap Hall", "state": "New York", "address": "1 Main St, Albany, NY",
                        "venue_fee_range": [1, 1]},
                       {"vendor_id": "V2", "name": "Grand Hall", "state": "New York", "address": "2 Main St, Albany, NY",
                        "venue_fee_range": [1, 1]}]
    world["rental"] = rental
    return world["run"]([("search_venues", {"states": ["New York"]}), present(["V1", "V2"])], ask=ask_text,
                        detail=detail)


def test_free_search_gets_verdicts_no_figures(world, monkeypatch):
    res, usage = _free_run(world, monkeypatch, "Any venues with a rental fee under $10,000?", {"V1": 6000, "V2": 25000})
    r = _search_result(world["log"])
    fits = {v["vendor_id"]: v.get("budget_fit") for v in r["venues"]}
    assert fits == {"V1": "in_budget", "V2": "over_budget"} and usage.get("budget_fit") is True
    blob = json.dumps(r) + json.dumps(free_mode.sanitize_result(res, allow_verdicts=True)["cards"])
    assert "6000" not in blob and "25000" not in blob and "6,000" not in blob and "25,000" not in blob


def test_free_search_all_in_without_an_estimate_only_says_over(world, monkeypatch):
    _free_run(world, monkeypatch, "Anything under $10,000?", {"V1": 6000, "V2": 25000})
    fits = {v["vendor_id"]: v.get("budget_fit") for v in _search_result(world["log"])["venues"]}
    assert fits == {"V1": None, "V2": "over_budget"}             # a rental under the number proves nothing all-in


def test_free_budget_fit_off_is_today(world, monkeypatch):
    _, usage = _free_run(world, monkeypatch, "Any venues with a rental fee under $10,000?", {"V1": 6000}, flag=False)
    assert all("budget_fit" not in v for v in _search_result(world["log"])["venues"]) and "budget_fit" not in usage


def test_full_members_never_get_verdicts(world, monkeypatch):
    _, usage = _free_run(world, monkeypatch, "Any venues with a rental fee under $10,000?", {"V1": 6000}, detail="full")
    assert all("budget_fit" not in v for v in _search_result(world["log"])["venues"]) and "budget_fit" not in usage
