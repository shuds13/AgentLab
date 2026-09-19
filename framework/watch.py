#!/usr/bin/env python3
"""
Watch a running campaign in a browser: the agent's log as it is written, and the files
it is writing.

A run is long and quiet -- minutes can pass inside one turn with nothing printed -- and
whoever started it usually has no terminal attached to it. This serves the same files
they would otherwise `tail`, on localhost, read-only, so a run can be followed without
touching it.

Usage:
    python watch.py <campaign> [--port 8765] [--no-open]

It reads the campaign's workspace and serves what it finds. The campaign named on the
command line is the one it opens on; the page can switch to any other campaign in the
lab, since one watcher can serve them all. The one thing it writes is a message you
type: that goes to the board the agent reads between turns. Stop it with Ctrl-C; the
run is unaffected either way.
"""

import glob
import html
import http.server
import json
import os
import re
import threading
import time
import sys
import urllib.parse
import webbrowser
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
# Files worth opening while a run is in flight. Anything else in the workspace is
# listed but not offered as a tab: run directories, caches, figures.
READABLE = ("LOGBOOK.md", "JOURNAL.md", "REVIEWS.md", "results.jsonl",
            "ANNOUNCEMENTS.md", "jobs.jsonl")
TAIL_BYTES = 400_000        # of a file view; the log is followed from an offset instead

try:                        # in requirements.txt; without it records are plain text
    import markdown as _markdown
except Exception:
    _markdown = None


def workspace(campaign):
    return os.path.join(LAB_DIR, "workspace", campaign)


def campaigns():
    """Every campaign this lab has a workspace for, for the page to choose between.

    A campaign workspace is one holding a campaign's own records or its runs. The lab
    keeps its own directories under workspace/ too -- run/, logs/, belonging to the
    services rather than to any campaign -- and those are not offered.

    This is also the set a request is checked against: the name it asks for becomes a
    path, so only a name from here is allowed to."""
    root = os.path.join(LAB_DIR, "workspace")
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        ws = os.path.join(root, name)
        if not os.path.isdir(ws):
            continue
        if os.path.isdir(os.path.join(ws, "runs")) or any(
                os.path.isfile(os.path.join(ws, f)) for f in READABLE):
            found.append(name)
    return sorted(found)


def newest_run(campaign):
    """The run to show: one that is still beating, else the most recent.

    Not simply the newest meta.json -- a live run writes its meta once at startup and
    a finished one writes its own at exit, so a run that ended later looks newer than
    a run still going, and the view would stick to the finished one."""
    metas = glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json"))
    if not metas:
        return None
    live = [m for m in metas
            if os.path.isfile(os.path.join(os.path.dirname(m), "heartbeat"))]
    return os.path.dirname(max(live or metas, key=os.path.getmtime))


def _count_lines(path):
    try:
        with open(path, errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _submits(path, run_id):
    """Jobs fired, in total and by this run, and how many of this run's have come back.
    This run's counts are split by where the work ran. The log spans every run of the
    campaign, so a budget only means something against the run's own count."""
    total = 0
    this_run = {"remote": 0, "local": 0}
    done = {"remote": 0, "local": 0}
    # Per bucket, for a campaign whose jobs come in more than one resource shape.
    buckets = {}
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                event = str(row.get("event", ""))
                mine = bool(run_id) and row.get("run") == run_id
                # Remote and local jobs are logged to the same file and are different
                # work: one went to a compute system, the other ran here.
                where = "local" if event.startswith("local_") else "remote"
                if event.endswith("completed"):
                    done[where] += mine
                    continue
                if not event.endswith("submit"):
                    continue
                total += 1
                this_run[where] += mine
                b = row.get("bucket")
                if mine and where == "remote" and b:
                    buckets[b] = buckets.get(b, 0) + 1
    except OSError:
        pass
    return total, this_run, done, buckets


def status(campaign):
    """Where this run has got to, in terms any campaign has: work done against the
    budgets it stops at, and whether it is still going."""
    run_dir = newest_run(campaign)
    if not run_dir:
        return {"run": None}
    try:
        with open(os.path.join(run_dir, "meta.json")) as f:
            meta = json.load(f)
    except Exception:
        return {"run": None}
    ws = workspace(campaign)
    beat, age = os.path.join(run_dir, "heartbeat"), None
    if os.path.isfile(beat):
        try:
            with open(beat) as f:
                age = int(time.time() - float(f.read().strip()))
        except Exception:
            age = None
    submits_total, submits_run, done_run, submits_bucket = _submits(
        os.path.join(ws, "jobs.jsonl"), meta.get("run_id"))
    ran_here = sum(submits_run.values())
    # How long the run took, not how long ago it began: once it has ended, the clock
    # stops where it stopped.
    phase, phase_age = None, None
    try:
        with open(os.path.join(run_dir, "phase")) as f:
            stamp, phase = f.read().split("\n", 1)
            phase, phase_age = phase.strip(), int(time.time() - float(stamp))
    except Exception:
        pass
    started, ended = meta.get("started_at"), meta.get("ended_at")
    elapsed = None
    if started:
        try:
            end = datetime.fromisoformat(ended).timestamp() if ended else time.time()
            elapsed = int(end - datetime.fromisoformat(started).timestamp())
        except Exception:
            elapsed = None
    return {
        "run": meta.get("run_id"), "handle": meta.get("handle"),
        "campaign": campaign, "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason"), "model": meta.get("model"),
        "critic": meta.get("critic"), "host": meta.get("host"),
        "system": meta.get("system"),
        "session_id": meta.get("session_id"), "session_cwd": meta.get("session_cwd"),
        "context_tokens": meta.get("context_tokens"),
        "context_window": meta.get("context_window"),
        "context_pct": meta.get("context_pct"),
        "started_at": started, "ended_at": meta.get("ended_at"),
        "elapsed_s": elapsed, "heartbeat_age_s": age,
        "phase": phase, "phase_age_s": phase_age,
        "results": _count_lines(os.path.join(ws, "results.jsonl")),
        "jobs": submits_total, "jobs_run": ran_here,
        "jobs_bucket": submits_bucket,
        "buckets": meta.get("buckets") or {},
        "default_bucket": meta.get("default_bucket"),
        "jobs_done": sum(done_run.values()),
        "jobs_remote": submits_run["remote"], "jobs_local": submits_run["local"],
        "done_remote": done_run["remote"], "done_local": done_run["local"],
        "endpoint": meta.get("endpoint"), "has_local": meta.get("has_local"),
        "reviews": _count_lines(os.path.join(ws, "REVIEWS.md")) and
                   open(os.path.join(ws, "REVIEWS.md"), errors="replace").read().count("\n## "),
        "max_submits": meta.get("max_submits"),
        "max_runtime_s": meta.get("max_runtime_s"),
        "max_turns": meta.get("max_turns"),
    }


def newest_log(campaign):
    logs = glob.glob(os.path.join(workspace(campaign), "logs", "run_*.log"))
    return max(logs, key=os.path.getmtime) if logs else None


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>%(campaign)s</title>
<style>
 html,body { height:100%%; margin:0; }
 /* The frame the run sits in: the header's blue carried round the page. */
 body { display:flex; flex-direction:column;
        font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;
        background:#111; color:#ddd;
        border:2px solid #3a4a5a; box-sizing:border-box; }
 header { padding:8px 12px; background:#1b2836; border-bottom:2px solid #3a4a5a;
          display:flex; gap:16px; align-items:baseline; flex:none; color:#cfe0f0; }
 header b { color:#fff; font-size:14px; }
 header .sep { color:#555; }
 header #head { margin-left:auto; }
 header span { color:#888; }
 /* The campaign being watched, and the way to watch another. Styled as the heading it
    replaces, so the header still reads as a name rather than as a form. */
 header select { background:#1b2836; color:#fff; font:inherit; font-size:14px;
                 font-weight:bold; border:1px solid #3a4a5a; padding:1px 4px;
                 cursor:pointer; }
 header select:hover { border-color:#6a8aaa; }
 #tabs { display:flex; gap:4px; padding:6px 12px; background:#161616;
         border-bottom:1px solid #333; flex-wrap:wrap; flex:none; }
 #tabs button { background:#222; color:#bbb; border:1px solid #333; padding:3px 10px;
                cursor:pointer; font:inherit; }
 #tabs button.on { background:#2d4a2d; color:#fff; }
 #tabs .div { align-self:stretch; border-left:1px solid #3a4a5a; margin:0 8px; }
 /* The pane scrolls, not the page, so the tabs stay put wherever you are in a file. */
 #pane { flex:1; overflow:auto; position:relative; }
 pre { margin:0; padding:12px; white-space:pre-wrap; word-break:break-word; }
 .doc { padding:12px 16px; white-space:normal; max-width:60em; }
 .doc h1,.doc h2,.doc h3 { color:#fff; margin:1.2em 0 .4em; line-height:1.3; }
 .doc h1 { font-size:19px; } .doc h2 { font-size:16px; } .doc h3 { font-size:14px; }
 .doc code { background:#1d1d1d; padding:1px 4px; }
 .doc pre { background:#1a1a1a; padding:10px; overflow:auto; }
 .doc table { border-collapse:collapse; margin:10px 0; }
 .doc th,.doc td { border:1px solid #333; padding:3px 10px; text-align:left; }
 .doc th { background:#1d1d1d; color:#eee; }
 .doc img { max-width:100%%; max-height:75vh; border:1px solid #333; background:#fff;
            display:block; margin:10px 0; cursor:zoom-in; }
 .doc blockquote { border-left:3px solid #333; margin:8px 0; padding-left:12px;
                   color:#aaa; }
 table { border-collapse:collapse; margin:12px; }
 /* The run and the campaign are different spans of time, and their counts differ. */
 tr.sec td { color:#6f8296; padding:14px 0 2px; border-bottom:1px solid #2b3946; }
 td { padding:3px 18px 3px 0; vertical-align:top; }
 td.k { color:#888; }
 .bar { display:inline-block; width:150px; height:9px; background:#222;
        border:1px solid #333; vertical-align:middle; margin-right:8px; }
 .bar i { display:block; height:100%%; background:#3d7a3d; }
 /* Clear of the chat panel, whichever of its height and its floor is in force. */
 #newest { position:fixed; right:18px;
           bottom:calc(max(var(--chat-h, 30vh), 120px) + 14px); display:none;
           background:#2d4a2d; color:#fff; border:1px solid #4a7a4a; padding:5px 12px;
           cursor:pointer; font:inherit; }
 /* The conversation with the run: what has been said, and where you say it. One
    section, because an input detached from its transcript reads as a search box. */
 /* --chat-h is set when the CHAT bar is dragged; anything sitting above the chat
    reads it too, so it moves with the divider. */
 #chat { flex:none; height:var(--chat-h, 30vh); min-height:120px;
         display:flex; flex-direction:column;
         background:#0b0f14; border-top:2px solid #3a4a5a; }
 #chathead { flex:none; padding:4px 12px; background:#1b2836; color:#cfe0f0;
             border-bottom:1px solid #24313d; letter-spacing:.08em;
             cursor:ns-resize; user-select:none; }
 #chatlog { flex:1; overflow:auto; padding:8px 12px; }
 #chatlog .m { margin:0 0 7px; }
 #chatlog .t { color:#666; margin-right:8px; }
 #chatlog .who { color:#7aa87a; margin-right:6px; }
 #chatlog .who.you { color:#7a9ac8; }
 #chatlog .none { color:#666; }
 #say { display:flex; gap:6px; padding:8px 12px; background:#101720; flex:none;
        border-top:1px solid #24313d; }
 #say input { flex:1; background:#0b0f14; color:#ddd; border:1px solid #2b3946;
              padding:5px 8px; font:inherit; }
 #say button { background:#222; color:#bbb; border:1px solid #333; padding:5px 14px;
               cursor:pointer; font:inherit; }
 button.copy { background:#222; color:#999; border:1px solid #333; padding:0 6px;
               margin-left:8px; cursor:pointer; font:inherit; font-size:11px; }
 button.copy:hover { color:#fff; }
</style></head><body>
<header><b>AgentLab</b><span class="sep">/</span>\
<select id="camp"><option>%(campaign)s</option></select>\
<span id="head">connecting\u2026</span></header>
<div id="tabs"></div>
<div id="pane"><pre id="view">loading\u2026</pre></div>
<button id="newest">\u2193 newest</button>
<div id="chat">
  <div id="chathead">CHAT</div>
  <div id="chatlog" class="none">no messages yet</div>
  <div id="say"><input id="msg" autocomplete="off"
    placeholder="message the agent \u2014 it reads between turns"><button>send</button></div>
</div>
<script>
let tab = "status", offset = 0, logText = "", rawMode = false;
// Which campaign the page is showing. Every request says so, and the address bar
// carries it, so a reload or a bookmark comes back to the same one. The server has
// already checked the name it served this page with, so that is what it opens on.
const HOME = %(campaign_json)s;
let campaign = HOME;
const url = p => p + (p.includes("?") ? "&" : "?") + "c=" + encodeURIComponent(campaign);
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
  const key = JSON.stringify(files) + tab + rawMode;
  if (t.dataset.key === key) return;
  t.dataset.key = key;
  t.innerHTML = "";
  for (const n of ["status", "log"].concat(files)) {
    // The first two are this run; the files after them are the campaign's records,
    // written by every run of it. Marked off, because the counts differ for the same
    // reason.
    if (n === files[0]) {
      const d = document.createElement("span");
      d.className = "div";
      t.appendChild(d);
    }
    const b = document.createElement("button");
    // The log is what the agent said and did; the file name is not the point.
    b.textContent = n === "log" ? "agent log" : n;
    b.className = n === tab ? "on" : "";
    b.onclick = () => {
      // Every tab starts clean: the log re-reads from the beginning, and the styling
      // of a rendered record does not follow you to the next tab.
      tab = n; offset = 0; logText = "";
      view.innerHTML = ""; view.className = ""; view.dataset.body = "";
      delete view.dataset.statusSig;
      pane.scrollTop = 0; newest.style.display = "none";
      setTabs(files); refresh();
    };
    t.appendChild(b);
  }
  if (tab.endsWith(".md")) {
    const r = document.createElement("button");
    r.textContent = rawMode ? "rendered" : "raw";
    r.style.marginLeft = "auto";
    r.onclick = () => { rawMode = !rawMode; view.dataset.body = ""; setTabs(files); refresh(); };
    t.appendChild(r);
  }
}

const short = n => n == null ? "?"
  : n >= 1e6 ? (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\\.0$/, "") + "m"
  : n >= 1e3 ? Math.round(n / 1e3) + "k" : String(n);

const hms = s => s == null ? "\u2014" :
  (s >= 3600 ? Math.floor(s/3600) + "h " : "") +
  (s >= 60 ? Math.floor(s%%3600/60) + "m " : "") + (s%%60) + "s";

// A budget is a ceiling, not a target: an agent that has answered its question stops
// early, so the bar shows how much of the allowance is used, not progress towards it.
function bar(done, total, fmt) {
  fmt = fmt || String;
  if (!total) return fmt(done);
  const pct = Math.min(100, Math.round(100 * done / total));
  return `<span class="bar"><i style="width:${pct}%%"></i></span>` +
         `${fmt(done)} of ${fmt(total)} (max)`;
}

function renderStatus(s) {
  if (!s.run) { view.textContent = "no run yet"; return; }
  const shapes = Object.entries(s.buckets || {});
  const rows = [
    ["run", `${s.handle || "\u2014"} \u00b7 ${s.run}`],
    ["state", s.status === "running"
        ? `running \u00b7 heartbeat ${hms(s.heartbeat_age_s)} ago`
        : `${s.status} \u00b7 ${s.stop_reason || ""}`],
    ["doing", s.status === "running" && s.phase
        ? `${s.phase} \u00b7 ${hms(s.phase_age_s)}` : "\u2014"],
    // Where the work ran, next to the counts of it. A campaign whose task defines both
    // kinds of job splits the counts, since they did not run in the same place.
    ["jobs run on", (s.endpoint
        ? `${s.system || "?"} (${s.endpoint})` + (s.has_local ? ", this machine" : "")
        : "this machine")
        // The shapes a job can be given, when there is a choice of them.
        + (shapes.length > 1
           ? `<br><span style="color:#777">` + shapes.map(([k, b]) =>
               `${k}: ${b.num_nodes} node${b.num_nodes === 1 ? "" : "s"}` +
               (b.queue ? ` on ${b.queue}` : "") + (b.walltime ? `, ${b.walltime}` : "") +
               (b.max_concurrent ? `, max ${b.max_concurrent}` : "") +
               (k === s.default_bucket ? " (default)" : "")).join("<br>") + `</span>` : "")],
    ["jobs submitted", bar(s.jobs_run, s.max_submits)
        // Every configured shape, so one that has taken no work yet still shows as zero.
        + (shapes.length > 1
           ? ` <span style="color:#777">(` + shapes.map(([k]) =>
               `${(s.jobs_bucket || {})[k] || 0} ${k}`).join(", ") + `)</span>` : "")
        + (s.endpoint && s.has_local
           ? ` <span style="color:#777">(${s.jobs_remote} on ${s.system || "endpoint"}, `
             + `${s.jobs_local} here)</span>` : "")],
    ["still running", `${s.jobs_run - s.jobs_done} of ${s.jobs_run} submitted, `
        + `${s.jobs_done} returned`],
    ["elapsed", bar(s.elapsed_s, s.max_runtime_s, hms)],
    ["model", `${s.model || "\u2014"} \u00b7 context ` + (s.context_tokens == null
        ? "no data"
        : s.context_pct == null
        ? `${short(s.context_tokens)} (window not known yet)`
        : `${short(s.context_tokens)}/${short(s.context_window)} (${Math.round(s.context_pct)}%%)`)],
    ["critic", s.critic || "\u2014"],
    // The run's Claude session, to reopen afterwards with `claude -r`. Copying is
    // offered once the run has stopped: opening a session the runner still holds puts
    // a second client on it.
    ["session", !s.session_id ? "\u2014"
        : s.status === "running"
        ? `<span style="color:#666">${s.session_id}</span>`
        : `${s.session_id} <button class="copy" data-copy="${s.session_id}">copy</button>`],
    ["host", s.host || "\u2014"],
    ["started", s.started_at || "\u2014"],
  ];
  if (s.ended_at) rows.push(["ended", s.ended_at]);
  // The campaign outlives the run: these count every run of it, which is why they can
  // be larger than the numbers above.
  rows.push(["campaign totals"],
            ["jobs submitted", String(s.jobs)],
            ["results recorded", String(s.results)],
            ["critic reviews", String(s.reviews || 0)]);
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
  return esc(text).replace(/!\\[([^\\]]*)\\]\\(([^)\\s]+)\\)/g, (m, alt, src) => {
    if (/^https?:/.test(src)) return m;
    // Into an attribute, so the query separator is escaped along with the rest.
    const u = esc(url("/image?name=" + encodeURIComponent(src)));
    // Sized to sit inside the text rather than replace it; click for the full thing.
    return `<a href="${u}" target="_blank" ` +
        `title="${src} \u2014 click to open full size">` +
        `<img src="${u}" alt="${alt}" ` +
        `style="max-width:100%%;max-height:75vh;display:block;margin:8px 0;` +
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

async function refresh() {
  // The campaign this pass is about. A reply that arrives after the page has been
  // switched belongs to the campaign that asked for it, not the one now showing, so
  // it is dropped rather than drawn.
  const mine = campaign;
  try {
    const s = await (await fetch(url("/status"))).json();
    if (mine !== campaign) return;
    document.getElementById("head").textContent = s.run
      ? (s.status === "running" ? "running \u00b7 " : s.status + " \u00b7 ") + (s.handle || s.run)
      : "no run yet";
    if (tab === "status") { view.className = ""; renderStatus(s); return; }
    if (tab === "log") {
      view.className = "";
      const stick = atBottom();
      const r = await fetch(url(`/log?from=${offset}`));
      const j = await r.json();
      if (mine !== campaign) return;
      if (j.reset) { logText = ""; offset = 0; }      // a new run: start the pane again
      if (j.text) { logText += j.text; view.textContent = logText; }
      offset = j.offset;
      if (stick) { pane.scrollTop = pane.scrollHeight; newest.style.display = "none"; }
      else if (j.text) newest.style.display = "block";
    } else {
      if (selecting()) return;
      // The board is a list of lines, not a document: rendered as Markdown, consecutive
      // messages run together into one paragraph.
      const md = tab.endsWith(".md") && !rawMode && tab !== "ANNOUNCEMENTS.md";
      const body = await (await fetch(url(
        "/file?name=" + encodeURIComponent(tab) + (md ? "" : "&raw=1")))).text();
      if (mine !== campaign) return;
      if (body !== view.dataset.body) {                // keep where you were reading
        const at = pane.scrollTop;
        view.dataset.body = body;
        view.className = md ? "doc" : "";
        view.innerHTML = md ? body : withFigures(body);
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
  const mine = campaign;
  let body;
  try { body = await (await fetch(url("/messages"))).text(); }
  catch (e) { return; }
  if (mine !== campaign) return;
  if (body === chatText) return;
  chatText = body;
  const msgs = body.split(/\\n\\s*\\n/).map(m => m.trim()).filter(Boolean);
  if (!msgs.length) { chatlog.className = "none";
                      chatlog.textContent = "no messages yet"; return; }
  const near = chatlog.scrollTop + chatlog.clientHeight > chatlog.scrollHeight - 40;
  chatlog.className = "";
  chatlog.innerHTML = msgs.map(m => {
    const p = m.match(/^`(\\d\\d:\\d\\d)`\\s+\\*\\*(\\w+)\\*\\*\\s+([\\s\\S]*)$/);
    if (!p) return `<div class="m">${esc(m)}</div>`;
    const you = p[2] === "you" ? " you" : "";
    return `<div class="m"><span class="t">${p[1]}</span>` +
           `<span class="who${you}">${p[2]}</span>${esc(p[3])}</div>`;
  }).join("");
  if (near) chatlog.scrollTop = chatlog.scrollHeight;
}

document.addEventListener("click", e => {
  const b = e.target.closest("button.copy");
  if (!b) return;
  navigator.clipboard.writeText(b.dataset.copy).then(() => {
    b.textContent = "copied";
    setTimeout(() => { b.textContent = "copy"; }, 1500);
  }, () => { b.textContent = "no clipboard"; });
});

async function files() {
  const mine = campaign;
  try {
    const list = await (await fetch(url("/files"))).json();
    if (mine === campaign) setTabs(list);
  } catch (e) {}
}
// A message goes to the board the agent reads between turns, so it lands after the
// turn in flight rather than interrupting it.
async function say() {
  const box = document.getElementById("msg"), text = box.value.trim();
  if (!text) return;
  box.value = "";
  try { await fetch(url("/say"), {method: "POST", body: text}); } catch (e) {}
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
    from = e.clientY; start = chat.offsetHeight;
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    e.preventDefault();
  });
})();

// One watcher serves every campaign in the lab, so switching is a change of what the
// page asks for rather than another process on another port.
const camp = document.getElementById("camp");

// Where a message would land, said next to the box you type it in: the chat follows
// the campaign, and a line meant for one agent should not reach another unnoticed.
function showCampaign() {
  document.title = campaign;
  document.getElementById("chathead").textContent = "CHAT · " + campaign;
}

async function campaignList() {
  let names;
  try { names = await (await fetch("/campaigns")).json(); } catch (e) { return; }
  if (!names.length) return;
  // The campaign being shown has gone from the lab -- its workspace removed while the
  // page was open. The server is answering for the one it started on, so show that.
  if (!names.includes(campaign)) {
    campaign = HOME;
    history.replaceState(null, "", location.pathname);
    showCampaign();
  }
  const key = names.join("|");
  if (camp.dataset.key !== key) {
    camp.dataset.key = key;
    camp.innerHTML = names.map(n => `<option>${esc(n)}</option>`).join("");
  }
  camp.value = campaign;
}

camp.onchange = () => {
  campaign = camp.value;
  history.replaceState(null, "", "?c=" + encodeURIComponent(campaign));
  showCampaign();
  // Nothing carries over: the log, the file being read and the conversation all belong
  // to the campaign that was showing. Same reset as changing tab, one level up.
  tab = "status"; offset = 0; logText = ""; chatText = "";
  view.innerHTML = ""; view.className = ""; view.dataset.body = "";
  delete view.dataset.statusSig;
  document.getElementById("tabs").dataset.key = "";
  pane.scrollTop = 0; newest.style.display = "none";
  chatlog.className = "none"; chatlog.textContent = "no messages yet";
  document.getElementById("head").textContent = "connecting…";
  files(); refresh(); chat();
};

document.querySelector("#say button").onclick = say;
document.getElementById("msg").addEventListener(
  "keydown", e => { if (e.key === "Enter") say(); });

showCampaign();
campaignList(); files(); refresh(); chat();
setInterval(refresh, 1500);
setInterval(files, 10000);
// A campaign that starts while the page is open should appear in the list.
setInterval(campaignList, 10000);
setInterval(chat, 2000);
</script></body></html>
"""


def _render(text, campaign):
    """A record as its author meant it to read -- headings, tables, figures. Falls back
    to the text itself if anything goes wrong: a viewer that shows nothing is worse than
    one that shows the file."""
    try:
        html_out = _markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        return "<pre>" + html.escape(text) + "</pre>"
    # Figures are referenced relative to the workspace, which only this server can read,
    # and the workspace is the campaign's -- so the route is told which one. These go
    # into attributes of the HTML below, so the separator is written as an entity.
    owner = "&amp;c=" + urllib.parse.quote(campaign)

    def _img(m):
        src = urllib.parse.quote(m.group("src"))
        alt = m.group(0)
        alt = re.search(r'alt="([^"]*)"', alt)
        return (f'<a href="/image?name={src}{owner}" target="_blank">'
                f'<img src="/image?name={src}{owner}" alt="{alt.group(1) if alt else ""}"></a>')

    html_out = re.sub(r'<img[^>]*?src="(?!https?:|/)(?P<src>[^"]+)"[^>]*/?>', _img, html_out)

    # A record may link a figure rather than embed it. The link is relative to the
    # workspace, which only this server can read, so point it at the same route.
    return re.sub(
        r'<a href="(?!https?:|/)(?P<href>[^"]+\.(?:png|jpg|jpeg|gif|svg|webp))"',
        lambda m: '<a target="_blank" href="/image?name='
                  f'{urllib.parse.quote(m.group("href"))}{owner}"',
        html_out, flags=re.I)


class Handler(http.server.BaseHTTPRequestHandler):
    campaign = ""           # the one it was started on: where the page opens, and the
                            # campaign a request that names none is about

    last_request = 0.0      # for --exit-when-idle: a page open polls constantly

    def log_message(self, *a):
        pass            # a watcher that narrates its own requests is noise

    def _campaign(self, q):
        """The campaign a request is about. The page names one; the name becomes a path,
        so only a campaign this lab has may be named."""
        name = (q.get("c") or [""])[0]
        return name if name in self._choices() else self.campaign

    def _choices(self):
        """What the page may switch between. The campaign this was started on is always
        among them, whether or not its workspace has anything in it yet."""
        names = campaigns()
        if self.campaign and self.campaign not in names:
            names = sorted(names + [self.campaign])
        return names

    def handle_one_request(self):
        type(self).last_request = time.time()
        super().handle_one_request()

    def _send(self, body, ctype="text/plain; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        """The one write: a message for the agent. It lands on the board the agent reads
        between turns, and in the conversation the page shows."""
        url = urllib.parse.urlparse(self.path)
        if url.path != "/say":
            self.send_error(404)
            return
        campaign = self._campaign(urllib.parse.parse_qs(url.query))
        length = int(self.headers.get("Content-Length") or 0)
        text = self.rfile.read(length).decode("utf-8", "replace").strip()
        if not text:
            self._send(json.dumps({"sent": False}), "application/json")
            return
        ws = workspace(campaign)
        stamp = datetime.now().strftime("%H:%M")
        try:
            os.makedirs(ws, exist_ok=True)
            with open(os.path.join(ws, "ANNOUNCEMENTS.md"), "a") as f:
                f.write(text.replace("\n", " ") + "\n")
            with open(os.path.join(ws, "MESSAGES.md"), "a") as f:
                f.write(f"`{stamp}` **you** {text}\n\n")
        except OSError as e:
            self._send(json.dumps({"sent": False, "error": str(e)}), "application/json")
            return
        self._send(json.dumps({"sent": True}), "application/json")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        campaign = self._campaign(q)
        if url.path == "/":
            # The page opens on the campaign asked for, so a bookmarked one is showing
            # before the first poll rather than a moment after it.
            self._send(PAGE % {"campaign": html.escape(campaign),
                               "campaign_json": json.dumps(campaign)},
                       "text/html; charset=utf-8")
        elif url.path == "/campaigns":
            self._send(json.dumps(self._choices()), "application/json")
        elif url.path == "/log":
            self._send(json.dumps(
                self._log_from(campaign, int(q.get("from", ["0"])[0]))),
                "application/json")
        elif url.path == "/status":
            self._send(json.dumps(status(campaign)), "application/json")
        elif url.path == "/files":
            ws = workspace(campaign)
            self._send(json.dumps([f for f in READABLE
                                   if os.path.isfile(os.path.join(ws, f))]),
                       "application/json")
        elif url.path == "/messages":
            # The conversation, not a file tab: served whether or not it exists yet.
            try:
                with open(os.path.join(workspace(campaign), "MESSAGES.md"),
                          errors="replace") as f:
                    self._send(f.read())
            except OSError:
                self._send("")
        elif url.path == "/image":
            self._send_image(campaign, q.get("name", [""])[0])
        elif url.path == "/file":
            name = q.get("name", [""])[0]
            text = self._file(campaign, name)
            if q.get("raw") or not name.endswith(".md") or _markdown is None:
                self._send(text)
            else:
                self._send(_render(text, campaign), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    # Per campaign: one watcher serves them all, and a page on one must not reset a
    # page on another.
    _serving = {}              # campaign -> the log file it is currently being fed

    def _log_from(self, campaign, offset):
        path = newest_log(campaign)
        if not path:
            return {"text": "", "offset": 0, "name": "no run log yet", "running": False}
        size = os.path.getsize(path)
        # Start the pane again when the file under it changes -- a new run writes a new
        # log, and a truncated one is no longer what we were reading. Either way,
        # splicing two logs together would be a lie.
        cls = type(self)
        was = cls._serving.get(campaign)
        reset = offset > size or (was is not None and was != path)
        cls._serving[campaign] = path
        if reset:
            offset = 0
        with open(path, errors="replace") as f:
            f.seek(offset)
            text = f.read()
        run_dir = os.path.join(workspace(campaign), "runs")
        beating = glob.glob(os.path.join(run_dir, "*", "heartbeat"))
        return {"text": text, "offset": size, "reset": reset,
                "name": os.path.basename(path), "running": bool(beating)}

    IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}

    def _send_image(self, campaign, name):
        """Serve a figure the records point at. Confined to the campaign's workspace:
        the name is resolved and checked to be inside it, so a path from a file cannot
        reach out of it."""
        ws = os.path.realpath(workspace(campaign))
        path = os.path.realpath(os.path.join(ws, name))
        ext = os.path.splitext(path)[1].lower()
        if not path.startswith(ws + os.sep) or ext not in self.IMAGE_TYPES:
            self.send_error(404)
            return
        try:
            with open(path, "rb") as f:
                self._send(f.read(), self.IMAGE_TYPES[ext])
        except OSError:
            self.send_error(404)

    def _file(self, campaign, name):
        if name not in READABLE:
            return "not a file this watcher serves"
        path = os.path.join(workspace(campaign), name)
        try:
            size = os.path.getsize(path)
            with open(path, errors="replace") as f:
                if size > TAIL_BYTES:
                    f.seek(size - TAIL_BYTES)
                    return f"[showing the last {TAIL_BYTES // 1000} KB of {size // 1000} KB]\n\n" + f.read()
                return f.read()
        except OSError as e:
            return f"cannot read {name}: {e}"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        sys.exit("usage: python watch.py <campaign> [--port N] [--no-open]")
    campaign = args[0]
    port = 8765
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    ws = workspace(campaign)
    if not os.path.isdir(ws):
        sys.exit(f"no workspace at {ws} -- has this campaign run?")

    Handler.campaign = campaign
    # Another campaign may already be watched here, so take the next free port rather
    # than dying on the one that was asked for.
    for candidate in range(port, port + 20):
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    else:
        sys.exit(f"no free port between {port} and {port + 19}")
    url = f"http://127.0.0.1:{port}/"
    print(f"watching {campaign} at {url}  (Ctrl-C to stop; the run is unaffected)",
          flush=True)
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    # An open page polls every second or so, so a long silence means nobody is looking.
    # That, rather than the end of the run, is when this has nothing left to serve.
    idle = 0
    for a in sys.argv:
        if a.startswith("--exit-when-idle="):
            idle = int(a.split("=", 1)[1])
    if idle:
        Handler.last_request = time.time()

        def _reap():
            while time.time() - Handler.last_request < idle:
                time.sleep(5)
            span = f"{idle // 60} minutes" if idle >= 120 else f"{idle} seconds"
            print(f"nobody has looked for {span}; stopping.", flush=True)
            server.shutdown()

        threading.Thread(target=_reap, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nwatcher stopped.", flush=True)


if __name__ == "__main__":
    main()
