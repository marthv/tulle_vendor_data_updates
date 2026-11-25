"""EXP_TAB_V2: read lock, harm stop, balance, setup checks, cards (FRAMEWORK.md section 14). No network.
Simulations use fixed seeds. Run: python -m pytest tests/test_exp_tab_v2.py -q"""
import copy
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from experiments_engine import alerts, event_read, history, metrics, readlock, service, snapshot, stats, v2  # noqa: E402
from test_metrics import P, R, U, fake_client, ms  # noqa: E402

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _switch(monkeypatch):
    monkeypatch.setenv("EXP_TAB_V2", "1")


def entry(**over):
    e = {"id": "EXP-2026-11-t", "name": "Test split", "status": "live", "tier": "ux", "themes": [],
         "what_changed": "Copy changed.", "surfaces": ["weweb:planner"],
         "audience": {"segment": "free couples", "est_daily_exposed": 100},
         "hypothesis": "If X then Y because Z.", "tests": "Does it work?",
         "metric": {"key": "new_paid_within_days", "params": {"days": 1}, "direction": "increase", "baseline": 0.1,
                    "mde_rel": 0.5, "label": "did the thing"},
         "guardrails": [], "assignment": {"type": "split", "rule": "hash_pct", "salt": "t1", "pct": 50},
         "start_date": "2026-11-10", "planned_days": 14, "review_date": "2026-11-24", "max_days": 42,
         "reporting": "auto", "rollback": "off", "links": ["x"], "results": [], "framework_version": 2}
    e.update(over)
    return e


def expo(days=8, c=50, t=50, end="2026-11-17"):
    d = dt.date.fromisoformat(end)
    return [{"date": (d - dt.timedelta(days=days - 1 - i)).isoformat(), "control": c, "treatment": t} for i in range(days)]


def ro(c=(400, 43), t=(400, 97), **over):
    out = {"arms": {"control": {"n": c[0], "conv": c[1]}, "treatment": {"n": t[0], "conv": t[1]}},
           "exposure_by_day": expo(), "error": None, "computed_at": "2026-11-17T12:00:00Z",
           "mixing": {"exposed": 800, "both_arms": 0, "repeat_accounts": 0, "excluded": 0, "share": 0.0}}
    out.update(over)
    return out


def snap_for(e, readout, today, store=None):
    return snapshot.build_snapshot({"experiments": [e] if isinstance(e, dict) else e, "changes": []},
                                   (lambda x: copy.deepcopy(readout(x) if callable(readout) else readout)), store, today)


def row_for(e, readout, today, store=None):
    return snap_for(e, readout, today, store)["experiments"][0]


def colour(row, key):
    return next(c["colour"] for c in row["checks"] if c["key"] == key)


def binom(rng, n, p):
    return rng.binomialvariate(n, p) if hasattr(rng, "binomialvariate") else sum(rng.random() < p for _ in range(n))


# --------------------------------------------------------------------------------------- 1 read lock

FORBIDDEN = re.compile(r'"(p_beat|lift_median_rel|lift_ci95_rel|expected_loss_abs|control_rate|treatment_rate|conv|'
                       r'p_breach|t_conv|c_conv|before_rate|after_rate)": ?[0-9\[]')


def test_no_result_anywhere_before_the_read_date(tmp_path):
    e = entry(results=[{"date": "2026-11-15", "note": "treatment 24.25% vs 10.75%, winning",
                        "counts": {"control": {"n": 400, "conv": 43}, "treatment": {"n": 400, "conv": 97}}}],
              guardrails=[{"key": "planner_return_within_days", "max_drop_rel": 0.25}], stat_sig="p=0.001")
    r = ro(guardrails=[{"key": "planner_return_within_days",
                        "arms": {"control": {"n": 400, "conv": 211}, "treatment": {"n": 400, "conv": 205}}}],
           overlap={"EXP-x": {"shared": 9, "cells": {"control|control": {"n": 9, "conv": 4}}}}, revenue=777.0)
    s = snap_for(e, r, "2026-11-17", history.HistoryStore(str(tmp_path)))
    txt = json.dumps({k: v for k, v in s.items() if k != "generated_at"})
    assert not FORBIDDEN.search(txt), FORBIDDEN.search(txt)
    for bad in ("24.25", "10.75", "winning", "777", "0.1075", "0.2425", "211", "205", "chance"):
        assert bad not in txt, bad
    row = s["experiments"][0]
    assert row["card"]["result_text"] == "Result: hidden until 2026-11-24"
    assert row["health"] not in ("callable_win", "callable_loss") and row["verdict"]["numbers"] is None
    from experiments_ui import cards
    page = cards.build_html(s).split("</style>")[1]
    for bad in ("24.25", "97 of", "43 of", "211", "winning"):
        assert bad not in page, bad


@pytest.mark.parametrize("status", ["live", "paused", "running", "LIVE "])
def test_lock_follows_any_running_status(status):
    txt = json.dumps(row_for(entry(status=status, results=[{"date": "2026-11-15", "note": "up 99.9%"}]), ro(), "2026-11-17"))
    assert "99.9" not in txt and not FORBIDDEN.search(txt)


def test_one_read_on_the_date_and_it_is_kept(tmp_path):
    st = history.HistoryStore(str(tmp_path))
    e = entry()
    assert row_for(e, ro(), "2026-11-23", st)["read"] is None
    row = row_for(e, ro(), "2026-11-24", st)
    assert row["read"]["call"] == "ship" and row["health"] == "callable_win" and "97 of 400" in row["card"]["result_text"]
    later = row_for(e, ro(t=(400, 20)), "2026-11-27", st)          # a later run does not change the read
    assert colour(later, "harm") in ("amber", "green")
    assert later["read"]["call"] == "ship" and later["read"]["arms"]["treatment"]["conv"] == 97


def test_97_5_bar_not_the_tier_bar():
    r = ro(c=(400, 40), t=(400, 56))                               # over the old ux bar (90%), under 97.5%
    assert 0.93 < v2.p_better(r["arms"]["control"], r["arms"]["treatment"]) < 0.975
    row = row_for(entry(tier="ux"), r, "2026-11-24")
    assert row["read"]["call"] == "inconclusive" and row["health"] == "needs_call"


def test_moving_the_date_earlier_after_launch_never_opens_it(tmp_path):
    st = history.HistoryStore(str(tmp_path))
    row_for(entry(), ro(), "2026-11-12", st)
    row = row_for(entry(review_date="2026-11-13"), ro(), "2026-11-14", st)
    assert row["result_hidden"] and colour(row, "settings") == "red" and not FORBIDDEN.search(json.dumps(row))


def test_explicit_read_date_wins_and_no_date_stays_hidden():
    assert row_for(entry(read_date="2026-12-05"), ro(), "2026-11-30")["result_hidden"]
    row = row_for(entry(review_date=None, planned_days=None), ro(), "2027-06-01")
    assert row["result_hidden"] and row["light"] == "red"


def _cn(c, t):
    return {"control": {"n": 400, "conv": c}, "treatment": {"n": 400, "conv": t}}


def test_manual_split_interim_counts_hidden_first_entry_on_the_date_is_the_read():
    e = entry(reporting="manual", metric={"key": "manual", "direction": "increase", "baseline": .1, "mde_rel": .5, "label": "x"},
              exposure={"source": "mixpanel:seen"}, exposure_log=expo(),
              results=[{"date": "2026-11-15", "counts": _cn(43, 97)}, {"date": "2026-11-24", "counts": _cn(50, 60)},
                       {"date": "2026-11-26", "counts": _cn(10, 99)}])
    assert not FORBIDDEN.search(json.dumps(row_for(e, None, "2026-11-20")))
    row = row_for(e, None, "2026-11-27")
    assert row["read"]["arms"]["treatment"]["conv"] == 60 and row["read"]["call"] == "inconclusive"


def test_quiet_exposure_is_red_but_does_not_void_the_read():
    row = row_for(entry(), ro(), "2026-11-24")
    assert colour(row, "exposure") == "red" and row["read"]["call"] == "ship"


def test_red_setup_blocks_a_winner():
    row = row_for(entry(), ro(mixing={"exposed": 800, "both_arms": 20, "repeat_accounts": 0, "excluded": 20, "share": .025}),
                  "2026-11-24")
    assert row["read"]["call"] == "untrusted" and row["health"] == "needs_call"


def test_slack_messages_carry_no_result():
    s = snap_for(entry(in_rotation=True), ro(), "2026-11-17")
    r = s["experiments"][0]
    for t in (alerts.format_weekly(s, dt.date(2026, 11, 17)), alerts.format_digest(s), alerts.format_guardrail(r),
              alerts.counts_line(r), alerts.format_calls(s, [r["id"]])):
        assert not t or not re.search(r"\b(97|43) of|chance it's better|24\.2|10\.7", t), t


def test_harm_dm_has_no_numbers():
    r = row_for(entry(), ro(c=(400, 120), t=(400, 40)), "2026-11-17")
    assert r["health"] == "guardrail_breach" and r["result_hidden"]
    t = alerts.format_guardrail(r)
    assert "Clear harm" in t and not re.search(r"40|120|%", t)


class _Http:
    def get(self, *a, **k):
        raise ConnectionError("no network in tests")


def _engine(tmp_path, reg, readouts, now):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps(reg), encoding="utf-8")
    sent = []
    eng = service.Engine(data_dir=str(tmp_path / "d"), clock=lambda: now[0], http=_Http(), sync=True, registry_file=p,
                         sender=lambda t: (sent.append(t), {"ok": True})[1],
                         metric_runner_factory=lambda r, n, d: ((lambda e: copy.deepcopy(readouts[e["id"]])), {"requests": 0}))
    return eng, sent


def test_http_api_history_and_rebuilds(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    monkeypatch.setenv("EXP_API_KEY", "k")
    now = [dt.datetime(2026, 11, 17, 12, tzinfo=UTC)]
    e = entry()
    eng, sent = _engine(tmp_path, {"experiments": [e], "changes": []}, {e["id"]: ro()}, now)
    eng.store.append_day({"today": "2026-11-16", "experiments": [            # a row from before the switch
        {"id": e["id"], "health": "callable_win", "stats": {"p_beat": 0.987654}, "readout": {}}]})
    eng.refresh("full", now[0])
    c = TestClient(service.create_app(eng, start_scheduler=False))
    for path in ("/snapshot", "/snapshot?history=30", f"/experiments/{e['id']}/history", "/decisions/pending", "/health", "/changes"):
        body = c.get(path, headers={"X-Exp-Key": "k"}).text
        assert "0.987654" not in body and "callable_win" not in body and not FORBIDDEN.search(body), path
    assert not any(re.search(r"97 of|chance", t) for t in sent)
    with open(eng.snapshot_path, "rb") as f:                                  # the copy on the volume is locked too
        assert not FORBIDDEN.search(gzip.decompress(f.read()).decode())
    now[0] = dt.datetime(2026, 11, 24, 6, tzinfo=UTC)
    eng.refresh("registry", now[0])
    assert eng.snapshot["experiments"][0]["read"]["call"] is None             # cached numbers predate the read date
    eng.refresh("full", now[0])
    assert eng.snapshot["experiments"][0]["read"]["call"] == "ship"


def test_switching_on_and_off_around_a_saved_snapshot(tmp_path, monkeypatch):
    now = [dt.datetime(2026, 11, 17, 12, tzinfo=UTC)]
    e = entry()
    reg = {"experiments": [e], "changes": []}
    monkeypatch.setenv("EXP_TAB_V2", "0")
    eng, _ = _engine(tmp_path, reg, {e["id"]: ro()}, now)
    eng.refresh("full", now[0])
    assert FORBIDDEN.search(json.dumps(eng.snapshot))
    monkeypatch.setenv("EXP_TAB_V2", "1")
    eng2, _ = _engine(tmp_path, reg, {e["id"]: ro()}, now)
    assert eng2.load_persisted()
    assert not FORBIDDEN.search(json.dumps(eng2.snapshot))
    eng2.refresh("full", now[0])
    assert not FORBIDDEN.search(json.dumps(eng2.snapshot))
    monkeypatch.setenv("EXP_TAB_V2", "0")                                     # the redacted copy is never scored as zeros
    eng3, _ = _engine(tmp_path, reg, {e["id"]: ro()}, now)
    eng3.load_persisted()
    eng3.refresh("registry", now[0])
    assert eng3.snapshot["experiments"][0]["stats"]["t_conv"] == 97


def test_tulle_bot_prompt_and_answer(monkeypatch):
    sys.path.insert(0, os.path.join(ROOT, "slack_qa"))
    try:
        import knowledge
        import engine as bot_engine
    except Exception as exc:
        pytest.skip(f"slack_qa not importable: {exc}")
    e = entry(start_date=readlock.utc_today().isoformat(), read_date="2099-01-01", decision="winner 31.7%",
              results=[{"date": "2026-11-15", "note": "31.7% vs 11.1%"}], confounds_noted=["31.7%"])
    txt = json.dumps(knowledge.bot_view({"experiments": [e], "changes": []}))
    assert "31.7" not in txt and "hidden until 2099-01-01" in txt
    ans = bot_engine.format_experiment(row_for(entry(), ro(), "2026-11-17"))
    assert "hidden until 2026-11-24" in ans and not re.search(r"97|43 of|chance", ans)
    old = {"name": "x", "status": "live", "lifecycle": "live", "assignment_type": "split",
           "verdict": {"tldr": "24% vs 10%: 99% chance"}}
    assert "99%" not in bot_engine.format_experiment(old)
    monkeypatch.setenv("EXP_TAB_V2", "")
    assert "99%" in bot_engine.format_experiment(old)


def test_daily_brief_ab_block(monkeypatch):
    import ab_report
    monkeypatch.setenv("REC_PLAN_AB_PCT", "50")
    monkeypatch.setattr(readlock, "hidden", lambda e, today=None, locked=None: e.get("id") == ab_report.EXPERIMENT_ID)
    out = ab_report.block("tok", fetch_day=lambda *a: 1 / 0)
    assert "hidden" in out or "not read" in out


@pytest.mark.parametrize("name,p,n", [("price report", 0.171, 1148), ("2nd question", 0.48, 421), ("compare", 0.03, 131),
                                      ("bubble", 0.54, 105), ("pdf in 1 day", 0.60, 1300), ("paid in 7 days", 0.068, 350)])
def test_sim_aa_false_ship(name, p, n):
    rng = random.Random(20261125)
    ship = sum(v2.read_call({"n": n, "conv": binom(rng, n, p)}, {"n": n, "conv": binom(rng, n, p)})[0] == "ship"
               for _ in range(2000))
    assert ship / 2000 <= 0.05, (name, ship / 2000)


# --------------------------------------------------------------------------------------- 2 harm stop

def harm_fast(c, t, bar):
    """harm_p with a normal pre-filter, so the simulation only integrates the close calls."""
    if min(c["n"], t["n"]) < v2.HARM_MIN_N or c["conv"] + t["conv"] < v2.HARM_MIN_CONV:
        return False
    pc, pt = (c["conv"] + 1) / (c["n"] + 2), (t["conv"] + 1) / (t["n"] + 2)
    sd = math.sqrt(pc * (1 - pc) / c["n"] * .81 + pt * (1 - pt) / t["n"])
    return False if (pt - 0.9 * pc) / sd > -2.2 else v2.harm_p(c, t) <= bar


def run_harm(rng, rates, days, per_day, looks=1):
    """rates: [(old, new)] per watched measure, all sharing v2.HARM_P. The day the stop fires, or None."""
    tot = [[0, 0, 0, 0] for _ in rates]
    for k in range(1, days * looks + 1):
        n = 2 * per_day // looks + (1 if k % looks < (2 * per_day) % looks else 0)
        nc = binom(rng, n, .5)
        for (pc, pt), x in zip(rates, tot):
            x[0] += nc
            x[1] += binom(rng, nc, pc)
            x[2] += n - nc
            x[3] += binom(rng, n - nc, pt)
            if harm_fast({"n": x[0], "conv": x[1]}, {"n": x[2], "conv": x[3]}, v2.HARM_P / len(rates)):
                return k / looks
    return None


@pytest.mark.parametrize("name,rates,days,per_day,looks", [
    ("4 measures, every 6 hours", [(.171,) * 2, (.017,) * 2, (.48,) * 2, (.3,) * 2], 28, 42, 4),
    ("4 low measures, every 6 hours", [(.03,) * 2, (.017,) * 2, (.068,) * 2, (.1,) * 2], 35, 42, 4),
    ("4 measures, daily", [(.171,) * 2, (.017,) * 2, (.48,) * 2, (.3,) * 2], 28, 42, 1),
    ("1 measure, daily", [(.171,) * 2], 28, 42, 1), ("2nd question", [(.48,) * 2], 14, 9, 4), ("pdf", [(.6,) * 2], 28, 48, 4)])
def test_sim_aa_false_stop(name, rates, days, per_day, looks, capsys):
    rng = random.Random(7)
    hits = sum(run_harm(rng, rates, days, per_day, looks) is not None for _ in range(2000))
    with capsys.disabled():
        print(f"\n  false stop, {name}: {hits / 20:.2f}%")
    assert hits / 2000 <= 0.03, (name, hits / 2000)


@pytest.mark.parametrize("name,p,per_day,n", [("price report", .171, 42, 4), ("2nd question", .48, 9, 3), ("pdf", .6, 48, 2)])
def test_sim_60pct_drop_stops_within_14_days(name, p, per_day, n, capsys):
    rng = random.Random(11)
    rates = [(p, p * .4)] + [(.3, .3)] * (n - 1)          # the bar is shared with n - 1 healthy measures
    hits = sum(run_harm(rng, rates, 14, per_day) is not None for _ in range(300))
    with capsys.disabled():
        print(f"\n  60% drop caught in 14 days, {name}, {n} measures: {hits / 3:.1f}%")
    assert hits / 300 >= 0.80, (name, hits / 300)


def test_harm_direction_guardrails_and_small_samples():
    assert v2.harm_p({"n": 400, "conv": 100}, {"n": 400, "conv": 40}) <= v2.HARM_P
    assert v2.harm_p({"n": 400, "conv": 40}, {"n": 400, "conv": 100}) > 0.5
    assert v2.harm_p({"n": 400, "conv": 40}, {"n": 400, "conv": 100}, "decrease") <= v2.HARM_P
    assert v2.harm_p({"n": 20, "conv": 10}, {"n": 20, "conv": 0}) is None
    e = entry(guardrails=[{"key": "planner_return_within_days", "max_drop_rel": .25, "label": "came back"}])
    g = [{"key": "planner_return_within_days", "arms": {"control": {"n": 400, "conv": 120}, "treatment": {"n": 400, "conv": 40}}}]
    row = row_for(e, ro(c=(400, 40), t=(400, 41), guardrails=g), "2026-11-17")
    assert row["health"] == "guardrail_breach" and "came back" in row["reason"]
    assert row_for(entry(), ro(), "2026-11-17")["health"] != "guardrail_breach"


# --------------------------------------------------------------------------------------- 3 balance

def test_balance_planted_and_true():
    e = entry()
    assert v2.check_balance(e, {"exposed": {"control": 900, "treatment": 1100}})["colour"] == "red"
    assert v2.check_balance(e, {"exposed": {"control": 1000, "treatment": 1000}})["colour"] == "green"
    assert v2.check_balance(e, {"exposure_by_day": expo(20, 45, 55)}, 4000)["colour"] == "red"
    skew = entry(assignment={"type": "split", "rule": "hash_pct", "salt": "a", "pct": 20})
    assert v2.check_balance(skew, {"exposed": {"control": 1600, "treatment": 400}})["colour"] == "green"
    assert v2.check_balance(skew, {"exposed": {"control": 1000, "treatment": 1000}})["colour"] == "red"
    scipy = pytest.importorskip("scipy.stats")
    for c, t, s in ((900, 1100, .5), (10, 190, .9), (511, 489, .5), (0, 120, .5), (120, 0, .5)):
        tail = scipy.binom.cdf(t, c + t, s) if t <= (c + t) * s else scipy.binom.sf(t - 1, c + t, s)
        assert abs(v2.balance_p(c, t, s) - min(1, 2 * tail)) < 1e-9


def test_sim_true_split_rarely_red(capsys):
    rng = random.Random(5)
    one = sum(v2.check_balance(entry(), {"exposed": {"treatment": t, "control": 2000 - t}})["colour"] == "red"
              for t in (binom(rng, 2000, .5) for _ in range(2000)))
    assert one / 2000 <= 0.005
    ever = 0
    for _ in range(10000):                      # looked at every day of a 24-day run with the Price Report's plan
        rows, c, t = [], 0, 0
        for d in range(24):
            a = binom(rng, 84, .5)
            rows.append({"date": (dt.date(2026, 11, 1) + dt.timedelta(days=d)).isoformat(), "treatment": a, "control": 84 - a})
            t, c = t + a, c + 84 - a
            if abs(t - c) / math.sqrt(t + c) > 3.0 and \
                    v2.check_balance(entry(), {"exposure_by_day": rows}, 1148)["colour"] == "red":
                ever += 1
                break
    with capsys.disabled():
        print(f"\n  true 50/50 red at 2,000 couples: {one / 20:.2f}%; ever red over 24 daily looks: {ever / 100:.2f}%")
    assert ever / 10000 <= 0.005, ever / 10000


def test_balance_is_red_at_once():
    rows = [{"date": "2026-11-10", "control": 480, "treatment": 520}, {"date": "2026-11-11", "control": 445, "treatment": 555}]
    assert 1e-5 < v2.balance_p(925, 1075) < 0.001
    assert v2.check_balance(entry(), {"exposure_by_day": rows}, 1148)["colour"] == "red"
    early = [{"date": "2026-11-10", "control": 180, "treatment": 246}]
    assert v2.check_balance(entry(), {"exposure_by_day": early}, 1148)["colour"] == "amber"
    assert v2.check_balance(entry(), {"exposure_by_day": early}, 1148, final=True)["colour"] in ("amber", "red")


def test_sim_55_45_turns_red():
    rng = random.Random(6)
    hits = sum(v2.check_balance(entry(), {"exposed": {"treatment": t, "control": 2000 - t}})["colour"] == "red"
               for t in (binom(rng, 2000, .55) for _ in range(1000)))
    assert hits / 1000 >= 0.80      # a random 55/45 draw; exactly 1,100 / 900 is always red (above)


# --------------------------------------------------------------------------------------- 4 both arms / repeats

NOW = ms("2026-11-19T12:00:00Z")


def tables(n=300, dup=0, switch=0):
    users = [U(5000 + i, "2026-11-12T10:00:00Z", email="p%d@example.com" % i) for i in range(n)]
    for i in range(dup):                         # the same person, second account: +tag, dots and case ignored
        users[2 * i]["email"] = "same.person%d@gmail.com" % i
        users[2 * i + 1]["email"] = "SamePerson%d+x@googlemail.com" % i
    use = []
    for i in range(n):
        r = R(5000 + i, "2026-11-13T10:00:00Z")
        r["surface"] = "planner"
        if i >= n - switch:
            r["ctx_arm"] = "control" if (5000 + i) % 2 == 0 else "context"     # not the version this id should get
        use.append(r)
    return {1: users, 16: [], 79: use, 77: []}


CF = dict(id="EXP-2026-11-planner-context-first", metric={"key": "planner_second_question", "params": {}, "direction": "increase",
          "baseline": .48, "mde_rel": .2}, assignment={"type": "split", "rule": "user_id_parity", "treatment": "even"},
          start_date="2026-11-10", review_date="2026-12-01", surfaces=["rec-engine", "weweb:planner", "weweb:ask-tulle-bar"])


def run_x(e, tb, entries=None):
    c, _tr, _fc = fake_client(tb)
    out, _ctx = metrics.run_all(entries or [e], c, now_ms=NOW)
    return out if entries else out[e["id"]]


def test_repeat_accounts_found_counted_excluded_and_red():
    r = run_x(entry(**CF), tables(300, dup=5))
    assert r["mixing"]["repeat_accounts"] == 10 and r["mixing"]["excluded"] == 10
    assert r["arms"]["control"]["n"] + r["arms"]["treatment"]["n"] == 290 and sum(r["exposed"].values()) == 300
    chk = v2.check_mixing(entry(**CF), r)
    assert chk["colour"] == "red" and "10 repeat accounts" in chk["text"]
    ok = run_x(entry(**CF), tables(300, dup=1))
    chk = v2.check_mixing(entry(**CF), ok)                    # no version is stored: never "0 in both versions"
    assert ok["mixing"]["repeat_accounts"] == 2 and chk["colour"] == "amber" and "can't be checked" in chk["text"]
    assert "0 couples in both" not in chk["text"] and "0 in both" not in chk["text"]
    full = v2.check_mixing(entry(), {"mixing": {"exposed": 10, "checked": 10, "arm_recorded": 10, "both_arms": 0, "excluded": 0, "share": 0}})
    assert full["colour"] == "green" and "0 couples in both versions" in full["text"]
    assert "@" not in json.dumps(r) and "person" not in json.dumps(r).lower()


def test_arm_switchers_found_and_excluded():
    r = run_x(entry(**CF), tables(300, switch=6))
    assert r["mixing"]["switched"] == 6 and r["arms"]["control"]["n"] + r["arms"]["treatment"]["n"] == 294
    assert v2.check_mixing(entry(**CF), r)["colour"] == "red"


def test_no_person_key_when_switch_is_off(monkeypatch):
    from experiments_engine import xano
    assert xano.sanitize_row({"id": 1, "email": "a@b.co"}, 1).get("_who")
    monkeypatch.setenv("EXP_TAB_V2", "")
    assert "_who" not in xano.sanitize_row({"id": 1, "email": "a@b.co"}, 1)


# --------------------------------------------------------------------------------------- 5 assignment and settings

def test_engine_arm_matches_shipped_code_for_20000_ids():
    with open(os.path.join(ROOT, "experiments", "experiment_log.json"), encoding="utf-8") as f:
        reg = {e["id"]: e for e in json.load(f)["experiments"]}
    ids = range(1, 20001)
    parity = reg["EXP-2026-11-planner-context-first"]["assignment"]          # component: id % 2 === 0 ? 'context'
    assert all((metrics.split_arm(i, parity) == "treatment") == (i % 2 == 0) for i in ids)
    for eid in ("EXP-2026-11-planner-compare", "EXP-2026-11-price-report-v2"):
        a = reg[eid]["assignment"]
        for i in ids:
            h = int(hashlib.sha256(f"{a['salt']}:{i}".encode()).hexdigest()[:8], 16) % 100
            assert (metrics.split_arm(i, a) == "treatment") == (h < 50)
    # the shipped Compare function itself, in its own process (rec_service has its own module names)
    code = ("import config,compare\nconfig.COMPARE_PICK=True;config.COMPARE_AB_PCT=50;config.COMPARE_USER_IDS=set()\n"
            "print(config.COMPARE_AB_SALT,''.join('1' if compare.arm(i)=='compare' else '0' for i in range(1,20001)))")
    got = subprocess.run([sys.executable, "-c", code], cwd=os.path.join(ROOT, "rec_service"), capture_output=True, text=True)
    if got.returncode:
        pytest.skip("rec_service not importable here: " + got.stderr[-200:])
    salt, arms = got.stdout.split()
    a = reg["EXP-2026-11-planner-compare"]["assignment"]
    assert salt == a["salt"] and arms == "".join("1" if metrics.split_arm(i, a) == "treatment" else "0" for i in ids)


def test_price_report_arm_matches_the_component(tmp_path):
    src = os.path.join(os.path.dirname(ROOT), "ww-price-report-v2", "src", "v2")
    node = shutil.which("node")
    if not node or not os.path.exists(os.path.join(src, "arm.js")):
        pytest.skip("component checkout or node not here")
    for f in ("arm.js", "sha256.js"):
        shutil.copy(os.path.join(src, f), tmp_path / f)
    (tmp_path / "package.json").write_text('{"type":"module"}')
    (tmp_path / "a.js").write_text("import {armFor} from './arm.js';let o='';for(let i=1;i<=20000;i++)"
                                   "o+=armFor('split',i)==='v2'?'1':'0';console.log(o)")
    got = subprocess.run([node, "a.js"], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert got.returncode == 0, got.stderr[:300]
    a = {"type": "split", "rule": "hash_pct", "salt": "price-report-v2", "pct": 50}
    assert got.stdout.strip() == "".join("1" if metrics.split_arm(i, a) == "treatment" else "0" for i in range(1, 20001))


@pytest.mark.parametrize("change", [
    {"assignment": {"type": "split", "rule": "hash_pct", "salt": "t2", "pct": 50}},
    {"assignment": {"type": "split", "rule": "hash_pct", "salt": "t1", "pct": 60}},
    {"assignment": {"type": "split", "rule": "user_id_parity", "treatment": "even"}},
    {"start_date": "2026-11-11"}, {"metric": {"key": "pdf_open_within_1d", "direction": "increase"}},
    {"metric": {"key": "new_paid_within_days", "params": {"days": 7}, "direction": "increase"}},
    {"review_date": "2026-12-24"}])
def test_changed_settings_turn_red(change, tmp_path):
    st = history.HistoryStore(str(tmp_path))
    assert colour(row_for(entry(), ro(), "2026-11-12", st), "settings") == "green"
    assert colour(row_for(entry(**change), ro(), "2026-11-13", st), "settings") == "red"
    locked = entry(launch_lock=v2.launch_lock(entry()))
    assert v2.check_settings(locked, None)["colour"] == "green"
    assert v2.check_settings(dict(locked, **change), None)["colour"] == "red"
    only_hash = dict(locked, launch_lock={"hash": locked["launch_lock"]["hash"]}, **change)
    assert v2.check_settings(only_hash, None)["colour"] == "red"


def test_writing_a_default_out_is_not_a_change():
    lk = v2.launch_lock(entry())
    same = entry(read_date="2026-11-24", name="Renamed", tldr="new text")
    same["assignment"] = dict(same["assignment"], salt="t1:", pct=50.0, note="x")
    same["metric"] = dict(same["metric"], baseline=.11)
    assert v2.check_settings(dict(same, launch_lock=lk), lk)["colour"] == "green"


# --------------------------------------------------------------------------------------- 6 exposure

def test_context_first_counts_the_planner_page_only():
    def row(uid, when, surface):
        r = R(uid, when)
        r["surface"] = surface
        return r
    use = [row(7001, "2026-11-12T10:00:00Z", "planner"), row(7001, "2026-11-12T11:00:00Z", "planner"),
           row(7002, "2026-11-12T10:00:00Z", "bubble:/vendors/a"), row(7003, "2026-11-12T10:00:00Z", ""),
           row(7006, "2026-11-12T10:00:00Z", "plannerx")]
    tb = {1: [], 16: [], 79: use, 77: []}
    r = run_x(entry(**CF), tb)
    assert sum(a["n"] for a in r["arms"].values()) == 1 and sum(a["conv"] for a in r["arms"].values()) == 1
    both = entry(**CF, exposure={"source": "xano:table79", "surfaces": ["planner", "bubble"]})
    assert sum(a["n"] for a in run_x(both, tb)["arms"].values()) == 2


def test_exposure_rule_limits_a_sign_up_metric():
    e = entry(exposure={"source": "xano:table79", "surfaces": ["bubble"]}, review_date="2026-12-01")
    tb = tables(40)
    for r in tb[79][:10]:
        r["surface"] = "bubble:/x"
    assert sum(run_x(e, tb)["exposed"].values()) == 10


def test_exposure_stop():
    def c(rows, today, est=100):
        return v2.check_exposure(entry(audience={"segment": "s", "est_daily_exposed": est}), {"exposure_by_day": rows},
                                 dt.date.fromisoformat(today))["colour"]
    assert c(expo(8), "2026-11-18") == "green"
    assert c(expo(8), "2026-11-20") == "red"              # two whole days with nobody, ~200 expected
    assert c(expo(8, 1, 1), "2026-11-20", 2) == "amber"   # ~4 expected
    assert c([{"date": "2026-11-12", "control": 0, "treatment": 1}, {"date": "2026-11-17", "control": 1, "treatment": 0}],
             "2026-11-20", .3) == "green"                 # a trickle
    assert c([], "2026-11-13") == "red"                   # never started
    one_arm = expo(8) + [{"date": d, "control": 50, "treatment": 0} for d in ("2026-11-18", "2026-11-19", "2026-11-20")]
    assert c(one_arm, "2026-11-20") == "red"


def test_validate_refuses_a_live_split_with_no_exposure_source(tmp_path):
    e = entry(reporting="manual", metric={"key": "manual", "direction": "increase", "baseline": .1, "mde_rel": .5})
    good = dict(e, exposure={"source": "mixpanel:card_seen"})
    for x, code in ((e, 1), (dict(good, launch_lock=v2.launch_lock(good)), 0)):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"experiments": [x], "changes": []}), encoding="utf-8")
        for extra in ([], ["--id", x["id"]]):
            got = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "validate.py"), "--registry", str(p), *extra],
                                 capture_output=True, text=True, env=dict(os.environ, EXP_TAB_V2="1"))
            assert got.returncode == code, got.stdout
    assert colour(row_for(e, None, "2026-11-17"), "exposure") == "red"


# --------------------------------------------------------------------------------------- 7 coverage

def test_pdf_metric_drops_uncovered_accounts(tmp_path):
    users = [U(6000 + i, "2026-11-12T10:00:00Z") for i in range(200)]
    hist = {str(6000 + i): {"created_at": ms("2026-11-12T10:00:00Z"), "first_seen": ms("2026-11-12T12:00:00Z"),
                            "last_zero": ms("2026-11-16T00:00:00Z"), "first_positive": ms("2026-11-12T13:00:00Z") if i % 2 else None}
            for i in range(199)}
    path = tmp_path / "pdf_first_open.json"
    e = entry(metric={"key": "pdf_open_within_1d", "params": {}, "direction": "increase", "baseline": .6, "mde_rel": .1},
              review_date="2026-12-01", assignment={"type": "split", "rule": "user_id_parity", "treatment": "even"})

    def run():
        path.write_text(json.dumps(hist))
        c, _t, _f = fake_client({1: users, 16: [], 79: [], 77: []})
        return metrics.run_all([e], c, now_ms=NOW, data_dir=str(tmp_path))[0][e["id"]]
    r = run()
    assert not r.get("approximate") and sum(a["n"] for a in r["arms"].values()) == 199
    assert {k: (v["covered"], v["total"]) for k, v in r["coverage"].items()} == {"control": (99, 100), "treatment": (100, 100)}
    assert v2.check_coverage(r)["colour"] == "green" and sum(r["exposed"].values()) == 200
    for i in (1, 3, 5):
        del hist[str(6000 + i)]
    assert v2.check_coverage(run())["colour"] == "red"


# --------------------------------------------------------------------------------------- 8 payments

def test_payments_by_arm_never_before_after():
    tb = tables(40)
    tb[16] = [P(str(5000 + i), "2026-11-14T10:00:00Z", 30) for i in range(6)] + \
             [P("5001", "2026-11-13T09:00:00Z", 30), P("9999", "2026-11-14T10:00:00Z", 30), P("5003", "2026-11-01T10:00:00Z", 30)]
    e = entry(**CF, guardrails=[{"key": "purchases_per_day", "max_drop_rel": .25}])
    r = run_x(e, tb)
    assert {k: v["payers"] for k, v in r["payments"]["arms"].items()} == {"treatment": 3, "control": 3}   # exposed only
    assert all("count" not in g for g in r["guardrails"])
    manual = entry(reporting="manual", metric={"key": "manual", "label": "tapped"}, guardrails=e["guardrails"], review_date="2026-12-01",
                   exposure={"source": "mixpanel:seen"}, assignment=e["assignment"])
    m = run_x(manual, tb)
    assert m["payments"]["basis"] == "assigned"
    assert {k: v["payers"] for k, v in m["payments"]["arms"].items()} == {"treatment": 3, "control": 4}
    row = row_for(manual, m, "2026-11-19")
    assert row["guardrail_status"][0]["status"] == "watch"
    txt = row["card"]["payments_text"]
    assert "can't be read" in txt and "new version 3" in txt and "old version 4" in txt


def test_payments_hidden_by_arm_when_they_are_the_result():
    r = ro(payments={"basis": "exposed", "arms": {"treatment": {"payers": 97, "n": 400}, "control": {"payers": 43, "n": 400}}})
    row = row_for(entry(), r, "2026-11-17")
    assert not re.search(r"\b97\b(?!\.5%)", json.dumps(row)) and "140 couples paid in all" in row["card"]["payments_text"]


def test_whole_site_count_can_not_be_a_split_metric():
    e = entry(metric={"key": "purchases_per_day", "direction": "increase"}, review_date="2026-12-01")
    assert "can't be read inside a split" in run_x(e, tables(10))["error"]


def test_manual_rollout_guardrails_are_computed_and_payments_only_watched():
    e = entry(id="EXP-2026-11-r", reporting="manual", metric={"key": "manual"}, assignment={"type": "rollout"},
              guardrails=[{"key": "purchases_per_day", "max_drop_rel": .25}, {"key": "planner_second_question", "max_drop_rel": .25}])
    tb = tables(10)
    tb[16] = [P(str(400 + i), "2026-11-0%dT10:00:00Z" % (1 + i % 9), 30) for i in range(90)]
    row = row_for(e, run_x(e, tb), "2026-11-19")
    st = {g["key"]: g["status"] for g in row["guardrail_status"]}
    assert st["purchases_per_day"] == "watch" and row["health"] != "guardrail_breach"
    assert "Payments, watch only" in row["card"]["payments_text"]


# --------------------------------------------------------------------------------------- 9 sample plan

def test_two_sided_plan():
    assert stats.sample_plan(.171, mde_abs=.045)["n_per_arm"] >= 1100
    assert abs(stats.sample_plan(.03, mde_rel=3)["n_per_arm"] - 130) <= 6
    z = (1.959964 + 0.841621) ** 2
    for p, d in ((.171, .045), (.03, .09), (.48, .096), (.6, .05)):
        want = z * (p * (1 - p) + (p + d) * (1 - p - d)) / d ** 2
        assert abs(stats.sample_plan(p, mde_abs=d)["n_per_arm"] / want - 1) < .05
    assert row_for(entry(), ro(), "2026-11-17")["sample_plan"]["two_sided"]


# --------------------------------------------------------------------------------------- 10 / 11 cards

FAMILY = [{"version": f"V{i}", "from": f"2026-10-{10 + i}", "to": f"2026-10-{11 + i}", "entry": f"EXP-2026-10-v{i}",
           "outcome": f"outcome {i}"} for i in range(5)] + [
    {"version": "Now", "from": "2026-11-10", "to": None, "entry": "EXP-2026-11-t", "outcome": "5 of 93 paid"}]


def test_family_history_on_one_card_and_not_counted():
    from experiments_ui import cards
    pp = entry(assignment={"type": "pre_post", "pre": ["2026-11-01", "2026-11-09"], "post_start": "2026-11-10"},
               family="f", family_card=True, family_history=FAMILY)
    old = [entry(id=f"EXP-2026-10-v{i}", status="reverted", family="f") for i in range(3)]
    s = snap_for([pp] + old, ro(), "2026-11-17")
    page = cards.build_html(s)
    assert s["summary"]["decided"] == 0 and s["summary"]["live"] == 1
    assert page.count('<details class="row"') == 1 and page.count("outcome ") == 5 and "6, one test" in page
    split = entry(family="f", family_card=True, family_history=FAMILY)
    assert "5 of 93" not in json.dumps(row_for(split, ro(), "2026-11-17"))


def test_overlap_text_now_and_2x2_only_at_the_read():
    from experiments_ui import cards
    a = entry(**dict(CF, id="EXP-2026-11-a"), exposure={"source": "xano:table79", "surfaces": ["planner"]})
    b = entry(**dict(CF, id="EXP-2026-11-b", assignment={"type": "split", "rule": "hash_pct", "salt": "bb", "pct": 50}),
              exposure={"source": "xano:table79", "surfaces": ["planner"]})
    out = run_x(a, tables(200), entries=[a, b])
    assert out["EXP-2026-11-a"]["overlap"]["EXP-2026-11-b"]["shared"] == 200
    c = snap_for([a, b], lambda e: out[e["id"]], "2026-11-19")["experiments"][0]["card"]
    assert "Shares couples with" in c["overlaps"][0]["text"] and not c["overlaps"][0]["cells"]
    later = dict(b, review_date="2026-12-20")                 # one read, the other not: nothing about either
    s = snap_for([a, later], lambda e: out[e["id"]], "2026-12-02")
    assert s["experiments"][0]["read"]["call"] and not s["experiments"][0]["card"]["overlaps"][0]["cells"]
    assert '"cells": {"' not in json.dumps(s) and "treatment|" not in json.dumps(s)
    s = snap_for([a, b], lambda e: out[e["id"]], "2026-12-01")
    cells = s["experiments"][0]["card"]["overlaps"][0]["cells"]
    assert sum(x["n"] for x in cells.values()) == 200 and len(cells) == 4
    assert "Other test: new" in cards.build_html(s)


def test_same_arms_are_called_out():
    a, b = entry(id="EXP-2026-11-a", surfaces=["weweb:x"]), entry(id="EXP-2026-11-b", surfaces=["weweb:y"])
    s = snap_for([a, b], ro(), "2026-11-17")
    assert "can't be told apart" in s["experiments"][0]["card"]["overlaps"][0]["text"]


# --------------------------------------------------------------------------------------- 12 look

def test_colour_is_health_only_and_text_is_escaped():
    from experiments_ui import cards
    page = cards.build_html(snap_for(entry(name="<script>x</script>"), ro(), "2026-11-24"))
    assert "<script>x" not in page
    res = re.search(r'<p class="result">.*?</p>', page, re.S).group(0)
    assert not re.search(r"green|amber|red\b", res)


def test_real_registry_builds_every_card():
    from experiments_ui import cards
    with open(os.path.join(ROOT, "experiments", "experiment_log.json"), encoding="utf-8") as f:
        reg = json.load(f)
    s = snapshot.build_snapshot(reg, None, None, "2026-11-25")
    assert not [x for x in s["errors"] if x["stage"] in ("card", "evaluate")]
    assert all(r.get("card") for r in s["experiments"]) and cards.build_html(s)


# --------------------------------------------------------------------------------------- 13 off = today

@pytest.mark.parametrize("value", [None, "", "0", "off", "2", "v1"])
def test_off_has_no_v2_anything(value, monkeypatch):
    if value is None:
        monkeypatch.delenv("EXP_TAB_V2")
    else:
        monkeypatch.setenv("EXP_TAB_V2", value)
    s = snap_for(entry(), ro(), "2026-11-24")
    txt = json.dumps(s)
    assert "exp_tab_v2" not in s and '"card"' not in txt and "result_hidden" not in txt
    assert s["experiments"][0]["stats"]["p_beat"] > .9
    assert stats.sample_plan(.171, mde_abs=.045)["n_per_arm"] == 865


def test_non_split_rows_keep_every_field_they_had(monkeypatch):
    e = entry(assignment={"type": "pre_post", "pre": ["2026-11-01", "2026-11-09"], "post_start": "2026-11-10"},
              guardrails=[{"key": "planner_return_within_days", "max_drop_rel": .25}])
    g = [{"key": "planner_return_within_days", "arms": {"control": {"n": 400, "conv": 211}, "treatment": {"n": 400, "conv": 205}}}]
    on = row_for(e, ro(guardrails=g), "2026-11-17")
    monkeypatch.setenv("EXP_TAB_V2", "")
    off = row_for(e, ro(guardrails=g), "2026-11-17")
    assert {k: on[k] for k in off} == off


# --------------------------------------------------------------------------------------- event reads (Price Report v2)

def ev(event, uid, when, **kw):
    return {"event": event, "user_id": uid, "time": ms(when), **kw}


def test_event_read_rules():
    e = entry(id="EXP-2026-11-price-report-v2", start_date="2026-12-01", review_date="2026-12-29", reporting="manual",
              assignment={"type": "split", "rule": "hash_pct", "salt": "price-report-v2", "pct": 50},
              metric={"key": "manual", "label": "tapped"}, exposure={"source": "mixpanel:card-seen"})
    T = [u for u in range(1000, 1400) if metrics.split_arm(u, e["assignment"]) == "treatment"][:20]
    C = [u for u in range(1000, 1400) if metrics.split_arm(u, e["assignment"]) == "control"][:20]
    evs = []
    for i, u in enumerate(T + C):                # the event's own arm says nothing, half are unsettled: all are kept
        evs.append(ev("card-seen", u, "2026-12-02T10:00:00Z", arm="none", access_settled=bool(i % 2), locked=True))
    evs += [ev("unlock-clicked", T[0], "2026-12-05T10:00:00Z", access_settled=False),
            ev("unlock-clicked", T[1], "2026-12-12T10:00:00Z"), ev("unlock-clicked", C[0], "2026-12-03T10:00:00Z"),
            ev("late-id", T[2], "2026-12-02T10:01:00Z", arm="none", late_arm="v2"),
            ev("late-id", C[2], "2026-12-02T10:01:00Z", arm="v1", late_arm="v1"),    # dropped although arm == late_arm
            ev("card-seen", T[3], "2026-12-03T10:00:00Z", arm="v1"), ev("card-seen", T[3], "2026-12-04T10:00:00Z", arm="v2"),
            ev("card-seen", 0, "2026-12-02T10:00:00Z"), ev("card-seen", "", "2026-12-02T10:00:00Z")]
    pay = [P(str(T[4]), "2026-11-28T10:00:00Z", 30, "1 week"),       # still a member at first card-seen
           P(str(C[4]), "2026-11-01T10:00:00Z", 30, "1 week"),       # expired: a free couple again
           P(str(T[5]), "2026-12-02T11:00:00Z", 30, "1 week"),       # bought AFTER seeing it: kept
           P(str(C[5]), "2026-01-01T10:00:00Z", 75, "Forever weeks")]
    r = event_read.read_events(e, evs, pay, now_ms=ms("2026-12-15T00:00:00Z"))
    assert r["members_left_out"] == 2 and r["exposed"] == {"treatment": 19, "control": 19}
    assert r["mixing"]["late_id"] == 2 and r["mixing"]["both_arms"] == 1 and r["mixing"]["excluded"] == 3
    assert r["arms"] == {"treatment": {"n": 17, "conv": 1}, "control": {"n": 18, "conv": 1}}
    assert 0 < r["settled"]["treatment"]["settled"] < r["settled"]["treatment"]["views"]
    row = row_for(dict(e, exposure_log=event_read.exposure_log(r)), None, "2026-12-15")
    assert colour(row, "mixing") == "red" and row["result_hidden"]
    assert not [c for c in row["checks"] if c["key"] == "settled"] and "settled" not in json.dumps(row)
    assert '"conv"' not in json.dumps(event_read.exposure_log(r))


def test_settled_share_is_never_red():
    c = v2.check_settled(entry(), {"settled": {"treatment": {"settled": 10, "views": 500}, "control": {"settled": 490, "views": 500}}})
    assert c["colour"] == "amber"


def test_flat_history_without_daily_counts_and_approximate_metric():
    h = [{"date": "2026-11-14", "n_total": 800}]
    out = v2.evaluate_split(entry(), ro(exposure_by_day=None), h, [], "2026-11-17")
    assert colour(out, "exposure") == "red"
    assert row_for(entry(), ro(approximate=True), "2026-11-24")["read"]["call"] == "untrusted"


# --------------------------------------------------------------------------------------- 2026-11-25 independent check

LEAK = re.compile(r"31\.7|97 of|11\.1")


def leaky(**over):
    e = entry(results=[{"date": "2026-11-05", "note": "31.7% vs 11.1%", "counts": _cn(43, 97)}], decision="kept, 31.7% vs 11.1%",
              stat_sig="31.7", confounds_noted=["31.7"])
    e.update(over)
    return e


def every_surface(e, today, tmp_path, monkeypatch, readout=None):
    """Text of every surface this repo has for one entry."""
    from experiments_ui import cards, render
    sys.path.insert(0, os.path.join(ROOT, "slack_qa"))
    import engine as bot_engine
    import knowledge
    reg = {"experiments": [e], "changes": []}
    monkeypatch.setattr(readlock, "utc_today", lambda: dt.date.fromisoformat(today))
    monkeypatch.setattr(knowledge, "load_registry", lambda *a, **k: reg)
    s = snapshot.build_snapshot(reg, (lambda x: copy.deepcopy(readout or ro())), history.HistoryStore(str(tmp_path)), today)
    deg = render.degraded_snapshot(reg, today)
    mini = render._minimal_snapshot(reg, dt.date.fromisoformat(today))
    rows = s["experiments"] + s["decided"]
    out = [json.dumps(s), json.dumps(deg), json.dumps(mini), cards.build_html(s).split("</style>")[1], cards.build_html(deg).split("</style>")[1],
           json.dumps(knowledge.bot_view(reg)), knowledge.headlines(3650), str(alerts.format_weekly(s, today)), str(alerts.format_digest(s)),
           str(alerts.format_calls(s, alerts.call_ids(s)))] + [bot_engine.format_experiment(r) for r in rows]
    monkeypatch.setenv("EXP_TAB_V2", "")
    raw = snapshot.build_snapshot(reg, (lambda x: copy.deepcopy(readout or ro())), None, today)       # an engine without the switch
    monkeypatch.setenv("EXP_TAB_V2", "1")
    out += [bot_engine.format_experiment(r) for r in raw["experiments"] + raw["decided"]]
    out.append(json.dumps(v2.lock_snapshot(raw, reg, today)))
    out.append(json.dumps(readlock.redact_registry(reg)))
    return out


LOCKED = v2.launch_lock(entry())


@pytest.mark.parametrize("over", [
    {"status": "shipped", "decided_on": "2026-11-15"}, {"status": "inconclusive"}, {"status": "concluded"}, {"status": "reverted", "end_date": "2026-11-14"},
    {"assignment": {"type": "pre_post", "pre": ["2026-11-01", "2026-11-09"], "post_start": "2026-11-10"}, "launch_lock": LOCKED},
    {"assignment": {"type": "pre_post", "rule": "hash_pct", "salt": "t1", "pct": 50}}, {"assignment": {"type": "rollout"}, "launch_lock": LOCKED},
    {"assignment": {"type": "rollout", "split": True}}, {"status": "planned", "start_date": None}, {"status": "draft"}, {}, {"review_date": "2026-11-12", "launch_lock": LOCKED},
    {"read_date": "2026-11-11", "review_date": "2026-11-11", "planned_days": 1, "launch_lock": LOCKED},
    {"status": "shipped", "decided_on": "2026-11-15", "review_date": "2026-11-12", "launch_lock": LOCKED}])
def test_no_surface_opens_before_the_read_date(over, tmp_path, monkeypatch):
    for i, t in enumerate(every_surface(leaky(**over), "2026-11-17", tmp_path, monkeypatch)):
        assert not LEAK.search(t), (i, LEAK.search(t).group(0), t[:200])
        assert not FORBIDDEN.search(t), (i, FORBIDDEN.search(t).group(0))


def test_stopped_early_stays_unread_and_says_so(tmp_path, monkeypatch):
    e = leaky(status="shipped", decided_on="2026-11-15", launch_lock=LOCKED)
    for today in ("2026-11-17", "2027-03-01"):
        out = every_surface(e, today, tmp_path, monkeypatch)
        assert not any(LEAK.search(t) for t in out)
        assert any("Stopped early, result not read" in t for t in out[:1]) and "Stopped early, result not read" in out[3]


def test_type_change_is_red_and_never_read(tmp_path):
    st = history.HistoryStore(str(tmp_path))
    row_for(entry(), ro(), "2026-11-12", st)                  # the engine saw it as a split; no registry lock
    e = entry(assignment={"type": "pre_post", "pre": ["2026-11-01", "2026-11-09"], "post_start": "2026-11-10"})
    row = row_for(e, ro(), "2026-11-17", st)
    assert row["result_hidden"] and colour(row, "settings") == "red" and not FORBIDDEN.search(json.dumps(row))
    row = row_for(e, ro(), "2026-11-30", st)
    assert (row["read"] or {}).get("call") is None and row["light"] == "red"


def test_changed_settings_show_red_on_the_bot_and_in_the_registry_view():
    e = entry(launch_lock=LOCKED, review_date="2026-11-12")
    out = readlock.redact_entry(e, "2026-11-17")
    assert out["setup"].startswith("RED") and "read date" in out["setup"] and "2026-11-24" in out["result"]
    moved = entry(launch_lock=LOCKED, assignment={"type": "split", "rule": "hash_pct", "salt": "zz", "pct": 50})
    assert "salt" in readlock.redact_entry(moved, "2026-12-30")["setup"]
    edited = copy.deepcopy(LOCKED)
    edited["settings"]["read_date"] = "2026-11-11"
    assert v2.check_settings(entry(launch_lock=edited), None)["colour"] == "red"


def test_one_read_date_source():
    from experiments_ui import render
    e = entry(launch_lock=LOCKED)
    reg = {"experiments": [e], "changes": []}
    for d, hid in (("2026-11-23", True), ("2026-11-24", False)):
        assert readlock.hidden(e, d) is hid
        assert bool(row_for(e, ro(), d)["result_hidden"]) is hid
        assert bool(render.degraded_snapshot(reg, d)["experiments"][0]["result_hidden"]) is hid
        assert ("result" in readlock.redact_entry(e, d)) is hid
    # without a launch_lock the text surfaces and the registry-only tab never open it
    assert "result" in readlock.redact_entry(entry(), "2027-01-01")
    assert render.degraded_snapshot({"experiments": [entry()], "changes": []}, "2027-01-01")["experiments"][0]["result_hidden"]


def test_harm_is_never_green_when_not_checked():
    small = row_for(entry(), ro(c=(20, 5), t=(20, 5)), "2026-11-17")
    h = next(c for c in small["checks"] if c["key"] == "harm")
    assert h["colour"] == "amber" and "Not checked" in h["text"] and "too few couples" in h["text"]
    man = entry(reporting="manual", metric={"key": "manual", "label": "tapped"}, exposure={"source": "mixpanel:seen_it"}, exposure_log=expo())
    h = next(c for c in row_for(man, None, "2026-11-17")["checks"] if c["key"] == "harm")
    assert h["colour"] == "amber" and "no outcome feed" in h["text"] and "no clear harm" not in h["text"]
    h = next(c for c in row_for(entry(), {"error": "boom"}, "2026-11-17")["checks"] if c["key"] == "harm")
    assert h["colour"] == "amber" and "metric failed" in h["text"]
    ok = next(c for c in row_for(entry(), ro(c=(400, 43), t=(400, 44)), "2026-11-17")["checks"] if c["key"] == "harm")
    assert ok["colour"] == "green" and "did the thing" in ok["text"]


def test_one_harm_look_a_day_and_a_fired_stop_stays(tmp_path):
    st = history.HistoryStore(str(tmp_path))
    assert row_for(entry(), ro(c=(400, 43), t=(400, 44)), "2026-11-16", st)["health"] != "guardrail_breach"
    bad = ro(c=(400, 120), t=(400, 40))
    assert row_for(entry(), bad, "2026-11-16", st)["health"] != "guardrail_breach"       # today's look is taken
    assert row_for(entry(), bad, "2026-11-17", st)["health"] == "guardrail_breach"
    assert row_for(entry(), ro(c=(400, 43), t=(400, 44)), "2026-11-18", st)["health"] == "guardrail_breach"


@pytest.mark.parametrize("env", ["", "0", "1"])
@pytest.mark.parametrize("source", [None, "none", "todo", "everyone by default", "mixpanel:todo", "mixpanel:<event>", "manual", "xano"])
def test_validate_refuses_whatever_the_switch(env, source, tmp_path):
    e = entry(**({"exposure": {"source": source}} if source else {}))
    e["launch_lock"] = v2.launch_lock(e)
    good = entry(exposure={"source": "mixpanel:price_report_card_seen"})
    good["launch_lock"] = v2.launch_lock(good)
    planned = dict(e, status="planned", start_date=None)

    def run(x, *extra):
        p = tmp_path / "r.json"
        p.write_text(json.dumps({"experiments": [x], "changes": []}), encoding="utf-8")
        g = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "validate.py"), "--registry", str(p), *extra],
                           capture_output=True, text=True, env=dict(os.environ, EXP_TAB_V2=env))
        return g.returncode, g.stdout
    assert run(e)[0] == 1 and run(e, "--id", e["id"])[0] == 1 and run(planned, "--id", e["id"])[0] == 1
    if source is None:
        assert run(good)[0] == 0 and run(good, "--id", good["id"])[0] == 0
        nolock = {k: v for k, v in good.items() if k != "launch_lock"}
        assert run(nolock)[0] == 1
        assert run(dict(good, review_date="2026-11-20"))[0] == 1 and "RED" in run(dict(good, review_date="2026-11-20"))[1]


def test_sample_pace_counts_finished_couples_only():
    e = entry(metric={"key": "manual", "label": "tapped within 7 days", "direction": "increase", "baseline": .171, "mde_rel": .27},
              reporting="manual", exposure={"source": "mixpanel:seen_it"}, exposure_log=expo(16, 40, 40, "2026-11-25"), review_date="2026-12-08",
              planned_days=28)
    assert v2.follow_days(e) == 7
    row = row_for(e, None, "2026-11-25")
    assert row["exposed"]["control"] == 640 and row["finished"]["control"] == 9 * 40
    assert row["card"]["so_far"] == 360
    txt = next(c["text"] for c in row["checks"] if c["key"] == "sample")
    assert "945" in txt                                        # 360 / 8 finished days * (28 - 7) days, not 640 / 15 * 28


@pytest.mark.parametrize("label", ["upgraded within 7 days", "subscribed", "picked a plan", "tapping upgrade", "bought"])
def test_money_like_labels_hide_payments_by_version(label):
    assert v2.is_money_metric(entry(metric={"key": "manual", "label": label}))
    assert not v2.is_money_metric(entry(metric={"key": "manual", "label": "asked the Planner a question"}))


def test_stop_and_never_decides_are_never_side_by_side():
    pay = {"basis": "exposed", "arms": {"treatment": {"payers": 2, "n": 400}, "control": {"payers": 30, "n": 400}}}
    row = row_for(entry(metric={"key": "x", "label": "asked"}), ro(c=(400, 120), t=(400, 40), payments=pay), "2026-11-17")
    txt = json.dumps(row)
    assert row["health"] == "guardrail_breach" and "never decides" not in txt


def test_row_is_short_plain_and_not_repeated():
    from experiments_ui import cards
    e = entry(plain_name="Ask for context first", plain_line="New Planner couples get three starter questions.",
              audience={"segment": "signed-in Planner couples (split by user id parity)", "est_daily_exposed": 100}, what_changed="X" * 500)
    s = snap_for(e, ro(), "2026-11-17")
    page = cards.build_html(s).split("</style>")[1]
    summ = re.search(r'<details class="row".*?</summary>', page, re.S).group(0)
    text = re.sub(r"<[^>]+>", " ", summ)
    assert "Ask for context first" in text and "three starter questions" in text and len(text) < 260
    assert page.count("three starter questions") == 1 and page.count("X" * 500) == 1
    assert "parity" not in page and "half by account number" in page
