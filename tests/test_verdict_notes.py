"""Verdicts, buckets, calendar fields, notes API, rotation and the weekly call (2026-11-19 additions).
Run: python -m pytest tests/test_verdict_notes.py -q
"""
import datetime as dt
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from experiments_engine import alerts, buckets, notes, service, snapshot, verdict  # noqa: E402

REAL_REGISTRY = os.path.join(ROOT, "experiments", "experiment_log.json")
UTC = dt.timezone.utc


def real_entry(eid):
    reg = json.load(open(REAL_REGISTRY, encoding="utf-8"))
    return next(e for e in reg["experiments"] if e.get("id") == eid)


# ------------------------------------------------------------------------------- verdict: real case

def test_pdf_limit_verdict_from_live_numbers():
    """Live 06:30 UTC 11-19 readout: 20 of 395 before vs 22 of 371 after, 70% chance, money bar 95%."""
    e = real_entry("EXP-2026-11-limit-cards-plans-copy")
    ro = {"arms": {"control": {"n": 395, "conv": 20}, "treatment": {"n": 371, "conv": 22}},
          "pending": {"treatment": 17}, "params": {"gate": "pdf_limit"},
          "definition": "share of new accounts with >= 3 PDF views who pay within 1 day(s) of sign-up; cohort before "
                        "2026-10-20 to 2026-11-03 vs after 2026-11-03 21:10 UTC to 2026-11-19 06:30 UTC",
          "guardrails": [], "computed_at": "2026-11-19T06:30:00Z", "error": None}
    snap = snapshot.build_snapshot({"experiments": [e], "changes": []}, lambda _e: ro, None, "2026-11-19")
    row = snap["experiments"][0]
    v = row["verdict"]
    assert v["tldr"].startswith("Started at 5.1% (20 of 395, 10-20..11-03) of couples who hit the PDF limit "
                                "paid within 1 day; after the change 5.9% (22 of 371, 11-03..11-19): "
                                "70% chance it's genuinely better; money tests need 95%.")
    assert "evidence, not proof" in v["tldr"]
    assert v["recommend"]["call"] == "inconclusive"
    assert v["recommend"]["label"] == "Keep it, close inconclusive"
    assert v["result"]["label"] == "No clear difference"
    n = v["numbers"]
    assert n["baseline"]["source"] == "readout control" and n["baseline"]["value"] == "20 of 395 (5.1%)"
    assert abs(n["before_rate"] - 20 / 395) < 1e-6 and abs(n["after_rate"] - 22 / 371) < 1e-6
    assert n["bar"] == 0.95 and abs(n["p_beat"] - 0.7004) < 0.001
    assert v["unstick"] and "Decision note" in v["unstick"]       # review date passed
    assert "17 recent couples" in v["confidence_note"]


# ------------------------------------------------------------------------------- verdict: rules

def base_row(**kw):
    r = {"id": "EXP-2026-11-x", "name": "Test X", "lifecycle": "live", "status": "live", "health": "on_track",
         "flags": [], "tier_effective": "ux", "assignment_type": "split", "reporting": "auto",
         "decision_rule": {"ship_p": 0.9, "stop_p": 0.1}, "days_running": 10, "days_left": 4,
         "success_metric": {"metric": {"key": "planner_second_question", "label": "second question"}},
         "stats": {"p_beat": 0.5, "c_n": 200, "c_conv": 20, "t_n": 200, "t_conv": 20},
         "readout": {}, "guardrail_status": [], "results": [], "spec_issues": [], "stale": False}
    r.update(kw)
    return r


def stats(p, c=(200, 20), t=(200, 24)):
    return {"p_beat": p, "c_n": c[0], "c_conv": c[1], "t_n": t[0], "t_conv": t[1]}


def test_ship_when_bar_cleared():
    v = verdict.build_verdict(base_row(stats=dict(stats(0.95), expected_loss_abs=0.001), health="callable_win",
                                       days_left=0))
    assert v["recommend"]["call"] == "ship" and v["recommend"]["label"] == "Ship it"
    assert v["result"] == {"label": "Ahead", "tone": "good"}
    assert "with the new version" in v["tldr"]


def test_revert_when_clearly_worse():
    v = verdict.build_verdict(base_row(stats=stats(0.04), health="callable_loss", flags=["callable_loss"]))
    assert v["recommend"]["call"] == "revert" and v["result"]["tone"] == "bad"


def test_keep_running_inside_planned_days():
    v = verdict.build_verdict(base_row(stats=stats(0.6), days_left=4))
    assert v["recommend"]["call"] == "keep_running" and v["recommend"]["label"] == "Keep running (4 days left)"
    assert v["unstick"] is None


def test_past_planned_leaning_worse_reverts():
    v = verdict.build_verdict(base_row(stats=stats(0.3), days_left=0, health="needs_call", overdue_days=4,
                                       stale=True))
    assert v["recommend"]["label"] == "Revert (leaning worse)" and "product reason" in v["recommend"]["why"]
    assert v["unstick"]


def test_guardrail_breach_reverts():
    v = verdict.build_verdict(base_row(stats=stats(0.97), health="guardrail_breach", flags=["guardrail_breach"],
                                       reason="Guardrail purchases_per_day crossed its threshold"))
    assert v["recommend"]["call"] == "revert" and v["tldr"].startswith("A guardrail broke")


def test_manual_overdue_reads_out_with_unstick():
    v = verdict.build_verdict(base_row(reporting="manual", stats=None, health="manual_overdue", stale=True,
                                       assignment_type="pre_post", flags=["manual_overdue"],
                                       results=[{"date": "2026-10-25", "note": "Baseline p50 12.6 s"}]))
    assert v["recommend"]["call"] == "read_out"
    assert v["unstick"].startswith("Ask Claude: 'read out Test X'")
    assert "Last note (2026-10-25): Baseline p50 12.6 s" in v["tldr"] and v["result"]["label"] == "Not measured"


def test_paused_long_recommends_shelve():
    v = verdict.build_verdict(base_row(lifecycle="paused", health="paused", paused_days=20, stale=True, stats=None))
    assert v["recommend"]["call"] == "resume_or_shelve" and v["recommend"]["label"].startswith("Shelve")
    assert v["unstick"] and "resume or shelve" in v["unstick"]
    v2 = verdict.build_verdict(base_row(lifecycle="paused", health="paused", paused_days=3, stats=None))
    assert v2["recommend"]["label"] == "Resume or shelve"


def test_approximate_metric_has_no_numbers_verdict():
    v = verdict.build_verdict(base_row(stats=stats(0.004), approximate_metric="biased"))
    assert v["result"]["label"] == "Biased metric" and v["recommend"]["call"] == "split_test"
    assert "revert" not in v["recommend"]["label"].lower()


def test_failing_metric_fix_tracking():
    v = verdict.build_verdict(base_row(stats=None, health="no_data", flags=["no_data"], stale=True,
                                       readout={"error": "unsupported param: cohort='CA'"}))
    assert v["recommend"]["call"] == "fix_tracking" and "cohort='CA'" in v["tldr"]
    assert "fix the metric" in v["unstick"]


def test_zero_in_both_periods_is_not_a_comparison():
    v = verdict.build_verdict(base_row(stats=stats(0.38, (26, 0), (44, 0)), days_left=0, health="needs_call"))
    assert v["result"]["label"] == "No data" and v["recommend"]["call"] == "fix_tracking"


def test_spec_incomplete_without_numbers():
    row = base_row(reporting="manual", stats=None, health="spec_incomplete", assignment_type="pre_post",
                   flags=["spec_incomplete"], spec_issues=[{"severity": "error", "field": "metric.baseline"},
                                                           {"severity": "error", "field": "guardrails[0]"}])
    v = verdict.build_verdict(row)
    assert v["numbers"] is None and "No starting point" not in v["tldr"]   # the UI shows it from numbers
    assert v["recommend"]["call"] == "complete_spec" and "metric.baseline, guardrails" in v["recommend"]["why"]


def test_no_starting_point_in_tldr_when_numbers_but_no_baseline():
    row = base_row(assignment_type="split", stats=None, readout={"arms": {"treatment": {"n": 50, "conv": 5}}},
                   success_metric={"metric": {"key": "planner_second_question"}})
    row["stats"] = {"p_beat": None}
    n = verdict.numbers(row)
    assert n["baseline"]["source"] is None


def test_registry_baseline_used_when_no_readout():
    row = base_row(reporting="manual", stats=None, assignment_type="pre_post",
                   success_metric={"metric": {"key": "manual", "baseline": 0.149, "label": "x"}})
    n = verdict.build_verdict(row)["numbers"]
    assert n["baseline"] == {"label": "Baseline (registry)", "value": "14.9%", "rate": 0.149,
                             "source": "registry baseline"}


def test_planned_row():
    v = verdict.build_verdict(base_row(lifecycle="planned", health=None, stats=None,
                                       spec_issues=[{"severity": "error", "field": "hypothesis"}]))
    assert v["recommend"]["call"] == "complete_spec" and v["result"]["label"] == "Too early"


def test_verdict_never_raises():
    v = verdict.build_verdict({"lifecycle": "live", "stats": "garbage", "success_metric": 5})
    assert "tldr" in v


def test_decided_verdict_and_bucket_in_snapshot():
    reg = {"experiments": [{"id": "EXP-2026-08-x", "name": "Budget Roadmap PDF", "status": "reverted",
                            "decided_on": "2026-09-27", "decision": "Killed: 1 buyer in 7 weeks",
                            "themes": ["upsell", "revenue"], "start_date": "2026-08-09"}], "changes": []}
    snap = snapshot.build_snapshot(reg, None, None, "2026-11-19")
    d = snap["decided"][0]
    assert d["verdict"]["result"] == {"label": "Reverted", "tone": "bad"}
    assert d["verdict"]["tldr"] == "Reverted on 2026-09-27: Killed: 1 buyer in 7 weeks"
    assert d["verdict"]["recommend"] is None and d["bucket"] == "payment_funnel"
    assert [b["key"] for b in snap["buckets"]] == list(buckets.KEYS)
    assert snap["buckets"][0]["decided"]["reverted"] == 1 and snap["buckets"][2]["running"] == []


# ------------------------------------------------------------------------------- buckets / calendar

@pytest.mark.parametrize("entry,want", [
    ({"name": "Planner speed: pricing cache", "themes": ["planner", "performance"]}, "reliability"),
    ({"name": "Remove T&C checkbox on sign-up", "themes": ["activation"]}, "onboarding"),
    ({"name": "Save offer", "themes": ["paywall"], "surfaces": ["brevo"], "metric": {"key": "offer_code_buyers"}},
     "payment_funnel"),
    ({"name": "NPS Trustpilot ask", "metric": {"key": "trustpilot_completion"}}, "retention"),
    ({"name": "Planner open-ended replies", "themes": ["planner", "assistant"]}, "planner_engagement"),
    ({"name": "Railway search", "themes": ["search"], "surfaces": ["railway:search-api"]}, "search"),
    ({"name": "Catering prices from extraction", "themes": ["data"]}, "data"),
    ({"name": "x", "bucket": "search", "themes": ["planner"]}, "search"),
])
def test_bucket_derivation(entry, want):
    assert buckets.derive(entry)[0] == want


def test_calendar_fields():
    e = {"id": "EXP-2026-10-p", "status": "paused", "start_date": "2026-10-20", "planned_days": 14,
         "max_days": 28, "paused_on": "2026-11-07"}
    snap = snapshot.build_snapshot({"experiments": [e], "changes": []}, None, None, "2026-11-19")
    r = snap["experiments"][0]
    assert r["start_date"] == "2026-10-20" and r["review_date"] == "2026-11-03"
    assert r["max_end_date"] == "2026-11-17" and r["decided_on"] is None
    assert r["paused_ranges"] == [{"from": "2026-11-07", "to": None}]


# ------------------------------------------------------------------------------- rotation

def live_entry(eid, **kw):
    e = {"id": eid, "name": eid, "status": "live", "start_date": "2026-11-10", "planned_days": 14,
         "reporting": "manual"}
    e.update(kw)
    return e


def test_rotation_block_and_over_cap():
    reg = {"experiments": [live_entry(f"EXP-2026-11-r{i}", in_rotation=True) for i in range(4)]
           + [live_entry("EXP-2026-11-b")], "changes": []}
    snap = snapshot.build_snapshot(reg, None, None, "2026-11-19")      # a Thursday
    rot = snap["rotation"]
    assert rot["max"] == 3 and rot["over_cap"] is True and len(rot["in_rotation"]) == 4
    assert rot["backlog"] == ["EXP-2026-11-b"] and rot["next_call_date"] == "2026-11-23"
    assert all(r["in_rotation"] for r in snap["experiments"] if r["id"] != "EXP-2026-11-b")


def test_validate_strict_rotation_cap(tmp_path):
    reg = {"experiments": [live_entry(f"EXP-2026-11-r{i}", in_rotation=True) for i in range(4)], "changes": []}
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    sys.path.insert(0, os.path.join(ROOT, "experiments"))
    import validate  # noqa: PLC0415
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = validate.main(["--registry", str(p), "--id", "EXP-2026-11-r0"])
    assert rc == 1 and "in rotation (max 3)" in buf.getvalue()
    with contextlib.redirect_stdout(io.StringIO()):
        assert validate.main(["--registry", str(p)]) in (0, 1)   # full run only warns about the cap


# ------------------------------------------------------------------------------- weekly call

def test_weekly_call_agenda():
    rows = [{"id": "EXP-a", "name": "Rot A", "health": "on_track", "in_rotation": True,
             "verdict": {"tldr": "Started at 5%; after 6%.", "recommend": {"label": "Keep running (3 day(s) left)"}}},
            {"id": "EXP-b", "name": "Backlog B", "health": "needs_call",
             "verdict": {"tldr": "x", "recommend": {"label": "Keep the change, close as inconclusive"}}},
            {"id": "EXP-c", "name": "Pending C", "health": "needs_call", "decision_pending": {"call": "ship"}}]
    snap = {"experiments": rows, "decided": [{"id": "EXP-d", "name": "Done D", "outcome": "shipped",
                                              "decided_on": "2026-11-19", "takeaway": "Worked."}],
            "rotation": {"max": 3, "in_rotation": ["EXP-a"], "backlog": ["EXP-b"], "over_cap": False}}
    text = alerts.format_weekly(snap, "2026-11-23")
    assert text.startswith("*Weekly call, 2026-11-23: 1 in rotation (max 3), 1 in the backlog, 1 decided last week*")
    assert "Rot A*: Started at 5%; after 6%." in text and "Recommended: *Keep running (3 day(s) left)*" in text
    assert "Backlog B*: Keep the change, close as inconclusive" in text
    assert "1 decided in the dash" in text and "Done D" in text and "*Decision* note" in text
    assert alerts.format_weekly({"experiments": [], "decided": [], "rotation": {"in_rotation": [], "backlog": []}},
                                "2026-11-23") is None


def test_digest_uses_recommendation_and_unstick_and_skips_pending():
    rows = [{"id": "EXP-s", "name": "Stale S", "health": "manual_overdue", "stale": True, "reason": "No readout",
             "verdict": {"recommend": {"label": "Read it out, then decide"}, "unstick": "Ask Claude: 'read out Stale S'"}},
            {"id": "EXP-p", "name": "Pending P", "health": "needs_call", "decision_pending": {"call": "ship"}}]
    snap = {"summary": {"stale": 1, "needs_call": 0}, "experiments": rows}
    text = alerts.format_digest(snap)
    assert "Stale S*: Read it out, then decide." in text and "Next: Ask Claude: 'read out Stale S'" in text
    assert "Pending P" not in text


# ------------------------------------------------------------------------------- notes API

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class NoHTTP:
    def get(self, *a, **k):
        raise AssertionError("network")

    def post(self, *a, **k):
        raise AssertionError("network")


@pytest.fixture
def napp(monkeypatch, tmp_path):
    monkeypatch.setenv("EXP_API_KEY", "k-test")
    monkeypatch.setenv("EXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DRY_RUN", raising=False)
    reg = {"experiments": [
        live_entry("EXP-2026-11-call", review_date="2026-11-12", start_date="2026-11-01"),
        live_entry("EXP-2026-11-sync", review_date="2026-11-12", start_date="2026-11-01",
                   notes_synced_through="2026-11-19T12:00:00Z")], "changes": []}
    p = tmp_path / "experiment_log.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    clock = Clock(dt.datetime(2026, 11, 19, 15, 0, tzinfo=UTC))
    eng = service.Engine(data_dir=str(tmp_path / "data"), clock=clock, http=NoHTTP(),
                         metric_runner_factory=lambda r, n, d: ((lambda e: {"error": "x"}), {"requests": 0}),
                         sender=lambda t: {"ok": True}, sync=True, registry_file=str(p))
    monkeypatch.setattr(service.changefeed, "collect",
                        lambda *a, **k: {"events": [], "notes": [], "calls": 0, "new": []})
    eng.refresh("registry", clock())
    return eng, TestClient(service.create_app(eng, start_scheduler=False)), clock


H = {"X-Exp-Key": "k-test"}


def snap_of(c):
    return c.get("/snapshot", headers=H).json()


def test_notes_gating(napp):
    eng, c, _ = napp
    assert c.post("/experiments/EXP-2026-11-call/notes", json={"author": "Des", "text": "hi"}).status_code == 401
    assert c.get("/experiments/EXP-2026-11-call/notes").status_code == 401
    assert c.get("/decisions/pending").status_code == 401
    r = c.post("/experiments/EXP-2026-11-call/notes", json={"author": "Des", "text": "hi"}, headers=dict(H, host="x.up.railway.app"))
    assert r.status_code == 404


def test_notes_validation(napp):
    _, c, _ = napp
    url = "/experiments/EXP-2026-11-call/notes"
    assert c.post(url, json={"author": "", "text": "x"}, headers=H).status_code == 422
    assert c.post(url, json={"author": "Des", "text": "x" * 2001}, headers=H).status_code == 422
    assert c.post(url, json={"author": "Des", "text": "x", "kind": "rant"}, headers=H).status_code == 422
    assert c.post(url, json={"author": "Des", "text": "x", "kind": "decision"}, headers=H).status_code == 422
    assert c.post(url, json={"author": "Des", "text": "x", "call": "nuke"}, headers=H).status_code == 422
    assert c.post(url, content=b"not json", headers=H).status_code == 422
    assert c.post("/experiments/EXP-nope/notes", json={"author": "Des", "text": "x"}, headers=H).status_code == 404
    r = c.post(url, json={"author": "Des\x00", "text": "line1\nline2\x07"}, headers=H)
    assert r.status_code == 201 and r.json()["note"]["author"] == "Des" and r.json()["note"]["text"] == "line1\nline2"


def test_notes_rate_limit(napp):
    _, c, _ = napp
    url = "/experiments/EXP-2026-11-call/notes"
    for i in range(notes.RATE_MAX):
        assert c.post(url, json={"author": "Des", "text": f"n{i}"}, headers=H).status_code == 201
    assert c.post(url, json={"author": "Des", "text": "one more"}, headers=H).status_code == 429


def test_notes_attach_at_serve_time_and_decision_pending(napp):
    eng, c, clock = napp
    before = snap_of(c)
    assert before["summary"]["needs_call"] == 2 and before.get("notes_by_id") == {}
    c.post("/experiments/EXP-2026-11-call/notes", json={"author": "Des", "text": "Discussed on the call"}, headers=H)
    s = snap_of(c)
    assert [n["text"] for n in s["notes_by_id"]["EXP-2026-11-call"]] == ["Discussed on the call"]
    assert s["summary"]["needs_call"] == 2           # a plain note is not a decision
    r = c.post("/experiments/EXP-2026-11-call/notes",
               json={"author": "Vivek", "kind": "decision", "call": "ship", "text": "Keep it"}, headers=H)
    assert r.status_code == 201
    s = snap_of(c)
    row = next(x for x in s["experiments"] if x["id"] == "EXP-2026-11-call")
    assert row["decision_pending"]["call"] == "ship" and row["decision_pending"]["author"] == "Vivek"
    assert s["summary"]["needs_call"] == 1 and s["summary"]["decision_pending"] == 1
    assert "EXP-2026-11-call" not in [q["id"] for q in s["action_queue"]]
    assert "EXP-2026-11-call" not in s["rotation"]["backlog"]
    p = c.get("/decisions/pending", headers=H).json()["pending"]
    assert [x["id"] for x in p] == ["EXP-2026-11-call"] and p[0]["call"] == "ship"
    # notes survive a rebuild (persisted on the volume)
    eng.refresh("registry", clock())
    assert snap_of(c)["summary"]["decision_pending"] == 1
    got = c.get("/experiments/EXP-2026-11-call/notes", headers=H).json()["notes"]
    assert [n["kind"] for n in got] == ["note", "decision"]


def test_decision_older_than_registry_sync_is_not_pending(napp):
    eng, c, clock = napp
    clock.t = dt.datetime(2026, 11, 19, 11, 0, tzinfo=UTC)       # before notes_synced_through 12:00
    c.post("/experiments/EXP-2026-11-sync/notes", json={"author": "V", "kind": "decision", "call": "keep_running",
                                                        "text": "two more weeks"}, headers=H)
    assert snap_of(c)["summary"]["decision_pending"] == 0
    clock.t = dt.datetime(2026, 11, 19, 13, 0, tzinfo=UTC)
    c.post("/experiments/EXP-2026-11-sync/notes", json={"author": "V", "kind": "decision", "call": "shelve",
                                                        "text": "drop it"}, headers=H)
    assert snap_of(c)["summary"]["decision_pending"] == 1


def test_notes_file_torn_line_is_ignored(tmp_path):
    st = notes.NotesStore(str(tmp_path))
    st.add("EXP-a", {"author": "D", "text": "ok"})
    with open(st.path, "a", encoding="utf-8") as f:
        f.write('{"exp_id": "EXP-a", "text": "torn')
    assert [n["text"] for n in st.load("EXP-a")] == ["ok"]


SCRATCH_SNAPSHOT = os.environ.get("EXP_VERDICT_SNAPSHOT", os.path.join(
    os.environ.get("TEMP", "/tmp"), "claude", "3d907d6bddb47d00", "b1614ada-e602-409d-bacc-1819cb3f570d",
    "scratchpad", "live_snapshot3.json"))


def _all_labels(snap):
    out = []
    for r in snap["experiments"]:
        rec = (r.get("verdict") or {}).get("recommend")
        if rec:
            out.append((r["id"], rec["label"]))
    return out


def test_recommend_labels_are_short_on_real_registry():
    """Every recommend label <= 36 chars for every real registry row (live readouts when available)."""
    reg = json.load(open(REAL_REGISTRY, encoding="utf-8"))
    runner = None
    if os.path.exists(SCRATCH_SNAPSHOT):
        runner = service.cached_runner(json.load(open(SCRATCH_SNAPSHOT, encoding="utf-8")))
    snap = snapshot.build_snapshot(reg, runner, None, "2026-11-19")
    long = [(i, lab) for i, lab in _all_labels(snap) if len(lab) > 36]
    assert not long, long


@pytest.mark.parametrize("row", [
    base_row(stats=stats(0.95), health="callable_win", days_left=0),
    base_row(stats=stats(0.3), days_left=0, health="needs_call"),
    base_row(stats=stats(0.6), days_left=0, health="needs_call", assignment_type="pre_post"),
    base_row(stats=stats(0.6), days_left=12),
    base_row(lifecycle="paused", health="paused", paused_days=20, stats=None),
    base_row(stats=None, reporting="manual", health="spec_incomplete", flags=["spec_incomplete"],
             spec_issues=[{"severity": "error", "field": "audience.est_daily_exposed"},
                          {"severity": "error", "field": "metric.mde_rel"}]),
    base_row(lifecycle="planned", health=None, stats=None, spec_issues=[{"severity": "error", "field": "x"}]),
])
def test_labels_short_for_every_rule(row):
    assert len(verdict.build_verdict(row)["recommend"]["label"]) <= 36


def test_no_starting_point_not_in_tldr_when_no_numbers():
    v = verdict.build_verdict(base_row(reporting="manual", stats=None, health="manual_overdue", stale=True,
                                       assignment_type="pre_post", flags=["manual_overdue"]))
    assert v["numbers"] is None and "No starting point" not in v["tldr"]


def test_sitewide_purchases_metric_is_named_and_caveated():
    row = base_row(assignment_type="pre_post", days_left=0, health="confounded", flags=["confounded"],
                   success_metric={"metric": {"key": "purchases_per_day", "label": "purchases/day"}},
                   stats={"p_beat": 0.947, "control_rate": 3.43, "treatment_rate": 5.2, "model": "gamma_poisson"},
                   readout={"count": {"pre": {"count": 24, "days": 7}, "post": {"count": 40, "days": 7.691}}})
    v = verdict.build_verdict(row)
    assert "all-site purchases a day" in v["tldr"]
    assert "whole site" in v["confidence_note"] and "can't be credited to this change alone" in v["confidence_note"]


def _sitewide(p, **kw):
    r = base_row(assignment_type="pre_post", days_left=0, health="needs_call", flags=["confounded"],
                 success_metric={"metric": {"key": "purchases_per_day", "label": "purchases/day"}},
                 stats={"p_beat": p, "control_rate": 3.43, "treatment_rate": 5.2, "model": "gamma_poisson"},
                 readout={"count": {"pre": {"count": 24, "days": 7}, "post": {"count": 40, "days": 7.691}}})
    r.update(kw)
    return r


@pytest.mark.parametrize("p,label", [(0.97, "Keep it, close inconclusive"), (0.6, "Keep it, close inconclusive"),
                                     (0.3, "Close inconclusive"), (0.02, "Close inconclusive")])
def test_sitewide_metric_never_ships_or_reverts(p, label):
    v = verdict.build_verdict(_sitewide(p))
    assert v["recommend"]["call"] == "inconclusive" and v["recommend"]["label"] == label
    assert "can't credit this change" in v["recommend"]["why"] and "split test" in v["recommend"]["why"]


def test_sitewide_metric_keeps_running_inside_its_run_and_reverts_only_on_guardrail():
    v = verdict.build_verdict(_sitewide(0.6, days_left=5, health="on_track"))
    assert v["recommend"]["call"] == "keep_running"
    v = verdict.build_verdict(_sitewide(0.6, health="guardrail_breach", flags=["guardrail_breach"]))
    assert v["recommend"]["call"] == "revert"


def test_sitewide_cap_only_for_before_after():
    v = verdict.build_verdict(_sitewide(0.97, assignment_type="split", flags=[], health="callable_win"))
    assert v["recommend"]["call"] == "ship"            # gamma_poisson: no expected loss, treated as met


# ------------------------------------------------------------------------------- QA round (M1-M3, L1, L3, L6)

def test_m1_between_bars_past_run_is_inconclusive_call():
    v = verdict.build_verdict(base_row(stats=stats(0.7), days_left=0, health="needs_call", assignment_type="pre_post"))
    assert v["recommend"]["call"] == "inconclusive" and v["recommend"]["label"] == "Keep it, close inconclusive"


def test_m2_sitewide_inside_run_and_without_run():
    v = verdict.build_verdict(_sitewide(0.97, days_left=6, health="on_track", flags=[]))
    assert v["recommend"]["call"] == "keep_running" and v["recommend"]["label"] == "Keep running (6 days left)"
    v = verdict.build_verdict(_sitewide(0.97, days_left=None, health="confounded", flags=["confounded"]))
    assert v["recommend"]["call"] == "keep_running" and "review date" in v["recommend"]["label"]
    v = verdict.build_verdict(_sitewide(0.97, days_left=None, health="needs_call"))
    assert v["recommend"]["call"] == "inconclusive"     # a due call still closes it inconclusive


def test_m2_missing_days_left_is_not_past_run_for_normal_metrics():
    v = verdict.build_verdict(base_row(stats=stats(0.2), days_left=None, health="on_track"))
    assert "no planned run or review date" in v["recommend"]["why"]


def test_m3_expected_loss_blocks_ship():
    st = dict(stats(0.96), expected_loss_abs=0.01)
    row = base_row(stats=st, days_left=0, health="needs_call", decision_rule={"ship_p": 0.9, "stop_p": 0.1,
                                                                                "max_loss": 0.002})
    v = verdict.build_verdict(row)
    assert v["recommend"]["call"] == "inconclusive" and "expected loss" in v["recommend"]["why"]
    st["expected_loss_abs"] = 0.001
    assert verdict.build_verdict(row)["recommend"]["call"] == "ship"
    st.pop("expected_loss_abs")
    st["exp_loss"] = 0.001                      # alternate field name is read too
    assert verdict.build_verdict(row)["recommend"]["call"] == "ship"


def test_l1_draft_finishes_spec_never_read_out():
    v = verdict.build_verdict(base_row(lifecycle="draft", status="draft", health=None, stats=None, reporting="manual"))
    assert v["recommend"]["label"] == "Finish the spec before launch" and v["recommend"]["call"] == "complete_spec"
    real = snapshot.build_snapshot(json.load(open(REAL_REGISTRY, encoding="utf-8")), None, None, "2026-11-19")
    for r in real["experiments"]:
        if r.get("lifecycle") in ("draft", "planned"):
            assert r["verdict"]["recommend"]["call"] != "read_out", r["id"]


def test_l3_client_id_idempotent(napp):
    _, c, _ = napp
    url = "/experiments/EXP-2026-11-call/notes"
    body = {"author": "Des", "text": "once", "client_id": "dash-abc_1"}
    r1 = c.post(url, json=body, headers=H)
    r2 = c.post(url, json=body, headers=H)
    assert r1.status_code == 201 and r2.status_code == 200 and r2.json()["created"] is False
    assert r1.json()["note"]["id"] == r2.json()["note"]["id"]
    assert len(c.get(url, headers=H).json()["notes"]) == 1
    assert c.post(url, json=dict(body, client_id="bad id!"), headers=H).status_code == 422


def test_l6_startup_runs_without_scheduler(napp, monkeypatch):
    eng, _, clock = napp
    fresh = service.Engine(data_dir=eng.data_dir, clock=clock, http=NoHTTP(),
                           metric_runner_factory=lambda r, n, d: ((lambda e: {"error": "x"}), {"requests": 0}),
                           sender=lambda t: {"ok": True}, sync=True, registry_file=str(eng.registry_file))
    assert fresh.snapshot is None
    with TestClient(service.create_app(fresh, start_scheduler=False)) as c:
        assert fresh._started and fresh.snapshot is not None
        assert c.get("/snapshot", headers=H).status_code == 200
        assert fresh._sched is None                     # scheduler not started
