// The campaign's files: its records, the chosen agent's own records and the files its
// run was handed, its pictures, and every file under the workspace and the campaign
// directory. A list on the left, the file on the right.
import { html, useState, useEffect, useRef, usePoll, getJSON, when } from "./util.js";

const ROOTS = {ws: "workspace", camp: "campaign"};
const isImage = n => /\.(png|jpe?g|gif|svg|webp)$/i.test(n);
const sizeof = n => n < 1000 ? n + " B" : n < 1e6 ? (n / 1e3).toFixed(1) + " KB" : (n / 1e6).toFixed(1) + " MB";
const stamp = t => when(new Date(t * 1000).toISOString());
const imageUrl = (campaign, root, name) => "/image?" + new URLSearchParams({c: campaign, root, name});

// Where a file is read from, by the kind of entry it is in the list.
function source(sel, campaign, run) {
  const [kind, name] = [sel.slice(0, sel.indexOf(":")), sel.slice(sel.indexOf(":") + 1)];
  const q = {c: campaign};
  if (kind === "rec") return {name, path: "/file", q: {...q, name}};
  if (kind === "agent") return {name, path: "/agentfile", q: {...q, name, ...(run ? {run} : {})}};
  if (kind === "input") return {name, path: "/runfile", q: {...q, name, ...(run ? {run} : {})}};
  return {name, root: kind, path: "/wsfile", q: {...q, root: kind, path: name}};
}

// A text file that references a figure shows it; everything else stays as written.
function WithFigures({text, campaign}) {
  const parts = [];
  const re = /!\[([^\]]*)\]\(([^)\s]+)\)/g;
  let at = 0, m;
  while ((m = re.exec(text))) {
    if (/^https?:/.test(m[2])) continue;
    parts.push(text.slice(at, m.index));
    const u = imageUrl(campaign, "ws", m[2]);
    parts.push(html`<a href=${u} target="_blank" title=${m[2]}><img class="inlinefig" src=${u} alt=${m[1]} /></a>`);
    at = m.index + m[0].length;
  }
  parts.push(text.slice(at));
  return html`<pre class="filetext">${parts}</pre>`;
}

// A redraw replaces the nodes a selection sits in, so a file is not refreshed while
// text in it is selected.
const selectingIn = el => {
  const s = getSelection();
  return !!el && !!s && !s.isCollapsed && s.rangeCount > 0 && el.contains(s.getRangeAt(0).commonAncestorContainer);
};

function Viewer({sel, campaign, run, onBack}) {
  const src = source(sel, campaign, run);
  const md = src.name.endsWith(".md");
  const [raw, setRaw] = useState(false);
  const [body, setBody] = useState(null);
  const box = useRef(null);
  const image = !!src.root && isImage(src.name);
  useEffect(() => {
    if (image) return;
    let live = true, timer;
    const tick = async () => {
      if (!selectingIn(box.current)) {
        try {
          const params = new URLSearchParams({...src.q, ...(md && !raw ? {} : {raw: "1"})});
          const r = await fetch(src.path + "?" + params);
          const text = await r.text();
          if (live) setBody(text);
        } catch (e) {}
      }
      if (live) timer = setTimeout(tick, 3000);
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [sel, campaign, run, raw]);

  return html`<div class="viewer">
    <div class="viewhead">
      ${src.root && html`<button class="btn sm" onClick=${onBack}>← All files</button>
        <span class="faint">${ROOTS[src.root]} /</span>`}
      <b class="mono">${src.name}</b>
      <span class="spacer" style="flex:1"></span>
      ${md && !image && html`<div class="seg">
        <button class=${raw ? "" : "on"} onClick=${() => setRaw(false)}>Rendered</button>
        <button class=${raw ? "on" : ""} onClick=${() => setRaw(true)}>Raw</button></div>`}
    </div>
    <div class="viewbody" ref=${box}>
      ${image ? html`<a href=${imageUrl(campaign, src.root, src.name)} target="_blank">
            <img class="fullfig" src=${imageUrl(campaign, src.root, src.name)} /></a>`
        : body == null ? html`<div class="empty">Loading…</div>`
        : md && !raw ? html`<article class="doc" dangerouslySetInnerHTML=${{__html: body}}></article>`
        : html`<${WithFigures} text=${body} campaign=${campaign} />`}
    </div>
  </div>`;
}

// Every picture the run's jobs returned and its agents drew, newest first. One opened
// fills the page; the arrow keys go to the next and the one before, Escape closes it.
function Pictures({campaign}) {
  const {data} = usePoll(() => getJSON("/pictures", {c: campaign}), 10000, [campaign], {keep: true});
  const [open, setOpen] = useState(-1);
  const list = data || [];
  useEffect(() => {
    if (open < 0) return;
    const key = e => {
      if (e.key === "Escape") setOpen(-1);
      else if (e.key === "ArrowRight") setOpen(i => Math.min(i + 1, list.length - 1));
      else if (e.key === "ArrowLeft") setOpen(i => Math.max(i - 1, 0));
    };
    addEventListener("keydown", key);
    return () => removeEventListener("keydown", key);
  }, [open, list.length]);
  if (!data) return html`<div class="empty">Loading…</div>`;
  if (!list.length) return html`<div class="empty">No pictures yet.</div>`;
  const p = list[open];
  return html`<div class="viewbody">
    <div class="pics">${list.map((p, i) => html`<figure onClick=${() => setOpen(i)} title=${p.caption}>
      <img loading="lazy" src=${imageUrl(campaign, "ws", p.path)} /><figcaption>${p.caption}</figcaption></figure>`)}</div>
    ${p && html`<div class="lightbox" onClick=${e => e.target === e.currentTarget && setOpen(-1)}>
      <img src=${imageUrl(campaign, "ws", p.path)} />
      <div class="lbcap">${open + 1} of ${list.length} · ${p.caption}<br /><span class="faint">← → to move, Esc to close</span></div>
    </div>`}
  </div>`;
}

// Directories first, as in any file listing; under size they keep A-to-Z.
const SORT_DESC = {name: false, size: true, date: true};
function rowsOf(d, sort) {
  const sign = sort.desc ? -1 : 1;
  const byName = (a, b) => a.name.toLowerCase() < b.name.toLowerCase() ? -1 : 1;
  const by = sort.key === "date" ? (a, b) => sign * (a.mtime - b.mtime)
    : sort.key === "size" ? (a, b) => sign * (a.size - b.size) : (a, b) => sign * byName(a, b);
  return d.dirs.map(x => ({...x, dir: true})).sort(sort.key === "size" ? byName : by)
    .concat(d.files.map(x => ({...x, dir: false})).sort(by));
}

// Every file under the campaign's roots. Only what is open is asked for.
function Tree({campaign, onOpen, open, setOpen, sort, setSort}) {
  const keys = [...new Set([...open, "ws:", "camp:"])].join(",");
  const {data} = usePoll(() => getJSON("/tree", {c: campaign, open: keys}), 5000, [campaign, keys], {keep: true});
  if (!data) return html`<div class="empty">Loading…</div>`;
  const toggle = k => setOpen(s => { const t = new Set(s); t.has(k) ? t.delete(k) : t.add(k); return t; });
  const rows = [];
  const node = (root, rel, depth) => {
    const d = data.nodes[root + ":" + rel];
    if (!d) return;
    if (d.error) { rows.push(html`<div class="trow faint" style=${`padding-left:${16 + depth * 16}px`}>${d.error}</div>`); return; }
    for (const r of rowsOf(d, sort)) {
      const sub = rel ? rel + "/" + r.name : r.name, k = root + ":" + sub;
      const pad = `padding-left:${16 + depth * 16}px`;
      if (r.dir) {
        rows.push(html`<div class="trow dir" style=${pad} onClick=${() => toggle(k)}>
          <span class="tname"><span class="caret">${open.has(k) ? "▾" : "▸"}</span>${r.name}</span>
          <span class="tsize"></span><span class="tdate">${r.mtime ? stamp(r.mtime) : ""}</span></div>`);
        if (open.has(k)) node(root, sub, depth + 1);
      } else {
        rows.push(html`<div class="trow file" style=${pad} onClick=${() => onOpen(k)}>
          <span class="tname">${r.name}</span><span class="tsize">${sizeof(r.size)}</span>
          <span class="tdate">${r.mtime ? stamp(r.mtime) : ""}</span></div>`);
      }
    }
  };
  for (const root of data.roots) {
    const k = root + ":";
    rows.push(html`<div class="trow root" onClick=${() => toggle(k)}>
      <span class="tname"><span class="caret">${open.has(k) ? "▾" : "▸"}</span>${ROOTS[root]}</span></div>`);
    if (open.has(k)) node(root, "", 1);
  }
  const col = (key, label, cls) => html`<button class=${cls} onClick=${() =>
    setSort(s => ({key, desc: s.key === key ? !s.desc : SORT_DESC[key]}))}>${label}${sort.key === key ? (sort.desc ? " ↓" : " ↑") : ""}</button>`;
  return html`<div class="viewbody tree">
    <div class="trow thead">${col("name", "Name", "tname")}${col("size", "Size", "tsize")}${col("date", "Modified", "tdate")}</div>
    ${rows}
  </div>`;
}

function NavItem({label, on, onClick, meta}) {
  return html`<div class="navitem ${on ? "on" : ""}" onClick=${onClick} title=${label}>
    <span class="name">${label}</span>${meta && html`<span class="meta">${meta}</span>`}</div>`;
}

export function Files({campaign, run, sel, onSel, agentName, records}) {
  const list = usePoll(() => getJSON("/files", {c: campaign}), 10000, [campaign], {keep: true}).data || [];
  const inputs = usePoll(() => getJSON("/runfiles", {c: campaign, run}), 15000, [campaign, run], {keep: true}).data || [];
  const recs = list.filter(n => n !== "pictures");
  const pics = list.includes("pictures");
  const chosen = sel || (recs.length ? "rec:" + recs[0] : "tree");
  // Kept here, so the tree is as you left it when you come back from a file.
  const [open, setOpen] = useState(() => new Set(["ws:"]));
  const [sort, setSort] = useState({key: "name", desc: false});
  const section = (title, items, kind) => items.length > 0 && html`
    <div class="navsec"><span>${title}</span></div>
    ${items.map(n => html`<${NavItem} label=${n} on=${chosen === kind + ":" + n} onClick=${() => onSel(kind + ":" + n)} />`)}`;
  return html`<div class="files">
    <nav class="filenav">
      ${section("Campaign records", recs, "rec")}
      ${section(`${agentName}'s records`, records || [], "agent")}
      ${section("Run inputs", inputs, "input")}
      <div class="navsec"><span>Browse</span></div>
      ${pics && html`<${NavItem} label="Pictures" on=${chosen === "pictures"} onClick=${() => onSel("pictures")} />`}
      <${NavItem} label="All files" on=${chosen === "tree" || /^(ws|camp):/.test(chosen)} onClick=${() => onSel("tree")} />
    </nav>
    <div class="filemain">
      ${chosen === "tree" ? html`<${Tree} campaign=${campaign} onOpen=${onSel} open=${open} setOpen=${setOpen} sort=${sort} setSort=${setSort} />`
        : chosen === "pictures" ? html`<${Pictures} campaign=${campaign} />`
        : html`<${Viewer} key=${chosen} sel=${chosen} campaign=${campaign} run=${run} onBack=${() => onSel("tree")} />`}
    </div>
  </div>`;
}
