"""QA round 4 (2026-11-20): one email sanitizer at the Slack-send and store-write boundaries, loser undo, blanks,
UTC dates, partial state survives a failed/dry-run Undo or Resume, Details claim expiry."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import cards
import dedupe
import interactions
import sanitize
from proposals import Store
from test_v2 import OWNER, RecSlack, V, click
from test_v2_owner import ROWS, FakeX

PROBES = ["bride@gmail.com", "name at gmail dot com", "info(at)hall.com", "x [at] hall [dot] com",
          "kate＠hall．com", "Sales {at} plaza.com", "events @ theplaza . com"]


@pytest.fixture(autouse=True)
def _allow_hidden_members(monkeypatch):
    """The shared fixture pair has a hidden member on purpose (visibility plan, undo, cards). The scan holds such pairs
    since 2026-11-22 (tested in test_merge_scan.py); these tests are about what a card does once it exists."""
    monkeypatch.setattr(dedupe, "HOLD_HIDDEN", False)


@pytest.mark.parametrize("probe", PROBES)
def test_sanitizer_catches_each_probe(probe):
    out = sanitize.scrub_text(f"call {probe} today")
    assert "[email]" in out and not sanitize.has_email(out), out


def test_sanitizer_keeps_ordinary_text():
    for t in ("Meet at noon at the Plaza", "V12 engine", "dinner at 7.30", "The Plaza (New York)"):
        assert sanitize.scrub_text(t) == t


def test_sanitizer_covers_keys_and_nested():
    out = sanitize.scrub({"a@b.com": [{"x": "c at d dot com"}], "ok": ("e@f.io",)})
    assert not sanitize.has_email(out)


@pytest.mark.parametrize("probe", PROBES)
def test_nothing_leaves_or_is_stored_with_an_email(probe):
    rows = [dict(r) for r in ROWS]
    rows[0]["Name"] = f"The Plaza {probe}"                       # even the vendor Name
    rows[1].update({"Name": f"The Plaza {probe}", "Description": f"write to {probe}",
                    "Collection": [probe], "Flags": probe, "Website": probe})
    store = Store()
    p = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=rows)["proposals"][0]
    slack = RecSlack()
    interactions.post_proposal(slack, store, "C", p)
    q = store.get(p["id"])
    it = interactions.Interactions(slack=slack, store=store)
    it.handle(click(cards.ACTION_DETAILS, q["id"], ts=q["ts"]))
    it.handle(click(cards.ACTION_APPROVE, q["id"], user="UNOTAPPROVER", ts=q["ts"]))
    it.handle(click(cards.ACTION_REJECT, q["id"], ts=q["ts"]))
    sent = json.dumps(slack.calls, ensure_ascii=False)
    disk = store.path.read_text(encoding="utf-8") + store.log_path.read_text(encoding="utf-8")
    assert not sanitize.has_email(sent), "slack"
    assert not sanitize.has_email(disk), "store"


def test_feedback_email_fix_is_refused(monkeypatch):
    from test_v2 import FIX, fb_event, watcher
    monkeypatch.setenv("FEEDBACK_CHANNEL", "CFEED")
    monkeypatch.setenv("FEEDBACK_WATCH", "1")
    fix = dict(FIX, field="Contact_Information", new_value="events@rosehall.com", evidence="events@rosehall.com",
               confidence=0.95)
    assert watcher(fix)[0].handle(fb_event(text="email them at events@rosehall.com")).endswith("email-not-supported")


def test_summed_columns_zero_losers_and_blanks_skip():
    s, l = V(1, "V1", "A", pid="P", clicks=10), V(2, "V2", "A", pid="P", clicks=5)
    s["scrape_cost_usd"], l["scrape_cost_usd"] = 0.4, 0.6
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["scrape_cost_usd"]["after"] == 1.0 and plan["scrape_cost_usd"]["losers_after"] == 0
    assert plan["Clicks_on_Venue"]["losers_after"] == 0
    assert "Max_Capacity_Seated" not in plan                      # blank on both: no write of 0


def test_time_offsets_compared_in_utc():
    s, l = V(1, "V1", "A", pid="P"), V(2, "V2", "A", pid="P")
    s["Time_of_Submission"] = "2025-03-01T10:00:00-05:00"        # 15:00 UTC
    l["Time_of_Submission"] = "2025-03-01T14:00:00Z"             # 14:00 UTC: earlier
    plan = {c["column"]: c for c in dedupe.counter_plan(s, [l])}
    assert plan["Time_of_Submission"]["after"] == "2025-03-01T14:00:00Z"


def _partial():
    slack = RecSlack()
    store = Store()
    p = dedupe.scan(FakeX(), store, mode="weekly", vendor_rows=[dict(r) for r in ROWS])["proposals"][0]
    interactions.post_proposal(slack, store, "C", p)
    store.transition(p["id"], {"pending"}, "partial", OWNER)
    return slack, store, store.get(p["id"])


@pytest.mark.parametrize("res", [{"ok": False, "text": "refused"}, {"ok": True, "dry_run": True, "text": "dry"}])
def test_partial_survives_failed_or_dry_run_undo(res):
    slack, store, p = _partial()
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": lambda *a, **k: dict(res)})
    it.handle(click(cards.ACTION_UNDO, p["id"], ts=p["ts"]))
    assert store.get(p["id"])["status"] == "partial"
    labels = [e["text"]["text"] for b in slack.of("chat.update")[-1]["blocks"] if b["type"] == "actions"
              for e in b["elements"]]
    assert "Resume merge" in labels and "Undo merge" in labels


@pytest.mark.parametrize("res", [{"ok": False, "text": "refused"}, {"ok": True, "dry_run": True, "text": "dry"}])
def test_partial_survives_failed_or_dry_run_resume(res):
    slack, store, p = _partial()
    it = interactions.Interactions(slack=slack, store=store, apply_fns={"merge": lambda *a, **k: dict(res)})
    assert it.handle(click(cards.ACTION_APPROVE, p["id"], ts=p["ts"])) == "approved:partial"


def test_details_claim_expires():
    store = Store()
    p = store.create("merge", "t", "w", "y", "n", {"snapshots": {}})
    assert store.claim_details(p["id"]) and not store.claim_details(p["id"])
    store._data["proposals"][p["id"]]["details_claimed_at"] = (datetime.now(timezone.utc)
                                                              - timedelta(minutes=6)).isoformat()
    assert store.claim_details(p["id"])


def test_spec_round4_text():
    doc = (Path(__file__).resolve().parent.parent / "XANO_MERGE_PROPOSAL.md").read_text(encoding="utf-8")
    assert "exact V-id token" not in doc and "hidden from search via" not in doc
    assert "pending the owner" not in doc and "pending an explicit owner exception" not in doc   # granted 2026-11-20
    assert "live - 0 + backed-up value" in doc and "except User_Email and google_data_cache" in doc
    assert "scrape_cost_usd) are set to 0" in doc
