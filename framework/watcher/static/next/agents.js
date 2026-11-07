// The run and every worker it started, as a tree by who started whom. Agents the
// framework started (a resource agent) sit beside the run, not under it.
import { html, useLayoutEffect, useRef, usd, score, plural, tone } from "./util.js";

const modelCost = s => (s.cost_models || []).reduce((t, m) => t + (m.usd || 0), 0);

export function AgentTree({top, agent, onAgent}) {
  const list = useRef(null);
  const ws = (top && top.workers) || [];
  // The chosen agent is kept in view when the page opens on it.
  useLayoutEffect(() => {
    const el = list.current && list.current.querySelector(".agent.on");
    if (el) el.scrollIntoView({block: "nearest"});
  }, [agent, ws.length > 0]);
  if (!top) return html`<div class="empty">Loading…</div>`;
  const costs = top.show_cost !== false;
  const fw = ws.filter(w => w.started_by === "framework" || w.kind === "resources");
  const own = ws.filter(w => !fw.includes(w));
  const byBranch = Object.fromEntries(ws.map(w => [w.branch, w]));
  const me = {run: "", branch: own.length ? "coordinator" : "researcher", method: top.method,
              status: top.status, usd: modelCost(top), score: null};
  const rows = [];
  const row = (a, depth, extra) => rows.push(html`
    <div class="agent ${a.run === agent ? "on" : ""}" onClick=${() => onAgent(a.run)}
         title=${`${a.branch || a.run} · ${(a.method || a.kind || "") + (extra || "")} · ${a.status || ""}`}>
      <span class="who" style=${`padding-left:${Math.min(depth, 6) * 10}px`}>
        <span class="dot ${tone(a.status)} ${a.status === "running" ? "live" : ""}"></span>
        <b>${a.branch || a.run}</b>
      </span>
      <span class="score" title=${score(a.score)}>${a.score == null ? "" : String(+(+a.score).toPrecision(4))}</span>
      ${costs && html`<span class="usd">${usd(a.usd)}</span>`}
    </div>`);
  const below = (branch, depth) => own
    .filter(w => (byBranch[w.parent] ? w.parent : null) === branch)
    .forEach(w => { row(w, depth); below(w.branch, depth + 1); });
  fw.forEach(w => row(w, 0, " · framework"));
  row(me, 0);
  below(null, 1);
  const running = ws.filter(w => w.status === "running").length;
  return html`<div class="agents" ref=${list}>
    <div class="agentshead"><span>${plural(own.length, "worker")}${fw.length ? ` + ${fw.length} framework` : ""}</span>
      <span>${running} running</span></div>
    ${rows}
    ${costs && html`<div class="total"><span>Total</span><span class="num">${usd(top.tree_usd)}</span></div>`}
  </div>`;
}
