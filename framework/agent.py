#!/usr/bin/env python3
"""
CAS: an agent that searches a domain by running work on HPC via Globus Compute.

System prompt: prompt.md (the goal) + method.md (how to work)
User prompt:   user_prompt.md (initial task — editable without touching this file)

Usage:
    python agent.py
"""

import asyncio
import glob
import json
import re
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Resolved and exported BEFORE importing tools: tools.py reads WORKSPACE_DIR at import
# time and falls back to its own directory, which puts claims.jsonl, jobs.jsonl and
# ANNOUNCEMENTS.md in framework/ instead of the campaign's workspace.
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR") or (
    os.path.join(os.path.abspath(os.environ.get("LAB_DIR",
        os.path.join(SCRIPT_DIR, ".."))), "workspace", os.environ["CAMPAIGN"])
    if os.environ.get("CAMPAIGN") else SCRIPT_DIR)
os.environ["WORKSPACE_DIR"] = WORKSPACE_DIR

import critic  # noqa: E402
import tools  # noqa: E402
from tools import create_server, shutdown_executor  # noqa: E402
# Campaign files (prompt.md, user prompt) live with the campaign, not the framework.
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
CAMPAIGN = os.environ.get("CAMPAIGN", "")
CAMPAIGN_DIR = os.path.abspath(os.environ.get(
    "CAMPAIGN_DIR", os.path.join(LAB_DIR, "campaigns", CAMPAIGN) if CAMPAIGN else SCRIPT_DIR))
SYSTEM = tools.SYSTEM          # from the campaign's campaign.json
ROLE = os.environ.get("ROLE", "both")
# Roles only mean something when a campaign splits work between agents. Left unset,
# they are noise in anything a person reads, so they are shown only when set.
ROLE_SET = bool(os.environ.get("ROLE"))
ROLE_NOTE = f" ({ROLE})" if ROLE_SET else ""
LOG_DIR = os.path.join(WORKSPACE_DIR, "logs")
USER_PROMPT_FILE = os.environ.get("USER_PROMPT_FILE", "user_prompt.md")

# One timestamp per process, shared by the log file and this run's directory so the
# two line up. RUN_ID names the run dir and is what kill_agent.sh targets.
RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_ID = f"{SYSTEM}_{ROLE}_{RUN_STAMP}" if ROLE_SET else f"{SYSTEM}_{RUN_STAMP}"
RUN_DIR = os.path.join(WORKSPACE_DIR, "runs", RUN_ID)
os.environ["RUN_ID"] = RUN_ID      # tools stamps the job log with it
LOG_PATH = os.path.join(LOG_DIR, f"run_{SYSTEM}_{RUN_STAMP}.log")
HEARTBEAT_INTERVAL = 30   # s; minimum gap between heartbeat writes during a wait
CRITIC_MODEL = None       # resolved in preflight()
CRITIC_LABEL = "no critic"
# What a cycle gets written up in depends on the method: the standard one records to
# LOGBOOK.md and writes JOURNAL.md only at the end, the research one keeps both as it
# goes. The critic watches both and reviews whichever grew.
CYCLE_RECORDS = [os.path.join(WORKSPACE_DIR, name)
                 for name in ("LOGBOOK.md", "JOURNAL.md")]

AGENT_ALIVE_WITHIN = int(os.environ.get("AGENT_ALIVE_WITHIN", "300"))  # s; fresher heartbeat = agent is up


def _live_handles():
    """(handle, campaign) for every agent running anywhere in the lab right now, from
    their meta.json. A handle is only held while its agent is alive, so both slugs and
    numbers are reused once a run ends."""
    out = []
    now = time.time()
    root = os.path.dirname(WORKSPACE_DIR)      # workspace/, one dir per campaign
    for hb in glob.glob(os.path.join(root, "*", "runs", "*", "heartbeat")):
        try:
            with open(hb) as f:
                if now - float(f.read().strip()) > AGENT_ALIVE_WITHIN:
                    continue
            with open(os.path.join(os.path.dirname(hb), "meta.json")) as f:
                meta = json.load(f)
        except Exception:
            continue          # unreadable run: treat its handle as free
        if meta.get("handle"):
            out.append((meta["handle"], meta.get("campaign", "")))
    return out


def _allocate_handle():
    """The short name a person uses to mean this agent -- "get a report from epez1".

    RUN_ID is exact but too long to say, and a campaign/system pair is not one word.
    So: a slug of the campaign name plus a number, unique across every agent running
    in the lab, which is what makes it usable on its own in Slack.

    The slug grows only when two campaigns would otherwise collide, and the number is
    the lowest free one, so the common case stays as short as it can be."""
    live = _live_handles()
    name = "".join(c for c in (CAMPAIGN or SYSTEM).lower() if c.isalnum() or c in "-_")
    words = [w for w in re.split(r"[-_]", name) if w] or ["agent"]
    # First word, then as much of the rest as it takes to stop looking like another
    # campaign's agents. Never shortened: "local" reads as the campaign, "loca" does not.
    slugs = [words[0][:8]]
    for w in words[1:]:
        slugs.append((slugs[-1] + w)[:10])
    slugs.append(name.replace("-", "").replace("_", "")[:12])
    for slug in slugs:
        if any(h.rstrip("0123456789") == slug and c != CAMPAIGN for h, c in live):
            continue          # another campaign already answers to this slug
        for n in range(1, 100):
            cand = f"{slug}{n}"
            if cand not in {h for h, _ in live}:
                return cand
    return f"{words[0][:8]}{RUN_STAMP[9:]}"


HANDLE = _allocate_handle()
# Prefixed to this agent's Slack posts by slack_notify.sh. The handle alone, because
# it is unique across the lab and is what someone types to address this agent.
os.environ.setdefault("SLACK_PREFIX", HANDLE)
# Turns given to the agent AFTER everything has drained, so it can write the
# journal/LOGBOOK before the process exits.
MAX_FINALIZE_ROUNDS = 2

# The agent runs as ONE stateful conversation (ClaudeSDKClient), so it keeps all
# prior context and reasoning across turns. Each turn it acts on whatever jobs
# have finished; then agent.py waits Python-side (off the event loop) for the next
# job to complete and nudges the same conversation onward. Remote jobs keep
# running and are never cancelled by a turn ending.
_COMPLETED_TOOL = "get_completed_jobs" if tools.HAS_REMOTE else "get_local_completed"
CONTINUE_PROMPT = (
    f"One or more jobs have finished. Collect them with {_COMPLETED_TOOL}, "
    "fit and log each, then continue exploring: pick the next promising region "
    "from your results and submit it. A good result means probe nearby, not stop."
)
EXPLORE_PROMPT = (
    "No jobs are running. Using results.jsonl and your LOGBOOK.md notes, choose "
    "the next promising region to probe and submit it. Keep "
    "exploring -- do not stop because earlier configs finished or did well."
)
WINDDOWN_PROMPT = (
    "Wind-down requested: this run is ending. Submit no new work -- the submit tools "
    "will refuse it. Collect and log the jobs already in flight as they finish. Once "
    "everything is collected you get a final turn to write up the cycle."
)
FINALIZE_PROMPT = (
    # Which records a cycle is written up in is the method's business, not the
    # runner's: naming a file here produces one that the method never asked for.
    "All outstanding work is collected and this run is now ending. Close out the "
    "current cycle: write it up in the records your method keeps, and note anything a "
    "later run needs to pick up where you left off. Submit no new work."
)
# A session id to start from. Its whole conversation becomes this run's context, which
# costs what it costs and brings any stale conclusions with it, so it is off by default.
# The session must belong to this user on this machine.
RESUME_SESSION = (os.environ.get("RESUME_SESSION") or "").strip()
MAX_ROUNDS = 500          # backstop against a runaway loop
MAX_EMPTY_ROUNDS = 3      # consecutive idle rounds (no work proposed) before giving up
MAX_RUNTIME = int(os.environ["MAX_RUNTIME"]) if os.environ.get("MAX_RUNTIME") else None  # total agent wallclock (s); None = no time limit
WAIT_TIMEOUT = 1800       # s between "still-alive" logs / backend-health checks during a wait
ANNOUNCE_POLL = int(os.environ.get("ANNOUNCE_POLL", "2"))   # s between announcement-board checks during a job wait
STALL_LIMIT = int(os.environ["STALL_LIMIT"]) if os.environ.get("STALL_LIMIT") else None  # None = wait indefinitely (HPC queues can take many hours); set seconds to cap (tests do)

# --- Slack notifications (optional; see SLACK_NOTIFY.md). Missing webhook/script
# or a failed post is ignored so a run is never affected. ---
def _bool_env(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")

# A browser view of this run: the log as it is written and the files it writes. Off
# unless asked for, and it never affects the run -- it only reads the workspace.
WATCH = _bool_env("WATCH", False)
WATCH_PORT = int(os.environ.get("WATCH_PORT", "8765"))
# The viewer outlives the run -- the end of a run is when its records are worth reading
# -- and stops itself once nobody has looked for this long.
WATCH_IDLE = int(os.environ.get("WATCH_IDLE", "600"))
_watcher = None           # the viewer process, stopped when the run ends

NOTIFY_START = _bool_env("NOTIFY_START", False)
NOTIFY_DAILY = _bool_env("NOTIFY_DAILY", True)
NOTIFY_FINISH = _bool_env("NOTIFY_FINISH", True)
DAILY_INTERVAL = int(os.environ.get("NOTIFY_DAILY_INTERVAL", "86400"))  # seconds between periodic summaries
PROBLEM_GRACE = int(os.environ.get("NOTIFY_PROBLEM_GRACE", "1800"))     # shut down this long (s) after the agent flags an unresolved blocking problem
NOTIFY_SCRIPT = os.environ.get("NOTIFY_SCRIPT") or os.path.join(SCRIPT_DIR, "slack_notify.sh")

# When a periodic summary is due, the runner asks the agent to write it (its own
# words) via the notify tool, instead of a fixed harness string.
REPORT_PROMPT = (
    "Before anything else this turn, post a brief (1-2 line) status summary to Slack "
    "with the notify tool: what you are currently working on, recent progress, and any "
    "concern. Then continue as normal."
)


def slack_notify(msg):
    if not os.path.isfile(NOTIFY_SCRIPT):
        return
    try:
        subprocess.run(["bash", NOTIFY_SCRIPT, msg], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[slack_notify] failed (ignored): {e}", flush=True)


def _fmt_uptime(secs):
    secs = int(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# How long to let the context call run before giving up on it. A campaign with short
# rounds can set a shorter one, so the figures are either current or absent.
CONTEXT_TIMEOUT = float(os.environ.get("CONTEXT_TIMEOUT", "60"))


async def _context_usage(client):
    """The /context figures -- model, tokens, window, pct. None if it is slow or fails."""
    try:
        return await asyncio.wait_for(client.get_context_usage(), CONTEXT_TIMEOUT)
    except asyncio.TimeoutError:
        print("[context] no data", flush=True)
        return None
    except Exception as e:
        print(f"[context] no data ({e})", flush=True)
        return None


_context_task = None        # the in-flight context lookup, if any


def _refresh_context(client):
    """Ask for the context figures without making the round wait for them. One lookup
    at a time: while one is running, later rounds go without rather than queue."""
    global _context_task
    if _context_task is not None and not _context_task.done():
        return
    _context_task = asyncio.create_task(_record_context(client))


async def _record_context(client):
    """Record what the context call returns, whenever it returns."""
    ctx = _as_context(await _context_usage(client))
    if not ctx:
        return
    _last_context.update(ctx)
    _write_meta(context_tokens=ctx["tokens"], context_window=ctx["window"],
                context_pct=ctx["pct"], **({"model": ctx["model"]} if ctx["model"] else {}))


def _as_context(usage):
    """The /context answer in the shape the rest of the run records."""
    if not usage or usage.get("totalTokens") is None:
        return None
    return {"tokens": usage.get("totalTokens"), "window": usage.get("rawMaxTokens"),
            "pct": usage.get("percentage"), "model": usage.get("model")}


_last_context = {}          # tokens/window/pct/model, from the most recent turn


async def _post_scheduled_status(client, round_num, start_time):
    """Post the fixed-metrics scheduled status line to Slack (harness-owned, deterministic)."""
    u = _last_context
    model = u.get("model") or "?"
    tok, win, pct = u.get("tokens"), u.get("window"), u.get("pct")
    ctx = (f"ctx ~{tok}/{win} (~{pct:.0f}%)"
           if tok is not None and win and pct is not None else "ctx n/a")
    slack_notify(f":calendar: Scheduled Status — {model}, "
                 f"round {round_num} · {tools.submit_count()} remote / {tools.local_submit_count()} local "
                 f"this session · {tools.jobs_in_flight()} in-flight · {ctx} · "
                 f"uptime {_fmt_uptime(time.time() - start_time)}")


def _new_board_lines(seen, current):
    """Lines added to the board since it was last looked at. Falls back to the whole
    board if it was edited rather than appended to, since there is no clean 'new
    part' then. Without this, any change re-sends every old message and the agent
    re-acts on things it already handled."""
    old, new = seen.splitlines(), current.splitlines()
    if new[:len(old)] == old:
        return "\n".join(new[len(old):]).strip()
    return current


def _record_texts():
    out = {}
    for path in CYCLE_RECORDS:
        try:
            with open(path) as f:
                out[path] = f.read()
        except Exception:
            out[path] = ""
    return out


def _new_record_text(before, after):
    """The text added to the cycle records since the last look, ignoring a file that
    was edited rather than appended to -- there is no clean 'new part' then."""
    added = []
    for path, text in after.items():
        old = before.get(path, "")
        if len(text) > len(old) and text.startswith(old):
            chunk = text[len(old):].strip()
            if chunk:
                added.append(f"--- new in {os.path.basename(path)} ---\n{chunk}")
    return "\n\n".join(added)


def _recent_results(budget=120000):
    """The rows a claim can be checked against: all of them if they fit, the most recent
    otherwise. A slice is labelled as one -- a critic that cannot tell a missing row from
    a missing measurement calls everything unsupported."""
    try:
        with open(os.path.join(WORKSPACE_DIR, "results.jsonl")) as f:
            rows = f.readlines()
    except Exception:
        return ""
    kept, size = [], 0
    for row in reversed(rows):
        size += len(row)
        if size > budget and kept:
            break
        kept.append(row)
    kept.reverse()
    head = (f"({len(rows)} rows recorded; all supplied)\n" if len(kept) == len(rows)
            else f"({len(rows)} rows recorded, the {len(kept)} most recent supplied)\n")
    return head + "".join(kept)


def _append_review(reply):
    """Keep the review beside the work it judged. Notes the agent never acts on still
    belong in the record, and a later reader can see what was checked."""
    try:
        with open(os.path.join(WORKSPACE_DIR, "REVIEWS.md"), "a") as f:
            f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                    f"-- {CRITIC_LABEL}, run {RUN_ID}\n\n{reply}\n")
    except Exception as e:
        print(f"[critic] could not record the review (ignored): {e}", flush=True)


def _critic_prompt(findings, reply, tail=""):
    """Blocking findings become the next turn's work. The agent is not told to agree:
    a critic reading only the rows can be wrong about what the rows mean, and saying so
    with evidence is a legitimate answer."""
    listed = "\n".join(f"- {claim} ({verdict})" for claim, verdict in findings)
    return (f"The critic ({CRITIC_LABEL}) reviewed your latest journal section and "
            f"found claims it says the recorded results do not support:\n\n"
            f"{listed}\n\nIts full review:\n\n{reply}\n\n"
            "Deal with each one before continuing: correct the write-up, run what "
            "would settle it, or answer the objection in the journal citing the rows "
            "that support you.\n\n" + tail)


def _announcements_prompt(text, tail=""):
    """Wrap NEW announcements-board lines as the next turn's prompt."""
    body = ("New on the shared announcements board:\n" + text +
            "\nAct on anything here that concerns you. Anything marked as already "
            "answered by the secretary needs no reply from you. If it needs immediate "
            "action, take it now; otherwise acknowledge it briefly and continue. "
            "Pending jobs remain tracked.")
    return body + ("\n\n" + tail if tail else "")


class Tee:
    """Write to both a file and the original stream."""
    def __init__(self, log_file, stream):
        self.log_file = log_file
        self.stream = stream

    def write(self, data):
        self.stream.write(data)
        self.log_file.write(data)

    def flush(self):
        self.stream.flush()
        self.log_file.flush()


def load_prompt():
    with open(os.path.join(CAMPAIGN_DIR, "prompt.md")) as f:
        return f.read()


def method_path():
    """How the agent works, and what records it keeps. Setup copies one of `methods/`
    into the campaign, so each campaign owns its own and can change it. The library
    default applies to a campaign created before this, or one whose copy is missing."""
    campaign_copy = os.path.join(CAMPAIGN_DIR, "method.md")
    return (campaign_copy if os.path.isfile(campaign_copy)
            else os.path.join(LAB_DIR, "methods", "standard.md"))


def load_method():
    with open(method_path()) as f:
        return f.read()


def load_framework():
    """How a run works whatever method it follows: the tools, the records the runner
    reads, and the two ways a run ends. Not copied into a campaign -- a campaign owns
    its method, but the framework it runs in is the framework's to state."""
    with open(os.path.join(SCRIPT_DIR, "framework_prompt.md")) as f:
        return f.read()


def load_user_prompt():
    with open(os.path.join(CAMPAIGN_DIR, USER_PROMPT_FILE)) as f:
        return f.read()


# What the agent works with: its own job tools, and the Claude Code tools that suit a
# run nobody is watching. Reading, writing, editing, shell, skills, the web, and Agent
# -- a campaign should reach a facility's own procedures, look up what a library's
# defaults actually are, and hand a long read to a subagent, the way anyone else would.
# Agent is here rather than behind a setting because delegating is the agent's call to
# make, and a prompt asking for it should work without the campaign being configured
# for it first.
#
# Left out are the tools that belong to an interactive session rather than a campaign:
# the CLI's messaging and scheduling, where this run posts through its own notify tool
# and the runner owns its loop, and the ones that fork work out from under the
# framework's bookkeeping. WebSearch is left out too: it runs on the API server rather
# than here, so a gateway that does not carry server tools refuses it, and some sites
# are behind one. AGENT_TOOLS changes this set.
BASE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill",
              "WebFetch", "Agent"]
AGENT_TOOLS = os.environ.get("AGENT_TOOLS", "").strip()


def claude_tools():
    """The Claude Code tools this run gives the agent.

    AGENT_TOOLS unset leaves the defaults. Entries all prefixed + or - adjust them;
    entries with no prefix are the set, for a campaign that wants to say exactly what
    it works with. Mixing the two forms is refused rather than guessed at, since either
    reading of "Read -Bash" silently loses something the campaign asked for.
    """
    entries = [t for t in re.split(r"[,\s]+", AGENT_TOOLS) if t]
    if not entries:
        return list(BASE_TOOLS)
    signed = [e for e in entries if e[0] in "+-"]
    if signed and len(signed) != len(entries):
        raise ValueError("AGENT_TOOLS mixes +/- adjustments with plain tool names; "
                         "use one form or the other")
    if not signed:
        return list(dict.fromkeys(entries))
    out = list(BASE_TOOLS)
    for e in entries:
        name = e[1:]
        if not name:
            raise ValueError(f"AGENT_TOOLS entry '{e}' names no tool")
        if e[0] == "+":
            if name not in out:
                out.append(name)
        elif name in out:
            out.remove(name)
    return out


def agent_tools():
    """Every tool this run is given, job tools first.

    The job tools are not part of the choice: they come from what the campaign's own
    task.py defines, and a run without them cannot submit anything.
    """
    return list(dict.fromkeys(tools.tool_names() + claude_tools()))


# Which model the agent runs as. Unset leaves it to Claude Code. Set it to test a
# campaign on a cheaper model before giving a machine to a long run. An alias
# ('sonnet', 'opus') or a full model name; which ones work depends on the lab's gateway.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "").strip()
RESOLVED_MODEL = ""      # what the agent will actually run as; filled in by preflight


# One worked example of delegating: a subagent that reads a long record and returns
# what the run needs from it, so the record itself never enters the main agent's
# context. It is offered, not imposed -- the agent may use it, use a built-in type, or
# not delegate at all, and a campaign prompt can ask for something else.
#
# The whole definition lives in one file, front matter and prompt, in the form a
# subagent is normally written in. It is read here rather than from `.claude/agents/`
# because the SDK only loads that directory when `setting_sources` includes the
# project, which would bring the rest of a project's settings with it.
SUBAGENT_DIR = os.environ.get("SUBAGENT_DIR") or SCRIPT_DIR
SUBAGENT_FILES = ["subagent_reader.md"]


def _parse_subagent(path):
    """A front-matter subagent file -> (name, AgentDefinition)."""
    with open(path) as f:
        text = f.read()
    if not text.startswith("---"):
        raise ValueError(f"{path}: no front matter")
    _, front, body = text.split("---", 2)
    meta = {}
    for line in front.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    fields = {}
    if meta.get("tools"):
        fields["tools"] = [t for t in re.split(r"[,\s]+", meta["tools"]) if t]
    if meta.get("model"):
        fields["model"] = meta["model"]
    if "background" in meta:
        # A blocking call puts the answer in the tool result, so the turn that asked
        # can act on it. A background call returns a launch stub and the answer lands
        # some turns later.
        fields["background"] = meta["background"].lower() == "true"
    return meta["name"], AgentDefinition(description=meta["description"],
                                         prompt=body.strip(), **fields)


def subagent_defs():
    """Subagent types this run offers, or None if none could be read."""
    defs = {}
    for fname in SUBAGENT_FILES:
        path = os.path.join(SUBAGENT_DIR, fname)
        try:
            name, d = _parse_subagent(path)
        except (OSError, ValueError, KeyError) as e:
            print(f"[subagent] skipped {fname}: {e}", flush=True)
            continue
        defs[name] = d
    return defs or None


# --- Run directory: one permanent dir per run -----------------------------------
# Holds this run's metadata and a snapshot of the prompt files it actually used, so
# a run stays reproducible after the prompts change. It is NEVER deleted -- it is the
# run history. Liveness is the heartbeat file inside it, not the dir existing.
# The stop file also lives here, so it is scoped to this run: a restart gets a fresh
# dir and can never inherit a stale stop request.
# What a tool call means for someone watching. The tool name says which function was
# called; a phase says what the run is doing.
_PHASES = {
    "submit_job": "submitting jobs", "submit_local": "submitting jobs",
    "get_completed_jobs": "collecting results", "get_local_completed": "collecting results",
    "check_backend": "checking the backend", "release_claim": "releasing a claim",
    "notify": "posting to Slack", "cycle_done": "closing the cycle",
    "Read": "reading records", "Grep": "reading records", "Glob": "reading records",
    "Write": "writing up", "Edit": "writing up", "NotebookEdit": "writing up",
    "Bash": "running analysis",
}
# Which subagent each Agent call started, keyed by the call's id, so a turn arriving
# from a subagent can be named after it. These labels describe the agent's own work,
# and a subagent is doing a different job with the same tools.
_DELEGATES = {}


def _set_phase(text):
    """What the run is doing right now, for anything watching it. A turn can be minutes
    of silence, and "thinking" and "waiting for jobs" look identical from outside."""
    try:
        tmp = os.path.join(RUN_DIR, "phase.tmp")
        with open(tmp, "w") as f:
            f.write(f"{time.time():.0f}\n{text}\n")
        os.replace(tmp, os.path.join(RUN_DIR, "phase"))
    except Exception:
        pass


def _start_watcher():
    """Serve this run for a browser, if asked. Failing to start one is not a reason to
    lose a run, so a failure is reported and the run carries on."""
    global _watcher
    if not WATCH:
        return
    try:
        # Find the free port here rather than letting the viewer shift to one, so the
        # address printed is the address it is on.
        port = WATCH_PORT
        for candidate in range(WATCH_PORT, WATCH_PORT + 20):
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", candidate)) != 0:
                    port = candidate
                    break
        # Its own log, so a viewer that fails to start says why instead of vanishing.
        # Preflight runs before the run directory exists, so make it.
        os.makedirs(RUN_DIR, exist_ok=True)
        watch_log = open(os.path.join(RUN_DIR, "watch.log"), "w")
        _watcher = subprocess.Popen(
            [sys.executable, os.path.join(SCRIPT_DIR, "watch.py"), CAMPAIGN,
             "--port", str(port), "--no-open", f"--exit-when-idle={WATCH_IDLE}"],
            stdout=watch_log, stderr=subprocess.STDOUT)
        print(f"watch: http://127.0.0.1:{port}/", flush=True)
    except Exception as e:
        print(f"[watch] could not start the viewer (ignored): {e}", flush=True)


def _stop_watcher():
    """Left running on purpose: the end of a run is when its records are worth reading,
    and the viewer stops itself once nobody has looked for a while."""
    if _watcher is None:
        return
    print("watch: still serving; it stops itself when nobody is looking", flush=True)


def _write_meta(**updates):
    """Merge fields into this run's meta.json. Best-effort: never breaks a run."""
    path = os.path.join(RUN_DIR, "meta.json")
    try:
        with open(path) as f:
            meta = json.load(f)
    except Exception:
        meta = {}
    meta.update(updates)
    try:
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print(f"[run] meta.json write failed (ignored): {e}", flush=True)


def _heartbeat(force=True):
    """Record that this process is alive. Liveness = a RECENT heartbeat, which goes
    stale by itself if the process dies for any reason (crash, SIGHUP, kill -9) --
    unlike a marker file, which is only removed on a clean exit and so lingers
    after a crash, reporting a dead agent as running."""
    global _last_heartbeat
    now = time.time()
    if not force and now - _last_heartbeat < HEARTBEAT_INTERVAL:
        return
    _last_heartbeat = now
    try:
        with open(os.path.join(RUN_DIR, "heartbeat"), "w") as f:
            f.write(str(int(now)))
    except Exception:
        pass


_last_heartbeat = 0.0


def _start_run_dir():
    os.makedirs(RUN_DIR, exist_ok=True)
    for path in (os.path.join(CAMPAIGN_DIR, "prompt.md"), method_path(),
                 os.path.join(CAMPAIGN_DIR, USER_PROMPT_FILE)):
        name = os.path.basename(path)
        try:
            shutil.copy2(path, os.path.join(RUN_DIR, name))
        except Exception as e:
            print(f"[run] could not snapshot {name} (ignored): {e}", flush=True)
    # The budgets this run stops at, recorded so anything reading the run -- a watcher,
    # a later reader -- can say how far through it is without knowing the environment
    # it was launched in.
    _write_meta(max_submits=tools.MAX_SUBMITS, max_runtime_s=MAX_RUNTIME,
                max_rounds=MAX_ROUNDS, critic=CRITIC_LABEL,
                run_id=RUN_ID, handle=HANDLE, system=SYSTEM, role=ROLE,
                started_by=os.environ.get("STARTED_BY", ""),
                host=socket.gethostname(), pid=os.getpid(),
                started_at=datetime.now().isoformat(timespec="seconds"),
                user_prompt_file=USER_PROMPT_FILE,
                campaign=CAMPAIGN,
                shared_dir=WORKSPACE_DIR, log=LOG_PATH, status="running")
    _heartbeat()
    print(f"Run dir: {RUN_DIR}", flush=True)


async def _heartbeat_loop():
    """Write the heartbeat on a timer, independent of where the round loop is.
    Needed because a turn (client.query -> tool calls -> reply) can run for minutes
    with no natural place to beat, which would make a healthy agent look dead. The
    turn is awaited, so the event loop is free and this keeps ticking through it."""
    while True:
        _heartbeat()
        await asyncio.sleep(HEARTBEAT_INTERVAL)


def _stop_file_present():
    return os.path.exists(os.path.join(RUN_DIR, "stop"))


# PREFLIGHT runs the checks and stops, for someone deciding whether a campaign is
# ready before giving a machine to it for days. It leaves no trace of a run: the log is
# not opened, so the last run's log survives, and no watcher or Slack post is made.
# The environment variable is the form campaigns use, since it reaches here whatever a
# run.sh looks like; --preflight is accepted too, for running this file directly.
CHECK_ONLY = (os.environ.get("PREFLIGHT", "").lower() in ("1", "true", "yes")
              or "--preflight" in sys.argv)


GATEWAY_URL = None          # set when this run is routed through the lab's gateway


def _on_lab_gateway():
    """Whether the agent itself is talking to the lab's gateway.

    Compared by URL, not by asking what the gateway serves: it may not be up yet,
    which is the case this answers.
    """
    lab = (os.environ.get("CRITIC_BASE_URL") or "").rstrip("/")
    mine = (os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    return bool(lab and mine and lab == mine)


def _route_to_gateway():
    """Send the agent through the lab's gateway when AGENT_MODEL is a model it serves.

    Which models it serves is read from its config file rather than asked of it: it
    may not be running, and whether to start it is what this decides. A name in that
    file is only reachable through the gateway, so it settles where to send the run
    whatever ANTHROPIC_BASE_URL happens to say -- that is usually a person's shell
    pointing at their usual endpoint, which does not serve this model.
    """
    url = (os.environ.get("CRITIC_BASE_URL") or "").rstrip("/")
    conf = os.environ.get("LITELLM_CONFIG") or ""
    if not AGENT_MODEL or not url or not conf:
        return
    try:
        with open(conf) as f:
            served = re.findall(r"^\s*-?\s*model_name:\s*(\S+)", f.read(), re.M)
    except OSError:
        return
    if AGENT_MODEL in served:
        global GATEWAY_URL
        GATEWAY_URL = url
        if (os.environ.get("ANTHROPIC_BASE_URL") or "").rstrip("/") == url:
            return
        # The critic and the framework's own checks read this; the CLI does not, which
        # _gateway_settings_file handles.
        os.environ["ANTHROPIC_BASE_URL"] = url
        print(f"gateway: {AGENT_MODEL} is served by {url} — routing the agent there",
              flush=True)


def _gateway_settings_file():
    """A settings file pointing Claude Code at the gateway, or None.

    The CLI takes ANTHROPIC_BASE_URL from Claude Code's own settings in preference to
    the environment it was started in, so a person whose settings name their usual
    endpoint would go there whatever this process sets. A file passed as --settings
    outranks both.
    """
    if not GATEWAY_URL:
        return None
    os.makedirs(RUN_DIR, exist_ok=True)
    path = os.path.join(RUN_DIR, "gateway_settings.json")
    env = {"ANTHROPIC_BASE_URL": GATEWAY_URL}
    if os.environ.get("ANTHROPIC_API_KEY"):
        env["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    with open(path, "w") as f:
        json.dump({"env": env}, f)
    return path


def _probe_model():
    """The model this run will actually use. AGENT_MODEL when a campaign names one;
    otherwise the CLI's own default, which only the CLI knows -- and which the critic
    needs, since it picks a family different from the agent's."""
    if AGENT_MODEL:
        return AGENT_MODEL

    async def _ask():
        opts = ClaudeAgentOptions(
            cwd=SCRIPT_DIR,
            **({"settings": _gateway_settings_file()} if GATEWAY_URL else {}))
        async with ClaudeSDKClient(options=opts) as c:
            return (await _context_usage(c) or {}).get("model")

    try:
        return asyncio.run(_ask()) or ""
    except Exception as e:
        print(f"[model] could not ask the CLI which model it runs ({e})", flush=True)
        return ""


def preflight():
    """Verify everything this run needs BEFORE starting. Fail fast with a clear
    message instead of discovering a missing piece mid-run and spinning."""
    problems = []
    # The task plug-in must satisfy the contract before anything else is tried. A task
    # supplies remote jobs, local jobs, or both, so each set is required only when the
    # task offers it -- and at least one of them must be there.
    required = []
    if tools.HAS_REMOTE:
        required += ["JOB_DESC", "JOB_SCHEMA", "job_key", "remote_fn"]
    if tools.HAS_LOCAL:
        required += ["LOCAL_DESC", "LOCAL_SCHEMA", "local_fn"]
    if not required:
        problems.append(f"task {tools.TASK_DIR} defines neither 'remote_fn' nor "
                        f"'local_fn' (see AGENTS.md)")
    for attr in required:
        if not hasattr(tools.task, attr):
            problems.append(f"task {tools.TASK_DIR} is missing '{attr}' "
                            f"(see AGENTS.md)")
    # A task may declare its own checks -- e.g. that its binary is where it expects.
    if hasattr(tools.task, "preflight"):
        try:
            problems += list(tools.task.preflight() or [])
        except Exception as e:
            problems.append(f"task preflight() raised: {e}")
    try:
        claude_tools()
    except ValueError as e:
        problems.append(str(e))
    if not os.path.isfile(method_path()):
        problems.append(f"method.md missing: {method_path()} (how-to-work prompt loaded into the agent)")
    _fw = os.path.join(SCRIPT_DIR, "framework_prompt.md")
    if not os.path.isfile(_fw):
        problems.append(f"framework_prompt.md missing: {_fw}")
    # Fail fast if the Globus Compute endpoint is not online -- otherwise every
    # submit fails with ENDPOINT_NOT_ONLINE and the run does nothing. A task with no
    # remote_fn never reaches the endpoint, so there is nothing to probe.
    if tools.HAS_REMOTE:
        try:
            import globus_compute_sdk as _gc
            _c = _gc.Client()
            _st = _c.get_endpoint_status(tools.ENDPOINT_ID).get("status")
            if _st != "online":
                try:
                    _nm = _c.get_endpoint_metadata(tools.ENDPOINT_ID).get("name") or tools.ENDPOINT_ID
                except Exception:
                    _nm = tools.ENDPOINT_ID
                if not CHECK_ONLY:
                    slack_notify(f":rotating_light: Agent exiting -- Globus Compute "
                                 f"endpoint '{_nm}' is not online (status={_st}). Start it: "
                                 f"globus-compute-endpoint start {_nm} --detach")
                problems.append(f"Globus Compute endpoint '{_nm}' ({tools.ENDPOINT_ID}) is not online "
                                f"(status={_st}); start it: globus-compute-endpoint start {_nm} --detach")
        except Exception as e:
            print(f"[preflight] WARNING: could not query endpoint status ({tools.ENDPOINT_ID}): {e}", flush=True)
    try:
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        _t = os.path.join(WORKSPACE_DIR, ".preflight_write_test")
        with open(_t, "w"):
            pass
        os.remove(_t)
    except Exception as e:
        problems.append(f"WORKSPACE_DIR not writable ({WORKSPACE_DIR}): {e}")
    if problems:
        print("PREFLIGHT FAILED - cannot run. Fix these and restart:", flush=True)
        for pr in problems:
            print(f"  - {pr}", flush=True)
        sys.exit(1)
    backend = "endpoint online" if tools.HAS_REMOTE else "local execution only"
    print(f"preflight OK: task={tools.TASK_DIR}, method.md, WORKSPACE_DIR, {backend}.", flush=True)
    # The gateway converts between the Messages API and a backend that does not speak
    # it. The agent needs it whenever it is pointed at one, whether or not there is a
    # critic: a run on a non-Anthropic model goes through the same proxy.
    global CRITIC_MODEL, CRITIC_LABEL
    _route_to_gateway()
    note = critic.ensure_gateway(needed=_on_lab_gateway())
    if note:
        print(f"gateway: {note}", flush=True)
    # The critic is resolved here rather than at first use: a campaign that needs its
    # cycles reviewed should fail now, not in round twelve.
    global RESOLVED_MODEL
    RESOLVED_MODEL = _probe_model()
    try:
        CRITIC_MODEL, CRITIC_LABEL = critic.resolve(
            RESOLVED_MODEL or os.environ.get("ANTHROPIC_MODEL", ""))
    except critic.CriticUnavailable as e:
        print(f"preflight FAILED: {e}", flush=True)
        sys.exit(1)
    print(f"critic: {CRITIC_LABEL}", flush=True)
    # The names, not a count: what a campaign is actually given is otherwise only
    # discoverable by reading the framework. Split in two because the halves are
    # decided by different things -- the job tools by what the task defines, the rest
    # by the framework's defaults and AGENT_TOOLS.
    given = agent_tools()
    job = [t.rsplit("__", 1)[-1] for t in given if t.startswith("mcp__")]
    claude = [t for t in given if not t.startswith("mcp__")]
    print(f"job tools:    {' '.join(job)}", flush=True)
    print(f"claude tools: {' '.join(claude)}", flush=True)
    print(f"model:        {RESOLVED_MODEL or '(could not be determined)'}", flush=True)
    if not CHECK_ONLY:
        _start_watcher()


_session_id = None          # this run's Claude session, for reopening it later


async def drain_turn(client, round_num):
    """Print the assistant's output for one turn (until its ResultMessage), and start
    a context lookup for the status pane, which the turn does not wait for."""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            # A subagent's own turns arrive on this stream as well, carrying the id of
            # the Agent call that started them. Name that subagent in the phase, so a
            # watcher sees who is working rather than the agent's own word for whatever
            # tool the subagent happens to be using.
            parent = getattr(message, "parent_tool_use_id", None)
            who = _DELEGATES.get(parent) if parent else None
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text, flush=True)
                name = getattr(block, "name", None)
                if not name:
                    continue
                bare = name.rsplit("__", 1)[-1]
                if bare == "Agent":
                    sub = (getattr(block, "input", None) or {}).get("subagent_type")
                    if sub:
                        _DELEGATES[getattr(block, "id", None)] = sub
                    _set_phase(f"round {round_num}: waiting on subagent "
                               f"{sub or '(unnamed)'}")
                elif who:
                    _set_phase(f"round {round_num}: subagent {who} ({bare})")
                else:
                    _set_phase(f"round {round_num}: {_PHASES.get(bare, bare)}")
        elif isinstance(message, ResultMessage):
            # The session id first becomes known here. Recorded once, so a finished run
            # can be reopened later with `claude -r <id>` for a postmortem.
            global _session_id
            sid = getattr(message, "session_id", None)
            if sid and sid != _session_id:
                _session_id = sid
                _write_meta(session_id=sid, session_cwd=SCRIPT_DIR)
            print(f"\n[round {round_num} turn end] {message.subtype}", flush=True)
    _refresh_context(client)


async def main():
    system_prompt = load_prompt()
    system_prompt += "\n\n" + load_framework()
    system_prompt += "\n\n" + load_method()
    # What runs at once, which the agent cannot discover except by being refused.
    # Not the job budget: that changes as the run goes, so each submit returns it.
    at_once = []
    if tools.HAS_REMOTE:
        at_once.append(f"jobs running at once: {tools.MAX_CONCURRENT}")
    if tools.HAS_LOCAL:
        at_once.append(f"local jobs running at once: {tools.LOCAL_MAX_CONCURRENT}")
    if MAX_RUNTIME:
        at_once.append(f"wall clock for this run: {MAX_RUNTIME}s")
    system_prompt += ("\n\n# This run\n" + "\n".join(at_once)
                      + "\n\nSubmitting more at once than that queues the rest, which "
                        "tells you nothing sooner. Each submit answers with how much of "
                        "the run's job budget it has used.")
    system_prompt += f"\n\n# This agent\nSYSTEM={SYSTEM}.{f'  ROLE={ROLE}.' if ROLE_SET else ''}\nThe shared files (results.jsonl, LOGBOOK.md, JOURNAL.md, claims.jsonl) live in {WORKSPACE_DIR} \u2014 always read and write them by full path there (e.g. {WORKSPACE_DIR}/results.jsonl). Follow the role rules in the Collaboration section of the prompt."
    server = create_server()

    options = ClaudeAgentOptions(
        mcp_servers={"cas": server},
        # The task plug-in decides which job tools exist (a task with no local
        # comparator does not get the local pair), so take the list from tools.
        # `tools`, not `allowed_tools`: this is the list the agent is given.
        # allowed_tools only says which calls proceed without someone being asked,
        # which decides nothing in a run with nobody there to ask.
        tools=agent_tools(),
        **({"model": AGENT_MODEL} if AGENT_MODEL else {}),
        **({"settings": _gateway_settings_file()} if GATEWAY_URL else {}),
        agents=subagent_defs(),
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        cwd=SCRIPT_DIR,
        # Carry on from a conversation someone already had -- working out what to try
        # with an agent, then handing that reasoning to the run rather than restating
        # it in a prompt. Forked, so the original transcript is left as it was.
        **({"resume": RESUME_SESSION, "fork_session": True} if RESUME_SESSION else {}),
    )

    results_file = os.path.join(WORKSPACE_DIR, "results.jsonl")
    loop = asyncio.get_event_loop()

    if RESUME_SESSION:
        print(f"Resuming from session {RESUME_SESSION} (forked)", flush=True)
    print("Starting agent...", flush=True)
    print(f"Results: {results_file}", flush=True)
    print("=" * 60, flush=True)

    start_time = time.time()
    stop_reason = "ended (max rounds)"

    # PID file keyed by SYSTEM+ROLE so kill_agent.sh can target THIS agent when
    # several run at once. Removed on clean exit.
    run_dir = os.path.join(WORKSPACE_DIR, "run")
    os.makedirs(run_dir, exist_ok=True)
    pid_file = os.path.join(run_dir, f"agent_{SYSTEM}_{ROLE}.pid" if ROLE_SET
                            else f"agent_{SYSTEM}.pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    _start_run_dir()

    # SIGTERM (plain `kill`) is not caught by default, so `finally` would be skipped
    # and no finish ping fired. Cancel the main task instead so shutdown runs. SIGINT
    # (kill -INT / Ctrl-C) already unwinds via KeyboardInterrupt.
    main_task = asyncio.current_task()
    def _on_sigterm():
        nonlocal stop_reason
        stop_reason = "signal (SIGTERM)"
        print("SIGTERM received -- shutting down gracefully.", flush=True)
        main_task.cancel()
    try:
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
    except (NotImplementedError, RuntimeError):
        pass

    beat_task = asyncio.create_task(_heartbeat_loop())

    try:
        async with ClaudeSDKClient(options=options) as client:
            # Before any turn has run: what the prompt, the tools and the memory
            # files already cost, which is where every run starts from.
            start = _as_context(await _context_usage(client))
            model = (start or {}).get("model") or RESOLVED_MODEL or AGENT_MODEL or "?"
            if start:
                _last_context.update(start)
                _write_meta(context_tokens=start["tokens"],
                            context_window=start["window"],
                            context_pct=start["pct"])
            print(f"Agent started -- {SYSTEM}{ROLE_NOTE} · model {model}", flush=True)
            _write_meta(model=model)
            if NOTIFY_START:
                slack_notify(f":rocket: Agent {HANDLE} started — "
                             f"{CAMPAIGN or 'no campaign'} on {SYSTEM}{ROLE_NOTE} · {model}"
                             f" · critic {CRITIC_LABEL}.")
            prompt = load_user_prompt()
            empty_rounds = 0
            last_daily = start_time
            report_due = False
            # Whatever is already on the board counts as seen: a fresh agent must not
            # act on messages sent before it existed. Only what arrives from now on
            # reaches it. Standing instructions belong in the user prompt file, which
            # IS read fresh at startup -- the board is for live messages.
            last_announcements = tools.read_announcements()
            # Only growth from here counts: what is already written was reviewed, or
            # not, by whoever ran before.
            last_records = _record_texts()
            # Set while the agent is dealing with findings, so its answer to them is
            # not itself put up for review.
            answering_critic = False
            stopping = None           # set to the reason once the run starts winding down
            finalize_rounds = 0
            for round_num in range(1, MAX_ROUNDS + 1):
                print(f"\n===== ROUND {round_num} =====", flush=True)
                _heartbeat()
                _set_phase(f"round {round_num}: reasoning")
                # Shut down if the agent flagged a blocking problem it could not get
                # around and it has stayed unresolved past the grace period.
                ps = tools.problem_since()
                if ps is not None and time.time() - ps >= PROBLEM_GRACE:
                    print(f"Agent-flagged problem unresolved for >{PROBLEM_GRACE}s -- stopping.", flush=True)
                    stop_reason = "agent-flagged problem unresolved past grace"
                    break
                # Scheduled status: post the fixed-metrics line, then ask the agent to
                # add a short narrative in its own words.
                if NOTIFY_DAILY and time.time() - last_daily >= DAILY_INTERVAL:
                    await _post_scheduled_status(client, round_num, start_time)
                    prompt = REPORT_PROMPT + "\n\n" + prompt
                    last_daily = time.time()
                # Operator asked this run to wind down (kill_agent.sh --drain).
                if stopping is None and _stop_file_present():
                    stopping = "stop requested"
                    tools.request_stop()
                    print("Stop requested -- winding down: no new work, finishing "
                          "what is in flight.", flush=True)
                    # Replace, not prepend: whatever was queued (CONTINUE/EXPLORE) tells
                    # the agent to submit the next region, which contradicts winding down.
                    prompt = WINDDOWN_PROMPT
                # The agent says the campaign's goal is met. Same wind-down: no new
                # work, drain what is in flight, write it up.
                if stopping is None and tools.goal_is_met():
                    stopping = "goal met"
                    tools.request_stop()
                    _write_meta(goal_met=tools.goal_is_met())
                    print(f"Goal met -- winding down: {tools.goal_is_met()}", flush=True)
                    prompt = WINDDOWN_PROMPT
                # A cycle write-up is the trigger: the journal gains a section, so a
                # longer journal than last round means there is something to review.
                # A cycle is the agent's own boundary, so the agent marks it: the
                # runner reviews what was written since the last one, not an amount of
                # text it guessed was enough to be a write-up.
                # Every closed cycle is reviewed, winding down included -- that is
                # often where the conclusions are written. What is not reviewed is a
                # write-up produced in answer to findings: reviewing that reviews the
                # corrections, which produces more corrections.
                if CRITIC_MODEL and not answering_critic:
                    conclusion = tools.cycle_done_pending()
                    records = _record_texts()
                    new_section = _new_record_text(last_records, records)
                    if conclusion:
                        last_records = records
                        new_section = (f"The agent's stated conclusion for this cycle:\n"
                                       f"{conclusion}\n\n{new_section}")
                        print(f"[critic] reviewing {len(new_section)} new chars "
                              f"with {CRITIC_LABEL}", flush=True)
                        if stopping:
                            # A review takes a couple of minutes. During a wind-down
                            # that silence looks like a hang, so say what it is waiting
                            # for.
                            slack_notify(f":mag: Reviewing the last cycle with "
                                         f"{CRITIC_LABEL} before exit.")
                        _set_phase(f"round {round_num}: critic reviewing ({CRITIC_LABEL})")
                        reply = critic.review(CRITIC_MODEL, new_section,
                                              _recent_results())
                        if reply:
                            _append_review(reply)
                            found = critic.blocking(reply)
                            print(f"[critic] {len(found)} blocking finding(s)", flush=True)
                            answering_critic = bool(found)
                            if found:
                                prompt = _critic_prompt(found, reply, tail=prompt)
                # Announcements board changed between rounds -> surface it first.
                board = tools.read_announcements()
                if board and board != last_announcements:
                    fresh = _new_board_lines(last_announcements, board)
                    if fresh:
                        prompt = _announcements_prompt(fresh, tail=prompt)
                last_announcements = board
                submits_before = tools.submit_count()
                await client.query(prompt)
                await drain_turn(client, round_num)
                if answering_critic:
                    # That turn was the answer; take its write-up as read and review
                    # what comes after it.
                    last_records = _record_texts()
                    tools.cycle_done_pending()
                    answering_critic = False
                new_submits = tools.submit_count() - submits_before
                print(f"[round {round_num}] new_submits={new_submits} "
                      f"in_flight={tools.jobs_in_flight()} pending={tools.pending_count()}",
                      flush=True)

                # Start winding down when the budget is spent or time is up. Only the
                # DECISION happens here -- outstanding work still drains below, so a
                # run never orphans in-flight jobs. Finding a good result is NOT a
                # stop condition; keep exploring new regions.
                over_time = MAX_RUNTIME is not None and (time.time() - start_time) >= MAX_RUNTIME
                if stopping is None and (tools.submit_count() >= tools.MAX_SUBMITS or over_time):
                    stopping = "time limit" if over_time else f"budget ({tools.MAX_SUBMITS} submits)"
                    tools.request_stop()
                    print(f"{stopping} reached -- winding down: no new work, finishing "
                          f"what is in flight.", flush=True)
                # Everything collected and the run is ending: give the agent a turn (or
                # two) to write the journal and LOGBOOK BEFORE exiting. Without this the
                # loop would break the moment the last job landed and the write-up would
                # be lost.
                if stopping is not None and tools.jobs_in_flight() == 0:
                    if finalize_rounds < MAX_FINALIZE_ROUNDS:
                        finalize_rounds += 1
                        print(f"Drained -- finalize turn {finalize_rounds}/{MAX_FINALIZE_ROUNDS} "
                              f"(write-up).", flush=True)
                        prompt = FINALIZE_PROMPT
                        continue
                    print(f"{stopping}: drained and written up — run complete.", flush=True)
                    stop_reason = stopping
                    break
                # Idle round with budget left: re-prompt the agent to propose a NEW
                # region instead of exiting. A consecutive-empty cap stops a truly
                # stuck agent.
                if tools.jobs_in_flight() == 0 and new_submits == 0:
                    empty_rounds += 1
                    if empty_rounds >= MAX_EMPTY_ROUNDS:
                        print(f"No new work proposed for {MAX_EMPTY_ROUNDS} rounds — "
                              f"stopping.", flush=True)
                        stop_reason = f"no new work for {MAX_EMPTY_ROUNDS} rounds"
                        break
                    prompt = EXPLORE_PROMPT
                    continue
                empty_rounds = 0

                # Wait for a job to finish. Run the blocking wait in a thread so the
                # open client stays serviced. A pending job may sit in the Polaris
                # queue for MANY hours -- that is normal -- so by default we wait
                # indefinitely while a job is genuinely queued/running. A cap
                # (STALL_LIMIT) is applied only if explicitly set (tests set one).
                stalled = 0
                since_tick = 0
                backend_problem = None
                board_update = None
                while tools.pending_count() > 0:
                    _set_phase(f"round {round_num}: waiting for "
                               f"{tools.pending_count()} job(s)")
                    done = await loop.run_in_executor(None, tools.wait_for_any, ANNOUNCE_POLL)
                    _heartbeat(force=False)
                    if done > 0:
                        break
                    # A stop request during a long wait should be picked up now, not
                    # whenever a job happens to finish.
                    if stopping is None and _stop_file_present():
                        break
                    # Poll the announcements board every pass: a small-file read, no LLM
                    # turn. A change breaks the wait so the agent can react promptly.
                    board = tools.read_announcements()
                    if board and board != last_announcements:
                        board_update = _new_board_lines(last_announcements, board)
                        last_announcements = board
                        if board_update:
                            break
                    since_tick += ANNOUNCE_POLL
                    if since_tick < WAIT_TIMEOUT:
                        continue
                    since_tick = 0
                    stalled += WAIT_TIMEOUT
                    print(f"[waiting] {tools.pending_count()} job(s) still queued/running "
                          f"after {stalled // 60} min -- still alive.", flush=True)
                    # Catch a stuck/dead backend AT THIS TICK (not on the summary interval):
                    # if nothing is really running, break now and let the agent recover or alert.
                    backend_problem = tools.backend_trouble()
                    if backend_problem:
                        print(f"[backend check] problem detected: {backend_problem}", flush=True)
                        break
                    # Periodic summary during a long queue wait: break to give the agent
                    # a turn to report in its own words, then resume waiting next round.
                    if NOTIFY_DAILY and time.time() - last_daily >= DAILY_INTERVAL:
                        report_due = True
                        break
                    if STALL_LIMIT is not None and stalled >= STALL_LIMIT:
                        print(f"Stall cap ({STALL_LIMIT}s) reached; stopping. Pending jobs will "
                              f"need recovery on restart.", flush=True)
                        stop_reason = f"stall cap ({STALL_LIMIT}s)"
                        return
                if board_update:
                    prompt = _announcements_prompt(board_update)
                elif backend_problem:
                    prompt = ("A backend health check during the wait found a problem: "
                              f"{backend_problem}. Nothing is completing. Act now: if it is "
                              "recoverable, resubmit the affected config(s); if not, call "
                              "notify(blocking=true) with a clear one-line message so the run stops.")
                    backend_problem = None
                elif report_due:
                    report_due = False
                    last_daily = time.time()
                    await _post_scheduled_status(client, round_num, start_time)
                    prompt = (REPORT_PROMPT + " Nothing new has completed; just post the "
                              "summary and take no other action.")
                elif stopping is not None:
                    # CONTINUE asks for the next region, which is the one thing a run
                    # that is winding down must not do.
                    prompt = WINDDOWN_PROMPT
                else:
                    prompt = CONTINUE_PROMPT
    except asyncio.CancelledError:
        print("Cancelled -- running graceful shutdown.", flush=True)
    finally:
        beat_task.cancel()
        # Its answer is of no use once the run has ended.
        if _context_task is not None and not _context_task.done():
            _context_task.cancel()
        # Record the outcome and clear the liveness marks FIRST. Shutting an executor
        # down can block (a local job runs for as long as it runs), and if that
        # happens the run must still end up correctly recorded rather than frozen as
        # "running" forever. The run dir itself STAYS -- it is the run history.
        _write_meta(status="stopped", stop_reason=stop_reason,
                    ended_at=datetime.now().isoformat(timespec="seconds"),
                    remote_submitted=tools.submit_count(),
                    local_submitted=tools.local_submit_count(),
                    uptime_s=int(time.time() - start_time))
        for path in (pid_file, os.path.join(RUN_DIR, "heartbeat")):
            try:
                os.remove(path)
            except OSError:
                pass
        if NOTIFY_FINISH:
            slack_notify(f":checkered_flag: Agent stopped — "
                         f"reason: {stop_reason} · {tools.submit_count()} remote / "
                         f"{tools.local_submit_count()} local submitted · "
                         f"uptime {_fmt_uptime(time.time() - start_time)}.")
        shutdown_executor()
        print("Executor shut down.", flush=True)
        _stop_watcher()


if __name__ == "__main__":
    if CHECK_ONLY:
        preflight()
        print("preflight only: the run was not started.", flush=True)
        sys.exit(0)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_PATH, "w") as log_file:
        sys.stdout = Tee(log_file, sys.__stdout__)
        sys.stderr = Tee(log_file, sys.__stderr__)
        print(f"Logging to {LOG_PATH}", flush=True)
        preflight()
        asyncio.run(main())
