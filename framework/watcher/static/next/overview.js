// A run at a glance: headline figures, budgets, its agents, where its tasks run, and
// the details of the run itself.
import { html, useState, hms, short, usd, score, plural, when, tone, copyText } from "./util.js";

const modelCost = s => (s.cost_models || []).reduce((t, m) => t + (m.usd || 0), 0);
const hasCost = s => (s.cost_models || []).some(m => m.usd != null);

function Kpi({label, value, sub, title, text}) {
  return html`<div class="kpi" title=${title || ""}>
    <div class="label">${label}</div>
    <div class="value ${text ? "text" : ""}">${value}</div>
    ${sub && html`<div class="sub">${sub}</div>`}
  </div>`;
}

function Kpis({s}) {
  const placer = s.kind === "resources";
  const runtime = (s.budgets || []).find(b => b.name === "runtime_s");
  const inFlight = s.jobs_run - s.jobs_done - (s.jobs_abandoned || 0);
  const primary = s.primary_score && (s.scores || {})[s.primary_score];
  const state = s.status === "running"
    ? (s.phase || "running") : (s.stop_reason || s.status || "—");
  const stateSub = s.status === "running"
    ? `${hms(s.phase_age_s)} in this step · heartbeat ${hms(s.heartbeat_age_s)} ago`
    : s.turns ? `after ${plural(s.turns, "turn")}` : "";
  return html`<div class="kpis">
    <${Kpi} label=${s.status === "running" ? "Doing" : "Stopped"} value=${state} sub=${stateSub} title=${state} text />
    <${Kpi} label="Run time" value=${hms(s.elapsed_s)}
            sub=${runtime && runtime.limit ? `of ${hms(runtime.limit)} allowed` : when(s.started_at) + " start"} />
    ${s.show_cost !== false && html`<${Kpi} label="Cost" value=${hasCost(s) ? usd(modelCost(s)) : "—"}
            sub=${s.tree_usd != null ? `${usd(s.tree_usd)} with workers` : "this agent"} />`}
    ${placer
      ? html`<${Kpi} label="Checks" value=${s.turns || 0} sub=${plural((s.routes || []).length, "route") + " set"} />`
      : html`<${Kpi} label="Tasks returned" value=${`${s.jobs_done} / ${s.jobs_run}`}
               sub=${(inFlight > 0 ? `${inFlight} in flight` : "none in flight")
                     + (s.jobs_abandoned ? ` · ${s.jobs_abandoned} abandoned` : "")} />`}
    ${primary != null && html`<${Kpi} label="Best score" value=${score(primary)} sub=${s.primary_score} />`}
  </div>`;
}

function Card({title, right, children, flush, more}) {
  return html`<section class="card">
    <h2>${title}${right != null && html`<span class="right">${right}</span>`}${more && html`<a class="more" href="#" onClick=${e => { e.preventDefault(); more[1](); }}>${more[0]} →</a>`}</h2>
    ${flush ? children : html`<div class="body">${children}</div>`}
  </section>`;
}

// A budget is a ceiling, not a target: the bar shows how much of it is used.
function Budgets({s, onTab}) {
  const rows = s.budgets || [];
  if (!rows.length) return null;
  return html`<${Card} title="Budgets" more=${["Details", () => onTab("budgets")]}>
    ${rows.map(r => {
      const fmt = r.unit === "s" ? hms : r.unit === "h" ? (v => (+v).toFixed(1)) : String;
      const used = r.used || 0;
      const pct = r.limit ? Math.min(100, 100 * used / r.limit) : 0;
      const cls = pct >= 100 ? "warn" : "";
      return html`<div class="budget">
        <span class="what" title=${r.label}>${r.label}</span>
        ${r.limit == null
          ? html`<span class="faint">no limit</span>`
          : html`<div class="meter ${cls}"><i style=${`width:${pct}%`}></i></div>`}
        <span class="amt">${fmt(used)}${r.limit != null && html`<span class="faint"> / ${fmt(r.limit)}</span>`}</span>
      </div>`;
    })}
  </${Card}>`;
}

// The run and every worker it started, as a tree by who started whom. Agents the
// framework started (a resource agent) sit beside the run, not under it.
function Agents({top, agent, onAgent}) {
  const ws = (top && top.workers) || [];
  if (!ws.length) return null;
  const costs = top.show_cost !== false;
  const fw = ws.filter(w => w.started_by === "framework" || w.kind === "resources");
  const own = ws.filter(w => !fw.includes(w));
  const byBranch = Object.fromEntries(ws.map(w => [w.branch, w]));
  const me = {run: "", branch: own.length ? "coordinator" : "researcher", method: top.method,
              status: top.status, usd: modelCost(top), score: null};
  const rows = [];
  const row = (a, depth, extra) => rows.push(html`
    <div class="agent ${a.run === agent ? "on" : ""}" onClick=${() => onAgent(a.run)}>
      <span class="who" style=${`padding-left:${depth * 18}px`}>
        ${depth > 0 && html`<span class="elbow">└</span>`}
        <span class="dot ${tone(a.status)}"></span>
        <b>${a.branch || a.run}</b>
        <span class="faint">${(a.method || a.kind || "") + (extra || "")}</span>
      </span>
      <span class="score">${score(a.score)}</span>
      <span class="usd">${costs ? usd(a.usd) : ""}</span>
    </div>`);
  const below = (branch, depth) => own
    .filter(w => (byBranch[w.parent] ? w.parent : null) === branch)
    .forEach(w => { row(w, depth); below(w.branch, depth + 1); });
  fw.forEach(w => row(w, 0, " · framework"));
  row(me, 0);
  below(null, 1);
  const running = ws.filter(w => w.status === "running").length;
  return html`<${Card} title="Agents" right=${`${running} of ${ws.length} running`} flush>
    <div class="agents">
      ${rows}
      <div class="total">
        <span>${[own.length ? plural(own.length, "worker") : "", fw.length ? `${fw.length} from the framework` : ""]
                 .filter(Boolean).join(" + ")}</span>
        ${costs && html`<span class="num">total ${usd(top.tree_usd)}</span>`}
      </div>
    </div>
  </${Card}>`;
}

// Where the run's tasks go: each bucket's block shape and what is in flight in it.
function Compute({s, onTab}) {
  const shapes = Object.entries(s.buckets || {});
  const where = s.endpoint
    ? (Object.keys(s.systems || {}).length ? Object.keys(s.systems) : [s.system || "?"])
        .join(", ") + (s.has_local ? ", this machine" : "")
    : "this machine";
  return html`<${Card} title="Compute" right=${s.kind === "resources" ? "places work" : "tasks run on " + where} flush more=${["Resources", () => onTab("resources")]}>
    ${shapes.length ? html`<table class="table">
      <thead><tr><th>Bucket</th><th>System</th><th>Queue</th><th class="r">Nodes / block</th>
        <th class="r">Walltime</th><th class="r">Max blocks</th><th class="r">In flight</th></tr></thead>
      <tbody>${shapes.map(([k, b]) => {
        const out = s.status === "running" ? ((s.inflight_bucket || {})[k] || 0) : 0;
        return html`<tr>
          <td>${k}${k === s.default_bucket && shapes.length > 1 ? html` <span class="tag">default</span>` : ""}</td>
          <td>${b.system || "—"}</td><td>${b.queue || b.qos || "—"}</td>
          <td class="r">${b.num_nodes ?? "—"}</td><td class="r">${b.walltime || "—"}</td>
          <td class="r">${b.max_blocks || 1}</td>
          <td class="r" style=${out ? "color:var(--ok)" : ""}>${out}</td></tr>`;
      })}</tbody></table>`
      : html`<div class="body muted">No batch buckets: tasks run on ${where}.</div>`}
  </${Card}>`;
}

function Routes({s}) {
  if (s.kind !== "resources") return null;
  const routes = s.routes || [];
  return html`<${Card} title="Routes set" right=${routes.length}>
    ${routes.length ? html`<dl class="kv">${routes.map(r => html`
      <dt class="num">${new Date(r.ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</dt>
      <dd>${r.from} → ${r.after || "(back)"} ${r.reason && html`<span class="faint">${r.reason}</span>`}</dd>`)}</dl>`
      : html`<span class="faint">none</span>`}
  </${Card}>`;
}

function Session({id, running}) {
  const [copied, setCopied] = useState(false);
  if (!id) return "—";
  // Copying is offered once the run has stopped: a session the runner still holds
  // should not get a second client.
  return html`<span class="mono">${id}</span>
    ${!running && html` <button class="btn sm" onClick=${async () => {
      setCopied(await copyText(id)); setTimeout(() => setCopied(false), 1500);
    }}>${copied ? "Copied" : "Copy"}</button>`}`;
}

function Context({s}) {
  if (s.context_tokens == null) return html`<span class="faint">no data</span>`;
  if (s.context_pct == null) return html`${short(s.context_tokens)} <span class="faint">(window not known yet)</span>`;
  const pct = Math.min(100, s.context_pct);
  return html`<div style="display:flex;align-items:center;gap:10px">
    <div class="meter ${pct >= 85 ? "warn" : ""}" style="flex:1;max-width:140px"><i style=${`width:${pct}%`}></i></div>
    <span class="num">${short(s.context_tokens)} / ${short(s.context_window)}</span>
    <span class="faint">${Math.round(s.context_pct)}%</span></div>`;
}

function RunDetails({s, onAgent}) {
  const rows = [
    ["Run", html`<span class="mono">${s.run}</span>`],
    ["Model", s.model || "—"],
    s.effort && ["Effort", s.effort],
    s.advisor && ["Advisor", s.advisor],
    s.method && ["Method", s.method],
    ["Context", html`<${Context} s=${s} />`],
    s.critic && !/^(none|no critic)/.test(s.critic) && ["Critic", s.critic],
    s.parent_run && ["Started by", s.parent_run_known
      ? html`<a href="#" onClick=${e => { e.preventDefault(); onAgent(""); }}>${s.parent_run}</a>`
      : html`${s.parent_run} <span class="faint">(no such run here)</span>`],
    s.parent_run && s.kind !== "resources" && ["Works on", `${s.branch || "—"} (${s.kind || "?"})`],
    ["Host", s.host || "—"],
    ["Started", when(s.started_at)],
    s.ended_at && ["Ended", when(s.ended_at)],
    ["Session", html`<${Session} id=${s.session_id} running=${s.status === "running"} />`],
  ].filter(Boolean);
  return html`<${Card} title="Run">
    <dl class="kv">${rows.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
  </${Card}>`;
}

function Cost({s}) {
  const models = [...(s.cost_models || [])].sort((a, b) => (b.usd || 0) - (a.usd || 0));
  if (!models.length || s.show_cost === false) return null;
  return html`<${Card} title="Cost by model" flush>
    <table class="table"><thead><tr><th>Model</th><th class="r">In</th><th class="r">Out</th><th class="r">Cost</th></tr></thead>
      <tbody>${models.map(m => html`<tr><td>${m.model.replace(/-\d{8}$/, "")}</td>
        <td class="r">${short(m.input_tokens)}</td><td class="r">${short(m.output_tokens)}</td>
        <td class="r">${usd(m.usd)}</td></tr>`)}</tbody></table>
  </${Card}>`;
}

function Scores({s}) {
  const entries = Object.entries(s.scores || {})
    .sort(([a], [b]) => (b === s.primary_score) - (a === s.primary_score));
  if (!entries.length) return null;
  return html`<${Card} title="Scores">
    <dl class="kv">${entries.map(([k, v]) => html`
      <dt>${k}${k === s.primary_score && html` <span class="tag">primary</span>`}</dt>
      <dd class="num" style=${k === s.primary_score ? "font-weight:600" : ""}>${score(v)}</dd>`)}</dl>
  </${Card}>`;
}

// The campaign outlives the run: these count every run of it.
function Totals({s}) {
  return html`<${Card} title="Campaign totals" right="all runs">
    <dl class="kv">
      <dt>Tasks submitted</dt><dd class="num">${s.jobs}</dd>
      <dt>Results recorded</dt><dd class="num">${s.results}</dd>
      ${s.reviews > 0 && html`<dt>Critic reviews</dt><dd class="num">${s.reviews}</dd>`}
    </dl>
  </${Card}>`;
}

export function Overview({top, s, agent, onAgent, onTab}) {
  if (!s) return html`<div class="empty">Loading…</div>`;
  if (!s.run) return html`<div class="empty">This campaign has no run yet.</div>`;
  return html`
    <${Kpis} s=${s} />
    <div class="cols">
      <div class="col">
        <${Budgets} s=${s} onTab=${onTab} />
        <${Agents} top=${top} agent=${agent} onAgent=${onAgent} />
        <${Compute} s=${s} onTab=${onTab} />
        <${Routes} s=${s} />
      </div>
      <div class="col">
        <${RunDetails} s=${s} onAgent=${onAgent} />
        <${Scores} s=${s} />
        <${Cost} s=${s} />
        <${Totals} s=${s} />
      </div>
    </div>`;
}
