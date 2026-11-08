// The lab: every campaign's latest run, running ones first. Workspaces under one
// directory (a comparison, a set of variants) are one row for their group, which opens
// to its own page.
import { html, useState, usePoll, getJSON, hms, usd, when, tone, plural } from "./util.js";

const COLUMNS = [
  ["campaign", "Campaign"], ["status", "State"], ["model", "Model"], ["doing", "Doing / stopped"],
  ["elapsed_s", "Run time", "r"], ["started_at", "Started"],
];

const doing = r => (r.live ? r.phase : r.stop_reason) || "";

// A group's row stands for its runs: running if any is, started when the latest did.
export function groupRows(rows) {
  const out = [], groups = {};
  for (const r of rows) {
    const i = r.campaign.indexOf("/");
    if (i < 0) { out.push(r); continue; }
    const g = r.campaign.slice(0, i);
    if (!groups[g]) out.push(groups[g] = {campaign: g, group: true, members: []});
    groups[g].members.push(r);
  }
  for (const g of Object.values(groups)) {
    const m = g.members, ran = m.filter(r => r.run);
    g.live = m.some(r => r.live);
    g.up = m.filter(r => r.live).length;
    g.started_at = ran.map(r => r.started_at || "").sort().pop() || null;
    g.elapsed_s = ran.length ? Math.max(...ran.map(r => r.elapsed_s || 0)) : null;
    const costs = m.map(r => r.usd).filter(v => v != null);
    g.usd = costs.length ? costs.reduce((a, b) => a + b, 0) : null;
  }
  return out;
}

function order(rows, sort) {
  const val = (r, k) => k === "doing" ? doing(r) : r[k];
  return [...rows].sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1;
    const x = val(a, sort.key), y = val(b, sort.key);
    const c = x == null || x === "" ? 1 : y == null || y === "" ? -1
      : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
    return (sort.desc ? -c : c) || a.campaign.localeCompare(b.campaign);
  });
}

function RunRow({r, label, onPick}) {
  return html`<tr class="click" onClick=${() => onPick(r.campaign)}>
    <td><b style="font-weight:500">${label || r.campaign}</b></td>
    <td><span class="pill ${tone(r.status, r.live)}"><span class="dot ${tone(r.status, r.live)} ${r.live ? "live" : ""}"></span>${!r.run ? "never run" : r.live ? "running" : r.status || "—"}</span></td>
    <td class="muted">${(r.model || "—").replace(/^claude-/, "")}</td>
    <td class="wrap muted" style="max-width:360px">${doing(r)}</td>
    <td class="r">${hms(r.elapsed_s)}</td>
    <td class="muted">${when(r.started_at)}</td>
  </tr>`;
}

function GroupRow({g, onGroup}) {
  return html`<tr class="click grouprow" onClick=${() => onGroup(g.campaign)}>
    <td><b style="font-weight:500">${g.campaign}</b> <span class="faint">${plural(g.members.length, "run")}</span></td>
    <td>${g.up ? html`<span class="pill ok"><span class="dot ok live"></span>${g.up} running</span>`
               : html`<span class="pill">none running</span>`}</td>
    <td class="muted">—</td><td class="muted">—</td>
    <td class="r">${hms(g.elapsed_s)}</td>
    <td class="muted">${when(g.started_at)}</td>
  </tr>`;
}

function useSort(initial) {
  const [sort, setSort] = useState(initial);
  const head = (k, label, cls) => html`<th class=${cls || ""} style="cursor:pointer"
    onClick=${() => setSort(s => ({key: k, desc: s.key === k ? !s.desc : k !== "campaign"}))}>
    ${label}${sort.key === k ? (sort.desc ? " ↓" : " ↑") : ""}</th>`;
  return [sort, head];
}

// Runs as a table; a group's are named by their own part.
export function RunsTable({rows, onPick, strip}) {
  const [sort, head] = useSort({key: "started_at", desc: true});
  return html`<section class="card scrollx">
    <table class="table">
      <thead><tr>${COLUMNS.map(([k, l, c]) => head(k, l, c))}</tr></thead>
      <tbody>${order(rows, sort).map(r => html`<${RunRow} key=${r.campaign} r=${r} onPick=${onPick}
        label=${strip ? r.campaign.slice(r.campaign.indexOf("/") + 1) : null} />`)}</tbody>
    </table>
  </section>`;
}

export function Lab({onPick, onGroup}) {
  const {data, error} = usePoll(() => getJSON("/lab"), 10000, [], {keep: true});
  const [sort, head] = useSort({key: "started_at", desc: true});
  if (!data) return html`<div class="empty">${error ? "Could not load the lab: " + error.message : "Loading the lab…"}</div>`;
  const rows = [];
  for (const r of order(groupRows(data), sort))
    rows.push(r.group ? html`<${GroupRow} key=${"g:" + r.campaign} g=${r} onGroup=${onGroup} />`
                      : html`<${RunRow} key=${r.campaign} r=${r} onPick=${onPick} />`);
  return html`<section class="card scrollx">
    <table class="table">
      <thead><tr>${COLUMNS.map(([k, l, c]) => head(k, l, c))}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </section>`;
}

// A group: its resource session, if it has one, and its runs.
export function Group({group, onPick}) {
  const names = usePoll(() => getJSON("/campaigns"), 15000, [], {keep: true}).data || [];
  const first = names.find(n => n.startsWith(group + "/"));
  const {data} = usePoll(() => first ? getJSON("/group", {c: first}) : null, 5000, [first], {keep: true});
  if (!data) return html`<div class="empty">${names.length && !first ? "No runs in this group." : "Loading…"}</div>`;
  const runs = data.runs || [];
  const s = data.session;
  const up = runs.filter(r => r.live).length;
  const costs = runs.map(r => r.usd).filter(v => v != null);
  return html`
    <div class="kpis">
      <div class="kpi"><div class="label">Runs</div><div class="value">${runs.length}</div>
        <div class="sub">${up ? `${up} running` : "none running"}</div></div>
      <div class="kpi"><div class="label">Cost</div><div class="value">${costs.length ? usd(costs.reduce((a, b) => a + b, 0)) : "—"}</div>
        <div class="sub">${costs.length < runs.length ? `${costs.length} of ${runs.length} runs have a cost` : "all runs of the group"}</div></div>
      ${s && Object.entries(s.node_hours || {}).map(([sy, cap]) => html`<div class="kpi">
        <div class="label">Node-hours, ${sy}</div>
        <div class="value">${(s.used[sy] || 0).toFixed(1)} <span class="faint" style="font-size:15px">/ ${cap}</span></div>
        <div class="sub">resource session${s.stopped ? " (stopped)" : ""} · ${plural(s.runs, "run")}</div></div>`)}
    </div>
    <${RunsTable} rows=${runs} onPick=${onPick} strip />`;
}
