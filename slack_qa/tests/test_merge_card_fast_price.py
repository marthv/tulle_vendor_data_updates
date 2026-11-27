"""MERGE_CARD_FAST, the price check: same price text, and a slow read is waited for. Needs quickjs (production has it)."""
import json
import os
import re
import sys
import threading
import time

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
pytest.importorskip("quickjs")
import merge_checks as mc  # noqa: E402
import price_impact as pi  # noqa: E402


import test_price_impact as tpi  # noqa: E402  (toy pricing source and toy world; never the live source)


@pytest.fixture
def toy(monkeypatch):
    pi._CODE.clear()
    monkeypatch.setattr(pi, "PINNED", {54: pi.fingerprint(tpi.TOY54, 54), 56: pi.fingerprint(tpi.TOY56, 56),
                                       21: pi.fingerprint(tpi.TOY21, 21)})
    yield
    pi._CODE.clear()


class Clock:
    """Virtual time for price_impact: a slow read costs no real seconds. Reads in parallel are ADDED, so the
    time measured is an upper bound."""

    def __init__(self):
        self.now, self.lock = 1000.0, threading.Lock()

    def monotonic(self):
        return self.now

    def time(self):
        return 1.8e9 + self.now

    def sleep(self, s):
        with self.lock:
            self.now += max(0.0, s)


class SlowFake(tpi.Fake):
    """The toy Xano; each request takes `lat(kind)` virtual seconds and times out like requests does."""

    def __init__(self, tables, clock, lat):
        super().__init__(tables)
        self.clock, self.lat = clock, lat

    def _cost(self, kind, timeout):
        d = self.lat(kind)
        if timeout is not None and d > timeout:
            self.clock.sleep(timeout)
            raise requests.ReadTimeout("read timed out")
        self.clock.sleep(d)

    def get(self, url, params=None, headers=None, timeout=None):
        self._cost("fn" if "/function/" in url else "page", timeout)
        return super().get(url, params, headers, timeout)

    def post(self, url, json=None, headers=None, timeout=None):
        self._cost(int(re.search(r"/table/(\d+)/", url).group(1)), timeout)
        return super().post(url, json, headers, timeout)


def _run(monkeypatch, lat, timeouts, deadline):
    clock = Clock()
    monkeypatch.setattr(pi, "time", clock)
    pi._CODE.clear()
    fake = SlowFake(tpi.world(s_fee=10000, l_fee=12000), clock, lat)
    t0 = clock.now
    r = pi.compute(fake, "V1", ["V2"], deadline=deadline, timeouts=timeouts)
    return r, pi.blocks(r), clock.now - t0


def test_a_read_slower_than_twelve_seconds_was_cant_compute_and_is_now_waited_for(monkeypatch, toy):
    """11-25: Xano was 2-3x slower than usual (ep578 previews 20-30 s vs 10 s at 06:20 UTC). One read at 13 s."""
    slow = lambda k: 13.0 if k == 37 else 1.0                                     # noqa: E731
    old, ob, _ = _run(monkeypatch, slow, pi.TIMEOUTS, mc.SECONDS)
    assert old["cant"] == ["Xano did not answer (ReadTimeout)"] and "can't compute" in tpi.text(ob)
    new, nb, secs = _run(monkeypatch, slow, pi.PATIENT_TIMEOUTS, mc.PATIENT_SECONDS)
    ref, rb, _ = _run(monkeypatch, lambda k: 0.0, pi.TIMEOUTS, mc.SECONDS)
    assert not new["cant"] and nb == rb and new["stamp"] == ref["stamp"]
    assert secs < 30                                                               # a typical pair, one slow read


def test_typical_pair_on_a_slow_xano_takes_well_under_thirty_seconds(monkeypatch, toy):
    """Every read 3 s (3x a normal read): eleven reads, two at a time."""
    new, nb, secs = _run(monkeypatch, lambda k: 3.0, pi.PATIENT_TIMEOUTS, mc.PATIENT_SECONDS)
    assert not new["cant"] and new["calls"] == 11 and secs <= 33          # virtual seconds, parallel reads added up
    ref, rb, _ = _run(monkeypatch, lambda k: 0.0, pi.TIMEOUTS, mc.SECONDS)
    assert nb == rb


def test_a_read_that_never_answers_is_still_cant_compute(monkeypatch, toy):
    r, b, secs = _run(monkeypatch, lambda k: 999.0 if k == 36 else 0.5, pi.PATIENT_TIMEOUTS, mc.PATIENT_SECONDS)
    assert r["alert"] and r["cant"] and "can't compute" in tpi.text(b) and secs <= mc.PATIENT_SECONDS + 1


def test_five_cards_at_once_all_get_a_price_real_time(monkeypatch, toy):
    """Five price checks started together on a Xano where every read takes 0.25 s (real time): they queue one at a
    time (merge_checks._ONE) and none says can't compute."""
    monkeypatch.setenv("MERGE_CARD_FAST", "1")
    monkeypatch.delenv("XANO_MERGE_URL", raising=False)

    class RealSlow(tpi.Fake):
        def get(self, *a, **k):
            time.sleep(0.25)
            return super().get(*a, **k)

        def post(self, *a, **k):
            time.sleep(0.25)
            return super().post(*a, **k)
    w = tpi.world()
    out, t0 = [], time.monotonic()

    def one():
        out.append(mc.price(RealSlow(w), {"survivor": "V1", "losers": ["V2"]}))
    ts = [threading.Thread(target=one) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    assert len(out) == 5 and all(o["status"] == "ok" and not o["cant"] for o in out), [o.get("cant") for o in out]
    assert all(o["seconds"] < 30 for o in out) and time.monotonic() - t0 < 60


# ------------------------------------------------------------ recorded inputs (2026-11-22 rows + live source, offline)
# The recording holds vendor rows and the live pricing source, so it is NOT in this public repo: point
# MERGE_PRICE_FIXTURES at the folder (raw.json, sample.json, fn54.xs, fn56.xs, fn21.xs); skipped without it.

FIX = os.environ.get("MERGE_PRICE_FIXTURES", "")


def _recorded():
    if not FIX or not os.path.exists(os.path.join(FIX, "raw.json")):
        pytest.skip("recorded price fixtures not available (set MERGE_PRICE_FIXTURES)")
    raw = json.load(open(os.path.join(FIX, "raw.json"), encoding="utf-8"))
    src = {f: open(os.path.join(FIX, f"fn{f}.xs"), encoding="utf-8").read() for f in (54, 56, 21)}
    groups = json.load(open(os.path.join(FIX, "sample.json"), encoding="utf-8"))["groups"]
    ids = {r["Vendor_ID"] for r in raw["11"]}
    return raw, src, [g for g in groups if all(v in ids for v in g)]


class RecordedFake:
    root, token = "https://x/api:meta/workspace/1", "t"

    def __init__(self, raw, src, clock, lat):
        self.http, self.raw, self.src, self.clock, self.lat, self.run = self, raw, src, clock, lat, None

    def _cost(self, timeout):
        d = self.lat()
        if timeout is not None and d > timeout:
            self.clock.sleep(timeout)
            raise requests.ReadTimeout("read timed out")
        self.clock.sleep(d)

    def get(self, url, params=None, headers=None, timeout=None):
        self._cost(timeout)
        m = re.search(r"/function/(\d+)$", url)
        if m:
            return tpi.Resp({"xanoscript": self.src[int(m.group(1))], "updated_at": "x"})
        v = params["vendor_id"]                     # the live page = fn56 over the stored rows, as Xano serves it
        self.run = self.run or pi.engine(self.src[54], self.src[56])
        rows = sorted([r for r in self.raw["62"] if r["VENDOR_ID"] == v], key=lambda r: r.get("sort_rank") or 0)
        vend = next(r for r in self.raw["11"] if r["Vendor_ID"] == v)
        st = {"__NATIONAL__"}
        st.update([str(t) for t in (vend.get("flt_states") or []) if t and not str(t).startswith("country:")][:1])
        st.update(str(t) for r in rows for t in [x for x in (r.get("state_tokens") or []) if x][:1])
        return tpi.Resp(pi.sim_panel(self.run, v, rows, str(vend.get("Category") or ""),
                                     [b for b in self.raw["63"] if b.get("State") in st], entitled=False))

    def post(self, url, json=None, headers=None, timeout=None):
        self._cost(timeout)
        t = re.search(r"/table/(\d+)/content/search$", url).group(1)
        (key, vals), = json["search"].items()
        col = key.split("|")[0]
        items = sorted([r for r in self.raw.get(t, []) if str(r.get(col)) in vals], key=lambda r: r["id"])
        p, n = json["page"], json["per_page"]
        return tpi.Resp({"items": items[(p - 1) * n: p * n], "nextPage": p + 1 if p * n < len(items) else None})


@pytest.mark.parametrize("lat", [0.0, 6.0])
def test_recorded_pairs_price_section_is_unchanged(monkeypatch, lat):
    """For every recorded group: the card's price section (full blocks and the short card) with the flag on equals
    the one with it off. lat 6 s per read: slow, but inside the old 8 s timeout, so both must answer."""
    raw, src, groups = _recorded()
    clock = Clock()
    monkeypatch.setattr(pi, "time", clock)
    seen = 0
    for g in groups:
        outs = []
        for timeouts, secs in ((pi.TIMEOUTS, 600.0), (pi.PATIENT_TIMEOUTS, 600.0)):
            pi._CODE.clear()
            r = pi.compute(RecordedFake(raw, src, clock, lambda: lat), g[0], g[1:], deadline=secs, timeouts=timeouts)
            b = pi.blocks(r)
            outs.append((r["stamp"], b, mc.short(r, b), r["cant"]))
        assert outs[0] == outs[1], g
        seen += not outs[0][3]
    assert seen >= len(groups) * 0.9            # the comparison is of real prices, not two "can't compute"s


# ------------------------------------------------------- the queue: three cards and an Approve (QA 11-26, item 2)
# Real threads and the real queue, real time scaled down 20x (1 simulated second = 0.05 s): every merge_checks /
# price_impact time limit is scaled with it, so the semaphore wait, the deadlines and the timeouts meet as they would.

REAL_SUBMIT = mc.submit          # conftest makes submit inline per test; the queue itself is under test here
SCALE = 0.05


def _scaled(monkeypatch):
    for k in ("WAIT", "PREVIEW_TIMEOUT", "SECONDS", "PATIENT_SECONDS", "BREATH"):
        monkeypatch.setattr(mc, k, getattr(mc, k) * SCALE)
    monkeypatch.setattr(pi, "TIMEOUTS", tuple(x * SCALE for x in pi.TIMEOUTS))
    monkeypatch.setattr(pi, "PATIENT_TIMEOUTS", tuple(x * SCALE for x in pi.PATIENT_TIMEOUTS))


def _queue_run(monkeypatch, preview_s, read_s, seed):
    import random
    rnd, lock = random.Random(seed), threading.Lock()

    def draw(lo, hi):
        with lock:
            return rnd.uniform(lo, hi)
    monkeypatch.setenv("MERGE_CARD_FAST", "1")
    _scaled(monkeypatch)

    def preview(payload, hide):                      # ep578's preview: slow, and cut off at PREVIEW_TIMEOUT like _post
        time.sleep(min(draw(*preview_s) * SCALE, mc.PREVIEW_TIMEOUT))
        return None
    monkeypatch.setattr(mc, "preview", preview)

    class Slow(tpi.Fake):
        def _wait(self, timeout):
            d = draw(*read_s) * SCALE
            if timeout is not None and d > timeout:
                time.sleep(timeout)
                raise requests.ReadTimeout("read timed out")
            time.sleep(d)

        def get(self, url, params=None, headers=None, timeout=None):
            self._wait(timeout)
            return super().get(url, params, headers, timeout)

        def post(self, url, json=None, headers=None, timeout=None):
            self._wait(timeout)
            return super().post(url, json, headers, timeout)
    w, pl = tpi.world(), {"survivor": "V1", "losers": ["V2"]}
    out, done = {}, threading.Event()
    t0 = time.monotonic()

    def card(i):
        def job():
            out[f"card{i}"] = (mc.price(Slow(w), pl), (time.monotonic() - t0) / SCALE)
            if sum(k.startswith("card") for k in out) == 3:
                done.set()
        return job
    for i in (1, 2, 3):
        REAL_SUBMIT(card(i))
    time.sleep(5 * SCALE)                            # Approve pressed on an older card while the first is computing

    def approve():
        out["approve"] = (mc.price(Slow(w), pl, pv={"survivor": "V1", "losers": ["V2"], "hide_pdf_ids": []}),
                          (time.monotonic() - t0) / SCALE)
    a = threading.Thread(target=approve)
    a.start()
    a.join(600 * SCALE)
    done.wait(1200 * SCALE)
    return out


def _report(out):
    return {k: (round(at), r["status"], r.get("cant"), json.dumps(r.get("extra_blocks"))[:160]) for k, (r, at) in out.items()}


def test_three_queued_cards_and_an_approve_on_a_slow_preview(monkeypatch, toy):
    """ep578 preview 20-60 s, Xano reads 2-5 s (11-25 was ~2-3x slow). All three cards and the Approve get a price;
    the Approve waits behind at most the one check that is running (well under WAIT = 240 s)."""
    out = _queue_run(monkeypatch, (20, 60), (2, 5), seed=1)
    assert sorted(out) == ["approve", "card1", "card2", "card3"], _report(out)
    for k, (r, at) in out.items():
        assert r["status"] == "ok" and not r["cant"], _report(out)
    assert out["approve"][1] < 240, _report(out)


def test_reads_of_twenty_to_sixty_seconds_are_the_limit(monkeypatch, toy):
    """Every read 20-60 s (and the preview too): eleven reads cannot fit in 90 s, so each card says can't compute and
    so does Approve (20/30 s reads since 8d432d3). Pinned so the limit is known, not hidden. The queue itself never refuses ("other
    price checks were still running"): queued cards wait in the queue, not on the 240 s lock."""
    out = _queue_run(monkeypatch, (20, 60), (20, 60), seed=2)
    assert sorted(out) == ["approve", "card1", "card2", "card3"], _report(out)
    for k, (r, at) in out.items():
        assert r["cant"] and "other price checks were still running" not in json.dumps(r), _report(out)
