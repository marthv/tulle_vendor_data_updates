// Pure cohort math (no DOM) — imported by cohorts.js and by the parity test.

export const GROUP_OPTS = ["density_tier", "hub_key", "hub_mapped", "signup_month", "planning_phase",
  "age_range", "referral", "customization", "budget_bucket", "guest_bucket"];
export const INSIGHT_DIMS = ["density_tier", "hub_key", "referral", "customization", "planning_phase",
  "age_range", "budget_bucket", "guest_bucket", "signup_month"];
export const HORIZONS = [1, 7, 14, 30, 60, 90, 180];
export const BACKFILL_START = "2026-07-21";
export const UNMAPPED = "(unmapped)";
export const DAY = 86400000;

// ── decode snapshot ────────────────────────────────────────────────────────────
export function decode(snap) {
  const { cols, dicts } = snap, n = snap.n, rows = new Array(n);
  for (let i = 0; i < n; i++) {
    const su = cols.signup[i];
    const hk = dicts.hub_key[cols.hub_key[i]];
    rows[i] = {
      planning_phase: dicts.planning_phase[cols.planning_phase[i]],
      age_range: dicts.age_range[cols.age_range[i]],
      referral: dicts.referral[cols.referral[i]],
      customization: dicts.customization[cols.customization[i]],
      budget_bucket: dicts.budget_bucket[cols.budget_bucket[i]],
      guest_bucket: dicts.guest_bucket[cols.guest_bucket[i]],
      density_tier: dicts.density_tier[cols.density_tier[i]],
      hub_key: hk || UNMAPPED,
      hub_mapped: hk ? "True" : "False",
      vendor_views: cols.vendor_views[i],
      pdf_views: cols.pdf_views[i],
      paid: cols.paid[i],
      is_payer: cols.payments[i] > 0,
      signup: su,
      last_active: cols.last_active[i],
      signup_month: su ? new Date(su).toISOString().slice(0, 7) : "nan",
    };
  }
  return rows;
}

// ── metrics (same definitions as cohorts._metrics) ────────────────────────────
export function metrics(sub) {
  const n = sub.length;
  let payers = 0, amt = 0, vv = 0, vp = 0, vsum = 0, psum = 0;
  for (const r of sub) {
    if (r.is_payer) payers++;
    amt += r.paid; vsum += r.vendor_views; psum += r.pdf_views;
    if (r.vendor_views > 0) vv++;
    if (r.pdf_views > 0) vp++;
  }
  if (!n) return { signups: 0, payers: 0, payment_rate: 0, arpu: 0, rev_per_signup: 0, revenue: 0, viewed_vendor: 0, viewed_pdf: 0, view_rate: 0, pdf_rate: 0, vendor_views_pp: 0, pdf_views_pp: 0, vendor_views_pp_engaged: 0, pdf_views_pp_engaged: 0 };
  return {
    signups: n, payers, payment_rate: payers / n, arpu: payers ? amt / payers : 0,
    rev_per_signup: amt / n, revenue: amt, viewed_vendor: vv, viewed_pdf: vp,
    view_rate: vv / n, pdf_rate: vp / n,
    vendor_views_pp: vsum / n, pdf_views_pp: psum / n,
    vendor_views_pp_engaged: vv ? vsum / vv : 0, pdf_views_pp_engaged: vp ? psum / vp : 0,
  };
}

export function groupBy(sub, key) {
  const m = new Map();
  for (const r of sub) { const k = String(r[key]); (m.get(k) || m.set(k, []).get(k)).push(r); }
  return m;
}

export function wilsonLb(k, n, z = 1.96) {
  if (!n) return 0;
  const p = k / n, denom = 1 + z * z / n, centre = p + z * z / (2 * n);
  const margin = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n));
  return Math.max(0, (centre - margin) / denom);
}
export function ztest(k1, n1, k2, n2) {
  if (!n1 || !n2) return false;
  const p = (k1 + k2) / (n1 + n2), se = Math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2));
  return !!se && Math.abs(k1 / n1 - k2 / n2) / se >= 1.96;
}
export function pearson(x, y) {
  const n = x.length;
  if (n < 3) return null;
  const mx = x.reduce((a, b) => a + b, 0) / n, my = y.reduce((a, b) => a + b, 0) / n;
  let sxy = 0, sxx = 0, syy = 0;
  for (let i = 0; i < n; i++) { const dx = x[i] - mx, dy = y[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
  if (!sxx || !syy) return null;
  return Math.round((sxy / Math.sqrt(sxx * syy)) * 1000) / 1000;
}

export function insights(sub) {
  const nBase = sub.length;
  const basePayers = sub.reduce((a, r) => a + (r.is_payer ? 1 : 0), 0);
  const baseRate = nBase ? basePayers / nBase : 0;
  const movers = [];
  for (const dim of INSIGHT_DIMS) {
    for (const [val, grp] of groupBy(sub, dim)) {
      const gn = grp.length, payers = grp.reduce((a, r) => a + (r.is_payer ? 1 : 0), 0);
      if (gn < 200 || payers < 5 || val === "Unknown" || val === "") continue;
      const rate = payers / gn, amt = grp.reduce((a, r) => a + r.paid, 0);
      movers.push({ dimension: dim, group: val, n: gn, pay_rate: rate, lift: baseRate ? rate / baseRate : 0,
        arpu: payers ? amt / payers : 0, revenue: amt, wilson_lb: wilsonLb(payers, gn),
        significant: ztest(payers, gn, basePayers, nBase), direction: rate >= baseRate ? "over" : "under" });
    }
  }
  const over = movers.filter((m) => m.direction === "over").sort((a, b) => b.wilson_lb - a.wilson_lb).slice(0, 8);
  const under = movers.filter((m) => m.direction === "under").sort((a, b) => a.pay_rate - b.pay_rate).slice(0, 5);
  return { baseRate, nBase, over, under };
}

export function density(sub, hubMeta) {
  const pts = [];
  for (const [val, grp] of groupBy(sub.filter((r) => r.hub_mapped === "True"), "hub_key")) {
    const gn = grp.length;
    if (gn < 100) continue;
    const payers = grp.reduce((a, r) => a + (r.is_payer ? 1 : 0), 0), amt = grp.reduce((a, r) => a + r.paid, 0);
    pts.push({ hub_key: val, hub: hubMeta[val]?.display_name || val, vendor_count: hubMeta[val]?.vendor_count || 0, n: gn,
      pay_rate_pct: Math.round((payers / gn) * 10000) / 100, arpu: payers ? Math.round((amt / payers) * 100) / 100 : 0 });
  }
  return { pts, rPay: pearson(pts.map((p) => p.vendor_count), pts.map((p) => p.pay_rate_pct)),
    rArpu: pearson(pts.map((p) => p.vendor_count), pts.map((p) => p.arpu)) };
}

export function cohortTable(sub, dim, hubMeta) {
  const totalRev = sub.reduce((a, r) => a + r.paid, 0) || 1;
  const rows = [];
  for (const [val, grp] of groupBy(sub, dim)) {
    const m = metrics(grp);
    rows.push({ [dim]: dim === "hub_key" ? (hubMeta[val]?.display_name || val) : val, signups: m.signups,
      "pay_rate_%": +(m.payment_rate * 100).toFixed(2), "ARPU_$": +m.arpu.toFixed(2), "revenue_$": Math.round(m.revenue),
      "rev_share_%": +((m.revenue / totalRev) * 100).toFixed(1), "viewed_pdf_%": +(m.pdf_rate * 100).toFixed(1),
      vendor_views_pp: +m.vendor_views_pp.toFixed(2), pdf_views_pp: +m.pdf_views_pp.toFixed(2), payers: m.payers });
  }
  return rows.sort((a, b) => b.signups - a.signups).slice(0, 40);
}

export function retention(sub, cohortBy) {
  const groups = [...groupBy(sub, cohortBy)].sort((a, b) => b[1].length - a[1].length).slice(0, 12);
  const series = [];
  for (const [val, grp] of groups) {
    const valid = grp.filter((r) => r.last_active != null);
    if (valid.length < 10) continue;
    const days = valid.map((r) => (r.signup != null ? Math.floor((r.last_active - r.signup) / DAY) : NaN));
    series.push({ label: `${val} (n=${valid.length})`,
      data: HORIZONS.map((hz) => Math.round((days.filter((d) => d >= hz).length / valid.length) * 1000) / 10) });
  }
  return series;
}

export function applyFilters(rows, F) {
  const from = F.signup_from ? Date.parse(F.signup_from + "T00:00:00Z") : null;
  const to = F.signup_to ? Date.parse(F.signup_to + "T00:00:00Z") + DAY : null;
  const sets = {};
  for (const k of ["planning_phase", "age_range", "referral", "customization", "hub_key", "density_tier", "budget_bucket", "guest_bucket"])
    if (F[k]?.length) sets[k] = new Set(F[k]);
  return rows.filter((r) => {
    for (const k in sets) if (!sets[k].has(r[k])) return false;
    if (F.min_vendor_views && r.vendor_views < F.min_vendor_views) return false;
    if (F.min_pdf_views && r.pdf_views < F.min_pdf_views) return false;
    if (from != null && !(r.signup != null && r.signup >= from)) return false;
    if (to != null && !(r.signup != null && r.signup < to)) return false;
    return true;
  });
}

export const opts = (rows, col) => [...new Set(rows.map((r) => r[col]))].filter((v) => v !== "" && v !== "nan" && v !== UNMAPPED).sort();

