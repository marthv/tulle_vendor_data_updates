"""
📊 Cohort / Funnel Analytics — Streamlit tab for the Tulle admin dashboard.

Self-contained: dashboard.py just does
    from cohorts import render_cohorts_tab
    with tab_co:
        render_cohorts_tab(XANO_BASE)

Tests the revenue = signups × pay_rate × ARPU thesis (does geographic-hub vendor
density lift monetization). Xano-only, cached snapshot, in-memory pandas cutting so
it never hammers Xano — one 6h-cached pull of two secret-gated endpoints
(analytics_users_export ep205 + analytics_hub_stats ep206), then all slicing is local.

Metrics: ARPU = revenue / PAYING user (so revenue = signups × pay_rate × ARPU);
rev_per_signup kept as the blended figure. Retention is a last-active survival proxy
(last_active_at instrumentation went live 2026-07-21, so it only reflects activity
from then, over users with a recorded session).
"""

import os
import time
import datetime as _dt

import requests
import pandas as pd
import streamlit as st

EXPORT_SECRET = os.environ.get(
    "ANALYTICS_EXPORT_SECRET",
    "ttv_export_da19ae7c3fbcdd2c51747199117a63a33f848ca9",
)
_ROLE_EXCLUDE = {"admin", "test", "staff", "superadmin", "dev"}
_BACKFILL_START = "2026-07-21"
_GROUP_OPTS = ["density_tier", "hub_key", "hub_mapped", "signup_month", "planning_phase",
               "age_range", "referral", "intent", "budget_bucket", "guest_bucket"]


# ── HTTP (retry transient 000/5xx) ───────────────────────────────────────────────
def _xget(url, tries=4, timeout=60):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None
            last = r.status_code
        except Exception as e:                       # noqa: BLE001
            last = e
        time.sleep(1.5 * (i + 1))
    return None


# ── Stats helpers ────────────────────────────────────────────────────────────────
def _wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - margin) / denom)


def _ztest(k1, n1, k2, n2):
    if n1 == 0 or n2 == 0:
        return False
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = (p * (1 - p) * (1 / n1 + 1 / n2)) ** 0.5
    return bool(se) and abs(p1 - p2) / se >= 1.96


def _pearson(x, y):
    if len(x) < 3:
        return None
    sx, sy = pd.Series(x, dtype="float64"), pd.Series(y, dtype="float64")
    if sx.std() == 0 or sy.std() == 0:
        return None
    return round(float(sx.corr(sy)), 3)


def _budget_bucket(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "Unknown"
    if v <= 0:      return "Unknown"
    if v < 15000:   return "<$15k"
    if v < 30000:   return "$15–30k"
    if v < 50000:   return "$30–50k"
    if v < 100000:  return "$50–100k"
    return "$100k+"


def _guest_bucket(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "Unknown"
    if v <= 0:   return "Unknown"
    if v < 50:   return "<50"
    if v < 100:  return "50–99"
    if v < 150:  return "100–149"
    if v < 250:  return "150–249"
    return "250+"


def _first(lst):
    if isinstance(lst, list) and lst:
        return str(lst[0]).strip()
    if isinstance(lst, str) and lst.strip():
        return lst.strip()
    return "Unknown"


def _clean(s):
    return (str(s).strip() or "Unknown") if s is not None else "Unknown"


# ── Snapshot pull + dataframe build (cached 6h) ─────────────────────────────────
def _build_df(rows):
    # density_tier is added later in the tab from the separately-cached hub pull, so a
    # transient hub-stats blip can never poison this (heavy) users snapshot.
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ("total_vendor_views", "total_pdf_views", "total_payments",
              "total_amount_paid", "Wedding_Budget", "Wedding_Guest_Count"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce").fillna(0)
    df["signup_dt"] = pd.to_datetime(df.get("signup_at"), unit="ms", errors="coerce")
    df["last_active_dt"] = pd.to_datetime(df.get("last_active_at"), unit="ms", errors="coerce")
    df["signup_month"] = df["signup_dt"].dt.strftime("%Y-%m")
    # hub_key nulls arrive as either None or float NaN depending on the pull; bool(nan) is True,
    # so detect "mapped" via notna()+non-empty (never bool()) — else NaN pollutes sorts/tiers.
    _hk = df.get("hub_key")
    if _hk is None:
        df["hub_key"] = None
        _hk = df["hub_key"]
    df["hub_mapped"] = _hk.notna() & (_hk.astype(str).str.strip() != "")
    df["budget_bucket"] = df.get("Wedding_Budget").apply(_budget_bucket)
    df["guest_bucket"] = df.get("Wedding_Guest_Count").apply(_guest_bucket)
    df["referral"] = df.get("How_did_you_hear_about_us").apply(_clean)
    df["intent"] = df.get("What_are_you_most_interested_in_").apply(_clean)
    df["planning_phase"] = df.get("Planning_Phase").apply(_clean)
    df["age_range"] = df.get("Age_Range").apply(_clean)
    df["role_l"] = df.get("role").apply(lambda s: str(s or "").strip().lower())
    df["is_payer"] = df["total_payments"] > 0
    df["viewed_vendor"] = df["total_vendor_views"] > 0
    df["viewed_pdf"] = df["total_pdf_views"] > 0
    return df


@st.cache_data(ttl=21600, show_spinner=False)
def load_hubs(xano_base, secret):
    """Tiny hub-density pull, cached separately so a transient blip can't poison the
    heavy users snapshot. Returns (tiers, hub_meta); {} on failure so the caller can
    detect an empty result, clear this cache, and self-heal on the next render."""
    hub = _xget(f"{xano_base}/analytics_hub_stats?secret={secret}", timeout=30, tries=6)
    hub_rows = hub.get("items", []) if isinstance(hub, dict) else (hub or [])
    hub_meta, hubs = {}, []
    for r in hub_rows:
        k = str(r.get("hub_key") or "")
        if not k:
            continue
        vc = int(r.get("vendor_count") or 0)
        hub_meta[k] = {"display_name": r.get("display_name") or k, "vendor_count": vc,
                       "venue_count": int(r.get("venue_count") or 0)}
        hubs.append((k, vc))
    hubs.sort(key=lambda x: -x[1])
    n, tiers = len(hubs), {}
    for i, (k, _c) in enumerate(hubs):
        tiers[k] = ("High density" if i < n / 3
                    else "Medium density" if i < 2 * n / 3 else "Low density")
    return tiers, hub_meta


@st.cache_data(ttl=21600, show_spinner="Loading cohort snapshot (first load ~30s)…")
def load_users(xano_base, secret):
    rows, page = [], 1
    while page <= 60:
        d = _xget(f"{xano_base}/analytics_users_export?secret={secret}&page={page}&per_page=1000")
        batch = d.get("items", []) if isinstance(d, dict) else (d or [])
        if not batch:
            break
        rows.extend(batch)
        if not (isinstance(d, dict) and d.get("nextPage")):
            break
        page += 1
        time.sleep(0.25)
    return _build_df(rows), _dt.datetime.utcnow().isoformat()


# ── Metrics / filters / aggregations ─────────────────────────────────────────────
def _metrics(sub):
    n = int(len(sub))
    if n == 0:
        return dict(signups=0, payers=0, payment_rate=0.0, arpu=0.0, rev_per_signup=0.0,
                    revenue=0.0, viewed_vendor=0, viewed_pdf=0, view_rate=0.0, pdf_rate=0.0)
    payers = int(sub["is_payer"].sum())
    amt = float(sub["total_amount_paid"].sum())
    vv = int(sub["viewed_vendor"].sum())
    vp = int(sub["viewed_pdf"].sum())
    return dict(
        signups=n, payers=payers,
        payment_rate=payers / n,
        arpu=(amt / payers) if payers else 0.0,          # revenue per PAYING user
        rev_per_signup=amt / n,                          # blended
        revenue=amt, viewed_vendor=vv, viewed_pdf=vp,
        view_rate=vv / n, pdf_rate=vp / n,
    )


def _apply_filters(df, F):
    m = pd.Series(True, index=df.index)
    for key in ("planning_phase", "age_range", "referral", "intent", "hub_key",
                "density_tier", "budget_bucket", "guest_bucket"):
        vals = F.get(key)
        if vals:
            m &= df[key].astype(str).isin([str(v) for v in vals])
    for key, col in (("min_vendor_views", "total_vendor_views"),
                     ("min_pdf_views", "total_pdf_views")):
        v = F.get(key) or 0
        if v:
            m &= df[col] >= v
    sf, st_ = F.get("signup_from"), F.get("signup_to")
    if sf:
        d = pd.to_datetime(sf, errors="coerce")
        if pd.notna(d):
            m &= df["signup_dt"] >= d
    if st_:
        d = pd.to_datetime(st_, errors="coerce")
        if pd.notna(d):
            m &= df["signup_dt"] < d + pd.Timedelta(days=1)
    return df[m]


def _insights(sub):
    n_base = int(len(sub))
    base_payers = int(sub["is_payer"].sum())
    base_rate = base_payers / n_base if n_base else 0.0
    dims = ["density_tier", "hub_key", "referral", "intent", "planning_phase",
            "age_range", "budget_bucket", "guest_bucket", "signup_month"]
    movers = []
    for dim in dims:
        for val, grp in sub.groupby(sub[dim].astype(str)):
            gn = int(len(grp))
            payers = int(grp["is_payer"].sum())
            if gn < 200 or payers < 5 or str(val) in ("Unknown", ""):
                continue
            rate = payers / gn
            amt = float(grp["total_amount_paid"].sum())
            movers.append(dict(
                dimension=dim, group=str(val), n=gn, pay_rate=rate,
                lift=(rate / base_rate) if base_rate else 0.0,
                arpu=(amt / payers) if payers else 0.0, revenue=amt,
                wilson_lb=_wilson_lb(payers, gn),
                significant=_ztest(payers, gn, base_payers, n_base),
                direction="over" if rate >= base_rate else "under",
            ))
    over = sorted([m for m in movers if m["direction"] == "over"],
                  key=lambda m: -m["wilson_lb"])[:8]
    under = sorted([m for m in movers if m["direction"] == "under"],
                   key=lambda m: m["pay_rate"])[:5]
    return base_rate, n_base, over, under


def _funnel(sub):
    m = _metrics(sub)
    steps = [("Signed up", m["signups"]), ("Viewed a vendor", m["viewed_vendor"]),
             ("Viewed a PDF", m["viewed_pdf"]), ("Paid", m["payers"])]
    return pd.DataFrame(steps, columns=["step", "count"])


def _density(sub, hub_meta):
    hsub = sub[sub["hub_mapped"]]
    pts = []
    for val, grp in hsub.groupby(hsub["hub_key"].astype(str)):
        gn = int(len(grp))
        if gn < 100:
            continue
        hm = hub_meta.get(val, {})
        payers = int(grp["is_payer"].sum())
        amt = float(grp["total_amount_paid"].sum())
        pts.append(dict(hub_key=val, vendor_count=hm.get("vendor_count", 0), n=gn,
                        pay_rate_pct=round(payers / gn * 100, 2),
                        arpu=round(amt / payers, 2) if payers else 0.0))
    d = pd.DataFrame(pts)
    if d.empty:
        return d, None, None
    return (d,
            _pearson(d["vendor_count"].tolist(), d["pay_rate_pct"].tolist()),
            _pearson(d["vendor_count"].tolist(), d["arpu"].tolist()))


def _table(sub, dim):
    total_rev = float(sub["total_amount_paid"].sum()) or 1.0
    rows = []
    for val, grp in sub.groupby(sub[dim].astype(str)):
        m = _metrics(grp)
        rows.append({dim: val, "signups": m["signups"],
                     "pay_rate_%": round(m["payment_rate"] * 100, 2),
                     "ARPU_$": round(m["arpu"], 2), "revenue_$": round(m["revenue"], 0),
                     "rev_share_%": round(m["revenue"] / total_rev * 100, 1),
                     "viewed_pdf_%": round(m["pdf_rate"] * 100, 1), "payers": m["payers"]})
    return pd.DataFrame(rows).sort_values("signups", ascending=False).head(40)


def _retention(sub, cohort_by):
    horizons = [1, 7, 14, 30, 60, 90, 180]
    s = sub.copy()
    s["days_active"] = (s["last_active_dt"] - s["signup_dt"]).dt.days
    counts = s.groupby(s[cohort_by].astype(str)).size().sort_values(ascending=False)
    series = {}
    for val in counts.head(12).index:
        grp = s[s[cohort_by].astype(str) == val]
        valid = grp[grp["last_active_dt"].notna()]
        if len(valid) < 10:
            continue
        series[f"{val} (n={len(valid)})"] = [round(float((valid["days_active"] >= h).mean()) * 100, 1)
                                             for h in horizons]
    # numeric index → st.line_chart orders the x-axis ascending (1→180), not lexically
    piv = pd.DataFrame(series, index=pd.Index(horizons, name="days since signup"))
    return piv


# ── UI ───────────────────────────────────────────────────────────────────────────
def _opts(df, col):
    return sorted([v for v in df[col].astype(str).unique() if v not in ("", "nan")])


def _funnel_html(sub):
    """Horizontal funnel bars with the count + % printed above each bar."""
    m = _metrics(sub)
    signups = m["signups"] or 1
    steps = [("Signed up", m["signups"]), ("Viewed a vendor", m["viewed_vendor"]),
             ("Viewed a PDF", m["viewed_pdf"]), ("Paid", m["payers"])]
    html, prev = [], None
    for label, cnt in steps:
        pct = cnt / signups * 100
        of_prev = "" if prev in (None, 0) else f" · {cnt / prev * 100:.0f}% of previous step"
        html.append(
            f'<div style="margin:10px 0">'
            f'<div style="font-size:13px;margin-bottom:3px"><b>{label}</b> — {cnt:,} '
            f'<span style="color:#4B5563">({pct:.1f}% of signups{of_prev})</span></div>'
            f'<div style="background:#eef2f0;border-radius:6px;height:22px">'
            f'<div style="width:{max(pct, 1.5):.1f}%;background:#0F7348;height:22px;'
            f'border-radius:6px"></div></div></div>'
        )
        prev = cnt or prev
    return "<div>" + "".join(html) + "</div>"


def _style_table(df):
    """Per-column transparent→green heatmap, black text for legibility. No matplotlib."""
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]

    def _grad(col):
        v = col.astype(float)
        lo, hi = v.min(), v.max()
        rng = (hi - lo) or 1.0
        return [f"background-color: rgba(15,115,72,{0.55 * (x - lo) / rng:.2f}); color:#111"
                for x in v]

    sty = df.style
    if num_cols:
        sty = sty.apply(_grad, axis=0, subset=num_cols)
    return sty


def render_cohorts_tab(xano_base):
    st.markdown("### 📊 Cohort & Funnel Analytics")
    st.caption("revenue = signups × payment rate × ARPU · thesis: geographic-hub vendor density lifts all three")

    users_df, as_of = load_users(xano_base, EXPORT_SECRET)
    top = st.columns([4, 1])
    if users_df is None or users_df.empty:
        top[0].warning("Snapshot empty or pull failed (Xano may be briefly unreachable).")
        if top[1].button("↻ Retry"):
            load_users.clear(); st.rerun()
        return
    tiers, hub_meta = load_hubs(xano_base, EXPORT_SECRET)
    if not hub_meta:                       # a transient hub blip cached empty — self-heal
        load_hubs.clear()
        tiers, hub_meta = load_hubs(xano_base, EXPORT_SECRET)

    adf = users_df[~users_df["role_l"].isin(_ROLE_EXCLUDE)].copy()
    adf["density_tier"] = [
        tiers.get(str(k), "Low density") if mapped else "Unmapped"
        for k, mapped in zip(adf["hub_key"], adf["hub_mapped"])
    ]
    top[0].caption(f"Snapshot {as_of[:16].replace('T', ' ')} UTC · {len(adf):,} users (excl. admin/test)")
    if top[1].button("↻ Refresh data"):
        load_users.clear(); load_hubs.clear(); st.rerun()

    with st.expander("Filters", expanded=False):
        c = st.columns(4)
        F = {
            "planning_phase": c[0].multiselect("Planning phase", _opts(adf, "planning_phase")),
            "age_range":      c[1].multiselect("Age", _opts(adf, "age_range")),
            "density_tier":   c[2].multiselect("Hub density", _opts(adf, "density_tier")),
            "referral":       c[3].multiselect("Referral", _opts(adf, "referral")),
        }
        c2 = st.columns(4)
        F["intent"]        = c2[0].multiselect("Intent", _opts(adf, "intent"))
        F["budget_bucket"] = c2[1].multiselect("Budget", _opts(adf, "budget_bucket"))
        F["guest_bucket"]  = c2[2].multiselect("Guests", _opts(adf, "guest_bucket"))
        hub_keys           = sorted(adf.loc[adf["hub_mapped"], "hub_key"].dropna().astype(str).unique())
        F["hub_key"]       = c2[3].multiselect(
            "Hub", hub_keys,
            format_func=lambda k: hub_meta.get(k, {}).get("display_name", k))
        c3 = st.columns(4)
        F["min_vendor_views"] = c3[0].number_input("Min vendor views", 0, value=0, step=1)
        F["min_pdf_views"]    = c3[1].number_input("Min PDF views", 0, value=0, step=1)
        F["signup_from"]      = c3[2].text_input("Signup from (YYYY-MM-DD)", "")
        F["signup_to"]        = c3[3].text_input("Signup to (YYYY-MM-DD)", "")

    sub = _apply_filters(adf, F)
    if sub.empty:
        st.info("No users match these filters.")
        return
    m = _metrics(sub)

    t = st.columns(3)
    t[0].metric("Signups", f"{m['signups']:,}")
    t[1].metric("Payment rate", f"{m['payment_rate']*100:.2f}%")
    t[2].metric("ARPU (per payer)", f"${m['arpu']:,.2f}")
    t2 = st.columns(3)
    t2[0].metric("Revenue", f"${m['revenue']:,.0f}")
    t2[1].metric("Rev / signup", f"${m['rev_per_signup']:,.2f}")
    t2[2].metric("Viewed a PDF", f"{m['pdf_rate']*100:.1f}%")

    # Funnel
    st.markdown("#### Funnel")
    st.markdown(_funnel_html(sub), unsafe_allow_html=True)

    # Auto-insights (simplified: a few numbers + a short explanation)
    st.markdown("#### 🔎 Auto-insights")
    base_rate, base_n, over, under = _insights(sub)
    st.caption(f"compared to the {base_rate*100:.1f}% average pay rate across the {base_n:,} selected users")
    if over:
        st.markdown("**Segments paying MORE than average**")
        for x in over[:3]:
            st.markdown(f"- **{x['group']}** ({x['dimension']}) — **{x['pay_rate']*100:.1f}%** pay, "
                        f"**{x['lift']:.1f}× the average** · {x['n']:,} users")
    if under:
        st.markdown("**Segments paying LESS than average**")
        for x in under[:2]:
            st.markdown(f"- **{x['group']}** ({x['dimension']}) — **{x['pay_rate']*100:.1f}%** pay, "
                        f"**{x['lift']:.1f}× the average** · {x['n']:,} users")
    if not over and not under:
        st.caption("No segment clears the minimum sample size (200) in this slice.")

    # Hub density scatter
    st.markdown("#### Hub density → payment (thesis)")
    ddf, r_pay, r_arpu = _density(sub, hub_meta)
    if ddf.empty:
        st.info("No hubs with ≥100 users in this slice (or hub-density data is briefly unavailable — try ↻ Refresh).")
    else:
        st.caption(f"each dot = one hub · x = vendors in that hub, y = % who pay · "
                   f"correlation r = {r_pay} (pay rate), {r_arpu} (ARPU)")
        st.scatter_chart(ddf, x="vendor_count", y="pay_rate_pct", color="hub_key")
        st.dataframe(ddf, hide_index=True, use_container_width=True)

    # Cohort comparison (green heatmap table)
    st.markdown("#### Cohort comparison")
    gb = st.selectbox("Group by", _GROUP_OPTS, index=0, key="co_gb")
    tdf = _table(sub, gb).reset_index(drop=True)
    st.caption("Greener = higher within each column.")
    st.dataframe(_style_table(tdf), hide_index=True, use_container_width=True)

    # Retention
    st.markdown("#### Retention — last-active survival")
    cb = st.selectbox("Cohort by", _GROUP_OPTS, index=_GROUP_OPTS.index("signup_month"), key="co_cb")
    st.caption("Of each cohort's users who have any recorded session, the % still active at least "
               "N days after signup (x-axis: days since signup, ascending). Higher lines = better retention.")
    rpiv = _retention(sub, cb)
    if rpiv.empty:
        st.info("Not enough users with recorded activity for a retention curve in this slice.")
    else:
        st.line_chart(rpiv)

    st.caption(
        f"💵 ARPU = revenue ÷ paying user (dollars). ⏱ Retention only reflects activity since "
        f"{_BACKFILL_START} (when last-active tracking went live), over users with a recorded "
        f"session. Snapshot refreshes every 6h; use ↻ Refresh to force."
    )
