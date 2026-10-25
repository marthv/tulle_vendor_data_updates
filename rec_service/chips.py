"""Follow-up chip post-processing (pure - no app/config imports, so tests can load it alone)."""
import re


# Always answerable (each maps to a search filter - see the CHIPS rule); used only to top up after de-dupe.
CHIP_TOPUPS = ["Cheaper options", "All-inclusive venues", "Bring-your-own-caterer venues",
               "Outdoor ceremony space", "Waterfront venues", "Historic Architecture venues"]


# Starter prompts on the OPENING reply (user 2026-10-07). 7 of 14 real users never asked anything after the
# opening, so the first reply offers one chip per bucket real users asked about (10-03..10-07, 28 messages):
# more/cheaper 32%, change criteria 25%, style 21%, a named venue 14%. Fixed wording, not the model's: each is
# answerable (search filter / profile / venue lookup) and reads as a full question when tapped.
STARTER_CHIPS = ["Show me cheaper options", "Try a different style", "Is a venue I like realistic?",
                 "Change my guests or budget"]


def chip_key(c):
    return re.sub(r"[^a-z0-9 ]", "", (c or "").lower()).strip()


def more_chips(history):
    """Chips for a "Show 3 more" reply (user 2026-10-08: "there should always be a follow up chip" - it had none).
    The extra venues come from the latest answer's own searches, so that answer's chips still fit: carry them
    forward. No chips there -> the always-answerable top-ups."""
    for t in reversed(history or []):
        if t.get("role") == "assistant" and t.get("chips"):
            return list(t["chips"])[:4]
    return list(CHIP_TOPUPS[:2])


def ensure_chips(chips):
    """Last line of defence: never send a reply with no follow-up chip."""
    return list(chips) if chips else list(CHIP_TOPUPS[:2])


def dedupe_chips(chips, history):
    """Drop chips already offered earlier in this chat. MEASURED 2026-10-04: telling the model "don't
    repeat a chip" and showing it the earlier chips changed nothing (12 repeats in user 31797's real chat,
    12 and 11 in two replays), so it's enforced here. Tops up to 2 from CHIP_TOPUPS; if even that runs
    out, pads with the model's own (repeated) chips rather than show fewer than 2. ACTION chips (below) may
    repeat across replies (2026-10-25) - place_action_chip only stops one showing twice in a row."""
    offered = {chip_key(c) for t in history if t.get("role") == "assistant" for c in (t.get("chips") or [])}
    fresh, keys = [], set()
    for c in chips or []:
        k = chip_key(c)
        if k and (k not in offered or action_label(c)) and k not in keys:
            fresh.append(c)
            keys.add(k)
    for c in CHIP_TOPUPS:
        if len(fresh) >= 2:
            break
        if chip_key(c) not in offered and chip_key(c) not in keys:
            fresh.append(c)
            keys.add(chip_key(c))
    for c in chips or []:   # top-ups ran out too: pad with the fewest repeats rather than show none
        if len(fresh) >= 2:
            break
        if chip_key(c) not in keys:
            fresh.append(c)
            keys.add(chip_key(c))
    return fresh


# ---------------------------------------------------------------- next planning step (owner 2026-10-25)
# "Make sure the chips still exist to push them to the next step": follow-ups should move a couple to the next
# planning step (cost tools, their own saved venues / quotes / budget), not only to more searching.

# ACTION chips: the front end handles these WITHOUT calling the model (switches tab / opens the PDF-quote flow), so
# they never spend a free question. Label -> action id the page switches on. Exact labels - the page matches them.
ACTION_CHIPS = {"Open Cost tools →": "open_cost_tools", "Open my budget →": "open_budget",
                "Add a quote from a PDF →": "add_pdf_quote"}
_ACTION_BY_KEY = {chip_key(k): k for k in ACTION_CHIPS}

# Starter chips when REC_COST_TOOLS_IN_CHAT is on: the whole-wedding estimate replaces "Is a venue I like realistic?"
# (the estimate is the one cost tool every couple can use with only their profile - state + guests).
STARTER_CHIPS_COST_TOOLS = ["Show me cheaper options", "Try a different style", "What will our wedding cost?",
                            "Change my guests or budget"]

# Natural-language planning chips (sent back as questions - they go through the model and its cost tools).
PLANNING_CHIPS = ["Compare my saved venues", "What will our wedding cost?", "What will our wedding cost overall?",
                  "Save one to compare", "Split our budget by category", "Find venues within my venue budget",
                  "Find a photographer in my budget", "Find a photographer"]
_PLANNING_KEYS = {chip_key(c) for c in PLANNING_CHIPS}
_LEFT_FOR = re.compile(r"^what'?s left for [a-z &/-]+\??$", re.I)   # "What's left for photography?" (biggest unbooked)
# Planning chips that are a PRICE-filtered search: a free user's search tools drop price params, so these can't be
# answered on the free plan and free_mode.chips still strips them.
PRICE_FILTER_PLANNING = {"find venues within my venue budget", "find a photographer in my budget"}

# Next steps per cost widget type, best first. The code guarantee and /rec/tool use them; the prompt
# (agent.COST_TOOLS_RULES, NEXT PLANNING STEP) gives the model the same menu.
NEXT_STEPS = {
    "cost_estimate": ["Split our budget by category", "Compare my saved venues", "Open Cost tools →"],
    "budget_split": ["Find venues within my venue budget", "Find a photographer in my budget", "Open my budget →"],
    "venue_compare": ["Add a quote from a PDF →", "Open my budget →", "Find a photographer"],
    "budget_status": ["What's left for photography?", "Open my budget →"],
}
TOOL_WIDGET = {"estimate_cost": "cost_estimate", "split_budget": "budget_split", "compare_venues": "venue_compare",
               "budget_status": "budget_status"}
# What the code guarantee appends when a cost reply has no planning/action chip (owner spec 2026-10-25).
GUARANTEE_DEFAULT = {"cost_estimate": "Split our budget by category", "budget_split": "Find venues within my venue budget",
                     "venue_compare": "Add a quote from a PDF →", "budget_status": "Open my budget →"}
# Free plan: same steps minus the price-filtered searches, topped up with steps a free user can take.
FREE_FALLBACK_STEPS = ["Compare my saved venues", "What will our wedding cost?", "Open my budget →", "Find a photographer"]


# "What's left for <the biggest unbooked category>?" after budget_status: the first of these (Tulle's largest budget
# shares after venue + food) the couple has nothing added for yet; Photography when the widget is unknown.
UNBOOKED_ORDER = ["Photography", "Entertainment", "Flowers", "Wedding Planner", "Attire"]


def left_for_chip(widget=None):
    have = {(b.get("category") or "").strip().lower() for b in (widget or {}).get("by_category") or []
            if b.get("added")}
    cat = next((c for c in UNBOOKED_ORDER if c.lower() not in have), UNBOOKED_ORDER[0])
    return "What's left for %s?" % cat.lower()


def _steps(kind, widget=None):
    steps = list(NEXT_STEPS[kind])
    if kind == "budget_status":
        steps[0] = left_for_chip(widget)
    return steps


def action_label(c):
    """The canonical action-chip label for c (case / punctuation / a missing arrow ignored), else None."""
    return _ACTION_BY_KEY.get(chip_key(c))


def is_planning_chip(c):
    return chip_key(c) in _PLANNING_KEYS or bool(_LEFT_FOR.match((c or "").strip()))


def is_next_step(c):
    return bool(action_label(c)) or is_planning_chip(c)


def free_ok(c):
    """A planning / action chip the free-mode price guard must let through (market averages or the couple's own
    numbers - not vendor prices). Price-filtered searches are not."""
    return is_next_step(c) and chip_key(c) not in PRICE_FILTER_PLANNING


def _last_actions(history):
    """Action chips on the most recent assistant reply (an action chip never shows twice in a row)."""
    for t in reversed(list(history or [])):
        if t.get("role") == "assistant":
            return {action_label(c) for c in t.get("chips") or []} - {None}
    return set()


def _offered(history):
    return {chip_key(c) for t in history or () if t.get("role") == "assistant" for c in (t.get("chips") or [])}


def place_action_chip(chips, history=(), topups=None):
    """At most ONE action chip per reply, placed LAST, never the same action chip as the previous reply; 2-4 chips
    total. No action chip in the list -> returned unchanged (so the flag-off path is untouched)."""
    chips = list(chips or [])
    if not any(action_label(c) for c in chips):
        return chips
    blocked = _last_actions(history)
    rest, action, keys = [], None, set()
    for c in chips:
        a = action_label(c)
        if a:
            if action is None and a not in blocked:
                action = a
            continue
        if chip_key(c) and chip_key(c) not in keys:
            rest.append(c)
            keys.add(chip_key(c))
    n_act = 1 if action else 0
    rest = rest[:4 - n_act]
    offered = _offered(history)
    pool = list(topups if topups is not None else CHIP_TOPUPS)
    for strict in (True, False):   # unoffered top-ups first, then repeats rather than fewer than 2
        for c in pool:
            if len(rest) + n_act >= 2:
                break
            k = chip_key(c)
            if k not in keys and not action_label(c) and (not strict or k not in offered):
                rest.append(c)
                keys.add(k)
    return rest + ([action] if action else [])


def guarantee_next_step(chips, widgets, history=(), free=False):
    """CODE guarantee (don't rely on the prompt alone): a reply that carries a cost widget gets at least one
    planning or action chip. If none of its chips is one, append the next step for the LAST widget - the owner's
    default first, skipping an action chip the previous reply already showed and (free plan) price-filtered
    searches - keeping 2-4 chips. Call only when REC_COST_TOOLS_IN_CHAT is on."""
    chips = list(chips or [])
    kinds = [w.get("type") for w in widgets or [] if isinstance(w, dict) and w.get("type") in NEXT_STEPS]
    if not kinds or any(is_next_step(c) for c in chips):
        return chips
    t = kinds[-1]
    last = [w for w in widgets if isinstance(w, dict) and w.get("type") == t][-1]
    cands = [GUARANTEE_DEFAULT[t]] + [c for c in _steps(t, last) if c != GUARANTEE_DEFAULT[t]]
    if free:
        cands = [c for c in cands if free_ok(c)] + [c for c in FREE_FALLBACK_STEPS if c not in cands]
    blocked = _last_actions(history)
    cands = [c for c in cands if action_label(c) not in blocked]
    if not cands:
        return chips
    offered = _offered(history)
    pick = next((c for c in cands if chip_key(c) not in offered), cands[0])
    return chips[:3] + [pick]


def tool_chips(tool, history=(), free=False, widget=None):
    """Deterministic next-step chips for a /rec/tool quick action (no model): 2-3 chips, at most one action chip,
    placed last. Free plan: price-filtered searches swapped for steps a free user can take."""
    w = TOOL_WIDGET.get(tool)
    if not w:
        return []
    out = _steps(w, widget)
    if free:
        out = [c for c in out if free_ok(c)]
        for c in FREE_FALLBACK_STEPS:
            if len(out) >= 3:
                break
            if c not in out and not (action_label(c) and any(action_label(x) for x in out)):
                out.append(c)
    blocked = _last_actions(history)
    out = [c for c in out if action_label(c) not in blocked]
    rest = [c for c in out if not action_label(c)]
    acts = [c for c in out if action_label(c)][:1]
    return rest[:3 - len(acts)] + acts
