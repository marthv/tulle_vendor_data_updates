"""Photo-first 50/50 by account (REC_PHOTO_FIRST_AB_PCT / _SALT / REC_PHOTO_FIRST_USER_IDS, owner 2026-11-27).
The server decides the arm, so the page can send ?photo_first=1 for everyone: a 'control' account gets exactly what a
page not asking gets. pct 0 = origin/main (8829339) output, byte for byte, on fixed scenarios. No network, no model."""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
from fastapi import Header  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import config  # noqa: E402
import photo_first as pf  # noqa: E402
import pf_split_scenarios as sc  # noqa: E402
import xano  # noqa: E402

GOLDEN = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures" / "pf_split_main_golden.json").read_text("utf-8"))
ASKED = ("asked=True", "?photo_first=1")
# What only a photo-first answer carries (photo_first.apply / the stored marks).
PF_MARKS = ("photo_first", "facts", "price_line", "understood", "not_found", "unmatched_name", "pf_meta") + pf.PF_ONLY_KEYS


def _set(monkeypatch, pct, testers=(), salt="photo-first-2026-11", switch=True):
    monkeypatch.setattr(config, "PHOTO_FIRST", switch)
    monkeypatch.setattr(config, "PHOTO_FIRST_AB_PCT", pct)
    monkeypatch.setattr(config, "PHOTO_FIRST_AB_SALT", salt)
    monkeypatch.setattr(config, "PHOTO_FIRST_USER_IDS", set(testers))


def marks(o, path=""):
    """Every photo-first mark in a response (key path list). Empty = nothing photo-first went out."""
    found = []
    if isinstance(o, dict):
        for k, v in o.items():
            if k in PF_MARKS and v not in (None, False, [], {}):
                found.append(path + "." + k)
            found += marks(v, path + "." + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            found += marks(v, "%s[%d]" % (path, i))
    return found


def control_violations(outputs, control_ids):
    """The test's own check: a control account must never receive a photo-first mark, page asking or not."""
    bad = []
    for k, v in outputs.items():
        if any("/%d/" % uid in k for uid in control_ids) and marks(v):
            bad.append((k, marks(v)[:3]))
    return bad


def _salt_for(control_id, pf_id, pct=50):
    """A salt (deterministic search) that puts control_id in control and pf_id in photo_first at pct."""
    for i in range(1000):
        s = "split-test-%d" % i

        def a(uid):
            import hashlib
            return "photo_first" if int(hashlib.sha256(("%s:%d" % (s, uid)).encode()).hexdigest()[:8], 16) % 100 < pct else "control"
        if a(control_id) == "control" and a(pf_id) == "photo_first":
            return s
    raise AssertionError("no salt")


# ---------------------------------------------------------------- arm assignment

def test_arm_is_stable_and_close_to_half(monkeypatch):
    _set(monkeypatch, 50)
    arms = [pf.arm(uid) for uid in range(1, 10001)]
    assert arms == [pf.arm(uid) for uid in range(1, 10001)]          # stable: same account, same arm, every call
    share = arms.count("photo_first") / 10000
    assert 0.48 <= share <= 0.52, share                               # ~4 standard deviations either side of 50%
    assert set(arms) == {"photo_first", "control"}
    assert pf.arm("123") == pf.arm(123)                               # the id's type does not matter


def test_salt_reshuffles_and_pct_is_respected(monkeypatch):
    _set(monkeypatch, 50)
    a = [pf.arm(u) for u in range(1, 2001)]
    _set(monkeypatch, 50, salt="another-salt")
    b = [pf.arm(u) for u in range(1, 2001)]
    assert 0.4 < sum(x != y for x, y in zip(a, b)) / 2000 < 0.6        # a new salt is a new split
    _set(monkeypatch, 10)
    share = [pf.arm(u) for u in range(1, 10001)].count("photo_first") / 10000
    assert 0.08 <= share <= 0.12
    _set(monkeypatch, 100)
    assert {pf.arm(u) for u in range(1, 1001)} == {"photo_first"}


def test_testers_always_in_and_off_states(monkeypatch):
    testers = [u for u in range(1, 400) if (_set(monkeypatch, 50) or pf.arm(u)) == "control"][:5]
    _set(monkeypatch, 50, testers=testers)
    assert {pf.arm(u) for u in testers} == {"photo_first"}
    _set(monkeypatch, 0, testers=testers)
    assert {pf.arm(u) for u in testers} == {"photo_first"} and pf.arm(999) == "off"
    _set(monkeypatch, 50, testers=testers, switch=False)              # switch off: nobody, testers included
    assert {pf.arm(u) for u in testers + [999]} == {"off"}
    _set(monkeypatch, 50)
    assert pf.arm(0) == pf.arm(None) == pf.arm("x") == "off"


def test_on_follows_page_and_arm(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    tok = pf.REQ.set(True)
    try:
        assert pf.on({"id": 40213}) and not pf.on({"id": 7})
        assert not pf.on({"id": 0})                                   # pct > 0 and no account: control's answer
        _set(monkeypatch, 0, salt=s)
        assert pf.on({"id": 7}) and pf.on({"id": 40213}) and pf.on()  # pct 0: a page asking gets it, as on main
    finally:
        pf.REQ.reset(tok)
    _set(monkeypatch, 50, salt=s)
    assert not pf.on({"id": 40213})                                   # the page did not ask: never


# ---------------------------------------------------------------- outputs

def test_pct_zero_is_origin_main_output(monkeypatch):
    _set(monkeypatch, 0)
    got = sc.run(monkeypatch)
    assert sorted(got) == sorted(GOLDEN)
    diff = [k for k in GOLDEN if json.dumps(got[k], sort_keys=True) != json.dumps(GOLDEN[k], sort_keys=True)]
    assert not diff, diff


def test_golden_really_has_photo_first_on_the_asking_page():
    """The pct-0 check above is only meaningful if main's asking-page outputs are photo-first and the others are not."""
    for k, v in GOLDEN.items():
        if k.endswith(ASKED) and not k.startswith("pf/no_cards"):
            assert marks(v), k
        if not k.endswith(ASKED):
            assert not marks(v), (k, marks(v))


def _arm_outputs(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    return sc.run(monkeypatch)


def test_control_arm_gets_the_not_asking_answer_and_photo_first_arm_gets_main(monkeypatch):
    got = _arm_outputs(monkeypatch)
    for k, v in got.items():
        if "/7/" in k:   # control: the page asking == the page not asking (on main), for every scenario
            plain = k.replace("asked=True", "asked=False").replace("?photo_first=1", "")
            assert json.dumps(v, sort_keys=True) == json.dumps(GOLDEN[plain], sort_keys=True), k
        else:            # photo_first arm: exactly main's output
            assert json.dumps(v, sort_keys=True) == json.dumps(GOLDEN[k], sort_keys=True), k
    assert not control_violations(got, [7])


def test_control_arm_equals_switch_off(monkeypatch):
    """Inputs as the switch-off pipeline builds them: control (switch on, page asking) == REC_PHOTO_FIRST=0."""
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    ctrl = sc.run(monkeypatch, users=({"id": 7},), clean=True)
    off = sc.run(monkeypatch, users=({"id": 7},), switch=False, clean=True)
    assert sorted(ctrl) == sorted(off)
    diff = [k for k in ctrl if json.dumps(ctrl[k], sort_keys=True) != json.dumps(off[k], sort_keys=True)]
    assert not diff, diff


def test_free_couples_have_no_price_digit_in_either_arm(monkeypatch):
    got = _arm_outputs(monkeypatch)
    money = re.compile(r"\$\s?\d|\d[\d,]*\s?[kK]\b")
    free = {k: v for k, v in got.items() if k.startswith("http/free/")}
    assert len(free) == 8 and {k.split("/")[2] for k in free} == {"7", "40213"}
    for k, v in free.items():
        blob = json.dumps(v)
        assert not money.search(blob), k
        assert '"price_line"' not in blob and '"estimate_total"' not in blob, k


# ---------------------------------------------------------------- canaries: the checks above can fail

def test_canary_control_receiving_photo_first_is_caught(monkeypatch):
    """Plant the bug (on() ignores the arm, as main does): the control check must fail."""
    got_ok = _arm_outputs(monkeypatch)
    assert not control_violations(got_ok, [7])
    monkeypatch.setattr(pf, "on", lambda user=None: bool(config.PHOTO_FIRST and pf.REQ.get()))
    planted = sc.run(monkeypatch)
    assert control_violations(planted, [7]), "the control check passed a control account that got photo-first"


def test_canary_planted_marks_and_digits_are_caught():
    assert marks({"turns": [{"cards": [{"facts": [{"label": "Seats 220"}]}]}]})
    assert marks({"photo_first": True}) and not marks({"photo_first": False, "text": "x"})
    assert control_violations({"http/member/7/chat?photo_first=1": {"understood": {"place": "Brooklyn"}}}, [7])
    assert re.search(r"\$\s?\d|\d[\d,]*\s?[kK]\b", json.dumps({"text": "about $38K"}))


def test_canary_golden_mismatch_is_caught(monkeypatch):
    _set(monkeypatch, 0)
    got = sc.run(monkeypatch)
    k = "http/member/7/more?photo_first=1"
    got[k] = dict(got[k], text=got[k]["text"] + " ")
    assert json.dumps(got[k], sort_keys=True) != json.dumps(GOLDEN[k], sort_keys=True)


# ---------------------------------------------------------------- wiring: real _auth, /rec/tool, Mixpanel, /rec/status

def test_real_auth_fills_the_request_account(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    if not any(getattr(r, "path", "") == "/_pf_who_probe" for r in app.app.routes):
        def probe(authorization: str = Header(None)):
            app._auth(authorization)
            return {"on": pf.on(), "arm": pf.arm(pf._uid())}
        app.app.get("/_pf_who_probe")(probe)
    c = TestClient(app.app)
    for uid, arm in ((7, "control"), (40213, "photo_first")):
        monkeypatch.setattr(xano, "verify_user", lambda t, uid=uid: {"id": uid})
        assert c.get("/_pf_who_probe?photo_first=1", headers={"Authorization": "Bearer t"}).json() == {
            "on": arm == "photo_first", "arm": arm}
        assert c.get("/_pf_who_probe", headers={"Authorization": "Bearer t"}).json()["on"] is False
    assert pf.WHO.get() is None                                       # the box never leaks past the request


def test_cost_tool_is_unknown_for_control(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    r = TestClient(app.app).post("/rec/tool?photo_first=1", json={"tool": "cost_lines", "args": {"vendor_id": "V1"}},
                                 headers={"Authorization": "Bearer t"})
    assert r.status_code == 400 and r.json()["detail"]["status"] == "unknown_tool"


def test_mixpanel_planner_events_carry_the_arm(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    sent = []
    monkeypatch.setattr(config, "MIXPANEL_TOKEN", "tok")
    monkeypatch.setattr(xano.requests, "post", lambda url, json=None, timeout=None: sent.append(json))
    xano.mp_track(7, "rec_server_request", {"kind": "refine"})
    xano.mp_track(40213, "rec_server_request", {"kind": "refine"})
    xano.mp_track(40213, "page_view", {})                             # not a Planner event: untouched, as ab_plan
    assert [b[0]["properties"].get("pf_arm") for b in sent] == ["control", "photo_first", None]
    _set(monkeypatch, 0, salt=s)
    xano.mp_track(7, "rec_server_request", {})
    assert sent[-1][0]["properties"]["pf_arm"] == "off"


def test_status_reports_the_arm(monkeypatch):
    s = _salt_for(7, 40213)
    _set(monkeypatch, 50, salt=s)
    monkeypatch.setattr(xano, "beta_member", lambda u: True)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: True)
    monkeypatch.setattr(xano, "has_forever", lambda u: True)
    monkeypatch.setattr(xano, "pi_entitled", lambda u: True)
    monkeypatch.setattr(xano, "user_usage", lambda uid: [])
    monkeypatch.setattr(app, "_feedback_given", lambda uid: True)
    monkeypatch.setattr(app, "_memory_row", lambda uid: {})
    monkeypatch.setattr(app, "_memory_notes", lambda uid: [])
    c = TestClient(app.app)
    for uid, arm in ((7, "control"), (40213, "photo_first")):
        monkeypatch.setattr(app, "_auth", lambda a, uid=uid: ("tok", {"id": uid}))
        r = c.get("/rec/status?photo_first=1", headers={"Authorization": "Bearer t"}).json()
        assert r["photo_first_arm"] == arm and r.get("photo_first", False) is (arm == "photo_first")
    _set(monkeypatch, 0, salt=s)
    r = c.get("/rec/status?photo_first=1", headers={"Authorization": "Bearer t"}).json()
    assert r["photo_first_arm"] == "off" and r["photo_first"] is True   # pct 0: as on main
