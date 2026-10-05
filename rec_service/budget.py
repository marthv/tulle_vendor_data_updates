"""Budget planner for the Tulle Assistant's Budget panel (user decision 2026-10-05). Deterministic - no AI
call - so every tweak costs ~$0 (Vivek: watch our costs at volume).

Rebuilds the hidden Cost Research "forecast calculator" (WeWeb page 34730a52) from its data, Xano table 19
(13 categories, one share column per budget focus, each summing to 100). The old page's logic is not reused:
it only rendered for logged-out users while its collections needed a token, wrote exclusions into the
shared table, and had a typo that broke Entertainment focus.

    allocate: each included category's share / sum of included shares x total  (sum stays == total)
    project:  max(entered, allocated) per category, plus RIPPLE - overspending category c adds
              overspend_c x RIPPLE[c][d] to each related category d the couple hasn't priced themselves
"""
import time

import config
import xano

BUDGET_TABLE_ID = 19
FOCUS_COLUMNS = {   # panel value -> table 19 column ("typical" = Budget_Focus_Normal; see open items)
    "typical": "Budget_Focus_Normal",
    "venue": "Budget_Focus_Venue",
    "food": "Budget_Focus_Food_Bev",
    "flowers": "Budget_Focus_Flowers",
    "planner": "Budget_Focus_Planner",
    "entertainment": "Budget_Focus_Entertainment",
}
FOCUS_LABELS = {"typical": "Typical", "venue": "Venue", "food": "Food & drink", "flowers": "Flowers",
                "planner": "Planner", "entertainment": "Entertainment"}
MAX_AMOUNT = 10_000_000
# Canvas can't read CSS vars, so Tulle tokens as hex (CLAUDE.md 5.1). No purple.
PALETTE = ["#00A368", "#0D5C3A", "#52D19A", "#0A3D24", "#A8EAC8", "#1DBD7E", "#D97706", "#52555C",
           "#7E8189", "#FEF3C7", "#0F7348", "#A8ABB3", "#D4F5E4"]

_cache = {"at": 0.0, "rows": None}


def load_shares(ttl=3600):
    """[{category, shares:{focus: pct}}] in table 19 id order. Cached: the table changes rarely."""
    if _cache["rows"] is None or time.time() - _cache["at"] > ttl:
        rows = sorted(xano._search_all(BUDGET_TABLE_ID, []), key=lambda r: int(r.get("id") or 0))
        _cache["rows"] = [{"category": r["Category"],
                           "shares": {f: float(r.get(col) or 0) for f, col in FOCUS_COLUMNS.items()}}
                          for r in rows if r.get("Category")]
        _cache["at"] = time.time()
    return _cache["rows"]


def clean_request(shares, focus, excluded, edits):
    """Validate panel input against the known categories. Raises ValueError with a short reason."""
    focus = (focus or "typical").lower()
    if focus not in FOCUS_COLUMNS:
        raise ValueError("unknown focus")
    names = {s["category"] for s in shares}
    excluded = sorted({e for e in (excluded or []) if e in names})
    if len(excluded) >= len(names):
        raise ValueError("keep at least one category")
    out = {}
    for cat, amount in (edits or {}).items():
        if cat not in names or cat in excluded or amount in (None, ""):
            continue
        a = float(amount)
        if not 0 <= a <= MAX_AMOUNT:
            raise ValueError("amount out of range")
        out[cat] = round(a)
    return focus, excluded, out


def allocate(shares, total, focus, excluded):
    """{category: dollars} for included categories, renormalised so they add up to total."""
    inc = [s for s in shares if s["category"] not in excluded]
    weight = sum(s["shares"][focus] for s in inc) or 1.0
    return {s["category"]: total * s["shares"][focus] / weight for s in inc}


def plan(shares, total, focus="typical", excluded=(), edits=None, ripple=None):
    """The full plan the panel draws and the assistant reads."""
    ripple = config.BUDGET_RIPPLE if ripple is None else ripple
    edits = edits or {}
    alloc = allocate(shares, total, focus, excluded)
    over = {c: edits[c] - alloc[c] for c in edits if c in alloc and edits[c] > alloc[c]}
    knock = {}
    for c, amount in over.items():
        for d, f in (ripple.get(c) or {}).items():
            if d in alloc and d not in edits:   # a category the couple priced themselves doesn't drift
                knock[d] = knock.get(d, 0.0) + amount * f
    rows = []
    for s in shares:
        c = s["category"]
        if c not in alloc:
            rows.append({"category": c, "excluded": True, "share_pct": 0, "allocated": 0, "entered": None,
                         "ripple": 0, "projected": 0})
            continue
        projected = max(edits.get(c, 0), alloc[c]) + knock.get(c, 0.0)
        rows.append({"category": c, "excluded": False, "share_pct": round(100 * alloc[c] / total, 1) if total else 0,
                     "allocated": round(alloc[c]), "entered": edits.get(c), "ripple": round(knock.get(c, 0.0)),
                     "projected": round(projected)})
    projected_total = sum(r["projected"] for r in rows)
    return {"total": round(total), "focus": focus, "focus_label": FOCUS_LABELS[focus],
            "excluded": list(excluded), "edits": edits, "rows": rows, "projected_total": projected_total,
            "over_by": max(0, projected_total - round(total)), "chart": donut(rows)}


def donut(rows):
    """Chart.js doughnut spec for WeWeb's chart element (projected dollars per included category)."""
    live = [r for r in rows if not r["excluded"] and r["projected"] > 0]
    return {"type": "doughnut",
            "data": {"labels": [r["category"] for r in live],
                     "datasets": [{"label": "Projected", "data": [r["projected"] for r in live],
                                   "backgroundColor": [PALETTE[i % len(PALETTE)] for i in range(len(live))],
                                   "borderWidth": 0}]},
            "options": {"cutout": "62%", "plugins": {"legend": {"position": "bottom"}}}}


def venue_line(p):
    """One context line for the assistant, e.g. so a breakdown can say 'this puts you $3k over'."""
    venue = next((r for r in p["rows"] if r["category"] == "Venue" and not r["excluded"]), None)
    fb = next((r for r in p["rows"] if r["category"] == "Food and Beverage" and not r["excluded"]), None)
    bits = ["focus %s" % p["focus_label"]]
    if venue:
        bits.append("venue share $%s" % format(venue["projected"], ","))
    if fb:
        bits.append("food and drink share $%s" % format(fb["projected"], ","))
    bits.append("projected total $%s of $%s" % (format(p["projected_total"], ","), format(p["total"], ",")))
    if p["excluded"]:
        bits.append("not using: " + ", ".join(p["excluded"]))
    return "The couple's saved budget plan: " + "; ".join(bits) + "."
