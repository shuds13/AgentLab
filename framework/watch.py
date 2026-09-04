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

Nothing here writes: it reads the campaign's workspace and serves what it finds. Stop it
with Ctrl-C; the run is unaffected either way.
"""

import glob
import html
import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
# Files worth opening while a run is in flight. Anything else in the workspace is
# listed but not offered as a tab: run directories, caches, figures.
READABLE = (
    "LOGBOOK.md",
    "JOURNAL.md",
    "REVIEWS.md",
    "results.jsonl",
    "ANNOUNCEMENTS.md",
    "jobs.jsonl",
)
TAIL_BYTES = 400_000  # of a file view; the log is followed from an offset instead

try:  # in requirements.txt; without it records are plain text
    import markdown as _markdown
except Exception:
    _markdown = None


def workspace(campaign):
    return os.path.join(LAB_DIR, "workspace", campaign)


def newest_run(campaign):
    """The run to show: one that is still beating, else the most recent.

    Not simply the newest meta.json -- a live run writes its meta once at startup and
    a finished one writes its own at exit, so a run that ended later looks newer than
    a run still going, and the view would stick to the finished one."""
    metas = glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json"))
    if not metas:
        return None
    live = [
        m
        for m in metas
        if os.path.isfile(os.path.join(os.path.dirname(m), "heartbeat"))
    ]
    return os.path.dirname(max(live or metas, key=os.path.getmtime))


def _count_lines(path):
    try:
        with open(path, errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _submits(path, run_id):
    """Jobs fired, in total and by this run, and how many of this run's have come back.
    The log spans every run of the campaign, so a budget only means something against
    the run's own count."""
    total = this_run = done = 0
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                event = str(row.get("event", ""))
                mine = bool(run_id) and row.get("run") == run_id
                if event.endswith("completed"):
                    done += mine
                    continue
                if not event.endswith("submit"):
                    continue
                total += 1
                this_run += mine
    except OSError:
        pass
    return total, this_run, done


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
    submits_total, submits_run, done_run = _submits(
        os.path.join(ws, "jobs.jsonl"), meta.get("run_id")
    )
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
        "run": meta.get("run_id"),
        "handle": meta.get("handle"),
        "campaign": campaign,
        "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason"),
        "model": meta.get("model"),
        "critic": meta.get("critic"),
        "host": meta.get("host"),
        "context_tokens": meta.get("context_tokens"),
        "context_window": meta.get("context_window"),
        "context_pct": meta.get("context_pct"),
        "started_at": started,
        "ended_at": meta.get("ended_at"),
        "elapsed_s": elapsed,
        "heartbeat_age_s": age,
        "phase": phase,
        "phase_age_s": phase_age,
        "results": _count_lines(os.path.join(ws, "results.jsonl")),
        "jobs": submits_total,
        "jobs_run": submits_run,
        "jobs_done": done_run,
        "reviews": _count_lines(os.path.join(ws, "REVIEWS.md"))
        and open(os.path.join(ws, "REVIEWS.md"), errors="replace")
        .read()
        .count("\n## "),
        "max_submits": meta.get("max_submits"),
        "max_runtime_s": meta.get("max_runtime_s"),
        "max_rounds": meta.get("max_rounds"),
    }


def newest_log(campaign):
    logs = glob.glob(os.path.join(workspace(campaign), "logs", "run_*.log"))
    return max(logs, key=os.path.getmtime) if logs else None


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>%(campaign)s</title>
<style>
 html,body { height:100%%; margin:0; }
 body { display:flex; flex-direction:column;
        font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;
        background:#111; color:#ddd; }
 header { padding:8px 12px; background:#1b1b1b; border-bottom:1px solid #333;
          display:flex; gap:16px; align-items:baseline; flex:none; }
 header b { color:#fff; font-size:14px; }
 header .sep { color:#555; }
 header #head { margin-left:auto; }
 header span { color:#888; }
 #tabs { display:flex; gap:4px; padding:6px 12px; background:#161616;
         border-bottom:1px solid #333; flex-wrap:wrap; flex:none; }
 #tabs button { background:#222; color:#bbb; border:1px solid #333; padding:3px 10px;
                cursor:pointer; font:inherit; }
 #tabs button.on { background:#2d4a2d; color:#fff; }
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
 td { padding:3px 18px 3px 0; vertical-align:top; }
 td.k { color:#888; }
 .bar { display:inline-block; width:150px; height:9px; background:#222;
        border:1px solid #333; vertical-align:middle; margin-right:8px; }
 .bar i { display:block; height:100%%; background:#3d7a3d; }
 #newest { position:fixed; right:18px; bottom:14px; display:none;
           background:#2d4a2d; color:#fff; border:1px solid #4a7a4a; padding:5px 12px;
           cursor:pointer; font:inherit; }
</style></head><body>
<header><b>AgentLab</b><span class="sep">/</span><b id="camp">%(campaign)s</b>\
<span id="head">connecting\u2026</span></header>
<div id="tabs"></div>
<div id="pane"><pre id="view">loading\u2026</pre></div>
<button id="newest">\u2193 newest</button>
<script>
let tab = "status", offset = 0, logText = "", rawMode = false;
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
    const b = document.createElement("button");
    // The log is what the agent said and did; the file name is not the point.
    b.textContent = n === "log" ? "agent log" : n;
    b.className = n === tab ? "on" : "";
    b.onclick = () => {
      // Every tab starts clean: the log re-reads from the beginning, and the styling
      // of a rendered record does not follow you to the next tab.
      tab = n; offset = 0; logText = "";
      view.innerHTML = ""; view.className = ""; view.dataset.body = "";
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
  const rows = [
    ["run", `${s.handle || "\u2014"} \u00b7 ${s.run}`],
    ["state", s.status === "running"
        ? `running \u00b7 heartbeat ${hms(s.heartbeat_age_s)} ago`
        : `${s.status} \u00b7 ${s.stop_reason || ""}`],
    ["doing", s.status === "running" && s.phase
        ? `${s.phase} \u00b7 ${hms(s.phase_age_s)}` : "\u2014"],
    ["jobs submitted", bar(s.jobs_run, s.max_submits)],
    ["in flight", `${s.jobs_run - s.jobs_done} \u00b7 ${s.jobs_done} returned`],
    ["jobs, all runs", String(s.jobs)],
    ["results recorded", String(s.results)],
    ["reviews", String(s.reviews || 0)],
    ["elapsed", bar(s.elapsed_s, s.max_runtime_s, hms)],
    ["model", `${s.model || "\u2014"} \u00b7 context ` + (s.context_pct == null
        ? "no data"
        : `${short(s.context_tokens)}/${short(s.context_window)} (${Math.round(s.context_pct)}%%)`)],
    ["critic", s.critic || "\u2014"],
    ["host", s.host || "\u2014"],
    ["started", s.started_at || "\u2014"],
  ];
  if (s.ended_at) rows.push(["ended", s.ended_at]);
  view.innerHTML = "<table>" + rows.map(
    ([k, v]) => `<tr><td class="k">${k}</td><td>${v}</td></tr>`).join("") + "</table>";
}

const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

// A record that references a figure should show it. Everything else stays as written:
// this is the file, not a rendering of it.
function withFigures(text) {
  return esc(text).replace(/!\\[([^\\]]*)\\]\\(([^)\\s]+)\\)/g, (m, alt, src) =>
    /^https?:/.test(src) ? m
      // Sized to sit inside the text rather than replace it; click for the full thing.
      : `<a href="/image?name=${encodeURIComponent(src)}" target="_blank" ` +
        `title="${src} \u2014 click to open full size">` +
        `<img src="/image?name=${encodeURIComponent(src)}" alt="${alt}" ` +
        `style="max-width:100%%;max-height:75vh;display:block;margin:8px 0;` +
        `border:1px solid #333;background:#fff;cursor:zoom-in"></a>`);
}

async function refresh() {
  try {
    const s = await (await fetch("/status")).json();
    document.getElementById("head").textContent = s.run
      ? (s.status === "running" ? "running \u00b7 " : s.status + " \u00b7 ") + (s.handle || s.run)
      : "no run yet";
    if (tab === "status") { view.className = ""; renderStatus(s); return; }
    if (tab === "log") {
      view.className = "";
      const stick = atBottom();
      const r = await fetch(`/log?from=${offset}`);
      const j = await r.json();
      if (j.reset) { logText = ""; offset = 0; }      // a new run: start the pane again
      if (j.text) { logText += j.text; view.textContent = logText; }
      offset = j.offset;
      if (stick) { pane.scrollTop = pane.scrollHeight; newest.style.display = "none"; }
      else if (j.text) newest.style.display = "block";
    } else {
      const md = tab.endsWith(".md") && !rawMode;
      const body = await (await fetch(
        "/file?name=" + encodeURIComponent(tab) + (rawMode ? "&raw=1" : ""))).text();
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

async function files() {
  try { setTabs(await (await fetch("/files")).json()); } catch (e) {}
}
files(); refresh();
setInterval(refresh, 1500);
setInterval(files, 10000);
</script></body></html>
"""


def _render(text):
    """A record as its author meant it to read -- headings, tables, figures. Falls back
    to the text itself if anything goes wrong: a viewer that shows nothing is worse than
    one that shows the file."""
    try:
        html_out = _markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        return "<pre>" + html.escape(text) + "</pre>"

    # Figures are referenced relative to the workspace, which only this server can read.
    def _img(m):
        src = urllib.parse.quote(m.group("src"))
        alt = m.group(0)
        alt = re.search(r'alt="([^"]*)"', alt)
        return (
            f'<a href="/image?name={src}" target="_blank">'
            f'<img src="/image?name={src}" alt="{alt.group(1) if alt else ""}"></a>'
        )

    html_out = re.sub(
        r'<img[^>]*?src="(?!https?:|/)(?P<src>[^"]+)"[^>]*/?>', _img, html_out
    )

    # A record may link a figure rather than embed it. The link is relative to the
    # workspace, which only this server can read, so point it at the same route.
    return re.sub(
        r'<a href="(?!https?:|/)(?P<href>[^"]+\.(?:png|jpg|jpeg|gif|svg|webp))"',
        lambda m: (
            f'<a target="_blank" href="/image?name={urllib.parse.quote(m.group("href"))}"'
        ),
        html_out,
        flags=re.IGNORECASE,
    )


class Handler(http.server.BaseHTTPRequestHandler):
    campaign = ""

    last_request = 0.0  # for --exit-when-idle: a page open polls constantly

    def log_message(self, *a):
        pass  # a watcher that narrates its own requests is noise

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

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/":
            self._send(
                PAGE
                % {
                    "campaign": html.escape(self.campaign),
                    "campaign_json": json.dumps(self.campaign),
                },
                "text/html; charset=utf-8",
            )
        elif url.path == "/log":
            self._send(
                json.dumps(self._log_from(int(q.get("from", ["0"])[0]))),
                "application/json",
            )
        elif url.path == "/status":
            self._send(json.dumps(status(self.campaign)), "application/json")
        elif url.path == "/files":
            ws = workspace(self.campaign)
            self._send(
                json.dumps(
                    [f for f in READABLE if os.path.isfile(os.path.join(ws, f))]
                ),
                "application/json",
            )
        elif url.path == "/image":
            self._send_image(q.get("name", [""])[0])
        elif url.path == "/file":
            name = q.get("name", [""])[0]
            text = self._file(name)
            if q.get("raw") or not name.endswith(".md") or _markdown is None:
                self._send(text)
            else:
                self._send(_render(text), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    _serving = None  # the log file the page is currently being fed

    def _log_from(self, offset):
        path = newest_log(self.campaign)
        if not path:
            return {"text": "", "offset": 0, "name": "no run log yet", "running": False}
        size = os.path.getsize(path)
        # Start the pane again when the file under it changes -- a new run writes a new
        # log, and a truncated one is no longer what we were reading. Either way,
        # splicing two logs together would be a lie.
        cls = type(self)
        reset = offset > size or (cls._serving is not None and cls._serving != path)
        cls._serving = path
        if reset:
            offset = 0
        with open(path, errors="replace") as f:
            f.seek(offset)
            text = f.read()
        run_dir = os.path.join(workspace(self.campaign), "runs")
        beating = glob.glob(os.path.join(run_dir, "*", "heartbeat"))
        return {
            "text": text,
            "offset": size,
            "reset": reset,
            "name": os.path.basename(path),
            "running": bool(beating),
        }

    IMAGE_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
    }

    def _send_image(self, name):
        """Serve a figure the records point at. Confined to the campaign's workspace:
        the name is resolved and checked to be inside it, so a path from a file cannot
        reach out of it."""
        ws = os.path.realpath(workspace(self.campaign))
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

    def _file(self, name):
        if name not in READABLE:
            return "not a file this watcher serves"
        path = os.path.join(workspace(self.campaign), name)
        try:
            size = os.path.getsize(path)
            with open(path, errors="replace") as f:
                if size > TAIL_BYTES:
                    f.seek(size - TAIL_BYTES)
                    return (
                        f"[showing the last {TAIL_BYTES // 1000} KB of {size // 1000} KB]\n\n"
                        + f.read()
                    )
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
    print(
        f"watching {campaign} at {url}  (Ctrl-C to stop; the run is unaffected)",
        flush=True,
    )
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
