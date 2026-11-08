"""'My wedding' (owner 2026-11-08): one screen - budget left to spend, the four categories Tulle covers well (venue,
photographer, music, flowers) with the couple's saved vendors and the one they booked, and "+ Add a cost" for the
rest. Built from the prototype the owner approved (one screen, no tabs, everything opens in place).

Data, no schema change:
  saved   = user.favorited_vendors (the same heart as Discover / vendor pages / the Planner), cards from table 11
  booked  = the couple's budget items, Xano table 33 "Packages" (the Budget tab's table): rows with Selected = true,
            the seeded "Example ..." rows left out. Mark booked = a new row (Selected true, Notes "tulle:<vendor_id>").
            Undo = Selected false (never a delete - nothing a couple entered is destroyed).
  budget  = user.Wedding_Budget.
Rollback: REC_WEDDING=0 (endpoints answer enabled:false and the page hides the view). Pure helpers here; xano.py does
the reads/writes; app.py the endpoints."""
import os
import re

ENABLED = os.environ.get("REC_WEDDING", "1") == "1"
PACKAGES_TABLE = 33
XANO_FILE_BASE = os.environ.get("XANO_FILE_BASE", "https://xqtb-2ma7-ijfy.n7e.xano.io")

# The categories Tulle covers well (11-07 counts: venues 13k, DJs/bands 3k, photographers 2.4k, florists 734).
# key = the card category; label = what the couple sees; table = table 33's Category value (table 19 names).
CATS = [
    {"key": "Venue", "label": "Venue", "icon": "🏛", "noun": "venues", "table": "Venue"},
    {"key": "Photography", "label": "Photographer", "icon": "📷", "noun": "photographers", "table": "Photography"},
    {"key": "Entertainment", "label": "Music", "icon": "🎵", "noun": "DJs and bands", "table": "Entertainment"},
    {"key": "Flowers", "label": "Flowers", "icon": "💐", "noun": "florists", "table": "Flowers"},
]
BY_TABLE = {c["table"].lower(): c for c in CATS}


def is_example(p):
    return str(p.get("vendor_name") or "").strip().lower().startswith("example")


def booked_rows(packages):
    return [p for p in packages or [] if p.get("Selected") is True and not is_example(p)]


def _num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


_MARK = re.compile(r"tulle:(\S+)")


def _vid(p):
    """The vendor a booked row came from. 11-08 v2: the marker lives in Package_Name ("Booked in My wedding ·
    tulle:V1") so Notes is the couple's own; rows from v1 still carry it in Notes."""
    for f in ("Package_Name", "Notes"):
        m = _MARK.search(str(p.get(f) or ""))
        if m:
            return "" if m.group(1) == "manual" else m.group(1)
    return ""


def user_notes(p):
    """The couple's own notes - never the v1 "tulle:..." marker."""
    n = str(p.get("Notes") or "").strip()
    return "" if _MARK.fullmatch(n) else n


def file_of(p):
    """Table 33 Files (single attachment) as {name, url}, or None."""
    f = p.get("Files")
    if not isinstance(f, dict) or not (f.get("url") or f.get("path")):
        return None
    url = f.get("url") or ""
    if not url and f.get("path"):
        url = XANO_FILE_BASE + f["path"]
    return {"name": f.get("name") or "File", "url": url, "mime": f.get("mime") or ""}


def clean_attachment(f):
    """An attachment object from the app's POST /upload/attachment, safe to write to Files: a Xano vault path, our
    own host. Anything else -> None (never store a client-chosen URL)."""
    if not isinstance(f, dict):
        return None
    path = str(f.get("path") or "")
    # Xano vault folder names can END in ".." (e.g. /vault/x/y/bgPB5g../qa.pdf), so only a whole ".." segment is traversal.
    if not path.startswith("/vault/") or ".." in path.split("/"):
        return None
    out = {k: f.get(k) for k in ("access", "path", "name", "type", "size", "mime", "meta") if f.get(k) is not None}
    out["access"] = "public"
    out["name"] = str(out.get("name") or "file")[:200]
    return out


def price_hint(card):
    """A saved vendor's 'about $X' from its card (paid access only): venue rental low end, or P/E package low end."""
    if not card:
        return None
    for k in ("price_range", "venue_fee_range"):
        r = card.get(k)
        if isinstance(r, list) and r and _num(r[0]) > 0:
            return int(_num(r[0]))
    return None


def view(user, packages, saved_cards, show_prices):
    """The whole screen as the page draws it."""
    budget = int(_num(user.get("Wedding_Budget"))) or None
    rows = sorted(booked_rows(packages), key=lambda p: (p.get("created_at") or 0, p.get("id") or 0))
    booked_by, extras = {}, []
    for p in rows:
        c = BY_TABLE.get(str(p.get("Category") or "").strip().lower())
        item = {"id": p.get("id"), "name": p.get("vendor_name") or "", "price": int(_num(p.get("total_cost"))),
                "paid": int(_num(p.get("Balance_Paid"))), "vendor_id": _vid(p), "category": p.get("Category") or "",
                "notes": user_notes(p)[:2000], "file": file_of(p)}
        if c and c["key"] not in booked_by:
            booked_by[c["key"]] = item
        else:
            extras.append(item)
    booked_total = sum(int(_num(p.get("total_cost"))) for p in rows)
    # Pie (owner 11-08: "a pie chart rather than a line, so we can see how much is being used up"): booked money by
    # category, biggest first; the page adds the "left" slice from budget.
    by_cat = {}
    for p in rows:
        c = BY_TABLE.get(str(p.get("Category") or "").strip().lower())
        lab = c["label"] if c else (str(p.get("Category") or "").strip() or "Other")
        by_cat[lab] = by_cat.get(lab, 0) + int(_num(p.get("total_cost")))
    slices = [{"label": k, "amount": v} for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]) if v > 0]
    cats = []
    saved_other = []
    keys = {c["key"] for c in CATS}
    for card in saved_cards or []:
        if card and card.get("category") not in keys:
            saved_other.append({"vendor_id": card.get("vendor_id"), "name": card.get("name"),
                                "category": card.get("category")})
    for c in CATS:
        saved = []
        for card in saved_cards or []:
            if card and (card.get("category") or "Venue") == c["key"]:
                saved.append({"vendor_id": card.get("vendor_id"), "name": card.get("name") or "",
                              "sub": _sub(card), "price": price_hint(card) if show_prices else None})
        cats.append({"key": c["key"], "label": c["label"], "icon": c["icon"], "noun": c["noun"],
                     "booked": booked_by.get(c["key"]), "saved": saved})
    return {"enabled": ENABLED, "budget": budget, "booked_total": booked_total,
            "left": (budget - booked_total) if budget else None, "categories": cats, "extras": extras, "slices": slices,
            "paid_total": sum(int(_num(p.get("Balance_Paid"))) for p in rows),
            "saved_other": saved_other, "show_prices": bool(show_prices)}


def _sub(card):
    t = card.get("venue_type")
    t = t[0] if isinstance(t, list) and t else t
    st = card.get("state")
    st = st[0] if isinstance(st, list) and st else st
    return " · ".join(x for x in (str(t or "").strip(), str(st or "").strip()) if x)[:80]


def table_category(key_or_label):
    """Category value for a new table-33 row: one of ours, else the couple's own label (\"Catering\") or Other."""
    k = str(key_or_label or "").strip()
    for c in CATS:
        if k.lower() in (c["key"].lower(), c["label"].lower(), c["table"].lower()):
            return c["table"]
    return k[:60] or "Other"
