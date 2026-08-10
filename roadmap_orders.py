"""
🗺️ Budget Roadmap Orders — fulfilment queue for the Tulle admin dashboard.

Self-contained: dashboard.py just does
    from roadmap_orders import render_roadmap_orders_tab
    with tab_ro:
        render_roadmap_orders_tab(XANO_BASE)

Backs the $75 Budget Roadmap product (custom wedding-budget PDF, promised in 5-7
BUSINESS days). Reads/writes two secret-gated Xano endpoints in the "WeWeb Transparency
Project" group:
    GET  /roadmap_orders_admin          (ep 213) — every order, newest first
    POST /roadmap_orders_admin_update   (ep 214) — status / pdf_url / admin_notes

Why a dedicated admin endpoint: the customer-facing endpoints (209/210/211) are
auth=user and owner-scoped, so ops has no way to see the queue through them.

The order row is created BEFORE Stripe checkout, so this table also contains
`draft` / `checkout_started` rows — those are abandoned carts, not work items. They're
shown separately at the bottom because they're the only measurement of funnel leak that
survives a Mixpanel outage.

Deliberately NOT cached for long (60s): this is a live fulfilment queue and a stale
read means a missed order.
"""

import os
import time
import datetime as _dt

import numpy as np
import requests
import pandas as pd
import streamlit as st

EXPORT_SECRET = os.environ.get(
    "ANALYTICS_EXPORT_SECRET",
    "ttv_export_da19ae7c3fbcdd2c51747199117a63a33f848ca9",
)

# Promised turnaround. Orders older than this (in business days) are chased.
SLA_BUSINESS_DAYS = 7

# Work items vs. everything else. draft/checkout_started never got paid.
ACTIVE_STATUSES = ["paid", "in_progress"]
INCOMPLETE_STATUSES = ["draft", "checkout_started"]

_STATUS_LABEL = {
    "draft": "📝 Draft (never paid)",
    "checkout_started": "🛒 Checkout started (never paid)",
    "paid": "💰 Paid — not started",
    "in_progress": "🔨 In progress",
    "delivered": "✅ Delivered",
    "abandoned": "🚫 Abandoned",
    "refunded": "↩️ Refunded",
}


# ── HTTP (retry transient 000/5xx, same shape as cohorts.py) ────────────────────
def _xget(url, tries=4, timeout=30):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None                      # 4xx won't self-heal
        except Exception:                        # noqa: BLE001
            pass
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return None


def _xpost(url, payload, tries=4, timeout=30):
    """Returns (ok, parsed_or_error_text). Retries 5xx/network only — a fulfilment
    write must survive a Xano 503 blip, which this app has hit before."""
    last = "no response"
    for i in range(tries):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            if r.status_code == 200:
                return True, r.json()
            if r.status_code < 500:
                return False, f"HTTP {r.status_code}: {r.text[:300]}"
            last = f"HTTP {r.status_code}"
        except Exception as e:                   # noqa: BLE001
            last = str(e)
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    return False, last


# ── Time helpers (Xano hands back epoch MILLIseconds; 0/None means unset) ───────
def _ms_to_dt(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return _dt.datetime.utcfromtimestamp(v / 1000.0)


def _fmt_dt(v, fmt="%Y-%m-%d %H:%M"):
    d = _ms_to_dt(v)
    return d.strftime(fmt) if d else "—"


def _business_days_since(v):
    """Business days between a ms timestamp and now. numpy.busday_count is
    half-open and Mon-Fri by default, which is exactly the SLA definition."""
    d = _ms_to_dt(v)
    if not d:
        return None
    return int(np.busday_count(d.date(), _dt.datetime.utcnow().date()))


def _money(n):
    try:
        return f"${float(n):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _listish(v):
    """states / priorities / days_of_week / seasons come back as lists.
    priorities is RANK-ORDERED — never sort it."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    return str(v or "")


# ── Data ────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60, show_spinner=False)
def load_orders(xano_base, secret):
    """All orders, newest first. Paged defensively even though volume is tiny today."""
    rows, page = [], 1
    while page <= 20:                            # hard stop; 20 × 500 = 10k orders
        d = _xget(f"{xano_base}/roadmap_orders_admin"
                  f"?secret={secret}&page={page}&per_page=500")
        if not d:
            break
        items = d.get("items", []) if isinstance(d, dict) else d
        if not items:
            break
        rows.extend(items)
        if not (isinstance(d, dict) and d.get("nextPage")):
            break
        page += 1
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for col in ("status", "user_name", "user_email", "source", "pdf_url",
                "admin_notes", "set_budget", "other_details"):
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")
    for col in ("guest_count", "budget_amount", "amount_paid"):
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ("created_at", "paid_at", "delivered_at"):
        if col not in df.columns:
            df[col] = 0

    df["age_bdays"] = df["paid_at"].map(_business_days_since)
    return df


def _update_order(xano_base, order_id, **fields):
    payload = {"secret": EXPORT_SECRET, "order_id": int(order_id)}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return _xpost(f"{xano_base}/roadmap_orders_admin_update", payload)


# ── UI ──────────────────────────────────────────────────────────────────────────
def render_roadmap_orders_tab(xano_base):
    st.markdown("### 🗺️ Budget Roadmap Orders")
    st.caption(f"$75 custom budget roadmap · promised in {SLA_BUSINESS_DAYS} business days "
               "· order rows are created before checkout, so unpaid drafts appear at the bottom")

    df = load_orders(xano_base, EXPORT_SECRET)
    top = st.columns([4, 1])
    if df.empty:
        top[0].info("No roadmap orders yet. (If you expected some, Xano may be briefly "
                    "unreachable — hit Refresh.)")
        if top[1].button("↻ Refresh", key="ro_refresh_empty"):
            load_orders.clear(); st.rerun()
        return
    if top[1].button("↻ Refresh", key="ro_refresh"):
        load_orders.clear(); st.rerun()

    paid_df = df[df["status"].isin(ACTIVE_STATUSES)]
    delivered_df = df[df["status"] == "delivered"]
    overdue_df = paid_df[paid_df["age_bdays"].fillna(0) > SLA_BUSINESS_DAYS]

    # Delivered in the last 7 calendar days
    week_ago = _dt.datetime.utcnow() - _dt.timedelta(days=7)
    delivered_7d = sum(
        1 for v in delivered_df["delivered_at"]
        if (_ms_to_dt(v) or _dt.datetime(1970, 1, 1)) >= week_ago
    )

    # Avg business days paid -> delivered, over delivered orders that have both stamps
    spans = []
    for _, r in delivered_df.iterrows():
        p, d = _ms_to_dt(r["paid_at"]), _ms_to_dt(r["delivered_at"])
        if p and d:
            spans.append(int(np.busday_count(p.date(), d.date())))
    avg_span = f"{np.mean(spans):.1f}" if spans else "—"

    revenue = float(delivered_df["amount_paid"].sum() + paid_df["amount_paid"].sum())

    m = st.columns(5)
    m[0].metric("Awaiting fulfilment", len(paid_df))
    m[1].metric("Delivered (7d)", delivered_7d)
    m[2].metric("Avg business days to deliver", avg_span)
    m[3].metric(f"⚠️ Overdue (>{SLA_BUSINESS_DAYS}bd)", len(overdue_df))
    m[4].metric("Paid revenue", _money(revenue))

    if len(overdue_df):
        names = ", ".join(f"#{int(r.id)} {r.user_email}" for r in overdue_df.itertuples())
        st.error(f"**{len(overdue_df)} order(s) past the {SLA_BUSINESS_DAYS}-business-day "
                 f"promise:** {names}")

    st.markdown("---")

    # ── Queue ──────────────────────────────────────────────────────────────────
    c = st.columns([2, 2, 3])
    status_opts = ["Active (paid + in progress)", "All", "delivered",
                   "paid", "in_progress", "abandoned", "refunded"]
    choice = c[0].selectbox("Show", status_opts, index=0, key="ro_status")
    src_opts = ["All"] + sorted(x for x in df["source"].unique() if x)
    src = c[1].selectbox("Source", src_opts, index=0, key="ro_source")
    q = c[2].text_input("Search name / email", "", key="ro_search").strip().lower()

    if choice == "Active (paid + in progress)":
        view = df[df["status"].isin(ACTIVE_STATUSES)]
    elif choice == "All":
        view = df[~df["status"].isin(INCOMPLETE_STATUSES)]
    else:
        view = df[df["status"] == choice]
    if src != "All":
        view = view[view["source"] == src]
    if q:
        view = view[
            view["user_name"].str.lower().str.contains(q, na=False)
            | view["user_email"].str.lower().str.contains(q, na=False)
        ]

    if view.empty:
        st.info("No orders match those filters.")
    else:
        grid = pd.DataFrame({
            "ID": view["id"],
            "Status": view["status"].map(lambda s: _STATUS_LABEL.get(s, s)),
            "Name": view["user_name"],
            "Email": view["user_email"],
            "Guests": view["guest_count"].astype(int),
            "Budget": view["budget_amount"].map(_money),
            "States": view["states"].map(_listish),
            "Paid": view["paid_at"].map(lambda v: _fmt_dt(v, "%Y-%m-%d")),
            "Age (bd)": view["age_bdays"].map(lambda v: "—" if v is None else int(v)),
            "Source": view["source"],
        })
        st.dataframe(grid, use_container_width=True, hide_index=True)

    # ── Work one order ─────────────────────────────────────────────────────────
    st.markdown("### Work an order")
    workable = df[~df["status"].isin(INCOMPLETE_STATUSES)]
    if workable.empty:
        st.caption("No paid orders to work yet.")
    else:
        ids = list(workable["id"])
        sel = st.selectbox(
            "Order", ids, key="ro_sel",
            format_func=lambda i: (
                f"#{i} · {workable.loc[workable['id'] == i, 'user_name'].iloc[0]}"
                f" · {_STATUS_LABEL.get(workable.loc[workable['id'] == i, 'status'].iloc[0], '')}"
            ),
        )
        row = workable[workable["id"] == sel].iloc[0]

        d1, d2 = st.columns(2)
        with d1:
            st.markdown("**Their inputs**")
            st.markdown(
                f"- **Name:** {row['user_name']}\n"
                f"- **Email:** {row['user_email']}\n"
                f"- **Has a budget?:** {row['set_budget'] or '—'}\n"
                f"- **Budget:** {_money(row['budget_amount'])}\n"
                f"- **Guests:** {int(row['guest_count'])}\n"
                f"- **States:** {_listish(row.get('states'))}"
                f"{'  ·  major city' if row.get('major_city') else ''}\n"
                f"- **Priorities (ranked):** {_listish(row.get('priorities'))}\n"
                f"- **Days:** {_listish(row.get('days_of_week'))}\n"
                f"- **Seasons:** {_listish(row.get('seasons'))}"
            )
            if row.get("other_details"):
                st.markdown("**Other details**")
                st.markdown(row["other_details"], unsafe_allow_html=True)
        with d2:
            st.markdown("**Order**")
            age = row["age_bdays"]
            age_txt = "—" if age is None else f"{int(age)} business days"
            st.markdown(
                f"- **Status:** {_STATUS_LABEL.get(row['status'], row['status'])}\n"
                f"- **Paid:** {_fmt_dt(row['paid_at'])} ({age_txt} ago)\n"
                f"- **Amount:** {_money(row['amount_paid'])}\n"
                f"- **Delivered:** {_fmt_dt(row['delivered_at'])}\n"
                f"- **Source:** {row['source'] or '—'}\n"
                f"- **Stripe session:** `{row.get('stripe_session_id') or '—'}`"
            )

        pdf_url = st.text_input("Roadmap PDF URL", value=row.get("pdf_url") or "",
                                key=f"ro_pdf_{sel}",
                                help="Paste a shareable link to the finished PDF. "
                                     "Direct file upload needs a Xano storage endpoint "
                                     "we haven't built.")
        pdf_url = pdf_url.strip()
        # The delivery email is nothing but a link to this URL, and it can only ever be sent
        # once (delivered_at is stamped on the first transition). So a bad URL here means the
        # customer gets a dead button and no way to re-send. Xano enforces non-empty; the
        # http check lives here because XanoScript has no starts_with/strpos filter.
        pdf_url_ok = pdf_url.lower().startswith(("http://", "https://"))
        if pdf_url and not pdf_url_ok:
            st.warning("That doesn't look like a link — it needs to start with https://")
        notes = st.text_area("Internal notes (not shown to the customer)",
                             value=row.get("admin_notes") or "", key=f"ro_notes_{sel}")

        b = st.columns(4)
        if b[0].button("🔨 Mark in progress", key=f"ro_wip_{sel}"):
            ok, res = _update_order(xano_base, sel, status="in_progress",
                                    pdf_url=pdf_url or None, admin_notes=notes)
            if ok:
                load_orders.clear(); st.success("Marked in progress."); st.rerun()
            else:
                st.error(f"Update failed: {res}")

        deliver_disabled = not pdf_url_ok
        if b[1].button("✅ Mark delivered", key=f"ro_del_{sel}",
                       disabled=deliver_disabled,
                       help="Add a valid PDF link first — the delivery email is just this link, "
                            "and it can only be sent once" if deliver_disabled else
                            "Emails the customer, stamps delivered_at, fires roadmap_delivered"):
            ok, res = _update_order(xano_base, sel, status="delivered",
                                    pdf_url=pdf_url, admin_notes=notes)
            if ok:
                load_orders.clear(); st.success("Delivered."); st.rerun()
            else:
                st.error(f"Update failed: {res}")

        if b[2].button("💾 Save notes / PDF", key=f"ro_save_{sel}"):
            ok, res = _update_order(xano_base, sel, pdf_url=pdf_url or None,
                                    admin_notes=notes)
            if ok:
                load_orders.clear(); st.success("Saved."); st.rerun()
            else:
                st.error(f"Save failed: {res}")

        if b[3].button("↩️ Mark refunded", key=f"ro_ref_{sel}"):
            ok, res = _update_order(xano_base, sel, status="refunded",
                                    admin_notes=notes)
            if ok:
                load_orders.clear(); st.success("Marked refunded."); st.rerun()
            else:
                st.error(f"Update failed: {res}")

    # ── Funnel leak ────────────────────────────────────────────────────────────
    st.markdown("---")
    incomplete = df[df["status"].isin(INCOMPLETE_STATUSES)]
    paid_count = len(df[~df["status"].isin(INCOMPLETE_STATUSES)])
    started = len(incomplete) + paid_count
    conv = f"{(paid_count / started * 100):.0f}%" if started else "—"
    with st.expander(f"🛒 Started but never paid — {len(incomplete)} "
                     f"(form → payment conversion: {conv})", expanded=False):
        st.caption("These rows exist because the order is saved before Stripe. They are the "
                   "abandonment measurement, and they survive a Mixpanel outage.")
        if incomplete.empty:
            st.caption("Nothing abandoned yet.")
        else:
            st.dataframe(
                pd.DataFrame({
                    "ID": incomplete["id"],
                    "Status": incomplete["status"].map(lambda s: _STATUS_LABEL.get(s, s)),
                    "Name": incomplete["user_name"],
                    "Email": incomplete["user_email"],
                    "Guests": incomplete["guest_count"].astype(int),
                    "Budget": incomplete["budget_amount"].map(_money),
                    "Started": incomplete["created_at"].map(lambda v: _fmt_dt(v)),
                    "Source": incomplete["source"],
                }),
                use_container_width=True, hide_index=True,
            )
