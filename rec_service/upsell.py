"""Price question -> upgrade prompt (owner decision 2026-10-26). Pure - no app/config imports.

Before: a couple without Forever (detail LIGHT or FREE) who asked about price got a refusal ("I can't give cost
numbers here") and nothing else - real examples: "Show me all-inclusive venues for 120 guests under $30k in
Brooklyn", "What will our wedding cost overall", "Best prices?", "Estimate my cost". Only 1 user saw the Planner
paywall in 7 days. Now, detected in CODE on the couple's own message (not left to the model):
  1. the model is told to answer on fit as usual plus one warm line that exact prices are on Forever, and
     apply_text() drops any flat-refusal sentence and guarantees that line;
  2. the answer carries `upsell_prompt` (prompt()) so the page can show the upgrade card.
At most once per chat (app checks the stored turns); never for paid / Forever members (app checks `paid`).
"""
import re

# Price / cost / budget NUMBERS asked for. Kept narrow on purpose: style, location, vendor-type and "more"
# requests must not match (tests: "barn venues in Georgia", "Show 3 more", "Find a photographer",
# "All-inclusive venues", "Change my guest count").
_PRICE_Q = re.compile(
    r"[$€£]\s?\d"                                              # $30k, $8,000
    r"|\b\d[\d,.]*\s?k\b"                                      # 30k, 30 k
    r"|\b\d{1,3},\d{3}\b(?!\s*(?:guests?|people|persons?|attendees))"   # 30,000 (not "1,200 guests")
    r"|\b\d[\d,.]*\s?(?:dollars?|bucks|grand)\b"
    r"|\b(?:prices?|pricing|priced|pricey|pricier|cost|costs|costing|costly|budgets?|budgeting|cheap|cheaper|"
    r"cheapest|inexpensive|expensive|afford|affordable|affordability|fees?|how much|per (?:head|person|guest|plate)|"
    r"quotes?|minimum spend|f&b minimum|food minimum|deposit|rates|good deal|best deal)\b",
    re.I)

REASON = "price_question"
# No "tap the card" wording: the line must read right on a page that doesn't draw upsell_prompt yet.
NUDGE = "The exact prices for these, and a full cost estimate built from real vendor pricing, come with Forever."
_HAS_NUDGE = re.compile(r"\bforever\b", re.I)
# A flat refusal the model may still write ("I can't give cost numbers here", "I don't have pricing for you").
_REFUSAL = re.compile(
    r"\b(?:can't|can ?not|cannot|won't|unable to|not able to|don't have|do not have|no access to)\b[^.!?\n]{0,60}"
    r"\b(?:price|prices|pricing|cost|costs|numbers|figures|estimates?|dollar)", re.I)

CONTEXT = ("\nPRICE QUESTION: the couple's latest message asks about prices, cost or budget numbers. Do NOT refuse "
           "and never say you can't give numbers. Answer it on fit exactly as your detail level allows (search and "
           "show picks that match everything else they asked for), then add ONE warm line that the exact prices "
           "and their full cost estimate come with Forever.")


# Owner 2026-10-26: "for free users, we're still pushing them to open pdfs where possible and forcing upgrades".
# Someone who CAN open a PDF (free views left, or a 1-week / 4-week plan) is sent to the top pick's PDF first;
# Forever is the second line. Only someone with no way to see a PDF is sold Forever as the answer.
PDF_FIRST = ("\nPRICE QUESTION: the couple's latest message asks about prices, cost or budget numbers. Do NOT refuse and "
             "never say you can't give numbers. Answer on fit exactly as your detail level allows (search and show "
             "picks that match everything else they asked for). Your closing line sends them to open your top pick's "
             "pricing PDF now - %s - because that PDF is where the exact prices are. Then ONE short line that Forever "
             "also puts exact prices and their full cost estimate right here.")


def context(views=0, has_plan=False):
    """The PRICE QUESTION context line: PDF first when they can open one, else Forever."""
    if has_plan:
        return PDF_FIRST % "their plan includes every PDF"
    if views > 0:
        return PDF_FIRST % ("they have %d free PDF view%s left" % (views, "" if views == 1 else "s"))
    return CONTEXT


def wall_prompt():
    """First answer of a chat for a free couple with no free PDF views left (owner 2026-10-26: force the upgrade)."""
    return {"reason": "no_free_views", "plan": "forever", "headline": "Unlock the exact prices",
            "body": "You've used your free PDF views. Forever unlocks every venue's real pricing PDF and a full cost "
                    "estimate for your wedding.", "cta": "See plans"}


def is_price_question(text):
    """True when the couple's message asks for price / cost / budget figures."""
    return bool(_PRICE_Q.search(text or ""))


def apply_text(text):
    """Drop flat-refusal sentences and make sure the reply carries the Forever nudge (once)."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [p for p in parts if p and not _REFUSAL.search(p)]
    out = " ".join(kept).strip() or (text or "").strip()
    if not _HAS_NUDGE.search(out):
        out = (out + " " + NUDGE).strip()
    return out


def prompt(has_plan=False):
    """The structured card for the page. has_plan: an active non-Forever plan (detail LIGHT) - they can open
    PDFs already, so the body sells what Forever adds here."""
    body = ("Forever adds every venue's exact prices and a full cost breakdown right here in the assistant."
            if has_plan else
            "Unlock every venue's real pricing PDF and a full cost estimate for your wedding, right here in the "
            "assistant.")
    return {"reason": REASON, "plan": "forever", "headline": "See the exact prices",
            "body": body, "cta": "See plans"}


def shown_in(history):
    """True if an earlier answer in this chat already carried the upgrade prompt."""
    return any(t.get("role") == "assistant" and t.get("upsell_shown") for t in history or ())
