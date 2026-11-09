// 💰 Venue Pricing — port of the Streamlit tab (dashboard.py _vp_*). The server sends every
// deduped pricing row once (background snapshot); filters, estimates, the State × Type grid and
// the drill-down all run here, instantly. The old "Apply" and "click a cell, then Run" buttons
// existed only to dodge Streamlit reruns, so they are gone: changes apply as you make them.

import { api, runJob } from "./app.js";
import { h, metric, notice, panel, multiSelect, table, loading, ago, fmtInt } from "./ui.js";

const GUESTS = [50, 75, 100, 125, 150, 175, 200, 250, 300];

// _vp_money: "$12.3K" style; "—" for <= 0.
export function money(n) {
  n = Number(n);
  if (!isFinite(n) || n <= 0) return "—";
  if (n >= 1000) return "$" + (n / 1000).toFixed(n >= 100000 ? 0 : 1) + "K";
  return "$" + Math.round(n);
}
const dollars = (n) => (n == null ? "" : "$" + Math.round(n).toLocaleString("en-US"));
// Python's round(): exact halves go to the even neighbour (760.5 -> 760), so totals match the old tab.
const pyRound = (x) => { const f = Math.floor(x); return x - f === 0.5 ? (f % 2 === 0 ? f : f + 1) : Math.round(x); };
const yearInt = (s) => { const m = String(s || "").match(/\d{4}/); return m ? Number(m[0]) : null; };

// _vp_estimate_parts
export function estimateParts(r, G) {
  const fb = Math.max(r.pp_fb * G, r.fb_min);
  const subtotal = r.venue_fee + fb;
  const admin = subtotal * (r.admin_pct / 100);
  const t = r.cer_type.toLowerCase();
  const perPerson = t.includes("per person") || t.includes("per head") || t === "pp";
  const ceremony = perPerson ? r.cer_fee * G : r.cer_fee;
  return [subtotal + admin + ceremony, { base: r.venue_fee, fb, admin, ceremony }];
}

// _vp_compute_vendors — one "from" estimate per venue (cheapest qualifying space) + quote count.
export function computeVendors(rows, F) {
  const G = F.guests, byVendor = new Map();
  for (const r of rows) {
    if (F.states.length && !F.states.includes(r.state)) continue;
    if (F.types.length && !F.types.includes(r.venue_type)) continue;
    if (F.year !== "All" && r.year !== F.year) continue;
    if (!byVendor.has(r.vendor_id)) byVendor.set(r.vendor_id, []);
    byVendor.get(r.vendor_id).push(r);
  }
  const q = (F.search || "").trim().toLowerCase(), want = new Set(F.vibes);
  const out = [];
  for (const [vid, vr] of byVendor) {
    // Vibes gate the VENUE (union of its rows' tags), never individual rows — see dashboard.py.
    const venueVibes = [];
    for (const r of vr) for (const v of r.vibes) if (!venueVibes.includes(v)) venueVibes.push(v);
    if (want.size && !venueVibes.some((v) => want.has(v))) continue;
    const qualify = vr.filter((r) => r.guest_min <= G && (r.max_cap === 0 || G <= r.max_cap));
    const cand = qualify.length ? qualify : vr;
    let best = null;
    for (const r of cand) {
      const [est, parts] = F.mode === "base" ? [r.venue_fee, { base: r.venue_fee, fb: 0, admin: 0, ceremony: 0 }] : estimateParts(r, G);
      if (est <= 0) continue;
      if (!best || est < best.est) best = { est, parts, r };
    }
    if (!best) continue;
    const nQuotes = new Set(vr.map((r) => r.pdf_id)).size;
    if (nQuotes < F.minQuotes) continue;
    const name = vr[0].name || "";
    if (q && !name.toLowerCase().includes(q)) continue;
    const cap = Math.max(0, ...vr.map((r) => r.max_cap)) || null;
    const years = vr.map((r) => yearInt(r.year)).filter(Boolean);
    out.push({
      Venue: name, State: vr[0].state || "—", Type: vr[0].venue_type || "—",
      Vibes: [...venueVibes].sort().join(", ") || "—",
      Capacity: cap ? Math.trunc(cap) : null, Quotes: nQuotes, Year: years.length ? Math.max(...years) : null,
      "Base fee": pyRound(best.parts.base), "F&B": pyRound(best.parts.fb),
      Admin: pyRound(best.parts.admin), Ceremony: pyRound(best.parts.ceremony),
      Estimate: pyRound(best.est),
      _vid: vid, _space: best.r.space || "", _per_head: best.r.pp_fb, _fb_min: best.r.fb_min,
      _admin_pct: best.r.admin_pct, _cer_type: best.r.cer_type,
    });
  }
  out.sort((a, b) => b.Estimate - a.Estimate);
  return out;
}

export function decode(snap) {
  const f = snap.fields;
  return snap.rows.map((a) => { const o = {}; f.forEach((k, i) => { o[k] = a[i]; }); return o; });
}

// sage-lt → amber, same as _vp_cell_style; cells are always light, so dark ink.
function cellColor(v, lo, hi) {
  const t = hi > lo ? (v - lo) / (hi - lo) : 0, a = [234, 244, 239], b = [245, 201, 138];
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
}

function grid(vendors, drill, setDrill) {
  const states = [...new Set(vendors.map((v) => v.State))].sort();
  const types = [...new Set(vendors.map((v) => v.Type))].sort();
  const acc = {};
  for (const v of vendors) { const k = v.State + "\u0000" + v.Type; (acc[k] = acc[k] || []).push(v.Estimate); }
  const avg = (k) => (acc[k] ? acc[k].reduce((s, x) => s + x, 0) / acc[k].length : null);
  const vals = Object.keys(acc).map(avg);
  if (!vals.length || types.length < 1) return h("div", { class: "caption" }, "Not enough venue-type data to build the matrix.");
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const isSel = (s, t) => drill && (drill.state == null || drill.state === s) && (drill.type == null || drill.type === t);
  return h("div", { class: "tbl-wrap", style: { maxHeight: "460px" } }, h("table", { class: "tbl pgrid" },
    h("thead", {}, h("tr", {}, h("th", {}, "State \\ Type"),
      types.map((t) => h("th", { class: "num click" + (drill && drill.type === t && drill.state == null ? " on" : ""), title: `Show every ${t}`, onclick: () => setDrill({ state: null, type: t }) }, t)))),
    h("tbody", {}, states.map((s) => h("tr", {},
      h("th", { class: "click" + (drill && drill.state === s && drill.type == null ? " on" : ""), title: `Show every venue in ${s}`, onclick: () => setDrill({ state: s, type: null }) }, s),
      types.map((t) => {
        const v = avg(s + "\u0000" + t);
        return h("td", {
          class: "num" + (v != null ? " click" : "") + (v != null && isSel(s, t) ? " on" : ""),
          style: v != null ? { background: cellColor(v, lo, hi), color: "#18191A" } : null,
          title: v != null ? `${s} × ${t}: ${acc[s + "\u0000" + t].length} venue(s)` : null,
          onclick: v != null ? () => setDrill({ state: s, type: t }) : null,
        }, v != null ? money(v) : "");
      }))))));
}

function breakdown(v, G, pdfs) {
  const byHead = v._per_head * G, which = byHead >= v._fb_min ? "per-head applies" : "F&B minimum applies";
  const cerPP = ["per person", "per head", "pp"].some((s) => v._cer_type.toLowerCase().includes(s));
  const li = (...k) => h("li", {}, ...k);
  return h("div", { class: "card" },
    h("h4", {}, `${v.Venue} — breakdown @ ${G} guests`),
    h("div", { class: "g2 grid" },
      h("ul", {},
        li(h("b", {}, "Base fee: "), money(v["Base fee"]), v._space ? `  ·  space: ${v._space}` : ""),
        li(h("b", {}, "F&B: "), `max( ${dollars(v._per_head)}/guest × ${G} = ${dollars(byHead)} ,  min ${dollars(v._fb_min)} ) → `, h("b", {}, money(v["F&B"])), h("i", {}, `  (${which})`)),
        li(h("b", {}, "Admin: "), `(base + F&B) × ${Math.round(v._admin_pct)}% → ${money(v.Admin)}`),
        li(h("b", {}, "Ceremony: "), money(v.Ceremony), h("i", {}, `  (${cerPP ? "per person × " + G : "flat"})`)),
        li(h("b", {}, "Estimate (from): "), money(v.Estimate))),
      h("div", {}, pdfs.length
        ? [h("b", {}, "Pricing PDFs"), h("ul", {}, pdfs.map((p) => h("li", {}, h("a", { href: p.link, target: "_blank", rel: "noopener" }, p.name + (p.year ? ` · ${p.year}` : "")))))]
        : h("div", { class: "caption" }, "No pricing PDFs linked for this venue."))));
}

export async function mount(root) {
  let snap, rows, meta, pdfs;
  const F = { guests: 150, states: [], types: [], vibes: [], year: "All", minQuotes: 0, search: "", mode: "allin" };
  let drill = null, vendors = [];

  const head = h("div", { class: "row" });
  const caption = h("div", { class: "caption" });
  const filters = h("div");
  const out = h("div");
  root.replaceChildren(h("h2", {}, "💰 Venue Pricing — approximate all-in cost"), caption, head, filters, out);

  const setCaption = () => caption.replaceChildren(F.mode === "base"
    ? "Peak-season Saturday · base fee only (venue rental, no F&B / admin / ceremony). Venues only. Estimate per venue is the cheapest qualifying space."
    : "Peak-season Saturday estimate · base fee + F&B (with minimum) + admin% + ceremony · no tax (none in Xano). Venues only. Estimate per venue is the cheapest qualifying space.");

  async function load(refresh = false) {
    out.replaceChildren(loading(refresh ? "Re-pulling venue pricing from Xano (~1 min)…" : "Loading venue pricing…"));
    try {
      if (refresh) {
        const before = (await api("/pricing")).as_of;
        await api("/pricing?refresh=1");
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 3000));
          snap = await api("/pricing");
          if (snap.as_of !== before) break;
        }
      } else snap = await api("/pricing");
    } catch (e) {
      out.replaceChildren(notice("warn", "Pricing snapshot unavailable (the first pull after a restart takes ~1 min, or Xano is down): ", e.message, " ",
        h("button", { onclick: () => load() }, "↻ Retry")));
      return;
    }
    rows = decode(snap); meta = snap.meta; pdfs = snap.pdfs || {};
    head.replaceChildren(
      h("div", { class: "caption" }, `Snapshot ${snap.as_of.slice(0, 16).replace("T", " ")} UTC (${ago(snap.as_of)}) · refreshes every 6h in the background (or press Refresh)`),
      h("div", { class: "spacer" }), h("button", { onclick: () => load(true) }, "🔄 Refresh data"));
    buildFilters(); compute();
  }

  function buildFilters() {
    const sel = (opts, val, on) => { const s = h("select", {}, opts.map((o) => h("option", { value: o, selected: String(o) === String(val) }, o))); s.addEventListener("change", () => on(s.value)); return s; };
    const minQ = h("input", { type: "number", min: 0, step: 1, value: F.minQuotes, style: { width: "6em" } });
    minQ.addEventListener("input", () => { F.minQuotes = Math.max(0, Number(minQ.value) || 0); compute(); });
    let t = null;
    const search = h("input", { type: "search", placeholder: "e.g. Mansion", value: F.search });
    search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { F.search = search.value; compute(); }, 150); });
    const mode = h("div", { class: "seg" });
    const paintMode = () => mode.replaceChildren(...[["allin", "All-in formula"], ["base", "Base fee only"]].map(([k, l]) =>
      h("button", { class: F.mode === k ? "primary" : "", onclick: () => { F.mode = k; paintMode(); setCaption(); compute(); } }, l)));
    paintMode(); setCaption();
    const vibes = multiSelect("Venue vibes", meta.vibes, (v) => { F.vibes = v; compute(); });
    vibes.title = "Aesthetic features from the pricing PDFs, most common first. A venue matches if ANY selected vibe applies. Roughly 1 venue in 8 carries no vibe tag and is excluded whenever this filter is set.";
    filters.replaceChildren(h("div", { class: "card" },
      h("div", { class: "grid g4" },
        h("label", { class: "field" }, "Guest count", sel(GUESTS, F.guests, (v) => { F.guests = Number(v); compute(); })),
        multiSelect("State", meta.states, (v) => { F.states = v; compute(); }),
        multiSelect("Venue type", meta.venue_types, (v) => { F.types = v; compute(); }),
        vibes,
        h("label", { class: "field" }, "Year", sel(["All", ...meta.years], F.year, (v) => { F.year = v; compute(); })),
        h("label", { class: "field" }, "Min quotes", minQ),
        h("label", { class: "field" }, "Filter by venue name", search),
        h("label", { class: "field" }, "Pricing mode", mode))));
  }

  function aiPanel() {
    const box = h("div", {}, h("div", { class: "caption" }, "One-line read on anomalies and patterns in the venues shown (Claude). Runs only when you click."));
    const btn = h("button", {}, "🤖 Summarize these venues");
    btn.addEventListener("click", async () => {
      if (vendors.length < 3) { box.replaceChildren(notice("info", "Needs at least 3 venues.")); return; }
      const ests = vendors.map((v) => v.Estimate), avg = ests.reduce((s, x) => s + x, 0) / ests.length;
      const pick = (f) => vendors.reduce((a, b) => (f(b, a) ? b : a));
      const slim = (v) => ({ Venue: v.Venue, State: v.State, Type: v.Type, Estimate: v.Estimate, Quotes: v.Quotes });
      const s = { n: vendors.length, guests: F.guests, mode: F.mode === "base" ? "Base fee only" : "All-in formula",
        avg, std: Math.sqrt(ests.reduce((t, x) => t + (x - avg) ** 2, 0) / ests.length), min: Math.min(...ests), max: Math.max(...ests),
        states: [...new Set(vendors.map((v) => v.State).filter((x) => x !== "—"))].sort(),
        types: [...new Set(vendors.map((v) => v.Type).filter((x) => x !== "—"))].sort(),
        cheapest: slim(pick((b, a) => b.Estimate < a.Estimate)), expensive: slim(pick((b, a) => b.Estimate > a.Estimate)),
        top_quoted: slim(pick((b, a) => b.Quotes > a.Quotes)) };
      btn.disabled = true; box.replaceChildren(loading("Asking Claude…"));
      try { const r = await runJob("pricing_insight", { s }); box.replaceChildren(h("p", {}, r.text), h("div", { class: "caption" }, r.model)); }
      catch (e) { box.replaceChildren(notice("err", "Analysis unavailable: ", e.message)); }
      finally { btn.disabled = false; }
    });
    return panel("🤖 AI summary (anomalies & patterns)", h("div", {}, btn, box));
  }

  function compute() {
    vendors = computeVendors(rows, F);
    if (drill && !vendors.some((v) => (drill.state == null || v.State === drill.state) && (drill.type == null || v.Type === drill.type))) drill = null;
    render();
  }

  function render() {
    const G = F.guests;
    const errs = meta.errors.length ? notice("err", "Xano fetch issue — " + meta.errors.join(", ")) : null;
    if (!vendors.length) { out.replaceChildren(errs || "", notice("warn", "No venues match these filters.")); return; }
    const ests = vendors.map((v) => v.Estimate).sort((a, b) => a - b), mid = Math.floor(ests.length / 2);
    const med = ests.length % 2 ? ests[mid] : (ests[mid - 1] + ests[mid]) / 2;
    const subset = drill ? vendors.filter((v) => (drill.state == null || v.State === drill.state) && (drill.type == null || v.Type === drill.type)) : vendors;
    const label = drill ? [drill.state, drill.type].filter((x) => x != null).join(" × ") : "";
    const detail = h("div");
    const money0 = (v) => dollars(v);
    const cols = F.mode === "base"
      ? [{ key: "Venue" }, { key: "State" }, { key: "Type" }, { key: "Vibes" }, { key: "Capacity", num: true, fmt: fmtInt }, { key: "Quotes", num: true }, { key: "Year", num: true }, { key: "Estimate", label: "Base fee", num: true, fmt: money0, heat: true }]
      : [{ key: "Venue" }, { key: "State" }, { key: "Type" }, { key: "Vibes" }, { key: "Capacity", num: true, fmt: fmtInt }, { key: "Quotes", num: true }, { key: "Year", num: true },
        { key: "Base fee", num: true, fmt: money0 }, { key: "F&B", num: true, fmt: money0 }, { key: "Admin", num: true, fmt: money0 }, { key: "Ceremony", num: true, fmt: money0 }, { key: "Estimate", num: true, fmt: money0, heat: true }];
    const c = meta.counts;
    out.replaceChildren(
      errs || "",
      h("div", { class: "grid g4" },
        metric("Avg estimate", money(ests.reduce((s, x) => s + x, 0) / ests.length)), metric("Median", money(med)),
        metric("Min", money(ests[0])), metric("Max", money(ests[ests.length - 1])),
        metric("Venues", fmtInt(vendors.length)), metric("Total quotes", fmtInt(vendors.reduce((s, v) => s + v.Quotes, 0)))),
      aiPanel(),
      panel("ℹ️ How the estimate is calculated", F.mode === "base"
        ? h("p", {}, `For each venue we take its cheapest qualifying space (one that can host ${G} guests) and show its base fee (venue rental only) using peak-season-Saturday pricing. F&B, admin % and ceremony fees are excluded in this mode.`)
        : h("div", {}, h("p", {}, `For each venue we take its cheapest qualifying space (one that can host ${G} guests) and add up, using peak-season-Saturday pricing:`),
          h("ul", {}, h("li", {}, h("b", {}, "Base fee"), " — the venue rental."),
            h("li", {}, h("b", {}, "F&B"), ` = max(per-person F&B × ${G} guests, F&B minimum) — scales with the guest count.`),
            h("li", {}, h("b", {}, "Admin"), " = (Base fee + F&B) × admin/service %."),
            h("li", {}, h("b", {}, "Ceremony"), ` — flat, or × ${G} when charged per person.`)),
          h("p", {}, "There is no tax in the data."))),
      h("h3", {}, "Average estimate · State × Venue type"),
      h("div", { class: "caption" }, "Click a cell to show those venues below; click a state or a venue type to show all of it · greener = cheaper"),
      grid(vendors, drill, (d) => { drill = d; render(); }),
      h("div", { class: "row" },
        h("h3", {}, drill ? `Venues in ${label} @ ${G} guests — ${subset.length} venue(s)` : `All venues @ ${G} guests — ${subset.length} venue(s)`),
        h("div", { class: "spacer" }), drill ? h("button", { onclick: () => { drill = null; render(); } }, "Show all") : null),
      h("div", { class: "caption" }, "Each row shows how the estimate is built (base · F&B · admin · ceremony). Click a row for its breakdown and pricing PDFs."),
      subset.length ? table(subset, cols, { maxHeight: "560px", onRow: (v) => { detail.replaceChildren(breakdown(v, G, pdfs[v._vid] || [])); detail.scrollIntoView({ behavior: "smooth", block: "nearest" }); } }) : h("div", { class: "caption" }, "No venues for this selection."),
      detail,
      h("div", { class: "caption" }, `Data: ${fmtInt(c.joined)} priced venue rows · ${fmtInt(c.venues)} venues (all states) · deduped ${fmtInt(c.t36_deduped)}/${fmtInt(c.t36_raw)} table-36 rows`));
  }

  load();
}
