// 📈 Cohorts — port of cohorts.py (Streamlit). Same metrics, insights, density
// correlation, cohort table and retention curves; all computed in the browser from one
// snapshot, so changing a filter updates the page instantly with no reload.

import { api } from "./app.js";
import { GROUP_OPTS, HORIZONS, BACKFILL_START, decode, metrics, insights, density, cohortTable, retention, applyFilters, opts } from "./cohorts_logic.js";
import { h, metric, notice, panel, multiSelect, table, fmtInt, fmtMoney, fmtPct, fmtNum, chartLib, PALETTE, loading, ago, downloadCsv } from "./ui.js";

function funnel(m) {
  const signups = m.signups || 1;
  const steps = [["Signed up", m.signups], ["Viewed a vendor", m.viewed_vendor], ["Viewed a PDF", m.viewed_pdf], ["Paid", m.payers]];
  let prev = null;
  return h("div", { class: "funnel" }, steps.map(([label, cnt]) => {
    const pct = (cnt / signups) * 100;
    const ofPrev = prev ? ` · ${((cnt / prev) * 100).toFixed(0)}% of previous step` : "";
    prev = cnt || prev;
    return h("div", { class: "step" },
      h("div", { class: "lbl" }, h("b", {}, label), ` — ${fmtInt(cnt)} `, h("span", { class: "pct" }, `(${pct.toFixed(1)}% of signups${ofPrev})`)),
      h("div", { class: "track" }, h("div", { class: "bar", style: { width: Math.max(pct, 1.5).toFixed(1) + "%" } })));
  }));
}

// ── mount ──────────────────────────────────────────────────────────────────────
export async function mount(root) {
  let snap, rows, hubMeta;
  const F = {};
  let gb = "density_tier", cb = "signup_month";
  const charts = {};

  const header = h("div", { class: "row" });
  const filtersBox = h("div");
  const out = h("div");
  root.replaceChildren(
    h("h2", {}, "📊 Cohort & Funnel Analytics"),
    h("div", { class: "caption" }, "revenue = signups × payment rate × ARPU · thesis: geographic-hub vendor density lifts all three"),
    explainer(), header, filtersBox, out);

  async function load(refresh = false) {
    out.replaceChildren(loading(refresh ? "Rebuilding snapshot from Xano (~30 s)…" : "Loading cohort snapshot…"));
    try {
      if (refresh) {
        const before = (await api("/cohorts")).as_of;
        await api("/cohorts?refresh=1");
        for (let i = 0; i < 40; i++) {
          await new Promise((r) => setTimeout(r, 3000));
          snap = await api("/cohorts");
          if (snap.as_of !== before) break;
        }
      } else snap = await api("/cohorts");
    } catch (e) {
      out.replaceChildren(notice("warn", "Snapshot empty or pull failed (Xano may be briefly unreachable): ", e.message, " ",
        h("button", { onclick: () => load() }, "↻ Retry")));
      return;
    }
    rows = decode(snap); hubMeta = snap.hub_meta || {};
    header.replaceChildren(
      h("div", { class: "caption" }, `Snapshot ${snap.as_of.slice(0, 16).replace("T", " ")} UTC (${ago(snap.as_of)}) · ${fmtInt(rows.length)} users (excl. admin/test) · refreshes every 6h in the background`),
      h("div", { class: "spacer" }),
      h("button", { onclick: () => load(true) }, "↻ Refresh data"));
    buildFilters();
    render();
  }

  function buildFilters() {
    const set = (k) => (v) => { F[k] = v; render(); };
    const num = (k, label) => h("label", { class: "field" }, label,
      h("input", { type: "number", min: 0, value: 0, oninput: (e) => { F[k] = Number(e.target.value) || 0; render(); } }));
    const date = (k, label) => h("label", { class: "field" }, label,
      h("input", { type: "date", onchange: (e) => { F[k] = e.target.value; render(); } }));
    const hubKeys = opts(rows.filter((r) => r.hub_mapped === "True"), "hub_key");
    filtersBox.replaceChildren(panel("Filters", h("div", { class: "grid g4" },
      multiSelect("Planning phase", opts(rows, "planning_phase"), set("planning_phase")),
      multiSelect("Age", opts(rows, "age_range"), set("age_range")),
      multiSelect("Hub density", opts(rows, "density_tier"), set("density_tier")),
      multiSelect("Referral", opts(rows, "referral"), set("referral")),
      multiSelect("Customization", opts(rows, "customization"), set("customization")),
      multiSelect("Budget", opts(rows, "budget_bucket"), set("budget_bucket")),
      multiSelect("Guests", opts(rows, "guest_bucket"), set("guest_bucket")),
      multiSelect("Hub", hubKeys, set("hub_key"), (k) => hubMeta[k]?.display_name || k),
      num("min_vendor_views", "Min vendor views"), num("min_pdf_views", "Min PDF views"),
      date("signup_from", "Signup from"), date("signup_to", "Signup to"))));
  }

  async function render() {
    const sub = applyFilters(rows, F);
    for (const c of Object.values(charts)) c.destroy();
    if (!sub.length) { out.replaceChildren(notice("info", "No users match these filters.")); return; }
    const m = metrics(sub);
    const ins = insights(sub);
    const dens = density(sub, hubMeta);

    const scatterCanvas = h("canvas"), retCanvas = h("canvas");
    const tableBox = h("div"), retBox = h("div");
    const gbSel = h("select", { onchange: (e) => { gb = e.target.value; paintTable(); } }, GROUP_OPTS.map((o) => h("option", { value: o, selected: o === gb }, o)));
    const cbSel = h("select", { onchange: (e) => { cb = e.target.value; paintRet(); } }, GROUP_OPTS.map((o) => h("option", { value: o, selected: o === cb }, o)));
    const moverLine = (x) => h("li", {}, h("b", {}, x.dimension === "hub_key" ? (hubMeta[x.group]?.display_name || x.group) : x.group), ` (${x.dimension}) — `,
      h("b", {}, `${(x.pay_rate * 100).toFixed(1)}%`), " pay, ", h("b", {}, `${x.lift.toFixed(1)}× the average`), ` · ${fmtInt(x.n)} users`);

    out.replaceChildren(h("div", {},
      h("div", { class: "grid g3" },
        metric("Signups", fmtInt(m.signups)), metric("Payment rate", fmtPct(m.payment_rate, 2)), metric("ARPU (per payer)", fmtMoney(m.arpu, 2)),
        metric("Revenue", fmtMoney(m.revenue)), metric("Rev / signup", fmtMoney(m.rev_per_signup, 2)), metric("Viewed a PDF", fmtPct(m.pdf_rate))),
      h("h4", {}, "Views per person"),
      h("div", { class: "grid g4" },
        metric("Vendor views / user", fmtNum(m.vendor_views_pp)),
        metric("Vendor views / viewer", fmtNum(m.vendor_views_pp_engaged), "Averaged over users who viewed at least one vendor."),
        metric("PDF views / user", fmtNum(m.pdf_views_pp)),
        metric("PDF views / PDF viewer", fmtNum(m.pdf_views_pp_engaged), "Averaged over users who viewed at least one PDF.")),
      h("div", { class: "caption" }, "Lifetime counters on the Xano user row, not a time series — good for comparing cohorts, useless as a weekly trend. Two known biases: ",
        h("code", {}, "total_vendor_views"), " (ep150) dedupes only CONSECUTIVE views, so A→B→A counts twice; and ", h("code", {}, "total_pdf_views"),
        " under-counts mobile, where the View-PDF workflows patch ", h("code", {}, "total_views"), " instead."),
      h("h4", {}, "Funnel"), funnel(m),
      h("h4", {}, "🔎 Auto-insights"),
      h("div", { class: "caption" }, `compared to the ${(ins.baseRate * 100).toFixed(1)}% average pay rate across the ${fmtInt(ins.nBase)} selected users`),
      ins.over.length ? [h("b", {}, "Segments paying MORE than average"), h("ul", {}, ins.over.slice(0, 3).map(moverLine))] : null,
      ins.under.length ? [h("b", {}, "Segments paying LESS than average"), h("ul", {}, ins.under.slice(0, 2).map(moverLine))] : null,
      !ins.over.length && !ins.under.length ? h("div", { class: "caption" }, "No segment clears the minimum sample size (200) in this slice.") : null,
      h("h4", {}, "Hub density → payment (thesis)"),
      dens.pts.length
        ? [h("div", { class: "caption" }, `each dot = one hub · x = vendors in that hub, y = % who pay · correlation r = ${dens.rPay} (pay rate), ${dens.rArpu} (ARPU)`),
           h("div", { class: "card chart-box" }, scatterCanvas), h("div", { style: { marginTop: "10px" } },
             table(dens.pts, [{ key: "hub", label: "hub" }, { key: "vendor_count", num: true, fmt: fmtInt }, { key: "n", num: true, fmt: fmtInt },
               { key: "pay_rate_pct", num: true }, { key: "arpu", num: true }]))]
        : notice("info", "No hubs with ≥100 users in this slice (or hub-density data is briefly unavailable — try ↻ Refresh)."),
      h("h4", {}, "Cohort comparison"),
      h("div", { class: "row" }, h("label", { class: "field" }, "Group by", gbSel), h("div", { class: "spacer" }),
        h("button", { onclick: () => downloadCsv(`cohorts_by_${gb}.csv`, cohortTable(sub, gb, hubMeta)) }, "⤓ CSV")),
      h("div", { class: "caption" }, "Greener = higher within each column. Click a header to sort."), tableBox,
      h("h4", {}, "Retention — last-active survival"),
      h("label", { class: "field", style: { maxWidth: "260px" } }, "Cohort by", cbSel),
      h("div", { class: "caption" }, "Of each cohort's users who have any recorded session, the % still active at least N days after signup. Higher lines = better retention."),
      retBox,
      h("div", { class: "caption", style: { marginTop: "16px" } }, `💵 ARPU = revenue ÷ paying user (dollars). ⏱ Retention only reflects activity since ${BACKFILL_START} (when last-active tracking went live), over users with a recorded session.`)));

    function paintTable() {
      const t = cohortTable(sub, gb, hubMeta);
      const cols = Object.keys(t[0] || {}).map((k, i) => (i === 0 ? { key: k } : { key: k, num: true, heat: true, fmt: (v) => (typeof v === "number" ? v.toLocaleString("en-US") : v) }));
      tableBox.replaceChildren(table(t, cols));
    }
    async function paintRet() {
      charts.ret?.destroy();
      const s = retention(sub, cb);
      if (!s.length) { retBox.replaceChildren(notice("info", "Not enough users with recorded activity for a retention curve in this slice.")); return; }
      retBox.replaceChildren(h("div", { class: "card chart-box" }, retCanvas));
      const Chart = await chartLib();
      charts.ret = new Chart(retCanvas, {
        type: "line",
        data: { datasets: s.map((d, i) => ({ label: d.label, data: HORIZONS.map((x, j) => ({ x, y: d.data[j] })), borderColor: PALETTE[i % PALETTE.length], backgroundColor: PALETTE[i % PALETTE.length], tension: 0.2 })) },
        options: { animation: false, maintainAspectRatio: false, scales: { x: { type: "linear", title: { display: true, text: "days since signup" } }, y: { title: { display: true, text: "% still active" } } }, plugins: { legend: { position: "right" } } },
      });
    }
    paintTable();
    paintRet();
    if (dens.pts.length) {
      const Chart = await chartLib();
      charts.scatter = new Chart(scatterCanvas, {
        type: "scatter",
        data: { datasets: dens.pts.map((p, i) => ({ label: p.hub, data: [{ x: p.vendor_count, y: p.pay_rate_pct }], backgroundColor: PALETTE[i % PALETTE.length], pointRadius: 6 })) },
        options: { animation: false, maintainAspectRatio: false, scales: { x: { title: { display: true, text: "vendors in hub" } }, y: { title: { display: true, text: "% who pay" } } }, plugins: { legend: { position: "right" } } },
      });
    }
  }

  await load();
}

function explainer() {
  return panel("ℹ️ What is this dashboard?", h("div", { html: `
<p><b>Tulle Admin</b> is the internal growth + ops tool for <a href="https://tulletogether.app">tulletogether.app</a>.</p>
<p>The business in one line: <b>Revenue = signups × payment rate × ARPU</b>. The thesis this tool tests: building out
<b>geographic hubs</b> — more pricing across more vendors in specific areas — lifts all three.</p>
<table class="tbl" style="max-width:820px">
<tr><th>Tab</th><th>What it does</th></tr>
<tr><td>📈 Cohorts</td><td>Slice the user base into cohorts and compare funnel, payment, and retention metrics.</td></tr>
<tr><td>💰 Venue Pricing</td><td>Approximate all-in venue cost by guest count, from extracted pricing (State × venue type).</td></tr>
<tr><td>🔍 Google Data &amp; Images</td><td>Cache Google Places data (rating, reviews, address) and pull vendor photos. Shared daily quota.</td></tr>
<tr><td>📄 PDF Extraction</td><td>Production extraction queue — Claude reads each PDF and posts structured rows to Xano.</td></tr>
<tr><td>🩺 Health</td><td>Dependency checks, the daily search report, and endpoint performance against budgets.</td></tr>
</table>` }));
}
