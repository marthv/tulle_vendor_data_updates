// 🩺 Health — port of the Streamlit Health tab's three panels (endpoint_health.py,
// perf_digest.py). Every run happens in the background on the API; the page polls, so a
// minute-long Xano read never freezes anything and a second click joins the running job.

import { api, runJob, lastJob } from "./app.js";
import { h, notice, panel, table, metric, loading, ago, esc, fmtInt } from "./ui.js";

// The daily report text is written for Slack (perf_digest._slack_to_markdown has already
// turned *bold* into **bold** and bullets into "- "). Tiny renderer: bold, italics, code,
// bullet lists (two levels). Input is escaped first.
function md(text) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/(^|[\s(])_(\S[^_]*?\S|\S)_(?=$|[\s).,])/g, "$1<i>$2</i>");
  const out = [];
  let depth = 0;
  const close = (to) => { while (depth > to) { out.push("</ul>"); depth--; } };
  for (const line of String(text || "").split("\n")) {
    const m = line.match(/^(\s*)- (.*)$/);
    if (m) {
      const want = m[1].length >= 2 ? 2 : 1;
      while (depth < want) { out.push("<ul>"); depth++; }
      close(want);
      out.push(`<li>${inline(m[2])}</li>`);
    } else {
      close(0);
      out.push(line.trim() ? `<p>${inline(line)}</p>` : "");
    }
  }
  close(0);
  return h("div", { class: "md", html: out.join("") });
}

const elapsed = (j) => (j && j.started ? `${Math.round((Date.now() / 1000) - j.started)}s` : "");

// One panel = button + result area. render(result) builds the result; restores the last
// run from the API when the tab opens (results live on the server, not in this page).
function jobPanel({ title, caption, kind, button, args = () => ({}), render, extra }) {
  const out = h("div");
  const btn = h("button", { class: "primary" }, button);
  const status = h("span", { class: "caption" });
  const show = (job) => {
    if (!job || job.status === "idle") { out.replaceChildren(notice("info", "Not run yet.")); return; }
    if (job.status === "running") { out.replaceChildren(loading(`Running… ${elapsed(job)}`)); return; }
    if (job.status === "error") { out.replaceChildren(notice("err", "Could not run: ", job.error)); return; }
    status.textContent = `Last run ${ago(job.finished)}${job.by ? " by " + job.by : ""}`;
    out.replaceChildren(render(job.result));
  };
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try { await runJob(kind, args(), show); show(await lastJob(kind)); }
    catch (e) { out.replaceChildren(notice("err", "Could not run: ", e.message)); }
    finally { btn.disabled = false; }
  });
  lastJob(kind).then((j) => (j.status === "running" ? btn.click() : show(j))).catch(() => show(null));
  return panel(title, h("div", {},
    h("div", { class: "caption" }, caption),
    h("div", { class: "row" }, extra || null, btn, status),
    out));
}

function endpointResult(r) {
  const bad = r.rows.filter((x) => x.Status !== "OK");
  return h("div", {},
    bad.length
      ? notice("err", `${bad.length} of ${r.rows.length} dependencies are failing.`,
        h("ul", {}, bad.map((x) => h("li", {}, h("b", {}, x.Endpoint), " → ", h("code", {}, x.Status), " — ", x.Detail, h("br"), "used by ", h("i", {}, x["Used by"])))))
      : notice("ok", `All ${r.rows.length} read dependencies OK.`),
    table(r.rows, [{ key: "Endpoint" }, { key: "Status" }, { key: "Detail" }, { key: "Used by" }, { key: "Note" }]),
    h("p", {}, h("b", {}, "Write dependencies"), " (listed, never probed — a health check must not mutate prod):"),
    table(r.writes, [{ key: "Endpoint" }, { key: "Verb" }, { key: "Gate" }, { key: "Used by" }]));
}

function perfResult(r) {
  const s = r.summary;
  return h("div", {},
    h("div", { class: "grid g4" },
      metric("App requests", fmtInt(s.requests_app)), metric("p95", `${s.p95}s`),
      metric("5xx", fmtInt(s.count_5xx)), metric("Race 401s", `${s.race_401_pct}%`)),
    r.breaches.length
      ? notice("err", `${r.breaches.length} budget breach(es).`,
        h("ul", {}, r.breaches.map((b) => h("li", {}, h("b", {}, b.scope), " · ", h("code", {}, b.metric), " — ", b.detail))))
      : notice("ok", "All budgets met."),
    h("div", { class: "caption" },
      `${s.sessions_with_401} of ${s.sessions} sessions saw at least one 401 · ${s.race_401} were from clients that were signed in (pre-auth race), ${s.signedout_401} from signed-out visitors. Window: ${r.hours}h.`),
    table(r.endpoints, [
      { key: "path", label: "Endpoint" }, { key: "n", num: true, fmt: fmtInt },
      { key: "p50", num: true }, { key: "p95", num: true, heat: true }, { key: "p99", num: true }, { key: "max", num: true },
      { key: "cache_pct", label: "cache %", num: true, fmt: (v) => (v == null ? "-" : v) },
      { key: "race_401_pct", label: "race 401 %", num: true }]));
}

function historyPanel() {
  const out = h("div", {}, loading("Loading history…"));
  api("/health/history?days=30")
    .then((r) => out.replaceChildren(r.rows.length
      ? table(r.rows, [{ key: "report_date", label: "Date" }, { key: "query_id", label: "ep", num: true },
        { key: "requests", num: true, fmt: fmtInt }, { key: "failures", num: true }, { key: "fail_pct", label: "fail %", num: true, heat: true },
        { key: "tokenless", num: true }, { key: "p50_s", label: "p50 s", num: true }, { key: "p95_s", label: "p95 s", num: true, heat: true },
        { key: "p99_s", label: "p99 s", num: true }, { key: "cache_hit_pct", label: "cache %", num: true }], { maxHeight: "420px" })
      : notice("info", "No history yet — the first row lands after the next 13:32 UTC run.")))
    .catch((e) => out.replaceChildren(notice("warn", "Could not read history: ", e.message)));
  return h("div", {}, h("p", {}, h("b", {}, "History"), " — one row per endpoint per day, written by the daily cron"), out);
}

export async function mount(root) {
  const hours = h("input", { type: "number", min: 1, max: 72, value: 24, style: { width: "5em" } });
  root.replaceChildren(
    h("h2", {}, "🩺 Health"),
    jobPanel({
      title: "🔐 Endpoint health — check every Xano dependency", kind: "endpoint_health",
      button: "▶ Run endpoint health check",
      caption: "Xano endpoints default to open, so the surface drifts insecure; but the admin tools hold no user token, so locking one with auth = \"user\" breaks a tab silently. Machine callers must use the secret gate. Run this after any endpoint change.",
      render: endpointResult,
    }),
    h("div", {},
      jobPanel({
        title: "🩺 Daily endpoint health — search (ep119) and map (ep121)", kind: "health_report",
        button: "▶ Run health report now",
        caption: "Failures and why, response times, most common queries, what makes searches slow, and the cache warmer. Posted daily at 13:32 UTC by health-report-cron; run it here any time. Reads the last 24h of Xano request history (all Xano keeps). On-demand runs are not recorded.",
        render: (r) => md(r.markdown),
      })),
    panel("📅 Daily health history (last 30 days)", historyPanel()),
    jobPanel({
      title: "📊 Performance digest — what real users actually experienced", kind: "perf_digest",
      button: "▶ Run performance digest",
      caption: "Read from Xano's own request history, scoped to browser traffic on the live app. Budgets live in perf_slo.json.",
      extra: h("label", { class: "field inline" }, "Window (hours) ", hours),
      args: () => ({ hours: Number(hours.value) || 24 }),
      render: perfResult,
    }));
}
