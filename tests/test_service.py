"""Offline tests for the experiments-engine service, change feed and alerts.
Run: python -m pytest tests/test_service.py -q   (needs fastapi + httpx for the HTTP tests)

No test touches the network: every collector gets a fake ``http``, and requests.post/get are
monkeypatched to raise wherever a real call could happen.
"""
import datetime as dt
import gzip
import json
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from experiments_engine import alerts, changefeed, history, service  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_REGISTRY = os.path.join(os.path.dirname(HERE), "experiments", "experiment_log.json")
UTC = dt.timezone.utc

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


# ------------------------------------------------------------------------------------ fakes

class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t = self.t + dt.timedelta(**kw)


class Resp:
    def __init__(self, status=200, text="", data=None, headers=None):
        self.status_code = status
        self.text = text
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data


class FakeHTTP:
    """route(url, params, headers) -> Resp. Records every GET; any POST fails the test."""

    def __init__(self, route=None):
        self.route = route or (lambda url, params, headers: Resp(503))
        self.gets = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, params, dict(headers or {})))
        return self.route(url, params, headers or {})

    def post(self, *a, **k):
        raise AssertionError("unexpected POST from a collector")


class NoNetwork:
    def get(self, *a, **k):
        raise AssertionError("network GET attempted")

    def post(self, *a, **k):
        raise AssertionError("network POST attempted")


class Sender:
    def __init__(self, ok=True):
        self.sent = []
        self.ok = ok

    def __call__(self, text):
        self.sent.append(text)
        return {"ok": self.ok, "error": None if self.ok else "boom"}


def exp_row(eid="EXP-2026-11-a", health="on_track", stale=False, **kw):
    r = {"id": eid, "name": f"Test {eid}", "status": "live", "health": health, "stale": stale,
         "action": "Keep running", "reason": "fine",
         "stats": {"p_beat": 0.93, "c_n": 300, "c_conv": 12, "t_n": 310, "t_conv": 20},
         "readout": {"arms": {"control": {"n": 300, "conv": 12}, "treatment": {"n": 310, "conv": 20}}},
         "guardrails": []}
    r.update(kw)
    return r


def make_snapshot(rows, decided=None, today=None):
    live = [r for r in rows if r.get("health")]
    return {"generated_at": None, "today": today,
            "summary": {"needs_call": sum(r["health"] in ("needs_call", "callable_win", "callable_loss") for r in live),
                        "stale": sum(bool(r.get("stale")) for r in live),
                        "healthy": sum(r["health"] == "on_track" and not r.get("stale") for r in live),
                        "guardrail": sum(r["health"] == "guardrail_breach" for r in live),
                        "live": len(live)},
            "action_queue": [], "experiments": rows, "decided": decided or [], "changes": [], "errors": []}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("EXP_API_KEY", "k-test")
    monkeypatch.setenv("EXP_HOOK_SECRET", "h-test")
    monkeypatch.setenv("EXP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("GITHUB_READ_TOKEN", raising=False)
    monkeypatch.delenv("XANO_METADATA_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_DM_CHANNEL", raising=False)
    import requests

    def boom(*a, **k):
        raise AssertionError("real network call attempted")
    monkeypatch.setattr(requests, "get", boom)
    monkeypatch.setattr(requests, "post", boom)
    reg = tmp_path / "experiment_log.json"
    reg.write_text(json.dumps({"experiments": [], "changes": []}), encoding="utf-8")
    return tmp_path, reg


def fake_factory(calls=None):
    def factory(registry, now, data_dir):
        if calls is not None:
            calls.append(now)
        return (lambda e: {"error": "fake", "computed_at": None}), {"requests": 0}
    return factory


def engine_with(tmp, reg, clock, rows_by_call=None, sender=None, sync=True, monkeypatch=None,
                factory=None, http=None):
    """Engine whose build_snapshot returns crafted rows (rows_by_call: callable(now) -> rows)."""
    if rows_by_call is not None and monkeypatch is not None:
        def fake_build(registry, runner, store, today):
            snap = make_snapshot(rows_by_call(today), today=today.isoformat())
            snap["generated_at"] = clock().isoformat().replace("+00:00", "Z")
            return snap
        monkeypatch.setattr(service.snapshot, "build_snapshot", fake_build)
    return service.Engine(data_dir=str(tmp / "data"), clock=clock, http=http or FakeHTTP(),
                          metric_runner_factory=factory or fake_factory(), sender=sender or Sender(),
                          sync=sync, registry_file=str(reg))


def client_for(eng):
    return TestClient(service.create_app(eng, start_scheduler=False))


# ------------------------------------------------------------------------------------ auth / http

def test_health_needs_no_key_and_leaks_nothing(env):
    tmp, reg = env
    eng = engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC)))
    c = client_for(eng)
    r = c.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "k-test" not in r.text and "h-test" not in r.text


def test_auth_required_everywhere_but_health(env):
    tmp, reg = env
    eng = engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC)))
    c = client_for(eng)
    for method, path in (("get", "/snapshot"), ("post", "/refresh"), ("get", "/changes"),
                         ("get", "/experiments/EXP-x/history")):
        assert getattr(c, method)(path).status_code == 401, path
        assert getattr(c, method)(path, headers={"X-Exp-Key": "wrong"}).status_code == 401, path
    assert c.get("/snapshot", headers={"X-Exp-Key": "k-test"}).status_code == 503   # warming


def test_missing_api_key_env_fails_closed(env, monkeypatch):
    tmp, reg = env
    monkeypatch.delenv("EXP_API_KEY")
    c = client_for(engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))))
    assert c.get("/snapshot", headers={"X-Exp-Key": ""}).status_code == 503


def test_snapshot_gzip_etag_and_history(env):
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))
    eng = engine_with(tmp, reg, clock)
    c = client_for(eng)
    r = c.post("/refresh?wait=5", headers={"X-Exp-Key": "k-test"})
    assert r.status_code == 200 and r.json()["last_run"]["ok"] is True
    r = c.get("/snapshot", headers={"X-Exp-Key": "k-test", "Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    snap = r.json()
    assert snap["engine"]["mode"] == "full" and "change_feed" in snap
    etag = r.headers["etag"]
    assert c.get("/snapshot", headers={"X-Exp-Key": "k-test", "If-None-Match": etag}).status_code == 304
    plain = c.get("/snapshot", headers={"X-Exp-Key": "k-test", "Accept-Encoding": "identity"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers
    h = c.get("/experiments/EXP-none/history?days=30", headers={"X-Exp-Key": "k-test"})
    assert h.status_code == 200 and h.json()["rows"] == []


def test_public_host_only_serves_health_and_hook(env):
    tmp, reg = env
    c = client_for(engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))))
    pub = {"Host": "experiments-engine-production.up.railway.app", "X-Exp-Key": "k-test"}
    assert c.get("/snapshot", headers=pub).status_code == 404
    assert c.get("/changes", headers=pub).status_code == 404
    assert c.get("/health", headers=pub).status_code == 200
    priv = {"Host": "experiments-engine.railway.internal:8080", "X-Exp-Key": "k-test"}
    assert c.get("/changes", headers=priv).status_code == 200


def test_refresh_joins_running_run(env):
    tmp, reg = env
    gate, entered, calls = threading.Event(), threading.Event(), []

    def slow_factory(registry, now, data_dir):
        calls.append(now)
        entered.set()
        assert gate.wait(10)
        return (lambda e: {"error": "fake"}), {"requests": 0}
    eng = engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC)), sync=False,
                      factory=slow_factory)
    c = client_for(eng)
    h = {"X-Exp-Key": "k-test"}
    r1 = c.post("/refresh", headers=h)
    assert r1.status_code == 202 and r1.json()["status"] == "started"
    assert entered.wait(5)
    r2 = c.post("/refresh", headers=h)
    assert r2.status_code == 202 and r2.json()["status"] == "running" and r2.json()["joined"] is True
    assert r2.json()["run"]["id"] == r1.json()["run"]["id"]
    gate.set()
    r3 = c.post("/refresh?wait=10", headers=h)   # joins the tail of run 1, or 429 (min gap) once it ended
    assert r3.status_code in (200, 429)
    assert eng.wait(10)
    assert len(calls) in (1, 2) and eng.last_run["ok"] is True
    assert len(calls) == eng.runs   # never two factories for one run


# ------------------------------------------------------------------------------------ railway hook

NEW_FORMAT = {"type": "Deployment.deployed",
              "details": {"id": "dep-1", "source": "GitHub", "status": "SUCCESS", "branch": "main",
                          "commitHash": "7252cee0123456789", "commitAuthor": "x", "commitMessage": "flag off"},
              "resource": {"project": {"id": "p", "name": "tulle admin dash"},
                           "environment": {"id": "e", "name": "production", "isEphemeral": False},
                           "service": {"id": "s", "name": "rec-engine"}, "deployment": {"id": "dep-1"}},
              "severity": "INFO", "timestamp": "2026-11-18T16:17:00.000Z"}


def test_hook_secret_and_storage(env, monkeypatch):
    tmp, reg = env
    eng = engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC)))
    c = client_for(eng)
    assert c.post("/hooks/railway", json=NEW_FORMAT).status_code == 401
    assert c.post("/hooks/railway?k=nope", json=NEW_FORMAT).status_code == 401
    r = c.post("/hooks/railway?k=h-test", json={"type": "Alert.triggered", "details": {}})
    assert r.json() == {"ok": True, "ignored": True}
    r = c.post("/hooks/railway?k=h-test", json=NEW_FORMAT)
    assert r.json() == {"ok": True, "stored": True}
    assert c.post("/hooks/railway?k=h-test", json=NEW_FORMAT).json()["stored"] is False   # retry dedup
    # public host can reach the hook (Railway calls it from outside)
    r = c.post("/hooks/railway?k=h-test", json={"type": "x"},
               headers={"Host": "experiments-engine-production.up.railway.app"})
    assert r.status_code == 200
    assert c.post("/hooks/railway?k=h-test", content=b"not json").status_code == 400
    ch = c.get("/changes", headers={"X-Exp-Key": "k-test"}).json()
    assert [e["source"] for e in ch["events"]] == ["railway"]
    assert ch["unlogged"] and ch["unlogged"][0]["ref"] == "7252cee"
    monkeypatch.delenv("EXP_HOOK_SECRET")
    assert c.post("/hooks/railway?k=", json=NEW_FORMAT).status_code == 503


def test_railway_payload_formats_and_variable_flip(tmp_path):
    legacy = {"type": "DEPLOY", "timestamp": "2026-11-18T18:21:06Z", "status": "SUCCESS",
              "service": {"id": "s", "name": "rec-engine"}, "environment": {"name": "production"},
              "project": {"name": "tulle admin dash"},
              "deployment": {"id": "dep-2", "meta": {"commitHash": "7252cee0123456789", "commitMessage": "flag off"}}}
    a = changefeed.normalise_railway_payload(NEW_FORMAT)
    b = changefeed.normalise_railway_payload(legacy)
    assert a["service"] == b["service"] == "rec-engine" and a["status"] == b["status"] == "SUCCESS"
    assert changefeed.normalise_railway_payload({"type": "Alert.triggered"}) is None
    sdir = str(tmp_path)
    changefeed.store_railway_event(sdir, a)
    changefeed.store_railway_event(sdir, b)
    changefeed.store_railway_event(sdir, dict(a, deployment_id="dep-3", status="SKIPPED", ts="2026-11-18T19:00:00Z"))
    evs = changefeed.railway_events(sdir)
    assert len(evs) == 2 and evs[0]["variable_change"] is False and evs[1]["variable_change"] is True
    assert "variable change" in evs[1]["title"] and all(e["user_facing"] for e in evs)


# ------------------------------------------------------------------------------------ scheduler

def test_scheduler_refresh_cadence_and_restart(env, monkeypatch):
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 1, 0, tzinfo=UTC))
    calls = []
    eng = engine_with(tmp, reg, clock, rows_by_call=lambda d: [exp_row()], monkeypatch=monkeypatch,
                      factory=fake_factory(calls))
    eng.tick()
    assert len(calls) == 1                         # no snapshot -> full refresh at start-up
    clock.advance(hours=1)
    eng.tick()
    assert len(calls) == 1
    clock.advance(hours=5)
    eng.tick()
    assert len(calls) == 2                         # 6 h later
    # restart one hour after that run: fresh snapshot on the volume -> no Xano read
    clock.advance(hours=1)
    eng2 = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    eng2.tick()
    assert len(calls) == 2 and eng2.snapshot is not None
    assert eng2.next_full_due == dt.datetime(2026, 11, 20, 13, 0, tzinfo=UTC)
    # restart after the snapshot is > 6 h old -> refresh
    clock.advance(hours=6)
    eng3 = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    eng3.tick()
    assert len(calls) == 3


def test_registry_change_on_restart_rebuilds_from_cache(env, monkeypatch):
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 1, 0, tzinfo=UTC))
    calls = []
    eng = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    eng.tick()
    assert len(calls) == 1
    reg.write_text(json.dumps({"experiments": [], "changes": [{"date": "2026-11-20", "what": "x"}]}), encoding="utf-8")
    clock.advance(hours=1)
    eng2 = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    eng2.tick()
    assert len(calls) == 1                         # no metric (Xano) run
    assert eng2.snapshot["engine"]["mode"] == "registry"
    assert eng2.snapshot["generated_at"] == eng.snapshot["generated_at"]   # data age kept


def test_digest_once_per_day_across_restarts(env, monkeypatch):
    tmp, reg = env
    monkeypatch.setenv("DRY_RUN", "0")
    clock = Clock(dt.datetime(2026, 11, 20, 13, 0, tzinfo=UTC))
    sender = Sender()
    rows = lambda d: [exp_row(health="needs_call", action="Owner call due", reason="3 days past review"),
                      exp_row("EXP-2026-11-b")]
    eng = engine_with(tmp, reg, clock, rows_by_call=rows, monkeypatch=monkeypatch, sender=sender)
    eng.tick()
    assert sender.sent == []                       # 13:00: before the digest window
    clock.advance(hours=1, minutes=1)
    eng.tick()
    assert len(sender.sent) == 1 and "need" in sender.sent[0] and "admin.tulletogether.app" in sender.sent[0]
    eng.tick()
    clock.advance(minutes=30)
    eng.tick()
    assert len(sender.sent) == 1
    eng2 = engine_with(tmp, reg, clock, sender=sender)   # restart same day
    eng2.tick()
    assert len(sender.sent) == 1
    clock.advance(days=1)
    eng2.tick()
    assert len(sender.sent) == 2                    # next day
    assert history.HistoryStore(str(tmp / "data")).was_sent("digest:2026-11-21")


def test_digest_suppressed_when_nothing_actionable(env, monkeypatch):
    tmp, reg = env
    monkeypatch.setenv("DRY_RUN", "0")
    clock = Clock(dt.datetime(2026, 11, 20, 14, 5, tzinfo=UTC))
    sender = Sender()
    eng = engine_with(tmp, reg, clock, rows_by_call=lambda d: [exp_row(), exp_row("EXP-2026-11-b")],
                      monkeypatch=monkeypatch, sender=sender)
    eng.tick()
    eng.tick()
    assert sender.sent == []
    store = history.HistoryStore(str(tmp / "data"))
    assert store.was_sent("digest:2026-11-20:quiet")
    assert alerts.format_digest(make_snapshot([exp_row()])) is None


def test_guardrail_sent_once_per_breach(env, monkeypatch):
    tmp, reg = env
    monkeypatch.setenv("DRY_RUN", "0")
    clock = Clock(dt.datetime(2026, 11, 20, 2, 0, tzinfo=UTC))
    sender = Sender()
    breach = {"on": True}

    def rows(d):
        if breach["on"]:
            return [exp_row(health="guardrail_breach", action="Pause or revert now",
                            reason="purchases/day down", guardrails=[{"key": "purchases_per_day", "breached": True,
                                                                       "p_breach": 0.95, "max_drop_rel": 0.25}])]
        return [exp_row()]
    eng = engine_with(tmp, reg, clock, rows_by_call=rows, monkeypatch=monkeypatch, sender=sender)
    eng.tick()
    assert len(sender.sent) == 1 and "Guardrail breach" in sender.sent[0] and "95%" in sender.sent[0]
    for _ in range(3):                              # next runs, same breach, incl. next day
        clock.advance(hours=6)
        eng.tick()
    eng2 = engine_with(tmp, reg, clock, sender=sender)
    eng2.tick()
    guard = [s for s in sender.sent if "Guardrail breach" in s]
    assert len(guard) == 1
    # breach clears for a day, then a new breach -> a new alert
    breach["on"] = False
    clock.advance(days=1)
    eng2.start_refresh("full")
    breach["on"] = True
    clock.advance(days=1)
    eng2.start_refresh("full")
    assert len([s for s in sender.sent if "Guardrail breach" in s]) == 2


def test_failed_send_retries_then_gives_up(env, monkeypatch):
    tmp, reg = env
    monkeypatch.setenv("DRY_RUN", "0")
    clock = Clock(dt.datetime(2026, 11, 20, 14, 0, tzinfo=UTC))
    sender = Sender(ok=False)
    eng = engine_with(tmp, reg, clock, rows_by_call=lambda d: [exp_row(health="needs_call")],
                      monkeypatch=monkeypatch, sender=sender)
    eng.tick()
    eng.tick()                                       # within 30 min: backoff, no resend
    assert len(sender.sent) == 1
    for _ in range(4):
        clock.advance(minutes=31)
        eng.tick()
    assert len(sender.sent) == 3                     # 3 attempts max


def test_weekly_monday_once(env, monkeypatch):
    tmp, reg = env
    monkeypatch.setenv("DRY_RUN", "0")
    clock = Clock(dt.datetime(2026, 11, 23, 14, 30, tzinfo=UTC))   # Monday, ISO week 48
    sender = Sender()

    def fake_build(registry, runner, store, today):
        s = make_snapshot([exp_row()], decided=[{"id": "EXP-2026-11-old", "name": "Old test", "outcome": "shipped",
                                                 "decided_on": "2026-11-19", "takeaway": "Daily allowance brought couples back."}],
                          today=today.isoformat())
        s["generated_at"] = clock().isoformat()
        return s
    monkeypatch.setattr(service.snapshot, "build_snapshot", fake_build)
    eng = engine_with(tmp, reg, clock, sender=sender)
    eng.tick()
    eng.tick()
    weekly = [s for s in sender.sent if "decided" in s]
    assert len(weekly) == 1 and "Old test" in weekly[0] and "shipped" in weekly[0]
    assert history.HistoryStore(str(tmp / "data")).was_sent("weekly:2026-W48")


def test_dry_run_keys_and_no_network(env, monkeypatch):
    tmp, reg = env                                   # DRY_RUN unset -> on
    clock = Clock(dt.datetime(2026, 11, 20, 14, 0, tzinfo=UTC))
    eng = engine_with(tmp, reg, clock, rows_by_call=lambda d: [exp_row(health="needs_call")],
                      monkeypatch=monkeypatch, sender=alerts.send)
    eng.tick()                                       # requests.post would raise (env fixture)
    store = history.HistoryStore(str(tmp / "data"))
    assert store.was_sent("digest:2026-11-20:dry") and not store.was_sent("digest:2026-11-20")


# ------------------------------------------------------------------------------------ alerts.send

def test_send_dry_run_default_never_posts(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setenv("SLACK_DM_CHANNEL", "D123")
    res = alerts.send("hello", http=NoNetwork())
    assert res == {"ok": True, "dry_run": True, "error": None}


def test_send_live_paths_never_raise(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "0")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_DM_CHANNEL", raising=False)
    assert alerts.send("hi", http=NoNetwork())["error"] == "not configured"
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setenv("SLACK_DM_CHANNEL", "D123")
    res = alerts.send("hi", http=NoNetwork())       # post raises -> caught
    assert res["ok"] is False and "AssertionError" in res["error"]
    posted = []

    class OkHTTP:
        def post(self, url, headers=None, json=None, timeout=None):
            posted.append((url, headers, json))
            return Resp(200, data={"ok": True})
    assert alerts.send("hi", http=OkHTTP())["ok"] is True
    assert posted[0][2]["channel"] == "D123" and posted[0][1]["Authorization"] == "Bearer xoxb-fake"

    class ErrHTTP:
        def post(self, *a, **k):
            return Resp(200, data={"ok": False, "error": "missing_scope"})
    assert alerts.send("hi", http=ErrHTTP())["error"] == "missing_scope"


def test_digest_lists_counts_and_unlogged():
    snap = make_snapshot([exp_row(health="callable_win", action="Call it: ship", reason="P(beat) 0.96 after 9 days"),
                          exp_row("EXP-2026-11-s", health="starved", stale=True, action="Shelve or redesign",
                                  reason="needs 120 days"), exp_row("EXP-2026-11-ok")])
    unlogged = [changefeed.event("2026-11-19T10:00:00Z", "github", "abc1234", "rec: new ranking", ["rec-engine"])]
    text = alerts.format_digest(snap, unlogged)
    assert "Calls due" in text and "Going stale" in text and "abc1234" in text
    assert "12 of 300" in text and "20 of 310" in text and "93% chance" in text
    assert "1 running healthy" in text


def test_guardrail_rise_wording():
    # QA 2026-11-19: a max_rise_rel guardrail was described as "chance of a real drop".
    row = exp_row(health="guardrail_breach", guardrails=[{"key": "refund_rate", "breached": True,
                                                         "p_breach": 0.97, "max_drop_rel": None,
                                                         "max_rise_rel": 0.2}])
    text = alerts.format_guardrail(row)
    assert "97% chance of a real rise" in text and "drop" not in text
    row["guardrails"][0].update(max_rise_rel=None, max_drop_rel=0.25)
    assert "97% chance of a real drop" in alerts.format_guardrail(row)


def test_full_refresh_min_gap(env, monkeypatch):
    # Production-safety audit: a dash loop must not be able to run back-to-back Xano reads.
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))
    calls = []
    eng = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    c = client_for(eng)
    h = {"X-Exp-Key": "k-test"}
    assert c.post("/refresh?wait=5", headers=h).status_code == 200
    clock.advance(minutes=10)
    r = c.post("/refresh?wait=5", headers=h)
    assert r.status_code == 429 and r.json()["last_full_run"].startswith("2026-11-20T09:00")
    assert len(calls) == 1
    assert c.post("/refresh?mode=registry&wait=5", headers=h).status_code == 200   # rescoring allowed
    clock.advance(minutes=6)
    assert c.post("/refresh?wait=5", headers=h).status_code == 200
    assert len(calls) == 2
    monkeypatch.setenv("EXP_MIN_FULL_GAP_S", "60")
    clock.advance(minutes=2)
    assert c.post("/refresh?wait=5", headers=h).status_code == 200
    # survives a restart: the gap is read from the persisted snapshot too
    eng2 = engine_with(tmp, reg, clock, factory=fake_factory(calls))
    eng2.load_persisted()
    monkeypatch.delenv("EXP_MIN_FULL_GAP_S")
    assert client_for(eng2).post("/refresh", headers=h).status_code == 429


def test_snapshot_inline_history_variant(env, monkeypatch):
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))
    eng = engine_with(tmp, reg, clock, rows_by_call=lambda d: [exp_row("EXP-2026-11-a"), exp_row("EXP-2026-11-b")],
                      monkeypatch=monkeypatch)
    for day in range(1, 5):                           # 4 days of readout rows
        eng.store.append_day({"today": "2026-11-%02d" % (15 + day), "generated_at": None,
                              "experiments": [exp_row("EXP-2026-11-a"), exp_row("EXP-2026-11-gone")]})
    c = client_for(eng)
    h = {"X-Exp-Key": "k-test"}
    assert c.post("/refresh?wait=5", headers=h).status_code == 200
    plain = c.get("/snapshot", headers=h)
    assert "history_by_id" not in plain.json()
    r = c.get("/snapshot?history=3", headers=dict(h, **{"Accept-Encoding": "gzip"}))
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    hb = r.json()["history_by_id"]
    assert set(hb) == {"EXP-2026-11-a", "EXP-2026-11-b"}   # only experiments in the snapshot
    assert [x["date"] for x in hb["EXP-2026-11-a"]][-3:] == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert len(hb["EXP-2026-11-a"]) == 3 and len(hb["EXP-2026-11-b"]) == 1   # b: only the refresh's own row
    assert set(hb["EXP-2026-11-a"][0]) == {"date", "p_beat", "n_control", "n_treatment", "health"}
    assert r.headers["etag"] != plain.headers["etag"]
    assert c.get("/snapshot?history=3", headers=dict(h, **{"If-None-Match": r.headers["etag"]})).status_code == 304
    assert c.get("/snapshot", headers=dict(h, **{"If-None-Match": r.headers["etag"]})).status_code == 200
    big = c.get("/snapshot?history=500", headers=h).json()["history_by_id"]
    assert len(big["EXP-2026-11-a"]) <= 60


def test_weweb_poll_cannot_overwrite_a_newer_snapshot(env, monkeypatch):
    tmp, reg = env
    clock = Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC))
    eng = engine_with(tmp, reg, clock)
    eng.start_refresh("full")
    old = eng.snapshot
    newer = dict(old, generated_at="2026-11-20T09:30:00Z")
    real_collect, real_match = service.changefeed.collect, service.changefeed.match_unlogged
    monkeypatch.setattr(service.changefeed, "collect", lambda *a, **k: dict(real_collect(*a, **k), new=[{"x": 1}]))

    def match_while_refresh_lands(*a, **k):
        t = threading.Thread(target=eng._set_snapshot, args=(newer,))   # the refresh thread
        t.start()
        t.join(0.2)
        return real_match(*a, **k)
    monkeypatch.setattr(service.changefeed, "match_unlogged", match_while_refresh_lands)
    eng.poll_weweb(clock())
    for th in threading.enumerate():
        if th is not threading.current_thread() and th.name.startswith("Thread"):
            th.join(2)
    assert eng.snapshot["generated_at"] == "2026-11-20T09:30:00Z"


def test_guardrail_onset_survives_a_day_without_a_full_run(env, monkeypatch):
    tmp, reg = env
    eng = engine_with(tmp, reg, Clock(dt.datetime(2026, 11, 20, 9, tzinfo=UTC)))
    for d in ("2026-11-16", "2026-11-17"):            # 11-18 and 11-19: no full run (outage)
        eng.store.append_day({"today": d, "generated_at": None,
                              "experiments": [exp_row(health="guardrail_breach")]})
    assert eng._breach_onset("EXP-2026-11-a", dt.date(2026, 11, 20)) == dt.date(2026, 11, 16)
    eng.store.append_day({"today": "2026-11-19", "generated_at": None, "experiments": [exp_row()]})
    assert eng._breach_onset("EXP-2026-11-a", dt.date(2026, 11, 20)) == dt.date(2026, 11, 20)


# ------------------------------------------------------------------------------------ change feed

def test_weweb_marker_parsing_on_fixture():
    html = open(os.path.join(HERE, "fixtures", "weweb_prod_home.html"), encoding="utf-8").read()
    build, cv, entry = changefeed.parse_weweb_build(html)
    assert cv == 1404 and entry == "entry-client-Cnp2nHg1.js" and build == "1404:entry-client-Cnp2nHg1.js"
    with pytest.raises(ValueError):
        changefeed.parse_weweb_build("<html>no markers</html>")


def test_weweb_poll_baseline_interval_and_change(tmp_path):
    html = open(os.path.join(HERE, "fixtures", "weweb_prod_home.html"), encoding="utf-8").read()
    page = {"html": html}
    http = FakeHTTP(lambda url, p, h: Resp(200, text=page["html"]))
    t0 = dt.datetime(2026, 11, 20, 9, tzinfo=UTC)
    ev, note = changefeed.poll_weweb(http, str(tmp_path), t0)
    assert ev == [] and note is None and len(http.gets) == 1          # baseline
    ev, _ = changefeed.poll_weweb(http, str(tmp_path), t0 + dt.timedelta(minutes=10))
    assert ev == [] and len(http.gets) == 1                            # < 30 min: no GET
    page["html"] = html.replace("1404", "1405").replace("Cnp2nHg1", "Zz9")
    ev, _ = changefeed.poll_weweb(http, str(tmp_path), t0 + dt.timedelta(minutes=31))
    assert len(http.gets) == 2 and len(ev) == 1
    assert ev[0]["ref"] == "1405" and ev[0]["surfaces"] == ["weweb"] and ev[0]["user_facing"]
    page["html"] = "<html>maintenance</html>"
    ev, note = changefeed.poll_weweb(http, str(tmp_path), t0 + dt.timedelta(minutes=62))
    assert ev == [] and "format changed" in note


def test_surfaces_for_paths():
    f = changefeed.surfaces_for_paths
    assert f(changefeed.MAIN_REPO, ["rec_service/plan.py"]) == (["rec-engine"], True, True)
    assert f(changefeed.MAIN_REPO, ["experiments/experiment_log.json"]) == ([], False, False)
    assert f(changefeed.MAIN_REPO, ["experiments/experiment_log.json", "slack_qa/app.py"]) == (["tulle-bot"], False, True)
    assert f(changefeed.MAIN_REPO, ["README.md"])[2] is False
    assert f("marthv/tulletogether", ["components/paywall-modal/src/wwElement.vue"]) == (["weweb:component"], True, True)


def _commit(sha, date, msg="m"):
    return {"sha": sha, "commit": {"message": msg, "committer": {"date": date}}}


def test_github_poll_budget_etag_and_registry_commits(tmp_path):
    now = dt.datetime(2026, 11, 20, 12, tzinfo=UTC)
    shas = [f"{i:07x}" + "f" * 33 for i in range(1, 7)]   # newest first
    listing = [_commit(s, "2026-11-20T0%d:00:00Z" % i) for i, s in enumerate(shas)]
    files = {shas[0]: ["rec_service/main.py"], shas[1]: ["experiments/experiment_log.json"]}
    state = {"etag": None}

    def route(url, params, headers):
        if url.endswith("/commits"):
            if headers.get("If-None-Match") == '"e1"':
                return Resp(304)
            return Resp(200, data=listing, headers={"ETag": '"e1"'})
        sha = url.rsplit("/", 1)[1]
        return Resp(200, data={"sha": sha, "html_url": "u", "commit": {"message": f"msg {sha[:7]}\nbody",
                    "committer": {"date": "2026-11-20T05:00:00Z"}},
                    "files": [{"filename": p} for p in files.get(sha, ["daily_brief.py"])]})
    http = FakeHTTP(route)
    ev, notes, calls = changefeed.poll_github(http, str(tmp_path), now)
    assert calls == 4 and len(http.gets) == 4                      # 1 list + 3 details
    assert any("waiting" in n for n in notes)
    ev2, notes2, calls2 = changefeed.poll_github(http, str(tmp_path), now)
    assert http.gets[4][2].get("If-None-Match") == '"e1"'          # conditional list
    assert calls2 == 4
    ev3, _, _ = changefeed.poll_github(http, str(tmp_path), now)
    allev = ev + ev2 + ev3
    got = {e["sha"]: e for e in allev}
    assert shas[1] not in got                                       # registry-only: not a change
    assert got[shas[0]]["surfaces"] == ["rec-engine"] and got[shas[0]]["user_facing"]
    assert len(got) == 5 and not any(e["user_facing"] for s, e in got.items() if s != shas[0])


def test_xano_audit_degrades(tmp_path):
    http = FakeHTTP(lambda url, p, h: Resp(403))
    ev, note, calls = changefeed.poll_xano_audit(http, str(tmp_path), token="t")
    assert ev == [] and "unavailable (HTTP 403)" in note and calls == 1
    ev, note, calls = changefeed.poll_xano_audit(FakeHTTP(), str(tmp_path), token="")
    assert calls == 0 and "no XANO_METADATA_TOKEN" in note
    now = dt.datetime(2026, 11, 20, 12, tzinfo=UTC)
    rows = {"items": [{"id": 9, "created_at": int((now - dt.timedelta(hours=2)).timestamp() * 1000),
                       "msg": "query:update GET pdfdata/vendor_prices", "user": {"email": "a@b.c"}},
                      {"id": 8, "created_at": int((now - dt.timedelta(days=5)).timestamp() * 1000), "msg": "old"}]}
    http = FakeHTTP(lambda url, p, h: Resp(200, data=rows))
    ev, note, _ = changefeed.poll_xano_audit(http, str(tmp_path), now=now, token="t")
    assert note is None and [e["ref"] for e in ev] == ["audit:9"]
    ev, _, _ = changefeed.poll_xano_audit(http, str(tmp_path), now=now, token="t")
    assert ev == []                                                 # seen


REG = {
    "experiments": [
        {"id": "EXP-2026-11-x", "status": "live", "start_date": "2026-11-10", "surfaces": ["rec-engine"],
         "links": ["rec e29f61a"]},
    ],
    "changes": [
        {"date": "2026-11-18", "what": "WeWeb production publish cacheVersion 1401, verified live 06:47 UTC"},
        {"date": "2026-11-18", "what": "rec-engine REC_WW_SERVICE_PRICES=1 set 18:21 UTC (code 7252cee, pushed flag-off 16:17)"},
        {"date": "2026-11-15", "what": "Xano ep230 no free first vendor", "source": "xano"},
    ],
}


def test_match_unlogged():
    E = changefeed.event
    events = [
        E("2026-11-18T06:47:00Z", "weweb", "1401", "WeWeb publish", ["weweb"], user_facing=True),        # ref
        E("2026-11-18T16:17:00Z", "github", "7252cee", "rec: flag", ["rec-engine"], sha="7252cee" + "0" * 33),  # ref
        E("2026-11-18T18:21:06Z", "railway", "7252cee", "rec-engine redeploy", ["rec-engine"],
          user_facing=True, commit="7252cee0", variable_change=True),                                   # date+family
        E("2026-11-12T09:00:00Z", "github", "e29f61a", "rec: daily q", ["rec-engine"], sha="e29f61a" + "0" * 33),  # exp link
        E("2026-11-10T09:00:00Z", "github", "1111111", "rec: start", ["rec-engine"], sha="1" * 40),       # exp start
        E("2026-11-15T09:00:00Z", "xano", "audit:1", "Xano: ep230 edit", ["xano"]),                       # v2 source
        E("2026-11-04T09:00:00Z", "github", "abcdef1", "rec: unlogged change", ["rec-engine"], sha="abcdef1" + "0" * 33),
        E("2026-11-21T09:00:00Z", "weweb", "1404", "WeWeb publish", ["weweb"], user_facing=True),         # no entry in 24 h
        E("2026-11-04T09:00:00Z", "github", "2222222", "registry", ["registry"], user_facing=False),
        E("2026-11-04T10:00:00Z", "railway", "abcdef1", "rec-engine deploy", ["rec-engine"],
          user_facing=True, commit="abcdef10"),                                                         # same commit
    ]
    un = changefeed.match_unlogged(events, REG)
    assert sorted(e["ref"] for e in un) == ["1404", "abcdef1"]
    assert all(e["unlogged"] and e["why"] for e in un)
    # a WeWeb publish on a day whose only logged change is a rec-engine one stays unlogged
    ev = E("2026-11-18T20:00:00Z", "weweb", "1402", "WeWeb publish", ["weweb"], user_facing=True)
    reg2 = {"changes": [REG["changes"][1]], "experiments": []}
    assert changefeed.match_event(ev, reg2)[0] is False


def test_collect_never_raises_and_engine_end_to_end_with_real_registry(env, monkeypatch, tmp_path):
    if not os.path.exists(REAL_REGISTRY):
        pytest.skip("registry not present")
    tmp, _reg = env

    def bad_route(url, params, headers):
        raise ConnectionError("offline")
    clock = Clock(dt.datetime(2026, 11, 20, 3, tzinfo=UTC))
    eng = service.Engine(data_dir=str(tmp / "data"), clock=clock, http=FakeHTTP(bad_route),
                         metric_runner_factory=fake_factory(), sender=Sender(), sync=True,
                         registry_file=REAL_REGISTRY)
    eng.tick()
    snap = eng.snapshot
    assert eng.last_run["ok"] is True, eng.last_run
    assert snap["summary"]["live"] > 0 and snap["engine"]["registry_sha256"]
    assert any("weweb" in n for n in snap["change_feed"]["notes"])
    with open(eng.snapshot_path, "rb") as f:
        assert json.loads(gzip.decompress(f.read()))["generated_at"] == snap["generated_at"]
