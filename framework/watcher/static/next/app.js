// The page: a sidebar of campaigns, and the lab or one campaign beside it. Where you are
// is kept in the address, so a reload or a bookmark comes back to it.
import { html, render, useState, useEffect, usePoll, getJSON, hms, usd, when, tone } from "./util.js";
import { Sidebar } from "./sidebar.js";
import { Overview } from "./overview.js";
import { Log } from "./log.js";
import { Lab } from "./lab.js";
import { Budgets } from "./budgets.js";
import { Resources } from "./resources.js";
import { Chat } from "./chat.js";
import { Files } from "./files.js";

const TABS = [["overview", "Overview"], ["log", "Log"], ["files", "Files"], ["budgets", "Budgets"], ["resources", "Resources"]];

function readAddress() {
  const q = new URLSearchParams(location.search);
  const named = q.get("c");
  const campaign = named != null ? named
    : q.has("lab") ? "" : (WATCH.scope === "campaign" ? WATCH.campaign : "");
  return {campaign, run: q.get("run") || "", agent: q.get("agent") || "", file: q.get("file") || "",
          tab: TABS.some(([t]) => t === q.get("tab")) ? q.get("tab") : "overview"};
}

function writeAddress(v, push) {
  const q = new URLSearchParams();
  if (v.campaign) q.set("c", v.campaign); else q.set("lab", "");
  if (v.run) q.set("run", v.run);
  if (v.agent) q.set("agent", v.agent);
  if (v.campaign && v.tab !== "overview") q.set("tab", v.tab);
  if (v.campaign && v.tab === "files" && v.file) q.set("file", v.file);
  const url = location.pathname + "?" + q.toString().replace(/^lab=$/, "lab");
  history[push ? "pushState" : "replaceState"](null, "", url);
}

// Runs started by the user rather than by another run, newest first, for the picker.
function RunPicker({campaign, run, onRun}) {
  const {data} = usePoll(() => getJSON("/runs", {c: campaign}), 15000, [campaign]);
  const tops = (data || []).filter(r => !r.parent_run);
  if (tops.length < 2) return null;
  return html`<select class="select" value=${run} onChange=${e => onRun(e.target.value)} title="Which run">
    <option value="">Latest run</option>
    ${tops.map(r => html`<option value=${r.run}>${when(r.started_at)} · ${r.status}${r.usd != null ? " · " + usd(r.usd) : ""}</option>`)}
  </select>`;
}

function Campaign({view, go, chat, top}) {
  const {campaign, run, agent, tab} = view;
  const picked = usePoll(() => agent ? getJSON("/status", {c: campaign, run: agent}) : null,
                         2000, [campaign, agent], {keep: true}).data;
  const s = agent ? picked || top : top;
  const workers = (top && top.workers) || [];
  const agentRow = workers.find(w => w.run === agent);
  // A worker the run no longer lists is let go.
  useEffect(() => { if (agent && top && top.run && !agentRow) go({agent: ""}); }, [top, agent]);
  const lead = workers.some(w => w.kind !== "resources") ? "coordinator" : "researcher";
  const status = top && top.status;
  const live = status === "running";

  return html`<div class="work"><div class="main">
    <header class="topbar">
      <div class="titlerow">
        <h1 title=${campaign}>${campaign}</h1>
        ${top && top.run && html`<span class="pill ${tone(status)}"><span class="dot ${tone(status)} ${live ? "live" : ""}"></span>${status}</span>`}
        <span class="spacer"></span>
        <${RunPicker} campaign=${campaign} run=${run} onRun=${r => go({run: r, agent: ""}, true)} />
        ${!chat.open && html`<button class="btn" onClick=${() => chat.set(true)}>Chat</button>`}
      </div>
      <div class="subrow">
        ${top && top.run ? html`
          <span>${top.handle || "—"}</span>
          <span class="mono faint">${top.run}</span>
          <span>${hms(top.elapsed_s)}</span>
          ${top.show_cost !== false && top.tree_usd != null && html`<span>${usd(top.tree_usd)} total</span>`}
          ${workers.length > 0 && html`<span class="chip" title="Whose run the tabs show">
            ${agent ? (agentRow ? agentRow.branch : agent) : lead}
            ${agent && html`<button onClick=${() => go({agent: ""}, true)} title="Back to the ${lead}">×</button>`}
          </span>`}`
        : html`<span class="faint">${top ? "No run yet" : "Loading…"}</span>`}
      </div>
      <nav class="tabs">
        ${TABS.map(([t, label]) => html`<button class="tab ${tab === t ? "on" : ""}" onClick=${() => go({tab: t}, true)}>${label}</button>`)}
      </nav>
    </header>
    ${tab === "log"
      ? html`<div class="content flush"><${Log} campaign=${campaign} run=${agent || run} /></div>`
      : tab === "files"
      ? html`<div class="content flush"><${Files} campaign=${campaign} run=${agent || run} sel=${view.file}
               onSel=${f => go({file: f}, true)} records=${s && s.records}
               agentName=${agent ? (agentRow ? agentRow.branch : agent) : workers.length ? lead : "The agent"} /></div>`
      : tab === "budgets"
      ? html`<div class="content"><${Budgets} campaign=${campaign} run=${agent || run} onTab=${t => go({tab: t}, true)} /></div>`
      : tab === "resources"
      ? html`<div class="content"><${Resources} campaign=${campaign} run=${agent || run}
               agentName=${agentRow && agentRow.branch} /></div>`
      : html`<div class="content"><${Overview} s=${s}
               onAgent=${a => go({agent: a}, true)} onTab=${t => go({tab: t}, true)} /></div>`}
  </div>
  ${chat.open && html`<${Chat} campaign=${campaign} run=${run} worker=${agentRow}
      ended=${!!(top && top.run && !live)} onClose=${() => chat.set(false)} />`}
  </div>`;
}

function LabPage({go, chat}) {
  return html`<div class="work"><div class="main">
    <header class="topbar" style="padding-bottom:14px">
      <div class="titlerow"><h1>Lab overview</h1><span class="spacer"></span>
        ${!chat.open && html`<button class="btn" onClick=${() => chat.set(true)}>Secretary</button>`}</div>
      <div class="subrow">Every campaign's latest run. Running campaigns come first.</div>
    </header>
    <div class="content"><${Lab} onPick=${c => go({campaign: c, run: "", agent: "", file: "", tab: "overview"}, true)} /></div>
  </div>
  ${chat.open && html`<${Chat} lab onClose=${() => chat.set(false)} />`}
  </div>`;
}

function App() {
  const [view, setView] = useState(readAddress);
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  useEffect(() => {
    const back = () => setView(readAddress());
    addEventListener("popstate", back);
    writeAddress(view, false);
    return () => removeEventListener("popstate", back);
  }, []);
  useEffect(() => { document.title = (view.campaign || "Lab") + " · AgentLab"; }, [view.campaign]);
  const go = (change, push) => setView(v => {
    const next = {...v, ...change};
    writeAddress(next, push);
    return next;
  });
  const [chatOpen, setChatOpen] = useState(() => { try { return localStorage.getItem("chat") !== "closed"; } catch (e) { return true; } });
  const chat = {open: chatOpen, set: open => {
    try { localStorage.setItem("chat", open ? "open" : "closed"); } catch (e) {}
    setChatOpen(open);
  }};
  // The run's status, for the page and for the agents in the sidebar.
  const here = view.campaign + "|" + view.run;
  const polled = usePoll(() => view.campaign
    ? getJSON("/status", {c: view.campaign, run: view.run}).then(d => ({...d, _for: here})) : null,
    2000, [view.campaign, view.run]).data;
  const top = polled && polled._for === here ? polled : null;
  const flipTheme = () => {
    const t = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("theme", t); } catch (e) {}
    setTheme(t);
  };
  return html`<div class="shell">
    <${Sidebar} campaign=${view.campaign} theme=${theme} onTheme=${flipTheme}
      top=${top} agent=${view.agent} onAgent=${a => go({agent: a}, true)}
      onPick=${c => go({campaign: c, run: "", agent: "", file: "", tab: view.tab}, true)} />
    ${view.campaign ? html`<${Campaign} key=${view.campaign} view=${view} go=${go} chat=${chat} top=${top} />` : html`<${LabPage} go=${go} chat=${chat} />`}
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
