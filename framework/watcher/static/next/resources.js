// Per system: its buckets and who shares them, what the system itself reports (IRI),
// the batch jobs (blocks) that were up while this run was, and the tasks each ran.
import { html, useState, usePoll, getJSON, hms, plural } from "./util.js";
import { Meter } from "./budgets.js";

const clock = t => t ? new Date(t * 1000).toLocaleString([], {weekday: "short", hour: "2-digit", minute: "2-digit"}) : "—";
const dur = s => s == null ? "—" : hms(Math.round(s));
const remember = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const recall = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } };

// A task in flight, made more exact by your batch jobs in its bucket's queue: queued
// when every one there is queued or held.
function taskState(sy, t) {
  const jobs = sy.iri && sy.iri.your_jobs;
  const q = ((sy.buckets || {})[t.bucket] || {}).queue;
  if (t.state !== "waiting or running" || !jobs || !q) return t.state;
  const here = jobs.filter(j => j.queue === q);
  if (here.length && here.every(j => j.state === "queued" || j.state === "held"))
    return `waiting: block ${here.map(j => j.id).join(", ")} ${here[0].state} in ${q}`;
  if (here.some(j => j.state === "active")) return `waiting or running (a block of yours in ${q} is running)`;
  return t.state;
}

function perBlock(b) {
  const n = b.num_nodes || 1, per = b.nodes_per_job || n, w = b.workers_per_node || 1;
  const slots = b.jobs_per_block || n / per;
  return slots <= 1 ? `one ${n}-node task at a time`
    : w > 1 ? `${slots} tasks at once` + (per === 1 ? `, ${w} per node` : "")
    : `${slots} ${per}-node tasks at once`;
}

function Stat({label, value, sub}) {
  return html`<div class="stat"><div class="label">${label}</div><div class="value num">${value}</div>${sub && html`<div class="sub">${sub}</div>`}</div>`;
}

// A section shown as one line until opened; whether it is open is remembered.
function Section({name, title, summary, children}) {
  const [open, setOpen] = useState(() => recall(name, "") === "1");
  return html`<div class="sect">
    <button class="secthead" onClick=${() => { setOpen(!open); remember(name, open ? "" : "1"); }}>
      <span class="caret">${open ? "▾" : "▸"}</span><b>${title}</b>${summary}</button>
    ${open && html`<div class="sectbody">${children}</div>`}
  </div>`;
}

// What the system reports through its IRI API: its state, how busy the queues are, and
// your batch jobs there. Open incidents are shown whether or not the section is open.
function Facility({iri}) {
  if (!iri) return null;
  if (!iri.checked_at) return html`<div class="body faint sect">Waiting for the system's first status report.</div>`;
  const queues = Object.entries(iri.queues || {});
  const jobs = iri.your_jobs || [];
  const states = {};
  for (const j of jobs) states[j.state] = (states[j.state] || 0) + 1;
  const summary = html`
    ${iri.status && html`<span class="pill ${iri.status === "active" || iri.status === "up" ? "ok" : "warn"}">${iri.status}</span>`}
    <span class="muted">${jobs.length ? `${plural(jobs.length, "batch job")} of yours: ${Object.entries(states).map(([k, n]) => `${n} ${k}`).join(", ")}`
      : "no batch jobs of yours"}</span>
    <span class="faint" style="margin-left:auto">checked ${hms(Date.now() / 1000 - iri.checked_at)} ago</span>`;
  return html`
    <${Section} name="resFacility" title="Facility" summary=${summary}>
      <div class="facility">
        ${queues.length > 0 && html`<div class="factrow"><span class="muted">Queues:</span>
          ${queues.map(([q, n]) => html`<span class="qcount"><b>${q}</b> ${n.active} running · ${n.queued} queued${n.held ? ` · ${n.held} held` : ""}</span>`)}</div>`}
        ${jobs.length > 0 && html`<div class="factrow"><span class="muted">Your batch jobs:</span>
          ${jobs.map(j => html`<span class="tag" title=${j.message || ""}>${j.id} · ${j.queue || "?"} · ${j.state}</span>`)}</div>`}
        ${(iri.jobs_error || iri.status_error) && html`<div class="faint">${iri.status_error || ""} ${iri.jobs_error || ""}</div>`}
      </div>
    <//>
    ${(iri.incidents || []).length > 0 && html`<div class="body facility">${iri.incidents.map(i => html`<div class="incident">
      <b>${i.name}</b> <span class="faint">${i.type || ""} · ${i.status || ""}</span>
      ${i.description && html`<div class="muted">${i.description}</div>`}</div>`)}</div>`}`;
}

function Buckets({sy}) {
  const buckets = Object.entries(sy.buckets || {});
  if (!buckets.length) return null;
  const summary = html`<span class="muted">${buckets.map(([k, b]) => `${k} → ${b.queue || "?"}`).join(" · ")}</span>`;
  return html`<${Section} name="resBuckets" title="Buckets" summary=${summary}>
    <table class="table">
      <thead><tr><th>Bucket</th><th>Queue</th><th class="r">Nodes / block</th><th>Per block</th><th class="r">Walltime</th><th class="r">Max blocks</th><th>Sent by</th></tr></thead>
      <tbody>${buckets.map(([k, b]) => {
        const who = (sy.sharing || {})[k] || [];
        return html`<tr><td><b style="font-weight:500">${k}</b></td><td>${b.queue || "?"}</td><td class="r">${b.num_nodes || 1}</td>
          <td class="muted">${perBlock(b)}</td><td class="r">${b.walltime || "?"}</td><td class="r">${b.max_blocks || 1}</td>
          <td class="muted" title=${who.join(", ")}>${who.length > 2 ? `${who.slice(0, 2).join(", ")} +${who.length - 2}` : who.join(", ") || "—"}</td></tr>`;
      })}</tbody></table>
  <//>`;
}

function BlockRows({b, pick, mark}) {
  const [open, setOpen] = useState(false);
  const events = b.tasks.filter(pick).map(t => ({at: t.started, t, ran: true}))
    .concat(b.refused.filter(pick).map(x => ({at: x.at, t: x, ran: false})))
    .sort((p, q) => p.at - q.at);
  return html`
    <tr class="click blockrow" onClick=${() => setOpen(!open)}>
      <td><span class="caret">${open ? "▾" : "▸"}</span> <b>${b.id}</b></td><td>${b.queue || "—"}</td>
      <td class="r">${b.nodes ?? "—"}</td>
      <td class="r">${dur(b.queued_at && b.start ? b.start - b.queued_at : null)}</td>
      <td>${clock(b.start)}</td><td class="r">${dur(b.walltime_s)}</td>
      <td class="r">${dur(b.start && b.last ? b.last - b.start : null)}</td>
      <td class="r">${b.node_hours ?? "—"}</td>
      <td class="r">${b.tasks.length}${b.refused.length ? html` <span class="faint">+${b.refused.length} postponed</span>` : ""}</td>
    </tr>
    ${open && events.map(({t, ran}) => html`<tr class="taskrow ${mark(t)}">
      <td colspan="3" class="indent">${t.agent} · task ${t.job_id}</td>
      <td class="r">${ran ? dur(t.started && t.submitted ? t.started - t.submitted : null) : ""}</td>
      <td>${clock(ran ? t.started : t.at)}</td>
      <td class="r">${ran ? dur(t.ran_s) : ""}</td><td></td>
      <td colspan="2" class=${ran ? (t.state === "done" ? "" : "badtext") : "warntext"}>
        ${ran ? t.state + (t.retries ? ` · ${t.retries} ${t.retries === 1 ? "retry" : "retries"}` : "") : "postponed: " + (t.reason || "")}</td>
    </tr>`)}`;
}

function BlockTable({sy, pick, mark, only}) {
  const blocks = sy.blocks.filter(b => !only || b.tasks.some(t => t.mine) || b.refused.some(x => x.mine));
  if (!blocks.length) return html`<div class="body faint">No blocks yet.</div>`;
  return html`<table class="table">
    <thead><tr><th>Block</th><th>Queue</th><th class="r">Nodes</th><th class="r">Waited</th><th>Started</th>
      <th class="r">Walltime</th><th class="r">To last task</th><th class="r">Node-hours</th><th class="r">Tasks</th></tr></thead>
    <tbody>${blocks.map(b => html`<${BlockRows} key=${b.id} b=${b} pick=${pick} mark=${mark} />`)}</tbody>
  </table>`;
}

// One block over its walltime, a lane per worker grouped by node. A task names its
// worker where the engine gives one; otherwise a node's tasks are packed into as few
// lanes as hold them, which shows how many ran at once but not which worker.
function Timeline({b, shapes, pick, mark}) {
  const perNode = Math.max(1, ...Object.values(shapes).map(s => s.workers_per_node || 1));
  const bt = b.tasks.find(t => shapes[t.bucket]);
  const shape = bt ? shapes[bt.bucket] : null;
  const perJob = shape ? (shape.nodes_per_job || shape.num_nodes || 1) : 1;
  const workers = perJob > 1 ? (shape.jobs_per_block || Math.max(1, Math.floor((b.nodes || perJob) / perJob))) : null;
  const span = b.walltime_s || Math.max(1, (b.end || b.last) - b.start);
  const pos = t => Math.max(0, Math.min(100, 100 * (t - b.start) / span));
  const bars = b.tasks.filter(pick).filter(t => t.started && t.ran_s != null)
    .map(t => ({on: t.ran_on || {}, from: t.started, to: t.started + t.ran_s, kind: t.state === "done" ? "" : "error", t,
                tip: `${t.agent} · task ${t.job_id} · ${dur(t.ran_s)} · ${t.state}`}))
    .concat(b.refused.filter(pick).map(x => {
      const from = x.ran_on.started_at || x.at;
      return {on: x.ran_on, from, to: x.ran_on.ended_at || from, kind: "postponed", t: x,
              tip: `${x.agent} · task ${x.job_id} · postponed: ${x.reason || ""}`};
    })).sort((p, q) => p.from - q.from);
  const nodes = {};
  for (const x of bars) {
    const host = x.on.host || "?";
    const n = nodes[host] = nodes[host] || {named: {}, packed: []};
    if (x.on.worker != null) (n.named[x.on.worker] = n.named[x.on.worker] || []).push(x);
    else {
      let lane = n.packed.find(l => l.end <= x.from);
      if (!lane) n.packed.push(lane = {end: 0, bars: []});
      lane.bars.push(x); lane.end = Math.max(lane.end, x.to);
    }
  }
  const busy = bars.filter(x => x.kind !== "postponed").reduce((a, x) => a + x.to - x.from, 0);
  const laneCount = workers || (b.nodes || Object.keys(nodes).length) * perNode;
  const used = (b.end || b.last) - b.start;
  const now = Date.now() / 1000;
  const showNow = now < b.start + span && (!b.end || now - b.end < 5);
  const rows = [];
  const hosts = Object.keys(nodes).sort();
  for (const host of hosts) {
    const n = nodes[host];
    const list = Object.keys(n.named).sort((p, q) => p - q).map(w => [`${host} · ${w}`, n.named[w]])
      .concat(n.packed.map((l, i) => [i ? "" : host, l.bars]));
    if (!workers) for (let i = list.length; i < perNode; i++) list.push([list.length ? "" : host, []]);
    rows.push(...list);
  }
  if (workers) {
    const usedLanes = hosts.reduce((a, h) => a + Object.keys(nodes[h].named).length + nodes[h].packed.length, 0);
    for (let i = usedLanes; i < workers; i++) rows.push(["worker ran no task", []]);
  } else {
    for (let i = hosts.length; i < (b.nodes || 0); i++)
      for (let w = 0; w < perNode; w++) rows.push([w ? "" : "node ran no task", []]);
  }
  return html`<div class="tlblock">
    <div class="tlhead"><b>${b.id}</b><span class="muted">${b.queue || ""} · ${plural(b.nodes ?? 0, "node")} · started ${clock(b.start)} · ${b.node_hours ?? "—"} node-hours</span>
      ${used > 0 && html`<span class="tag">workers busy ${Math.round(100 * busy / (laneCount * used))}%</span>`}</div>
    ${rows.map(([label, xs]) => html`<div class="tlrow"><div class="tllbl" title=${label}>${label}</div>
      <div class="tllane">
        ${b.end && html`<div class="tlafter" style=${`left:${pos(b.end)}%`}></div>`}
        ${showNow && html`<div class="tlnow" style=${`left:${pos(now)}%`}></div>`}
        ${xs.map(x => html`<div class="tlbar ${x.kind} ${mark(x.t)}" title=${x.tip}
            style=${`left:${pos(x.from)}%;width:${Math.max(0.3, pos(x.to) - pos(x.from))}%`}></div>`)}
      </div></div>`)}
    <div class="tlrow"><div class="tllbl"></div><div class="tlaxis">
      ${[0, 1, 2, 3, 4].map(k => html`<span style=${`left:${25 * k}%`}>${Math.round(span * k / 240)}m</span>`)}
    </div></div>
  </div>`;
}

function Timelines({sy, pick, mark, only}) {
  const blocks = sy.blocks.filter(b => b.start && (!only || b.tasks.some(t => t.mine) || b.refused.some(x => x.mine)));
  if (!blocks.length) return html`<div class="body faint">No blocks yet.</div>`;
  return blocks.map(b => html`<${Timeline} key=${b.id} b=${b} shapes=${sy.buckets || {}} pick=${pick} mark=${mark} />`);
}

function Loose({sy, tasks, mark, on}) {
  const loose = tasks.filter(t => !t.block && t.system === sy.system);
  const groups = [["In flight", "their block is known when they finish", loose.filter(t => t.state === "waiting or running")],
                  ["Never reached a block", "", loose.filter(t => t.state !== "waiting or running")]];
  return groups.filter(([, , ts]) => ts.length).map(([title, note, ts]) => html`<section class="card">
    <h2>${title}${on}<span class="h2note">${note}</span><span class="right">${plural(ts.length, "task")}</span></h2>
    <div class="scrollx"><table class="table"><thead><tr><th>Agent</th><th class="r">Task</th><th>Kind</th><th>Bucket</th><th>Submitted</th><th>State</th></tr></thead>
      <tbody>${ts.map(t => html`<tr class=${mark(t)}><td>${t.agent}</td><td class="r">${t.job_id}</td>
        <td class="muted">${t.kind || ""}</td><td class="muted">${t.bucket || ""}</td>
        <td>${clock(t.submitted)}</td><td>${taskState(sy, t)}</td></tr>`)}</tbody></table></div>
  </section>`);
}

function System({sy, d, view, only, multi}) {
  const pick = t => !only || t.mine;
  const mark = t => d.selected ? (t.mine ? "me" : "other") : "";
  const tasks = (d.tasks || []).filter(pick);
  const here = tasks.filter(t => t.system === sy.system);
  const flying = here.filter(t => t.state === "waiting or running");
  const agents = new Set(flying.map(t => t.agent));
  const on = multi ? ` on ${sy.system}` : "";
  return html`<section class="card">
    <h2>${sy.system}<span class="right mono" title="Endpoint">${sy.endpoint}</span></h2>
    <div class="stats">
      <div class="stat wide"><div class="label">Node-hours used</div>
        <${Meter} used=${sy.node_hours} limit=${sy.max_node_hours} fmt=${v => (+v).toFixed(1)} /></div>
      <${Stat} label="Blocks so far" value=${sy.blocks.length} />
      <${Stat} label="Tasks in flight" value=${flying.length} sub=${`from ${plural(agents.size, "agent")}`} />
      <${Stat} label="Tasks done" value=${here.length - flying.length} />
    </div>
    <${Facility} iri=${sy.iri} />
    <${Buckets} sy=${sy} />
  </section>
  <section class="card">
    <h2>Blocks${on}<span class="h2note">${d.group ? `shared by the ${d.group_runs} runs of ${d.group}, this run's tasks marked` : "shared by all agents of the run"}</span></h2>
    ${view === "timeline" ? html`<${Timelines} sy=${sy} pick=${pick} mark=${mark} only=${only} />`
      : html`<div class="scrollx"><${BlockTable} sy=${sy} pick=${pick} mark=${mark} only=${only} /></div>`}
  </section>
  <${Loose} sy=${sy} tasks=${tasks} mark=${mark} on=${on} />`;
}

export function Resources({campaign, run, agentName}) {
  const [view, setView] = useState(() => recall("resView", "table"));
  const [only, setOnly] = useState(() => !!recall("resOnly", ""));
  const [system, setSystem] = useState("");
  const {data: d, error} = usePoll(() => getJSON("/resources", {c: campaign, run}), 5000, [campaign, run], {keep: true});
  if (!d) return html`<div class="empty">${error ? error.message : "Loading…"}</div>`;
  const systems = d.systems || [];
  const shown = systems.filter(sy => !system || sy.system === system);
  return html`
    <div class="toolbar">
      <div class="seg">${["table", "timeline"].map(v => html`<button class=${view === v ? "on" : ""}
        onClick=${() => { setView(v); remember("resView", v); }}>${v === "table" ? "Table" : "Timeline"}</button>`)}</div>
      ${systems.length > 1 && html`<select class="select" value=${system} onChange=${e => setSystem(e.target.value)}>
        <option value="">All systems</option>${systems.map(sy => html`<option value=${sy.system}>${sy.system}</option>`)}</select>`}
      ${d.selected && html`<label class="check"><input type="checkbox" checked=${only}
        onChange=${e => { setOnly(e.target.checked); remember("resOnly", e.target.checked ? "1" : ""); }} />
        Only ${d.group ? "this run" : agentName || "this agent"}'s tasks</label>`}
      ${d.selected && !only && html`<span class="legend"><i class="me"></i>${agentName || "this agent"} <i class="other"></i>others</span>`}
    </div>
    ${!systems.length && html`<div class="empty">This run sent no work to another system.</div>`}
    <div class="col">${shown.map(sy => html`<${System} key=${sy.system} sy=${sy} d=${d} view=${view} only=${!!d.selected && only} multi=${shown.length > 1} />`)}</div>`;
}
