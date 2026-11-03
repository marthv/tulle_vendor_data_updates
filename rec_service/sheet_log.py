"""Planner activity -> a Google Sheet the team reads and categorizes (owner 2026-11-03).

One row per thing a couple does in the Planner: a typed question, a tapped suggested reply, the Planner's first
message, Show more, a rating, a thumbs up/down, a "still looking for" note, an opened pricing PDF, a vendor click, a
save. Columns (the Apps Script in planner_sheet.gs owns the header and the Category dropdown):

    Time (ET) | User | User id | Plan at the time | Chat # | Action | What they did or said | Tulle's answer |
    Vendors shown | Filters used | Flag | (Category, Notes: left blank for the team) | Device | Browser

Device + Browser (owner 2026-11-03) come from the request's User-Agent: app.py's middleware puts it in UA (a
contextvar) for the length of each request, event() copies it into the queued row. They sit after Notes so no
column the team already fills moves; the Apps Script writes row[11:13] into columns N:O.

How: event() only puts a dict on a queue - nothing on the request path waits on Google. One worker thread looks up
the plan (table 16, the same plan_label the Slack notices use), drops internal accounts, and POSTs batches to the
sheet's Apps Script web app every few seconds (one retry; a failed batch is printed, never raised). Off unless
REC_SHEET_WEBHOOK_URL is set. REC_SHEET_SECRET must match the SECRET script property, so the URL alone can't write.
"""
import contextvars
import datetime as dt
import json
import os
import queue
import re
import threading
import time

import requests

import xano

URL = os.environ.get("REC_SHEET_WEBHOOK_URL", "").strip()
SECRET = os.environ.get("REC_SHEET_SECRET", "").strip()
BATCH_S = float(os.environ.get("REC_SHEET_BATCH_S", "5"))
ANSWER_CHARS = 1500

# Internal + test accounts (feedback_exclude_internal_accounts): never in the team's sheet.
# 31619 (soccerdog333@gmail.com) removed 2026-11-03: owner tests the sheet with it.
INTERNAL_IDS = {14998, 30614, 32636, 118, 5622}

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:            # no tz database: UTC, labelled as such
    _ET = None

# The current request's User-Agent (set by app.py's middleware; background tasks run inside the same request).
UA = contextvars.ContextVar("sheet_log_ua", default="")

_Q = queue.Queue(maxsize=5000)
_started = False
_lock = threading.Lock()


def enabled():
    return bool(URL)


def internal(user):
    try:
        if int(user.get("id") or 0) in INTERNAL_IDS:
            return True
    except (TypeError, ValueError):
        pass
    email = str(user.get("email") or "").lower()
    return email.startswith("vivek.marthi") or email.endswith("@tulletogether.com")


def _vendors(cards):
    names = []
    for c in cards or []:
        if isinstance(c, dict) and c.get("name"):
            names.append(str(c["name"]))
    return ", ".join(names[:8])


def _filters(f):
    """search_filters -> 'states: Michigan · vibes: Scenic / Nature Views · guests 125'."""
    if not isinstance(f, dict):
        return ""
    parts = []
    for k, v in f.items():
        if v in (None, "", [], {}, 0, False):
            continue
        label = k.replace("_", " ")
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v)
        parts.append("%s: %s" % (label, v))
    return " · ".join(parts)[:500]


def event(user, action, did="", answer="", chat_id=0, cards=None, filters=None, flag="", latency_ms=None):
    """Queue one row. Never raises, never blocks."""
    if not URL or not user:
        return
    try:
        if internal(user):
            return
        if latency_ms and latency_ms > 20000:
            flag = (flag + "; " if flag else "") + "slow (%d s)" % round(latency_ms / 1000)
        _Q.put_nowait({"at": time.time(), "user": dict(user), "action": action, "did": str(did or "")[:2000],
                       "answer": str(answer or "")[:ANSWER_CHARS], "chat_id": int(chat_id or 0),
                       "vendors": _vendors(cards), "filters": _filters(filters), "flag": flag,
                       "ua": UA.get()})
        _ensure_worker()
    except Exception as e:
        print("sheet_log.event skipped:", repr(e)[:200])


def _ver(ua, pat):
    m = re.search(pat, ua)
    return m.group(1) if m else ""


def device_of(ua):
    """'iPhone' | 'iPad' | 'Android phone' | 'Android tablet' | 'Mac' | 'Windows PC' | 'Chromebook' | 'Linux PC'.
    An iPad on iPadOS 13+ in desktop mode sends a Mac User-Agent, so it reads as 'Mac'."""
    if not ua:
        return ""
    if "iPhone" in ua or "iPod" in ua:
        return "iPhone"
    if "iPad" in ua:
        return "iPad"
    if "Android" in ua:
        return "Android phone" if "Mobile" in ua else "Android tablet"
    if "CrOS" in ua:
        return "Chromebook"
    if "Macintosh" in ua or "Mac OS X" in ua:
        return "Mac"
    if "Windows" in ua:
        return "Windows PC"
    if "Linux" in ua:
        return "Linux PC"
    return "other"


def browser_of(ua):
    """Name + major version. In-app browsers (Instagram, Facebook, TikTok, Pinterest...) first: they matter for
    bugs (popups, downloads) and their UA also says Safari/Chrome."""
    if not ua:
        return ""
    for name, pat in (("Instagram in-app", r"Instagram (\d+)"), ("Facebook in-app", r"FBAV/(\d+)"),
                      ("TikTok in-app", r"(?:musical_ly|BytedanceWebview|TikTok)[_/ ]?(\d*)"),
                      ("Pinterest in-app", r"Pinterest(?:/| for iOS/)?(\d*)"), ("Snapchat in-app", r"Snapchat/(\d+)"),
                      ("Edge", r"Edg(?:e|A|iOS)?/(\d+)"), ("Samsung Internet", r"SamsungBrowser/(\d+)"),
                      ("Opera", r"(?:OPR|OPiOS)/(\d+)"), ("Firefox", r"(?:Firefox|FxiOS)/(\d+)"),
                      ("Chrome", r"(?:CriOS|Chrome)/(\d+)"), ("Safari", r"Version/(\d+)[.\d]* (?:Mobile/\S+ )?Safari")):
        if re.search(pat, ua):
            v = _ver(ua, pat)
            return (name + " " + v).strip()
    if "AppleWebKit" in ua and ("iPhone" in ua or "iPad" in ua):
        return "iOS in-app browser"
    return "other"


def flag_of(out):
    """The answer's outcome, in the team's words."""
    if not isinstance(out, dict):
        return ""
    lk = out.get("lookup") or {}
    if lk.get("status") == "not_found":
        return "not in Tulle"
    if out.get("error"):
        return "error"
    if out.get("stopped"):
        return "stopped by the couple"
    if not out.get("cards") and not out.get("widgets"):
        return "no vendors shown"
    return ""


def _when(ts):
    t = dt.datetime.fromtimestamp(ts, _ET) if _ET else dt.datetime.utcfromtimestamp(ts)
    return t.strftime("%Y-%m-%d %H:%M") + ("" if _ET else " UTC")


_PLAN_CACHE = {}   # user id -> (plan, fetched_at); a plan changes rarely, the lookup is a table-16 search


def _plan(user):
    uid = user.get("id")
    hit = _PLAN_CACHE.get(uid)
    if hit and time.time() - hit[1] < 600:
        return hit[0]
    try:
        p = xano.plan_label(user)
    except Exception:
        p = "unknown"
    _PLAN_CACHE[uid] = (p, time.time())
    return p


def _row(e):
    u = e["user"]
    who = str(u.get("email") or "").strip() or str(u.get("name") or "")
    return [_when(e["at"]), who, u.get("id"), _plan(u), e["chat_id"] or "", e["action"], e["did"], e["answer"],
            e["vendors"], e["filters"], e["flag"], device_of(e.get("ua", "")), browser_of(e.get("ua", ""))]


def _post(rows):
    body = json.dumps({"secret": SECRET, "rows": rows})
    for attempt in (1, 2):
        try:
            r = requests.post(URL, data=body, headers={"Content-Type": "application/json"}, timeout=30,
                              allow_redirects=True)
            if r.status_code < 300 and '"ok":true' in r.text.replace(" ", ""):
                return True
            print("sheet_log post %d: HTTP %s %s" % (attempt, r.status_code, r.text[:200]))
        except Exception as ex:
            print("sheet_log post %d failed: %s" % (attempt, repr(ex)[:200]))
        time.sleep(2)
    return False


def _worker():
    while True:
        batch = [_Q.get()]
        deadline = time.time() + BATCH_S
        while len(batch) < 200:
            left = deadline - time.time()
            if left <= 0:
                break
            try:
                batch.append(_Q.get(timeout=left))
            except queue.Empty:
                break
        try:
            rows = [_row(e) for e in batch]
            if not _post(rows):
                print("sheet_log dropped %d rows" % len(rows))
        except Exception as ex:
            print("sheet_log batch failed:", repr(ex)[:200])


def _ensure_worker():
    global _started
    if _started:
        return
    with _lock:
        if not _started:
            threading.Thread(target=_worker, name="sheet_log", daemon=True).start()
            _started = True
