"""🧪 Experiments tab: the experiment registry, timeline, confound check and a significance check.

Source of truth is experiments/experiment_log.json in this repo (added 2026-10-25). Claude Code records
an experiment by editing that file and pushing to main; Railway redeploys the dashboard, and git keeps the
history of every read and decision. No Xano table or endpoint is involved, so this tab is read-only and
cheap: it reads one local file per rerun and makes no network calls.

Confounds are computed, not typed: any other experiment or logged change whose dates overlap an
experiment's window is flagged on that experiment. The significance check is a two-proportion z-test on
counts stored in results[].counts, or typed in by hand.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os

import altair as alt
import pandas as pd
import streamlit as st

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments", "experiment_log.json")
STATUSES = ["planned", "live", "reading", "concluded", "reverted"]


def _load() -> dict:
    with open(LOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _d(s):
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _window(e, today):
    """(start, end) used for overlap. Planned experiments have no window yet."""
    s = _d(e.get("start_date"))
    if not s:
        return None
    return s, _d(e.get("end_date")) or today


def _overlaps(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def two_proportion_test(n1, c1, n2, c2):
    """Control (n1, c1) vs treatment (n2, c2). Returns rates, relative lift, 95% CI of the difference and
    a two-sided p-value. None when the inputs can't support a test."""
    if min(n1, n2) <= 0 or not (0 <= c1 <= n1 and 0 <= c2 <= n2):
        return None
    p1, p2 = c1 / n1, c2 / n2
    pooled = (c1 + c2) / (n1 + n2)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se_pooled == 0:
        return None
    z = (p2 - p1) / se_pooled
    p_value = math.erfc(abs(z) / math.sqrt(2))
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p2 - p1
    return {
        "control_rate": p1, "treatment_rate": p2,
        "lift": (diff / p1) if p1 else None,
        "ci_low": diff - 1.96 * se, "ci_high": diff + 1.96 * se,
        "p_value": p_value, "significant": p_value < 0.05,
    }


def _verdict(r):
    if r is None:
        return "Not enough data for a test."
    lift = "n/a" if r["lift"] is None else f"{r['lift']:+.1%}"
    sig = "**significant** (p < 0.05)" if r["significant"] else "**not significant**"
    return (f"Control {r['control_rate']:.2%} vs treatment {r['treatment_rate']:.2%} · relative lift {lift} · "
            f"difference 95% CI {r['ci_low']:+.2%} to {r['ci_high']:+.2%} · p = {r['p_value']:.3f} → {sig}")


def render_experiments_tab():
    try:
        log = _load()
    except (OSError, ValueError) as exc:
        st.error(f"Could not read experiments/experiment_log.json: {exc}")
        return
    exps = log.get("experiments", [])
    changes = log.get("changes", [])
    today = dt.date.today()

    st.caption("Every experiment: goal, dates, results, confounds and significance. "
               "Source: `experiments/experiment_log.json` (edit + push to update).")

    groups = {"Running": ("planned", "live", "reading"), "Past": ("concluded", "reverted"), "All": tuple(STATUSES)}
    pick = st.segmented_control("Show", list(groups), default="Running", key="exp_view") or "All"
    shown = [e for e in exps if e.get("status") in groups[pick]]

    # Timeline: experiments as bars, logged changes as dashed rules. Overlap = possible confound.
    rows = []
    for e in shown:
        w = _window(e, today)
        if w:
            rows.append({"experiment": e["name"][:60], "start": w[0], "end": w[1], "status": e["status"]})
    if rows:
        df = pd.DataFrame(rows)
        bars = alt.Chart(df).mark_bar(cornerRadius=3, height=14).encode(
            x=alt.X("start:T", title=None), x2="end:T",
            y=alt.Y("experiment:N", title=None, sort=None),
            color=alt.Color("status:N", legend=alt.Legend(orient="bottom", title=None), scale=alt.Scale(
                domain=STATUSES, range=["#CDD0D8", "#00A368", "#D97706", "#0D5C3A", "#C53030"])),
            tooltip=["experiment", "status", alt.Tooltip("start:T"), alt.Tooltip("end:T")],
        )
        ch = pd.DataFrame([{"date": _d(c["date"]), "what": c["what"]} for c in changes if _d(c.get("date"))])
        layers = [bars]
        if not ch.empty:
            layers.append(alt.Chart(ch).mark_rule(color="#7E8189", strokeDash=[4, 3]).encode(
                x="date:T", tooltip=["what", alt.Tooltip("date:T")]))
        st.altair_chart(alt.layer(*layers).properties(height=max(120, 26 * len(rows))), use_container_width=True)
        st.caption("Dashed lines = other changes shipped (hover). Anything overlapping an experiment can muddy its result.")
    elif not shown:
        st.info("Nothing here.")

    def _latest(e):
        r = e.get("results") or []
        return r[-1].get("note", "") if r else ""

    if shown:
        st.dataframe(pd.DataFrame([{
            "Experiment": e["name"], "Status": e["status"],
            "Dates": f"{e.get('start_date') or 'planned'} → {e.get('end_date') or ('open' if e.get('start_date') else '')}",
            "Latest result": _latest(e) or "—",
        } for e in shown]), use_container_width=True, hide_index=True)

    for e in shown:
        w = _window(e, today)
        with st.expander(f"{e['name']}  ·  {e['status']}"):
            st.markdown(f"**Goal:** {e.get('goal') or '—'}  \n**Primary metric:** {e.get('primary_metric') or '—'}"
                        + (f"  \n**Guardrail:** {e['guardrail']}" if e.get("guardrail") else "")
                        + f"  \n**Design:** {e.get('design') or '—'}"
                        + f"  \n**Dates:** {e.get('start_date') or 'planned'} → {e.get('end_date') or 'open'}"
                        + (f" (review {e['review_date']})" if e.get("review_date") else "")
                        + (f"  \n_{e['dates_note']}_" if e.get("dates_note") else ""))

            flagged = []
            if w:
                for o in exps:
                    ow = _window(o, today)
                    if o is not e and ow and _overlaps(w, ow):
                        flagged.append(f"Experiment running at the same time: {o['name']}")
                for c in changes:
                    cs, ce = _d(c.get("date")), _d(c.get("end_date")) or _d(c.get("date"))
                    if cs and _overlaps(w, (cs, ce)):
                        flagged.append(f"{c['date']}: {c['what']}")
            noted = e.get("confounds_noted", [])
            if flagged or noted:
                st.markdown("**Possible confounds**")
                for x in noted:
                    st.markdown(f"- {x}")
                for x in flagged:
                    st.markdown(f"- {x} _(auto: overlapping dates)_")
            if e.get("risks"):
                st.markdown("**Risks:** " + "; ".join(e["risks"]))

            st.markdown("**Results**")
            if not e.get("results"):
                st.caption("No reads yet.")
            for r in e.get("results", []):
                st.markdown(f"- **{r.get('date', '')}** {r.get('note', '')}")
                cnt = r.get("counts")
                if cnt:
                    res = two_proportion_test(cnt["control"]["n"], cnt["control"]["conv"],
                                              cnt["treatment"]["n"], cnt["treatment"]["conv"])
                    st.markdown("  " + _verdict(res))
            if e.get("stat_sig"):
                st.caption(f"Significance noted: {e['stat_sig']}")

            with st.popover("Check significance"):
                st.caption("Two-proportion z-test (conversion-style metrics). Pre/post designs are not randomized: "
                           "treat a significant result as suggestive, and check the confounds above.")
                k = e["id"]
                a, b = st.columns(2)
                n1 = a.number_input("Control: visitors", min_value=0, step=1, key=f"{k}_n1")
                c1 = a.number_input("Control: conversions", min_value=0, step=1, key=f"{k}_c1")
                n2 = b.number_input("Treatment: visitors", min_value=0, step=1, key=f"{k}_n2")
                c2 = b.number_input("Treatment: conversions", min_value=0, step=1, key=f"{k}_c2")
                if n1 and n2:
                    st.markdown(_verdict(two_proportion_test(int(n1), int(c1), int(n2), int(c2))))

    concluded = [e for e in exps if e.get("status") in ("concluded", "reverted")]
    with st.expander("What we've learned, by theme"):
        if not concluded:
            st.caption("Nothing concluded yet.")
        for t in sorted({t for e in concluded for t in e.get("themes", [])}):
            st.markdown(f"**{t}**")
            for e in concluded:
                if t in e.get("themes", []):
                    st.markdown(f"- {e['name']} ({e['status']}): {e.get('decision') or _latest(e)}")
