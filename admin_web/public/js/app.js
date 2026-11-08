// Shell: tab switching without reloads. Each tab module is imported the first time its
// tab is opened and then stays mounted, so switching back keeps filters, scroll and data.

import { loading, notice, h } from "./ui.js";

export async function api(path, opts = {}) {
  const res = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "content-type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `${res.status} ${res.statusText}`;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

const MODULES = {
  cohorts: () => import("./cohorts.js"),
  pricing: () => import("./pricing.js"),
  google: () => import("./google.js"),
  extraction: () => import("./extraction.js"),
  health: () => import("./health.js"),
};
const mounted = {};

async function show(tab) {
  if (!MODULES[tab]) tab = "cohorts";
  for (const a of document.querySelectorAll("#tabs a")) a.classList.toggle("active", a.dataset.tab === tab);
  for (const s of document.querySelectorAll("main > .tab")) s.hidden = s.id !== `tab-${tab}`;
  const root = document.getElementById(`tab-${tab}`);
  if (mounted[tab]) return;
  mounted[tab] = true;
  root.replaceChildren(loading());
  try {
    const mod = await MODULES[tab]();
    await mod.mount(root);
  } catch (e) {
    mounted[tab] = false;
    root.replaceChildren(notice("err", "This tab failed to load: ", e.message || String(e), " ",
      h("button", { onclick: () => show(tab) }, "Retry")));
  }
}

window.addEventListener("hashchange", () => show(location.hash.slice(1)));
show(location.hash.slice(1) || "cohorts");

api("/me").then((me) => {
  document.getElementById("who").textContent = me.email || "";
}).catch(() => {});
