"""Photo-first 50/50 (REC_PHOTO_FIRST_AB_PCT, 2026-11-27): fixed scenarios whose outputs are compared between code
versions. run(monkeypatch) returns {name: output} for: app._pf on the photo-first test fixtures (full / light / free,
page asking or not, two accounts), "Show 3 more" over HTTP with and without ?photo_first=1, and a reopened chat
(GET /rec/chats/{id}) for a member and a free couple, with and without ?photo_first=1.

tests/fixtures/pf_split_main_golden.json holds the outputs of origin/main (8829339), generated with this file;
test_photo_first_split.py checks pct 0 reproduces them exactly. No network, no model."""
import copy
import json

from fastapi.testclient import TestClient

import app
import config
import photo_first as pf
import xano

USERS = ({"id": 7}, {"id": 40213})


CLEAN = [False]   # True: cards carry no photo-first-only keys, as the search builds them with REC_PHOTO_FIRST=0


def venue(vid="V1", **kw):
    d = dict({"vendor_id": vid, "vendor_idx": 1, "name": "Brooklyn Botanic Garden", "state": "New York",
                 "address": "990 Washington Ave, Brooklyn, NY 11225", "venue_type": "Garden / Botanical Garden",
                 "max_capacity_seated": 220, "image": "https://x/1.jpg", "venue_fee_range": [7000, 7000],
                 "rental_source": "pdf", "rental_when": "peak_saturday", "estimate_total": 38115, "estimate_guests": 100,
                 "estimate_label": "About $38,115 for 100 guests", "setting_tags": ["Garden / Botanical Garden"]}, **kw)
    return {k: v for k, v in d.items() if k not in pf.PF_ONLY_KEYS} if CLEAN[0] else d


def answer(cards=None, **kw):
    return dict({"text": "99 Scott is a Brooklyn fit: about $36,900 for 100 guests. Prospect Park follows. "
                         "Open the PDF.", "cards": cards if cards is not None else [venue("V1"), venue("V2"), venue("V3")],
                 "chips": [], "search_filters": {"states": ["New York"], "city": "Brooklyn", "guests": 100},
                 "paid": True, "has_access": True}, **kw)


def answers():
    return {
        "full": answer(),
        "light": answer(paid=False),
        "free": answer(paid=False, has_access=False, price_locked=True),
        "no_cards": answer(cards=[], text="We don't have Foo Hall in Tulle yet. Want similar venues near Brooklyn?",
                           **({} if CLEAN[0] else {"unmatched_name": "Foo Hall"})),
        "elsewhere": answer(cards=[venue()], lookup={"status": "elsewhere", "query": "bbg"}),
        "more": answer(is_more=True),
    }


def _stub(monkeypatch, paid, access, switch=True):
    monkeypatch.setattr(config, "PHOTO_FIRST", switch)
    monkeypatch.setattr(config, "KILL_SWITCH", False)
    monkeypatch.setattr(config, "FOREVER_ONLY", False)   # pinned: the suite imports config with REC_FOREVER_ONLY=0 or 1 by file order
    monkeypatch.setattr(app, "_chat_used", lambda *a: None)
    monkeypatch.setattr(xano, "get_chat", lambda u, c: {"id": c, "title": "t"})
    monkeypatch.setattr(app, "_gate", lambda *a, **k: (paid, access, [], True, False, {}))
    monkeypatch.setattr(xano, "log_usage", lambda *a, **k: None)
    monkeypatch.setattr(xano, "mp_track", lambda *a, **k: None)
    monkeypatch.setattr(xano, "profile_of", lambda u: {"Wedding_Guest_Count": 90})
    monkeypatch.setattr(xano, "add_message", lambda *a, **k: None)
    monkeypatch.setattr(xano, "touch_chat", lambda *a, **k: None)
    monkeypatch.setattr(xano, "has_paid_access", lambda u: access)
    monkeypatch.setattr(xano, "beta_member", lambda u: paid)
    monkeypatch.setattr(xano, "pi_entitled", lambda u: access)
    monkeypatch.setattr(app.sheet_log, "event", lambda *a, **k: None)


def _stored_history():
    out = answer(cards=[venue("V1"), venue("V2")], lookup={"status": "elsewhere", "query": "bbg"})
    rows = [{"id": 1, "role": "user", "content": "garden venues in Brooklyn", "cards": []},
            {"id": 2, "role": "assistant", "content": out["text"], "cards": app._stored_cards(out)}]
    return [xano.parse_message_row(r) for r in rows]


def _http(monkeypatch, user, paid, access, switch=True):
    _stub(monkeypatch, paid, access, switch)
    monkeypatch.setattr(app, "_auth", lambda a: ("tok", dict(user)))
    hist = [{"role": "assistant", "text": "x", "cards": [], "filters": {"states": ["New York"], "city": "Brooklyn", "guests": 150},
             "more_cards": [venue("V%d" % i, estimate_guests=150) for i in range(5)]}]
    monkeypatch.setattr(xano, "get_messages", lambda c: copy.deepcopy(hist))
    c, h, got = TestClient(app.app), {"Authorization": "Bearer t"}, {}
    for q in ("", "?photo_first=1"):
        got["more" + q] = c.post("/rec/more" + q, json={"chat_id": 3}, headers=h).json()
    stored = _stored_history()
    monkeypatch.setattr(xano, "get_messages", lambda c: copy.deepcopy(stored))
    for q in ("", "?photo_first=1"):
        got["chat" + q] = c.get("/rec/chats/5" + q, headers=h).json()
    return got


def run(monkeypatch, users=USERS, switch=True, clean=False):
    """Every scenario's output, JSON-normalised (sort_keys) so two runs compare as strings. switch = REC_PHOTO_FIRST;
    clean = inputs as the switch-off pipeline builds them (no photo-first-only card keys)."""
    out = {}
    CLEAN[0] = clean
    try:
        return _run(monkeypatch, users, switch, out)
    finally:
        CLEAN[0] = False


def _run(monkeypatch, users, switch, out):
    _stub(monkeypatch, True, True, switch)
    for u in users:
        for asked in (False, True):
            tok = pf.REQ.set(asked)
            try:
                for name, a in answers().items():
                    out["pf/%s/%d/asked=%s" % (name, u["id"], asked)] = app._pf(copy.deepcopy(a), u)
            finally:
                pf.REQ.reset(tok)
    for u in users:
        for tier, paid, access in (("member", True, True), ("free", False, False)):
            for k, v in _http(monkeypatch, u, paid, access, switch).items():
                out["http/%s/%d/%s" % (tier, u["id"], k)] = v
    return json.loads(json.dumps(out, sort_keys=True, default=str))
