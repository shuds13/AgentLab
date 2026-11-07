// Every budget of the run tree with how much of it is used: the run's own, the
// resources it draws on, each worker's, and the changes made to them. A page opened
// with the watcher's key may change the run's budgets here.
import { html, useState, usePoll, getJSON, hms, plural, tone } from "./util.js";

export const fmtBudget = r => r.unit === "s" ? hms : r.unit === "h" ? (v => (+v).toFixed(1)) : String;

export function Meter({used, limit, fmt, compact}) {
  fmt = fmt || String;
  if (limit == null) return html`<span><span class="num">${fmt(used || 0)}</span> <span class="faint">no limit</span></span>`;
  const pct = limit ? Math.min(100, 100 * (used || 0) / limit) : 0;
  return html`<div class="metercell ${compact ? "compact" : ""}">
    <div class="meter ${pct >= 100 ? "warn" : ""}"><i style=${`width:${pct}%`}></i></div>
    <span class="amt num">${fmt(used || 0)}<span class="faint"> / ${fmt(limit)}</span></span>
  </div>`;
}

// A change is +N or -N, or N to set it; run time also takes s, m or h.
function Change({d, r, campaign, onDone}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const apply = async () => {
    const change = value.trim();
    if (!change || busy) return;
    setBusy(true);
    let res;
    try {
      const resp = await fetch("/budget?" + new URLSearchParams({c: campaign, run: d.run}),
        {method: "POST", body: JSON.stringify({name: r.name, key: r.key || null, change})});
      res = await resp.json();
    } catch (e) { res = {ok: false, error: String(e)}; }
    setBusy(false);
    if (res.ok) setValue("");
    onDone(res);
  };
  return html`<div class="change">
    <input value=${value} placeholder=${r.unit === "s" ? "+30m" : "+N"} size="7"
      onInput=${e => setValue(e.target.value)} onKeyDown=${e => e.key === "Enter" && apply()} />
    <button class="btn sm" disabled=${!value.trim() || busy} onClick=${apply}>Apply</button>
  </div>`;
}

function BudgetTable({rows, d, campaign, editable, onDone}) {
  return html`<table class="table budgets">
    <tbody>${rows.map(r => html`<tr key=${r.name + ":" + (r.key || "")}>
      <td class="what">${r.label}</td>
      <td style="width:55%"><${Meter} used=${r.used} limit=${r.limit} fmt=${fmtBudget(r)} /></td>
      ${d.writable && html`<td class="r">${editable(r) && html`<${Change} d=${d} r=${r} campaign=${campaign} onDone=${onDone} />`}</td>`}
    </tr>`)}</tbody>
  </table>`;
}

export function Budgets({campaign, run, onTab}) {
  const [note, setNote] = useState(null);
  const {data: d, error, reload} = usePoll(() => getJSON("/budgets", {c: campaign, run}), 3000, [campaign, run], {keep: true});
  if (!d) return html`<div class="empty">${error ? error.message : "Loading…"}</div>`;
  if (!d.run) return html`<div class="empty">This campaign has no run yet.</div>`;
  const onDone = res => {
    setNote(res.ok ? {ok: true, text: "Changed: " + res.done.join("; ") + ". The agent is told at its next turn."}
                   : {ok: false, text: "Not changed: " + res.error});
    reload();
  };
  const nh = r => r.name === "node_hours" || r.name === "group_node_hours";
  const ofRun = d.rows.filter(r => !nh(r)), ofSys = d.rows.filter(nh);
  const shapes = Object.entries(d.buckets || {});
  const workers = d.workers || [];
  const cell = (w, n) => { const r = w.rows.find(x => x.name === n); return r ? html`<${Meter} used=${r.used} limit=${r.limit} fmt=${fmtBudget(r)} compact />` : ""; };

  return html`
    ${note && html`<div class="banner ${note.ok ? "ok" : "bad"}">${note.text}
      <button class="x" onClick=${() => setNote(null)}>×</button></div>`}
    <div class="cols">
      <div class="col">
        <section class="card">
          <h2>Run budgets<span class="right">totals for the whole run${d.part > 1 ? `, over its ${d.part} parts` : ""}</span></h2>
          <${BudgetTable} rows=${ofRun} d=${d} campaign=${campaign} editable=${() => true} onDone=${onDone} />
        </section>
        ${(ofSys.length > 0 || shapes.length > 0) && html`<section class="card">
          <h2>Resources<span class="right"><a href="#" onClick=${e => { e.preventDefault(); onTab("resources"); }}>Blocks and tasks →</a></span></h2>
          ${ofSys.length > 0 && html`<${BudgetTable} rows=${ofSys} d=${d} campaign=${campaign}
              editable=${r => r.name === "node_hours"} onDone=${onDone} />`}
          ${shapes.length > 0 && html`<table class="table">
            <thead><tr><th>Bucket</th><th>System</th><th>Queue</th><th class="r">Nodes / block</th><th class="r">Walltime</th><th class="r">Max at a time</th></tr></thead>
            <tbody>${shapes.map(([k, b]) => html`<tr><td>${k}</td><td>${b.system || "—"}</td><td>${b.queue || "—"}</td>
              <td class="r">${b.num_nodes || 1}</td><td class="r">${b.walltime || "—"}</td><td class="r">${b.max_blocks || 1}</td></tr>`)}</tbody>
          </table>`}
        </section>`}
        ${workers.length > 0 && html`<section class="card">
          <h2>Workers<span class="right">${plural(workers.length, "worker")}</span></h2>
          <table class="table">
            <thead><tr><th>Worker</th><th>Kind</th><th>State</th><th style="width:30%">Tasks</th><th style="width:30%">Run time</th></tr></thead>
            <tbody>${workers.map(w => html`<tr class=${d.selected ? (w.run === d.selected ? "me" : "other") : ""}>
              <td>${w.branch || w.run}</td><td class="muted">${w.kind || ""}</td>
              <td><span class="dot ${tone(w.status)}" style="display:inline-block;margin-right:6px"></span>${w.status || ""}</td>
              <td>${cell(w, "submits")}</td><td>${cell(w, "runtime_s")}</td></tr>`)}</tbody>
          </table>
        </section>`}
      </div>
      <div class="col">
        <section class="card">
          <h2>Changing a budget</h2>
          <div class="body help">
            ${d.writable
              ? html`<p>Type a change beside a budget and press Enter: <code>+N</code> or <code>-N</code>, or <code>N</code> to set it. Run time also takes <code>m</code> or <code>h</code>, e.g. <code>+2h</code>.</p>`
              : html`<p class="muted">Read-only here. Changes need the watcher's own link, on a run of the user the watcher runs as.</p>`}
            <p>From a shell:</p>
            <pre class="code">bin/budget.sh ${d.workspace} NAME=+N</pre>
            <p class="faint">e.g. submits=+10, workers.explorer=+4, node_hours.polaris=+5, runtime_s=+2h</p>
          </div>
        </section>
        ${(d.changes || []).length > 0 && html`<section class="card">
          <h2>Changes<span class="right">${d.changes.length}</span></h2>
          <table class="table">
            <thead><tr><th>When</th><th>Budget</th><th class="r">Change</th><th>By</th></tr></thead>
            <tbody>${[...d.changes].reverse().map(c => html`<tr>
              <td class="muted">${new Date(c.ts * 1000).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"})}</td>
              <td>${c.name}${c.key ? html` <span class="faint">${c.key}</span>` : ""}</td>
              <td class="r num">${c.before == null ? "no limit" : c.before} → ${c.after}</td>
              <td class="muted">${c.who || ""}</td></tr>`)}</tbody>
          </table>
        </section>`}
      </div>
    </div>`;
}
