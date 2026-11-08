"""'My wedding' plan (owner 2026-11-07): the couple adds vendors from Planner cards into the slots of their wedding,
and the Planner builds on it. Free for everyone, every couple (owner 2026-11-07: no A/B). arm() stays so a split can
come back with REC_PLAN_AB_PCT. Storage: Xano table 90 rec_wedding_plan.

Pure helpers here (arm, slots, summary, next step); xano.py does the reads and writes; app.py the endpoints."""
import hashlib
import os

# 2026-11-07 (owner): everyone gets the plan - no A/B. Kept as a dial: 0 = off for everyone, 1-99 = a split again.
PLAN_AB_PCT = int(os.environ.get("REC_PLAN_AB_PCT", "100"))
PLAN_AB_SALT = os.environ.get("REC_PLAN_AB_SALT", "plan-2026-11")  # change to re-randomize a new test
# Internal accounts always see the plan (so the team can look at it); they are excluded from every read anyway.
ALWAYS_PLAN = {14998, 30614, 32636, 31619, 118, 5622}

# Slots in the order a wedding usually gets booked. key = the vendor category the cards carry.
SLOTS = [
    ("Venue", "Venue"),
    ("Photography", "Photographer"),
    ("Food and Beverage", "Catering"),
    ("Entertainment", "Music"),
    ("Flowers", "Flowers"),
    ("Wedding Planner", "Planner"),
    ("Beauty", "Hair & makeup"),
]
SLOT_KEYS = [k for k, _ in SLOTS]
NEXT_ASK = {"Venue": "Find venues for our wedding", "Photography": "Find a photographer for our wedding",
            "Food and Beverage": "Find caterers for our wedding", "Entertainment": "Find a DJ or band for our wedding",
            "Flowers": "Find a florist for our wedding", "Wedding Planner": "Find a wedding planner",
            "Beauty": "Find hair and makeup for our wedding"}


def arm(user_id):
    """'plan' or 'control' - stable per couple."""
    try:
        uid = int(user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid in ALWAYS_PLAN:
        return "plan"
    if PLAN_AB_PCT <= 0 or not uid:
        return "control"
    if PLAN_AB_PCT >= 100:
        return "plan"
    h = int(hashlib.sha256(("%s:%d" % (PLAN_AB_SALT, uid)).encode()).hexdigest()[:8], 16) % 100
    return "plan" if h < PLAN_AB_PCT else "control"


def slot_of(category):
    c = str(category or "").strip()
    return c if c in SLOT_KEYS else ("Venue" if not c else "Other")


def summary(items, budget=0, show_prices=True):
    """The plan as the page shows it: every slot (filled or open) in booking order, the running total of known
    estimates and the couple's own budget (never a per-category target - feedback_no_imposed_budgets)."""
    items = [i for i in items or [] if (i.get("status") or "active") == "active"]
    by = {}
    for i in items:
        by.setdefault(slot_of(i.get("slot")), []).append(i)
    slots = []
    for key, label in SLOTS + ([("Other", "Other")] if by.get("Other") else []):
        rows = sorted(by.get(key, []), key=lambda r: r.get("created_at") or 0)
        slots.append({"slot": key, "label": label, "items": [_public(r, show_prices) for r in rows]})
    total = sum(int(i.get("est_cost") or 0) for i in items)
    return {"slots": slots, "filled": sum(1 for s in slots if s["items"]), "of": len(SLOTS),
            "count": len(items), "total_est": total if show_prices and total else None,
            "budget": int(budget or 0) or None}


def _public(r, show_prices):
    return {"id": r.get("id"), "vendor_id": r.get("vendor_id") or "", "name": r.get("vendor_name") or "",
            "est_cost": (int(r.get("est_cost") or 0) or None) if show_prices else None, "note": r.get("note") or ""}


def next_slot(items):
    """The first open slot in booking order, or '' when every slot has something."""
    have = {slot_of(i.get("slot")) for i in items or [] if (i.get("status") or "active") == "active"}
    return next((k for k in SLOT_KEYS if k not in have), "")


def next_step(items, questions_asked):
    """{'slot', 'label', 'ask'} - the one suggestion under an answer once the couple is engaged (owner: "when they ask
    more, start building their wedding"): something already in the plan, or 3+ questions asked. None otherwise."""
    active = [i for i in items or [] if (i.get("status") or "active") == "active"]
    if not active and questions_asked < 3:
        return None
    s = next_slot(active)
    if not s:
        return None
    label = dict(SLOTS)[s]
    return {"slot": s, "label": label, "ask": NEXT_ASK[s]}


def context_line(items, show_prices=True):
    """For the model: what is already in the couple's wedding (so it builds on it and never re-suggests a slot)."""
    active = [i for i in items or [] if (i.get("status") or "active") == "active"]
    if not active:
        return ""
    parts = []
    for key, label in SLOTS:
        names = [i.get("vendor_name") or "?" for i in active if slot_of(i.get("slot")) == key]
        parts.append("%s: %s" % (label, ", ".join(names) if names else "open"))
    return ("\nTHEIR WEDDING SO FAR (the couple added these in the Planner): " + "; ".join(parts)
            + ". Build on it: when they ask broadly, help with the next open slot; never re-suggest a slot as empty "
              "when it is filled.")
