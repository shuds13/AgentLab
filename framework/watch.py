#!/usr/bin/env python3
"""
Watch a running campaign in a browser: the agent's log as it is written, and the files
it is writing.

A run is long and quiet -- minutes can pass inside one turn with nothing printed -- and
whoever started it usually has no terminal attached to it. This serves the same files
they would otherwise `tail`, on localhost, read-only, so a run can be followed without
touching it.

Usage:
    python watch.py [campaign] [--port 8765] [--no-open]

It reads the campaign's workspace and serves what it finds. The campaign named on the
command line is the one it opens on, and with none named it opens on the one most
recently active; the page can switch to any other campaign in the lab, since one
watcher can serve them all. The one thing it writes is a message you type: that goes
to the board the agent reads between turns. Stop it with Ctrl-C; the run is unaffected
either way.
"""

import glob
import html
import http.server
import json
import os
import re
import subprocess
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
    a run still going, and the view would stick to the finished one.

    Live means a RECENT heartbeat, not the file existing. A run killed outright leaves
    its heartbeat behind, and counting that as live pins the view to a dead run for
    good: every later run ends and removes its own, so the abandoned one is the only
    candidate left."""
    metas = glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json"))
    if not metas:
        return None
    live = []
    for m in metas:
        age = _beat_age(os.path.dirname(m))
        if age is not None and age < AGENT_ALIVE_WITHIN:
            live.append(m)
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


def _phase(run_dir):
    """What the run said it was doing, and how long ago it said so."""
    try:
        with open(os.path.join(run_dir, "phase")) as f:
            stamp, phase = f.read().split("\n", 1)
            return phase.strip(), int(time.time() - float(stamp))
    except Exception:
        return None, None


def _elapsed(meta):
    """How long the run took, not how long ago it began: once it has ended, the clock
    stops where it stopped."""
    started, ended = meta.get("started_at"), meta.get("ended_at")
    if not started:
        return None
    try:
        end = datetime.fromisoformat(ended).timestamp() if ended else time.time()
        return int(end - datetime.fromisoformat(started).timestamp())
    except Exception:
        return None


# The lab's own run directory: state belonging to the lab rather than to any one
# campaign. The secretary's inbox and the lab transcript live here.
def lab_run_dir():
    return os.path.join(LAB_DIR, "workspace", "run")


def lab_messages():
    """The lab conversation: what was asked here and what the secretary answered.

    `framework/notify.sh` appends every reply, whether or not Slack is configured, so
    this is the transcript even in a lab with no Slack at all."""
    try:
        with open(os.path.join(lab_run_dir(), "MESSAGES.md")) as fh:
            return fh.read()
    except OSError:
        return ""


def slack_attached():
    """Is there a Slack channel on the other end of this conversation?

    The page says so before you type: a line sent here is answered by the secretary
    through `framework/notify.sh`, which carries it on to Slack when a webhook is
    configured.
    Someone writing in the browser would otherwise have no way to know the reply --
    quoting their question -- appears in a channel.

    The webhook is the one this lab names in `notifiers/slack/slack.env`, which `watch.sh`
    reads through `settings.sh`. A lab that names none is its own conversation, so
    several labs on one machine stay separate until they are pointed at a channel."""
    path = os.environ.get("SLACK_WEBHOOK_FILE") or ""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# A secretary beats every poll (5s by default), so it is known dead in seconds -- not
# on the agent's window, which is sized for a research agent that can spend minutes in
# one model turn. This is the figure the Slack reader uses to decide the same thing,
# and the two must agree or the page and the bridge disagree about who is up.
SECRETARY_ALIVE_WITHIN = int(os.environ.get("SECRETARY_ALIVE_WITHIN", "60"))


def secretary_live():
    """Is a secretary reading the inbox? It writes a heartbeat every poll, so a stale
    one means questions asked here will sit unanswered until it is started."""
    try:
        with open(os.path.join(lab_run_dir(), "secretary_heartbeat")) as fh:
            return (time.time() - float(fh.read().strip())) <= SECRETARY_ALIVE_WITHIN
    except Exception:
        return False


# What `lab.sh status` last said, and when. The page polls, and each ask is a bash and
# a python3; a few seconds stale is not worth spawning those every second.
_services = {"at": 0.0, "rows": []}
SERVICES_TTL = 3


def lab_services(force=False):
    """The lab's own processes and whether they are up.

    Asked of `lab.sh` rather than worked out here: which services a lab has is
    lab.yaml's business and what counts as running is that script's, and a second
    opinion in this file would be one to keep in step."""
    now = time.time()
    if not force and now - _services["at"] < SERVICES_TTL:
        return _services["rows"]
    rows = []
    try:
        out = subprocess.run(["./lab.sh", "status"], cwd=os.path.join(LAB_DIR, "bin"),
                             capture_output=True, text=True, timeout=60).stdout
    except Exception as e:
        out = ""
        rows.append({"service": "lab.sh", "running": False, "detail": str(e)})
    for line in out.splitlines():
        name, _, rest = line.strip().partition(":")
        rest = rest.strip()
        if name and rest:
            rows.append({"service": name, "running": rest.startswith("running"),
                         "detail": rest})
    _services.update(at=now, rows=rows)
    return rows


def lab_control(action):
    """Start or stop those processes, by running what a person would run. `lab.sh` owns
    what that means -- which services, in which order, and leaving alone anything it did
    not start itself. Starting waits for each service to come up, so this is slow, and
    the page is told what the script printed rather than a bare success."""
    try:
        done = subprocess.run(["./lab.sh", action], cwd=os.path.join(LAB_DIR, "bin"),
                              capture_output=True, text=True, timeout=300)
        text = (done.stdout + done.stderr).strip() or f"{action}: nothing to do"
    except Exception as e:
        text = f"{action} failed: {e}"
    _services["at"] = 0.0          # whatever it did, the cached answer is now stale
    return text


def lab_users():
    """Who is set up in this lab, grouped by person.

    A user is a directory of `users/<name>/<system>.json`, so one person holds one entry
    per system they have access to. Read fresh each time and never cached: a user file
    appears when someone is added, and the page is how you see that happened.

    Credentials are not returned. The endpoint id is an address, not a secret, and it is
    what someone comes to this page for; nothing else in the file is sent.

    Two things are reported rather than hidden, because a lab is shared and a silent
    omission reads as "nobody is there": a file still carrying the template's
    placeholders is a stub, and a directory this process cannot read is named with
    `unreadable` set. Someone half set up, or set up behind permissions you do not have,
    is still someone in the lab."""
    people = []
    root = os.path.join(LAB_DIR, "users")
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        # The whole directory, not one person: say which it is, so an empty section
        # cannot be mistaken for a lab with nobody in it.
        return [{"user": os.path.basename(root), "systems": [], "unreadable": True,
                 "error": exc.strerror or str(exc)}] if os.path.exists(root) else []
    for user in names:
        udir = os.path.join(root, user)
        if not os.path.isdir(udir):
            continue
        try:
            fnames = sorted(os.listdir(udir))
        except OSError as exc:
            people.append({"user": user, "systems": [], "unreadable": True,
                           "error": exc.strerror or str(exc)})
            continue
        systems = []
        for fname in fnames:
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(udir, fname)) as fh:
                    cfg = json.load(fh)
                unreadable = False
            except OSError:
                cfg, unreadable = {}, True
            except Exception:
                cfg, unreadable = {}, False
            endpoint = str(cfg.get("endpoint", "") or "")
            account = str(cfg.get("account", "") or "")
            # The templates ship values in angle brackets, and an unedited one must not
            # read as a working endpoint.
            stub = endpoint.startswith("<") or not endpoint
            systems.append({"system": fname[: -len(".json")],
                            "endpoint": endpoint, "account": account,
                            "work_dir": str(cfg.get("work_dir", "") or ""),
                            "transfer": bool(cfg.get("globus")) and not stub,
                            "stub": stub, "unreadable": unreadable})
        if systems:
            people.append({"user": user, "systems": systems, "unreadable": False})
    return people


def lab_summary(campaign):
    """One row of the lab page: enough to tell whether to go in.

    Nothing here grows with the campaign's history. A lab accumulates campaigns and
    each of those accumulates runs, so this reads the newest run's own small files and
    leaves the counts -- which mean reading every job and result ever recorded -- to
    the campaign's own page, where there is one campaign to pay for."""
    run_dir = newest_run(campaign)
    if not run_dir:
        return {"campaign": campaign, "run": None}
    try:
        with open(os.path.join(run_dir, "meta.json")) as f:
            meta = json.load(f)
    except Exception:
        return {"campaign": campaign, "run": None}
    age = _beat_age(run_dir)
    phase, phase_age = _phase(run_dir)
    return {
        "campaign": campaign, "run": meta.get("run_id"),
        "handle": meta.get("handle"), "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason"), "model": meta.get("model"),
        "system": meta.get("system"), "endpoint": meta.get("endpoint"),
        "started_at": meta.get("started_at"), "ended_at": meta.get("ended_at"),
        "elapsed_s": _elapsed(meta),
        "heartbeat_age_s": None if age is None else int(age),
        # Liveness is the heartbeat being recent, not the run saying it is running:
        # a run killed outright never gets to write down that it stopped.
        "live": age is not None and age <= AGENT_ALIVE_WITHIN,
        "phase": phase, "phase_age_s": phase_age,
    }


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
    phase, phase_age = _phase(run_dir)
    started = meta.get("started_at")
    elapsed = _elapsed(meta)
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
        "cost_models": meta.get("cost_models") or [],
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


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


# Liveness is a RECENT heartbeat, not the file existing: a run killed outright leaves
# its heartbeat behind, and a stale one must not read as alive. The convention, and the
# override, are the agent's that writes it.
AGENT_ALIVE_WITHIN = int(os.environ.get("AGENT_ALIVE_WITHIN", "600"))


def _beat_age(run_dir):
    """Seconds since the run last said it was alive, or None if it never did."""
    try:
        with open(os.path.join(run_dir, "heartbeat")) as f:
            return time.time() - float(f.read().strip())
    except (OSError, ValueError):
        return None


def latest_campaign():
    """The campaign to open on when none was named: the one most recently active.

    A campaign whose run is still beating comes first, whenever that run began, since
    that is the one there is something to watch. The rest are ranked by when their last
    run wrote. A campaign that has never run sorts last; there is nothing of it to
    show."""
    def rank(campaign):
        run_dir = newest_run(campaign)
        if not run_dir:
            return (0, 0.0)
        age = _beat_age(run_dir)
        if age is not None and age <= AGENT_ALIVE_WITHIN:
            return (2, -age)            # beating: the freshest of them first
        return (1, _mtime(os.path.join(run_dir, "meta.json")))

    names = campaigns()
    return max(names, key=rank) if names else None


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
 /* The lab's name is the way back up to it, from inside a campaign. */
 header b.home { cursor:pointer; }
 header b.home:hover { color:#9cc4e8; }
 header b.here { cursor:default; }
 /* The lab: every campaign it has, and which of them is doing something. A row is the
    way in, so the whole row answers to the pointer rather than a link inside it. */
 /* The lab's own processes, at the top of the page and ruled off from the campaigns
    below: what is up, and the button that changes it. It stays put whatever the state
    and says what it would do -- a control that comes and goes is one you cannot find
    when you want it. */
 #labsvc { display:none; padding:12px; margin-bottom:8px;
            border-bottom:1px solid #2b3946; }
 #labsvc .svcs { display:flex; flex-wrap:wrap; gap:18px; align-items:center; }
 #labsvc .svc { color:#888; }
 #labsvc .svc.up { color:#7aa87a; }
 #labsvc .dot { display:inline-block; width:7px; height:7px; border-radius:50%%;
                background:#3a3a3a; margin-right:7px; vertical-align:middle; }
 #labsvc .svc.up .dot { background:#3d7a3d; }
 #labsvc .acts { margin-left:auto; display:flex; gap:8px; }
 #labsvc button { background:#1b2836; color:#cfe0f0; border:1px solid #2b3946;
                  padding:3px 12px; cursor:pointer; font:inherit; }
 #labsvc button:hover:enabled { background:#24374b; color:#fff; }
 #labsvc button:disabled { opacity:.5; cursor:default; }
 #lab { display:none; padding:12px; }
 #lab table { width:100%%; margin:0; }
 #lab th { text-align:left; color:#6f8296; font-weight:normal;
           padding:0 18px 5px 0; border-bottom:1px solid #2b3946; }
 #lab td { padding:7px 18px 7px 0; border-bottom:1px solid #1e1e1e; }
 /* A name and a duration read as one thing; broken over two lines they do not. The
    free text -- what stopped it, what it is doing -- is what gives way instead. */
 #lab th, #lab td { white-space:nowrap; }
 #lab .state, #lab .doing { white-space:normal; }
 #lab tr.row { cursor:pointer; }
 #lab tr.row:hover td { background:#1a2430; }
 #lab .name { color:#fff; }
 #lab .dot { display:inline-block; width:7px; height:7px; border-radius:50%%;
             background:#3d7a3d; margin-right:8px; vertical-align:middle; }
 #lab .dot.off { background:#3a3a3a; }
 #lab .on { color:#7aa87a; }
 #lab .quiet { color:#888; }
 #lab .none { color:#666; padding:12px 0; }
 /* People share the campaign table's grid and its click-the-whole-row behaviour, so
    the two read as one page. The separator earns its keep: without it the heading
    reads as an overflow of the table above rather than a section of its own. */
 #labusers { display:none; padding:12px; margin-top:26px;
             border-top:1px solid #2b3946; }
 #labusers h3 { color:#6f8296; font-weight:normal; font-size:13px;
                margin:14px 0 10px; letter-spacing:.06em; text-transform:uppercase; }
 #labusers table { width:100%%; margin:0; }
 #labusers th { text-align:left; color:#6f8296; font-weight:normal;
                padding:0 18px 5px 0; border-bottom:1px solid #2b3946; }
 #labusers td { padding:7px 18px 7px 0; border-bottom:1px solid #1e1e1e;
                white-space:nowrap; }
 #labusers tr.row { cursor:pointer; }
 #labusers tr.row:hover td { background:#1a2430; }
 #labusers .who { color:#fff; }
 /* A system the user is set up on. Muted when the file is still a template, so an
    unconfigured entry is visibly not access. */
 #labusers .chip { display:inline-block; padding:1px 8px; margin-right:6px;
                   border:1px solid #2b3946; border-radius:10px; color:#9ab;
                   font-size:12px; }
 #labusers .chip.stub { border-style:dashed; color:#666; }
 #labusers .chip.locked { border-color:#5a3a3a; color:#b08080; }
 #labusers tr.locked .who { color:#b08080; }
 #labusers .caret { display:inline-block; width:12px; color:#6f8296; }
 #labusers tr.detail td { border-bottom:1px solid #1e1e1e; padding:0 18px 10px 0; }
 #labusers .kv { display:grid; grid-template-columns:auto 1fr; gap:2px 14px;
                 padding:8px 0 2px 20px; white-space:normal; }
 #labusers .kv .k { color:#6f8296; }
 #labusers .kv .v { color:#ccc; word-break:break-all; }
 #labusers .on { color:#7aa87a; }
 #labusers .quiet { color:#888; }
 #labusers .none { color:#666; padding:12px 0; }
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
<header><b id="home">AgentLab</b><span class="sep" id="sep">/</span>\
<select id="camp"><option>%(campaign)s</option></select>\
<span id="head">connecting\u2026</span></header>
<div id="tabs"></div>
<div id="pane"><pre id="view">loading\u2026</pre>\
<div id="labsvc"><div class="svcs"><span id="svclist"></span>\
<span class="acts"><button id="labrun">start lab</button></span></div></div>\
<div id="lab"></div><div id="labusers"></div></div>
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
// Two places to be: the lab, which lists the campaigns it has, and one campaign's own
// page. The address bar says which, so a reload comes back to where you were.
let scope = %(scope_json)s;
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

// What the run consumed. A dash for the money means the price was not the serving
// model's own, not that the run was free -- the runner drops a figure it cannot stand
// behind rather than printing a catalog price for a model the catalog does not cover.
function costRows(models) {
  if (!models || !models.length) return [];
  return models.map(m => [
    models.length > 1 ? `cost · ${m.model}` : "cost",
    `${short(m.input_tokens)} in / ${short(m.output_tokens)} out · `
      + (m.usd == null ? "—"
         : "$" + m.usd.toFixed(2))]);
}

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
    ...costRows(s.cost_models),
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
  if (scope === "lab") return labRefresh();
  // The campaign this pass is about. A reply that arrives after the page has moved on
  // belongs to the campaign that asked for it, not to where you are now, so it is
  // dropped rather than drawn.
  const mine = campaign;
  const here = () => mine === campaign && scope === "campaign";
  try {
    const s = await (await fetch(url("/status"))).json();
    if (!here()) return;
    document.getElementById("head").textContent = s.run
      ? (s.status === "running" ? "running \u00b7 " : s.status + " \u00b7 ") + (s.handle || s.run)
      : "no run yet";
    if (tab === "status") { view.className = ""; renderStatus(s); return; }
    if (tab === "log") {
      view.className = "";
      const stick = atBottom();
      const r = await fetch(url(`/log?from=${offset}`));
      const j = await r.json();
      if (!here()) return;
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
      if (!here()) return;
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
  if (scope === "lab") return labChat();
  const mine = campaign;
  let body;
  try { body = await (await fetch(url("/messages"))).text(); }
  catch (e) { return; }
  if (mine !== campaign || scope !== "campaign") return;
  if (body === chatText) return;
  chatText = body;
  renderChat(body);
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
  if (scope === "lab") return;
  const mine = campaign;
  try {
    const list = await (await fetch(url("/files"))).json();
    if (mine === campaign && scope === "campaign") setTabs(list);
  } catch (e) {}
}
// The lab conversation: questions asked here and what the secretary answered. Every
// reply is appended by framework/notify.sh whether or not Slack is configured, so this is
// the whole exchange in a lab with no Slack at all.
async function labChat() {
  let d;
  try { d = await (await fetch("/labchat")).json(); } catch (e) { return; }
  if (scope !== "lab") return;
  labState = {secretary: !!d.secretary, slack: !!d.slack};
  setChatBar();
  if (d.text === chatText) return;
  chatText = d.text;
  renderChat(d.text);
}

// Both conversations are the same file format, so they are drawn by the same code.
function renderChat(body) {
  const msgs = body.split(/\\n\\s*\\n/).map(m => m.trim()).filter(Boolean);
  if (!msgs.length) { chatlog.className = "none";
                      chatlog.textContent = "no messages yet"; return; }
  const near = chatlog.scrollTop + chatlog.clientHeight > chatlog.scrollHeight - 40;
  chatlog.className = "";
  chatlog.innerHTML = msgs.map(m => {
    const p = m.match(/^`(\\d\\d:\\d\\d)`\\s+\\*\\*([\\w-]+)\\*\\*\\s+([\\s\\S]*)$/);
    if (!p) return `<div class="m">${esc(m)}</div>`;
    const you = p[2] === "you" ? " you" : "";
    return `<div class="m"><span class="t">${p[1]}</span>` +
           `<span class="who${you}">${p[2]}</span>${esc(p[3])}</div>`;
  }).join("");
  if (near) chatlog.scrollTop = chatlog.scrollHeight;
}

// A message goes to the board the agent reads between turns, so it lands after the
// turn in flight rather than interrupting it. On the lab page it goes to the
// secretary's inbox instead -- one reader, one answer, however many campaigns are up.
async function say() {
  const box = document.getElementById("msg"), text = box.value.trim();
  if (!text) return;
  box.value = "";
  const to = scope === "lab" ? "/say?lab" : url("/say");
  try { await fetch(to, {method: "POST", body: text}); } catch (e) {}
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

// One watcher serves the whole lab, so moving between the lab and a campaign, or
// between campaigns, is a change of what the page asks for rather than another
// process on another port.
const camp = document.getElementById("camp"), labPane = document.getElementById("lab");
const usersPane = document.getElementById("labusers");
const svcPane = document.getElementById("labsvc");

// Paint the frame for where we are. The lab has no file tabs and no chat: there is no
// one agent to read them for. The chat bar names the campaign it would write to, so a
// line meant for one agent cannot reach another unnoticed.
function showScope() {
  const lab = scope === "lab";
  svcPane.style.display = lab ? "block" : "none";
  labPane.style.display = lab ? "block" : "none";
  usersPane.style.display = lab ? "block" : "none";
  view.style.display = lab ? "none" : "";
  document.getElementById("tabs").style.display = lab ? "none" : "";
  // The chat is shown in both places, but it writes to different readers: a campaign's
  // board, or the secretary's inbox. The bar says which, so a line meant for one cannot
  // reach the other unnoticed.
  document.getElementById("chat").style.display = "";
  camp.style.display = lab ? "none" : "";
  document.getElementById("sep").style.display = lab ? "none" : "";
  document.getElementById("home").className = lab ? "here" : "home";
  if (lab) newest.style.display = "none";
  document.title = lab ? "AgentLab" : campaign;
  setChatBar();
}

// The chat bar says who reads what you type and, on the lab page, who else sees it.
// One writer: labChat() supplies what only it knows -- whether a secretary is reading
// and whether Slack is attached -- and the rest follows from the scope, so the two
// cannot overwrite each other's title as they poll.
let labState = {secretary: false, slack: false};
function setChatBar() {
  const head = document.getElementById("chathead"), box = document.getElementById("msg");
  if (scope !== "lab") {
    head.textContent = "CHAT \u00b7 " + campaign;
    box.placeholder = "message the agent \u2014 it reads between turns";
    return;
  }
  head.textContent = "SECRETARY"
    + (labState.secretary ? "" : " \u00b7 not running")
    + (labState.slack ? " \u00b7 shared with Slack" : "");
  box.placeholder = !labState.secretary
    ? "secretary not running \u2014 this is queued until it starts"
    : labState.slack
      ? "ask the secretary \u2014 the reply is posted to Slack too"
      : "ask the secretary \u2014 it answers from the recorded results";
}

// Nothing carries over between places: the log, the file being read and the
// conversation all belong to the campaign that was showing.
function resetPane() {
  tab = "status"; offset = 0; logText = ""; chatText = "";
  view.innerHTML = ""; view.className = ""; view.dataset.body = "";
  delete view.dataset.statusSig;
  const t = document.getElementById("tabs");
  t.dataset.key = ""; t.innerHTML = "";    // else the last campaign's tabs flash first
  labPane.dataset.key = "";
  usersPane.dataset.key = "";
  pane.scrollTop = 0; newest.style.display = "none";
  chatlog.className = "none"; chatlog.textContent = "no messages yet";
  document.getElementById("head").textContent = "connecting\u2026";
}

function enterCampaign(name) {
  campaign = name; scope = "campaign"; camp.value = name;
  history.replaceState(null, "", "?c=" + encodeURIComponent(name));
  resetPane(); showScope();
  files(); refresh(); chat();
}

function goLab() {
  scope = "lab";
  history.replaceState(null, "", "?lab");
  resetPane(); showScope();
  refresh();
}

// A date to glance at, not to read: the year is today's and the seconds do not matter.
const when = t => {
  const m = String(t || "").match(/^(\\d{4})-(\\d\\d)-(\\d\\d)T(\\d\\d:\\d\\d)/);
  return m ? `${m[2]}-${m[3]} ${m[4]}` : "\u2014";
};

function renderLab(rows) {
  if (!rows.length) {
    labPane.innerHTML = `<div class="none">no campaigns have run yet</div>`;
    labPane.dataset.key = "";
    return;
  }
  // Same reason the status table is built once and its cells rewritten: a table
  // rebuilt every second cannot hold a selection, and these rows tick.
  const key = rows.map(r => r.campaign).join("|");
  if (labPane.dataset.key !== key) {
    labPane.dataset.key = key;
    labPane.innerHTML = "<table><tr><th>campaign</th><th>state</th><th>doing</th>" +
      "<th>run</th><th>took</th><th>last active</th></tr>" +
      rows.map(r => `<tr class="row" data-c="${esc(r.campaign)}">` +
        `<td class="name"></td><td class="state"></td><td class="doing"></td>` +
        `<td class="run"></td><td class="took"></td><td class="last"></td></tr>`
      ).join("") + "</table>";
    for (const tr of labPane.querySelectorAll("tr.row"))
      tr.onclick = () => enterCampaign(tr.dataset.c);
  }
  const trs = labPane.querySelectorAll("tr.row");
  rows.forEach((r, i) => {
    const set = (sel, h) => {
      const td = trs[i].querySelector(sel);
      if (td && td.innerHTML !== h) td.innerHTML = h;
    };
    set(".name", `<span class="dot${r.live ? "" : " off"}"></span>${esc(r.campaign)}`);
    set(".state", !r.run ? `<span class="quiet">never run</span>`
        : r.live ? `<span class="on">running</span>`
        : `<span class="quiet">${esc(r.status || "stopped")}` +
          (r.stop_reason ? " \u00b7 " + esc(r.stop_reason) : "") + `</span>`);
    set(".doing", r.live && r.phase
        ? `${esc(r.phase)} <span class="quiet">\u00b7 ${hms(r.phase_age_s)}</span>`
        : "\u2014");
    set(".run", r.handle ? esc(r.handle) : "\u2014");
    set(".took", r.elapsed_s == null ? "\u2014" : hms(r.elapsed_s));
    // A live run is active now; a stopped one was last active when it ended.
    set(".last", r.live ? `<span class="on">now</span>` : when(r.ended_at));
  });
}

// Who is set up in this lab, one row per person. The rows carry nothing that ticks, so
// this rebuilds only when the set changes -- which is when someone is added.
// Which systems a person has is the thing worth seeing at a glance; the endpoint id and
// the paths are what you go looking for, so they live behind a click.
const userOpen = new Set();

function renderLabUsers(people) {
  if (!people.length) {
    usersPane.innerHTML = `<h3>people</h3><div class="none">` +
      `no user files yet \u2014 add users/&lt;you&gt;/&lt;system&gt;.json</div>`;
    usersPane.dataset.key = "none";
    return;
  }
  const key = people.map(p => p.user + ":" + (p.unreadable ? "x" :
    p.systems.map(a => a.system).join(","))).join("|");
  if (usersPane.dataset.key !== key) {
    usersPane.dataset.key = key;
    usersPane.innerHTML = `<h3>people</h3><table>` +
      `<tr><th>user</th><th>systems</th><th>transfer</th></tr>` +
      people.map(p => {
        // A person whose directory this process cannot read is still in the lab, so
        // the row says so rather than leaving a gap that reads as nobody.
        if (p.unreadable) return `<tr class="row locked" data-u="${esc(p.user)}">` +
          `<td class="who"><span class="caret"></span>${esc(p.user)}</td>` +
          `<td><span class="chip locked">no read access</span></td>` +
          `<td><span class="quiet">${esc(p.error || "")}</span></td></tr>` +
          `<tr class="detail" data-d="${esc(p.user)}"><td colspan="3"></td></tr>`;
        const stub = p.systems.every(a => a.stub);
        const chips = p.systems.map(a =>
          `<span class="chip${a.stub ? " stub" : ""}">${esc(a.system)}</span>`).join("");
        const moved = p.systems.filter(a => a.transfer).length;
        return `<tr class="row" data-u="${esc(p.user)}">` +
          `<td class="who"><span class="caret"></span>${esc(p.user)}</td>` +
          `<td>${chips}</td>` +
          `<td>${stub ? `<span class="quiet">not set up</span>`
                : moved ? `<span class="on">${moved} of ${p.systems.length}</span>`
                : `<span class="quiet">none</span>`}</td></tr>` +
          `<tr class="detail" data-d="${esc(p.user)}"><td colspan="3"></td></tr>`;
      }).join("") + `</table>`;
    for (const tr of usersPane.querySelectorAll("tr.row"))
      tr.onclick = () => {
        const u = tr.dataset.u;
        userOpen.has(u) ? userOpen.delete(u) : userOpen.add(u);
        paintUsers(people);
      };
  }
  paintUsers(people);
}

// The open/closed state is the only thing that changes between paints, so it is applied
// separately from the table that holds it.
function paintUsers(people) {
  for (const p of people) {
    const open = userOpen.has(p.user);
    const row = usersPane.querySelector(`tr.row[data-u="${CSS.escape(p.user)}"]`);
    const det = usersPane.querySelector(`tr.detail[data-d="${CSS.escape(p.user)}"]`);
    if (!row || !det) continue;
    const caret = row.querySelector(".caret");
    if (caret) caret.textContent = open ? "\u25be" : "\u25b8";
    det.style.display = open ? "" : "none";
    if (!open) continue;
    const html = p.unreadable
      ? `<div class="kv"><div class="k">status</div><div class="v">this user\u2019s directory cannot be read by the account running the watcher</div></div>`
      :
    p.systems.map(a => `<div class="kv">` +
      `<div class="k">system</div><div class="v">${esc(a.system)}` +
      (a.stub ? ` <span class="quiet">(template, not configured)</span>` : "") + `</div>` +
      `<div class="k">endpoint</div><div class="v">${a.endpoint ? esc(a.endpoint) : "\u2014"}</div>` +
      `<div class="k">account</div><div class="v">${a.account ? esc(a.account) : "\u2014"}</div>` +
      `<div class="k">work dir</div><div class="v">${a.work_dir ? esc(a.work_dir) : "\u2014"}</div>` +
      `<div class="k">transfer</div><div class="v">` +
      (a.transfer ? `<span class="on">configured</span>`
                  : `<span class="quiet">not configured</span>`) + `</div></div>`).join("");
    const td = det.querySelector("td");
    if (td.innerHTML !== html) td.innerHTML = html;
  }
}

async function usersRefresh() {
  let people;
  try { people = await (await fetch("/users")).json(); } catch (e) { return; }
  if (scope !== "lab") return;
  renderLabUsers(people);
}

async function labRefresh() {
  let rows;
  try { rows = await (await fetch("/lab")).json(); }
  catch (e) { document.getElementById("head").textContent = "watcher stopped"; return; }
  if (scope !== "lab") return;
  usersRefresh();
  servicesRefresh();
  const live = rows.filter(r => r.live).length;
  document.getElementById("head").textContent =
    `${rows.length} campaign${rows.length === 1 ? "" : "s"} \u00b7 ` +
    (live ? `${live} running` : "none running");
  renderLab(rows);
}

// The lab's own processes. Polled with everything else, and driven by the button beside
// them: the dots are the answer, so what the script printed is not shown.
function renderServices(rows) {
  const list = document.getElementById("svclist");
  const html = rows.length
    ? rows.map(r => `<span class="svc${r.running ? " up" : ""}" title="${esc(r.detail)}">` +
                    `<span class="dot"></span>${esc(r.service)}</span>`).join(" ")
    : `<span class="svc">nothing switched on in lab.yaml</span>`;
  if (list.innerHTML !== html) list.innerHTML = html;
  // One button, because there is one thing to do: whatever is not running, start it;
  // when it all is, the only move left is stopping it.
  const button = document.getElementById("labrun");
  const running = rows.length > 0 && rows.every(r => r.running);
  if (!button.disabled) {
    button.dataset.act = running ? "stop" : "start";
    button.textContent = running ? "stop lab" : "start lab";
  }
}

async function servicesRefresh() {
  let rows;
  try { rows = await (await fetch("/services")).json(); } catch (e) { return; }
  if (scope === "lab") renderServices(rows);
}

// A start waits for each service to come up, so the button says so rather than looking
// ignored. It stays in place throughout -- disabled while the script runs, never gone.
async function labControl(action) {
  const button = document.getElementById("labrun");
  button.disabled = true;
  button.textContent = action === "start" ? "starting\u2026" : "stopping\u2026";
  try {
    const d = await (await fetch("/services?do=" + action, {method: "POST"})).json();
    if (d.services) renderServices(d.services);
  } catch (e) {}
  button.disabled = false;
  chat();
}

document.getElementById("labrun").onclick = e =>
  labControl(e.currentTarget.dataset.act || "start");

async function campaignList() {
  let names;
  try { names = await (await fetch("/campaigns")).json(); } catch (e) { return; }
  if (!names.length) return;
  // The campaign being shown has gone from the lab -- its workspace removed while the
  // page was open. The server is answering for the one it started on, so show that.
  if (!names.includes(campaign)) {
    campaign = HOME;
    if (scope === "campaign") {
      history.replaceState(null, "", "?c=" + encodeURIComponent(campaign));
    }
    showScope();
  }
  const key = names.join("|");
  if (camp.dataset.key !== key) {
    camp.dataset.key = key;
    camp.innerHTML = names.map(n => `<option>${esc(n)}</option>`).join("");
  }
  camp.value = campaign;
}

camp.onchange = () => enterCampaign(camp.value);
document.getElementById("home").onclick = () => { if (scope !== "lab") goLab(); };

document.querySelector("#say button").onclick = say;
document.getElementById("msg").addEventListener(
  "keydown", e => { if (e.key === "Enter") say(); });

showScope();
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

    def _send(self, body, ctype="text/plain; charset=utf-8"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # Everything here is live: the page is generated from code that changes under
        # the viewer, and the JSON behind it changes every second. Nothing carries a
        # validator, so without this a browser is free to reuse a copy from before the
        # last edit -- and a restart of the watcher does not dislodge it.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        """The one write: a message for the agent. It lands on the board the agent reads
        between turns, and in the conversation the page shows."""
        url = urllib.parse.urlparse(self.path)
        if url.path not in ("/say", "/services"):
            self.send_error(404)
            return
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
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
        # `?lab` carries no value, and a query key without one is dropped unless
        # blanks are kept.
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        campaign = self._campaign(q)
        if url.path == "/":
            # The page opens where the address says, so a bookmark is showing the right
            # place before the first poll rather than a moment after it.
            self._send(PAGE % {"campaign": html.escape(campaign),
                               "campaign_json": json.dumps(campaign),
                               "scope_json": json.dumps(self._scope(q))},
                       "text/html; charset=utf-8")
        elif url.path == "/campaigns":
            self._send(json.dumps(self._choices()), "application/json")
        elif url.path == "/labchat":
            self._send(json.dumps({"text": lab_messages(),
                                   "secretary": secretary_live(),
                                   "slack": slack_attached()}),
                       "application/json")
        elif url.path == "/services":
            self._send(json.dumps(lab_services()), "application/json")
        elif url.path == "/users":
            self._send(json.dumps(lab_users()), "application/json")
        elif url.path == "/lab":
            # Ordered as the lab reads: what is running first, then by name.
            rows = [lab_summary(c) for c in self._choices()]
            rows.sort(key=lambda r: (not r.get("live"), r["campaign"]))
            self._send(json.dumps(rows), "application/json")
        elif url.path == "/log":
            self._send(json.dumps(
                self._log_from(campaign, int(q.get("from", ["0"])[0] or 0))),
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
    url = f"http://127.0.0.1:{port}/"
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


if __name__ == "__main__":
    main()
