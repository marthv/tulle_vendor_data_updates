"""Tests for experiments_engine/xano.py and experiments_engine/metrics.py. No network.

FakeXano = the real XanoReadOnly client wired to an in-memory transport that mimics the Xano
metadata search (one-key dict / list of one-key dicts applied server-side, a TWO-key dict
silently ignored, paging with nextPage). So every metric test also exercises the client.
"""
import copy
import datetime as dt
import hashlib
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from experiments_engine import metrics as M  # noqa: E402
from experiments_engine import xano as X  # noqa: E402

DAY = 86400000
HOUR = 3600000


def ms(s):
    return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


NOW = ms("2026-11-19T12:00:00Z")


# ---------------------------------------------------------------------------
# fake Xano
# ---------------------------------------------------------------------------

def _match(row, key, val):
    field, _, op = key.partition("|")
    op = op or "="
    return X._cmp(row.get(field), op, val)


class FakeTransport:
    def __init__(self, tables, statuses=None):
        self.tables = tables
        self.calls = []
        self.statuses = list(statuses or [])   # forced statuses for the next calls

    def __call__(self, method, url, headers, params, body, timeout):
        self.calls.append((method, url, copy.deepcopy(body)))
        if self.statuses:
            st = self.statuses.pop(0)
            if st != 200:
                return st, {}, None
        g = re.search(r"/table/(\d+)/content/(\d+)$", url)
        if method == "GET" and g:
            hit = [r for r in self.tables.get(int(g.group(1)), []) if str(r.get("id")) == g.group(2)]
            return (200, {}, copy.deepcopy(hit[0])) if hit else (404, {}, None)
        m = re.search(r"/table/(\d+)/content/search$", url)
        assert method == "POST" and m, (method, url)
        rows = self.tables.get(int(m.group(1)), [])
        search = (body or {}).get("search")
        if isinstance(search, dict):
            if len(search) == 1:
                (k, v), = search.items()
                rows = [r for r in rows if _match(r, k, v)]
            # two or more keys: silently ignored, whole table (the real trap)
        elif isinstance(search, list):
            for clause in search:
                (k, v), = clause.items()
                rows = [r for r in rows if _match(r, k, v)]
        page, per = body.get("page", 1), body.get("per_page", 500)
        chunk = rows[(page - 1) * per: page * per]
        nxt = page + 1 if page * per < len(rows) else None
        return 200, {}, {"items": copy.deepcopy(chunk), "nextPage": nxt, "curPage": page,
                         "itemsTotal": len(rows)}


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def fake_client(tables, budget=60, statuses=None):
    tr = FakeTransport(tables, statuses)
    fc = FakeClock()
    c = X.XanoReadOnly(base="https://example.invalid/api:meta", token="t0k3n", budget=budget,
                       transport=tr, sleep=fc.sleep, clock=fc.clock)
    return c, tr, fc


def U(uid, created, views=0, email=None, **kw):
    r = {"id": uid, "created_at": ms(created), "total_pdf_views": views,
         "email": email or ("c%d@example.com" % uid), "password": "$2y$hash", "token_session": "eyJ.jwt",
         "auth_magic": "x"}
    r.update(kw)
    return r


def P(ref, when, amount, typ="1 week", email="buyer@example.com"):
    return {"id": abs(hash((ref, when))) % 10**8, "Client_Reference_ID": ref, "Time_of_Payment": ms(when),
            "Amount": amount, "Type": typ, "User": email, "Name": "N", "Stripe_Payment": "pi_x"}


def R(uid, when, kind="refine", status="ok", free=True, rid=[0]):
    rid[0] += 1
    return {"id": rid[0], "user_id": uid, "created_at": ms(when), "usage_day": when[:10], "kind": kind,
            "status": status, "counted_as_free": free}


def N(uid, when, score, state="", email=None):
    return {"id": abs(hash((uid, when))) % 10**8, "user_id": uid, "created_at": ms(when), "score": score,
            "review_prompt_state": state, "user_email": email or "r%d@example.com" % uid, "comment": "c"}


def make_tables():
    users = [
        U(1001, "2026-11-10T10:00:00Z", 0, pricing_viewed_at=ms("2026-11-13T10:00:00Z"),
          pricing_viewed_ctx={"src": "price_report"}),
        U(1002, "2026-11-10T11:00:00Z", 4),
        U(1003, "2026-11-12T10:00:00Z", 3, pricing_viewed_at=ms("2026-11-15T08:00:00Z"),
          pricing_viewed_ctx={"src": "pi"}),
        U(14998, "2026-11-12T10:00:00Z", 5),
        U(1004, "2026-11-12T11:00:00Z", 5, email="Vivek.Marthi+t@gmail.com"),
        U(1005, "2026-11-19T08:00:00Z", 1),
        U(1006, "2026-11-03T10:00:00Z", 1),
        U(1007, "2026-11-04T10:00:00Z", 0),
        # offer holders, old accounts
        U(2001, "2026-01-01T00:00:00Z", 2, paywall_offer_code="TT60-AAA",
          paywall_offer_expires_at=ms("2026-11-12T10:00:00Z")),
        U(2002, "2026-01-02T00:00:00Z", 2, paywall_offer_code="TT60-BBB",
          paywall_offer_expires_at=ms("2026-11-13T12:00:00Z")),
        U(2003, "2026-01-03T00:00:00Z", 2, paywall_offer_code="", paywall_offer_code_4w="TT20-CCC",
          paywall_offer_expires_at=ms("2026-11-13T12:00:00Z")),
        U(2004, "2026-01-04T00:00:00Z", 2, email="kate@tulletogether.com", paywall_offer_code="TT60-DDD",
          paywall_offer_expires_at=ms("2026-11-13T12:00:00Z")),
    ]
    pays = [
        P("1006", "2026-11-03T12:00:00Z", 33.0),
        P("3002", "2026-11-03T13:00:00Z", 30.0),
        P("1002", "2026-11-10T20:00:00Z", 81.0, "forever weeks"),
        P("1003", "2026-11-15T09:00:00Z", 30.0),
        P("14998", "2026-11-12T12:00:00Z", 81.0),
        P("1004", "2026-11-12T15:00:00Z", 81.0, email="vivek.marthi+t@gmail.com"),
        P("", "2026-11-14T10:00:00Z", 81.0, "gift_forever"),
        P("2001", "2026-11-11T22:10:00Z", 58.73, "forever weeks"),
        P("9999", "2026-11-13T10:00:00Z", 30.0, email="someone@tulletogether.com"),
    ]
    usage = [
        R(3001, "2026-11-02T10:00:00Z"), R(3001, "2026-11-04T10:00:00Z", "opening", free=False),
        R(3002, "2026-11-03T10:00:00Z"), R(3002, "2026-11-04T10:00:00Z", "prewarm", free=False),
        R(3002, "2026-11-03T12:00:00Z", status="blocked_free_limit", free=False),
        R(3003, "2026-11-10T10:00:00Z", "opening", free=False), R(3003, "2026-11-11T10:00:00Z", free=False),
        R(3004, "2026-11-10T10:00:00Z"), R(3004, "2026-11-10T15:00:00Z"),
        R(3004, "2026-11-12T10:00:00Z", "lookup", free=False),
        R(3004, "2026-11-11T10:00:00Z", "pdf", status="free"),
        R(3005, "2026-11-17T10:00:00Z"),
        R(3006, "2026-11-12T10:00:00Z"), R(3006, "2026-11-13T10:00:00Z", "mcp_search"),
        R(3006, "2026-11-12T11:00:00Z", status="blocked_free_limit", free=False),
        R(3007, "2026-10-25T10:00:00Z"), R(3007, "2026-11-10T10:00:00Z"),
        R(118, "2026-11-10T10:00:00Z"), R(118, "2026-11-11T10:00:00Z", "opening"),
    ]
    nps = [
        N(4001, "2026-11-15T10:00:00Z", 5, "clicked"),
        N(4002, "2026-11-16T10:00:00Z", 2, "offered"),
        N(4003, "2026-11-03T10:00:00Z", 4, "declined"),
        N(4004, "2026-11-16T11:00:00Z", -1),
        N(118, "2026-11-16T12:00:00Z", 1, "offered"),
        N(0, "2026-11-17T10:00:00Z", 3, "not_offered"),
        N(4005, "2026-11-17T11:00:00Z", 1, "offered", email="x@tulletogether.com"),
        N(4006, "2026-11-16T13:00:00Z", 9),
    ]
    return {1: users, 16: pays, 79: usage, 77: nps}


PRE_POST = {"type": "pre_post", "pre": ["2026-11-01", "2026-11-05"], "post_start": "2026-11-09T00:00:00Z"}


def entry(key, params=None, assignment=None, start="2026-11-09", guardrails=None, eid="EXP-t", status="live"):
    return {"id": eid, "status": status, "reporting": "auto", "start_date": start,
            "metric": {"key": key, "params": params or {}}, "assignment": assignment or dict(PRE_POST),
            "guardrails": guardrails or []}


def run(key, params=None, assignment=None, start="2026-11-09", tables=None, now=NOW, data_dir=None):
    c, tr, fc = fake_client(tables or make_tables())
    ctx = M.DataContext(c, now_ms=now, data_dir=data_dir)
    e = entry(key, params, assignment, start)
    ctx.prepare([e])
    return M.run_experiment(ctx, e), c, tr


def arms(ro):
    return {k: (v["n"], v["conv"]) for k, v in ro["arms"].items()}


# ---------------------------------------------------------------------------
# client: refusal, budget, retries, pacing, sanitising, trap
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("DELETE", "/workspace/1/table/1/content/5"),
    ("PATCH", "/workspace/1/table/1/content/5"),
    ("PUT", "/workspace/1/table/1/content/5"),
    ("POST", "/workspace/1/table/1/content"),               # add row
    ("POST", "/workspace/1/table/1/content/bulk"),
    ("POST", "/workspace/1/table/1/content/search/../bulk"),
    ("GET", "/workspace/1/api/5"),                           # not a table read
    ("POST", "/workspace/1/table/1/truncate"),
])
def test_write_methods_refused_before_io(method, path):
    c, tr, _ = fake_client({})
    with pytest.raises(X.WriteRefused):
        c._request(method, path)
    assert tr.calls == [] and c.requests == 0


def test_refusal_is_not_an_assert():
    assert issubclass(X.WriteRefused, X.XanoError) and not issubclass(X.WriteRefused, AssertionError)


@pytest.mark.parametrize("method,path", [
    ("GET", "/workspace/1/table/1\n"),
    ("GET", "/workspace/1/table/1/content/5\n"),
    ("POST", "/workspace/1/table/1/content/search\n"),
])
def test_trailing_newline_path_refused(method, path):
    # QA 2026-11-19: re.match with "$" also matched before a trailing newline.
    with pytest.raises(X.WriteRefused):
        X.XanoReadOnly.check_allowed(method, path)


def test_allowed_reads():
    assert X.XanoReadOnly.check_allowed("get", "/workspace/1/table/16/content") == "GET"
    assert X.XanoReadOnly.check_allowed("POST", "/workspace/1/table/16/content/search") == "POST"


def test_budget_enforced():
    c, tr, _ = fake_client(make_tables(), budget=2)
    c.search(16, "Time_of_Payment", ">=", 0)
    c.search(77)
    with pytest.raises(X.BudgetExceeded):
        c.search(79, "created_at", ">=", 0)
    assert len(tr.calls) == 2 and c.stats["requests"] == 2


def test_budget_env_default(monkeypatch):
    monkeypatch.setenv("EXP_XANO_BUDGET", "7")
    assert X.XanoReadOnly(token="x").budget == 7
    monkeypatch.delenv("EXP_XANO_BUDGET")
    assert X.XanoReadOnly(token="x").budget == 60


def test_retries_then_success_and_counts_attempts():
    c, tr, fc = fake_client(make_tables(), statuses=[503, 429, 200])
    rows = c.search(77)
    assert len(rows) == 8 and c.retries == 2 and c.requests == 3
    assert fc.slept.count(1.0) >= 1 and 2.0 in fc.slept


def test_no_retry_on_400_and_gives_up_after_3():
    c, tr, _ = fake_client(make_tables(), statuses=[400])
    with pytest.raises(X.XanoError):
        c.search(77)
    assert c.requests == 1
    c, tr, _ = fake_client(make_tables(), statuses=[502, 502, 502, 502, 200])
    with pytest.raises(X.XanoError):
        c.search(77)
    assert c.requests == 4      # 1 + 3 retries


def test_pacing_at_least_300ms():
    c, tr, fc = fake_client(make_tables())
    c.search(77)
    c.search(77)
    c.search(77)
    assert len(fc.slept) == 2 and all(s >= 0.3 - 1e-9 for s in fc.slept)


def test_sensitive_fields_stripped_and_internal_flag():
    c, _, _ = fake_client(make_tables())
    rows = {r["id"]: r for r in c.search(1, "created_at", ">=", 0)}
    for r in rows.values():
        for k in r:
            assert not re.search("password|token|jwt|session|hash|auth|email", k, re.I), k
        assert not any(isinstance(v, str) and "@" in v for v in r.values())
    assert rows[1004]["is_internal"] is True        # vivek.marthi e-mail
    assert rows[14998]["is_internal"] is True       # id list
    assert rows[2004]["is_internal"] is True        # @tulletogether.com
    assert rows[1001]["is_internal"] is False
    pays = c.search(16, "Time_of_Payment", ">=", 0)
    assert all("User" not in p for p in pays)       # e-mail stored under "User"
    assert sum(p["is_internal"] for p in pays) == 3
    assert "t0k3n" not in repr(c)


def test_two_key_trap_never_sent_and_refilter():
    c, tr, _ = fake_client(make_tables())
    rows = c.search(16, filters=[("Time_of_Payment", ">=", ms("2026-11-10T00:00:00Z")),
                                 ("Time_of_Payment", "<", ms("2026-11-12T00:00:00Z"))])
    sent = tr.calls[-1][2]["search"]
    assert isinstance(sent, list) and all(len(d) == 1 for d in sent)
    assert len(rows) == 2


def test_client_refilters_when_server_ignores_filter():
    tables = make_tables()

    class Ignoring(FakeTransport):
        def __call__(self, method, url, headers, params, body, timeout):
            body = dict(body)
            body.pop("search", None)
            return FakeTransport.__call__(self, method, url, headers, params, body, timeout)

    c = X.XanoReadOnly(token="x", transport=Ignoring(tables), sleep=lambda s: None)
    rows = c.search(16, "Time_of_Payment", ">=", ms("2026-11-14T00:00:00Z"))
    assert len(rows) == 2       # gift 11-14 + 1003 11-15 only


def test_incomplete_read_raises():
    tables = {1: [U(i, "2026-11-10T00:00:00Z") for i in range(5000, 5012)]}
    c, _, _ = fake_client(tables)
    with pytest.raises(X.IncompleteRead):
        c.search(1, "created_at", ">=", 0, per_page=5, max_pages=2)
    assert len(c.search(1, "created_at", ">=", 0, per_page=5, max_pages=3)) == 12


# ---------------------------------------------------------------------------
# assignment parity with rec_service
# ---------------------------------------------------------------------------

def _plan_formula(salt, uid, pct):
    return int(hashlib.sha256(("%s:%d" % (salt, uid)).encode()).hexdigest()[:8], 16) % 100 < pct


def _full_formula(prefix, uid, pct):
    return int(hashlib.sha256(("%s:%s" % (prefix, uid)).encode()).hexdigest(), 16) % 100 < pct


UIDS = [1, 2, 3, 185, 12345, 30001, 35823, 36055, 36999]


def test_hash_matches_copied_formulas():
    for uid in UIDS:
        a = {"rule": "hash_pct", "salt": "plan-2026-11", "pct": 50}
        assert (M.split_arm(uid, a) == "treatment") == _plan_formula("plan-2026-11", uid, 50)
        a = {"rule": "hash_pct", "salt": "wholewedding", "pct": 30}
        assert (M.split_arm(uid, a) == "treatment") == _full_formula("wholewedding", uid, 30)
        a = {"rule": "hash_pct", "salt": "railsearch", "pct": 50}
        assert (M.split_arm(uid, a) == "treatment") == _full_formula("railsearch", uid, 50)


def test_known_buckets():
    # computed from rec_service/whole_wedding.bucket on 2026-11-19
    assert [M.hash_bucket("wholewedding", u, "full") for u in (1, 35823, 36055, 12345)] == [85, 60, 31, 71]


def test_parity_with_rec_service_modules(monkeypatch):
    rs = os.path.join(ROOT, "rec_service")
    monkeypatch.syspath_prepend(rs)
    for name, mod in list(sys.modules.items()):      # slack_qa has modules of the same names (config, ...)
        if "slack_qa" in str(getattr(mod, "__file__", "") or "") and os.path.exists(os.path.join(rs, name + ".py")):
            monkeypatch.delitem(sys.modules, name)
    try:
        import plan
        import railway_search
        import whole_wedding
    except Exception as exc:  # pragma: no cover - rec_service deps missing
        pytest.skip("rec_service import failed: %s" % exc)
    monkeypatch.setattr(plan, "PLAN_AB_PCT", 50)
    for uid in UIDS:
        if uid in plan.ALWAYS_PLAN:
            continue
        mine = M.split_arm(uid, {"rule": "hash_pct", "salt": plan.PLAN_AB_SALT, "pct": 50})
        assert (mine == "treatment") == (plan.arm(uid) == "plan")
        assert M.hash_bucket("wholewedding", uid, "full") == whole_wedding.bucket(uid)
        mine = M.split_arm(uid, {"rule": "hash_pct", "salt": "railsearch", "pct": 40})
        assert (mine == "treatment") == (railway_search.arm(uid, 40) == "railway")


def test_forced_and_parity():
    a = {"rule": "hash_pct", "salt": "wholewedding", "pct": 0, "force_ids": [36055]}
    assert M.split_arm(36055, a) == "treatment" and M.split_arm(35823, a) == "control"
    assert M.split_arm(4, {"rule": "user_id_parity", "treatment": "even"}) == "treatment"
    assert M.split_arm(5, {"rule": "user_id_parity", "treatment": "even"}) == "control"
    assert M.split_arm(5, {"rule": "user_id_parity", "treatment": "odd"}) == "treatment"
    assert M.split_arm(0, {"rule": "user_id_parity"}) is None


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

def test_new_paid_within_days_pre_post_internal_and_pending():
    ro, c, _ = run("new_paid_within_days", {"days": 1})
    assert arms(ro) == {"control": (2, 1), "treatment": (3, 1)}
    assert ro["pending"] == {"treatment": 1}
    assert "2026-11-10" in ro["exposure_by_day"] and "2026-11-19" in ro["exposure_by_day"]
    assert ro["computed_at"].startswith("2026-11-19") and "cohort" in ro["definition"]
    assert any("Pre/post" in x for x in ro["caveats"]) and ro["error"] is None
    ro7, _, _ = run("new_paid_within_n_days", {"days": 7})       # alias
    assert arms(ro7)["treatment"] == (3, 2)


def test_split_parity_readout():
    ro, _, _ = run("new_paid_within_days", {"days": 1},
                   {"type": "split", "rule": "user_id_parity", "treatment": "even", "post_start": "2026-11-09"})
    assert arms(ro) == {"control": (2, 0), "treatment": (1, 1)}
    assert ro["pending"] == {"control": 1}          # 1005 is odd
    assert ro["exposure_by_day"]["2026-11-10"] == {"control": 1, "treatment": 1}


def test_gate_hitters_paid_pdf_and_planner():
    ro, _, _ = run("gate_hitters_paid", {"days": 1})
    assert arms(ro) == {"control": (0, 0), "treatment": (2, 1)}
    ro, _, _ = run("paid_after_planner_gate", {"days": 1})
    assert arms(ro) == {"control": (1, 1), "treatment": (1, 0)}


def test_pdf_open_rate_new():
    ro, _, _ = run("pdf_open_rate_new")
    assert arms(ro) == {"control": (2, 1), "treatment": (3, 2)}
    assert ro["pending"] == {"treatment": 1}


def test_pdf_open_within_1d_approx_then_exact(tmp_path):
    ro, _, _ = run("pdf_open_within_1d", data_dir=str(tmp_path))
    assert ro.get("approximate") is True and ro["caveats"][0].startswith("APPROXIMATE")
    assert arms(ro) == {"control": (2, 1), "treatment": (3, 2)}

    tables = make_tables()
    tables[1] += [U(1008, "2026-11-18T08:00:00Z", 0), U(1009, "2026-11-18T09:00:00Z", 0)]
    roll = {"type": "rollout"}
    for now, v9 in ((ms("2026-11-18T10:00:00Z"), 0), (ms("2026-11-18T20:00:00Z"), 2),
                    (ms("2026-11-19T15:00:00Z"), 2)):
        tables[1][-1]["total_pdf_views"] = v9
        c, _, _ = fake_client(tables)
        ctx = M.DataContext(c, now_ms=now, record_pdf_history=True, data_dir=str(tmp_path))
        e = entry("pdf_open_within_1d", assignment=roll, start="2026-11-18")
        ctx.prepare([e])
        ro = M.run_experiment(ctx, e)
    assert os.path.exists(os.path.join(str(tmp_path), "pdf_first_open.json"))
    assert not ro.get("approximate")
    assert arms(ro) == {"treatment": (2, 1)}


def test_planner_second_question():
    ro, _, _ = run("planner_second_question", {"days": 1})
    assert arms(ro) == {"control": (2, 0), "treatment": (4, 1)}


def test_planner_return_within_days_whitelist():
    ro, _, _ = run("planner_return_within_days", {"days": 3})
    # 3002's only later row is a prewarm: not a return. 3006's later row is mcp_: not a return.
    assert arms(ro) == {"control": (2, 1), "treatment": (2, 1)}
    assert ro["pending"] == {"treatment": 1}
    ro, _, _ = run("planner_return_within_days", {"days": 3, "free_only": False})
    assert arms(ro)["treatment"] == (3, 2)       # + 3003 (paid) who came back next day


def test_offer_code_buyers_benchmark():
    a = {"type": "benchmark", "benchmark": {"n": 33, "conv": 1}, "post_start": "2026-11-11T06:14:44Z"}
    ro, c, _ = run("offer_code_buyers", {"prefix": "TT60-"}, a)
    assert arms(ro) == {"treatment": (2, 1)}
    assert ro["benchmark"] == {"n": 33, "conv": 1} and ro["revenue"] == 58.73
    ro2, _, _ = run("save_offer_buyers", {}, a)
    assert arms(ro2) == {"treatment": (2, 1)}


def test_offer_code_buyers_pending_cap_and_window_revenue():
    t = make_tables()
    t[1] += [
        # offer still running at NOW (made 2 h ago), not bought -> pending
        U(2101, "2026-01-05T00:00:00Z", 1, paywall_offer_code="TT60-EEE",
          paywall_offer_expires_at=NOW + 10 * HOUR),
        # bought 3 days after the offer: outside offer_hours + 24 h -> not a buyer
        U(2102, "2026-01-06T00:00:00Z", 1, paywall_offer_code="TT60-FFF",
          paywall_offer_expires_at=ms("2026-11-14T12:00:00Z")),
        # offer made before the experiment window, bought: not in the arms, not in revenue
        U(2103, "2026-01-07T00:00:00Z", 1, paywall_offer_code="TT60-GGG",
          paywall_offer_expires_at=ms("2026-11-11T12:00:00Z")),
    ]
    t[16] += [P("2102", "2026-11-17T12:00:00Z", 75.0, "forever weeks"),
              P("2103", "2026-11-11T05:00:00Z", 99.0, "forever weeks")]
    a = {"type": "benchmark", "benchmark": {"n": 33, "conv": 1}, "post_start": "2026-11-11T06:14:44Z"}
    ro, _, _ = run("offer_code_buyers", {"prefix": "TT60-", "since": "2026-11-01"}, a, tables=t)
    assert arms(ro) == {"treatment": (3, 1)}          # 2001 bought; 2002, 2102 did not
    assert ro["pending"] == {"treatment": 1}          # 2101
    assert ro["revenue"] == 58.73                     # 2103's $99 is outside the window


def test_sanitize_recurses_and_internal_only_from_own_email():
    row = {"id": 7, "email": "couple@example.com", "referrer_email": "vivek.marthi@gmail.com",
           "phone": "555", "first_name": "A", "last_name": "B", "name": "A B", "full_name": "A B",
           "reset_code": "123456", "stripe_customer": "cus_1", "StripeId": "x", "address": "1 Main",
           "ip": "1.2.3.4", "last_ip": "1.2.3.4", "zip": "10001", "description": "keep",
           "meta": {"email": "vivek.marthi@x.com", "token": "t", "ok": 1, "phone": "1"},
           "items": [{"email": "a@b.co", "keep": 2}, "c@d.io", 3]}
    out = X.sanitize_row(row, 1)
    for k in ("email", "referrer_email", "phone", "first_name", "last_name", "name", "full_name",
              "reset_code", "stripe_customer", "StripeId", "address", "ip", "last_ip"):
        assert k not in out, k
    assert out["zip"] == "10001" and out["description"] == "keep"
    assert out["meta"] == {"ok": 1}
    assert out["items"] == [{"keep": 2}, 3]
    assert out["is_internal"] is False                # a referrer's e-mail never marks the couple internal
    assert X.sanitize_row({"id": 8, "primary_email": "x@tulletogether.com"}, 1)["is_internal"] is True


def test_nps_excludes_accounts_internal_by_email():
    t = make_tables()
    t[77].append(N(1004, "2026-11-16T14:00:00Z", 1, "offered", email="someone@example.com"))
    c, _, _ = fake_client(t)
    ctx = M.DataContext(c, now_ms=NOW)
    e = entry("nps_low_share", guardrails=[{"key": "new_paid_within_days"}])   # loads table-1 users
    ctx.prepare([e])
    ro = M.run_experiment(ctx, e)
    assert arms(ro) == {"control": (1, 0), "treatment": (3, 2)}   # 1004 (vivek.marthi in table 1) dropped


def test_nps_metrics():
    ro, _, _ = run("nps_low_share")
    assert arms(ro) == {"control": (1, 0), "treatment": (3, 2)}
    ro, _, _ = run("nps_avg_stars")
    assert ro["mean"]["control"]["mean"] == 4.0 and ro["mean"]["treatment"]["mean"] == 3.333
    assert arms(ro)["treatment"] == (3, 2)
    assert any("above 5" in x for x in ro["caveats"])
    ro, _, _ = run("trustpilot_completion")
    assert arms(ro) == {"control": (1, 0), "treatment": (2, 1)}
    ro, _, _ = run("nps_ratings_per_day")
    assert ro["count"]["pre"]["count"] == 1 and ro["count"]["post"]["count"] == 3


def test_purchases_and_revenue_per_day():
    ro, _, _ = run("purchases_per_day")
    assert ro["count"] == {"pre": {"count": 2, "days": 5.0}, "post": {"count": 4, "days": 10.0}}
    assert ro["exposure_by_day"]["2026-11-14"] == 1
    ro, _, _ = run("purchases_per_day", {"types": ["forever"]})
    assert ro["count"]["post"]["count"] == 2 and ro["count"]["pre"]["count"] == 0
    ro, _, _ = run("revenue_per_day")
    assert ro["dollars"]["pre"] == 63.0 and ro["dollars"]["post"] == 250.73
    assert round(ro["count"]["pre"]["count"] + ro["count"]["post"]["count"]) == 6


def test_count_metric_rollout_uses_baseline():
    ro, _, _ = run("purchases_per_day", {"baseline_days": 14}, {"type": "rollout"}, start="2026-11-12")
    pre, post = ro["count"]["pre"], ro["count"]["post"]
    assert pre["days"] == 14.0 and post["days"] == 7.0
    assert pre["count"] == 4 and post["count"] == 2      # pre: 11-03 x2, 11-10, 11-11; post: gift, 11-15


def test_paid_after_pricing_view_and_planner_pdf():
    ro, _, _ = run("paid_after_pricing_view", {"days": 1}, {"type": "rollout"}, start="2026-11-12")
    assert arms(ro) == {"treatment": (2, 1)}
    ro, _, _ = run("paid_after_pricing_view", {"days": 1, "src": "pi"}, {"type": "rollout"}, start="2026-11-12")
    assert arms(ro) == {"treatment": (1, 1)}
    ro, _, _ = run("planner_pdf_open_rate", {"days": 7}, {"type": "rollout"}, start="2026-11-09")
    assert arms(ro) == {"treatment": (4, 1)} and ro["pending"] == {"treatment": 1}


def test_manual_and_unknown():
    ro, c, tr = run("planner_thumbs_down_rate")
    assert ro["error"] == "not in Xano (Mixpanel only)" and ro["manual"] is True
    ro, c, tr = run("no_such_metric")
    assert ro["error"] == "unknown metric" and tr.calls == []


def test_guardrails_attached():
    c, _, _ = fake_client(make_tables())
    ctx = M.DataContext(c, now_ms=NOW)
    e = entry("planner_return_within_days", {"days": 3},
              guardrails=[{"key": "purchases_per_day", "max_drop_rel": 0.25},
                          {"key": "planner_thumbs_down_rate"}])
    ctx.prepare([e])
    ro = M.run_experiment(ctx, e)
    g = {x["key"]: x for x in ro["guardrails"]}
    assert g["purchases_per_day"]["count"]["post"]["count"] == 4
    assert g["planner_thumbs_down_rate"]["manual"] is True


# ---------------------------------------------------------------------------
# shared context: one load per view per run
# ---------------------------------------------------------------------------

def test_each_view_loaded_once_for_many_experiments():
    entries = [
        entry("new_paid_within_days", {"days": 1}, eid="A"),
        entry("pdf_open_rate_new", eid="B", assignment={"type": "rollout"}, start="2026-11-12"),
        entry("gate_hitters_paid", {"days": 7}, eid="C"),
        entry("purchases_per_day", eid="D", assignment={"type": "rollout"}, start="2026-11-15"),
        entry("planner_return_within_days", {"days": 3}, eid="E"),
        entry("planner_second_question", eid="F"),
        entry("trustpilot_completion", eid="G"),
        entry("nps_low_share", eid="H"),
        entry("manual", eid="I"),
        dict(entry("new_paid_within_days", eid="J"), reporting="manual"),
        dict(entry("new_paid_within_days", eid="K"), status="planned"),
    ]
    c, tr, _ = fake_client(make_tables())
    out, ctx = M.run_all(entries, c, now_ms=NOW)
    assert set(out) == set("ABCDEFGH")
    assert all(not r.get("error") for r in out.values()), out
    # users, payments, usage, nps: one request each (all fit one 500-row page)
    assert c.requests == 4, ctx.load_log
    assert sorted(v for v, _s, _n in ctx.load_log) == ["nps", "payments", "usage", "users"]


def test_gap_fetch_when_earlier_needed():
    c, tr, _ = fake_client(make_tables())
    ctx = M.DataContext(c, now_ms=NOW)
    a = ctx.view("users", ms("2026-11-10T00:00:00Z"))
    b = ctx.view("users", ms("2026-11-01T00:00:00Z"))
    assert len(a) == 6 and len(b) == 8 and c.requests == 2
    assert isinstance(tr.calls[-1][2]["search"], list)
    ctx.view("users", ms("2026-11-05T00:00:00Z"))
    assert c.requests == 2


def test_budget_exceeded_aborts_run():
    entries = [entry("new_paid_within_days", eid="A"), entry("nps_low_share", eid="B")]
    c, _, _ = fake_client(make_tables(), budget=1)
    out, _ = M.run_all(entries, c, now_ms=NOW)
    assert out["A"]["error"].startswith("budget exceeded") and out["B"]["error"].startswith("budget exceeded")
    assert c.requests == 1


def test_registry_shapes_handled():
    # gate "pdf_limit" (registry spelling) == pdf gate
    ro, _, _ = run("gate_hitters_paid", {"gate": "pdf_limit", "days": 1})
    assert arms(ro) == {"control": (0, 0), "treatment": (2, 1)}
    # pre: null -> the 14 days before post_start, with a caveat
    ro, _, _ = run("new_paid_within_days", {"days": 1},
                   {"type": "pre_post", "pre": None, "post_start": "2026-11-09T00:00:00Z"})
    assert arms(ro)["control"] == (2, 1) and any("defaulted" in c for c in ro["caveats"])
    # unsupported things are reported, not computed as something else
    ro, c, _ = run("revenue_per_day", {"cohort": "Atlantis"})
    assert "cohort" in ro["error"]
    ro, c, _ = run("new_paid_within_days", {"cohort": "CA"})
    assert "cohort" in ro["error"] and c.requests == 0
    ro, _, _ = run("purchases_per_day", {"plan": "forever", "full_price": True})
    assert "full_price" in ro["error"]
    ro, _, _ = run("purchases_per_day", {"plan": "forever"})
    assert ro["count"]["post"]["count"] == 2
    ro, _, _ = run("pdf_open_rate_new", {"window_hours": 1})
    assert "window_hours" in ro["error"]
    ro, _, _ = run("new_paid_within_days", {}, {"type": "split", "rule": "geo_cohort"})
    assert "split rule" in ro["error"]
    ro, _, _ = run("manual")
    assert ro["manual"] is True
    # registry writes the railway salt in prefix form
    a = {"rule": "hash_pct", "salt": "railsearch:", "pct": 50}
    for uid in UIDS:
        assert (M.split_arm(uid, a) == "treatment") == _full_formula("railsearch", uid, 50)


def test_real_registry_runs_offline(tmp_path):
    """Every live auto entry in the real registry runs against the fake data without crashing."""
    import json
    with open(os.path.join(ROOT, "experiments", "experiment_log.json"), encoding="utf-8") as f:
        reg = json.load(f)
    c, _, _ = fake_client(make_tables())
    out, ctx = M.run_all(reg["experiments"], c, now_ms=NOW)
    assert out, "no live auto experiments in the registry"
    for eid, ro in out.items():
        assert "computed_at" in ro or ro.get("error"), (eid, ro)
        assert not str(ro.get("error") or "").startswith(("KeyError", "TypeError", "AttributeError",
                                                          "ValueError", "ZeroDivision")), (eid, ro)
    # table loads stay ~6; geo cohorts (Texas / CA) add bounded per-payer location lookups
    assert c.requests - ctx.location_lookups <= 6
    assert ctx.location_lookups <= M.LOCATION_LOOKUPS_PER_RUN



# --------------------------------------------------------------------------- geo cohorts (2026-11-19)

def geo_tables():
    t = make_tables()
    loc = {1002: ["Texas"], 1003: ["California, Nevada"], 1006: ["Texas", "Oklahoma"], 2001: ["texas"]}
    for u in t[1]:
        if u["id"] in loc:
            u["Wedding_Location_Updated"] = loc[u["id"]]
    t[1].append(U(3002, "2025-06-01T00:00:00Z", 1, Wedding_Location_Updated=["California"]))
    return t


GEO_ASSIGN = {"type": "pre_post", "pre": ["2026-11-01", "2026-11-09"], "post_start": "2026-11-09T00:00:00Z"}


def test_geo_texas_only_counts_only_texas_only_payers(tmp_path):
    ro, c, _ = run("purchases_per_day", {"cohort": "texas_only"}, GEO_ASSIGN, tables=geo_tables(), data_dir=str(tmp_path))
    assert not ro.get("error"), ro
    # pre: 1006 is Texas+Oklahoma (not Texas-only), 3002 California -> 0; post: 1002 + 2001 Texas-only
    assert ro["count"]["pre"]["count"] == 0 and ro["count"]["post"]["count"] == 2
    assert ro["geo"]["coverage"] == 1.0 and ro["geo"]["gifts_skipped"] == 1
    assert any("Texas-only" in x for x in ro["caveats"])
    assert "Texas-only couples" in ro["definition"]


def test_geo_ca_revenue_and_cache_reuse(tmp_path):
    ro, c1, _ = run("revenue_per_day", {"cohort": "CA"}, GEO_ASSIGN, tables=geo_tables(), data_dir=str(tmp_path))
    assert not ro.get("error"), ro
    assert ro["dollars"]["pre"] == 30.0 and ro["dollars"]["post"] == 30.0     # 3002 pre, 1003 post
    assert os.path.exists(os.path.join(str(tmp_path), "locations.json"))
    # second run, same volume: old-account payers come from the cache, no per-payer GETs
    c, tr, _ = fake_client(geo_tables())
    ctx = M.DataContext(c, now_ms=NOW, data_dir=str(tmp_path))
    e = entry("revenue_per_day", {"cohort": "CA"}, GEO_ASSIGN)
    ctx.prepare([e])
    M.run_experiment(ctx, e)
    assert ctx.location_lookups == 0
    assert not any(m == "GET" for m, _u, _b in tr.calls)


def test_geo_withheld_until_locations_looked_up(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "LOCATION_LOOKUPS_PER_RUN", 0)
    ro, _, _ = run("purchases_per_day", {"cohort": "texas_only"}, GEO_ASSIGN, tables=geo_tables(),
                   data_dir=str(tmp_path))
    assert ro["error"].startswith("looking up wedding locations") and ro["pending_geo"]["unresolved"] >= 1
    assert "count" not in ro


def test_geo_failed_lookup_is_unresolved_not_fatal(tmp_path):
    t = geo_tables()
    t[1] = [u for u in t[1] if u["id"] != 3002]          # GET 3002 -> 404
    ro, _, _ = run("purchases_per_day", {"cohort": "texas_only"}, GEO_ASSIGN, tables=t, data_dir=str(tmp_path))
    # 1 of 5 payers unresolved = 80% coverage < 95% -> withheld, not crashed
    assert ro["error"].startswith("looking up wedding locations")


def test_location_tokens_rule():
    assert M.location_tokens(["Texas"]) == {"texas"}
    assert M.location_tokens(["California, Nevada", None]) == {"california", "nevada"}
    assert M.location_tokens("New York") == {"new york"}
    assert M.location_tokens(None) == set()
    assert M.GEO_COHORTS["texas_only"][1]({"texas"}) and not M.GEO_COHORTS["texas_only"][1]({"texas", "oklahoma"})


# --------------------------------------------------------------------------- Planner segments (2026-11-19)

def seg_tables():
    t = make_tables()
    for r in t[79]:
        if r["user_id"] == 3003:
            r["forever"] = True
            r["has_access"] = True
        elif r["user_id"] == 3004:
            r["has_access"] = False
    return t


def test_planner_return_forever_segment():
    ro, _, _ = run("planner_return_within_days", {"days": 3, "segment": "forever"}, tables=seg_tables())
    assert not ro.get("error"), ro
    total = sum(a["n"] for a in ro["arms"].values()) + sum(ro["pending"].values())
    assert total == 1                                     # only 3003 is Forever
    assert "Forever Planner couples" in ro["definition"]
    ro2, _, _ = run("planner_return_within_days", {"days": 3}, tables=seg_tables())   # default stays free-only
    assert "free Planner couples" in ro2["definition"]


def test_planner_pdf_open_free_segment_and_bad_segment():
    ro, _, _ = run("planner_pdf_open_rate", {"days": 7, "segment": "free"}, tables=seg_tables())
    assert not ro.get("error") and "free Planner couples" in ro["definition"]
    ro, _, _ = run("planner_second_question", {"segment": "vip"}, tables=seg_tables())
    assert "segment" in ro["error"]


def test_planner_scorecard_rides_the_same_load():
    c, _, _ = fake_client(seg_tables())
    out, ctx = M.run_all([], c, now_ms=NOW, scorecards=True)
    assert out == {}
    rows = ctx.scorecards["planner"]
    assert [r["key"] for r in rows] == ["free_pdf_open", "forever_return", "forever_second_question"]
    assert all(not r["error"] for r in rows), rows
    assert all(r["before_label"] == "7 days before" and r["after_label"] == "Last 7 days" for r in rows)
    assert c.requests == 1          # one usage load serves all three
