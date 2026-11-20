"""#feedback trends (2026-11-20): the post classifier, "Venue not in Tulle" triage + add-vendor card, the weekly
read-only digest, and the paginated history read. Post texts are shaped like the real webhook posts read from
#feedback on 2026-11-20 (addresses replaced with made-up ones)."""
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import appliers
import cards
import feedback_watch
import scheduler
import trends
from fakes import FakeClaude, FakeSlack
from proposals import Store
from state import State


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network write attempted in a test")
    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests.Session, "post", boom)
    for k in ("TASK_DRY_RUN", "FEEDBACK_CHANNEL", "FEEDBACK_WATCH", "FEEDBACK_VENUE_REQUESTS", "TREND_DIGEST_CHANNEL",
              "QA_APPROVERS", "DEDUPE_CHANNEL"):
        monkeypatch.delenv(k, raising=False)


def V(i, vid, name, addr="1 Main St, Austin, TX 78701", state="Texas", vis="1"):
    return {"id": i, "Vendor_ID": vid, "Name": name, "Address": addr, "State": state, "Validated_Data": vis,
            "Category": "Venue"}


ROWS = [V(1, "V1422", "HollyHedge Estate", "88 Hollyhedge Rd, New Hope, PA 18938", "Pennsylvania"),
        V(2, "V1137", "Fox Theatre", "660 Peachtree St NE, Atlanta, GA 30308", "Georgia"),
        V(3, "V2900", "Bear Brook Valley", "1 Bear Brook Rd, Fredon, NJ 07860", "New Jersey"),
        V(4, "V3999", "Water Works - Mill House Deck and Engine House", "640 Waterworks Dr, Philadelphia, PA",
          "Pennsylvania"),
        V(5, "V700", "The Savoy", "1 Strand, Miami, FL 33139", "Florida"),
        V(6, "V701", "Savoy Palace", "2 Strand, Funchal", "Portugal"),
        V(7, "V172", "Arrow Park", "1 Arrow Park Rd, Monroe, NY 10950", "New York"),
        V(8, "V800", "Cipriani", "25 Broadway, New York, NY 10004", "New York"),
        V(9, "V900", "Skylands Manor at the New Jersey State Botanical Garden", "5 Morris Rd, Ringwood, NJ 07456",
          "New Jersey", vis="0")]


def vreq(name, user=91000, loc="", email="couple1@example.com", chat=1447):
    where = f" ({loc})" if loc else ""
    return (f"*Venue not in Tulle* - A couple (<mailto:{email}|{email}>, user {user}, free) asked for *{name}*{where}\n"
            f"_Closest names we have: Something, Else_\n_chat {chat}_")


# --- classifier --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,cat", [
    (vreq("Park savoy"), "venue_request"),
    ("Lau (<mailto:x1@example.com|x1@example.com>)  reported an issue with Farmhouse Inn (V15385) .\n Details: This isnt "
     "a PDF for farmhouse inn. Wrong venue attached", "wrong_pdf"),
    ("(<mailto:x2@example.com|x2@example.com>)  reported an issue with Holiday Inn St Louis SW (V6755) .\n Details: the "
     "pricing is for different venue", "wrong_pdf"),
    ("Sydney (<mailto:x3@example.com|x3@example.com>)  reported an issue with Deer Path Inn (V4536) .\n Details: The PDF "
     "attached to Deer Path Inn is actually the pdf for a different golf venue (Bull Valley Golf Club)", "wrong_pdf"),
    ("Taylor (x4@example.com)  reported an issue with The Club at Corazon (V6789) .\n Details: No pricing", "no_pricing_in_pdf"),
    ("(x5@example.com)  reported an issue with Springbrook Events (V19443) .\n Details: incomplete pricing - menu only",
     "no_pricing_in_pdf"),
    ("Jessica (x6@example.com)  reported an issue with The Mill Lakeside Manor (V3378) .\n Details: primary document "
     "opens up to a blank black screen on google drive", "pdf_wont_open"),
    ("L (x7@example.com)  reported an issue with undefined (V2072) .\n Details: Link is broken", "pdf_wont_open"),
    ("Molly (x8@example.com)  reported an issue with undefined (V1052) .\n Details: This is 2025 pricing, not 2027",
     "outdated_pricing"),
    ("(x9@example.com)  reported an issue with 650 North Avenue NE (V8022) .\n Details: Cropped document", "cropped_pdf"),
    ("Kate (x10@example.com)  reported an issue with Casa do Adro (V5699) .\n Details: The pricing intelligence is super "
     "messed up here because it's double counting the food and beverage and rental fee", "pricing_estimate_wrong"),
    ("Erin (x11@example.com)  reported an issue with The Grand Ivory (V3219) .\n Details: Location is pinning to Cape May "
     "New Jersey, but this venue is in Texas", "wrong_location"),
    ("Kate (x12@example.com)  reported an issue with USC University Club (V10487) .\n Details: duplicate of University "
     "of Southern California listing", "duplicate_listing"),
    ("Kailey (x13@example.com)  reported an issue with Vignette Gardens (V3847) .\n Details: they closed", "venue_closed"),
    (":rotating_light: Rating 1/5 - detractor | paid | <mailto:x14@example.com|x14@example.com> | Venue Vendor Page\n"
     "> I was told I would get real numbers", "nps_detractor"),
    (":star: Rating 5/5 - promoter | paid | x15@example.com | Venue Vendor Page\n_(did not share)_", "nps_promoter"),
    (":thinking_face: *What stopped you:* It costs more than I want to pay | free | x16@example.com | Emeline (V965)",
     "paywall_reason"),
    (":door: *Paywall walk-away:* tapped Maybe later on the PDF limit wall | free | x17@example.com | Loft (V1)",
     "paywall_walkaway"),
    ("*Tulle Assistant beta feedback* - A member (user 91005, 4 weeks)\n*Useful so far:* 1/5", "planner_feedback"),
    ("*Tulle Assistant thumbs down* - A member (user 91006, free)\n> not in dallas texas", "planner_thumbs_down"),
    ("Alyssa (x18@example.com)  reported a 8/10 rated issue/request. \n Category: Features\n Details: filter by how late",
     "feature_request"),
    ("Elle (x19@example.com)  reported a 10/10 rated issue/request. \n Category: Existing Issue\n Details: I'd like a "
     "refund.", "refund_request"),
    ("Name: Shannon\nEmail: x20@example.com\nMessage: you listed the price for the Courtyard as", "support_message"),
    ("Name: \nEmail: \nMessage: ", None),
    ("oh thats a good edit at least", None),
])
def test_classify(text, cat):
    assert trends.classify(text) == cat


def test_parse_venue_request_both_formats():
    old = "*Venue not in Tulle* - A couple (user 91004, free) asked for *Murrells inlet*\n_chat 644_"
    assert trends.parse_venue_request(old) == {"name": "Murrells inlet", "loc": "", "user_id": 91004, "email": ""}
    new = trends.parse_venue_request(vreq("Le Force DJ", loc="dallas, Texas", user=91003))
    assert new["name"] == "Le Force DJ" and new["loc"] == "dallas, Texas" and new["user_id"] == 91003
    assert trends.parse_venue_request("Kate: we do have this one") is None


@pytest.mark.parametrize("name", ["Ceremony only", "Houston And Serounding Areas", "Texas city", "Not new york",
                                  "Is That Inclusive", "Pricing", "Algonquin Illinois", "Only gardens and nature please",
                                  "Nevermind - Disregard", "Hotel Buyout", "Estate Multi Day", "Im Looking For A Bar Tender",
                                  "It’s stupid", "And one with a rooftop"])
def test_not_a_venue(name):
    assert trends.not_a_venue(name)


@pytest.mark.parametrize("name", ["Park savoy", "Virgin hotel NYC", "Le Force DJ", "The Pricing For The Sapphire Estate",
                                  "Woods And Co Pepin Minnesota", "Rosewood Bermuda", "Madonna Inn", "Ben Conger Inn"])
def test_is_a_venue(name):
    assert not trends.not_a_venue(name)


def test_clean_request_drops_leading_filler():
    assert trends.clean_request("The Pricing For The Sapphire Estate") == "Sapphire Estate"
    assert trends.clean_request("Is Il Sereno on Lake Como listed?") == "Il Sereno on Lake Como listed"


@pytest.mark.parametrize("name,loc,vid", [("Holly Hedge Estate", "", "V1422"), ("Fox Theatre Atl", "", "V1137"),
                                          ("Bear brook nj", "", "V2900"), ("Water works", "", "V3999"),
                                          ("Skylands manor", "", "V900")])
def test_match_vendor_finds_listed_under_another_spelling(name, loc, vid):
    have, _ = trends.match_vendor(name, loc, ROWS)
    assert [r["Vendor_ID"] for r in have] == [vid]


@pytest.mark.parametrize("name", ["Park savoy", "Virgin hotel NYC", "Cipriani south street", "Rosewood Bermuda"])
def test_match_vendor_really_missing(name):
    have, closest = trends.match_vendor(name, "", ROWS)
    assert have == [] and len(closest) == 3


def test_request_key_ignores_place_words_and_case():
    assert trends.request_key("Virgin Hotels NYC") == trends.request_key("virgin hotels") == "vreq:virginhotels"


# --- "Venue not in Tulle" in the watcher ----------------------------------------------------------------------
class RecSlack(FakeSlack):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls, self._n = [], 0

    def _call(self, method, http="GET", **params):
        if method in ("chat.postMessage", "chat.update", "chat.postEphemeral"):
            self.calls.append((method, params))
            self._n += 1
            return {"ok": True, "ts": f"1700000000.{self._n:06d}", "channel": params.get("channel")}
        return super()._call(method, http, **params)

    def of(self, method):
        return [p for m, p in self.calls if m == method]


class NoLLM(FakeClaude):
    def propose_fix(self, post):
        raise AssertionError("venue requests must not call the model")


class Dir:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        return self._rows


def ev(text, ts="1700.1"):
    return {"type": "message", "subtype": "bot_message", "bot_id": "BTB", "channel": "CFEED", "ts": ts, "text": text}


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    slack, store = RecSlack(), Store(Path(tempfile.mkdtemp()) / "proposals.json")
    return feedback_watch.FeedbackWatcher(slack, NoLLM(), State(), store, xano=None, directory=Dir(ROWS)), slack, store


def test_missing_vendor_gets_one_add_vendor_card(w):
    watcher, slack, store = w
    assert watcher.handle(ev(vreq("Park savoy", user=91001, email="couple.one@example.com"))) == \
        "feedback:venue-request:proposed"
    msg = slack.of("chat.postMessage")[0]
    assert msg["thread_ts"] == "1700.1" and cards.validate(msg["blocks"]) == []
    body = json.dumps(msg["blocks"])
    assert "Add vendor: Park savoy" in body and "Approve and apply" in body and "Reject" in body
    assert "Dry run is on" in body                                   # task applier defaults to dry run
    assert "Closest names we have" in body and "The Savoy (Miami, Florida)" in body
    assert "couple.one" not in body and "@example.com" not in body and "91001" not in body   # no names/emails/ids
    assert "couple.one" not in json.dumps(store._data)
    p = store.by_key("vreq:parksavoy")[0]
    assert p["kind"] == "task" and p["dry_run"] is True
    res = appliers.task(p["payload"], "U05UJMCHUV6", p["id"], dry_run=p["dry_run"])
    assert res["ok"] and "Dry run" in res["text"] and "Source pricing: Park savoy" in res["text"]


def test_repeat_request_counts_and_points_to_first_card(w):
    watcher, slack, store = w
    watcher.handle(ev(vreq("Park savoy", user=91001), ts="1700.1"))
    assert watcher.handle(ev(vreq("Park Savoy", user=91002), ts="1800.1")) == "feedback:venue-request:repeat"
    assert watcher.handle(ev(vreq("park savoy", user=91002), ts="1900.1")) == "feedback:venue-request:repeat"
    posts = slack.of("chat.postMessage")
    assert sum(1 for p in posts if p.get("blocks")) == 1                       # still one card
    assert "Asked again: 3 requests from 2 couples so far" in posts[-1]["text"]
    assert "slack.com/archives/CFEED/p1700000000000001" in posts[-1]["text"]
    assert watcher.handle(ev(vreq("Park savoy"), ts="1800.1")) == "feedback:venue-request:seen"


def test_rejected_vendor_is_never_suggested_again(w):
    watcher, slack, store = w
    watcher.handle(ev(vreq("Park savoy"), ts="1700.1"))
    p = store.by_key("vreq:parksavoy")[0]
    store.transition(p["id"], {"pending"}, "rejected", "U05UJMCHUV6")
    n = len(slack.of("chat.postMessage"))
    assert watcher.handle(ev(vreq("Park savoy", user=1), ts="1800.1")) == "feedback:venue-request:rejected-before"
    assert len(slack.of("chat.postMessage")) == n


def test_listed_vendor_gets_a_note_not_a_card(w):
    watcher, slack, store = w
    assert watcher.handle(ev(vreq("Holly Hedge Estate", loc="New Hope, PA"))) == "feedback:venue-request:listed"
    msg = slack.of("chat.postMessage")[0]
    assert "blocks" not in msg and msg["thread_ts"] == "1700.1"
    assert "We already list *HollyHedge Estate* (New Hope, Pennsylvania)" in msg["text"]
    assert watcher.handle(ev(vreq("Skylands manor nj"), ts="1800.1")) == "feedback:venue-request:listed"
    assert "hidden from search right now" in slack.of("chat.postMessage")[-1]["text"]
    assert watcher.handle(ev(vreq("Fox Theatre Atl"), ts="1900.1")) == "feedback:venue-request:listed"
    assert store._data["proposals"] == {}


def test_same_name_without_or_with_other_place_still_gets_the_card(w):
    watcher, slack, store = w
    assert watcher.handle(ev(vreq("Holly Hedge Estate"))) == "feedback:venue-request:proposed"     # no place given
    body = json.dumps(slack.of("chat.postMessage")[-1]["blocks"])
    assert "Possible match we already list: HollyHedge Estate (New Hope, Pennsylvania)" in body
    assert watcher.handle(ev(vreq("Fox Theatre", loc="Detroit, Michigan"), ts="1800.1")) == \
        "feedback:venue-request:proposed"                                                      # another city
    assert "Possible match we already list: Fox Theatre (Atlanta, Georgia)" in \
        json.dumps(slack.of("chat.postMessage")[-1]["blocks"])


def test_request_made_only_of_place_words_keeps_a_key(w):
    watcher, slack, store = w
    assert trends.request_key("Brooklyn", "Brooklyn") == "vreq:brooklyn"
    assert trends.request_key("New Jersey") == "vreq:newjersey"
    assert all(k != "vreq:" for k in (trends.request_key("NY", "ny"), trends.request_key("Atl")))
    assert trends.request_key("!!!") == "vreq:"
    assert watcher.handle(ev(vreq("Brooklyn", loc="Brooklyn"))) == "feedback:venue-request:proposed"
    assert store.by_key("vreq:brooklyn")


def test_thread_notes_count_toward_daily_cap(w, monkeypatch):
    watcher, slack, store = w
    monkeypatch.setenv("FEEDBACK_DAILY_CAP", "2")
    assert watcher.handle(ev(vreq("Park savoy", user=1), ts="1700.1")).endswith("proposed")          # 1 card
    assert watcher.handle(ev(vreq("Park savoy", user=2), ts="1800.1")) == "feedback:venue-request:repeat"  # 1 note
    n = len(slack.of("chat.postMessage"))
    assert watcher.handle(ev(vreq("Park savoy", user=3), ts="1900.1")) == "feedback:daily-cap"
    assert watcher.handle(ev(vreq("Bear brook nj", user=4), ts="2000.1")) == "feedback:daily-cap"
    assert watcher.handle(ev(vreq("Virgin hotel NYC", user=5), ts="2100.1")) == "feedback:daily-cap"
    assert len(slack.of("chat.postMessage")) == n
    assert store.meta("vreq:parksavoy")["n"] == 3                         # still counted, just not announced


def test_not_a_venue_and_internal_post_nothing(w, monkeypatch):
    watcher, slack, store = w
    assert watcher.handle(ev(vreq("Ceremony only"))) == "feedback:venue-request:not-a-venue"
    assert watcher.handle(ev(vreq("Park savoy", user=35527), ts="1800.1")) == "feedback:venue-request:internal"
    monkeypatch.setenv("INTERNAL_EMAIL_PREFIXES", "teamtest")
    assert watcher.handle(ev(vreq("Park savoy", user=0, email="teamtest9@example.com"), ts="1900.1")) == \
        "feedback:venue-request:internal"
    assert slack.of("chat.postMessage") == [] and store._data["proposals"] == {}


def test_venue_requests_switch_and_watch_flag(w, monkeypatch):
    watcher, slack, store = w
    monkeypatch.setenv("FEEDBACK_WATCH", "0")
    assert watcher.handle(ev(vreq("Park savoy"))) == "feedback:watch-off"
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    monkeypatch.setenv("FEEDBACK_VENUE_REQUESTS", "0")
    with pytest.raises(AssertionError, match="must not call the model"):     # falls through to the normal path
        watcher.handle(ev(vreq("Park savoy"), ts="1800.1"))


def test_daily_cap_applies_to_add_vendor_cards(w, monkeypatch):
    watcher, slack, store = w
    monkeypatch.setenv("FEEDBACK_DAILY_CAP", "1")
    assert watcher.handle(ev(vreq("Park savoy"), ts="1700.1")).endswith("proposed")
    assert watcher.handle(ev(vreq("Virgin hotel NYC"), ts="1800.1")) == "feedback:daily-cap"


# --- weekly digest -------------------------------------------------------------------------------------------
NOW = datetime(2026, 11, 23, 14, tzinfo=timezone.utc).timestamp()


def msg(text, days_ago):
    return {"type": "message", "subtype": "bot_message", "ts": f"{NOW - days_ago * 86400:.6f}", "text": text}


def issue(vendor, vid, details, who="x@example.com"):
    return f"A ({who})  reported an issue with {vendor} ({vid}) .\n Details: {details}"


HISTORY = [
    msg(vreq("Park savoy", user=1), 1), msg(vreq("Park Savoy", user=2), 2), msg(vreq("Park savoy", user=2), 2.5),
    msg(vreq("Virgin hotel NYC", user=3), 3), msg(vreq("Holly Hedge Estate", user=4), 3),
    msg(vreq("Ceremony only", user=5), 4), msg(vreq("Rosewood Bermuda", user=6), 20),
    msg(vreq("Park savoy", user=35527), 1),                                         # Des's test account: left out
    msg(issue("Chateau des Fleurs", "V16868", "No pricing"), 2), msg(issue("Chateau des Fleurs", "V16868", "No price"), 18),
    msg(issue("Deer Path Inn", "V4536", "pricing is for different venue"), 2),
    msg(issue("Casa do Adro", "V5699", "double counting the food and beverage"), 1),
    msg(issue("Bellevue Hall", "V7265", "wrong venue"), 9), msg(issue("The Grainary", "V18209", "incomplete"), 10),
    msg(":rotating_light: Rating 2/5 - detractor | paid | y@example.com | Venue Vendor Page\n> Not many vendors", 1),
    msg(":star: Rating 5/5 - promoter | paid | z@example.com | Venue Vendor Page", 1),
    msg(":thinking_face: *What stopped you:* It costs more than I want to pay | free | q@example.com | X (V1)", 1),
    msg(":thinking_face: *What stopped you:* It costs more than I want to pay | free | r@example.com | X (V1)", 2),
    msg("Kate: ugh that is annoying", 1),
    msg(vreq("Old request", user=7), 40),                                           # outside 28 days
]


def test_digest_counts_and_lists():
    t = trends.build_digest(HISTORY, now=NOW, rows=ROWS)
    assert "*Venue not in Tulle:* 6 (+6 vs the week before): 5 we don't list, 0 we already list under another name, " \
           "1 not a venue name" in t                                  # "Holly Hedge Estate" names no place: not confirmed
    assert "*PDF problems:* 2 (same vs the week before)" in t and "1 wrong pdf attached" in t
    assert "*Pricing estimate wrong:* 1 (+1 vs the week before)" in t
    assert "1 promoters, 1 detractors" in t and "*Paywall, what stopped them:* costs too much 2" in t
    assert "It costs more than I want to pay" not in t                 # labels + counts only, never the post text
    top = t.split("Most-requested vendors we don't list (last 28 days)*")[1]
    assert top.index("Park savoy: 2 couples, asked 3 times") < top.index("Virgin hotel NYC: 1 couple")
    assert "Rosewood Bermuda: 1 couple" in top and "Ceremony" not in top
    t2 = trends.build_digest(HISTORY + [msg(vreq("Bear brook nj", user=8), 1)], now=NOW, rows=ROWS)
    assert "1 we already list under another name" in t2 and "Bear brook" not in t2
    assert "Old request" not in t
    assert "Chateau des Fleurs (V16868): 2 reports" in t
    assert "Team and test accounts left out (1 posts)" in t
    assert "@example.com" not in t and "Kate" not in t and len(t) <= 3900


def test_paywall_label_maps_fixed_options_only():
    assert trends.paywall_label("It costs more than I want to pay") == "costs too much"
    assert trends.paywall_label("The vendor I want isn't on Tulle") == "vendor not on Tulle"
    assert trends.paywall_label("my cousin Jane said no") == "other"


def test_digest_without_vendor_list_says_so():
    t = trends.build_digest(HISTORY, now=NOW, rows=None)
    assert "5 look like vendor names (not checked against our list), 1 not a venue name" in t


def test_run_digest_reads_history_and_posts_sanitized(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")

    class B:
        slack = RecSlack(history={"CFEED": HISTORY})
        xano = type("X", (), {"enabled": False})()
    assert trends.run_digest(B(), now=NOW) == "trends:off"
    monkeypatch.setenv("TREND_DIGEST_CHANNEL", "CGROWTH")
    assert trends.run_digest(B(), now=NOW).endswith(":ok")
    post = B.slack.of("chat.postMessage")[-1]
    assert post["channel"] == "CGROWTH" and "#feedback trends" in post["text"] and "read-only" in post["text"]
    assert "@example.com" not in post["text"] and "thread_ts" not in post


def test_history_all_follows_cursors():
    pages = {None: {"ok": True, "messages": [{"ts": "3"}, {"ts": "2"}], "response_metadata": {"next_cursor": "c1"}},
             "c1": {"ok": True, "messages": [{"ts": "1"}], "response_metadata": {"next_cursor": ""}}}
    seen = []

    class S(FakeSlack):
        def _call(self, method, http="GET", **params):
            seen.append(params.get("cursor"))
            return pages[params.get("cursor")]
    assert [m["ts"] for m in S().history_all("C", days=28, max_pages=5)] == ["1", "2", "3"]
    assert seen == [None, "c1"]
    seen.clear()
    assert len(S().history_all("C", max_pages=1)) == 2 and seen == [None]


def test_scheduler_posts_digest_weekly_only(monkeypatch):
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("TREND_DIGEST_CHANNEL", "CGROWTH")
    ran = []
    monkeypatch.setattr(trends, "run_digest", lambda bot, now=None: ran.append(now) or "trends:x")

    class B:
        store = Store()
    mon = scheduler.Scheduler(B(), clock=lambda: datetime(2026, 11, 23, 15, tzinfo=timezone.utc))     # Monday
    assert mon.tick() == ["trends:x"] and mon.tick() == []
    early = scheduler.Scheduler(type("B2", (), {"store": Store(Path(tempfile.mkdtemp()) / "p.json")})(),
                                clock=lambda: datetime(2026, 11, 23, 10, tzinfo=timezone.utc))
    assert early.tick() == []                                          # before TREND_DIGEST_HOUR_UTC
    tue = scheduler.Scheduler(type("B3", (), {"store": Store(Path(tempfile.mkdtemp()) / "p.json")})(),
                              clock=lambda: datetime(2026, 11, 24, 15, tzinfo=timezone.utc))
    assert tue.tick() == [] and len(ran) == 1
