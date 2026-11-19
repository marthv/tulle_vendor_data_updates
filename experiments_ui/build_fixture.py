"""Build experiments_ui/fixture_snapshot.json and experiments_ui/preview.html.

FIXTURE, NOT REAL READOUTS. The registry entries (ids, names, dates, notes, changes) are the
real ones from experiments/experiment_log.json. What is invented here, so the design can be
reviewed against every health state before the engine runs against Xano:

* v2 spec fields (tier, assignment, metric, planned/max days...) for entries that don't
  have them yet - only ever filled where the registry lacks the key (setdefault), never
  overriding what the registry says;
* the arm counts the engine's metric runner would return (several are anchored on numbers
  quoted in the registry notes, e.g. free-daily-question 9/216 vs 2/83);
* daily readout history (P(beat) and couples-in over time);
* two "detected but unlogged" changes for the Changes tab.

The snapshot itself is produced by the real engine (experiments_engine.snapshot.build_snapshot)
so the shape is exactly what the dash will receive. Run:

    python experiments_ui/build_fixture.py
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from experiments_engine import snapshot as _snapshot  # noqa: E402
from experiments_ui import render  # noqa: E402

TODAY = dt.date(2026, 11, 19)
REGISTRY = os.path.join(ROOT, "experiments", "experiment_log.json")


def d(s):
    return dt.date.fromisoformat(s)


# --------------------------------------------------------------------------------------
# v2 overlays (only applied where the registry has no value for the key)
# --------------------------------------------------------------------------------------
TEXT_KEYS = {"name", "what_changed", "hypothesis", "tests", "surfaces", "rollback", "links"}
PURCHASES = {"key": "purchases_per_day", "max_drop_rel": 0.25, "label": "purchases per day"}

OVERLAYS = {
    "EXP-2026-11-price-report-vs-pi-card": dict(
        name="Tulle Price Report replaces the Pricing Intelligence card",
        tier="money", what_changed="Vendor pages show the Tulle Price Report (one line per cost, guest slider) instead of the Pricing Intelligence card. Free couples see one locked unlock card.",
        surfaces=["weweb:vendor-page", "ww-price-report", "xano:ep230"],
        audience={"segment": "every vendor-page visitor", "est_daily_exposed": 130},
        hypothesis="If the vendor page shows a simpler report card, then more visitors tap Unlock, because the value is easier to read at a glance.",
        tests="Does the Price Report get more unlock clicks per vendor-page visitor than the old card?",
        metric={"key": "unlock_click_rate", "direction": "increase", "baseline": 0.149, "mde_rel": 0.15,
                "label": "vendor-page visitors who tap Unlock",
                "definition": "Unique clicked_membership_for_pricing_venue_price_report / unique viewed_vendor_page per day (Mixpanel 3963130), internal accounts excluded."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-11-04", "2026-11-16"], "post_start": "2026-11-17T16:47:00Z"},
        planned_days=14, max_days=28, reporting="auto",
        rollback="Unhide the old PI card (restore file in tulletogether .claude/xano_backups) and hide ww-price-report; publish.",
        links=["WeWeb publish 1398", "WeWeb publish 1401", "ww-price-report 2201271"],
        caveats=["Period 2 (from 11-18 06:47 UTC) also removed the free first vendor - read periods separately."],
    ),
    "EXP-2026-10-search-railway": dict(
        tier="infra", what_changed="Half of couples get venue search from Meilisearch on Railway (Planner + Discovery); Xano ep119 stays as the fallback.",
        surfaces=["railway:search-api", "rec-engine", "xano:ep119", "weweb:discovery"],
        audience={"segment": "every couple who searches venues", "est_daily_exposed": 420},
        hypothesis="If search runs on Meilisearch, then more searches return in under 1 s, because Xano ep119 cache misses take 1.5-6 s.",
        tests="Is Railway search faster than ep119 without losing reliability?",
        metric={"key": "search_under_1s", "direction": "increase", "baseline": 0.55, "mde_rel": 0.1,
                "label": "searches answered in under 1 s",
                "definition": "Discovery + Planner searches with duration < 1000 ms / all searches, by X-Search-Engine bucket."},
        guardrails=[{"key": "search_error_rate", "max_rise_rel": 0.25, "label": "search error rate"}],
        assignment={"type": "split", "rule": "hash_pct", "salt": "railsearch", "pct": 50},
        planned_days=14, review_date="2026-11-09", max_days=42, reporting="auto",
        rollback="REC_RAILWAY_SEARCH_PCT=0 on rec-engine and ep119 $rw_pct=0.",
        links=["search-api 23:32 fix", "ep119 $rw_pct"],
    ),
    "EXP-2026-11-free-daily-question": dict(
        tier="ux", what_changed="Free couples get 3 Planner questions for life, then 1 per UTC day (no accumulation).",
        surfaces=["rec-engine", "weweb:planner", "xano:table79"],
        audience={"segment": "free couples who open the Planner", "est_daily_exposed": 14},
        hypothesis="If free couples get a daily question, then more of them return within 3 days, because there is a reason to come back.",
        tests="Does a daily allowance bring free Planner couples back?",
        metric={"key": "planner_return_within_days", "params": {"days": 3}, "direction": "increase",
                "baseline": 0.042, "mde_rel": 0.5, "label": "came back within 3 days",
                "definition": "New free Planner couples who type a question again on a later day within 3 days (table 79, internal excluded)."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-10-21", "2026-11-05"], "post_start": "2026-11-09T01:11:05Z"},
        planned_days=14, max_days=42, reporting="auto",
        rollback="REC_FREE_DAILY_MODE=off on rec-engine.", links=["rec e29f61a"],
    ),
    "EXP-2026-10-pdf-popup-copy": dict(
        tier="money", what_changed="Softer copy on the 3rd-free-PDF warning and the PDF wall; the submit-a-PDF link is smaller.",
        surfaces=["weweb:vendor-page", "weweb:pdf-wall"],
        audience={"segment": "free couples who reach the PDF warning or wall", "est_daily_exposed": 90},
        hypothesis="If the wall copy is softer, then more couples who hit it buy within a day, because it reads as an offer rather than a block.",
        tests="Does softer PDF-wall copy convert more wall viewers?",
        metric={"key": "wall_to_purchase_24h", "direction": "increase", "baseline": 0.06, "mde_rel": 0.3,
                "label": "wall viewers who buy within 24 h",
                "definition": "Couples who saw the PDF warning/wall and bought within 24 h / couples who saw it (Mixpanel + table 16)."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-10-05", "2026-10-25"], "post_start": "2026-10-26T00:00:00Z"},
        planned_days=21, max_days=42, reporting="auto",
        rollback="Restore the previous popup text (WeWeb backup 2026-10-26).", links=["WeWeb publish 2026-10-26"],
    ),
    "EXP-2026-11-ask-tulle-bubble": dict(
        tier="ux", what_changed="A floating 'Ask Tulle' Planner chat on Discovery, Membership and My wedding (phone: Map | Ask Tulle dock); empty-results banner.",
        surfaces=["weweb:discovery", "weweb:membership", "weweb:my-wedding", "ww-planner-chat"],
        audience={"segment": "every signed-in couple on those pages", "est_daily_exposed": 310},
        hypothesis="If the Planner is one tap away on every page, then more couples ask it something, because they no longer have to find the Planner page.",
        tests="Does a floating entry point grow the share of couples using the Planner?",
        metric={"key": "planner_user_share", "direction": "increase", "baseline": 0.08, "mde_rel": 0.25,
                "label": "couples who ask the Planner something",
                "definition": "Signed-in couples with 1+ Planner question / signed-in couples active that day (table 79 + Mixpanel)."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-10-28", "2026-11-10"], "post_start": "2026-11-11T05:00:00Z"},
        planned_days=7, max_days=28, reporting="auto",
        rollback="Hide the Ask Tulle dock elements in WeWeb and publish.", links=["WeWeb publish 2026-11-11"],
    ),
    "EXP-2026-10-chat-cost-cards": dict(
        tier="ux", what_changed="Planner answers can include cost cards (estimate, split, compare, budget).",
        surfaces=["rec-engine", "ww-planner-chat"],
        audience={"segment": "couples who ask the Planner about cost", "est_daily_exposed": 25},
        hypothesis="If answers show a cost card, then more couples open Cost tools, because the number invites a closer look.",
        tests="Do cost cards lead couples into Cost tools?",
        metric={"key": "manual", "direction": "increase", "label": "cost-card couples who open Cost tools"},
        guardrails=[{"key": "planner_answer_ms_p50", "max_rise_rel": 0.2, "label": "Planner answer time"}],
        assignment={"type": "rollout"}, planned_days=60, review_date="2026-12-25", max_days=90, reporting="manual",
        rollback="REC_COST_CARDS=0 on rec-engine.",
    ),
    "EXP-2026-10-next-step-chips": dict(
        tier="ux", what_changed="Planner answers end with next-step chips and action chips (Open Cost tools / Open my budget / Add a quote).",
        surfaces=["rec-engine", "ww-planner-chat"],
        audience={"segment": "couples who get a Planner answer", "est_daily_exposed": 60},
        hypothesis="If answers end with suggested next steps, then more couples ask a second question, because the next move is obvious.",
        tests="Do next-step chips keep couples in the conversation?",
        metric={"key": "second_question_rate", "direction": "increase", "baseline": 0.09, "mde_rel": 0.6,
                "label": "couples who ask a second question",
                "definition": "Planner sessions with 2+ questions / Planner sessions (table 79), by chips arm."},
        assignment={"type": "split", "rule": "hash_pct", "salt": "chips", "pct": 50},
        planned_days=28, review_date="2026-11-23", max_days=42, reporting="auto",
        rollback="REC_NEXT_CHIPS_PCT=0 on rec-engine.",
    ),
    "EXP-2026-08-pdf-warning-view3": dict(
        tier="money", what_changed="A 'Last free pricing PDF' warning on the 3rd free PDF view.",
        surfaces=["weweb:vendor-page"],
        audience={"segment": "free couples opening their 3rd PDF", "est_daily_exposed": 40},
        hypothesis="If couples are warned before the wall, then more of them buy, because the wall is no longer a surprise.",
        tests="Does a heads-up before the PDF wall lift purchases?",
        metric={"key": "manual", "direction": "increase", "label": "warned couples who buy within 7 days"},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-08-01", "2026-08-18"], "post_start": "2026-08-19T00:00:00Z"},
        planned_days=14, max_days=42, reporting="manual",
        rollback="Hide the warning popup in WeWeb.",
    ),
    "EXP-2026-11-pricing-page-copy": dict(
        tier="money", what_changed="New pricing-page headline ('See what wedding vendors really charge.'), outcome subtitles per plan and plain feature lines.",
        surfaces=["weweb:pricing"],
        audience={"segment": "couples who open the pricing page", "est_daily_exposed": 70},
        hypothesis="If the pricing page says plainly what each plan gets, then more visitors start checkout, because they can tell the plans apart.",
        tests="Does plainer pricing copy start more checkouts?",
        metric={"key": "pricing_to_checkout", "direction": "increase", "baseline": 0.07, "mde_rel": 0.3,
                "label": "pricing-page visitors who start checkout",
                "definition": "Unique checkout_clicked / unique viewed_pricing_page per day (Mixpanel), internal excluded."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-11-01", "2026-11-14"], "post_start": "2026-11-15T04:40:00Z"},
        planned_days=14, max_days=42, reporting="auto",
        rollback="Restore the previous pricing-page text (WeWeb backup 2026-11-15).", links=["WeWeb publish 2026-11-15"],
    ),
    "EXP-2026-11-whole-wedding-planner": dict(
        name="Plan my wedding: whole-wedding proposal for every couple",
        tier="ux", what_changed="One tap in the Planner proposes the whole wedding (venue plus a pick or estimate per category, total vs budget); couples add it to My wedding.",
        surfaces=["rec-engine", "ww-planner-chat", "weweb:my-wedding", "xano:table92", "xano:table93"],
        audience={"segment": "every couple who opens the Planner", "est_daily_exposed": 180},
        hypothesis="If couples can see a whole-wedding plan in one tap, then more of them plan inside Tulle, because the first draft is done for them.",
        tests="Do couples adopt the whole-wedding proposal without hurting purchases?",
        metric={"key": "rollout_adoption", "direction": "increase", "label": "adoption (rollout)"},
        guardrails=[PURCHASES, {"key": "planner_answer_ms_p50", "max_rise_rel": 0.2, "label": "Planner answer time"}],
        assignment={"type": "rollout"}, planned_days=14, max_days=28, reporting="auto",
        rollback="REC_WHOLE_WEDDING_PCT=0 on rec-engine.", links=["ww-planner-chat 03d4ff5", "WeWeb publish 1398"],
    ),
    "EXP-2026-11-quote-check-everyone": dict(
        tier="ux", what_changed="Planner quote check open to every signed-in couple (was Forever only), with a 'Got a quote?' home card.",
        surfaces=["rec-engine", "ww-planner-chat"],
        audience={"segment": "signed-in couples on the Planner home", "est_daily_exposed": 60},
        hypothesis="If every couple can check a quote, then more Planner couples run one, because the card is on the home screen.",
        tests="Does opening quote check to everyone lift its use?",
        metric={"key": "quote_check_rate", "direction": "increase", "mde_rel": 0.6, "label": "Planner couples who check a quote",
                "definition": "Couples with 1+ rec_quote_check / Planner couples (table 79)."},
        assignment={"type": "benchmark", "benchmark": {"n": 240, "conv": 12}},
        planned_days=14, max_days=42, reporting="auto", rollback="REC_QUOTE_CHECK_ALL=0 on rec-engine.",
    ),
    "EXP-2026-11-planner-pdf-count-link": dict(
        tier="ux", what_changed="Planner venue cards show a centred 'N Pricing PDFs' link under the button.",
        surfaces=["ww-planner-chat"],
        audience={"segment": "couples shown a Planner venue card", "est_daily_exposed": 160},
        hypothesis="If the card says how many PDFs a venue has, then more couples open one, because the evidence is visible.",
        tests="Does a PDF-count link on venue cards lift PDF opens from the Planner?",
        metric={"key": "pdf_open_from_card", "direction": "increase", "baseline": 0.12, "mde_rel": 0.25,
                "label": "card viewers who open a PDF",
                "definition": "Couples shown a venue card who open a pricing PDF from it within the session (Mixpanel)."},
        guardrails=[PURCHASES],
        assignment={"type": "split", "rule": "user_id_parity", "treatment": "even"},
        planned_days=14, max_days=42, reporting="auto", rollback="pdfCountLink=false on the component.",
    ),
    "EXP-2026-11-save-offer-60-forever": dict(
        tier="money", what_changed="'Too expensive' save offer is now 60% off Forever for 12 h (server-enforced code + email), replacing 20% for 30 min.",
        surfaces=["weweb:pricing", "xano:save-offer", "brevo"],
        audience={"segment": "couples who pick 'Too expensive' when leaving checkout", "est_daily_exposed": 7},
        hypothesis="If the save offer is 60% for 12 h, then more 'too expensive' couples buy, because the price objection is answered.",
        tests="Does a deeper, longer save offer recover more 'too expensive' couples?",
        metric={"key": "save_offer_accept", "direction": "increase", "baseline": 0.048, "mde_rel": 1.0,
                "label": "'too expensive' couples who buy",
                "definition": "Couples who saw the save offer and paid within 12 h / couples who saw it (table 16 + Brevo)."},
        guardrails=[PURCHASES],
        assignment={"type": "pre_post", "pre": ["2026-09-15", "2026-11-10"], "post_start": "2026-11-11T00:00:00Z"},
        planned_days=21, max_days=42, reporting="auto", rollback="Set the save offer back to 20% / 30 min (Xano save-offer).",
    ),
    "EXP-2026-11-free-question-limit-note": dict(
        tier="ux", what_changed="Free couples see a clear 'free question used' note (pay for more, or come back tomorrow).",
        surfaces=["ww-planner-chat"],
        audience={"segment": "free couples who use their free question", "est_daily_exposed": 20},
        hypothesis="If the limit is explained, then more free couples come back the next day, because they know one more question is waiting.",
        tests="Does explaining the limit bring free couples back?",
        metric={"key": "manual", "direction": "increase", "label": "note viewers who return next day"},
        assignment={"type": "rollout"}, guardrails=[PURCHASES],
        planned_days=14, max_days=28, reporting="manual", rollback="freeLimitNote=false on the component.",
    ),
    "EXP-2026-10-stop-progress": dict(
        tier="ux", audience={"segment": "couples waiting on a Planner answer", "est_daily_exposed": 120},
        metric={"key": "rollout_adoption", "direction": "increase", "label": "answers abandoned before they finish (rollout)"},
        guardrails=[{"key": "planner_answer_ms_p50", "max_rise_rel": 0.2, "label": "Planner answer time"}],
        assignment={"type": "rollout"}, planned_days=10, review_date="2026-11-26", max_days=60, reporting="auto",
    ),
    "EXP-2026-11-planner-my-wedding-plan": dict(
        tier="ux", what_changed="Planner 'My wedding' plan for every couple (no holdout).",
        surfaces=["ww-planner-chat", "weweb:my-wedding"],
        audience={"segment": "every couple who opens My wedding", "est_daily_exposed": 90},
        hypothesis="If couples can keep their plan in My wedding, then more of them return in a week, because their plan lives here.",
        tests="Is My wedding adopted without hurting purchases?",
        metric={"key": "rollout_adoption", "direction": "increase", "label": "adoption (rollout)"},
        guardrails=[PURCHASES], assignment={"type": "rollout"},
        planned_days=21, max_days=42, reporting="auto", rollback="Hide the My wedding entry points.",
    ),
}


# --------------------------------------------------------------------------------------
# Readouts the metric runner would return (fixture numbers)
# --------------------------------------------------------------------------------------

def exposure_rows(start, n_by_arm, split, quiet_last=0, seed=1):
    """Daily exposure rows from start to yesterday that sum to n_by_arm."""
    rng = random.Random(seed)
    days = max((TODAY - start).days, 1)
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    live = dates[:len(dates) - quiet_last] if quiet_last else dates
    w = [0.7 + 0.6 * rng.random() for _ in live]
    out = []
    for arm, n in n_by_arm.items():
        tot = sum(w)
        alloc = [int(round(n * x / tot)) for x in w]
        alloc[-1] += n - sum(alloc)
        for i, dte in enumerate(live):
            if len(out) <= i:
                out.append({"date": dte.isoformat()})
            out[i][arm if split else "n"] = alloc[i]
    for dte in dates[len(live):]:
        out.append({"date": dte.isoformat(), **({"control": 0, "treatment": 0} if split else {"n": 0})})
    return out


def arms(cn, cc, tn, tc):
    return {"control": {"n": cn, "conv": cc}, "treatment": {"n": tn, "conv": tc}}


READOUTS = {
    # guardrail breach: purchases/day well down since period 2 (memory 11-18: 4 pays/39 h vs 7.9 mean)
    "EXP-2026-11-price-report-vs-pi-card": dict(
        arms=arms(1700, 254, 262, 17), split=False, start="2026-11-17",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 110, "days": 14}, "post": {"count": 4, "days": 1.6}}}]),
    "EXP-2026-10-search-railway": dict(
        arms=arms(3104, 1707, 3081, 2711), split=True, start="2026-10-26",
        guardrails=[{"key": "search_error_rate", "arms": arms(3104, 31, 3081, 27)}]),
    "EXP-2026-11-free-daily-question": dict(arms=arms(216, 9, 83, 2), split=False, start="2026-11-09",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 98, "days": 14}, "post": {"count": 61, "days": 10}}}]),
    "EXP-2026-10-pdf-popup-copy": dict(arms=arms(2150, 129, 2096, 88), split=False, start="2026-10-26",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 120, "days": 21}, "post": {"count": 131, "days": 24}}}]),
    "EXP-2026-11-ask-tulle-bubble": dict(arms=arms(2400, 192, 2250, 201), split=False, start="2026-11-11",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 95, "days": 14}, "post": {"count": 52, "days": 8}}}]),
    "EXP-2026-10-next-step-chips": dict(arms=arms(412, 37, 405, 41), split=True, start="2026-10-26", quiet_last=4),
    "EXP-2026-11-pricing-page-copy": dict(arms=arms(980, 69, 288, 24), split=False, start="2026-11-15",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 98, "days": 14}, "post": {"count": 22, "days": 4}}}]),
    "EXP-2026-11-whole-wedding-planner": dict(arms=None, split=False, start="2026-11-17", total=410,
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 101, "days": 14}, "post": {"count": 13, "days": 2}}},
                    {"key": "planner_answer_ms_p50", "breached": False}]),
    "EXP-2026-11-quote-check-everyone": dict(arms={"treatment": {"n": 236, "conv": 19}}, split=False, start="2026-11-15"),
    "EXP-2026-11-planner-pdf-count-link": dict(arms=arms(318, 37, 322, 49), split=True, start="2026-11-15"),
    "EXP-2026-11-save-offer-60-forever": dict(arms=arms(210, 10, 54, 5), split=False, start="2026-11-11",
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 98, "days": 14}, "post": {"count": 49, "days": 8}}}]),
    "EXP-2026-10-stop-progress": dict(
        tier="ux", audience={"segment": "couples waiting on a Planner answer", "est_daily_exposed": 120},
        metric={"key": "rollout_adoption", "direction": "increase", "label": "answers abandoned before they finish (rollout)"},
        guardrails=[{"key": "planner_answer_ms_p50", "max_rise_rel": 0.2, "label": "Planner answer time"}],
        assignment={"type": "rollout"}, planned_days=10, review_date="2026-11-26", max_days=60, reporting="auto",
    ),
    "EXP-2026-10-stop-progress": dict(arms=None, split=False, start="2026-10-26", total=2600,
        guardrails=[{"key": "planner_answer_ms_p50", "breached": False}]),
    "EXP-2026-11-planner-my-wedding-plan": dict(arms=None, split=False, start="2026-11-08", total=980,
        guardrails=[{"key": "purchases_per_day", "count": {"pre": {"count": 96, "days": 14}, "post": {"count": 79, "days": 11}}}]),
}


def metric_runner(entry):
    eid = entry.get("id")
    spec_ = READOUTS.get(eid)
    if not spec_:
        return {"error": "metric not in catalog yet (fixture)", "computed_at": None}
    start = d(spec_["start"])
    a = spec_.get("arms")
    if a and spec_["split"]:
        ex = exposure_rows(start, {"control": a["control"]["n"], "treatment": a["treatment"]["n"]}, True,
                           spec_.get("quiet_last", 0), seed=len(eid))
    elif a:
        ex = exposure_rows(start, {"n": a["treatment"]["n"]}, False, spec_.get("quiet_last", 0), seed=len(eid))
    else:
        ex = exposure_rows(start, {"n": spec_.get("total", 100)}, False, seed=len(eid))
    out = {"exposure_by_day": ex, "guardrails": spec_.get("guardrails") or [],
           "computed_at": "2026-11-19T12:00:00Z", "error": None, "source": "auto"}
    if a:
        out["arms"] = a
    return out


class FixtureHistory:
    """Daily history rows: cumulative arms growing with exposure, P(beat) drifting to today's."""

    def __init__(self, final):
        self.final = final  # exp_id -> (arms, start)

    def load(self, exp_id, days=90, today=None):
        spec_ = READOUTS.get(exp_id)
        if not spec_ or not spec_.get("arms") or "control" not in spec_["arms"]:
            return []
        from experiments_engine import stats
        a = spec_["arms"]
        start = d(spec_["start"])
        n_days = (TODAY - start).days
        quiet = spec_.get("quiet_last", 0)
        rows = []
        rng = random.Random(len(exp_id) * 7)
        for i in range(1, n_days):
            day = start + dt.timedelta(days=i)
            frac = min(i / max(n_days - quiet, 1), 1.0)
            cn = max(int(a["control"]["n"] * (frac if spec_["split"] else 1)), 1)
            cc = int(round(a["control"]["conv"] * cn / a["control"]["n"]))
            tn = max(int(a["treatment"]["n"] * frac), 1)
            tc = int(round(a["treatment"]["conv"] * frac + rng.uniform(-1.2, 1.2) * math.sqrt(max(tn, 1)) * 0.08))
            tc = min(max(tc, 0), tn)
            st = stats.beta_binomial(cn, cc, tn, tc, draws=4000)
            rows.append({"date": day.isoformat(), "exp_id": exp_id, "p_beat": st["p_beat"],
                         "n_control": cn, "n_treatment": tn, "n_total": cn + tn, "error": None})
        return rows


UNLOGGED = [
    {"ts": "2026-11-19T09:12:00Z", "source": "weweb", "ref": "cacheVersion 1403",
     "title": "WeWeb production publish detected (build hash changed). No changes[] entry within 24 h.",
     "surfaces": ["weweb"]},
    {"ts": "2026-11-19T07:40:00Z", "source": "railway", "ref": "rec-engine redeploy of 7252cee",
     "title": "rec-engine redeployed on the same commit (looks like a REC_* variable flip). Not logged.",
     "surfaces": ["rec-engine"]},
]


def main():
    with open(REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    reg = copy.deepcopy(reg)
    for e in reg.get("experiments") or []:
        ov = OVERLAYS.get(e.get("id"))
        if not ov:
            continue
        for k, v in ov.items():
            # wording stays the registry's own where it has some; the numeric spec the engine
            # needs (metric, arms, days, reporting) is the fixture's, so each state can be shown
            if k in TEXT_KEYS and e.get(k) not in (None, "", [], {}):
                continue
            e[k] = copy.deepcopy(v)
        if "review_date" not in ov and e.get("start_date") and e.get("planned_days"):
            e["review_date"] = (d(e["start_date"]) + dt.timedelta(days=int(e["planned_days"]))).isoformat()
        e["framework_version"] = 2

    hist = FixtureHistory(READOUTS)
    snap = _snapshot.build_snapshot(reg, metric_runner=metric_runner, history_store=hist, today=TODAY)

    # history[] per row (the engine keeps it on its volume; the dash needs it inline)
    for row in snap["experiments"]:
        h = hist.load(row.get("id"))
        row["history"] = [{"date": r["date"], "p_beat": r["p_beat"], "n_control": r["n_control"],
                           "n_treatment": r["n_treatment"]} for r in h]
        ov = OVERLAYS.get(row.get("id")) or {}
        if ov.get("caveats"):
            row["caveats"] = ov["caveats"]
    # detected changes arrive the way service.py serves them: snapshot.change_feed
    snap["change_feed"] = {"events": [dict(c, user_facing=True) for c in UNLOGGED],
                           "unlogged": [dict(c, user_facing=True, why="no changes[] entry within 24 h") for c in UNLOGGED],
                           "notes": [], "collected_at": "2026-11-19T12:00:00Z"}
    snap["generated_at"] = "2026-11-19T12:00:00Z"
    snap["fixture"] = True
    snap["fixture_note"] = ("FIXTURE: real registry entries; v2 fields, arm counts, history and the two "
                            "unlogged changes are invented for design review. Not a readout.")

    out_json = os.path.join(HERE, "fixture_snapshot.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    with open(os.path.join(HERE, "preview.html"), "w", encoding="utf-8") as f:
        f.write(render.build_html(snap, theme_mode=None))
    print("wrote", out_json, "errors:", snap["errors"][:3])
    print("summary", snap["summary"])
    for r in snap["experiments"]:
        print(f"{str(r.get('health')):18} stale={r.get('stale')!s:5} {r.get('id')}")


if __name__ == "__main__":
    main()
