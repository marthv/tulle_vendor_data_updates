"""Photo-first round 8 (independent QA, 2026-11-26): the member "What it costs" widget worded figures more confidently
than today's Compare does for the same figures.
1. The total said "<room>, N guests, before tax and tip" with no estimate mark even when 3 of 4 rows were estimates.
   Compare marks its total "est." when any row with an amount is an estimate (VenueCompare subLine / isEst), and
   "food may be more" when the food is only the PDF's minimum.
2. A food row held at the venue's minimum (Pendry $50,000, Roundhouse $45,000) said only "From the PDF". Compare says
   "minimum spend", and the server's Compare / card wording adds "may cost more" when the PDF gives only a minimum.
Real estimate.compute + compare._cost, no network. Each canary below fails on 4034f98."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402

import config  # noqa: E402
import photo_first as pf  # noqa: E402


class _X:
    def __init__(self, inp, name="Pendry"):
        self.inp, self.name = inp, name

    def cached_vendor_card(self, vid):
        return {"vendor_id": vid, "vendor_idx": 1, "name": self.name, "category": "Venue", "state": "California"}

    def pi_entitled(self, user):
        return True

    def cached_venue_pricing(self, token, vid, full, user=None):
        return {"lines": [{"line": "Rental fee", "source": "venue"}], "cost_inputs": self.inp if full else None}


def _w(inp, name="Pendry", guests=100, access=True):
    return pf.build_cost_lines(_X(inp, name), "t", {"id": 1}, "V1", access=access, guests=guests)


# 3 of 4 rows estimated: only the rental is the venue's own
THREE_EST = {"space": "Ballroom", "rental": 8000, "pp_fb": None, "service_pct": None, "ceremony": None,
             "estimates": {"pp_fb": 150, "ceremony": 1500, "service_pct": 22}}
# the venue's PDF gives a per-guest price AND a food minimum above it (minimum applies)
MIN_APPLIES = {"space": "Ballroom", "rental": 10000, "pp_fb": 150, "fb_min": 50000, "service_pct": 24, "ceremony": 2000}
# the PDF gives only a food minimum (REC_MIN_FOOD_ONLY)
MIN_ONLY = {"space": "Main Hall", "rental": 6000, "pp_fb": None, "fb_min": 45000, "service_pct": 22, "ceremony": 1000}
# every row from the PDF, no minimum
ALL_PDF = {"space": "Ballroom", "rental": 10000, "pp_fb": 150, "service_pct": 24, "ceremony": 2000}


def _food(w):
    return next(l for l in w["lines"] if l["key"] == "food")


def test_canary_total_says_est_when_rows_are_estimates():
    w = _w(THREE_EST)
    est = [l for l in w["lines"] if l["source"] == "estimate" and l["amount"]]
    assert len(est) >= 2, w["lines"]          # the fixture really has estimated rows
    assert w["total_note"] == "Ballroom, 100 guests, before tax and tip, est."


def test_total_has_no_est_when_every_row_is_from_the_pdf():
    w = _w(ALL_PDF)
    assert all(l["source"] == "pdf" for l in w["lines"]), w["lines"]
    assert w["total_note"] == "Ballroom, 100 guests, before tax and tip" and w["estimated"] == []


def test_canary_food_held_at_minimum_says_minimum_spend():
    w = _w(MIN_APPLIES)
    f = _food(w)
    assert f["amount"] == 50000 and f["source"] == "pdf"
    assert f["note"] == "From the PDF, minimum spend"
    assert w["total_note"] == "Ballroom, 100 guests, before tax and tip"     # a minimum that applies is not a floor


def test_canary_food_only_a_minimum_says_may_cost_more(monkeypatch):
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", True)
    w = _w(MIN_ONLY, name="Roundhouse")
    f = _food(w)
    assert f["amount"] == 45000 and f["source"] == "pdf"
    assert f["note"] == "From the PDF, minimum spend, may cost more"
    assert w["total_note"] == "Main Hall, 100 guests, before tax and tip, food may be more"


def test_minimum_only_and_estimates_both_mark_the_total(monkeypatch):
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", True)
    w = _w(dict(MIN_ONLY, service_pct=None, estimates={"service_pct": 22}), name="Roundhouse")
    assert w["total_note"] == "Main Hall, 100 guests, before tax and tip, food may be more, est."


def test_food_not_at_minimum_keeps_from_the_pdf():
    w = _w(dict(MIN_APPLIES, fb_min=5000))
    assert _food(w)["note"] == "From the PDF" and _food(w)["amount"] == 15000


@pytest.mark.parametrize("inp", [THREE_EST, MIN_APPLIES, MIN_ONLY])
def test_words_only_version_carries_none_of_it(monkeypatch, inp):
    # free couples (T0-52/T0-53): the locked widget is rebuilt from a whitelist, no note, no total, no digits
    monkeypatch.setattr(config, "MIN_FOOD_ONLY", True)
    w = _w(inp, access=False)
    assert w["level"] == "words" and "total_note" not in w and "total" not in w
    assert all(set(l) == {"key", "label", "icon", "where"} for l in w["lines"])
    full = _w(inp)
    locked = pf.lock_cost(full)
    assert "total_note" not in locked and all("note" not in l and "minimum_only" not in l for l in locked["lines"])


# ---------------------------------------------------------------- T0-56: "Show 3 more" shows no figure today's card doesn't

from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import xano  # noqa: E402
from test_photo_first import venue  # noqa: E402


def _more(monkeypatch, more_cards, card_totals="0", entitled=True):
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(config, "CARD_TOTALS", card_totals)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(app, "_chat_used", lambda *a: None)
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c})
    hist = [{"role": "assistant", "text": "x", "cards": [], "more_cards": more_cards,
             "filters": {"states": ["New York"], "city": "Brooklyn", "guests": 100}}]
    monkeypatch.setattr(xano, "get_messages", lambda c: hist)
    monkeypatch.setattr(app, "_gate", lambda *a, **k: (True, True, [], True, False, {}))     # a full member
    monkeypatch.setattr(xano, "pi_entitled", lambda u: entitled)
    for n in ("log_usage", "mp_track", "add_message", "touch_chat"):
        monkeypatch.setattr(xano, n, lambda *a, **k: None)
    monkeypatch.setattr(xano, "profile_of", lambda u: {})
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)
    h = {"Authorization": "Bearer t"}
    on = TestClient(app.app).post("/rec/more?photo_first=1", json={"chat_id": 3}, headers=h).json()
    off = TestClient(app.app).post("/rec/more", json={"chat_id": 3}, headers=h).json()
    return on, off


def test_canary_show_more_member_card_has_no_estimate_line_today_does_not_show(monkeypatch):
    # cards with a rental range: today's reason is "$7,000 rental; ..." and REC_CARD_TOTALS is off, so today's card shows
    # no total - photo-first must not add "About $38,100 for 100 guests before tax and tip, an estimate"
    on, off = _more(monkeypatch, [venue("V%d" % i) for i in range(5)])
    assert on["photo_first"] is True and len(on["cards"]) == 3
    assert all("price_line" not in c for c in on["cards"]), [c.get("price_line") for c in on["cards"]]
    assert all("$38,1" not in (c.get("reason") or "") for c in off["cards"])        # today's card: no total anywhere
    assert "is_more" not in on and "is_more" not in off


def test_show_more_keeps_the_line_when_todays_reason_quotes_that_estimate(monkeypatch):
    # no rental range: agent.why_lines puts the estimate sentence in today's reason ("About $38,115 for 100 guests")
    on, off = _more(monkeypatch, [venue("V%d" % i, venue_fee_range=[None, None]) for i in range(5)])
    assert all("$38,115" in c["reason"] for c in off["cards"])
    assert all(c["price_line"]["text"] == "About $38,100 for 100 guests before tax and tip, an estimate" for c in on["cards"])


def test_show_more_keeps_the_line_when_todays_card_total_is_on(monkeypatch):
    on, _ = _more(monkeypatch, [venue("V%d" % i) for i in range(5)], card_totals="1")
    assert all(c.get("price_line") for c in on["cards"])
    on, _ = _more(monkeypatch, [venue("V%d" % i) for i in range(5)], card_totals="1", entitled=False)
    assert all("price_line" not in c for c in on["cards"])


def test_a_typed_answer_is_unchanged_by_t0_56():
    a = {"text": "x", "cards": [venue("V1"), venue("V2")], "search_filters": {"states": ["New York"], "guests": 100},
         "paid": True, "has_access": True}
    assert all(c.get("price_line") for c in pf.apply(a, "full", {})["cards"])
    assert all("price_line" not in c for c in pf.apply(dict(a, is_more=True), "full", {})["cards"])


def test_light_show_more_line_only_with_todays_band_line():
    band = {"label": "about $35K-40K all-in for 100 guests, before tax and tip", "low": 35000, "high": 40000}
    c = venue("V1", price_band=band, reason="seats 220; Garden")
    assert pf.today_shows_price(c, "light") and not pf.today_shows_price(dict(c, reason="$7,000 rental"), "light")
    assert not pf.today_shows_price(venue("V1", reason="seats 220"), "light") and not pf.today_shows_price(c, "free")


def test_reopened_show_more_turn_gets_the_same_rule(monkeypatch):
    import json as _j
    monkeypatch.setattr(config, "PHOTO_FIRST", True)
    monkeypatch.setattr(config, "CARD_TOTALS", "0")
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", {"id": 7}))
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    stored = [dict(venue("V%d" % i), reason="$7,000 rental", via_more=True) for i in range(3)]
    row = xano.parse_message_row({"id": 2, "role": "assistant", "content": "Here are 3 more", "cards": stored})
    monkeypatch.setattr(xano, "get_messages", lambda c: [{"id": 1, "role": "user", "text": "Show 3 more", "cards": []}, row])
    for n, v in (("has_paid_access", True), ("beta_member", True), ("pi_entitled", True), ("profile_of", {})):
        monkeypatch.setattr(xano, n, lambda *a, _v=v, **k: _v)
    paths = [r.path for r in app.app.routes if "chat" in getattr(r, "path", "")]
    path = next(p for p in paths if p.startswith("/rec/chats/{"))
    r = TestClient(app.app).get(path.split("{")[0] + "3?photo_first=1", headers={"Authorization": "Bearer t"})
    turn = [t for t in r.json()["turns"] if t["role"] == "assistant"][-1]
    assert turn["cards"] and all("price_line" not in c for c in turn["cards"]), _j.dumps(turn)[:400]
