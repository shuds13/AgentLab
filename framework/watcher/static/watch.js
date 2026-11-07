let tab = "status", offset = 0, logText = "", rawMode = false;
// In a run that started workers, the one chosen in the agents panel: what the status,
// log and inputs show. Blank for the run itself. With it, the name the tabs give the
// agent chosen (blank in a run without workers) and the records it keeps.
let agentSel = "", agentLabel = "", agentRecords = [], lastFiles = [];
// Whether a grouped run's panel lists the group's runs. Open unless closed, and kept.
let groupPanel = (() => { try { return localStorage.getItem("groupPanel") !== "0"; }
                          catch (e) { return true; } })();
// Each worker's run by its branch, from the agents panel, so a branch named elsewhere on
// the page can link to the agent that worked on it.
let agentByBranch = {};
// The file browser. A tab of "tree" is the browser itself; a tab of "f:<root>:<path>"
// is one file opened out of it, which is how it stays distinct from the record tabs,
// whose names are bare. `treeOpen` is which directories are expanded, keyed the same
// way, and the workspace root starts open because it is what there is to look at.
let treeOpen = {"ws:": true}, treeSort = "name", treeDesc = false, treeData = null;
const ROOT_LABEL = {ws: "workspace", camp: "campaign"};
// The run's own files, kept so refresh() knows which route a tab reads from.
let runFiles = [], sideOpen = false;
const SIDE_W = "210px";
// Whether a record can be read as it was written as well as rendered.
const SHOW_RAW = false;
// Which campaign the page is showing. Every request says so, and the address bar
// carries it, so a reload or a bookmark comes back to the same one. The server has
// already checked the name it served this page with, so that is what it opens on.
const HOME = WATCH.campaign;
let campaign = HOME;
// Which run the left-hand tabs show: blank for the newest, which follows a new run
// when it starts, or a past one by name. The address carries it, like the campaign.
let run = new URLSearchParams(location.search).get("run") || "", runList = [];
const url = p => p + (p.includes("?") ? "&" : "?") + "c=" + encodeURIComponent(campaign)
  + ((agentSel || run) ? "&run=" + encodeURIComponent(agentSel || run) : "");
const address = () => "?c=" + encodeURIComponent(campaign)
  + (run ? "&run=" + encodeURIComponent(run) : "");
// Two places to be: the lab, which lists the campaigns it has, and one campaign's own
// page. The address bar says which, so a reload comes back to where you were.
let scope = WATCH.scope;
const view = document.getElementById("view"), pane = document.getElementById("pane");
const newest = document.getElementById("newest");

// Following is a position, not a setting: you are following while you are at the
// bottom, and you stop by scrolling away. Nothing to tick.
const atBottom = () => pane.scrollHeight - pane.scrollTop - pane.clientHeight < 40;
newest.onclick = () => { pane.scrollTop = pane.scrollHeight; };
pane.addEventListener("scroll", () => {
  newest.style.display = (tab === "log" && !atBottom()) ? "block" : "none";
});

function setTabs(files) {
  const t = document.getElementById("tabs");
  lastFiles = files;
  const key = JSON.stringify(files) + tab + rawMode + sideOpen + groupPanel + run + agentLabel
    + JSON.stringify(agentRecords) + JSON.stringify(runList);
  if (t.dataset.key === key) return;
  t.dataset.key = key;
  t.innerHTML = "";
  // A run in a group: the group's runs, in the panel on the left. First, since it is
  // about the group rather than this run.
  if (campaign.includes("/")) {
    const g = document.createElement("button");
    // A chevron, since "runs" is already this run's history: pointing to where the
    // panel would open, or back to close it.
    // A side-panel icon, its left pane filled while the panel is open. Drawn, not a
    // font glyph: a glyph sits wherever its font puts it in the line.
    g.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">`
      + `<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>`
      + `<rect x="1.5" y="2.5" width="4.5" height="11" rx="1.5"`
      + ` class="${groupPanel ? "pane on" : "pane"}"/></svg>`;
    g.classList.add("groupbtn");
    g.title = groupPanel ? "hide group" : "show group";
    g.classList.toggle("on", groupPanel);
    g.onclick = () => {
      groupPanel = !groupPanel;
      try { localStorage.setItem("groupPanel", groupPanel ? "1" : "0"); } catch (e) {}
      setTabs(files); refresh();
    };
    t.appendChild(g);
  }
  // The run the next tabs are about: the newest, or one picked from the history. The
  // history is the campaign's runs; the workers a run started are in its agents tab.
  // A worker whose starter is not among them is listed as a run of its own.
  const tops = runList.filter(r => !r.parent_run || !runList.some(p => p.run === r.parent_run));
  const sel = document.createElement("select");
  // The newest run is "latest", which follows a new one when it starts; the rest by
  // when they started.
  sel.innerHTML = `<option value="">latest</option>` + tops.slice(1).map(r =>
    `<option value="${esc(r.run)}">${esc(when(r.started_at))}`
    + `${r.usd == null ? "" : " · $" + r.usd.toFixed(2)}</option>`).join("");
  sel.value = run;
  if (run && sel.value !== run)      // a worker's own page, reached from its coordinator
    sel.insertAdjacentHTML("beforeend", `<option value="${esc(run)}">${esc(run)}</option>`);
  sel.value = run;
  const picker = document.createElement("label");
  picker.className = "runs" + (run ? " past" : "");
  picker.title = run ? "showing " + run : "showing the latest run";
  picker.innerHTML = "<span>⏲</span>";
  picker.appendChild(sel);
  sel.onchange = () => {
    run = sel.value;
    agentSel = "";
    history.replaceState(null, "", address());
    // A file the run was handed may not exist in the other run.
    openTab(runFiles.includes(tab) ? "status" : tab);
    files();
  };
  t.appendChild(picker);
  const group = text => {
    if (!agentLabel) return;          // a run without workers has one agent: no names
    const g = document.createElement("span");
    g.className = "grp";
    g.textContent = text + " ▸";
    t.appendChild(g);
  };
  const button = n => {
    const b = document.createElement("button");
    // The log is what the agent said and did; the file name is not the point.
    b.textContent = n === "log" ? "agent log" : n.startsWith("r:") ? n.slice(2) : n;
    // Pictures as an icon, to leave room for the records: a frame with a hill and a sun.
    if (n === "pictures") {
      b.innerHTML = `<svg viewBox="0 0 16 16" aria-hidden="true">`
        + `<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>`
        + `<path d="M2 12 L6 7.5 L9 10.5 L11 8.5 L14 11.5"/>`
        + `<circle cx="11" cy="5.5" r="1.2"/></svg>`;
      b.title = "pictures";
    }
    b.className = (n === tab ? "on" : "") + (n === "pictures" ? " iconbtn" : "");
    b.onclick = () => { openTab(n); setTabs(files); };
    t.appendChild(b);
  };
  // The agent's own: its status, its log, and the records it keeps.
  group(agentLabel);
  for (const n of ["status", "log"].concat(agentRecords.map(r => "r:" + r))) button(n);
  // The campaign's records, written by every run of it. Marked off, because they are
  // the same whichever agent is chosen.
  const d = document.createElement("span");
  d.className = "div";
  t.appendChild(d);
  group("campaign");
  for (const n of files) button(n);
  // Everything else the campaign holds. Another view of the pane, so it sits with
  // the tabs that choose one rather than with the panel toggle on the right.
  const w = document.createElement("button");
  w.textContent = "all files";
  w.className = (tab === "tree" || tab.startsWith("f:")) ? "on" : "";
  t.appendChild(w);
  w.onclick = () => { openTab("tree"); setTabs(files); };
  // What the run uses, on the right: the blocks its jobs ran in, and every budget of the
  // run tree in one place.
  const rs = document.createElement("span");
  rs.style.marginLeft = "auto";
  t.appendChild(rs);
  button("resources");
  const bg = document.createElement("button");
  bg.textContent = "budgets";
  bg.className = tab === "budgets" ? "on" : "";
  bg.onclick = () => { openTab("budgets"); setTabs(files); };
  t.appendChild(bg);
  // The files the run was handed open in a panel beside the view, rather than in it: set
  // apart from the tabs. Several of them, each read once and referred back to while the
  // log moves.
  const d2 = document.createElement("span");
  d2.className = "div";
  t.appendChild(d2);
  const f = document.createElement("button");
  f.textContent = "inputs";
  f.className = sideOpen ? "on" : "";
  f.onclick = () => { sideOpen = !sideOpen; showSide(); setTabs(files); };
  t.appendChild(f);
  if (SHOW_RAW && tab.endsWith(".md")) {
    const r = document.createElement("button");
    r.textContent = rawMode ? "rendered" : "raw";
    r.onclick = () => { rawMode = !rawMode; view.dataset.body = ""; setTabs(files); refresh(); };
    t.appendChild(r);
  }
}

// The width goes on the root, so anything fixed to the viewport clears the panel.
function showSide() {
  const el = document.getElementById("side");
  const open = sideOpen && scope === "campaign";
  el.hidden = !open;
  document.documentElement.style.setProperty("--side-w", open ? SIDE_W : "0px");
  if (open) drawSide();
}

function drawSide() {
  const el = document.getElementById("side");
  const key = JSON.stringify(runFiles) + tab;
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.innerHTML = "";
  const h = document.createElement("div");
  h.className = "grp";
  h.textContent = "this run";
  el.appendChild(h);
  if (!runFiles.length) {
    const n = document.createElement("div");
    n.className = "none";
    n.textContent = "nothing yet";
    el.appendChild(n);
    return;
  }
  for (const n of runFiles) {
    const b = document.createElement("button");
    b.textContent = n;
    b.className = n === tab ? "on" : "";
    b.onclick = () => { openTab(n); };
    el.appendChild(b);
  }
}

// Opening a file is the same wherever it was clicked: the pane starts clean, so no
// styling or scroll position follows from what was there before.
function openTab(n) {
  tab = n; offset = 0; logText = "";
  view.innerHTML = ""; view.className = ""; view.dataset.body = "";
  delete view.dataset.statusSig;
  pane.scrollTop = 0; newest.style.display = "none";
  document.getElementById("tabs").dataset.key = "";
  document.getElementById("side").dataset.key = "";
  showSide(); refresh();
}

const short = n => n == null ? "?"
  : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, "") + "m"
  : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);

const hms = s => s == null ? "—" :
  (s >= 3600 ? Math.floor(s/3600) + "h " : "") +
  (s >= 60 ? Math.floor(s%3600/60) + "m " : "") + (s%60) + "s";

// What the run consumed. A dash for the money means the price was not the serving
// model's own, not that the run was free -- the runner drops a figure it cannot stand
// behind rather than printing a catalog price for a model the catalog does not cover.
// With more than one model, each gets a row labelled "cost", the largest consumer at the
// top. Every model but the run's own (named in the row above) is named first in the
// value, without its date suffix.
function costRows(models, main) {
  if (!models || !models.length) return [];
  const rows = models.length > 1
    ? [...models].sort((a, b) => (b.usd || 0) - (a.usd || 0)
                                 || (b.input_tokens || 0) - (a.input_tokens || 0))
    : models;
  const bare = name => name.replace(/-\d{8}$/, "");
  return rows.map(m => [
    "cost",
    (models.length > 1 && bare(m.model) !== bare(main || "") ? `${bare(m.model)} · ` : "")
      + `${short(m.input_tokens)} in / ${short(m.output_tokens)} out · `
      + (m.usd == null ? "—"
         : "$" + m.usd.toFixed(2))]);
}

// A budget is a ceiling, not a target: an agent that has answered its question stops
// early, so the bar shows how much of the allowance is used, not progress towards it.
function bar(done, total, fmt) {
  fmt = fmt || String;
  if (!total) return fmt(done);
  const pct = Math.min(100, Math.round(100 * done / total));
  return `<span class="bar"><i style="width:${pct}%"></i></span>` +
         `${fmt(done)} of ${fmt(total)} (max)`;
}

// Which agents of this run send work to each bucket, for the status page's "shared by".
// Read from the tasks log only, so it is cheap; refreshed every half minute.
let sharing = {}, sharingAt = 0;
let resOnly = false, resSystem = "", resView = "table";
try { resOnly = !!localStorage.getItem("resOnly"); } catch (e) {}
try { resView = localStorage.getItem("resView") || "table"; } catch (e) {}
async function loadSharing() {
  if (Date.now() - sharingAt < 30000) return;
  sharingAt = Date.now();
  try {
    const d = await (await fetch(url("/resources?light=1" + (run ? "&run=" + encodeURIComponent(run) : "")))).json();
    sharing = {};
    for (const sy of d.systems || []) Object.assign(sharing, sy.sharing || {});
  } catch (e) {}
}
const sharedBy = k => (sharing[k] || []).length;

// The budgets tab: every budget of the run tree with how much of it is used. The run's
// own, the resources it draws on, each worker's, and the changes made to them.
const budgetCell = r => {
  const fmt = r.unit === "s" ? hms : r.unit === "h" ? (v => (+v).toFixed(1)) : String;
  return r.limit == null
    ? `${fmt(r.used == null ? 0 : r.used)} <span style="color:#777">(no limit)</span>`
    : bar(r.used || 0, r.limit, fmt);
};
// A change typed into the budgets tab, kept while the view refreshes underneath it.
let budgetNote = "";
const budgetEditing = () => [...document.querySelectorAll("#res input.bchange")]
  .some(i => i.value || i === document.activeElement);
// Each budget the page may change gets a box for the change: +N or -N, or N to set it.
const budgetInput = (d, r) => d.writable
  ? `<td><input class="bchange" size="6" placeholder="${r.unit === "s" ? "+30m" : "+N"}"`
    + ` data-run="${esc(d.run)}" data-name="${esc(r.name)}" data-key="${esc(r.key || "")}">`
    + ` <button class="braise">apply</button></td>` : "";
async function applyBudget(input) {
  const body = {name: input.dataset.name, key: input.dataset.key || null,
                change: input.value.trim()};
  if (!body.change) return;
  let r;
  try {
    r = await (await fetch("/budget?c=" + encodeURIComponent(campaign) + "&run="
      + encodeURIComponent(input.dataset.run), {method: "POST",
      body: JSON.stringify(body)})).json();
  } catch (e) { r = {ok: false, error: String(e)}; }
  budgetNote = r.ok ? "changed: " + r.done.join("; ") + ". The agent is told at its next turn."
                    : "not changed: " + r.error;
  input.value = "";
  input.blur();
  renderBudgets();
}
document.addEventListener("click", e => {
  const b = e.target.closest("#res button.braise");
  if (b) applyBudget(b.parentElement.querySelector("input.bchange"));
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.matches && e.target.matches("#res input.bchange"))
    applyBudget(e.target);
});
async function renderBudgets() {
  const d = await (await fetch(url("/budgets"))).json();
  if (tab !== "budgets") return;
  if (budgetEditing()) return;
  view.className = "";
  if (!d.run) { view.innerHTML = '<div id="res"><div class="note">no run yet</div></div>'; return; }
  const nh = r => r.name === "node_hours" || r.name === "group_node_hours";
  const ofRun = d.rows.filter(r => !nh(r));
  const ofSys = d.rows.filter(nh);
  let h = '<div id="res">';
  h += `<h3>${esc(d.run)}</h3><div class="note">totals for the whole run`
    + (d.part > 1 ? `, over its ${d.part} parts` : "") + `</div>`;
  if (budgetNote) h += `<div class="note">${esc(budgetNote)}</div>`;
  h += '<table class="grid">' + ofRun.map(r =>
    `<tr><td>${esc(r.label)}</td><td>${budgetCell(r)}</td>${budgetInput(d, r)}</tr>`)
    .join("") + "</table>";
  // What the run draws on elsewhere: node-hours per system and the block shapes.
  const shapes = Object.entries(d.buckets || {});
  if (ofSys.length || shapes.length) {
    h += `<h3>resources</h3>`;
    // A group's node-hours are changed with bin/resource_session.sh, not from here.
    h += '<table class="grid">' + ofSys.map(r =>
      `<tr><td>${esc(r.label)}</td><td>${budgetCell(r)}</td>`
      + `${r.name === "node_hours" ? budgetInput(d, r) : ""}</tr>`).join("");
    for (const [k, b] of shapes) {
      const wall = (b.walltime || "").replace(/^0?(\d+):00:00$/, "$1h");
      h += `<tr><td>bucket ${esc(k)}</td><td>` + [b.system, b.queue,
        `${b.num_nodes || 1}-node blocks` + (wall ? ` ${wall}` : ""),
        `max ${b.max_blocks || 1} at a time`].filter(Boolean).map(esc).join(" · ")
        + `</td></tr>`;
    }
    h += "</table>";
    if (shapes.length) h += `<div class="note"><a href="#" data-tab="resources">blocks and tasks in resources</a></div>`;
  }
  if ((d.workers || []).length) {
    h += `<h3>workers</h3><table class="grid"><tr><th>worker</th><th>kind</th>`
      + `<th>status</th><th>jobs</th><th>run time</th></tr>`;
    for (const w of d.workers) {
      const get = n => w.rows.find(r => r.name === n);
      const cls = d.selected ? (w.run === d.selected ? "me" : "other") : "";
      h += `<tr class="${cls}"><td>${esc(w.branch || w.run)}</td><td>${esc(w.kind || "")}</td>`
        + `<td>${esc(w.status || "")}</td>`
        + `<td>${get("submits") ? budgetCell(get("submits")) : ""}</td>`
        + `<td>${get("runtime_s") ? budgetCell(get("runtime_s")) : ""}</td></tr>`;
    }
    h += "</table>";
  }
  if ((d.changes || []).length) {
    h += `<h3>changes</h3><table class="grid">` + d.changes.map(c =>
      `<tr><td>${clock(c.ts)}</td><td>${esc(c.who || "")}</td><td>${esc(c.name)}`
      + (c.key ? ` (${esc(c.key)})` : "") + `</td><td>${c.before == null ? "no limit" : c.before}`
      + ` → ${c.after}</td></tr>`).join("") + "</table>";
  }
  h += `<div class="note" style="margin-top:12px">`
    + (d.writable ? "a change is +N or -N, or N to set it; run time also takes m or h. "
                  : "read-only here: changes need the watcher's own link, on a run of its user. ")
    + `From a shell: <code>bin/budget.sh ${esc(d.workspace)} NAME=+N</code>, e.g. `
    + `submits=+10, workers.explorer=+4, node_hours.polaris=+5, runtime_s=+2h</div>`;
  view.innerHTML = h + "</div>";
}

// The resources tab: per system, its buckets and who shares them, the batch jobs
// (blocks) that were up while this run was, and the tasks each ran.
const clock = t => t ? new Date(t * 1000).toLocaleString([], {weekday: "short",
  hour: "2-digit", minute: "2-digit"}) : "—";
const dur = s => s == null ? "—" : hms(Math.round(s));
// A task in flight, made more exact by your batch jobs in its bucket's queue: queued
// when every one there is queued or held, else still "waiting or running".
function iriState(sy, t) {
  const jobs = sy.iri && sy.iri.your_jobs;
  const q = ((sy.buckets || {})[t.bucket] || {}).queue;
  if (t.state !== "waiting or running" || !jobs || !q) return t.state;
  const here = jobs.filter(j => j.queue === q);
  if (here.length && here.every(j => j.state === "queued" || j.state === "held"))
    return `waiting: block ${here.map(j => j.id).join(", ")} ${here[0].state} in ${q}`;
  if (here.some(j => j.state === "active"))
    return `waiting or running (a block of yours in ${q} is running)`;
  return t.state;
}
// One block as a timeline: a lane per worker, grouped by node, over the block's walltime.
// A task names its worker where the engine gives one; otherwise a node's tasks are packed
// into as few lanes as hold them, which shows how many ran at once but not which worker.
function blockTimeline(b, shapes, pick, cls) {
  // A bucket whose jobs span several nodes has one worker per job, on the job's first
  // node, not a worker on every node; its lanes are workers. Otherwise, a lane per worker
  // on each node.
  const perNode = Math.max(1, ...Object.values(shapes).map(s => s.workers_per_node || 1));
  const bt = b.tasks.find(t => shapes[t.bucket]);
  const shape = bt ? shapes[bt.bucket] : null;
  const perJob = shape ? (shape.nodes_per_job || shape.num_nodes || 1) : 1;
  const workers = perJob > 1
    ? (shape.jobs_per_block || Math.max(1, Math.floor((b.nodes || perJob) / perJob))) : null;
  const span = b.walltime_s || Math.max(1, (b.end || b.last) - b.start);
  const pos = t => Math.max(0, Math.min(100, 100 * (t - b.start) / span));
  const bars = b.tasks.filter(pick).filter(t => t.started && t.ran_s != null)
    .map(t => ({on: t.ran_on || {}, from: t.started, to: t.started + t.ran_s,
                kind: t.state === "done" ? "" : "error", t,
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
    if (x.on.worker != null) {
      (n.named[x.on.worker] = n.named[x.on.worker] || []).push(x);
    } else {
      let lane = n.packed.find(l => l.end <= x.from);
      if (!lane) n.packed.push(lane = {end: 0, bars: []});
      lane.bars.push(x); lane.end = Math.max(lane.end, x.to);
    }
  }
  const busy = bars.filter(x => x.kind !== "postponed").reduce((a, x) => a + x.to - x.from, 0);
  const lanes = workers || (b.nodes || Object.keys(nodes).length) * perNode;
  const used = (b.end || b.last) - b.start;
  let h = `<div class="tlb"><b>${esc(b.id)}</b> · ${esc(b.queue || "")} · `
    + `${b.nodes == null ? "?" : b.nodes} node${b.nodes === 1 ? "" : "s"} · started ${clock(b.start)}`
    + ` · node-hours ${b.node_hours == null ? "—" : b.node_hours}`
    + (used > 0 ? ` · workers busy <b>${Math.round(100 * busy / (lanes * used))}%</b>` : "")
    + `</div><div class="tl">`;
  const after = b.end ? `<div class="after" style="left:${pos(b.end)}%"></div>` : "";
  const now = Date.now() / 1000;
  const mark = now < b.start + span && (!b.end || now - b.end < 5)
    ? `<div class="now" style="left:${pos(now)}%"></div>` : "";
  const lane = (label, xs) => `<div class="row"><div class="lbl">${esc(label)}</div>`
    + `<div class="lane">${after}${mark}` + xs.map(x =>
      `<div class="t ${x.kind} ${cls(x.t)}" title="${esc(x.tip)}" style="left:${pos(x.from)}%;`
      + `width:${Math.max(0, pos(x.to) - pos(x.from))}%"></div>`).join("") + `</div></div>`;
  const hosts = Object.keys(nodes).sort();
  for (const host of hosts) {
    const n = nodes[host];
    const rows = Object.keys(n.named).sort((p, q) => p - q)
      .map(w => [`${host} · ${w}`, n.named[w]])
      .concat(n.packed.map((l, i) => [i ? "" : host, l.bars]));
    if (!workers) for (let i = rows.length; i < perNode; i++) rows.push([rows.length ? "" : host, []]);
    for (const [label, xs] of rows) h += lane(label, xs);
  }
  if (workers) {
    const used = hosts.reduce((a, host) => a + Object.keys(nodes[host].named).length
                                              + nodes[host].packed.length, 0);
    for (let i = used; i < workers; i++) h += lane("worker ran no task", []);
  } else {
    for (let i = hosts.length; i < (b.nodes || 0); i++)
      for (let w = 0; w < perNode; w++) h += lane(w ? "" : "node ran no task", []);
  }
  h += `<div class="row"><div class="lbl"></div><div class="axis">`
    + [0, 1, 2, 3, 4].map(k => `<span style="left:${25 * k}%">${Math.round(span * k / 240)}m</span>`).join("")
    + `</div></div></div>`;
  return h;
}
async function renderResources() {
  const d = await (await fetch(url("/resources"))).json();
  if (tab !== "resources") return;
  // With an agent chosen, the whole run is shown and that agent's rows stand out.
  const only = !!d.selected && resOnly;
  const pick = t => !only || t.mine;
  const rowCls = t => d.selected ? (t.mine ? "me" : "other") : "";
  let h = "";
  const names = (d.systems || []).map(sy => sy.system);
  if (resSystem && !names.includes(resSystem)) resSystem = "";
  // One line of controls for the whole view: which system, and whose tasks.
  const chooser = names.length > 1
    ? `system <select id="ressys"><option value="">all</option>`
      + names.map(n => `<option${n === resSystem ? " selected" : ""}>${esc(n)}</option>`).join("")
      + `</select>` : "";
  const toggle = d.selected
    ? `<label class="resonly"><input type="checkbox" id="resonly"${resOnly ? " checked" : ""}>`
      + ` only this ${d.group ? "run" : "agent"}'s tasks</label>` : "";
  const views = `<span class="resview">` + ["table", "timeline"].map(v =>
    `<a href="#" data-resview="${v}"${v === resView ? ' class="on"' : ""}>${v}</a>`).join("")
    + `</span>`;
  const ctl = `<div class="note">${views}${chooser}${toggle}</div>`;
  if (!(d.systems || []).length)
    h += '<div class="note">This run sent no work to another system.</div>';
  for (const sy of d.systems || []) {
    if (resSystem && sy.system !== resSystem) continue;
    h += `<h3 class="sys">${esc(sy.system)}</h3><div class="note">endpoint ${esc(sy.endpoint)}</div>`;
    // The limits, in grey: the whole pool, each bucket's blocks, what one block runs.
    // Then what is happening now, with its values in bold.
    h += `<div class="note">${sy.max_node_hours != null ? `max node-hours ${sy.max_node_hours} · ` : ""}`
      + (d.group ? `blocks shared by the ${d.group_runs} runs of ${esc(d.group)}, this run's tasks marked`
                 : `pool shared by all agents`) + `</div>`;
    for (const [k, b] of Object.entries(sy.buckets || {})) {
      const n = b.num_nodes || 1, per = b.nodes_per_job || n, w = b.workers_per_node || 1;
      // Runs that predate jobs_per_block in meta counted one job per node.
      const slots = b.jobs_per_block || n / per;
      const jobs = slots <= 1 ? `one ${n}-node job at a time`
        : w > 1 ? `${slots} concurrent jobs` + (per === 1 ? `, ${w} per node` : "")
        : `${slots} concurrent ${per}-node jobs`;
      h += `<div class="note">bucket <b>${esc(k)}</b>: ${n}-node blocks, `
        + `max concurrent blocks ${b.max_blocks || 1}</div>`
        + `<div class="note">per block: ${jobs}, ${esc(b.queue || "?")} queue, `
        + `${esc(b.walltime || "?")} walltime</div>`;
    }
    const here = (d.tasks || []).filter(t => t.system === sy.system);
    const flying = here.filter(t => t.state === "waiting or running");
    const busy = [...new Set(flying.map(t => t.agent))];
    h += `<div class="live">node-hours used <b>${sy.node_hours}</b>`
      + (sy.max_node_hours != null ? ` / ${sy.max_node_hours}` : "")
      + ` (block start to its last task plus idle time)<br>`
      + `blocks so far <b>${sy.blocks.length}</b> · tasks in flight <b>${flying.length}</b>`
      + ` (running or queued) from <b>${busy.length}</b> agent${busy.length === 1 ? "" : "s"}`
      + ` · done <b>${here.length - flying.length}</b></div>`;
    if (resView === "timeline") {
      for (const b of sy.blocks) {
        if (only && !b.tasks.some(t => t.mine) && !b.refused.some(x => x.mine)) continue;
        if (b.start) h += blockTimeline(b, sy.buckets || {}, pick, t => d.selected ? (t.mine ? "me" : "other") : "");
      }
    } else {
    h += '<table class="grid"><tr><th>block</th><th>queue</th><th>nodes</th><th>waited</th>'
      + '<th>started</th><th>walltime</th><th>to last task</th><th>node-hours</th><th></th></tr>';
    for (const b of sy.blocks) {
      if (only && !b.tasks.some(t => t.mine) && !b.refused.some(x => x.mine)) continue;
      h += `<tr class="blk"><td><b>${esc(b.id)}</b></td><td>${esc(b.queue || "—")}</td>`
        + `<td>${b.nodes == null ? "—" : b.nodes}</td>`
        + `<td>${dur(b.queued_at && b.start ? b.start - b.queued_at : null)}</td>`
        + `<td>${clock(b.start)}</td><td>${dur(b.walltime_s)}</td>`
        + `<td>${dur(b.start && b.last ? b.last - b.start : null)}</td>`
        + `<td>${b.node_hours == null ? "—" : b.node_hours}</td><td></td></tr>`;
      const events = b.tasks.filter(pick).map(t => ({at: t.started, t, kind: "ran"}))
        .concat(b.refused.filter(pick).map(x => ({at: x.at, t: x, kind: "refused"})))
        .sort((p, q) => p.at - q.at);
      for (const {t, kind} of events) {
        if (kind === "refused") {
          h += `<tr class="${rowCls(t)}"><td class="sub" colspan="3">${esc(t.agent)} · task ${t.job_id}</td>`
            + `<td></td><td>${clock(t.at)}</td><td></td><td></td>`
            + `<td>postponed: ${esc(t.reason || "")}</td></tr>`;
          continue;
        }
        h += `<tr class="${rowCls(t)}"><td class="sub" colspan="3">${esc(t.agent)} · task ${t.job_id}</td>`
          + `<td>${dur(t.started && t.submitted ? t.started - t.submitted : null)}</td>`
          + `<td>${clock(t.started)}</td><td>${dur(t.ran_s)}</td><td></td>`
          + `<td>${esc(t.state)}${t.retries ? " · " + t.retries + " retr" + (t.retries === 1 ? "y" : "ies") : ""}</td></tr>`;
      }
    }
    h += "</table>";
    }
    // A task names its block when it finishes, so one still running has none yet.
    const loose = (d.tasks || []).filter(t => !t.block && pick(t) && t.system === sy.system);
    for (const [title, ts] of [
        ["in flight (their block is known when they finish)",
         loose.filter(t => t.state === "waiting or running")],
        ["never reached a block", loose.filter(t => t.state !== "waiting or running")]]) {
      if (!ts.length) continue;
      h += `<div class="note" style="margin-top:8px">${title}</div><table class="grid">`
        + `<tr><th>agent</th><th>task</th><th>kind</th><th>submitted</th><th>state</th></tr>`;
      for (const t of ts)
        h += `<tr class="${rowCls(t)}"><td>${esc(t.agent)}</td><td>${t.job_id}</td>`
          + `<td>${esc(t.kind || "")}</td><td>${clock(t.submitted)}</td>`
          + `<td>${esc(iriState(sy, t))}</td></tr>`;
      h += "</table>";
    }
  }
  // The controls are redrawn only when they change, so a menu held open survives the
  // refresh that redraws the blocks beneath it.
  if (view.dataset.body !== "res" || !document.getElementById("resbody")) {
    view.dataset.body = "res"; view.className = "";
    view.innerHTML = '<div id="res"><div id="resctl"></div><div id="resbody"></div></div>';
  }
  for (const [id, html] of [["resctl", ctl], ["resbody", h]]) {
    const el = document.getElementById(id);
    if (el.dataset.html !== html) { el.dataset.html = html; el.innerHTML = html; }
  }
  for (const a of view.querySelectorAll("a[data-resview]")) a.onclick = e => {
    e.preventDefault(); resView = a.dataset.resview;
    try { localStorage.setItem("resView", resView); } catch (err) {}
    renderResources();
  };
  const pickSys = document.getElementById("ressys");
  if (pickSys) pickSys.onchange = () => { resSystem = pickSys.value; renderResources(); };
  const box = document.getElementById("resonly");
  if (box) box.onchange = () => {
    resOnly = box.checked;
    try { localStorage.setItem("resOnly", resOnly ? "1" : ""); } catch (e) {}
    renderResources();
  };
}

function renderStatus(s) {
  if (!s.run) { view.textContent = "no run yet"; return; }
  const shapes = Object.entries(s.buckets || {});
  const placer = s.kind === "resources";
  const rows = [
    ["run", `${s.handle || "—"} · ${s.run}`],
    ["state", s.status === "running"
        ? `running · heartbeat ${hms(s.heartbeat_age_s)} ago`
        : `${s.status} · ${s.stop_reason || ""}`
          + (s.turns ? ` · after ${s.turns} turn${s.turns === 1 ? "" : "s"}` : "")],
    ["doing", s.status === "running" && s.phase
        ? `${s.phase} · ${hms(s.phase_age_s)}` : "—"],
    // Where the work ran, next to the counts of it. A campaign whose task defines both
    // kinds of job splits the counts, since they did not run in the same place.
    // The systems by name; their endpoints are in the resources view.
    [placer ? "places work in" : "jobs run on", (placer ? "" : (s.endpoint
        ? (Object.keys(s.systems || {}).length ? Object.keys(s.systems) : [s.system || "?"])
            .join(" · ") + (s.has_local ? " · this machine" : "")
        : "this machine"))
        // The shape a job is given -- nodes, queue, walltime -- which is worth seeing
        // even when there is only one of them. The name and the default marker are
        // only meaningful where there is a choice.
        + (shapes.length
           ? (placer ? "" : `<br>`) + shapes.map(([k, b]) => {
               // Only while the run is live: a run killed with work outstanding never
               // logs those completions, so the count would stay up for good.
               const out = s.status === "running"
                 ? ((s.inflight_bucket || {})[k] || 0) : 0;
               // Systems named when the buckets span more than one, whether or not this
               // agent sends work itself (a coordinator's buckets are its workers').
               const multi = new Set(shapes.map(([, sb]) => sb.system).filter(Boolean)).size > 1;
               // The pool's shape, whether other agents share it, and this agent's tasks
               // in flight; the rest is in the resources view, which the line opens.
               const mx = b.max_blocks || 1;
               const wall = (b.walltime || "").replace(/^0?(\d+):00:00$/, "$1h");
               const text = (shapes.length > 1 ? `${k}: ` : "") + [[multi && b.system ? b.system : "",
                 b.queue || b.qos || ""].filter(Boolean).join(" "),
                 `${b.num_nodes}-node blocks` + (wall ? ` ${wall}` : "") + ` (max ${mx})`,
                 sharedBy(k) > 1 ? "shared" : ""].filter(Boolean).join(" · ")
                 + (out ? ` | ${out} in flight` : "");
               // Each line opens the resources view, where its blocks and tasks are.
               return `<a href="#" data-tab="resources" title="show in resources" `
                 + `style="color:${out ? "#2e7d32" : "#777"}">` + esc(text) + `</a>`;
             }).join("<br>") : "")],
    // Every budget of the run with how much of it is used: a total for the whole run,
    // which bin/budget.sh can raise.
    ...(s.budgets || []).map(r => {
      const fmt = r.unit === "s" ? hms : r.unit === "h" ? (v => (+v).toFixed(1)) : String;
      return [r.label, r.limit == null
        ? `${fmt(r.used == null ? 0 : r.used)} <span style="color:#777">(no limit)</span>`
        : bar(r.used || 0, r.limit, fmt)];
    }),
    ...(placer ? [
      ["checks", String(s.turns || 0)],
      ["routes set", !(s.routes || []).length ? "none"
        : s.routes.map(r => `${clock(r.ts)} ${esc(r.from)} → ${esc(r.after || "(back)")}`
            + (r.reason ? ` <span style="color:#777">${esc(r.reason)}</span>` : "")).join("<br>")]]
    : [["still running", `${s.jobs_run - s.jobs_done - (s.jobs_abandoned || 0)} of `
        + `${s.jobs_run} submitted, ${s.jobs_done} returned`
        + (s.jobs_abandoned ? `, ${s.jobs_abandoned} abandoned` : "")]]),
    // The scores the run's jobs reported, the campaign's primary one first and marked.
    ...(Object.keys(s.scores || {}).length ? [["scores", Object.entries(s.scores)
        .sort(([a], [b]) => (b === s.primary_score) - (a === s.primary_score))
        .map(([k, v]) => (k === s.primary_score ? `<b>${esc(k)} ${+v.toPrecision(7)}</b>`
                                                : `${esc(k)} ${+v.toPrecision(7)}`))
        .join(" · ")]] : []),
    ["model", `${s.model || "—"}` + (s.effort ? ` · effort ${s.effort}` : "")
        + (s.advisor ? ` · advisor ${s.advisor}` : "")
        + " · context " + (s.context_tokens == null
        ? "no data"
        : s.context_pct == null
        ? `${short(s.context_tokens)} (window not known yet)`
        : `${short(s.context_tokens)}/${short(s.context_window)} (${Math.round(s.context_pct)}%)`)],
    ...costRows(s.cost_models, s.model),
    ...(s.tree_usd == null ? [] : [["cost with workers", "$" + s.tree_usd.toFixed(2)]]),
    ...(s.parent_run ? [
      // The run that started a worker is the one whose agents panel it was chosen in.
      ["started by", (placer ? "the framework, with run " : "") + (s.parent_run_known
        ? `<a href="#" data-agent="">${esc(s.parent_run)}</a>`
        : `${esc(s.parent_run)} <span style="color:#777">(no such run here)</span>`)
        + (s.parents.length ? " · from " + s.parents.map(b => agentByBranch[b]
            ? `<a href="#" data-agent="${esc(agentByBranch[b])}">${esc(b)}</a>` : esc(b))
            .join(" + ") : "")],
      ...(placer ? [] : [["works on", `${esc(s.branch || "—")} (${esc(s.kind || "?")})`]])] : []),
    // Only a run that has a critic names one.
    ...(s.critic && !/^(none|no critic)/.test(s.critic) ? [["critic", esc(s.critic)]] : []),
    // The run's Claude session, to reopen afterwards with `claude -r`. Copying is
    // offered once the run has stopped: opening a session the runner still holds puts
    // a second client on it.
    ["session", !s.session_id ? "—"
        : s.status === "running"
        ? `<span style="color:#666">${s.session_id}</span>`
        : `${s.session_id} <button class="copy" data-copy="${s.session_id}">copy</button>`],
    ["host", s.host || "—"],
    ["started", s.started_at || "—"],
  ];
  if (s.ended_at) rows.push(["ended", s.ended_at]);
  // The campaign outlives the run: these count every run of it, which is why they can
  // be larger than the numbers above.
  rows.push(["campaign totals"],
            ["jobs submitted", String(s.jobs)],
            ["results recorded", String(s.results)]);
  if (s.reviews) rows.push(["critic reviews", String(s.reviews)]);
  // Rewrite only the cells whose value moved. Rebuilding the table every refresh
  // replaces the nodes a selection sits in, and the ticking rows guarantee a rebuild
  // every time -- so a value you are trying to copy cannot be held long enough.
  const sig = rows.map(r => r.length === 1 ? "sec:" + r[0] : r[0]).join("|");
  if (view.dataset.statusSig !== sig) {
    view.dataset.statusSig = sig;
    view.innerHTML = "<table>" + rows.map(
      r => r.length === 1
        ? `<tr class="sec"><td colspan="2">${r[0]}</td></tr>`
        : `<tr><td class="k">${r[0]}</td><td class="v"></td></tr>`).join("") + "</table>";
  }
  const cells = view.querySelectorAll("td.v");
  let i = 0;
  for (const r of rows) {
    if (r.length === 1) continue;
    const td = cells[i++];
    if (td && td.innerHTML !== r[1]) td.innerHTML = r[1];
  }
}

const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// A record that references a figure should show it. Everything else stays as written:
// this is the file, not a rendering of it.
function withFigures(text) {
  return esc(text).replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, src) => {
    if (/^https?:/.test(src)) return m;
    // Into an attribute, so the query separator is escaped along with the rest.
    const u = esc(url("/image?name=" + encodeURIComponent(src)));
    // Sized to sit inside the text rather than replace it; click for the full thing.
    return `<a href="${u}" target="_blank" ` +
        `title="${src} — click to open full size">` +
        `<img src="${u}" alt="${alt}" ` +
        `style="max-width:100%;max-height:75vh;display:block;margin:8px 0;` +
        `border:1px solid #333;background:#fff;cursor:zoom-in"></a>`;
  });
}

// A redraw replaces the nodes a selection is anchored in, so selecting text on a page
// that refreshes every second is impossible. Hold the redraw while the pointer is down
// and while anything is selected; it resumes when the selection is dropped.
let dragging = false;
document.addEventListener("mousedown", e => { if (pane.contains(e.target)) dragging = true; });
document.addEventListener("mouseup", () => { dragging = false; });

function selecting() {
  if (dragging) return true;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
  const n = sel.getRangeAt(0).commonAncestorContainer;
  return pane.contains(n) || document.getElementById("chatlog").contains(n);
}

const sizeof = n => n < 1000 ? n + " B"
                  : n < 1e6 ? (n / 1e3).toFixed(1) + " KB"
                  : (n / 1e6).toFixed(1) + " MB";
const isImage = n => /\.(png|jpe?g|gif|svg|webp)$/i.test(n);
// Every picture the run's jobs returned and its agents drew, newest first, as a grid.
// One clicked opens over the page; the arrow keys go to the next and the one before,
// Escape closes it.
let pics = [], picAt = -1;
async function renderPictures() {
  const mine = campaign;
  let list;
  try { list = await (await fetch(url("/pictures"))).json(); } catch (e) { return; }
  if (mine !== campaign || tab !== "pictures") return;
  const sig = JSON.stringify(list.map(p => p.path));
  if (view.dataset.body === sig) return;
  view.dataset.body = sig; view.className = "";
  pics = list;
  const src = p => esc(url("/image?root=ws&name=" + encodeURIComponent(p.path)));
  const cut = s => s.length > 48 ? s.slice(0, 46) + "…" : s;
  view.innerHTML = `<div class="pics">` + (list.length ? list.map((p, i) =>
    `<figure data-pic="${i}" title="${esc(p.caption)}"><img loading="lazy" src="${src(p)}">`
    + `<figcaption>${esc(cut(p.caption))}</figcaption></figure>`).join("")
    : `<div class="none">no pictures yet</div>`) + `</div>`;
}
function showPic(i) {
  let box = document.getElementById("picbox");
  if (i < 0 || i >= pics.length) { if (box) box.remove(); picAt = -1; return; }
  picAt = i;
  if (!box) {
    box = document.createElement("div");
    box.id = "picbox";
    box.onclick = e => { if (e.target === box) showPic(-1); };
    document.body.appendChild(box);
  }
  const p = pics[i];
  box.innerHTML = `<img src="${esc(url("/image?root=ws&name=" + encodeURIComponent(p.path)))}">`
    + `<div class="cap">${i + 1} of ${pics.length} · ${esc(p.caption)}`
    + `<br><span>← → to move, Esc to close</span></div>`;
}
document.addEventListener("click", e => {
  const f = e.target.closest("[data-pic]");
  if (f) showPic(+f.dataset.pic);
});
document.addEventListener("keydown", e => {
  if (picAt < 0) return;
  if (e.key === "Escape") showPic(-1);
  else if (e.key === "ArrowRight") showPic(Math.min(picAt + 1, pics.length - 1));
  else if (e.key === "ArrowLeft") showPic(Math.max(picAt - 1, 0));
});
const stamp = t => when(new Date(t * 1000).toISOString());
// A key is root and path together, because the same relative path means a different
// file under each root.
const keyOf = (root, rel) => root + ":" + rel;
const splitKey = k => [k.slice(0, k.indexOf(":")), k.slice(k.indexOf(":") + 1)];

// Which way each column runs when you first pick it: names read from A, while for a
// size or a date the big and the recent are what you went looking for.
const SORT_DESC = {name: false, size: true, date: true};

// Directories stay grouped at the top whichever column is sorted, as they do in every
// other file listing. They have no size, so under that column they hold their names.
function rowsOf(d) {
  const sign = treeDesc ? -1 : 1;
  const byName = (a, b) =>
    sign * (a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1);
  const by = treeSort === "date" ? (a, b) => sign * (a.mtime - b.mtime)
           : treeSort === "size" ? (a, b) => sign * (a.size - b.size)
           : byName;
  // Under the size column the directories have nothing to order by, so they keep a
  // plain A-to-Z whichever way the files are running.
  const byNameAsc = (a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  const dirs = d.dirs.map(x => Object.assign({dir: true}, x))
                     .sort(treeSort === "size" ? byNameAsc : by);
  const files = d.files.map(x => Object.assign({dir: false}, x)).sort(by);
  return dirs.concat(files);
}

function treeRow(pad, body, size, mtime) {
  return '<div class="trow" style="padding-left:' + pad + 'px">' + body +
    '<span class="sz">' + size + '</span>' +
    '<span class="dt">' + (mtime ? stamp(mtime) : "") + "</span></div>";
}

// Recursive, but only into what is open: a collapsed directory costs nothing and was
// never asked for.
function renderNode(root, rel, depth, out) {
  const d = treeData.nodes[keyOf(root, rel)];
  if (!d) return;
  if (d.error) {
    out.push('<div class="trow none" style="padding-left:' + (14 + depth * 14) + 'px">' +
             esc(d.error) + "</div>");
    return;
  }
  for (const r of rowsOf(d)) {
    const sub = rel ? rel + "/" + r.name : r.name;
    const k = keyOf(root, sub), pad = 14 + depth * 14;
    if (r.dir) {
      const open = !!treeOpen[k];
      out.push(treeRow(pad, '<button data-t="' + esc(k) + '">' +
        (open ? "\u25be " : "\u25b8 ") + esc(r.name) + "</button>", "", r.mtime));
      if (open) renderNode(root, sub, depth + 1, out);
    } else {
      out.push(treeRow(pad, '<button data-f="' + esc(k) + '">' + esc(r.name) +
        "</button>", sizeof(r.size), r.mtime));
    }
  }
}

// One handler for every control the browser draws, so a row behaves the same wherever
// it is in the tree.
function wireTree() {
  const again = () => { view.dataset.body = ""; refresh(); };
  view.querySelectorAll("[data-t]").forEach(b => b.onclick = () => {
    const k = b.dataset.t;
    if (treeOpen[k]) delete treeOpen[k]; else treeOpen[k] = true;
    again();
  });
  view.querySelectorAll("[data-s]").forEach(b => b.onclick = () => {
    const v = b.dataset.s;
    treeDesc = treeSort === v ? !treeDesc : SORT_DESC[v];
    treeSort = v; again();
  });
  view.querySelectorAll("[data-f]").forEach(b => b.onclick = () => {
    openTab("f:" + b.dataset.f);
  });
  view.querySelectorAll("[data-back]").forEach(b => b.onclick = () => openTab("tree"));
}

// Column headings, clicked to sort. A second click on the one already sorted turns
// it round, and the arrow says which way it is running -- so the order is readable
// without comparing two rows to work it out.
function treeHead() {
  const col = (v, label) => '<button data-s="' + v + '">' + label +
    (treeSort === v ? (treeDesc ? " \u25be" : " \u25b4") : "") + "</button>";
  return '<div class="trow thead">' + col("name", "name") +
    '<span class="sz">' + col("size", "size") + "</span>" +
    '<span class="dt">' + col("date", "modified") + "</span></div>";
}

async function renderTree() {
  const mine = campaign;
  // The roots are always asked for, so a tree collapsed to nothing still knows what it
  // would expand into -- and whether this campaign has a definition directory at all.
  const keys = Array.from(new Set(Object.keys(treeOpen).concat(["ws:", "camp:"])));
  const d = await (await fetch(url(
    "/tree?open=" + encodeURIComponent(keys.join(","))))).json();
  if (mine !== campaign || scope !== "campaign" || tab !== "tree") return;
  treeData = d;
  const out = [treeHead()];
  for (const root of d.roots) {
    const k = keyOf(root, ""), open = !!treeOpen[k];
    out.push('<div class="trow root"><button data-t="' + esc(k) + '">' +
      (open ? "\u25be " : "\u25b8 ") + ROOT_LABEL[root] + "</button></div>");
    if (open) renderNode(root, "", 1, out);
  }
  const html = out.join("");
  if (html === view.dataset.body) return;
  const at = pane.scrollTop;
  view.dataset.body = html;
  view.className = "tree";
  view.innerHTML = html;
  wireTree();
  pane.scrollTop = at;
}

// A coordinated run's agents: the run itself, then each worker under the worker whose
// branch it built on (or under the run, when it started from anything else). Hidden for
// a run that started none.
//
// A run in a group (several workspaces under one directory) lists the group's runs
// above it, each a click away; its own agents, if it has any, sit under its row.
function renderAgents(top, group, session) {
  const panel = document.getElementById("agents");
  const ws = top.workers || [];
  const grouped = groupPanel && (group || []).length > 1;
  if (!ws.length && !grouped) {
    if (!panel.hidden) { panel.hidden = true; panel.innerHTML = ""; }
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("grouped", grouped);
  const usd = models => (models || []).reduce((t, m) => t + (m.usd || 0), 0);
  // A run with SHOW_COST off shows no cost here: no column, no total.
  const costs = top.show_cost !== false;
  const money = v => !costs ? ""
    : `<span class="usd">${v == null ? "—" : "$" + v.toFixed(2)}</span>`;
  // Each row names how that agent works: its method (director, explorer, ...), or for a
  // run from before methods were recorded, a worker's kind.
  // The top run coordinates only if it started workers that do the work; beside a
  // resource agent alone it is the one researcher.
  // Agents the framework started (a resource agent) sit beside the top run, not under
  // it: its agent did not start them.
  const fw = ws.filter(w => w.started_by === "framework" || w.kind === "resources");
  const own = ws.filter(w => !fw.includes(w));
  const lead = own.length ? "coordinator" : "researcher";
  const me = {run: "", branch: top.kind ? top.branch : lead, method: top.method,
              kind: top.kind, status: top.status, usd: usd(top.cost_models), score: null};
  const byBranch = {};
  for (const w of ws) byBranch[w.branch] = w;
  agentByBranch = Object.fromEntries(ws.map(w => [w.branch, w.run]));
  setRecipients(ws.find(w => w.run === agentSel));
  const dot = st => st === "running" ? '<i class="on">●</i>'
    : st === "not responding" ? '<i class="bad">●</i>' : "<i>○</i>";
  const row = (a, depth) =>
    `<div class="arow${a.run === agentSel ? " sel" : ""}" data-agent="${esc(a.run)}"`
    + ` style="padding-left:${0.8 + 1.6 * depth}em">${depth ? "└ " : ""}${dot(a.status)}`
    + ` <b>${esc(a.branch || a.run)}</b> <span class="k">${esc(a.method || a.kind || "")}</span>`
    + `<span class="sc">${a.score == null ? "" : +a.score.toPrecision(7)}</span>`
    + money(a.usd) + `</div>`;
  const below = (branch, depth) => own
    .filter(w => (byBranch[w.parent] ? w.parent : null) === branch)
    .map(w => row(w, depth) + below(w.branch, depth + 1)).join("");
  const tree = (depth) => fw.map(w => row({...w, method: (w.method || w.kind || "") + " · framework"}, depth)).join("")
    + row(me, depth) + below(null, depth + 1)
    + `<div class="atotal">` + [own.length ? `${own.length} worker${own.length === 1 ? "" : "s"}` : "",
        fw.length ? `${fw.length} from the framework` : ""].filter(Boolean).join(" + ")
    + (!costs ? "" : ` · total `
       + (top.tree_usd == null ? "—" : "$" + top.tree_usd.toFixed(2))) + "</div>";
  let html;
  if (grouped) {
    const name = c => c.slice(c.indexOf("/") + 1);
    const gdot = g => g.live ? '<i class="on">●</i>'
      : g.status === "running" ? '<i class="bad">●</i>' : "<i>○</i>";
    const total = group.reduce((t, g) => t + (g.usd || 0), 0);
    const up = group.filter(g => g.live).length;
    html = `<div class="agrp">${esc(campaign.slice(0, campaign.indexOf("/")))} · `
      + `${group.length} runs · ${up} running`
      + (session ? Object.entries(session.node_hours).map(([sy, cap]) =>
          ` · ${esc(sy)} ${(session.used[sy] || 0).toFixed(1)} / ${cap} node-hours`
          + (session.stopped ? " (session stopped)" : "")).join("") : "")
      + `</div>`
      + group.map(g => {
        const here = g.campaign === campaign;
        return `<div class="arow grun${here && !agentSel ? " sel" : ""}" data-camp="${esc(g.campaign)}">`
          + `${gdot(g)} <b>${esc(name(g.campaign))}</b> <span class="k">${esc(g.model || "")}</span>`
          + `<span class="sc">${g.score == null ? "" : +g.score.toPrecision(7)}</span>`
          + money(g.usd) + `</div>`
          + (here && ws.length ? tree(1) : "");
      }).join("")
      + (costs ? `<div class="atotal">total $${total.toFixed(2)}</div>` : "");
  } else {
    html = tree(0);
  }
  if (panel.innerHTML !== html) panel.innerHTML = html;
}

async function refresh() {
  if (scope === "lab") {
    // The agents panel belongs to a campaign's run, not to the lab.
    const panel = document.getElementById("agents");
    if (!panel.hidden) { panel.hidden = true; panel.innerHTML = ""; }
    return labRefresh();
  }
  // The campaign this pass is about. A reply that arrives after the page has moved on
  // belongs to the campaign that asked for it, not to where you are now, so it is
  // dropped rather than drawn.
  const mine = campaign;
  const here = () => mine === campaign && scope === "campaign";
  try {
    const top = await (await fetch("/status?c=" + encodeURIComponent(campaign)
      + (run ? "&run=" + encodeURIComponent(run) : ""))).json();
    if (!here()) return;
    // A worker chosen in another run, or one this run no longer lists, is let go.
    if (agentSel && !(top.workers || []).some(w => w.run === agentSel)) {
      agentSel = ""; openTab(tab); return;
    }
    const g = campaign.includes("/") && groupPanel
      ? await (await fetch("/group?c=" + encodeURIComponent(campaign))).json() : null;
    if (!here()) return;
    renderAgents(top, g && g.runs, g && g.session);
    const s = agentSel ? await (await fetch(url("/status"))).json() : top;
    if (!here()) return;
    const ended = !!s.run && s.status !== "running";
    if (ended !== runEnded) { runEnded = ended; setChatBar(); }
    const lead = (top.workers || []).some(w => w.kind !== "resources") ? "coordinator" : "researcher";
    const label = (top.workers || []).length ? (agentSel ? s.branch || agentSel : lead) : "";
    const recs = s.records || [];
    if (label !== agentLabel || JSON.stringify(recs) !== JSON.stringify(agentRecords)) {
      agentLabel = label; agentRecords = recs; setTabs(lastFiles);
    }
    document.getElementById("head").textContent = top.run
      ? (top.status === "running" ? "running · " : top.status + " · ")
        + (top.handle || top.run) + (agentSel ? " · " + (s.branch || agentSel) : "")
      : "no run yet";
    if (tab === "status") {
      view.className = ""; renderStatus(s); loadSharing(); return;
    }
    if (tab === "resources") { await renderResources(); return; }
    if (tab === "budgets") { await renderBudgets(); return; }
    if (tab === "tree") { await renderTree(); return; }
    if (tab === "pictures") { await renderPictures(); return; }
    if (tab === "log") {
      view.className = "";
      const stick = atBottom();
      const r = await fetch(url(`/log?from=${offset}`));
      const j = await r.json();
      if (!here()) return;
      if (j.reset) { logText = ""; offset = 0; }      // a new run: start the pane again
      if (j.text) { logText += j.text; view.textContent = logText; }
      offset = j.offset;
      if (stick) { pane.scrollTop = pane.scrollHeight; newest.style.display = "none"; }
      else if (j.text) newest.style.display = "block";
    } else {
      if (selecting()) return;
      // A file opened out of the browser is read by root and path rather than by
      // name, and carries the way back to the tree it was clicked in.
      const ws = tab.startsWith("f:");
      const [root, name] = ws ? splitKey(tab.slice(2))
        : ["ws", tab.startsWith("r:") ? tab.slice(2) : tab];
      const head = ws ? '<div class="crumbs">' +
        '<button class="crumb" data-back="1">\u2190 all files</button> / ' +
        '<span class="lbl">' + ROOT_LABEL[root] + "</span> / <b>" + esc(name) +
        "</b></div>" : "";
      // An image is the file, not a rendering of it, so it is shown rather than read.
      if (ws && isImage(name)) {
        const u = esc(url("/image?root=" + root + "&name=" + encodeURIComponent(name)));
        const shown = head + '<a href="' + u + '" target="_blank"><img src="' + u +
          '" style="max-width:100%;max-height:80vh;display:block;margin:10px 14px;' +
          'border:1px solid #333;background:#fff;cursor:zoom-in"></a>';
        if (shown !== view.dataset.body) {
          view.dataset.body = shown; view.className = "";
          view.innerHTML = shown; wireTree();
        }
        return;
      }
      // The board is a list of lines, not a document: rendered as Markdown, consecutive
      // messages run together into one paragraph.
      const md = name.endsWith(".md") && !rawMode && name !== "ANNOUNCEMENTS.md";
      const route = ws ? "/wsfile?root=" + root + "&path=" : (
        (runFiles.includes(tab) ? "/runfile" : tab.startsWith("r:") ? "/agentfile" : "/file")
        + "?name=");
      const body = await (await fetch(url(
        route + encodeURIComponent(name) + (md ? "" : "&raw=1")))).text();
      if (!here()) return;
      if (body !== view.dataset.body) {                // keep where you were reading
        const at = pane.scrollTop;
        view.dataset.body = body;
        view.className = md ? "doc" : "";
        view.innerHTML = head + (md ? body : withFigures(body));
        if (ws) wireTree();
        pane.scrollTop = at;
      }
    }
  } catch (e) {
    document.getElementById("head").textContent = "watcher stopped";
  }
}

// The transcript: the runner's notices, the agent's replies and your own lines, in the
// order they were said. Written as `HH:MM` **who** text, one message per paragraph.
const chatlog = document.getElementById("chatlog");
let chatText = "";

async function chat() {
  if (selecting()) return;
  // The lab's chat has one reader, the secretary, so there is no one to pick.
  document.getElementById("to").hidden = scope === "lab";
  if (scope === "lab") return labChat();
  const mine = campaign;
  let body;
  try { body = await (await fetch(url("/messages"))).text(); }
  catch (e) { return; }
  if (mine !== campaign || scope !== "campaign") return;
  if (body === chatText) return;
  chatText = body;
  renderChat(body);
}

document.addEventListener("click", e => {
  const tb = e.target.closest("a[data-tab]");
  if (tb) { e.preventDefault(); openTab(tb.dataset.tab); return; }
  const gc = e.target.closest("[data-camp]");
  if (gc) {
    // The same tab in the run switched to, unless it is a file this run was handed.
    if (gc.dataset.camp !== campaign || agentSel)
      enterCampaign(gc.dataset.camp, runFiles.includes(tab) ? "" : tab);
    return;
  }
  const g = e.target.closest("[data-agent]");
  if (g) {
    agentSel = g.dataset.agent;
    // Status, log and what the run was handed are the agent's own, so they start again
    // for the one chosen (a file it was handed may not exist for it). The campaign's
    // records are the same whichever agent is chosen, so they stay as they are.
    if (tab === "status" || tab === "log" || tab.startsWith("r:") || runFiles.includes(tab))
      openTab(runFiles.includes(tab) ? "status" : tab);
    else
      refresh();
    files();
    return;
  }
  const a = e.target.closest("a[data-run]");
  if (a) {
    e.preventDefault();
    run = a.dataset.run;
    history.replaceState(null, "", address());
    openTab("status");
    files();
    return;
  }
  const b = e.target.closest("button.copy");
  if (!b) return;
  navigator.clipboard.writeText(b.dataset.copy).then(() => {
    b.textContent = "copied";
    setTimeout(() => { b.textContent = "copy"; }, 1500);
  }, () => { b.textContent = "no clipboard"; });
});

async function files() {
  if (scope === "lab") return;
  const mine = campaign;
  try {
    const [list, runs, past] = await Promise.all([
      fetch(url("/files")).then(r => r.json()),
      fetch(url("/runfiles")).then(r => r.json()),
      fetch(url("/runs")).then(r => r.json()),
    ]);
    if (mine === campaign && scope === "campaign") {
      runFiles = runs; runList = past; setTabs(list); showSide();
    }
  } catch (e) {}
}
// The lab conversation: questions asked here and what the secretary answered. Every
// reply is appended by framework/notify.sh whether or not Slack is configured, so this is
// the whole exchange in a lab with no Slack at all.
async function labChat() {
  let d;
  try { d = await (await fetch("/labchat")).json(); } catch (e) { return; }
  if (scope !== "lab") return;
  labState = {secretary: !!d.secretary, slack: !!d.slack};
  setChatBar();
  if (d.text === chatText) return;
  chatText = d.text;
  renderChat(d.text);
}

// Both conversations are the same file format, so they are drawn by the same code.
function renderChat(body) {
  const msgs = body.split(/\n\s*\n/).map(m => m.trim()).filter(Boolean);
  if (!msgs.length) { chatlog.className = "none";
                      chatlog.textContent = "no messages yet"; return; }
  const near = chatlog.scrollTop + chatlog.clientHeight > chatlog.scrollHeight - 40;
  chatlog.className = "";
  chatlog.innerHTML = msgs.map(m => {
    const p = m.match(/^`(\d\d:\d\d)`\s+\*\*([\w-]+)\*\*\s+([\s\S]*)$/);
    if (!p) return `<div class="m">${esc(m)}</div>`;
    const you = p[2] === "you" ? " you" : "";
    return `<div class="m"><span class="t">${p[1]}</span>` +
           `<span class="who${you}">${p[2]}</span>${esc(p[3])}</div>`;
  }).join("");
  if (near) chatlog.scrollTop = chatlog.scrollHeight;
}

// Who a message can go to: everyone (the board every agent reads), or the worker chosen
// in the agents panel alone (its run's inbox). Rebuilt only when that changes.
function setRecipients(chosen) {
  const sel = document.getElementById("to");
  const want = [["", "everyone"], ...(chosen ? [[chosen.run, "only " + (chosen.branch || chosen.run)]] : [])];
  const have = [...sel.options].map(o => [o.value, o.textContent]);
  if (JSON.stringify(want) === JSON.stringify(have)) return;
  const keep = sel.value;
  sel.innerHTML = want.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  sel.value = want.some(([v]) => v === keep) ? keep : "";
}
// A message goes to the board the agent reads between turns, so it lands after the
// turn in flight rather than interrupting it. On the lab page it goes to the
// secretary's inbox instead -- one reader, one answer, however many campaigns are up.
async function say() {
  const box = document.getElementById("msg"), text = box.value.trim();
  if (!text) return;
  box.value = "";
  const one = scope === "lab" ? "" : document.getElementById("to").value;
  // Everyone means the board of the run being watched, whichever worker is chosen in the
  // agents panel; only an ended top run takes a message into its saved session instead.
  const to = scope === "lab" ? "/say?lab"
    : one ? url("/say") + "&to=" + encodeURIComponent(one)
    : "/say?c=" + encodeURIComponent(campaign) + (run ? "&run=" + encodeURIComponent(run) : "");
  try {
    const r = await (await fetch(to, {method: "POST", body: text})).json();
    if (r.error) { box.value = text; alert(r.error); return; }
  } catch (e) {}
  // Show the conversation the message just joined, rather than leaving you on whatever
  // you were reading with no sign it went anywhere.
  chat();
}
// Drag the CHAT bar to resize. #pane is flex:1, so whatever the chat gives up it takes.
// The height is kept in localStorage, so it survives a reload and the next viewer.
(function () {
  const bar = document.getElementById("chathead"), chat = document.getElementById("chat");
  const KEY = "watch.chatHeight";
  const clamp = h => Math.max(60, Math.min(window.innerHeight - 120, h));
  const setH = h => document.documentElement.style.setProperty("--chat-h", h + "px");
  try {
    const saved = parseInt(localStorage.getItem(KEY), 10);
    if (saved > 0) setH(clamp(saved));
  } catch (e) {}
  let from = 0, start = 0;
  const move = e => { setH(clamp(start + (from - e.clientY))); };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    document.body.style.userSelect = "";
    try { localStorage.setItem(KEY, chat.offsetHeight); } catch (e) {}
  };
  bar.addEventListener("mousedown", e => {
    if (e.target.id === "chattoggle" || document.documentElement.classList.contains("chatoff")) return;
    from = e.clientY; start = chat.offsetHeight;
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    e.preventDefault();
  });
})();

// Hide the chat down to its bar, and open it again from the bar; kept like its height.
(function () {
  const root = document.documentElement, btn = document.getElementById("chattoggle");
  const set = off => {
    root.classList.toggle("chatoff", off);
    btn.textContent = off ? "show chat" : "hide";
    try { localStorage.setItem("watch.chatOff", off ? "1" : ""); } catch (e) {}
  };
  try { set(!!localStorage.getItem("watch.chatOff")); } catch (e) {}
  btn.addEventListener("click", e => { e.stopPropagation(); set(!root.classList.contains("chatoff")); });
  document.getElementById("chathead").addEventListener("click", () => {
    if (root.classList.contains("chatoff")) set(false);
  });
})();

// Drag the divider beside the agents panel to resize it, kept like the chat height.
(function () {
  const bar = document.getElementById("agentsdrag"), panel = document.getElementById("agents");
  const KEY = "watch.agentsWidth";
  const clamp = w => Math.max(120, Math.min(window.innerWidth * 0.7, w));
  const setW = w => document.documentElement.style.setProperty("--agents-w", w + "px");
  try {
    const saved = parseInt(localStorage.getItem(KEY), 10);
    if (saved > 0) setW(clamp(saved));
  } catch (e) {}
  let from = 0, start = 0;
  const move = e => { setW(clamp(start + (e.clientX - from))); };
  const up = () => {
    document.removeEventListener("mousemove", move);
    document.removeEventListener("mouseup", up);
    document.body.style.userSelect = "";
    try { localStorage.setItem(KEY, panel.offsetWidth); } catch (e) {}
  };
  bar.addEventListener("mousedown", e => {
    from = e.clientX; start = panel.offsetWidth;
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    e.preventDefault();
  });
})();

// One watcher serves the whole lab, so moving between the lab and a campaign, or
// between campaigns, is a change of what the page asks for rather than another
// process on another port.
const camp = document.getElementById("camp"), labPane = document.getElementById("lab");
const usersPane = document.getElementById("labusers");
const svcPane = document.getElementById("labsvc");

// Paint the frame for where we are. The lab has no file tabs and no chat: there is no
// one agent to read them for. The chat bar names the campaign it would write to, so a
// line meant for one agent cannot reach another unnoticed.
let labTab = "campaigns";
function showLabTab() {
  const camps = labTab === "campaigns";
  labPane.style.display = camps ? "block" : "none";
  usersPane.style.display = camps ? "none" : "block";
  document.getElementById("tabcamps").className = camps ? "on" : "";
  document.getElementById("tabpeople").className = camps ? "" : "on";
}
document.getElementById("tabcamps").onclick = () => { labTab = "campaigns"; showLabTab(); };
document.getElementById("tabpeople").onclick = () => { labTab = "people"; showLabTab(); };

function showScope() {
  const lab = scope === "lab";
  svcPane.style.display = lab ? "block" : "none";
  if (lab) showLabTab(); else { labPane.style.display = "none"; usersPane.style.display = "none"; }
  view.style.display = lab ? "none" : "";
  document.getElementById("tabs").style.display = lab ? "none" : "";
  showSide();
  // The chat is shown in both places, but it writes to different readers: a campaign's
  // board, or the secretary's inbox. The bar says which, so a line meant for one cannot
  // reach the other unnoticed.
  document.getElementById("chat").style.display = "";
  camp.style.display = lab ? "none" : "";
  document.getElementById("sep").style.display = lab ? "none" : "";
  document.getElementById("home").className = lab ? "here" : "home";
  if (lab) newest.style.display = "none";
  document.title = lab ? "AgentLab" : campaign;
  setChatBar();
}

// The chat bar says who reads what you type and, on the lab page, who else sees it.
// One writer: labChat() supplies what only it knows -- whether a secretary is reading
// and whether Slack is attached -- and the rest follows from the scope, so the two
// cannot overwrite each other's title as they poll.
let labState = {secretary: false, slack: false};
// Whether the run on show has ended, when a message reopens its session to answer.
let runEnded = false;
function setChatBar() {
  const head = document.getElementById("chattitle"), box = document.getElementById("msg");
  if (scope !== "lab") {
    head.textContent = "CHAT · " + campaign + (runEnded ? " · run ended" : "");
    box.placeholder = runEnded
      ? "ask the agent about its run — it answers from its session"
      : "message the agent — it reads between turns";
    return;
  }
  head.textContent = "SECRETARY"
    + (labState.secretary ? "" : " · not running")
    + (labState.slack ? " · shared with Slack" : "");
  box.placeholder = !labState.secretary
    ? "secretary not running — this is queued until it starts"
    : labState.slack
      ? "ask the secretary — the reply is posted to Slack too"
      : "ask the secretary — it answers from the recorded results";
}

// Nothing carries over between places: the log, the file being read and the
// conversation all belong to the campaign that was showing.
function resetPane() {
  tab = "status"; offset = 0; logText = ""; chatText = ""; runFiles = []; treeOpen = {"ws:": true}; treeData = null;
  treeSort = "name"; treeDesc = false;
  view.innerHTML = ""; view.className = ""; view.dataset.body = "";
  delete view.dataset.statusSig;
  const t = document.getElementById("tabs");
  t.dataset.key = ""; t.innerHTML = "";    // else the last campaign's tabs flash first
  const sd = document.getElementById("side");
  sd.dataset.key = ""; sd.innerHTML = "";
  labPane.dataset.key = "";
  usersPane.dataset.key = "";
  pane.scrollTop = 0; newest.style.display = "none";
  chatlog.className = "none"; chatlog.textContent = "no messages yet";
  document.getElementById("head").textContent = "connecting…";
}

// `keep` is a tab to stay on, for a move between runs of one group, which share their
// campaign's tabs.
function enterCampaign(name, keep) {
  campaign = name; scope = "campaign"; camp.value = name; run = ""; runList = [];
  agentSel = "";
  history.replaceState(null, "", address());
  resetPane(); showScope();
  if (keep) tab = keep;
  files(); refresh(); chat();
}

function goLab() {
  scope = "lab"; agentSel = "";
  history.replaceState(null, "", "?lab");
  resetPane(); showScope();
  refresh();
}

// A date to glance at, not to read: the year is today's and the seconds do not matter.
const when = t => {
  const m = String(t || "").match(/^(\d{4})-(\d\d)-(\d\d)T(\d\d:\d\d)/);
  return m ? `${m[2]}-${m[3]} ${m[4]}` : "—";
};

// The campaign table's columns, in the order they are drawn, and which way each one
// runs when first picked: a name or a state reads from A, while for a duration or a
// date the long and the recent are what you went looking for.
const LAB_COLS = [["campaign", "campaign"], ["state", "state"], ["doing", "doing"],
                  ["run", "run"], ["took", "took"], ["last", "last active"]];
const LAB_DESC = {campaign: false, state: false, doing: false, run: false,
                  took: true, last: true};
// By name, the order the lab has always opened on. A heading changes it, and changes
// it for both sections at once: they are two parts of one list, not two lists.
let labSort = "campaign", labDesc = false;
// Kept so a click on a heading reorders what is already on screen rather than waiting
// on the next poll to say the same thing again.
let labRows = [];

// Orders one section's rows. Which campaigns are running is not the sort's business --
// they are listed apart, above -- so this is only ever asked to order like with like.
function labOrder(rows) {
  const sign = labDesc ? -1 : 1;
  const state = r => !r.run ? "never run"
                   : r.live ? "running" : (r.status || "stopped");
  // A run still going was last active now, which is what its cell says. A run that was
  // killed never wrote down that it ended, so it is last active when it started. One
  // that has never run has no answer at all.
  const last = r => r.live ? Date.now()
                  : (Date.parse(r.ended_at || r.started_at || "") || 0);
  const str = f => (a, b) => {
    const x = String(f(a) || "").toLowerCase(), y = String(f(b) || "").toLowerCase();
    return x === y ? 0 : sign * (x < y ? -1 : 1);
  };
  const num = f => (a, b) => sign * ((f(a) || 0) - (f(b) || 0));
  const by = labSort === "state" ? str(state)
           : labSort === "doing" ? str(r => r.phase)
           : labSort === "run" ? str(r => r.handle)
           : labSort === "took" ? num(r => r.elapsed_s)
           : labSort === "last" ? num(last)
           : str(r => r.campaign);
  // A row showing "--" under the column being sorted has nothing to order it by, and an
  // empty value would otherwise win it a place at one end: sorted by what it is doing,
  // every campaign that is doing nothing would come before the one that is. So the
  // blanks sink to a block at the bottom whichever way the column runs, and the rows
  // with something to say hold the order between them. Name and state are never blank.
  const has = labSort === "doing" ? (r => !!(r.live && r.phase))
            : labSort === "took" ? (r => r.elapsed_s != null)
            : labSort === "run" ? (r => !!r.handle)
            : labSort === "last" ? (r => !!(r.live || r.ended_at || r.started_at))
            : (r => true);
  const blank = r => has(r) ? 0 : 1;
  // Ties keep a plain A-to-Z, so a column many rows share still reads in a settled
  // order rather than shuffling from one poll to the next -- and the block at the
  // bottom reads that way whichever direction the column is running.
  const byName = (a, b) => a.campaign.toLowerCase() < b.campaign.toLowerCase() ? -1 : 1;
  return rows.slice().sort((a, b) =>
    (blank(a) - blank(b)) || by(a, b) || byName(a, b));
}

// The groups opened on the lab page, by name.
const labOpen = new Set();

// Workspaces under one directory -- the runs of a campaign that gives each its own, such
// as a comparison or a set of variants -- are one row, `group`, standing for them all;
// opened, its runs are listed beneath it. The group's row is running if any of them is,
// and was last active when the latest of them was.
function labGroups(rows) {
  const top = [], groups = {};
  for (const r of rows) {
    const i = r.campaign.indexOf("/");
    if (i < 0) { top.push(r); continue; }
    const g = r.campaign.slice(0, i);
    if (!groups[g]) {
      groups[g] = {campaign: g, group: true, members: []};
      top.push(groups[g]);
    }
    groups[g].members.push(r);
  }
  for (const g of Object.values(groups)) {
    const m = g.members, ran = m.filter(r => r.run);
    g.live = m.some(r => r.live);
    g.run = ran.length ? ran[0].run : null;
    g.started_at = ran.map(r => r.started_at || "").sort().pop() || null;
    g.ended_at = m.some(r => r.live) ? null
               : ran.map(r => r.ended_at || "").sort().pop() || null;
    g.elapsed_s = ran.length ? Math.max(...ran.map(r => r.elapsed_s || 0)) : null;
  }
  return top;
}

function renderLab(rows) {
  if (!rows.length) {
    labPane.innerHTML = `<div class="none">no campaigns have run yet</div>`;
    labPane.dataset.key = "";
    return;
  }
  labRows = rows;
  // What is running is listed first whatever the sort, so it cannot be ordered or
  // scrolled out of sight: a run nobody knows is going is the one thing this page
  // exists to prevent. A lab with nothing running is just the list, with nothing to
  // mark off. An opened group's runs follow it, in the same order.
  const top = labGroups(rows);
  const unfold = list => list.flatMap(r => r.group && labOpen.has(r.campaign)
    ? [r].concat(labOrder(r.members).map(m => Object.assign({sub: true}, m))) : [r]);
  const live = unfold(labOrder(top.filter(r => r.live)));
  const rest = unfold(labOrder(top.filter(r => !r.live)));
  rows = live.concat(rest);
  // Same reason the status table is built once and its cells rewritten: a table
  // rebuilt every second cannot hold a selection, and these rows tick. The sort and
  // which campaigns are running are both part of the key, so picking a column that
  // happens to leave the rows where they are still moves the arrow, and a run starting
  // still lifts it above the rule.
  const key = labSort + labDesc + "|" +
    rows.map(r => r.campaign + (r.live ? "*" : "") + (r.group && labOpen.has(r.campaign)
                                                      ? "+" : "")).join("|");
  if (labPane.dataset.key !== key) {
    labPane.dataset.key = key;
    // The last of the running campaigns carries the line that marks them off. They are
    // already the ones with the lit dot, so a heading saying so would only repeat the
    // rows above it.
    const cut = live.length && rest.length ? live.length - 1 : -1;
    const row = (r, i) => `<tr class="row${i === cut ? " edge" : ""}` +
      `${r.sub ? " sub" : ""}" data-c="${esc(r.campaign)}"` +
      `${r.group ? ' data-g="1"' : ""}>` +
      `<td class="name"></td><td class="state"></td><td class="doing"></td>` +
      `<td class="run"></td><td class="took"></td><td class="last"></td></tr>`;
    labPane.innerHTML = "<table><tr>" +
      LAB_COLS.map(([v, label]) => `<th data-s="${v}">${label}` +
        (labSort === v ? (labDesc ? " ▾" : " ▴") : "") + `</th>`).join("") +
      "</tr>" + rows.map(row).join("") + "</table>";
    for (const tr of labPane.querySelectorAll("tr.row"))
      tr.onclick = () => {
        if (!tr.dataset.g) return enterCampaign(tr.dataset.c);
        const g = tr.dataset.c;
        labOpen.has(g) ? labOpen.delete(g) : labOpen.add(g);
        renderLab(labRows);
      };
    // A second click on the column already sorted turns it round, as the file tree's
    // headings do.
    for (const th of labPane.querySelectorAll("th[data-s]"))
      th.onclick = () => {
        const v = th.dataset.s;
        labDesc = labSort === v ? !labDesc : LAB_DESC[v];
        labSort = v;
        renderLab(labRows);
      };
  }
  const trs = labPane.querySelectorAll("tr.row");
  rows.forEach((r, i) => {
    const set = (sel, h) => {
      const td = trs[i].querySelector(sel);
      if (td && td.innerHTML !== h) td.innerHTML = h;
    };
    if (r.group) {
      const n = r.members.length, up = r.members.filter(m => m.live).length;
      set(".name", `<span class="dot${r.live ? "" : " off"}"></span>` +
          `<span class="fold">${labOpen.has(r.campaign) ? "▾" : "▸"}</span>` +
          `${esc(r.campaign)} <span class="quiet">· ${n} runs</span>`);
      set(".state", up ? `<span class="on">${up} running</span>` +
          (n - up ? ` <span class="quiet">· ${n - up} stopped</span>` : "")
          : `<span class="quiet">none running</span>`);
      set(".doing", "—");
      set(".run", "—");
      set(".took", r.elapsed_s == null ? "—" : hms(r.elapsed_s));
      set(".last", r.live ? `<span class="on">now</span>` : when(r.ended_at));
      return;
    }
    // A group's run is named by its own part; its model says what sets it apart.
    const name = r.sub ? r.campaign.slice(r.campaign.indexOf("/") + 1) : r.campaign;
    set(".name", `<span class="dot${r.live ? "" : " off"}"></span>${esc(name)}` +
        (r.sub && r.model ? ` <span class="quiet">· ${esc(r.model)}</span>` : ""));
    set(".state", !r.run ? `<span class="quiet">never run</span>`
        : r.live ? `<span class="on">running</span>`
        : `<span class="quiet">${esc(r.status || "stopped")}` +
          (r.stop_reason ? " · " + esc(r.stop_reason) : "") + `</span>`);
    set(".doing", r.live && r.phase
        ? `${esc(r.phase)} <span class="quiet">· ${hms(r.phase_age_s)}</span>`
        : "—");
    set(".run", r.handle ? esc(r.handle) : "—");
    set(".took", r.elapsed_s == null ? "—" : hms(r.elapsed_s));
    // A live run is active now; a stopped one was last active when it ended.
    set(".last", r.live ? `<span class="on">now</span>` : when(r.ended_at));
  });
}

// Who is set up in this lab, one row per person. The rows carry nothing that ticks, so
// this rebuilds only when the set changes -- which is when someone is added.
// Which systems a person has is the thing worth seeing at a glance; the endpoint id and
// the paths are what you go looking for, so they live behind a click.
const userOpen = new Set();

function renderLabUsers(people) {
  if (!people.length) {
    usersPane.innerHTML = `<div class="none">` +
      `no user files yet — add users/&lt;you&gt;/&lt;system&gt;.json</div>`;
    usersPane.dataset.key = "none";
    return;
  }
  const key = people.map(p => p.user + ":" + (p.unreadable ? "x" :
    p.systems.map(a => a.system).join(","))).join("|");
  if (usersPane.dataset.key !== key) {
    usersPane.dataset.key = key;
    usersPane.innerHTML = `<table>` +
      `<tr><th>user</th><th>systems</th><th>transfer</th></tr>` +
      people.map(p => {
        // A person whose directory this process cannot read is still in the lab, so
        // the row says so rather than leaving a gap that reads as nobody.
        if (p.unreadable) return `<tr class="row locked" data-u="${esc(p.user)}">` +
          `<td class="who"><span class="caret"></span>${esc(p.user)}</td>` +
          `<td><span class="chip locked">no read access</span></td>` +
          `<td><span class="quiet">${esc(p.error || "")}</span></td></tr>` +
          `<tr class="detail" data-d="${esc(p.user)}"><td colspan="3"></td></tr>`;
        const stub = p.systems.every(a => a.stub);
        const chips = p.systems.map(a =>
          `<span class="chip${a.stub ? " stub" : ""}">${esc(a.system)}</span>`).join("");
        const moved = p.systems.filter(a => a.transfer).length;
        return `<tr class="row" data-u="${esc(p.user)}">` +
          `<td class="who"><span class="caret"></span>${esc(p.user)}</td>` +
          `<td>${chips}</td>` +
          `<td>${stub ? `<span class="quiet">not set up</span>`
                : moved ? `<span class="on">${moved} of ${p.systems.length}</span>`
                : `<span class="quiet">none</span>`}</td></tr>` +
          `<tr class="detail" data-d="${esc(p.user)}"><td colspan="3"></td></tr>`;
      }).join("") + `</table>`;
    for (const tr of usersPane.querySelectorAll("tr.row"))
      tr.onclick = () => {
        const u = tr.dataset.u;
        userOpen.has(u) ? userOpen.delete(u) : userOpen.add(u);
        paintUsers(people);
      };
  }
  paintUsers(people);
}

// The open/closed state is the only thing that changes between paints, so it is applied
// separately from the table that holds it.
function paintUsers(people) {
  for (const p of people) {
    const open = userOpen.has(p.user);
    const row = usersPane.querySelector(`tr.row[data-u="${CSS.escape(p.user)}"]`);
    const det = usersPane.querySelector(`tr.detail[data-d="${CSS.escape(p.user)}"]`);
    if (!row || !det) continue;
    const caret = row.querySelector(".caret");
    if (caret) caret.textContent = open ? "▾" : "▸";
    det.style.display = open ? "" : "none";
    if (!open) continue;
    const html = p.unreadable
      ? `<div class="kv"><div class="k">status</div><div class="v">this user’s directory cannot be read by the account running the watcher</div></div>`
      :
    p.systems.map(a => `<div class="kv">` +
      `<div class="k">system</div><div class="v">${esc(a.system)}` +
      (a.stub ? ` <span class="quiet">(template, not configured)</span>` : "") + `</div>` +
      `<div class="k">endpoint</div><div class="v">${a.endpoint ? esc(a.endpoint) : "—"}</div>` +
      `<div class="k">account</div><div class="v">${a.account ? esc(a.account) : "—"}</div>` +
      `<div class="k">work dir</div><div class="v">${a.work_dir ? esc(a.work_dir) : "—"}</div>` +
      `<div class="k">transfer</div><div class="v">` +
      (a.transfer ? `<span class="on">configured</span>`
                  : `<span class="quiet">not configured</span>`) + `</div></div>`).join("");
    const td = det.querySelector("td");
    if (td.innerHTML !== html) td.innerHTML = html;
  }
}

async function usersRefresh() {
  let people;
  try { people = await (await fetch("/users")).json(); } catch (e) { return; }
  if (scope !== "lab") return;
  renderLabUsers(people);
}

async function labRefresh() {
  let rows;
  try { rows = await (await fetch("/lab")).json(); }
  catch (e) { document.getElementById("head").textContent = "watcher stopped"; return; }
  if (scope !== "lab") return;
  usersRefresh();
  servicesRefresh();
  const top = labGroups(rows);
  const live = top.filter(r => r.live).length;
  document.getElementById("head").textContent =
    `${top.length} campaign${top.length === 1 ? "" : "s"} · ` +
    (live ? `${live} running` : "none running");
  renderLab(rows);
}

// The lab's own processes. Polled with everything else, and driven by the button beside
// them: the dots are the answer, so what the script printed is not shown.
function renderServices(rows, none) {
  const list = document.getElementById("svclist");
  const html = none
    ? `<span class="svc">none</span>`
    : rows.length
    ? rows.map(r => `<span class="svc${r.running ? " up" : ""}" title="${esc(r.detail)}">` +
                    `<span class="dot"></span>${esc(r.service)}</span>`).join(" ")
    : `<span class="svc">none</span>`;
  if (list.innerHTML !== html) list.innerHTML = html;
  // One button, because there is one thing to do: whatever is not running, start it;
  // when it all is, the only move left is stopping it. A lab with no lab.sh has
  // neither, so the button has nothing to act on.
  const button = document.getElementById("labrun");
  const running = rows.length > 0 && rows.every(r => r.running);
  // A poll lands while the script is still running, and the label it set stands.
  if (button.dataset.busy) return;
  button.disabled = !!none;
  if (none) { button.textContent = "start"; return; }
  button.dataset.act = running ? "stop" : "start";
  button.textContent = running ? "stop" : "start";
}

async function servicesRefresh() {
  let d;
  try { d = await (await fetch("/servicestate")).json(); } catch (e) { return; }
  if (scope === "lab") renderServices(d.rows, d.none);
}

// A start waits for each service to come up, so the button says so rather than looking
// ignored. It stays in place throughout -- disabled while the script runs, never gone.
async function labControl(action) {
  const button = document.getElementById("labrun");
  button.dataset.busy = "1";
  button.disabled = true;
  button.textContent = action === "start" ? "starting…" : "stopping…";
  try {
    var d = await (await fetch("/services?do=" + action, {method: "POST"})).json();
    if (d && d.error) alert(d.error);
  } catch (e) {}
  delete button.dataset.busy;
  button.disabled = false;
  if (d && d.services) renderServices(d.services, false);
  chat();
}

document.getElementById("labrun").onclick = e =>
  labControl(e.currentTarget.dataset.act || "start");

async function campaignList() {
  let names, live = [];
  try { names = await (await fetch("/campaigns")).json(); } catch (e) { return; }
  try { live = await (await fetch("/running")).json(); } catch (e) {}
  let recent = [];
  try { recent = await (await fetch("/recent")).json(); } catch (e) {}
  if (!names.length) return;
  // The campaign being shown has gone from the lab -- its workspace removed while the
  // page was open. The server is answering for the one it started on, so show that.
  if (!names.includes(campaign)) {
    campaign = HOME; run = ""; agentSel = "";
    if (scope === "campaign") {
      history.replaceState(null, "", address());
    }
    showScope();
  }
  // Campaigns with an agent at work come first, marked, so a running one is a click away;
  // then those that ended recently, newest first, marked as ended and how long ago.
  const ended = {};
  recent.forEach(r => { if (names.includes(r.campaign)) ended[r.campaign] = r.ended; });
  const recentNames = Object.keys(ended).sort((a, b) => ended[b] - ended[a]);
  const ordered = names.filter(n => live.includes(n)).concat(recentNames,
    names.filter(n => !live.includes(n) && !(n in ended)));
  const ago = t => { const h = (Date.now() / 1000 - t) / 3600;
    return h < 1 ? `${Math.max(1, Math.round(h * 60))}m` : `${Math.round(h)}h`; };
  const key = ordered.join("|") + "#" + live.join("|") + "#" +
    recentNames.map(n => ago(ended[n])).join("|");
  if (camp.dataset.key !== key) {
    camp.dataset.key = key;
    camp.innerHTML = ordered.map(n => live.includes(n)
      ? `<option value="${esc(n)}" class="live">● ${esc(n)}</option>`
      : n in ended
      ? `<option value="${esc(n)}" class="recent">○ ${esc(n)} · ended ${ago(ended[n])} ago</option>`
      : `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  }
  camp.value = campaign;
}

camp.onchange = () => enterCampaign(camp.value);
document.getElementById("home").onclick = () => { if (scope !== "lab") goLab(); };

document.querySelector("#say button").onclick = say;
document.getElementById("msg").addEventListener(
  "keydown", e => { if (e.key === "Enter") say(); });

showScope();
campaignList(); files(); refresh(); chat();
setInterval(refresh, 1500);
setInterval(files, 10000);
// A campaign that starts while the page is open should appear in the list.
setInterval(campaignList, 10000);
setInterval(chat, 2000);
