"""CAS framework tools: run work on an HPC endpoint via Globus Compute, and
coordinate several agents through a shared directory.

This file is domain-agnostic. WHAT gets submitted comes from a task plug-in
(see the campaign's task.py, and AGENTS.md); this file owns the mechanism around it:
claiming work so two agents never duplicate it, tracking futures, capacity limits,
draining on shutdown, the announcements board, and Slack notification.

Which system to run on is picked with the SYSTEM env var and described in
config.json.
"""

import fcntl
import importlib
import itertools
import json
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as _futures_wait

import transfer as _transfer
from claude_agent_sdk import create_sdk_mcp_server, tool
from globus_compute_sdk import Executor
from globus_compute_sdk.serialize import AllCodeStrategies, ComputeSerializer

ROLE = os.environ.get("ROLE", "both")  # free-form; the prompt defines what roles mean

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", SCRIPT_DIR)

CAMPAIGN = os.environ.get("CAMPAIGN", "")
USER_NAME = os.environ.get("USER_NAME") or os.environ.get("USER", "")


def _read_json(path, what, needs=()):
    """Load one configuration file, saying what to do about it if it is unusable."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        sys.exit(f"{what} not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"{what} is not valid JSON: {path}\n  {e}")
    missing = [k for k in needs if not data.get(k) or str(data[k]).startswith("<")]
    if missing:
        sys.exit(f"{what} needs {', '.join(missing)} filled in: {path}")
    return data


# Configuration comes from three files, composed here. Machine facts are shared, access
# is per user, and what to run is per campaign, so each fact is stated once.
if not CAMPAIGN:
    sys.exit("CAMPAIGN is not set (the directory name under campaigns/).")

_CAMPAIGN_DIR = os.path.join(LAB_DIR, "campaigns", CAMPAIGN)
_cam = _read_json(
    os.path.join(_CAMPAIGN_DIR, "campaign.json"),
    f"campaign '{CAMPAIGN}'",
    needs=("system",),
)
SYSTEM = _cam["system"]

_sys_cfg = _read_json(
    os.path.join(LAB_DIR, "systems", f"{SYSTEM}.json"), f"system '{SYSTEM}'"
)

# --- the task plug-in ----------------------------------------------------------
# Supplies what a job IS: how it is described to the agent, what arguments it takes,
# how a piece of work is identified, and the function that runs remotely.
# See AGENTS.md for the contract.
#
# The task is imported from its own directory rather than copied in here, so it can
# keep support files (scripts, data) beside it and refer to them relative to its own
# __file__.
TASK_DIR = os.path.abspath(os.environ.get("TASK_DIR", _CAMPAIGN_DIR))
if TASK_DIR not in sys.path:
    sys.path.insert(0, TASK_DIR)
task = importlib.import_module(os.environ.get("TASK_MODULE", "task"))
HAS_LOCAL = hasattr(task, "local_fn")
HAS_REMOTE = hasattr(task, "remote_fn")

# Only remote jobs need a Globus endpoint and an account to charge, so a task that
# defines local_fn alone runs without either -- and without a user file at all. Its
# work_dir then defaults to the campaign workspace.
_user_path = os.path.join(LAB_DIR, "users", USER_NAME, f"{SYSTEM}.json")
if HAS_REMOTE:
    _usr = _read_json(
        _user_path,
        f"your access to '{SYSTEM}'",
        needs=("endpoint", "account", "work_dir"),
    )
else:
    _usr = (
        _read_json(_user_path, f"your access to '{SYSTEM}'")
        if os.path.isfile(_user_path)
        else {}
    )
    _usr.setdefault("work_dir", os.path.join(LAB_DIR, "workspace", CAMPAIGN))

ENDPOINT_ID = _usr.get("endpoint", "")

# Globus Transfer is optional: configured per user, and simply absent otherwise. It is
# how the agent reads files on the compute system when the two do not share a filesystem.
_transfer.CFG = _transfer.configure(
    _usr, os.path.join(LAB_DIR, "workspace", CAMPAIGN), _CAMPAIGN_DIR, _sys_cfg
)
HAS_TRANSFER = _transfer.CFG is not None
# How many jobs may be in flight at once. The system file holds a site default, bounded
# by queue policy and allocation rather than by the size of the machine; a campaign
# overrides it, because what is sensible depends on what one job does.
MAX_CONCURRENT = int(
    os.environ.get(
        "MAX_CONCURRENT", _cam.get("max_concurrent", _sys_cfg.get("max_concurrent", 1))
    )
)

# Named resource shapes on one system (e.g. a small quick queue and a large long one).
# A task may route a job to one; otherwise the default is used.
_bucket_defaults = dict(_sys_cfg.get("bucket_defaults", {}))
_bucket_defaults.update(_cam.get("resources", {}))  # campaign: queue, walltime, nodes
_bucket_defaults.update(_usr.get("resources", {}))  # user: anything they must override
_bucket_defaults["account"] = _usr.get("account", "")
_SYS = {
    "buckets": {
        "default": {
            "num_nodes": _bucket_defaults.get("num_nodes", 1),
            "user_config": _bucket_defaults,
        }
    }
}
_default_bucket = "default"

# TARGET is handed to the task's remote_fn. Everything the remote side needs must be
# in here: the function is shipped source-only and cannot read this module.
TARGET = dict(_sys_cfg.get("target", {}))
_cam_target = dict(_cam.get("target", {}))
# env merges key by key, so a campaign adds to the system's environment rather than
# replacing it. Everything else the campaign sets wins outright.
TARGET["env"] = {**TARGET.get("env", {}), **_cam_target.pop("env", {})}
TARGET.update(_cam_target)
TARGET["work_dir"] = _usr["work_dir"]
TARGET.setdefault("ppn", _sys_cfg.get("ppn", 1))
TARGET["nranks"] = _SYS["buckets"][_default_bucket].get("num_nodes", 1) * TARGET["ppn"]

REMOTE_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "43200"))  # 12h client-side wait
LOCAL_TIMEOUT = int(os.environ.get("LOCAL_JOB_TIMEOUT", "14400"))  # 4h
# The same, for jobs run on this machine. One by default: a local job is assumed to use
# the whole thing, and a task whose jobs are small enough to share it says so.
LOCAL_MAX_CONCURRENT = int(
    os.environ.get(
        "LOCAL_MAX_CONCURRENT",
        _cam.get("local_max_concurrent", _sys_cfg.get("local_max_concurrent", 1)),
    )
)

# One Executor per bucket, created lazily and reused. Each distinct user_endpoint_config
# gets its own block pool on the endpoint, so buckets can run concurrently.
_executors = {}
_sa_executor = None  # local backend, created lazily

_jobs = {}  # remote: job_id -> {"future", "args", "key", "bucket"}
_job_counter = itertools.count(1)
_submit_count = 0
MAX_SUBMITS = int(os.environ.get("MAX_SUBMITS", "60"))  # backstop on total jobs per run

_local_jobs = {}  # local: job_id -> {"future", "args"}
_local_counter = itertools.count(1)
_local_submit_count = 0

# Wind-down flag. When set, submitting NEW work is refused so outstanding jobs can
# drain and the run can end cleanly. Collecting finished work is unaffected.
_stop_requested = False

JOBS_LOG = os.path.join(
    WORKSPACE_DIR, "jobs.jsonl"
)  # durable record of every job fired

# --- Shared announcements board -------------------------------------------------
# One plain text file. Anyone (an operator, a Slack bridge) appends lines; every
# agent reads it and decides what applies to it. No routing, no per-agent state.
ANNOUNCEMENTS_FILE = os.path.join(WORKSPACE_DIR, "ANNOUNCEMENTS.md")

NOTIFY_SCRIPT = os.environ.get("NOTIFY_SCRIPT") or os.path.join(
    SCRIPT_DIR, "slack_notify.sh"
)
_last_success_time = None  # last non-error completion (real progress)
_problem_since = None  # when the agent flagged a blocking problem (None = none)


def _slack_post(msg):
    """Best-effort Slack post. Never raises; Slack is optional."""
    if not os.path.isfile(NOTIFY_SCRIPT):
        return
    try:
        subprocess.run(
            ["bash", NOTIFY_SCRIPT, msg],
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def problem_since():
    """When the agent last flagged an unresolved blocking problem, else None."""
    return _problem_since


def backend_trouble():
    """Health probe run at each no-progress wait tick. Returns a short problem string
    if the backend looks dead -- endpoint offline, or ALL in-flight tasks failed/lost --
    else None. A normal long queue is healthy and returns None, so a slow site is never
    false-flagged. Read-only; a transient query error returns None rather than raising."""
    if not HAS_REMOTE:
        return None
    import globus_compute_sdk as _gc

    try:
        c = _gc.Client()
        st = (c.get_endpoint_status(ENDPOINT_ID) or {}).get("status")
    except Exception:
        return None
    if st != "online":
        return f"endpoint {ENDPOINT_ID} is not online (status={st})"
    pend = [(jid, j) for jid, j in _jobs.items() if not j["future"].done()]
    if not pend:
        return None
    bad = []
    for jid, j in pend:
        tid = getattr(j["future"], "task_id", None)
        if not tid:
            return None  # not populated yet (just submitted) -> not stuck
        try:
            t = c.get_task(tid) or {}
        except Exception:
            return None
        status = str(t.get("status") or "").lower()
        if status in ("failed", "lost", "cancelled") or t.get("exception"):
            bad.append((jid, status or "exception"))
    if bad and len(bad) == len(pend):
        return f"all {len(pend)} in-flight task(s) failed/lost: {bad}"
    return None


def _append_jobs_log(record):
    # The run that fired it: the log accumulates across every run of a campaign, so
    # without this there is no way to say what this run did.
    record = {"run": os.environ.get("RUN_ID", ""), **record}
    try:
        with open(JOBS_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# --- cross-agent claims ---------------------------------------------------------
# Agents share WORKSPACE_DIR. Before running a piece of work an agent claims it in
# claims.jsonl under a file lock, so another never runs the same one. Released on
# completion, or expires after CLAIM_STALE if an agent dies holding it.
CLAIMS_FILE = os.path.join(WORKSPACE_DIR, "claims.jsonl")
CLAIMS_LOCK = os.path.join(WORKSPACE_DIR, "claims.lock")
CLAIM_STALE = int(os.environ.get("CLAIM_STALE_SECONDS", "21600"))  # 6h
AGENT_ID = f"{SYSTEM}-{os.getpid()}"


def _active_claims():
    active = {}
    if not os.path.exists(CLAIMS_FILE):
        return active
    now = time.time()
    with open(CLAIMS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            key = c.get("key")
            if c.get("state") == "done":
                active.pop(key, None)
            elif c.get("state") == "claimed":
                if now - c.get("ts", 0) <= CLAIM_STALE:
                    active[key] = c
                else:
                    active.pop(key, None)
    return active


def _try_claim(key, stage=""):
    """Claim a piece of work atomically. Returns (True, None) or (False, holder)."""
    with open(CLAIMS_LOCK, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            held = _active_claims().get(key)
            if held and held.get("agent") != AGENT_ID:
                return False, held.get("agent")
            with open(CLAIMS_FILE, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "key": key,
                            "stage": stage,
                            "agent": AGENT_ID,
                            "ts": time.time(),
                            "state": "claimed",
                        }
                    )
                    + "\n"
                )
            return True, None
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _release_claim(key):
    try:
        with open(CLAIMS_LOCK, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                with open(CLAIMS_FILE, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "key": key,
                                "agent": AGENT_ID,
                                "ts": time.time(),
                                "state": "done",
                            }
                        )
                        + "\n"
                    )
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except Exception:
        pass


# --- round bookkeeping for agent.py's outer loop --------------------------------
def request_stop():
    """Wind this run down: refuse NEW work so outstanding jobs can drain. Set by
    agent.py on a stop request, spent budget, or time limit. Collecting is unaffected."""
    global _stop_requested
    _stop_requested = True


def stop_is_requested():
    return _stop_requested


def submit_count():
    """Jobs submitted this process that MAX_SUBMITS caps. Remote where the task has
    remote_fn, else the local jobs -- so a local-only run is bounded the same way."""
    return _submit_count if HAS_REMOTE else _local_submit_count


def local_submit_count():
    """Local jobs submitted this process (observability only -- no cap)."""
    return _local_submit_count


def _remote_pending_count():
    return sum(1 for j in _jobs.values() if not j["future"].done())


def _local_pending_count():
    return sum(1 for j in _local_jobs.values() if not j["future"].done())


def jobs_in_flight():
    """Jobs submitted but not yet collected (pending + done-but-uncollected)."""
    return len(_jobs) + len(_local_jobs)


def pending_count():
    """In-flight jobs whose future is not yet done."""
    return _remote_pending_count() + _local_pending_count()


def wait_for_any(timeout=1800):
    """Block until at least one in-flight future completes, or timeout; return how many
    finished. Lets agent.py wait between rounds without spinning or holding the LLM open."""
    futs = [j["future"] for j in _jobs.values() if not j["future"].done()]
    futs += [j["future"] for j in _local_jobs.values() if not j["future"].done()]
    if not futs:
        return 0
    done, _ = _futures_wait(futs, timeout=timeout, return_when=FIRST_COMPLETED)
    return len(done)


def read_announcements():
    """Current text of the announcements board ('' if none). Never raises.
    Polled (not inotify) on purpose: the board may live on a shared filesystem and be
    written from another host, where inotify does not reliably fire."""
    try:
        with open(ANNOUNCEMENTS_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[announcements] read failed (ignored): {e}", flush=True)
        return ""


def get_executor(bucket=None):
    if bucket is None:
        bucket = _default_bucket
    ex = _executors.get(bucket)
    if ex is None or getattr(ex, "_stopped", False):
        try:
            if ex is not None:
                ex.shutdown(wait=False)
        except Exception:
            pass
        cfg = dict(_SYS["buckets"][bucket].get("user_config", {}))
        ex = Executor(endpoint_id=ENDPOINT_ID, user_endpoint_config=cfg)
        # Ship the function by source: the worker does not have this code installed.
        ex.serializer = ComputeSerializer(strategy_code=AllCodeStrategies())
        _executors[bucket] = ex
    return ex


def get_local_executor():
    """Backend for local jobs. A thread pool is enough: local_fn blocks on a subprocess,
    so no CPU work happens in the Python thread itself. max_workers matches the
    admission check in submit_local."""
    global _sa_executor
    if _sa_executor is None:
        _sa_executor = ThreadPoolExecutor(max_workers=LOCAL_MAX_CONCURRENT)
    return _sa_executor


def shutdown_executor():
    for ex in list(_executors.values()):
        try:
            ex.shutdown(wait=False)
        except Exception:
            pass
    _executors.clear()
    global _sa_executor
    if _sa_executor is not None:
        try:
            # Do not wait: a local job can run for hours, and blocking here would stop
            # the rest of shutdown from happening.
            _sa_executor.shutdown(wait=False)
        except Exception:
            pass
        _sa_executor = None


# --- tools the agent calls ------------------------------------------------------
_WINDDOWN_REFUSAL = (
    "submit refused: this run is winding down. Collect and log the work "
    "already in flight, but do not submit anything new."
)


@tool("submit_job", getattr(task, "JOB_DESC", ""), getattr(task, "JOB_SCHEMA", {}))
async def submit_job(args):
    """Fire one remote job and return immediately with a job_id."""
    global _submit_count
    if _stop_requested:
        return {
            "content": [{"type": "text", "text": _WINDDOWN_REFUSAL}],
            "is_error": True,
        }
    if _submit_count >= MAX_SUBMITS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submit refused: hit MAX_SUBMITS={MAX_SUBMITS} total-jobs cap for this run",
                }
            ],
            "is_error": True,
        }
    if _remote_pending_count() >= MAX_CONCURRENT:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submit refused: at capacity ({_remote_pending_count()} running/queued, "
                    f"max_concurrent={MAX_CONCURRENT}). Collect a finished job "
                    f"with get_completed_jobs before submitting more.",
                }
            ],
            "is_error": True,
        }

    key = task.job_key(args)
    # A fresh-context agent cannot remember what it already fired, so refuse a repeat.
    for info in _jobs.values():
        if info["key"] == key and not info["future"].done():
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"submit refused: a job for {key} is already in flight. Collect it "
                        f"with get_completed_jobs before re-submitting.",
                    }
                ],
                "is_error": True,
            }
    ok, holder = _try_claim(key, stage=ROLE)
    if not ok:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submit skipped: {key} is already claimed by {holder}. Pick different work.",
                }
            ],
            "is_error": True,
        }

    bucket = task.bucket_for(args) if hasattr(task, "bucket_for") else _default_bucket
    target = dict(TARGET)
    target["nranks"] = _SYS["buckets"][bucket].get("num_nodes", 1) * target["ppn"]
    try:
        fut = get_executor(bucket).submit(task.remote_fn, args, target)
    except Exception as e:
        _release_claim(key)
        traceback.print_exc(file=sys.stderr)
        return {
            "content": [{"type": "text", "text": f"submit failed: {e}"}],
            "is_error": True,
        }

    job_id = next(_job_counter)
    _submit_count += 1
    _jobs[job_id] = {"future": fut, "args": args, "key": key, "bucket": bucket}
    _append_jobs_log(
        {
            "event": "submit",
            "job_id": job_id,
            "key": key,
            "args": args,
            "bucket": bucket,
            "task_id": getattr(fut, "task_id", None),
        }
    )
    # The budget left, with the job that was just accepted counted. It changes as the
    # run goes, so it belongs in what a submit answers rather than in a prompt written
    # once at the start -- and it counts this run's submits, not the rows in a record
    # that outlives the run.
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "job_id": job_id,
                        "key": key,
                        "bucket": bucket,
                        "submits_used": _submit_count,
                        "submits_allowed": MAX_SUBMITS,
                    }
                ),
            }
        ]
    }


GET_COMPLETED_DESC = (
    "Collect any finished jobs (non-blocking). Returns completed results and the list of "
    "still-pending job_ids. Call it once near the start of a turn; jobs still running come "
    "back on a later turn."
)


@tool("get_completed_jobs", GET_COMPLETED_DESC, {})
async def get_completed_jobs(args):
    global _last_success_time, _problem_since
    completed = []
    for job_id, info in list(_jobs.items()):
        fut = info["future"]
        if not fut.done():
            continue
        try:
            res = fut.result()
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            res = {"error": f"{type(e).__name__}: {e}", "args": info["args"]}
        if not isinstance(res, dict):
            res = {"result": res}
        res["job_id"] = job_id
        res.setdefault("key", info["key"])
        if "error" not in res:
            _last_success_time = time.time()
            _problem_since = None  # real progress clears any flagged problem
        completed.append(res)
        _append_jobs_log(
            {
                "event": "completed",
                "job_id": job_id,
                "key": info["key"],
                "error": "error" in res,
            }
        )
        _release_claim(info["key"])
        del _jobs[job_id]
    pending = [
        {"job_id": jid, "key": i["key"], "args": i["args"], "bucket": i["bucket"]}
        for jid, i in _jobs.items()
    ]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"completed": completed, "pending": pending}, indent=2, default=str
                ),
            }
        ]
    }


@tool(
    "submit_local", getattr(task, "LOCAL_DESC", ""), getattr(task, "LOCAL_SCHEMA", {})
)
async def submit_local(args):
    """Fire the local comparator and return immediately with a job_id."""
    global _local_submit_count
    if _stop_requested:
        return {
            "content": [{"type": "text", "text": _WINDDOWN_REFUSAL}],
            "is_error": True,
        }
    if not HAS_REMOTE and _local_submit_count >= MAX_SUBMITS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submit refused: hit MAX_SUBMITS={MAX_SUBMITS} total-jobs cap for this run",
                }
            ],
            "is_error": True,
        }
    if _local_pending_count() >= LOCAL_MAX_CONCURRENT:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"submit refused: a local job is already running (max {LOCAL_MAX_CONCURRENT} "
                    f"at a time -- it uses the whole node). Collect it with get_local_completed "
                    f"before submitting more.",
                }
            ],
            "is_error": True,
        }
    fut = get_local_executor().submit(task.local_fn, args)
    job_id = next(_local_counter)
    _local_submit_count += 1
    _local_jobs[job_id] = {"future": fut, "args": args}
    _append_jobs_log({"event": "local_submit", "job_id": job_id, "args": args})
    # As for submit_job. Where the task has remote work too, MAX_SUBMITS caps that and
    # local jobs are uncapped, so there is a count to report and no allowance.
    out = {"job_id": job_id, "args": args, "local_submits_used": _local_submit_count}
    if not HAS_REMOTE:
        out["submits_allowed"] = MAX_SUBMITS
    return {"content": [{"type": "text", "text": json.dumps(out)}]}


GET_LOCAL_DESC = (
    "Collect the finished local job, if any (non-blocking). Returns its results and any "
    "still-pending job_id. Call it like get_completed_jobs -- once near the start of a turn."
)


@tool("get_local_completed", GET_LOCAL_DESC, {})
async def get_local_completed(args):
    completed = []
    for job_id, info in list(_local_jobs.items()):
        fut = info["future"]
        if not fut.done():
            continue
        try:
            res = fut.result()
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            res = {"error": f"{type(e).__name__}: {e}", "args": info["args"]}
        completed.append({"job_id": job_id, "results": res})
        _append_jobs_log({"event": "local_completed", "job_id": job_id})
        del _local_jobs[job_id]
    pending = [{"job_id": jid, "args": i["args"]} for jid, i in _local_jobs.items()]
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"completed": completed, "pending": pending}, indent=2, default=str
                ),
            }
        ]
    }


RELEASE_CLAIM_DESC = (
    "Release a claim you are holding on a piece of work, so another agent can take it. "
    "Use it for an orphaned claim from a crashed session: one with no job running and no "
    "results. Pass the same key the job was claimed under."
)


@tool("release_claim", RELEASE_CLAIM_DESC, {"key": str})
async def release_claim(args):
    _release_claim(args["key"])
    return {"content": [{"type": "text", "text": f"released claim on {args['key']}"}]}


NOTIFY_DESC = (
    "Post a short status message (1-2 lines) to the team's chat channel, in your own words. "
    "Use it for: (a) the periodic status summary when asked to report; (b) a notable "
    "milestone; (c) an ALERT when you have hit a problem you cannot get around (e.g. the "
    "compute endpoint is unreachable and submissions keep failing). Set blocking=true ONLY "
    "for a real problem you cannot work around: it tells the runner to shut the agent down "
    "if the problem is not resolved within the grace period. Do not spam -- at most one "
    "alert per distinct problem; a normal summary is one short line."
)


@tool("notify", NOTIFY_DESC, {"message": str, "blocking": bool})
async def notify(args):
    global _problem_since
    blocking = bool(args.get("blocking", False))
    if blocking and _problem_since is None:
        _problem_since = time.time()
    _slack_post(args["message"])
    note = " (blocking: shutdown grace started)" if blocking else ""
    return {"content": [{"type": "text", "text": "notified" + note}]}


CYCLE_DONE_DESC = (
    "Call this when you have finished writing up a cycle, after its record is written. "
    "Pass the cycle's conclusion in one or two lines -- what it established. It marks "
    "the boundary of the cycle in the record. Where this lab runs a reviewer, that "
    "boundary is where it reads: your write-up is checked against the results rows, and "
    "anything they do not support comes back to you next turn. One call per cycle."
)

_cycle_mark = None  # set by cycle_done, read and cleared by the runner


def cycle_done_pending():
    """The conclusion the agent recorded when it last closed a cycle, or None. Reading
    it clears it: a cycle is reviewed once."""
    global _cycle_mark
    mark, _cycle_mark = _cycle_mark, None
    return mark


@tool("cycle_done", CYCLE_DONE_DESC, {"conclusion": str})
async def cycle_done(args):
    global _cycle_mark
    _cycle_mark = (args.get("conclusion") or "").strip() or "(no conclusion given)"
    return {
        "content": [
            {"type": "text", "text": "cycle recorded; its write-up will be reviewed"}
        ]
    }


GOAL_MET_DESC = (
    "Call this when the campaign's goal, as its own prompt states it, is met: the "
    "question is answered to the standard that prompt sets. A good result is not a met "
    "goal, and neither is a result you have not yet recorded -- write the evidence "
    "first, then call this. Pass what settles it, in one or two lines. The run stops "
    "taking new work, finishes what is in flight, and gives you a turn to write up."
)

_goal_met = None  # what the agent said settles the goal, or None


def goal_is_met():
    """What the agent said settles the campaign's goal, or None."""
    return _goal_met


@tool("goal_met", GOAL_MET_DESC, {"reason": str})
async def goal_met(args):
    global _goal_met
    _goal_met = (args.get("reason") or "").strip() or "(no reason given)"
    request_stop()
    return {
        "content": [
            {
                "type": "text",
                "text": "goal recorded; this run is winding down -- collect what is in "
                "flight and write the cycle up",
            }
        ]
    }


CHECK_BACKEND_DESC = (
    "Check backend health when nothing has completed for a long time and you are unsure "
    "whether your jobs are genuinely still queued or the backend is stuck. Returns the "
    "endpoint status and the status of each in-flight task. Decide from it: if the endpoint "
    "is offline or tasks are failed/lost, send notify(blocking=true) so the run shuts down; "
    "if tasks are still waiting or running, it is a normal long queue -- keep waiting."
)


@tool("check_backend", CHECK_BACKEND_DESC, {})
async def check_backend(args):
    import globus_compute_sdk as _gc

    info = {"endpoint_id": ENDPOINT_ID}
    try:
        c = _gc.Client()
    except Exception as e:
        return {
            "content": [
                {"type": "text", "text": json.dumps({"error": f"client init: {e}"})}
            ],
            "is_error": True,
        }
    try:
        info["endpoint_status"] = c.get_endpoint_status(ENDPOINT_ID)
    except Exception as e:
        info["endpoint_status"] = {"error": str(e)}
    tasks = []
    for jid, j in list(_jobs.items()):
        tid = getattr(j["future"], "task_id", None)
        st = None
        if tid:
            try:
                st = c.get_task(tid)
            except Exception as e:
                st = {"error": str(e)}
        tasks.append(
            {
                "job_id": jid,
                "key": j["key"],
                "task_id": tid,
                "future_done": j["future"].done(),
                "task_status": st,
            }
        )
    info["in_flight_tasks"] = tasks
    return {
        "content": [{"type": "text", "text": json.dumps(info, indent=2, default=str)}]
    }


def create_server():
    """MCP server exposing the tools. Each pair is only offered when the task defines
    the matching function -- remote_fn for the remote tools, local_fn for the local."""
    tools = []
    if HAS_REMOTE:
        tools += [submit_job, get_completed_jobs, release_claim]
    if HAS_LOCAL:
        tools += [submit_local, get_local_completed]
    tools.append(notify)
    tools.append(cycle_done)
    tools.append(goal_met)
    if HAS_REMOTE:
        tools.append(check_backend)
    if HAS_TRANSFER:
        tools.append(_transfer.transfer)
    return create_sdk_mcp_server(name="cas", version="1.0.0", tools=tools)


def tool_names():
    """Fully-qualified names for ClaudeAgentOptions(allowed_tools=...)."""
    names = []
    if HAS_REMOTE:
        names += ["submit_job", "get_completed_jobs", "release_claim"]
    if HAS_LOCAL:
        names += ["submit_local", "get_local_completed"]
    names.append("notify")
    names.append("cycle_done")
    names.append("goal_met")
    if HAS_REMOTE:
        names.append("check_backend")
    if HAS_TRANSFER:
        names.append("transfer")
    return [f"mcp__cas__{n}" for n in names]
