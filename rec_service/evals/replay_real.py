"""Replay REAL conversations (asks taken from rec_messages, table 43) through the assistant exactly as app.py
runs them - typed questions through agent.run with the server-held history, "Show 3 more" through the
/rec/more logic (no model call) - and score the 2026-10-14 failure modes:
  repeats            cards shown again that the couple did not ask about by name (and replies that call a
                     repeat "new" / "haven't seen")
  violations         cards that break a constraint the couple stated (case "checks")
  cards per turn, reply length, peak-Saturday caveats, "open <venue>'s PDF" lines

SPENDS MONEY (~$0.04-0.08 per typed question; "Show 3 more" is free). Profiles are profile fields only (no
personal data), detail "full" (every case was a paying member at the time).
  python evals/replay_real.py [case ...]          e.g.  python evals/replay_real.py c33 c42
REC_CODE_DIR=<path to another rec_service checkout> replays that code instead (before/after comparisons);
the harness only uses functions that exist on both sides."""
import json
import os
import pathlib
import re
import sys

CODE = pathlib.Path(os.environ.get("REC_CODE_DIR") or pathlib.Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evals"))
import run_evals  # noqa: E402,F401  (loads secrets + tokens the same way, never prints them)
import agent  # noqa: E402
import app  # noqa: E402
import guards  # noqa: E402
import xano  # noqa: E402
from chips import dedupe_chips, ensure_chips, more_chips  # noqa: E402

MORE = "Show 3 more"
NYC_ZIP = re.compile(r"\b1(0[0-4]|1[0-4]|16)\d\d\b")      # Manhattan, Bronx, Staten Island, Brooklyn, Queens
MANHATTAN_ZIP = re.compile(r"\b10[0-2]\d\d\b")

CASES = {
    # chat 33 (10-07): rental cap under $11,000 ignored on later "more" turns; repeats called "you haven't seen".
    "c33": {"profile": {}, "asks": [
        "Looking for a romantic wedding venue for a party of 30-40 in california with a venue fee of less than $11000",
        "show me some other options please", "ok more options", "more options", "more"],
        "checks": [{"from": 0, "max_rental": 11000}]},
    # chat 42 (10-09): asked for New York City, then Manhattan; "Show 3 more" returned upstate venues.
    "c42": {"profile": {"Wedding_Location_Updated": ["New York"], "Planning_Phase": "Not engaged... yet"}, "asks": [
        "show me some options for a 60 person wedding", MORE,
        "what about for 100 guests. i want it to be in new york city", MORE, MORE,
        "what about restaurant venues within manhattan?", MORE, "Cheaper restaurant options"],
        "checks": [{"from": 2, "address": "nyc"}, {"from": 5, "address": "manhattan"}]},
    # chat 29 (10-04, user 31797): SF Zoo re-shown for "Cheaper options".
    "c29": {"profile": {"Wedding_Location_Updated": ["California", "Hawaii"], "Wedding_Budget": 10000,
                        "Planning_Phase": "Engaged and just started planning", "Age_Range": "40+"},
            "asks": ["Elopement with only 6 people", "Somewhere beautiful", "Cheaper options", "Big Sur?"],
            "checks": [{"from": 0, "min_capacity": 6}]},
    # chat 64 (10-13): Fonthill Castle re-shown as "all-inclusive estates you haven't seen yet".
    "c64": {"profile": {"Wedding_Location_Updated": ["Pennsylvania", "New Jersey"], "Wedding_Guest_Count": 175,
                        "Wedding_Budget": 90000, "Planning_Phase": "I'm engaged and just started wedding planning"},
            "opening": True, "asks": [MORE, MORE, "Try a different style", "All-inclusive venues", "more venues please"],
            "checks": [{"from": 0, "min_capacity": 175}]},
    # chat 62 (10-13): good chat - breakdown + budget answers. Regression check.
    "c62": {"profile": {"Wedding_Location_Updated": ["Rhode Island", "Pennsylvania", "Massachusetts", "Connecticut"],
                        "Wedding_Guest_Count": 160, "Wedding_Budget": 60000},
            "opening": True, "asks": [MORE, "What cheaper venues would you recommend?",
                                      "Break down the full cost of your top pick",
                                      "How much should I budget for a 160 person wedding in the northeast ?"],
            "checks": [{"from": 0, "min_capacity": 160}]},
    # chat 26 (10-03): good chat - a venue asked for by name. Regression check.
    "c26": {"profile": {"Wedding_Location_Updated": ["New York"], "Wedding_Guest_Count": 125, "Wedding_Budget": 125000,
                        "Wedding_Date": "2027-11-13", "Planning_Phase": "I'm engaged and just started wedding planning"},
            "opening": True, "asks": ["yale club nyc -"], "checks": []},
}


def _turn(role, text, out=None):
    """A stored turn exactly as xano.get_messages hands it back."""
    out = out or {}
    if hasattr(xano, "parse_message_row"):
        cards = app._stored_cards(out) if role == "assistant" and "more_cards" in out else (out.get("cards") or [])
        return xano.parse_message_row({"role": role, "content": text, "cards": cards, "chips": out.get("chips") or []})
    allc = (list(out.get("cards") or []) + [dict(c, more=True) for c in (out.get("more_cards") or [])])
    strip = lambda c: {k: v for k, v in c.items() if k not in ("more", "via_more")}
    return {"role": role, "text": text, "cards": [strip(c) for c in allc if not c.get("more")],
            "more_cards": [strip(c) for c in allc if c.get("more")],
            "is_more": any(c.get("via_more") for c in allc), "chips": out.get("chips") or []}


def _typed(token, profile, history, text):
    if hasattr(app, "_model_inputs"):
        msgs, kw = app._model_inputs(history, text)
    else:   # 54f2d14 (live 2026-10-14): the body of _refine_in_chat
        msgs = [{"role": "user", "content": app.OPENING_ASK}] if history and history[0]["role"] == "assistant" else []
        msgs += [app._turn_for_model(t) for t in history]
        msgs = msgs[-(app.HISTORY_TURNS - 1):] if len(msgs) >= app.HISTORY_TURNS else msgs
        if msgs and msgs[0]["role"] != "user":
            msgs = msgs[1:]
        msgs.append({"role": "user", "content": text})
        prior = [c.get("vendor_id") for t in history if t["role"] == "assistant"
                 for c in (t.get("cards") or []) + (t.get("more_cards") or [])]
        kw = {"prior_vendor_ids": [v for v in prior if v]}
    res, usage = agent.run(token, {"id": 0}, True, msgs, profile_override=profile, detail="full", **kw)
    res["chips"] = ensure_chips(dedupe_chips(res.get("chips"), history))
    return res, usage


def _more(history):
    if hasattr(app, "_more_reply"):
        r = app._more_reply(history, access=True)
        if r is None:
            return None
        return {"text": r["text"], "cards": [dict(c, via_more=True) for c in r["cards"]], "chips": r["chips"]}
    remaining = app._more_remaining(history)
    if len(remaining) < agent.MORE_STEP:
        return None
    return {"text": app.MORE_TEXT, "cards": [dict(c, via_more=True) for c in remaining[:agent.MORE_STEP]],
            "chips": more_chips(history)}


def run_case(token, name):
    case = CASES[name]
    history, log, cost = [], [], 0.0
    if case.get("opening"):
        res, usage = agent.run(token, {"id": 0}, True, [{"role": "user", "content": app.OPENING_ASK}],
                               profile_override=case["profile"], detail="full")
        cost += guards.cost_usd(usage["model"], usage)
        history.append(_turn("assistant", res["text"], res))
        log.append({"ask": "(opening)", "kind": "opening", "idx": -1})
    for i, ask in enumerate(case["asks"]):
        if ask == MORE:
            r = _more(history)
            if r is None:
                log.append({"ask": ask, "kind": "more", "idx": i, "no_more": True})
                continue
            history += [dict(_turn("user", MORE), ask_idx=i), _turn("assistant", r["text"], r)]
            log.append({"ask": ask, "kind": "more", "idx": i})
            continue
        res, usage = _typed(token, case["profile"], history, ask)
        cost += guards.cost_usd(usage["model"], usage)
        history += [dict(_turn("user", ask), ask_idx=i), _turn("assistant", res["text"], res)]
        log.append({"ask": ask, "kind": "typed", "idx": i, "tool_calls": usage["tool_calls"],
                    "latency_s": round(usage["latency_ms"] / 1000, 1)})
    return score(case, history, log), history, round(cost, 3)


_NEW_CLAIM = re.compile(r"\bnew\b|haven'?t seen|not seen yet|fresh|you haven'?t", re.I)
_PEAK = re.compile(r"peak[- ]season saturday|peak saturday|peak-saturday", re.I)
_OPEN_PDF = re.compile(r"\bopen\b[^.]{0,80}\bpdf\b", re.I)
_STOP = {"the", "and", "venue", "venues", "hotel", "house", "club", "estate", "mansion", "gardens", "garden",
         "weddings", "wedding", "events", "event", "center", "inn", "farm", "farms", "winery", "park", "at", "of"}


def _named(ask, name):
    words = [w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 3 and w not in _STOP]
    return any(w in (ask or "").lower() for w in words)


def _rental(c):
    r = c.get("venue_fee_range") or []
    vals = [x for x in r if isinstance(x, (int, float)) and x > 0]
    return min(vals) if vals else None


def score(case, history, log):
    """Per assistant turn: cards, repeats, violations; totals at the end."""
    seen, rows, ask_i = set(), [], -1
    checks = case.get("checks") or []
    asks_so_far = []
    turn_ask = None
    for t in history:
        if t["role"] == "user":
            turn_ask = t["text"]
            asks_so_far.append(turn_ask)
            ask_i = t.get("ask_idx", len(asks_so_far) - 1)
            continue
        ids = [c.get("vendor_id") for c in t["cards"]]
        rep = [c.get("name") for c in t["cards"] if c.get("vendor_id") in seen and not _named(turn_ask, c.get("name"))]
        viol = []
        for ck in checks:
            if ask_i < ck["from"] or (turn_ask is None and ck["from"] > 0):
                continue
            for c in t["cards"]:
                if "max_rental" in ck and (_rental(c) or 0) > ck["max_rental"]:
                    viol.append("%s rental %s" % (c.get("name"), _rental(c)))
                if ck.get("address") == "nyc" and not NYC_ZIP.search(c.get("address") or ""):
                    viol.append("%s not NYC (%s)" % (c.get("name"), c.get("address")))
                if ck.get("address") == "manhattan" and not MANHATTAN_ZIP.search(c.get("address") or ""):
                    viol.append("%s not Manhattan (%s)" % (c.get("name"), c.get("address")))
                if "min_capacity" in ck and isinstance(c.get("max_capacity_seated"), (int, float)) \
                        and 0 < c["max_capacity_seated"] < ck["min_capacity"]:
                    viol.append("%s seats %s" % (c.get("name"), c["max_capacity_seated"]))
        rows.append({"ask": (turn_ask or "(opening)")[:60], "is_more": t.get("is_more", False),
                     "cards": len(ids), "words": len((t["text"] or "").split()), "repeats": rep,
                     "new_claim_on_repeat": bool(rep and _NEW_CLAIM.search(t["text"] or "")),
                     "violations": viol, "peak": bool(_PEAK.search(t["text"] or "")),
                     "open_pdf": bool(_OPEN_PDF.search(t["text"] or "")), "text": t["text"],
                     "names": [c.get("name") for c in t["cards"]], "more_available": len(t.get("more_cards") or [])})
        seen.update(ids)
    typed = [r for r in rows if not r["is_more"]]
    return {"turns": rows, "totals": {
        "typed_turns": len(typed), "more_turns": len(rows) - len(typed),
        "repeats": sum(len(r["repeats"]) for r in rows),
        "new_claim_on_repeat": sum(r["new_claim_on_repeat"] for r in rows),
        "violations": sum(len(r["violations"]) for r in rows),
        "cards_per_typed_turn": round(sum(r["cards"] for r in typed) / max(1, len(typed)), 2),
        "words_per_typed_turn": round(sum(r["words"] for r in typed) / max(1, len(typed)), 1),
        "peak_caveats": sum(r["peak"] for r in rows), "open_pdf_lines": sum(r["open_pdf"] for r in typed)}}


if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if a in CASES] or list(CASES)
    tag = os.environ.get("REPLAY_TAG", "after")
    out, total = {}, 0.0
    for n in names:
        sc, hist, cost = run_case(os.environ["REC_TEST_TOKEN"], n)
        total += cost
        out[n] = {"cost": cost, **sc}
        print(n, "cost $%.3f" % cost, json.dumps(sc["totals"]))
        for r in sc["turns"]:
            print("   %-40s cards=%d words=%d rep=%s viol=%s" % (r["ask"][:40], r["cards"], r["words"], r["repeats"],
                                                               r["violations"][:3]))
    print("TOTAL $%.3f" % total)
    dest = pathlib.Path(os.environ.get("REPLAY_OUT") or (pathlib.Path(__file__).parent / ("results_replay_real_%s.json" % tag)))
    dest.write_text(json.dumps(out, indent=1, default=str))
