// The campaign list: running first, then recently ended, then all of them, with groups
// (`group/name`) folded under their group.
import { html, useState, useEffect, usePoll, getJSON } from "./util.js";
import { AgentTree } from "./agents.js";

function useCampaigns() {
  const names = usePoll(() => getJSON("/campaigns"), 15000, []);
  const running = usePoll(() => getJSON("/running"), 5000, []);
  const recent = usePoll(() => getJSON("/recent"), 30000, []);
  return {names: names.data || [], running: running.data || [], recent: recent.data || []};
}

const ago = t => {
  const s = Date.now() / 1000 - t;
  return s < 3600 ? `${Math.max(1, Math.round(s / 60))}m` : `${Math.round(s / 3600)}h`;
};

function Item({name, label, on, dot, meta, sub, onPick}) {
  return html`<div class="navitem ${on ? "on" : ""} ${sub ? "sub" : ""}" title=${name}
                   onClick=${() => onPick(name)}>
    <span class="dot ${dot || ""}"></span>
    <span class="name">${label || name}</span>
    ${meta && html`<span class="meta">${meta}</span>`}
  </div>`;
}

// Inside a campaign whose run started workers, the sidebar shows its agents, so one can
// be chosen from any tab; a switch at the top goes back to the campaigns.
export function Sidebar({campaign, onPick, theme, onTheme, top, agent, onAgent}) {
  const {names, running, recent} = useCampaigns();
  const hasAgents = !!campaign && !!top && (top.workers || []).length > 0;
  const [mode, setMode] = useState(null);
  useEffect(() => setMode(null), [campaign]);
  const show = mode || (hasAgents ? "agents" : "campaigns");
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(() => new Set(campaign.includes("/") ? [campaign.split("/")[0]] : []));
  const live = new Set(running);
  const f = filter.trim().toLowerCase();
  const match = n => !f || n.toLowerCase().includes(f);

  const recentRows = recent.filter(r => !live.has(r.campaign) && match(r.campaign));
  const groups = new Map();
  for (const n of names.filter(match)) {
    const g = n.includes("/") ? n.split("/")[0] : "";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(n);
  }
  const toggle = g => setOpen(s => { const t = new Set(s); t.has(g) ? t.delete(g) : t.add(g); return t; });
  const all = [];
  const order = [...[...groups.keys()].filter(Boolean).map(key => ({key, group: true})),
                 ...(groups.get("") || []).map(key => ({key, group: false}))]
    .sort((a, b) => a.key.localeCompare(b.key) || b.group - a.group);
  for (const {key, group} of order) {
    if (group) {
      const members = groups.get(key);
      const isOpen = open.has(key) || !!f;
      const anyLive = members.some(m => live.has(m));
      all.push(html`<div class="navitem" onClick=${() => toggle(key)} title=${key}>
        <span class="caret">${isOpen ? "▾" : "▸"}</span>
        <span class="name">${key}</span>
        <span class="meta">${anyLive ? html`<span class="dot ok" style="display:inline-block;margin-right:6px"></span>` : ""}${members.length}</span>
      </div>`);
      if (isOpen) for (const m of members)
        all.push(html`<${Item} name=${m} label=${m.slice(key.length + 1)} sub on=${m === campaign}
                                dot=${live.has(m) ? "ok" : ""} onPick=${onPick} />`);
    } else {
      all.push(html`<${Item} name=${key} on=${key === campaign} dot=${live.has(key) ? "ok" : ""} onPick=${onPick} />`);
    }
  }

  return html`<aside class="sidebar">
    <div class="brand"><span class="logo">A</span>AgentLab</div>
    ${hasAgents && html`<div class="seg sideseg">
      <button class=${show === "campaigns" ? "on" : ""} onClick=${() => setMode("campaigns")}>Campaigns</button>
      <button class=${show === "agents" ? "on" : ""} onClick=${() => setMode("agents")}>Agents</button>
    </div>`}
    ${show === "agents" ? html`<div class="navlist flushlist">
      <div class="sidecamp" title=${campaign}>${campaign}</div>
      <${AgentTree} top=${top} agent=${agent} onAgent=${onAgent} />
    </div>` : html`
    <div class="search"><input placeholder="Filter campaigns" value=${filter}
      onInput=${e => setFilter(e.target.value)} /></div>
    <div class="navlist">
      <div class="navitem ${campaign ? "" : "on"}" onClick=${() => onPick("")}>
        <span class="caret">⌂</span><span class="name">Lab overview</span>
        <span class="meta">${names.length}</span>
      </div>
      ${running.filter(match).length > 0 && html`
        <div class="navsec"><span>Running</span><span>${running.length}</span></div>
        ${running.filter(match).map(n => html`<${Item} name=${n} on=${n === campaign} dot="ok live" onPick=${onPick} />`)}`}
      ${recentRows.length > 0 && html`
        <div class="navsec"><span>Ended recently</span></div>
        ${recentRows.map(r => html`<${Item} name=${r.campaign} on=${r.campaign === campaign}
                                           meta=${ago(r.ended)} onPick=${onPick} />`)}`}
      <div class="navsec"><span>All campaigns</span></div>
      ${all}
    </div>`}
    <div class="sidefoot">
      <a href=${"/?" + (campaign ? "c=" + encodeURIComponent(campaign) : "lab")} class="muted">Classic view</a>
      <button class="btn sm" onClick=${onTheme} title="Switch theme">${theme === "light" ? "Dark" : "Light"}</button>
    </div>
  </aside>`;
}
