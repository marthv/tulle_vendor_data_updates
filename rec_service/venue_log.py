"""Venue lookup log (config.VENUE_LOG, 2026-11-18; owner's 14-day Planner review 11-04..11-17).

One row per venue a couple looks for - even an approximate one - in Xano table `venue_lookup_log`
(config.VENUE_LOG_TABLE_ID), so the weekly digest (venue_digest.py at the repo root) can list what couples ask for that
we don't have, the matches worth a second look, and the venues we have but missed.

  source   'lookup'       the fast path (app._lookup_fast)
           'model_search' the model's search_venues ran with a keyword (a venue name)
           'vendor_page'  a bubble question about the vendor page it was asked on (REC_PAGE_VENUE)
  outcome  'found' | 'best' | 'choices' | 'not_found' | 'dym' (a "did you mean" card) | 'elsewhere' (the only match
           is outside the place they named) | 'wrong' (the same couple typed a similar name again within
           WRONG_WINDOW_S - the answer did not give them what they asked for; `answered_as` keeps what it said)

Writes never block or fail an answer: app.py submits record() to its background pool; each row is held WRONG_WINDOW_S
(so a retype can still mark it 'wrong') and then written by a daemon timer. A restart loses at most the rows still held.
No table id configured, or the table missing (404) -> nothing is written and that is printed once."""
import difflib
import json
import threading
import time

import config
import lookup
import xano

WRONG_WINDOW_S = 120
TYPED_MAX = 200
# Outcomes that claimed something; a retype after one of these means it was wrong. A "choices" / "dym" answer
# asked the couple to pick, so typing one of the names next is the expected follow-up, not a miss.
_CLAIMS = ("found", "best", "not_found", "elsewhere")

_LOCK = threading.Lock()
_PENDING = {}          # user_id -> its latest held entry {"row", "t", "timer", "done", "req"}
_OPEN = []             # every entry not yet written (an older one of a couple stays here until its timer fires)
_STATE = {"disabled": False, "said": set()}


def _say_once(key, msg):
    if key not in _STATE["said"]:
        _STATE["said"].add(key)
        print(msg)


def _write(row):
    """POST one row through the metadata API (the same path as rec_usage). Never raises."""
    if _STATE["disabled"]:
        return False
    if not config.VENUE_LOG_TABLE_ID:
        _say_once("no_table", "venue_log: REC_VENUE_LOG_TABLE_ID not set - venue lookups are not written")
        return False
    try:
        xano._meta("POST", "/table/%d/content" % config.VENUE_LOG_TABLE_ID, json=row)
        return True
    except Exception as e:
        msg = str(e)
        if " 404 " in msg or "not found" in msg.lower():
            _STATE["disabled"] = True
            _say_once("missing", "venue_log: table %s not found - writes off until restart" % config.VENUE_LOG_TABLE_ID)
        else:
            print("venue_log write failed:", msg[:200])
        return False


def _drop(entry):
    for i, e in enumerate(_OPEN):     # by identity: two held rows can be equal dicts
        if e is entry:
            del _OPEN[i]
            return


def _flush(uid, entry):
    with _LOCK:
        _drop(entry)
        if entry["done"]:
            return
        entry["done"] = True
        if _PENDING.get(uid) is entry:
            _PENDING.pop(uid, None)
    _write(entry["row"])


def _similar(a, b):
    """The same venue typed again: a shared distinctive word, or close spelling of the whole name."""
    da, db = set(lookup._distinctive(a)), set(lookup._distinctive(b))
    if da & db:
        return True
    ka, kb = lookup.key(a), lookup.key(b)
    return bool(ka and kb) and difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.75


def record(row, now=None, req=None):
    """Hold this row WRONG_WINDOW_S, then write it. A previous held row of the same couple, for a similar name, that
    claimed an answer, from an EARLIER message (req differs: several searches inside one answer are not a retype), is
    marked 'wrong' and written now. Never raises."""
    try:
        if not config.VENUE_LOG:
            return
        uid = row.get("user_id")
        now = time.time() if now is None else now
        flush_now = None
        with _LOCK:
            prev = _PENDING.get(uid)
            if (prev and not prev["done"] and now - prev["t"] <= WRONG_WINDOW_S
                    and (req is None or prev.get("req") != req)
                    and prev["row"].get("outcome") in _CLAIMS
                    and _similar(prev["row"].get("typed") or "", row.get("typed") or "")):
                prev["row"]["answered_as"] = prev["row"].get("outcome")
                prev["row"]["outcome"] = "wrong"
                if prev.get("timer"):
                    prev["timer"].cancel()
                prev["done"] = True
                flush_now = prev
            entry = {"row": row, "t": now, "timer": None, "done": False, "req": req}
            _PENDING[uid] = entry
            _OPEN.append(entry)
            if flush_now:
                _drop(flush_now)
        if flush_now:
            _write(flush_now["row"])
        if WRONG_WINDOW_S <= 0:
            _flush(uid, entry)
            return
        t = threading.Timer(WRONG_WINDOW_S, _flush, args=(uid, entry))
        t.daemon = True
        entry["timer"] = t
        t.start()
    except Exception as e:      # never let the log hurt an answer
        print("venue_log record failed:", repr(e)[:200])


def flush_all():
    """Write every held row now (tests; a graceful shutdown could call it)."""
    with _LOCK:
        items = [(e["row"].get("user_id"), e) for e in _OPEN]
    for uid, entry in items:
        if entry.get("timer"):
            entry["timer"].cancel()
        _flush(uid, entry)


# ---------------------------------------------------------------- row builders (pure)

def _hints(location):
    """(city_hint, state_hint) from a typed place: "sharon, Massachusetts" -> ("sharon", "Massachusetts")."""
    loc = lookup.place_hint(location or "") if location else ""
    state = lookup.state_of(loc) if loc else ""
    parts = [p.strip() for p in str(loc).split(",") if p.strip()]
    city = parts[0] if parts and lookup.norm(parts[0]) not in lookup.US_STATES and \
        lookup.norm(parts[0]) not in lookup.STATE_ABBR else ""
    return city[:80], state[:40]


def _plan(paid, access):
    return "paid" if (paid or access) else "free"


def base(user, paid, access, chat_id, surface, source, typed):
    return {"user_id": int(user.get("id") or 0), "plan": _plan(paid, access), "chat_id": int(chat_id or 0),
            "surface": str(surface or "")[:80], "source": source,
            "typed": " ".join(str(typed or "").split())[:TYPED_MAX]}


def _alts(items):
    return json.dumps([{"vendor_id": a.get("vendor_id"), "name": a.get("name"), "score": a.get("score")}
                       for a in items][:3])


def from_lookup(user, paid, access, chat_id, surface, typed, q, res):
    """The fast path's row: what was typed, the name read from it, the place hint, and what we answered."""
    row = base(user, paid, access, chat_id, surface, "lookup", typed)
    city, state = _hints(q.get("location") or q.get("alt_location"))
    ranked = list(res.get("ranked") or [])
    scores = {r.get("vendor_id"): r.get("score") for r in ranked}
    ms = list(res.get("matches") or [])
    best = ms[0] if ms and res.get("status") != "not_found" else (None)
    others = [{"vendor_id": m.get("vendor_id"), "name": m.get("name"), "score": scores.get(m.get("vendor_id"))}
              for m in ms[1:4]] if best else []
    if not others:
        others = [r for r in ranked if not best or r.get("vendor_id") != best.get("vendor_id")]
    row.update(normalized=lookup.norm(q.get("name"))[:120], city_hint=city, state_hint=state,
               outcome=res.get("status") or "not_found", alternatives=_alts(others))
    if best:
        sc = scores.get(best.get("vendor_id"))
        if sc is None and res.get("dym_scores"):
            sc = res["dym_scores"][0]
        row.update(best_vendor_id=str(best.get("vendor_id") or ""), best_name=str(best.get("name") or "")[:160],
                   best_score=float(sc or 0))
    elif ranked:
        # not found: keep the nearest names we looked at, for the digest's "have it but missed" list
        row.update(best_vendor_id=str(ranked[0].get("vendor_id") or ""), best_name=str(ranked[0].get("name") or "")[:160],
                   best_score=float(ranked[0].get("score") or 0))
    return row


def from_model_search(user, paid, access, chat_id, surface, typed, hit):
    """hit = agent's record of one search_venues call with a keyword: {"keyword", "state", "results": [{vendor_id, name,
    state}]}. Outcome from the best name score: found >= lookup.HIGH, best >= lookup.MID, else not_found."""
    row = base(user, paid, access, chat_id, surface, "model_search", typed)
    kw = hit.get("keyword") or ""
    scored = sorted(({"vendor_id": r.get("vendor_id"), "name": r.get("name"),
                      "score": round(lookup.score(kw, r.get("name") or ""), 3)} for r in hit.get("results") or []),
                    key=lambda x: -x["score"])
    top = scored[0] if scored else None
    outcome = ("found" if top and top["score"] >= lookup.HIGH else "best" if top and top["score"] >= lookup.MID
               else "not_found")
    row.update(normalized=lookup.norm(kw)[:120], city_hint=str(hit.get("city") or "")[:80],
               state_hint=str(hit.get("state") or "")[:40], outcome=outcome, alternatives=_alts(scored[1:4]))
    if top:
        row.update(best_vendor_id=str(top["vendor_id"] or ""), best_name=str(top["name"] or "")[:160],
                   best_score=float(top["score"]))
    return row


def from_vendor_page(user, paid, access, chat_id, surface, typed, card):
    """A bubble question about the page's vendor: found when the page's vendor_idx resolved, else not_found."""
    row = base(user, paid, access, chat_id, surface, "vendor_page", typed)
    st = card.get("state") if card else ""
    st = st[0] if isinstance(st, list) and st else (st or "")
    row.update(normalized=lookup.norm((card or {}).get("name"))[:120], city_hint="", state_hint=str(st)[:40],
               outcome="found" if card else "not_found", alternatives="[]")
    if card:
        row.update(best_vendor_id=str(card.get("vendor_id") or ""), best_name=str(card.get("name") or "")[:160],
                   best_score=1.0)
    return row
