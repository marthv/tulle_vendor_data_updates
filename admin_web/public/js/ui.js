// Small DOM + formatting helpers shared by every tab. No framework: each tab renders once
// and then updates only the parts that change, so nothing ever "reruns" the whole page.

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmtInt = (n) => (n == null || isNaN(n) ? "—" : Math.round(n).toLocaleString("en-US"));
export const fmtMoney = (n, d = 0) => (n == null || isNaN(n) ? "—" : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
export const fmtPct = (x, d = 1) => (x == null || isNaN(x) ? "—" : (x * 100).toFixed(d) + "%");
export const fmtNum = (n, d = 2) => (n == null || isNaN(n) ? "—" : Number(n).toFixed(d));
export function fmtTs(v) {
  if (v == null || v === "") return "—";
  const d = typeof v === "number" ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
  return isNaN(d) ? String(v) : d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}
export function ago(v) {
  const d = typeof v === "number" ? new Date(v < 1e12 ? v * 1000 : v) : new Date(v);
  const s = (Date.now() - d.getTime()) / 1000;
  if (isNaN(s)) return "";
  if (s < 90) return Math.round(s) + "s ago";
  if (s < 5400) return Math.round(s / 60) + "m ago";
  if (s < 129600) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

export const metric = (label, value, help) =>
  h("div", { class: "card metric" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), help ? h("div", { class: "help" }, help) : null);

export const notice = (kind, ...kids) => h("div", { class: `notice ${kind}` }, ...kids);
export const loading = (text = "Loading…") => h("div", { class: "loading" }, h("span", { class: "spin" }), text);

export function panel(title, body, open = false) {
  return h("details", { class: "panel", open }, h("summary", {}, title), h("div", { class: "body" }, body));
}

// Multi-select dropdown. onChange(selectedArray). label(v) formats an option.
export function multiSelect(title, options, onChange, label = (v) => v) {
  const sel = new Set();
  const btn = h("button", { type: "button" });
  const menu = h("div", { class: "menu", hidden: true });
  const wrap = h("label", { class: "field" }, title, h("div", { class: "msel" }, btn, menu));
  const paint = () => { btn.textContent = sel.size ? [...sel].map(label).join(", ") : "All"; };
  for (const o of options) {
    const cb = h("input", { type: "checkbox", value: o });
    cb.addEventListener("change", () => { cb.checked ? sel.add(o) : sel.delete(o); paint(); onChange([...sel]); });
    menu.append(h("label", {}, cb, label(o)));
  }
  btn.addEventListener("click", (e) => { e.preventDefault(); menu.hidden = !menu.hidden; });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) menu.hidden = true; });
  paint();
  return wrap;
}

// Sortable table. cols: [{key, label, num, fmt, heat}] ; heat=true shades the column green
// by value (same transparent->green heatmap the Streamlit tab used).
export function table(rows, cols, { maxHeight, onRow } = {}) {
  let sortKey = null, dir = -1;
  const wrap = h("div", { class: "tbl-wrap", style: maxHeight ? { maxHeight } : null });
  const render = () => {
    let data = rows.slice();
    if (sortKey) data.sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * dir;
    });
    const ranges = {};
    for (const c of cols) if (c.heat) {
      const v = rows.map((r) => Number(r[c.key])).filter((x) => !isNaN(x));
      ranges[c.key] = [Math.min(...v), Math.max(...v)];
    }
    const thead = h("tr", {}, cols.map((c) => h("th", {
      class: c.num ? "num" : "", onclick: () => { dir = sortKey === c.key ? -dir : -1; sortKey = c.key; render(); },
    }, c.label ?? c.key, sortKey === c.key ? (dir > 0 ? " ▲" : " ▼") : "")));
    const body = data.map((r) => h("tr", onRow ? { class: "click", onclick: (ev) => { wrap.querySelectorAll("tr.sel").forEach((x) => x.classList.remove("sel")); ev.currentTarget.classList.add("sel"); onRow(r); } } : {}, cols.map((c) => {
      const v = r[c.key];
      let style = null;
      if (c.heat && ranges[c.key]) {
        const [lo, hi] = ranges[c.key]; const a = (0.55 * ((Number(v) - lo) / ((hi - lo) || 1))).toFixed(2);
        style = { background: `rgba(var(--heat),${a})` };
      }
      const cell = c.render ? c.render(r) : (c.fmt ? c.fmt(v, r) : (v ?? ""));
      return h("td", { class: c.num ? "num" : "", style }, cell);
    })));
    wrap.replaceChildren(h("table", { class: "tbl" }, h("thead", {}, thead), h("tbody", {}, body)));
  };
  render();
  return wrap;
}

export function downloadCsv(filename, rows, cols) {
  const keys = cols || Object.keys(rows[0] || {});
  const q = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [keys.join(","), ...rows.map((r) => keys.map((k) => q(r[k])).join(","))].join("\n");
  const a = h("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: filename });
  document.body.append(a); a.click(); a.remove();
}

// Chart.js loads with `defer`; wait for it.
export async function chartLib() {
  for (let i = 0; i < 100 && !window.Chart; i++) await new Promise((r) => setTimeout(r, 50));
  const css = getComputedStyle(document.documentElement);
  window.Chart.defaults.color = css.getPropertyValue("--muted").trim() || "#7E8189";
  window.Chart.defaults.borderColor = css.getPropertyValue("--line").trim() || "#E8EAEE";
  window.Chart.defaults.font.family = '"Sora", system-ui, sans-serif';
  return window.Chart;
}
export const PALETTE = ["#00A368", "#D97706", "#2F6FDE", "#C53030", "#0D9488", "#B45309", "#0F7348", "#DB2777", "#64748B", "#65A30D", "#0891B2", "#A16207"];
