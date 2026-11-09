"""The HTTP server: the page, the JSON it polls, and the few writes it may make."""

import getpass
import glob
import hashlib
import html
import http.cookies
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime

import budgets

from .workspace import read_meta, workspace
from .files import HAS_MARKDOWN, NOT_YET, READABLE, TAIL_BYTES, campaign_tabs, is_binary, pictures, render_markdown, root_path, tree
from .runs import ended_with_session, newest_log, run_cwd, run_files, run_path, runs, talk_to_run
from .budget_view import budgets_view, group_session, owns
from .lab import campaigns, group_siblings, lab_control, lab_messages, lab_run_dir, lab_services, lab_summary, lab_users, latest_campaign, recent_campaigns, running_campaigns, secretary_live, services_state, slack_attached
from .resources import resources
from .status import status


STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}


class Handler(http.server.BaseHTTPRequestHandler):
    campaign = ""           # the one it was started on: where the page opens, and the
                            # campaign a request that names none is about
    open_on_lab = False     # set when no campaign was named: `/` shows the lab instead

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

    def _scope(self, q):
        """Which of the two places the page opens on. The address says so -- `?lab` for
        the lab, `?c=` for a campaign -- and a bare `/` opens where this watcher was
        pointed: at a campaign if one was named, else at the lab."""
        if "lab" in q:
            return "lab"
        if q.get("c", [""])[0]:
            return "campaign"
        return "lab" if self.open_on_lab else "campaign"

    def handle_one_request(self):
        type(self).last_request = time.time()
        super().handle_one_request()

    def _send(self, body, ctype="text/plain; charset=utf-8", status=200, headers=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Everything here is live: the page is generated from code that changes under
        # the viewer, and the JSON behind it changes every second. Nothing carries a
        # validator, so without this a browser is free to reuse a copy from before the
        # last edit -- and a restart of the watcher does not dislodge it.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    # Changes are made as the user running this watcher, so they are taken only from a
    # page opened with its key: the link it printed. Another user on the machine can
    # reach the port, and a web page in the browser can send to localhost; neither has
    # the key. The page keeps it in a cookie of this watcher's own.
    key = ""

    def _cookie_name(self):
        return "watchkey_" + hashlib.sha256(self.key.encode()).hexdigest()[:10]

    def _can_write(self):
        jar = http.cookies.SimpleCookie(self.headers.get("Cookie") or "")
        held = jar.get(self._cookie_name())
        return bool(self.key) and held is not None and secrets.compare_digest(
            held.value, self.key)

    def _refuse_write(self):
        self._send(json.dumps({"ok": False, "sent": False,
                               "error": "this page cannot make changes: open the link "
                                        "the watcher printed when it started"}),
                   "application/json", status=403)

    def do_POST(self):
        """The writes: a message for the agent, which lands on the board it reads between
        turns; starting or stopping the lab's services; and a change to a run's budget."""
        url = urllib.parse.urlparse(self.path)
        if url.path not in ("/say", "/services", "/budget"):
            self.send_error(404)
            return
        if not self._can_write():
            self._refuse_write()
            return
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        if url.path == "/budget":
            self._send(json.dumps(self._change_budget(q)), "application/json")
            return
        if url.path == "/services":
            # The lab's processes, started and stopped from the page that reports them.
            action = (q.get("do") or [""])[0]
            if action not in ("start", "stop"):
                self._send(json.dumps({"ok": False, "text": "start or stop"}),
                           "application/json")
                return
            self._send(json.dumps({"ok": True, "text": lab_control(action),
                                   "services": lab_services(force=True)}),
                       "application/json")
            return
        campaign = self._campaign(q)
        length = int(self.headers.get("Content-Length") or 0)
        text = self.rfile.read(length).decode("utf-8", "replace").strip()
        if not text:
            self._send(json.dumps({"sent": False}), "application/json")
            return
        # A lab-scope line is a question for the secretary, not for any one agent, so it
        # goes to the inbox it polls rather than to a campaign board. The bridge writes
        # the same file; this is a second writer, not a different channel.
        if "lab" in q:
            run = lab_run_dir()
            stamp = datetime.now().strftime("%H:%M")
            try:
                os.makedirs(run, exist_ok=True)
                with open(os.path.join(run, "secretary_inbox.md"), "a") as f:
                    f.write(text.replace("\n", " ") + "\n")
                with open(os.path.join(run, "MESSAGES.md"), "a") as f:
                    f.write(f"`{stamp}` **you** {text}\n\n")
            except OSError as e:
                self._send(json.dumps({"sent": False, "error": str(e)}),
                           "application/json")
                return
            self._send(json.dumps({"sent": True, "secretary": secretary_live()}),
                       "application/json")
            return
        ws = workspace(campaign)
        stamp = datetime.now().strftime("%H:%M")
        # A message to one agent alone goes to its run's inbox, which only it reads.
        to = (q.get("to") or [""])[0]
        if to:
            to_dir = run_path(campaign, to)
            if not to_dir:
                self._send(json.dumps({"sent": False, "error": f"no run {to}"}),
                           "application/json")
                return
            if ended_with_session(to_dir):
                self._send(json.dumps(talk_to_run(to_dir, ws, text, stamp)),
                           "application/json")
                return
            name = read_meta(os.path.join(to_dir, "meta.json")).get("branch") or to
            try:
                with open(os.path.join(to_dir, "INBOX.md"), "a") as f:
                    f.write(text.replace("\n", " ") + "\n")
                with open(os.path.join(ws, "MESSAGES.md"), "a") as f:
                    f.write(f"`{stamp}` **you** (to {name} only) {text}\n\n")
            except OSError as e:
                self._send(json.dumps({"sent": False, "error": str(e)}), "application/json")
                return
            self._send(json.dumps({"sent": True}), "application/json")
            return
        # A run that has ended reads no board, so a message to it reopens its session
        # instead, and the agent's reply joins the same conversation (talk.py).
        run_dir = run_path(campaign, (q.get("run") or [""])[0] or None)
        if run_dir and ended_with_session(run_dir):
            self._send(json.dumps(talk_to_run(run_dir, ws, text, stamp)), "application/json")
            return
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

    def _change_budget(self, q):
        """Apply one change to a run's budget: {"name", "key", "change"} in the body,
        the change as bin/budget.sh takes it (+N, -N, N; runtime_s also with s, m, h).
        Only a run of the user this watcher runs as."""
        run_dir = run_path(self._campaign(q), (q.get("run") or [""])[0] or None)
        if not run_dir:
            return {"ok": False, "error": "no such run"}
        if not owns(run_dir):
            return {"ok": False, "error": "only the user who started this run can "
                                          "change its budgets"}
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            name, key = str(body.get("name", "")), body.get("key")
            change = str(body.get("change", "")).strip()
            done = budgets.change(run_dir, [budgets._parse(
                f"{name}{'.' + str(key) if key else ''}={change}")],
                who=f"{getpass.getuser()} (watch)")
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return {"ok": False, "error": str(e)}
        except OSError as e:
            return {"ok": False, "error": f"could not write the budget: {e}"}
        return {"ok": True, "done": [budgets.describe(c) for c in done]}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        # `?lab` carries no value, and a query key without one is dropped unless
        # blanks are kept.
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        campaign = self._campaign(q)
        run = (q.get("run") or [""])[0] or None    # a past run, else the newest
        if url.path in ("/", "/next"):
            # `/next` is the page being redesigned, on the same data as `/`.
            # The page opens where the address says, so a bookmark is showing the right
            # place before the first poll rather than a moment after it. Opened with the
            # key, the page may make changes from then on (see _can_write).
            given = (q.get("k") or [""])[0]
            headers = ()
            if self.key and given and secrets.compare_digest(given, self.key):
                headers = (("Set-Cookie", f"{self._cookie_name()}={self.key}; Path=/; "
                                          f"HttpOnly; SameSite=Strict"),)
            page = "next/index.html" if url.path == "/next" else "index.html"
            self._send(self._page(page, campaign, self._scope(q)),
                       "text/html; charset=utf-8", headers=headers)
        elif url.path.startswith("/static/"):
            self._send_static(url.path[len("/static/"):])
        elif url.path == "/campaigns":
            self._send(json.dumps(self._choices()), "application/json")
        elif url.path == "/running":
            self._send(json.dumps(running_campaigns()), "application/json")
        elif url.path == "/recent":
            self._send(json.dumps(recent_campaigns()), "application/json")
        elif url.path == "/labchat":
            self._send(json.dumps({"text": lab_messages(),
                                   "secretary": secretary_live(),
                                   "slack": slack_attached()}),
                       "application/json")
        elif url.path == "/servicestate":
            rows = lab_services()
            self._send(json.dumps({"rows": rows, "none": services_state["none"]}),
                       "application/json")
        elif url.path == "/services":
            self._send(json.dumps(lab_services()), "application/json")
        elif url.path == "/users":
            self._send(json.dumps(lab_users()), "application/json")
        elif url.path == "/group":
            # The runs of the group this workspace belongs to, itself among them.
            names = sorted([campaign] + group_siblings(campaign)) if "/" in campaign else []
            self._send(json.dumps({"runs": [lab_summary(c) for c in names],
                                   "session": group_session(campaign)}),
                       "application/json")
        elif url.path == "/lab":
            # By name: the page orders it, so which column it is sorted on survives a
            # poll rather than being undone by one.
            rows = [lab_summary(c) for c in self._choices()]
            rows.sort(key=lambda r: r["campaign"])
            self._send(json.dumps(rows), "application/json")
        elif url.path == "/log":
            self._send(json.dumps(
                self._log_from(campaign, int(q.get("from", ["0"])[0] or 0), run)),
                "application/json")
        elif url.path == "/status":
            self._send(json.dumps(status(campaign, run)), "application/json")
        elif url.path == "/budgets":
            view = budgets_view(campaign, run)
            # Whether this page may change them: opened with the key, and the run is
            # this watcher's user's own.
            view["writable"] = bool(view.get("run")) and self._can_write() and owns(
                run_path(campaign, view["run"]))
            self._send(json.dumps(view), "application/json")
        elif url.path == "/resources":
            self._send(json.dumps(resources(campaign, run, light="light" in q)),
                       "application/json")
        elif url.path == "/runs":
            self._send(json.dumps(runs(campaign)), "application/json")
        elif url.path == "/files":
            # The record tabs: the campaign's own choice, else the usual records, and
            # pictures wherever there are any. Only those that exist yet.
            ws = workspace(campaign)
            chosen = campaign_tabs(campaign)
            names = chosen if chosen is not None else list(READABLE) + ["pictures"]
            have_pics = "pictures" in names and bool(pictures(campaign))
            self._send(json.dumps([n for n in names
                                   if (n == "pictures" and have_pics)
                                   or (n != "pictures"
                                       and os.path.isfile(os.path.join(ws, n)))]),
                       "application/json")
        elif url.path == "/pictures":
            self._send(json.dumps(pictures(campaign)), "application/json")
        elif url.path == "/tree":
            keys = [k for k in q.get("open", [""])[0].split(",") if k]
            self._send(json.dumps(tree(campaign, keys)), "application/json")
        elif url.path == "/wsfile":
            name = q.get("path", [""])[0]
            text = self._wsfile(campaign, q.get("root", ["ws"])[0], name)
            if q.get("raw") or not name.endswith(".md") or not HAS_MARKDOWN:
                self._send(text)
            else:
                self._send(render_markdown(text, campaign), "text/html; charset=utf-8")
        elif url.path == "/runfiles":
            self._send(json.dumps(run_files(campaign, run)), "application/json")
        elif url.path == "/messages":
            # The conversation, not a file tab: served whether or not it exists yet.
            try:
                with open(os.path.join(workspace(campaign), "MESSAGES.md"),
                          errors="replace") as f:
                    self._send(f.read())
            except OSError:
                self._send("")
        elif url.path == "/image":
            self._send_image(campaign, q.get("name", [""])[0],
                             q.get("root", ["ws"])[0])
        elif url.path in ("/file", "/runfile", "/agentfile"):
            name = q.get("name", [""])[0]
            text = (self._runfile(campaign, name, run) if url.path == "/runfile"
                    else self._agentfile(campaign, name, run) if url.path == "/agentfile"
                    else self._file(campaign, name))
            if q.get("raw") or not name.endswith(".md") or not HAS_MARKDOWN:
                self._send(text)
            else:
                self._send(render_markdown(text, campaign), "text/html; charset=utf-8")
        else:
            self.send_error(404)

    # Per campaign: one watcher serves them all, and a page on one must not reset a
    # page on another.
    _serving = {}              # (campaign, run) -> the log file it is being fed

    def _log_from(self, campaign, offset, run=None):
        path = newest_log(campaign)
        if run:
            d = run_path(campaign, run)
            named = os.path.join(workspace(campaign), "logs",
                                 "run_" + os.path.basename(d) + ".log") if d else None
            path = named if named and os.path.isfile(named) else None
        if not path:
            return {"text": "", "offset": 0, "name": "no run log yet", "running": False}
        size = os.path.getsize(path)
        # Start the pane again when the file under it changes -- a new run writes a new
        # log, and a truncated one is no longer what we were reading. Either way,
        # splicing two logs together would be a lie.
        cls = type(self)
        was = cls._serving.get((campaign, run))
        reset = offset > size or (was is not None and was != path)
        cls._serving[(campaign, run)] = path
        if reset:
            offset = 0
        with open(path, errors="replace") as f:
            f.seek(offset)
            text = f.read()
        run_dir = os.path.join(workspace(campaign), "runs")
        beating = glob.glob(os.path.join(run_dir, "*", "heartbeat"))
        return {"text": text, "offset": size, "reset": reset,
                "name": os.path.basename(path), "running": bool(beating)}

    def _page(self, name, campaign, scope):
        """The page, opening on the campaign and scope the address asked for. Read on
        every request, like everything else here, so an edit shows on reload."""
        with open(os.path.join(STATIC, name)) as f:
            page = f.read()
        return (page.replace("{{campaign}}", html.escape(campaign))
                    .replace("{{campaign_json}}", json.dumps(campaign))
                    .replace("{{scope_json}}", json.dumps(scope)))

    def _send_static(self, name):
        """The page's own stylesheets and scripts, from under static/. A path with an
        empty or hidden part (which covers `..`) is refused."""
        ext = os.path.splitext(name)[1]
        if any(not p or p.startswith(".") for p in name.split("/")) or ext not in STATIC_TYPES:
            self.send_error(404)
            return
        try:
            with open(os.path.join(STATIC, name), "rb") as f:
                self._send(f.read(), STATIC_TYPES[ext])
        except OSError:
            self.send_error(404)

    IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp"}

    def _send_image(self, campaign, name, root="ws"):
        """Serve a figure the records point at. Confined to the campaign's root: the
        name is resolved and checked to be inside it, so a path from a file cannot reach
        out of it."""
        path = root_path(campaign, root, name)
        ext = os.path.splitext(path)[1].lower() if path else ""
        if path is None or ext not in self.IMAGE_TYPES:
            self.send_error(404)
            return
        try:
            with open(path, "rb") as f:
                self._send(f.read(), self.IMAGE_TYPES[ext])
        except OSError:
            self.send_error(404)

    def _runfile(self, campaign, name, run=None):
        d = run_path(campaign, run)
        if not d or name not in run_files(campaign, run):
            return "not a file this watcher serves"
        return self._read(os.path.join(d, name), name)

    def _agentfile(self, campaign, name, run=None):
        """A record a run keeps in its own working directory: one its meta.json names
        under `records`, as a worker's notes are."""
        d = run_path(campaign, run)
        meta = read_meta(os.path.join(d, "meta.json")) if d else {}
        cwd = run_cwd(campaign, meta)
        if name not in (meta.get("records") or []) or not cwd:
            return "not a record of this run"
        path = os.path.join(cwd, name)
        if not os.path.isfile(path):
            return f"{name} has not been written yet"
        return self._read(path, name)

    def _wsfile(self, campaign, root, rel):
        """Any file under one of the campaign's roots, as against the records `_file`
        serves. Confined to that root by `root_path`."""
        path = root_path(campaign, root, rel)
        if path is None or not os.path.isfile(path):
            return "not a file of this campaign"
        if is_binary(path):
            return "[%s \u2014 binary, %d KB]" % (os.path.basename(path),
                                                  os.path.getsize(path) // 1000)
        return self._read(path, rel)

    def _file(self, campaign, name):
        if name not in READABLE and name not in (campaign_tabs(campaign) or []):
            return "not a file this watcher serves"
        path = os.path.join(workspace(campaign), name)
        # A record is written when there is first something in it, so a missing one is
        # empty so far, not an error.
        if not os.path.exists(path):
            return NOT_YET.get(name, f"{name} has not been written yet")
        return self._read(path, name)

    def _read(self, path, name):
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
    argv = sys.argv[1:]
    port = 8765
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 >= len(argv):
            sys.exit("--port needs a number")
        port = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]      # its value is a port, not a campaign
    args = [a for a in argv if not a.startswith("-")]
    # The campaign is which one to open on, not which one this can serve: the page
    # switches between all of them. Left out, it opens on the lab -- every campaign and
    # which of them is running -- and the campaign behind that is the most recent one,
    # so going into a campaign from the header lands somewhere sensible. A lab where
    # nothing has run yet opens the same way, on a lab page with no campaigns in it.
    campaign = args[0] if args else (latest_campaign() or "")
    if args and not os.path.isdir(workspace(campaign)):
        sys.exit(f"no workspace at {workspace(campaign)} -- has this campaign run?")

    Handler.campaign = campaign
    Handler.open_on_lab = not args
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
    # The key the page needs to make changes; a run that starts its own watcher gives
    # one, so the link it prints works.
    Handler.key = os.environ.get("WATCH_KEY") or secrets.token_urlsafe(18)
    url = f"http://127.0.0.1:{port}/?k={Handler.key}"
    opened = campaign if args else "the lab"
    print(f"watching {opened} at {url}  (Ctrl-C to stop; the run is unaffected)",
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
