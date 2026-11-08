// The campaign list: running first, then recently ended, then all of them, with groups
// (`group/name`) folded under their group.
import { html, useState, useEffect, usePoll, getJSON } from "./util.js";
import { AgentTree, GroupRuns } from "./agents.js";

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

// The sidebar's width, dragged from its right edge and remembered.
const WIDTH = "sidebarWidth";
const clampWidth = w => Math.max(200, Math.min(window.innerWidth * 0.7, w));
const setWidth = w => document.documentElement.style.setProperty("--sidebar", w + "px");
try { const w = parseInt(localStorage.getItem(WIDTH), 10); if (w > 0) setWidth(clampWidth(w)); } catch (e) {}

function startDrag(e) {
  const side = e.currentTarget.parentElement, from = e.clientX, start = side.offsetWidth;
  const move = m => setWidth(clampWidth(start + m.clientX - from));
  const up = () => {
    removeEventListener("mousemove", move); removeEventListener("mouseup", up);
    document.body.style.userSelect = "";
    try { localStorage.setItem(WIDTH, side.offsetWidth); } catch (err) {}
  };
  document.body.style.userSelect = "none";
  addEventListener("mousemove", move); addEventListener("mouseup", up);
  e.preventDefault();
}

// Inside a campaign whose run started workers, the sidebar shows its agents, so one can
// be chosen from any tab; a switch at the top goes back to the campaigns.
export function Sidebar({campaign, group: groupOn, onGroup, onPick, theme, onTheme, top, agent, onAgent}) {
  const {names, running, recent} = useCampaigns();
  const inGroup = campaign.includes("/");
  const hasAgents = !!campaign && !!top && (top.workers || []).length > 0;
  // A run in a group shows the group's runs, with its own agents under it.
  const context = inGroup ? "group" : hasAgents ? "agents" : null;
  const [mode, setMode] = useState(null);
  useEffect(() => setMode(null), [campaign]);
  const show = mode === "campaigns" || !context ? "campaigns" : context;
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
      const isOpen = open.has(key) || !!f || key === groupOn;
      const anyLive = members.some(m => live.has(m));
      all.push(html`<div class="navitem ${groupOn === key ? "on" : ""}" title=${key}
          onClick=${() => { onGroup(key); setOpen(s => new Set(s).add(key)); }}>
        <span class="caret" title=${isOpen ? "Fold" : "Unfold"} onClick=${e => { e.stopPropagation(); toggle(key); }}>${isOpen ? "▾" : "▸"}</span>
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
    <div class="sidedrag" onMouseDown=${startDrag} title="Drag to widen"></div>
    <div class="brand"><span class="logo">A</span>AgentLab</div>
    ${context && html`<div class="seg sideseg">
      <button class=${show === "campaigns" ? "on" : ""} onClick=${() => setMode("campaigns")}>Campaigns</button>
      <button class=${show === context ? "on" : ""} onClick=${() => setMode(null)}>${context === "group" ? "Group" : "Agents"}</button>
    </div>`}
    ${show === "agents" ? html`<div class="navlist flushlist">
      <div class="sidecamp" title=${campaign}>${campaign}</div>
      <${AgentTree} top=${top} agent=${agent} onAgent=${onAgent} />
    </div>` : show === "group" ? html`<div class="navlist flushlist">
      <div class="sidecamp"><a href="#" class="grouplink" title="The group's page"
        onClick=${e => { e.preventDefault(); onGroup(campaign.split("/")[0]); }}>${campaign.split("/")[0]}</a></div>
      <${GroupRuns} campaign=${campaign} top=${top} agent=${agent} onAgent=${onAgent} onPick=${onPick} />
    </div>` : html`
    <div class="search"><input placeholder="Filter campaigns" value=${filter}
      onInput=${e => setFilter(e.target.value)} /></div>
    <div class="navlist">
      <div class="navitem ${campaign || groupOn ? "" : "on"}" onClick=${() => onPick("")}>
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
