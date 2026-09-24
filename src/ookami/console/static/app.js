"use strict";
// Ookami console. No framework, no build step, no external requests. Every server value is escaped before rendering.

const $ = (sel, el = document) => el.querySelector(sel);
const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = {
  n: (v) => Number(v || 0).toLocaleString(),
  usd: (v) => "$" + Number(v || 0).toFixed(Number(v || 0) < 1 ? 4 : 2),
  when: (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "—"),
  ago: (ts) => {
    if (!ts) return "—";
    const s = Date.now() / 1000 - ts;
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  },
};
const STATUS = {
  ready: "ok", passed: "ok", promoted: "accent", succeeded: "ok", active: "ok", pass: "ok",
  training: "warn", evaluating: "warn", running: "warn", queued: "warn", starting: "warn", partial: "warn",
  failed: "bad", rejected: "bad", exited: "bad", fail: "bad", revoked: "", retired: "", cancelled: "",
};
const badge = (s) => `<span class="badge ${STATUS[s] ?? ""}">${h(s || "—")}</span>`;

// ------------------------------------------------------------------ API and session
let token = sessionStorage.getItem("ookami.key") || "";
let timers = [];

async function api(path, opts = {}) {
  const res = await fetch("/api/" + path, {
    method: opts.body ? "POST" : "GET",
    headers: { Authorization: "Bearer " + token, ...(opts.body ? { "Content-Type": "application/json" } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { signOut(); throw new Error(data.error || "signed out"); }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.add("hidden"), 4000);
}

function modal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
}
const closeModal = () => $("#modal").classList.add("hidden");

function signOut() {
  token = "";
  sessionStorage.removeItem("ookami.key");
  $("#app").classList.add("hidden");
  $("#signin").classList.remove("hidden");
}

async function signIn(key) {
  token = key.trim();
  const ov = await api("overview");
  sessionStorage.setItem("ookami.key", token);
  $("#signin").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#platform-name").textContent = `${ov.platform} · ${ov.backend}`;
  $("#gateway-pill").textContent = ov.gateway.url ? `gateway ${ov.gateway.url} · auth ${ov.gateway.auth}` : "no gateway";
  route();
}

// ------------------------------------------------------------------ router
const PAGES = { overview, models, training, keys, usage, playground };
const TITLES = { overview: "Overview", models: "Models", training: "Training", keys: "Keys", usage: "Usage", playground: "Playground" };

async function route() {
  timers.forEach(clearInterval);
  timers = [];
  const [page, arg] = (location.hash.replace(/^#\//, "") || "overview").split("/");
  const name = PAGES[page] ? page : "overview";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.page === name));
  $("#page-title").textContent = TITLES[name];
  const el = $("#page");
  el.innerHTML = `<div class="muted">Loading…</div>`;
  try {
    await PAGES[name](el, arg && decodeURIComponent(arg));
  } catch (e) {
    el.innerHTML = `<div class="card error">${h(e.message)}</div>`;
  }
}

// ------------------------------------------------------------------ pages
async function overview(el) {
  const ov = await api("overview");
  const s = ov.stats;
  el.innerHTML = `
    <div class="grid grid-4">
      ${stat("Requests · 24h", fmt.n(s.requests_24h), s.errors_24h ? `${fmt.n(s.errors_24h)} errors` : "no errors")}
      ${stat("Tokens · 24h", fmt.n(s.tokens_24h), "through the gateway")}
      ${stat("Spend · 30d", fmt.usd(s.spend_30d), "API + self-hosted hardware")}
      ${stat("Active keys", fmt.n(s.active_keys), `auth ${h(ov.gateway.auth)}`)}
    </div>
    <div class="grid grid-2">
      <div class="card card-flush"><h2>Services</h2>${table(["Service", "Kind", "State", "Endpoint"],
        ov.services.map((x) => [h(x.name), h(x.kind), badge(x.state), `<span class="mono">${h(x.url)}</span>`]),
        "Nothing running. Start with <code>ookami up</code>.")}</div>
      <div class="card card-flush"><h2>Models</h2>${table(["Model", "Kind", "Serves", "Live version"],
        ov.models.map((m) => [`<a href="#/models/${encodeURIComponent(m.name)}">${h(m.name)}</a>`, h(m.kind),
          `<span class="mono">${h(m.provider || m.base)}</span>`, m.live ? badge("promoted") + " " + h(m.live) : `<span class="muted">base model</span>`]),
        "No models in ookami.yaml.")}</div>
    </div>
    ${ov.issues.length ? `<div class="card"><h2>Config notes</h2><ul>${ov.issues.map((i) => `<li class="small">${h(i)}</li>`).join("")}</ul></div>` : ""}`;
}

async function models(el, selected) {
  const ov = await api("overview");
  const list = ov.models;
  if (!list.length) { el.innerHTML = `<div class="card empty">No models in ookami.yaml.</div>`; return; }
  const name = selected && list.some((m) => m.name === selected) ? selected : list[0].name;
  const m = list.find((x) => x.name === name);
  const [versions, events] = await Promise.all([api(`models/${encodeURIComponent(name)}/versions`), api(`models/${encodeURIComponent(name)}/events`)]);
  el.innerHTML = `
    <div class="split">
      <div class="card card-flush"><table>${list.map((x) => `
        <tr class="clickable ${x.name === name ? "selected" : ""}" data-go="#/models/${encodeURIComponent(x.name)}">
          <td><strong>${h(x.name)}</strong><div class="muted small">${h(x.kind)}${x.live ? " · live " + h(x.live) : ""}</div></td></tr>`).join("")}
      </table></div>
      <div class="grid">
        <div class="card">
          <div class="card-head"><h2>${h(name)}</h2><span class="pill">${h(m.provider || m.base)}</span></div>
          <p class="muted small">${m.kind === "api" ? "API model routed through the gateway." :
            m.live ? `Serving <strong>${h(m.live)}</strong>. Promote another version that passed its gate to replace it.` :
            "Serving the base model. Train a version and promote it once it passes its gate."}</p>
        </div>
        <div class="card card-flush"><h2>Versions</h2>${table(["Version", "Status", "Gate", "Data", "Created", ""],
          versions.map((v) => [h(v.tag), badge(v.status), v.decision ? badge(v.decision) : "—", `<span class="mono">${h(v.dataset_hash || "—")}</span>`,
            `<span title="${h(fmt.when(v.created_at))}">${fmt.ago(v.created_at)}</span>`,
            `<div class="row">${v.report ? `<button class="btn btn-sm" data-report="${h(v.report)}">Report</button>` : ""}
             ${v.status === "passed" ? `<button class="btn btn-sm btn-primary" data-promote="${v.version}">Promote</button>` : ""}
             ${["rejected", "retired"].includes(v.status) ? `<button class="btn btn-sm btn-danger" data-force="${v.version}">Force…</button>` : ""}</div>`]),
          m.trainable ? "No versions yet. Start one on the Training page." : "This model has no train section.")}</div>
        <div class="card card-flush"><h2>History</h2>${table(["When", "Version", "Event", "Detail"],
          events.slice(0, 20).map((e) => [`<span class="nowrap">${fmt.ago(e.ts)}</span>`, e.version ? "v" + h(e.version) : "—", h(e.action), detail(e.detail)]),
          "No events yet.")}</div>
      </div>
    </div>`;
  el.querySelectorAll("[data-go]").forEach((r) => r.addEventListener("click", () => (location.hash = r.dataset.go)));
  el.querySelectorAll("[data-report]").forEach((b) => b.addEventListener("click", () => showReport(b.dataset.report)));
  el.querySelectorAll("[data-promote]").forEach((b) => b.addEventListener("click", () => promote(name, b.dataset.promote, false)));
  el.querySelectorAll("[data-force]").forEach((b) => b.addEventListener("click", () => promote(name, b.dataset.force, true)));
}

async function promote(name, version, force) {
  let reason = null;
  if (force) {
    reason = prompt(`v${version} did not pass its gate. Promoting it anyway is recorded. Reason:`);
    if (!reason) return;
  } else if (!confirm(`Put ${name}:v${version} live? The running engine restarts to serve it.`)) return;
  try {
    const r = await api(`models/${encodeURIComponent(name)}/promote`, { body: { version: Number(version), force, reason } });
    toast(`${r.live} is live${r.restarted ? " (engine restarted)" : ""}${r.warning ? " — " + r.warning : ""}`, !!r.warning);
    route();
  } catch (e) { toast(e.message, true); }
}

async function showReport(path) {
  try {
    const r = await api("report?path=" + encodeURIComponent(path));
    modal(`Gate report · ${r.model}`, `<div class="report">${markdown(r.markdown)}</div>`);
  } catch (e) { toast(e.message, true); }
}

async function training(el, selectedJob) {
  const ov = await api("overview");
  const trainable = ov.models.filter((m) => m.trainable);
  el.innerHTML = `
    <div class="card">
      <div class="card-head"><h2>Start training</h2></div>
      ${trainable.length ? `<form id="train-form" class="row">
        <div class="field"><label for="train-model">Model</label><select id="train-model">${trainable.map((m) => `<option>${h(m.name)}</option>`).join("")}</select></div>
        <button class="btn btn-primary" type="submit">Snapshot, train, then gate</button></form>
        <p class="muted small">Held-out and audit rows are never trained on. The new version is gated against the live one when training ends.</p>`
        : `<p class="muted">No model in ookami.yaml has a <code>train</code> section.</p>`}
    </div>
    <div class="card card-flush"><h2>Jobs</h2><div id="jobs"></div></div>
    <div class="card"><div class="card-head"><h2>Log</h2><span id="log-id" class="pill hidden"></span></div><pre id="log" class="log">Select a job to see its trainer log.</pre></div>`;
  $("#train-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("train", { body: { model: $("#train-model").value } });
      toast(`${r.version} queued: ${r.rows.train} training rows, plan ${r.plan.engine} LoRA r=${r.plan.rank}, ${r.plan.iters} iters`);
      location.hash = "#/training/" + encodeURIComponent(r.job);
    } catch (err) { toast(err.message, true); }
  });
  let current = selectedJob;
  const refresh = async () => {
    const jobs = await api("jobs");
    $("#jobs").innerHTML = table(["Job", "State", "Message", "Gate"],
      jobs.map((j) => [`<a href="#/training/${encodeURIComponent(j.id)}" class="mono">${h(j.id)}</a>`, badge(j.state), h(j.message), j.gate ? badge(j.gate) : "—"]),
      "No training jobs yet.");
    if (current) {
      const log = await api(`jobs/${encodeURIComponent(current)}/logs?lines=300`);
      const pre = $("#log");
      const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      pre.textContent = log.log || "(empty)";
      $("#log-id").textContent = current;
      $("#log-id").classList.remove("hidden");
      if (atBottom) pre.scrollTop = pre.scrollHeight;
    }
  };
  await refresh();
  timers.push(setInterval(() => refresh().catch(() => {}), 3000));
}

async function keys(el) {
  const list = await api("keys");
  el.innerHTML = `
    <div class="card">
      <h2>Create a key</h2>
      <form id="key-form" class="row">
        <div class="field"><label for="k-name">Name</label><input id="k-name" required placeholder="alice"></div>
        <div class="field"><label for="k-team">Team</label><input id="k-team" placeholder="default"></div>
        <div class="field"><label for="k-budget">Budget (USD / month)</label><input id="k-budget" type="number" min="0" step="0.01" placeholder="none"></div>
        <div class="field"><label for="k-rpm">Requests / minute</label><input id="k-rpm" type="number" min="1" step="1" placeholder="none"></div>
        <button class="btn btn-primary" type="submit">Create</button>
      </form>
    </div>
    <div class="card card-flush"><h2>Keys</h2>${table(["Name", "Team", "Key", "State", "API spend this month", "Limit", ""],
      list.map((k) => [h(k.name), h(k.team), `<span class="mono">${h(k.prefix)}…</span>`, badge(k.revoked ? "revoked" : "active"),
        k.budget_usd != null ? `<div class="bar ${k.spent_this_month >= k.budget_usd ? "over" : ""}" data-pct="${Math.min(100, (k.spent_this_month / (k.budget_usd || 1e-9)) * 100)}"><span></span></div><span class="small muted">${fmt.usd(k.spent_this_month)} of ${fmt.usd(k.budget_usd)}</span>` : fmt.usd(k.spent_this_month),
        k.rpm ? `${k.rpm}/min` : "—",
        k.revoked ? "" : `<button class="btn btn-sm btn-danger" data-revoke="${h(k.name)}">Revoke</button>`]),
      "No keys yet. The master key works everywhere; create per-person or per-app keys here.")}</div>`;
  el.querySelectorAll(".bar[data-pct]").forEach((b) => ($("span", b).style.width = b.dataset.pct + "%"));
  $("#key-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("keys", { body: { name: $("#k-name").value, team: $("#k-team").value, budget_usd: $("#k-budget").value, rpm: $("#k-rpm").value } });
      modal("Key created", `<p>Copy it now. Ookami stores only its hash, so it can't be shown again.</p>
        <div class="keybox"><span id="new-key">${h(r.key)}</span><button id="copy-key" class="btn btn-sm">Copy</button></div>`);
      $("#copy-key").addEventListener("click", () => navigator.clipboard.writeText(r.key).then(() => toast("Copied")));
      route();
    } catch (err) { toast(err.message, true); }
  });
  el.querySelectorAll("[data-revoke]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`Revoke ${b.dataset.revoke}? Calls with it will be refused immediately.`)) return;
    try { await api(`keys/${encodeURIComponent(b.dataset.revoke)}/revoke`, { body: {} }); toast("Revoked"); route(); }
    catch (err) { toast(err.message, true); }
  }));
}

async function usage(el) {
  const state = usage.state || (usage.state = { since: "30d", by: "key" });
  const u = await api(`usage?since=${state.since}&by=${state.by}`);
  const hosted = Object.entries(u.models).filter(([, v]) => v.hardware_cost);
  el.innerHTML = `
    <div class="row">
      <div class="segmented" id="since">${["24h", "7d", "30d"].map((s) => `<button class="btn btn-sm ${s === state.since ? "on" : ""}" data-v="${s}">${s}</button>`).join("")}</div>
      <div class="segmented" id="by">${["key", "team"].map((s) => `<button class="btn btn-sm ${s === state.by ? "on" : ""}" data-v="${s}">by ${s}</button>`).join("")}</div>
    </div>
    <div class="card"><h2>Requests per day</h2>${chart(u.daily)}</div>
    <div class="card card-flush"><h2>Spend by ${h(state.by)}</h2>${table([state.by, "Requests", "Errors", "Tokens", "API", "Self-hosted", "Total"],
      u.rows.map((r) => [h(r.who), num(fmt.n(r.requests)), num(fmt.n(r.errors)), num(fmt.n(r.tokens)), num(fmt.usd(r.api_cost)), num(fmt.usd(r.self_hosted_cost)), num(`<strong>${fmt.usd(r.total)}</strong>`)]),
      "No gateway traffic in this period.")}</div>
    <div class="card card-flush"><h2>Self-hosted models</h2>${table(["Model", "Tokens", "Hardware cost", "Per 1M tokens"],
      hosted.map(([m, v]) => [h(m), num(fmt.n(v.tokens)), num(fmt.usd(v.hardware_cost)), num(v.cost_per_million_tokens != null ? fmt.usd(v.cost_per_million_tokens) : "—")]),
      "Set <code>serve.costPerHour</code> on a model to see what its tokens cost, next to API prices.")}</div>`;
  el.querySelectorAll("#since button").forEach((b) => b.addEventListener("click", () => { state.since = b.dataset.v; route(); }));
  el.querySelectorAll("#by button").forEach((b) => b.addEventListener("click", () => { state.by = b.dataset.v; route(); }));
}

async function playground(el) {
  const ov = await api("overview");
  const history = playground.history || (playground.history = []);
  el.innerHTML = `
    <div class="grid grid-2">
      <div class="card">
        <label for="pg-model">Model</label>
        <select id="pg-model">${ov.models.map((m) => `<option>${h(m.name)}</option>`).join("")}</select>
        <label for="pg-system">System prompt</label>
        <textarea id="pg-system" placeholder="Optional"></textarea>
        <div class="row">
          <div class="field"><label for="pg-temp">Temperature</label><input id="pg-temp" type="number" min="0" max="2" step="0.1" value="0"></div>
          <div class="field"><label for="pg-max">Max tokens</label><input id="pg-max" type="number" min="1" step="1" value="512"></div>
        </div>
        <p class="muted small">Calls go through the gateway with the master key, so they show up in Usage.</p>
        <button id="pg-clear" class="btn btn-sm">Clear conversation</button>
      </div>
      <div class="card">
        <div id="chat" class="chat"></div>
        <form id="pg-form" class="row">
          <div class="field"><label for="pg-input">Message</label><textarea id="pg-input" required placeholder="Ask something…"></textarea></div>
          <button class="btn btn-primary" type="submit">Send</button>
        </form>
      </div>
    </div>`;
  const draw = () => {
    $("#chat").innerHTML = history.length ? history.map((m) => `<div class="msg ${m.role}">${h(m.content)}${m.meta ? `<span class="meta">${h(m.meta)}</span>` : ""}</div>`).join("")
      : `<div class="muted">Pick a model and send a message.</div>`;
    $("#chat").scrollTop = $("#chat").scrollHeight;
  };
  draw();
  $("#pg-clear").addEventListener("click", () => { history.length = 0; draw(); });
  $("#pg-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#pg-input");
    history.push({ role: "user", content: input.value });
    input.value = "";
    draw();
    const sys = $("#pg-system").value.trim();
    const messages = [...(sys ? [{ role: "system", content: sys }] : []), ...history.map(({ role, content }) => ({ role, content }))];
    try {
      const r = await api("chat", { body: { model: $("#pg-model").value, messages, temperature: $("#pg-temp").value, max_tokens: $("#pg-max").value } });
      const tokens = r.usage ? `${r.usage.prompt_tokens} in · ${r.usage.completion_tokens} out · ` : "";
      history.push({ role: "assistant", content: r.message.content || JSON.stringify(r.message.tool_calls || ""), meta: `${$("#pg-model").value} · ${tokens}${r.latency_ms} ms` });
    } catch (err) {
      history.push({ role: "assistant", content: "Error: " + err.message });
    }
    draw();
  });
}

// ------------------------------------------------------------------ helpers
// Event details are JSON; show them as short key: value pairs with paths cut to their last parts.
function detail(raw) {
  let d;
  try { d = JSON.parse(raw || "{}"); } catch { return `<span class="small">${h(raw)}</span>`; }
  const short = (v) => (typeof v === "string" && v.includes("/") ? "…/" + v.split("/").slice(-2).join("/") : v);
  const parts = Object.entries(d).filter(([, v]) => v !== null && v !== "" && v !== false)
    .map(([k, v]) => `<span class="kv"><span class="muted">${h(k)}</span> ${h(short(v))}</span>`);
  return parts.length ? parts.join(" ") : `<span class="muted small">—</span>`;
}

function stat(label, value, sub) {
  return `<div class="card stat"><div class="label">${h(label)}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
}
const num = (v) => ({ num: true, html: v });
function table(head, rows, empty) {
  if (!rows.length) return `<div class="empty">${empty}</div>`;
  const cell = (c, tag) => (c && c.num ? `<${tag} class="num">${c.html}</${tag}>` : `<${tag}>${c}</${tag}>`);
  return `<div class="table-wrap"><table><thead><tr>${head.map((x, i) => (rows[0][i] && rows[0][i].num ? `<th class="num">${h(x)}</th>` : `<th>${h(x)}</th>`)).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => cell(c, "td")).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function chart(daily) {
  if (!daily.length) return `<div class="empty">No traffic yet.</div>`;
  const W = 720, H = 160, pad = 24, max = Math.max(...daily.map((d) => d.requests), 1);
  const bw = Math.min((W - pad * 2) / daily.length, 56);
  const bars = daily.map((d, i) => {
    const bh = ((H - pad * 2) * d.requests) / max;
    const x = pad + i * bw + bw * 0.15, y = H - pad - bh;
    const label = new Date(d.day * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    return `<rect class="col" x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw * 0.7).toFixed(1)}" height="${bh.toFixed(1)}" rx="3"><title>${h(label)}: ${d.requests} requests, ${d.tokens} tokens</title></rect>
      ${daily.length <= 14 ? `<text x="${(x + bw * 0.35).toFixed(1)}" y="${H - 6}" text-anchor="middle">${h(label)}</text>` : ""}`;
  }).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Requests per day">
    <line class="axis" x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}"></line>${bars}
    <text x="${pad}" y="12">${fmt.n(max)} max/day</text></svg>`;
}
// Just enough Markdown for gate reports: headings, lists, tables, inline code, bold. Input is escaped first.
function markdown(src) {
  const inline = (s) => h(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const out = [];
  const lines = (src || "").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      i--;
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const body = rows.filter((r) => !/^\|[\s|:-]+\|$/.test(r));
      out.push(`<div class="table-wrap"><table><thead><tr>${cells(body[0]).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.slice(1).map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    } else if (l.startsWith("# ")) out.push(`<h3>${inline(l.slice(2))}</h3>`);
    else if (l.startsWith("- ")) out.push(`<div class="small">• ${inline(l.slice(2))}</div>`);
    else if (l.trim()) out.push(`<p class="small">${inline(l)}</p>`);
  }
  return out.join("");
}

// ------------------------------------------------------------------ boot
$("#signin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#signin-error").classList.add("hidden");
  try { await signIn($("#signin-key").value); }
  catch (err) { $("#signin-error").textContent = err.message; $("#signin-error").classList.remove("hidden"); }
});
$("#signout").addEventListener("click", signOut);
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
window.addEventListener("hashchange", () => token && route());
// One-click sign-in: #key=<master key>. A URL fragment never reaches the server or its logs, and it is removed
// from the address bar straight away.
if (location.hash.startsWith("#key=")) {
  const params = new URLSearchParams(location.hash.slice(1));
  token = params.get("key") || "";
  const next = (params.get("next") || "/overview").replace(/[^\w/.-]/g, "");
  history.replaceState(null, "", location.pathname + "#" + next);
}
if (token) signIn(token).catch(() => signOut()); else signOut();
