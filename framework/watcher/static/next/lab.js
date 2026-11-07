// The lab: every campaign's latest run, running ones first.
import { html, useState, usePoll, getJSON, hms, usd, score, when, tone } from "./util.js";

const COLUMNS = [
  ["campaign", "Campaign"], ["status", "State"], ["model", "Model"], ["doing", "Doing / stopped"],
  ["elapsed_s", "Run time", "r"], ["usd", "Cost", "r"], ["score", "Score", "r"], ["started_at", "Started"],
];

export function Lab({onPick}) {
  const {data, error} = usePoll(() => getJSON("/lab"), 10000, []);
  const [sort, setSort] = useState({key: "started_at", desc: true});
  if (!data) return html`<div class="empty">${error ? "Could not load the lab: " + error.message : "Loading the lab…"}</div>`;
  const val = (r, k) => k === "doing" ? (r.live ? r.phase : r.stop_reason) || "" : r[k];
  const rows = [...data].sort((a, b) => {
    if (a.live !== b.live) return a.live ? -1 : 1;
    const x = val(a, sort.key), y = val(b, sort.key);
    const c = x == null ? 1 : y == null ? -1 : typeof x === "number" ? x - y : String(x).localeCompare(String(y));
    return sort.desc ? -c : c;
  });
  const head = (k, label, cls) => html`<th class=${cls || ""} style="cursor:pointer"
    onClick=${() => setSort(s => ({key: k, desc: s.key === k ? !s.desc : k !== "campaign"}))}>
    ${label}${sort.key === k ? (sort.desc ? " ↓" : " ↑") : ""}</th>`;
  return html`<section class="card">
    <table class="table">
      <thead><tr>${COLUMNS.map(([k, l, c]) => head(k, l, c))}</tr></thead>
      <tbody>${rows.map(r => html`<tr class="click" onClick=${() => onPick(r.campaign)}>
        <td><b style="font-weight:500">${r.campaign}</b></td>
        <td><span class="pill ${tone(r.status, r.live)}"><span class="dot ${tone(r.status, r.live)} ${r.live ? "live" : ""}"></span>${r.live ? "running" : r.status || "—"}</span></td>
        <td class="muted">${(r.model || "—").replace(/^claude-/, "")}</td>
        <td class="wrap muted" style="max-width:360px">${val(r, "doing")}</td>
        <td class="r">${hms(r.elapsed_s)}</td>
        <td class="r">${usd(r.usd)}</td>
        <td class="r">${score(r.score)}</td>
        <td class="muted">${when(r.started_at)}</td>
      </tr>`)}</tbody>
    </table>
  </section>`;
}
