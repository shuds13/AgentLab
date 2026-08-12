#!/usr/bin/env python3
"""
CAS: an agent that searches a domain by running work on HPC via Globus Compute.

System prompt: prompt.md (method) + SYSTEM.md (where/how runs are hosted)
User prompt:   user_prompt.md (initial task — editable without touching this file)

Usage:
    python agent.py
"""

import asyncio
import json
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
    AssistantMessage,
    ResultMessage,
)
import tools
from tools import create_server, shutdown_executor

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR") or (
    os.path.join(os.path.abspath(os.environ.get("LAB_DIR",
        os.path.join(SCRIPT_DIR, ".."))), "workspace", os.environ["CAMPAIGN"])
    if os.environ.get("CAMPAIGN") else SCRIPT_DIR)
# Campaign files (prompt.md, user prompt) live with the campaign, not the framework.
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
CAMPAIGN = os.environ.get("CAMPAIGN", "")
CAMPAIGN_DIR = os.path.abspath(os.environ.get(
    "CAMPAIGN_DIR", os.path.join(LAB_DIR, "campaigns", CAMPAIGN) if CAMPAIGN else SCRIPT_DIR))
SYSTEM = tools.SYSTEM          # from the campaign's campaign.json
ROLE = os.environ.get("ROLE", "both")
LOG_DIR = os.path.join(WORKSPACE_DIR, "logs")
USER_PROMPT_FILE = os.environ.get("USER_PROMPT_FILE", "user_prompt.md")

# One timestamp per process, shared by the log file and this run's directory so the
# two line up. RUN_ID names the run dir and is what kill_agent.sh targets.
RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
RUN_ID = f"{SYSTEM}_{ROLE}_{RUN_STAMP}"
RUN_DIR = os.path.join(WORKSPACE_DIR, "runs", RUN_ID)
LOG_PATH = os.path.join(LOG_DIR, f"run_{SYSTEM}_{RUN_STAMP}.log")
HEARTBEAT_INTERVAL = 30   # s; minimum gap between heartbeat writes during a wait
# Turns given to the agent AFTER everything has drained, so it can write the
# journal/LOGBOOK before the process exits.
MAX_FINALIZE_ROUNDS = 2

# The agent runs as ONE stateful conversation (ClaudeSDKClient), so it keeps all
# prior context and reasoning across turns. Each turn it acts on whatever jobs
# have finished; then agent.py waits Python-side (off the event loop) for the next
# job to complete and nudges the same conversation onward. Remote jobs keep
# running and are never cancelled by a turn ending.
CONTINUE_PROMPT = (
    "One or more jobs have finished. Collect them with get_completed_jobs, "
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
    "All outstanding work is collected and this run is now ending. Close out the "
    "current cycle: write its JOURNAL.md section (with any figures), append the "
    "one-line pointer to LOGBOOK.md, and note anything a later run needs to pick up "
    "where you left off. Submit no new work."
)
MAX_ROUNDS = 500          # backstop against a runaway loop
MAX_EMPTY_ROUNDS = 3      # consecutive idle rounds (no work proposed) before giving up
MAX_RUNTIME = int(os.environ["CAS_MAX_RUNTIME"]) if os.environ.get("CAS_MAX_RUNTIME") else None  # total agent wallclock (s); None = no time limit
WAIT_TIMEOUT = 1800       # s between "still-alive" logs / backend-health checks during a wait
ANNOUNCE_POLL = int(os.environ.get("ANNOUNCE_POLL", "2"))   # s between announcement-board checks during a job wait
STALL_LIMIT = int(os.environ["CAS_STALL_LIMIT"]) if os.environ.get("CAS_STALL_LIMIT") else None  # None = wait indefinitely (HPC queues can take many hours); set seconds to cap (tests do)

# --- Slack notifications (optional; see SLACK_NOTIFY.md). Missing webhook/script
# or a failed post is ignored so a run is never affected. ---
def _bool_env(name, default=False):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")

NOTIFY_START = _bool_env("NOTIFY_START", False)
NOTIFY_DAILY = _bool_env("NOTIFY_DAILY", True)
NOTIFY_FINISH = _bool_env("NOTIFY_FINISH", True)
DAILY_INTERVAL = int(os.environ.get("NOTIFY_DAILY_INTERVAL", "86400"))  # seconds between periodic summaries
PROBLEM_GRACE = int(os.environ.get("NOTIFY_PROBLEM_GRACE", "1800"))     # shut down this long (s) after the agent flags an unresolved blocking problem
NOTIFY_SCRIPT = os.path.join(SCRIPT_DIR, "slack_notify.sh")

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


async def _context_usage(client):
    """Best-effort /context data (model, tokens, window, pct) via the SDK method that
    backs the CLI /context command. Returns the dict or None on failure."""
    try:
        return await client.get_context_usage()
    except Exception as e:
        print(f"[context] get_context_usage failed (ignored): {e}", flush=True)
        return None


async def _post_scheduled_status(client, round_num, start_time):
    """Post the fixed-metrics scheduled status line to Slack (harness-owned, deterministic)."""
    u = await _context_usage(client) or {}
    model = u.get("model") or "?"
    tok, win, pct = u.get("totalTokens"), u.get("rawMaxTokens"), u.get("percentage")
    ctx = (f"ctx ~{tok}/{win} (~{pct:.0f}%)"
           if tok is not None and win and pct is not None else "ctx n/a")
    slack_notify(f":calendar: Scheduled Status — {SYSTEM} ({ROLE}) · {model}, "
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


def load_system():
    with open(os.path.join(SCRIPT_DIR, "SYSTEM.md")) as f:
        return f.read()


def load_user_prompt():
    with open(os.path.join(CAMPAIGN_DIR, USER_PROMPT_FILE)) as f:
        return f.read()


# --- Run directory: one permanent dir per run -----------------------------------
# Holds this run's metadata and a snapshot of the prompt files it actually used, so
# a run stays reproducible after the prompts change. It is NEVER deleted -- it is the
# run history. Liveness is the heartbeat file inside it, not the dir existing.
# The stop file also lives here, so it is scoped to this run: a restart gets a fresh
# dir and can never inherit a stale stop request.
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
    for base, name in ((CAMPAIGN_DIR, "prompt.md"), (SCRIPT_DIR, "SYSTEM.md"),
                       (CAMPAIGN_DIR, USER_PROMPT_FILE)):
        try:
            shutil.copy2(os.path.join(base, name), os.path.join(RUN_DIR, name))
        except Exception as e:
            print(f"[run] could not snapshot {name} (ignored): {e}", flush=True)
    _write_meta(run_id=RUN_ID, system=SYSTEM, role=ROLE,
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


def preflight():
    """Verify everything this run needs BEFORE starting. Fail fast with a clear
    message instead of discovering a missing piece mid-run and spinning."""
    problems = []
    # The task plug-in must satisfy the contract before anything else is tried.
    for attr in ("JOB_DESC", "JOB_SCHEMA", "job_key", "remote_fn"):
        if not hasattr(tools.task, attr):
            problems.append(f"task {tools.TASK_DIR} is missing '{attr}' "
                            f"(see AGENTS.md)")
    # A task may declare its own checks -- e.g. that its binary is where it expects.
    if hasattr(tools.task, "preflight"):
        try:
            problems += list(tools.task.preflight() or [])
        except Exception as e:
            problems.append(f"task preflight() raised: {e}")
    if not os.path.isdir(WORKSPACE_DIR):
        problems.append(f"WORKSPACE_DIR does not exist: {WORKSPACE_DIR}")
    system_md = os.path.join(SCRIPT_DIR, "SYSTEM.md")
    if not os.path.isfile(system_md):
        problems.append(f"SYSTEM.md missing: {system_md} (system-details prompt loaded into the agent)")
    # Fail fast if the Globus Compute endpoint is not online -- otherwise every
    # submit fails with ENDPOINT_NOT_ONLINE and the run does nothing.
    try:
        import globus_compute_sdk as _gc
        _c = _gc.Client()
        _st = _c.get_endpoint_status(tools.ENDPOINT_ID).get("status")
        if _st != "online":
            try:
                _nm = _c.get_endpoint_metadata(tools.ENDPOINT_ID).get("name") or tools.ENDPOINT_ID
            except Exception:
                _nm = tools.ENDPOINT_ID
            slack_notify(f":rotating_light: Agent exiting -- {SYSTEM} ({ROLE}): Globus Compute "
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
    print(f"preflight OK: task={tools.TASK_DIR}, SYSTEM.md, WORKSPACE_DIR, endpoint online.", flush=True)


async def drain_turn(client, round_num):
    """Print the assistant's output for one turn (until its ResultMessage)."""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text, flush=True)
        elif isinstance(message, ResultMessage):
            print(f"\n[round {round_num} turn end] {message.subtype}", flush=True)


async def main():
    system_prompt = load_prompt()
    system_prompt += "\n\n" + load_system()
    system_prompt += f"\n\n# This agent\nSYSTEM={SYSTEM}  ROLE={ROLE}.\nThe shared files (results.jsonl, LOGBOOK.md, JOURNAL.md, claims.jsonl) live in {WORKSPACE_DIR} \u2014 always read and write them by full path there (e.g. {WORKSPACE_DIR}/results.jsonl). Follow the role rules in the Collaboration section of the prompt."
    server = create_server()

    options = ClaudeAgentOptions(
        mcp_servers={"cas": server},
        # The task plug-in decides which job tools exist (a task with no local
        # comparator does not get the local pair), so take the list from tools.
        allowed_tools=tools.tool_names() + [
            "Read",
            "Write",
            "Glob",
            "Grep",
            "Bash",
        ],
        permission_mode="bypassPermissions",
        system_prompt=system_prompt,
        cwd=SCRIPT_DIR,
    )

    results_file = os.path.join(WORKSPACE_DIR, "results.jsonl")
    loop = asyncio.get_event_loop()

    print("Starting CAS search agent...", flush=True)
    print(f"Results: {results_file}", flush=True)
    print("=" * 60, flush=True)

    start_time = time.time()
    stop_reason = "ended (max rounds)"

    # PID file keyed by SYSTEM+ROLE so kill_agent.sh can target THIS agent when
    # several run at once. Removed on clean exit.
    run_dir = os.path.join(WORKSPACE_DIR, "run")
    os.makedirs(run_dir, exist_ok=True)
    pid_file = os.path.join(run_dir, f"agent_{SYSTEM}_{ROLE}.pid")
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
            model = (await _context_usage(client) or {}).get("model") or "?"
            print(f"Agent started -- {SYSTEM} ({ROLE}) · model {model}", flush=True)
            _write_meta(model=model)
            if NOTIFY_START:
                slack_notify(f":rocket: Agent started — {SYSTEM} ({ROLE}) · {model}.")
            prompt = load_user_prompt()
            empty_rounds = 0
            last_daily = start_time
            report_due = False
            # Whatever is already on the board counts as seen: a fresh agent must not
            # act on messages sent before it existed. Only what arrives from now on
            # reaches it. Standing instructions belong in the user prompt file, which
            # IS read fresh at startup -- the board is for live messages.
            last_announcements = tools.read_announcements()
            stopping = None           # set to the reason once the run starts winding down
            finalize_rounds = 0
            for round_num in range(1, MAX_ROUNDS + 1):
                print(f"\n===== ROUND {round_num} =====", flush=True)
                _heartbeat()
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
                # (CAS_STALL_LIMIT) is applied only if explicitly set (tests set one).
                stalled = 0
                since_tick = 0
                backend_problem = None
                board_update = None
                while tools.pending_count() > 0:
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
                else:
                    prompt = CONTINUE_PROMPT
    except asyncio.CancelledError:
        print("Cancelled -- running graceful shutdown.", flush=True)
    finally:
        beat_task.cancel()
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
            slack_notify(f":checkered_flag: Agent stopped — {SYSTEM} ({ROLE}), "
                         f"reason: {stop_reason} · {tools.submit_count()} remote / "
                         f"{tools.local_submit_count()} local submitted · "
                         f"uptime {_fmt_uptime(time.time() - start_time)}.")
        shutdown_executor()
        print("Executor shut down.", flush=True)


if __name__ == "__main__":
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_PATH, "w") as log_file:
        sys.stdout = Tee(log_file, sys.__stdout__)
        sys.stderr = Tee(log_file, sys.__stderr__)
        print(f"Logging to {LOG_PATH}", flush=True)
        preflight()
        asyncio.run(main())
