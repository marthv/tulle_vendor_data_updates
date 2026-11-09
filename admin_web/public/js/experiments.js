// 🧪 Experiments — port of experiments_tab.py. Source of truth: experiments/experiment_log.json
// (edit + push to main). Confounds are computed (overlapping dates), and the significance check
// is the same two-proportion z-test, all in the browser.

import { api } from "./app.js";
import { h, notice, panel, table } from "./ui.js";

const STATUSES = ["planned", "live", "reading", "concluded", "reverted"];
const COLORS = { planned: "#CDD0D8", live: "#00A368", reading: "#D97706", concluded: "#0D5C3A", reverted: "#C53030" };
const GROUPS = { Running: ["planned", "live", "reading"], Past: ["concluded", "reverted"], All: STATUSES };

const day = (s) => { if (!s) return null; const d = new Date(s + "T00:00:00Z"); return isNaN(d) ? null : d; };
const todayUTC = () => { const n = new Date(); return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate())); };
const windowOf = (e, today) => { const s = day(e.start_date); return s ? [s, day(e.end_date) || today] : null; };
const overlaps = (a, b) => a[0] <= b[1] && b[0] <= a[1];
const iso = (d) => d.toISOString().slice(0, 10);
// The log is hand-edited: list fields are sometimes a plain string. Accept both.
const arr = (x) => (Array.isArray(x) ? x : x ? [String(x)] : []);

// Same maths as experiments_tab.two_proportion_test.
function erfc(x) {   // Abramowitz-Stegun 7.1.26 (|error| < 1.5e-7)
  const t = 1 / (1 + 0.3275911 * x);
  const y = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
  return y * Math.exp(-x * x);
}
export function twoProportionTest(n1, c1, n2, c2) {
  if (Math.min(n1, n2) <= 0 || !(c1 >= 0 && c1 <= n1 && c2 >= 0 && c2 <= n2)) return null;
  const p1 = c1 / n1, p2 = c2 / n2, pooled = (c1 + c2) / (n1 + n2);
  const sePooled = Math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2));
  if (!sePooled) return null;
  const z = (p2 - p1) / sePooled, pValue = erfc(Math.abs(z) / Math.SQRT2);
  const se = Math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2), diff = p2 - p1;
  return { control_rate: p1, treatment_rate: p2, lift: p1 ? diff / p1 : null,
    ci_low: diff - 1.96 * se, ci_high: diff + 1.96 * se, p_value: pValue, significant: pValue < 0.05 };
}
const pct = (x, d = 2) => (x * 100).toFixed(d) + "%";
const sgn = (x, d = 2) => (x >= 0 ? "+" : "") + pct(x, d);
function verdict(r) {
  if (!r) return "Not enough data for a test.";
  return h("span", {}, `Control ${pct(r.control_rate)} vs treatment ${pct(r.treatment_rate)} · relative lift ${r.lift == null ? "n/a" : sgn(r.lift, 1)} · difference 95% CI ${sgn(r.ci_low)} to ${sgn(r.ci_high)} · p = ${r.p_value.toFixed(3)} → `,
    h("b", {}, r.significant ? "significant (p < 0.05)" : "not significant"));
}

// Timeline: one bar per experiment on a shared date axis; logged changes as dashed lines.
function timeline(shown, changes, today) {
  const rows = shown.map((e) => ({ e, w: windowOf(e, today) })).filter((r) => r.w);
  if (!rows.length) return null;
  const ch = changes.map((c) => ({ c, d: day(c.date) })).filter((x) => x.d);
  let lo = Math.min(...rows.map((r) => r.w[0])), hi = Math.max(...rows.map((r) => r.w[1]));
  const span = Math.max(hi - lo, 86400000 * 7);
  lo -= span * 0.03; hi = lo + span * 1.06;
  const x = (d) => (((d - lo) / (hi - lo)) * 100).toFixed(2) + "%";
  const lines = ch.filter(({ d }) => d >= lo && d <= hi).map(({ c, d }) =>
    h("div", { class: "tl-change", style: { left: x(d) }, title: `${c.date}: ${c.what}` }));
  const ticks = [];
  for (let t = new Date(lo); t <= hi; t = new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 1)))
    if (t >= lo) ticks.push(h("div", { class: "tl-tick", style: { left: x(t) } },
      t.toLocaleString("en-US", { month: "short", timeZone: "UTC" }) + (t.getUTCMonth() === 0 ? " " + t.getUTCFullYear() : "")));
  return h("div", { class: "card" },
    h("div", { class: "tl" },
      h("div", { class: "tl-axis" }, ticks),
      rows.map(({ e, w }) => h("div", { class: "tl-row" },
        h("div", { class: "tl-label", title: e.name }, e.name),
        h("div", { class: "tl-track" }, lines.map((l) => l.cloneNode()),
          h("div", { class: "tl-bar", title: `${e.name} · ${e.status} · ${iso(w[0])} → ${iso(w[1])}`,
            style: { left: x(w[0]), width: `calc(${x(w[1])} - ${x(w[0])} + 3px)`, background: COLORS[e.status] || "#7E8189" } }))))),
    h("div", { class: "caption" }, "Dashed lines = other changes shipped (hover). Anything overlapping an experiment can muddy its result. ",
      STATUSES.map((s) => h("span", { class: "tl-key" }, h("i", { style: { background: COLORS[s] } }), s))));
}

function sigChecker() {
  const inp = () => h("input", { type: "number", min: 0, step: 1, style: { width: "7em" } });
  const n1 = inp(), c1 = inp(), n2 = inp(), c2 = inp();
  const out = h("div");
  const run = () => { const v = [n1, c1, n2, c2].map((i) => Number(i.value) || 0); out.replaceChildren(v[0] && v[2] ? verdict(twoProportionTest(...v)) : ""); };
  [n1, c1, n2, c2].forEach((i) => i.addEventListener("input", run));
  return h("div", { class: "card" },
    h("div", { class: "caption" }, "Two-proportion z-test (conversion-style metrics). Pre/post designs are not randomized: treat a significant result as suggestive, and check the confounds above."),
    h("div", { class: "row" },
      h("label", { class: "field" }, "Control: visitors", n1), h("label", { class: "field" }, "Control: conversions", c1),
      h("label", { class: "field" }, "Treatment: visitors", n2), h("label", { class: "field" }, "Treatment: conversions", c2)),
    out);
}

function detail(e, exps, changes, today) {
  const w = windowOf(e, today);
  const flagged = [];
  if (w) {
    for (const o of exps) { const ow = windowOf(o, today); if (o !== e && ow && overlaps(w, ow)) flagged.push(`Experiment running at the same time: ${o.name}`); }
    for (const c of changes) { const cs = day(c.date), ce = day(c.end_date) || cs; if (cs && overlaps(w, [cs, ce])) flagged.push(`${c.date}: ${c.what}`); }
  }
  const noted = arr(e.confounds_noted);
  const line = (k, v) => (v ? h("div", {}, h("b", {}, k + ": "), v) : null);
  return h("div", {},
    line("Goal", e.goal || "—"), line("Primary metric", e.primary_metric || "—"), line("Guardrail", e.guardrail),
    line("Design", e.design || "—"),
    line("Dates", `${e.start_date || "planned"} → ${e.end_date || "open"}${e.review_date ? ` (review ${e.review_date})` : ""}`),
    e.dates_note ? h("div", { class: "caption" }, e.dates_note) : null,
    flagged.length || noted.length ? h("div", {}, h("b", {}, "Possible confounds"),
      h("ul", {}, noted.map((x) => h("li", {}, x)), flagged.map((x) => h("li", {}, x, h("i", { class: "caption" }, " (auto: overlapping dates)"))))) : null,
    arr(e.risks).length ? line("Risks", arr(e.risks).join("; ")) : null,
    h("b", {}, "Results"),
    arr(e.results).length
      ? h("ul", {}, arr(e.results).map((r) => h("li", {}, h("b", {}, r.date || ""), " ", r.note || "",
        r.counts ? h("div", {}, verdict(twoProportionTest(r.counts.control.n, r.counts.control.conv, r.counts.treatment.n, r.counts.treatment.conv))) : null)))
      : h("div", { class: "caption" }, "No reads yet."),
    e.stat_sig ? h("div", { class: "caption" }, `Significance noted: ${e.stat_sig}`) : null,
    panel("Check significance", sigChecker()));
}

export async function mount(root) {
  let log;
  try { log = await api("/experiments"); }
  catch (e) { root.replaceChildren(notice("err", e.message)); return; }
  const exps = log.experiments || [], changes = log.changes || [], today = todayUTC();
  const latest = (e) => { const r = arr(e.results); return r.length ? r[r.length - 1].note || "" : ""; };
  let pick = "Running";
  const seg = h("div", { class: "seg" });
  const body = h("div");
  const render = () => {
    seg.replaceChildren(...Object.keys(GROUPS).map((g) => h("button", { class: g === pick ? "primary" : "", onclick: () => { pick = g; render(); } }, g)));
    const shown = exps.filter((e) => GROUPS[pick].includes(e.status));
    if (!shown.length) { body.replaceChildren(notice("info", "Nothing here.")); return; }
    body.replaceChildren(
      timeline(shown, changes, today) || "",
      table(shown.map((e) => ({ Experiment: e.name, Status: e.status,
        Dates: `${e.start_date || "planned"} → ${e.end_date || (e.start_date ? "open" : "")}`, "Latest result": latest(e) || "—" })),
      [{ key: "Experiment" }, { key: "Status" }, { key: "Dates" }, { key: "Latest result" }]),
      ...shown.map((e) => panel(`${e.name}  ·  ${e.status}`, detail(e, exps, changes, today))));
  };
  const concluded = exps.filter((e) => ["concluded", "reverted"].includes(e.status));
  const themes = [...new Set(concluded.flatMap((e) => arr(e.themes)))].sort();
  root.replaceChildren(
    h("h2", {}, "🧪 Experiments"),
    h("div", { class: "caption" }, "Every experiment: goal, dates, results, confounds and significance. Source: experiments/experiment_log.json (edit + push to update)."),
    h("div", { class: "row" }, h("span", { class: "caption" }, "Show"), seg),
    body,
    panel("What we've learned, by theme", themes.length
      ? h("div", {}, themes.map((t) => h("div", {}, h("b", {}, t),
        h("ul", {}, concluded.filter((e) => arr(e.themes).includes(t)).map((e) => h("li", {}, `${e.name} (${e.status}): ${e.decision || latest(e)}`))))))
      : h("div", { class: "caption" }, "Nothing concluded yet.")));
  render();
}
