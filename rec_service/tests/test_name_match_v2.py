"""Name match v2 (config.NAME_MATCH_V2, 2026-11-18; #feedback 2026-11-17).

1. Chat 2544 (Forever couple): "Holly Hedge" and "Holly Hedge Estate" were answered "Venue not in Tulle" (closest: The
   Holly Farm, The Holly Vault, The Vineyard at 37 High Holly). The listing is "HollyHedge Estate" (Pennsylvania),
   checked read-only through ep119 on 2026-11-18: "holly hedge" / "holly" / "hedge" never return it (the search matches
   whole words), "hollyhedge" does.
2. Chat 2565 (free couple): two area constraints were read as venue names and posted as missing venues.
Flag off = lookup.py exactly as before."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
import config  # noqa: E402
import lookup  # noqa: E402
from test_name_lookup import env, ask, settle, row  # noqa: E402,F401

MIAMI_1 = "Stick to the Miami area, no further north than Davie. No further south than Homestead."
MIAMI_2 = ("The cities include Davie, Miramar, Pembroke Pines, Hialeah, Miami, Hollywood, Wynwood, doral, Kendall, "
           "West Kendall")

TABLE = [
    row("V7001", "HollyHedge Estate", "Pennsylvania", "6987 Upper York Rd, New Hope, PA 18938"),
    row("V7002", "The Holly Farm", "California", "1 Farm Rd, Carmel, CA 93923"),
    row("V7003", "The Holly Vault", "Michigan", "2 Vault St, Detroit, MI 48226"),
    row("V7004", "The Vineyard at 37 High Holly", "North Carolina", "37 High Holly Rd, Cleveland, NC 27013"),
    row("V7005", "Hollyhedgerow Barn", "Ohio", "3 Row Rd, Akron, OH 44308"),
    row("V7006", "Area Four", "Massachusetts", "500 Technology Sq, Cambridge, MA 02139"),
    row("V7007", "Old Davie School Historical Museum", "Florida", "6650 Griffin Rd, Davie, FL 33314"),
]
COMBINED = TABLE + [row("V7008", "Holly Hedge Estate & Gardens at Solebury", "Pennsylvania",
                        "9 Garden Ln, Solebury, PA 18963")]


def word_search(table):
    """Like xano.name_search: every WORD of the term must be a whole word of the name (popular first)."""
    def search(term):
        return [dict(r) for r in table if all(w in lookup.tokens(r["name"]) for w in term.split())]
    return search


def look(text, v2, table=TABLE):
    q = lookup.detect(text, v2=v2)
    assert q, text
    return lookup.resolve(q, lookup.find(q, word_search(table)))


# ---------------------------------------------------------------- 1. the joined name

def test_flag_off_reproduces_the_report():
    for text in ("Holly Hedge", "Holly Hedge Estate"):
        res = look(text, v2=False)
        assert res["status"] == "not_found"
        assert set(res["near"]) <= {"The Holly Farm", "The Holly Vault", "The Vineyard at 37 High Holly"}


@pytest.mark.parametrize("text", ["Holly Hedge", "Holly Hedge Estate", "holly hedge estate pricing",
                                  "Do you have Holly-Hedge Estate?", "HOLLY HEDGE"])
def test_flag_on_finds_hollyhedge_estate(text):
    res = look(text, v2=True)
    assert res["status"] == "found", res
    assert res["matches"][0]["vendor_id"] == "V7001"
    assert "hollyhedge" in res["queries"]


def test_typed_words_inside_a_longer_stored_name():
    # The words typed, in order, as whole words of a longer listing name.
    res = look("Holly Hedge", v2=True, table=[r for r in COMBINED if r["vendor_id"] != "V7001"])
    assert res["status"] == "found" and res["matches"][0]["vendor_id"] == "V7008"


def test_both_spellings_listed_are_both_offered_not_missing():
    res = look("Holly Hedge", v2=True, table=COMBINED)
    assert res["status"] in ("choices", "best")
    assert {m["vendor_id"] for m in res["matches"][:2]} == {"V7001", "V7008"}


def test_joined_match_rule():
    jm = lookup.joined_match
    assert jm("Holly Hedge", "HollyHedge Estate")
    assert jm("Holly Hedge Estate", "HollyHedge Estate")
    assert jm("holly hedge", "Holly Hedge Estate & Gardens at Solebury")
    assert jm("the holly hedge", "The HollyHedge")
    # whole words only: no partial word at either end
    assert not jm("Holly Hedge", "Hollyhedgerow Barn")
    assert not jm("Olly Hedge", "HollyHedge Estate")
    assert not jm("Holly Hedge", "The Holly Farm")
    # short / generic inputs never match: fewer than 2 distinctive words, or under 6 letters joined
    for q in ("The", "Estate", "Barn", "The Barn", "Holly", "Holly Estate", "The Barn at", "Estate Gardens",
              "Hotel Inn", "A B", "Ab Cd"):
        assert not jm(q, "HollyHedge Estate"), q
        assert not jm(q, "The Barn at Forest Hill"), q
        assert not jm(q, "Ab Cd Estate"), q


def test_scores_flag_off_identical_flag_on_only_lifts_joined():
    pairs = [("Holly Hedge", "The Holly Farm"), ("oheka castle", "Oheka Castle"), ("Khol mansion", "Kohl Mansion"),
             ("Ashton gardens", "Ashton Farm"), ("Estate", "HollyHedge Estate"), ("The Barn", "The Barn at Forest Hill")]
    for q, n in pairs:
        assert lookup.score(q, n) == lookup.score(q, n, v2=True), (q, n)
        assert lookup.strong(q, n) == lookup.strong(q, n, v2=True), (q, n)
    assert lookup.score("Holly Hedge", "HollyHedge Estate") < 0.1
    assert lookup.score("Holly Hedge", "HollyHedge Estate", v2=True) == 0.9


def test_first_queries_flag_off_identical():
    for n in ("Holly Hedge Estate", "royal palm mansion", "The Harold Pratt House and Peterson Hall", "oheka"):
        off, on = lookup.first_queries(n), lookup.first_queries(n, v2=True)
        assert on[:min(len(off), 5)] == off[:5] and len(on) <= 6   # v2 only adds (or swaps in as the 6th) the joined term
        assert all(" " not in t for t in set(on) - set(off))
    assert lookup.first_queries("Holly Hedge Estate", v2=True)[-1] == "hollyhedge"
    assert "hollyhedge" not in lookup.first_queries("Holly Hedge Estate")


# ---------------------------------------------------------------- 2. area constraints

@pytest.mark.parametrize("text", [MIAMI_1, MIAMI_2])
def test_miami_messages_are_not_venue_lookups(text):
    assert lookup.detect(text) is not None            # today: read as a venue name (the bug)
    assert lookup.detect(text, v2=True) is None       # v2: not a lookup


@pytest.mark.parametrize("text", [
    "Within 30 miles of Austin", "no farther south than Savannah", "keep it within an hour of Boston",
    "Houston area.", "towns like Sonoma, Napa", "stay close to Charlotte", "20 minutes from downtown Denver",
    "Davie, Miramar, Pembroke Pines, Hialeah and Doral",
])
def test_other_area_constraints(text):
    assert lookup.area_request(text)


@pytest.mark.parametrize("text", ["Area Four", "Bay Area Discovery Museum pricing", "Holly Hedge Estate",
                                  "oheka castle", "The Barn at Forest Hill", "Bacchus, Brooklyn",
                                  "Is Il Sereno, the hotel in Torno on Lake Como, Italy, listed in Tulle's vendor "
                                  "directory? What is its pricing?", "North Fork Table pricing"])
def test_real_names_are_not_area_requests(text):
    assert not lookup.area_request(text)
    assert lookup.detect(text, v2=True) is not None


# ---------------------------------------------------------------- the endpoint

@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(config, "NAME_MATCH_V2", True)


def test_endpoint_finds_hollyhedge_for_a_paying_couple(env, v2):
    env["search"] = word_search(TABLE)
    j = ask("Holly Hedge Estate")
    assert env["model"] == 0 and j["lookup"]["status"] == "found" and j["venue_request"] is None
    assert j["cards"][0]["vendor_id"] == "V7001" and "HollyHedge Estate" in j["text"]
    assert env["slack"] == []


def test_endpoint_flag_off_still_reports_it_missing(env, monkeypatch):
    monkeypatch.setattr(config, "NAME_MATCH_V2", False)
    env["search"] = word_search(TABLE)
    j = ask("Holly Hedge Estate")
    assert j["lookup"]["status"] == "not_found" and j["venue_request"]["name"] == "Holly Hedge Estate"


def test_endpoint_free_couple_found_gets_no_price(env, v2):
    env["search"] = word_search(TABLE)
    env["forever"], env["access"] = False, False
    j = ask("Holly Hedge")
    assert env["model"] == 0 and j["cards"][0]["vendor_id"] == "V7001"
    assert "venue_fee_range" not in j["cards"][0] and "$" not in j["text"]


@pytest.mark.parametrize("text", [MIAMI_1, MIAMI_2])
def test_endpoint_area_message_goes_to_the_model_for_a_free_couple(env, v2, text):
    env["search"] = word_search(TABLE)
    env["forever"], env["access"] = False, False
    j = ask(text)
    assert env["model"] == 1                          # the model's area search (its answer goes through free_mode)
    assert j["lookup"] is None and j["venue_request"] is None
    assert env["slack"] == [] and not any(ev == "rec_venue_request" for ev, _ in env["mp"])
