"""CAS framework tools: run work on an HPC endpoint via Globus Compute, and
coordinate several agents through a shared directory.

This file is domain-agnostic. WHAT gets submitted comes from a task plug-in
(see the campaign's task.py, and AGENTS.md); this file owns the mechanism around it:
claiming work so two agents never duplicate it, tracking futures, capacity limits,
draining on shutdown, the announcements board, and notification.

Which system to run on is picked with the SYSTEM env var and described in
config.json.
"""

import importlib
import inspect
import itertools
import json
import os
import fcntl
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor
from concurrent.futures import wait as _futures_wait

from claude_agent_sdk import tool, create_sdk_mcp_server

import budgets
import node_hours as _node_hours
import transfer as _transfer
from globus_compute_sdk import Executor
from globus_compute_sdk.serialize import ComputeSerializer, AllCodeStrategies

ROLE = os.environ.get("ROLE", "both")          # free-form; the prompt defines what roles mean

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
_cam = _read_json(os.path.join(_CAMPAIGN_DIR, "campaign.json"),
                  f"campaign '{CAMPAIGN}'", needs=("system",))
SYSTEM = os.environ.get("SYSTEM") or _cam["system"]

_sys_cfg = _read_json(os.path.join(LAB_DIR, "systems", f"{SYSTEM}.json"),
                      f"system '{SYSTEM}'")

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
# Remote work needs both halves: a task that defines the function, and a system that
# sends work somewhere. A task with both functions runs either way, by the system it
# is given.
# The kinds of remote job. A task with several gives TASKS: a name to {"fn", "desc",
# "schema", and "bucket" or "bucket_for", and optionally "key"}, and each becomes a
# tool, submit_<name>. A task with one kind gives remote_fn with JOB_DESC, JOB_SCHEMA,
# job_key and bucket_for, which becomes submit_job. Either may give an estimate --
# estimate_seconds, or "estimate" in a kind's spec -- which runs with the job, before
# it, as estimate(args, timings): `timings` is what earlier jobs of the kind returned
# as `timings`, and the seconds it returns replace the job's needs_seconds.
if getattr(task, "TASKS", None):
    KINDS = {name: dict(spec) for name, spec in task.TASKS.items()}
elif hasattr(task, "remote_fn"):
    KINDS = {"job": {"fn": task.remote_fn, "desc": getattr(task, "JOB_DESC", ""),
                     "schema": getattr(task, "JOB_SCHEMA", {}),
                     "key": getattr(task, "job_key", None),
                     "bucket_for": getattr(task, "bucket_for", None),
                     "estimate": getattr(task, "estimate_seconds", None)}}
else:
    KINDS = {}
MULTI_KIND = bool(getattr(task, "TASKS", None))
HAS_REMOTE = bool(KINDS) and _sys_cfg.get("remote", True)
# Each remote function as it stood when this run started. Every submit sends this
# source, so an edit to the task file while the run is going cannot reach its jobs.
for _k in KINDS.values():
    _k["source"] = __import__("textwrap").dedent(inspect.getsource(_k["fn"]))
    _k["name"] = _k["fn"].__name__
    if _k.get("estimate"):
        _k["estimate_source"] = __import__("textwrap").dedent(
            inspect.getsource(_k["estimate"]))
        _k["estimate_name"] = _k["estimate"].__name__


def _run_remote(source, name, args, target):
    """Runs on the compute system in place of the task's remote function. It first reads
    the batch job (block) it landed in from the scheduler on the node -- PBS or Slurm --
    and hands the task that block as target["block"] (start, walltime_s, end, nodes,
    queue), so a task can size itself to the time left without asking the scheduler.
    A job whose args give `needs_seconds` and that does not fit in the time left is
    handed back with `retry_after` instead of run, so it goes to a fresh block. Then it
    defines the task's function from its source, calls it, and adds `ran_on` to a dict
    result -- the host, when the task started and ended, and the block. Whatever cannot
    be read is left out; the task's own result is otherwise unchanged.

    Shipped by source, like any remote function: everything it uses is imported here."""
    import os
    import re
    import socket
    import subprocess
    import time

    ran_on = {"host": socket.gethostname()}

    def stamp(text, fmt):
        return round(time.mktime(time.strptime(text.strip(), fmt)), 1)

    def secs(text):
        parts = [int(p) for p in re.split(r"[-:]", text.strip())]
        days = parts.pop(0) if len(parts) == 4 else 0
        while len(parts) < 3:
            parts.insert(0, 0)
        return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]

    try:
        if os.environ.get("PBS_JOBID"):
            jid = os.environ["PBS_JOBID"]
            ran_on.update(scheduler="pbs", batch_job=jid.split(".")[0])
            out = subprocess.run(["qstat", "-f", jid], capture_output=True, text=True,
                                 timeout=60).stdout
            f = dict(re.findall(r"^\s+([\w.]+) = (.*)$", out, re.M))
            fmt = "%a %b %d %H:%M:%S %Y"
            if f.get("stime"):
                ran_on["block_start"] = stamp(f["stime"], fmt)
            if f.get("qtime"):
                ran_on["block_queued_at"] = stamp(f["qtime"], fmt)
            if f.get("Resource_List.walltime"):
                ran_on["block_walltime_s"] = secs(f["Resource_List.walltime"])
            if f.get("Resource_List.nodect"):
                ran_on["block_nodes"] = int(f["Resource_List.nodect"])
            if f.get("queue"):
                ran_on["block_queue"] = f["queue"]
        elif os.environ.get("SLURM_JOB_ID"):
            jid = os.environ["SLURM_JOB_ID"]
            ran_on.update(scheduler="slurm", batch_job=jid)
            out = subprocess.run(["scontrol", "show", "job", "-o", jid], capture_output=True,
                                 text=True, timeout=60).stdout
            f = dict(re.findall(r"(\w+)=(\S+)", out))
            fmt = "%Y-%m-%dT%H:%M:%S"
            if f.get("StartTime", "Unknown") != "Unknown":
                ran_on["block_start"] = stamp(f["StartTime"], fmt)
            if f.get("SubmitTime", "Unknown") != "Unknown":
                ran_on["block_queued_at"] = stamp(f["SubmitTime"], fmt)
            if f.get("TimeLimit", "UNLIMITED") not in ("UNLIMITED", "Partition_Limit"):
                ran_on["block_walltime_s"] = secs(f["TimeLimit"])
            if f.get("NumNodes", "").isdigit():
                ran_on["block_nodes"] = int(f["NumNodes"])
            if f.get("Partition"):
                ran_on["block_queue"] = f["Partition"]
    except Exception as e:
        ran_on["block_error"] = f"{type(e).__name__}: {e}"

    block = {k: ran_on[f"block_{k}"] for k in ("start", "walltime_s", "nodes", "queue")
             if f"block_{k}" in ran_on}
    if ran_on.get("batch_job"):
        block["batch_job"] = ran_on["batch_job"]
    need = int((args or {}).get("needs_seconds") or 0) if isinstance(args, dict) else 0
    # The task's estimate from earlier jobs' timings, where it gives one; the job's own
    # needs_seconds otherwise.
    est = target.get("estimate")
    if est:
        try:
            scope = {}
            exec(compile(est["source"], f"<{est['name']}>", "exec"), scope)
            secs_est = scope[est["name"]](args, target.get("timings") or [])
            if secs_est:
                ran_on["needs_seconds"] = {"given": need, "estimated": int(secs_est)}
                need = int(secs_est)
        except Exception as e:
            ran_on["estimate_error"] = f"{type(e).__name__}: {e}"
    if "start" in block and "walltime_s" in block:
        block["end"] = block["start"] + block["walltime_s"]
        left = round(block["end"] - time.time())
        # A margin for starting up and handing back the result.
        if need > left - 30:
            return {"retry_after": max(left - 30, 0) + 60, "ran_on": ran_on,
                    "error": f"{left} s left in block, task needs {need} s"}
    target = {**target, "block": block, "needs_seconds": need}

    started = time.time()
    scope = {}
    exec(compile(source, f"<{name}>", "exec"), scope)
    result = scope[name](args, target)
    ran_on.update(started_at=round(started, 1), ended_at=round(time.time(), 1))
    if isinstance(result, dict):
        result = {**result, "ran_on": ran_on}
    return result

# Only remote jobs need a Globus endpoint and an account to charge, so a task that
# defines local_fn alone runs without either -- and without a user file at all. Its
# work_dir then defaults to the campaign workspace.
_user_path = os.path.join(LAB_DIR, "users", USER_NAME, f"{SYSTEM}.json")

# Named access to the same machine: a second allocation, a facility endpoint. The user
# file's top level is the default, `profiles` the alternatives, the campaign picks one.
PROFILE = os.environ.get("PROFILE") or _cam.get("profile") or ""


def _apply_profile(usr):
    """Merge the named profile over the user file's top level, one key deep."""
    if not PROFILE:
        return usr
    available = usr.get("profiles") or {}
    chosen = available.get(PROFILE)
    if chosen is None:
        sys.exit(f"campaign '{CAMPAIGN}' asks for profile '{PROFILE}', which is not in "
                 f"{_user_path}. It has: {sorted(available) or 'no profiles'}.")
    merged = {k: v for k, v in usr.items() if k != "profiles"}
    merged.update({k: v for k, v in chosen.items() if k != "_comment"})
    return merged


def _check_needed(usr, needs):
    """The same check _read_json makes, after the profile has been merged in."""
    missing = [k for k in needs if not usr.get(k) or str(usr[k]).startswith("<")]
    if missing:
        where = f"{_user_path} (profile '{PROFILE}')" if PROFILE else _user_path
        sys.exit(f"your access to '{SYSTEM}' needs {', '.join(missing)} filled in: {where}")


if HAS_REMOTE:
    # A system with no batch scheduler has nothing to charge, so it says so and the
    # account is not asked for.
    _needs = ("endpoint",)
    if _sys_cfg.get("needs_account", True):
        _needs += ("account",)
    # Check after merging: a profile may be the only place account or endpoint is set.
    _usr = _apply_profile(_read_json(_user_path, f"your access to '{SYSTEM}'"))
    _check_needed(_usr, _needs)
    # Where this campaign's jobs write. WORK_DIR in the environment wins, so a run can
    # be given its own directory the way WORKSPACE_DIR gives it its own workspace;
    # `work_dir` in the user file names it outright; `work_root` is the directory
    # campaigns sit under, with the campaign appended.
    if os.environ.get("WORK_DIR"):
        _usr["work_dir"] = os.environ["WORK_DIR"]
    if not _usr.get("work_dir"):
        _root = _usr.get("work_root")
        if not _root:
            raise SystemExit(
                f"{_user_path} needs 'work_dir' (an exact path) or 'work_root' "
                f"(the directory campaigns sit under; '{CAMPAIGN}' is appended).")
        _usr["work_dir"] = os.path.join(_root, CAMPAIGN)
else:
    _usr = _apply_profile(_read_json(_user_path, f"your access to '{SYSTEM}'")
                          if os.path.isfile(_user_path) else {})
    _usr.setdefault("work_dir", os.environ.get("WORK_DIR")
                    or os.path.join(LAB_DIR, "workspace", CAMPAIGN))

ENDPOINT_ID = _usr.get("endpoint", "")


# A campaign may send work to more than one system: a bucket names another with
# `system`. Each has its own facts (systems/<system>.json), access, endpoint, work_dir
# and Transfer collection. The campaign's `system` is the main one; its settings are also
# kept at the top level of this module (SYSTEM, ENDPOINT_ID, TARGET, WORK_DIR).
def _access(system):
    """A further system's facts and your access to it. Its work_dir is `work_dir` in the
    user file, or `work_root` with the campaign appended, or WORK_DIR_<SYSTEM>; a worker
    gets its own directory under it, named by WORK_SUBDIR, as spawn sets."""
    sys_cfg = _read_json(os.path.join(LAB_DIR, "systems", f"{system}.json"),
                         f"system '{system}'")
    path = os.path.join(LAB_DIR, "users", USER_NAME, f"{system}.json")
    usr = _read_json(path, f"your access to '{system}'")
    needs = ("endpoint",) + (("account",) if sys_cfg.get("needs_account", True) else ())
    missing = [k for k in needs if not usr.get(k) or str(usr[k]).startswith("<")]
    if missing:
        sys.exit(f"your access to '{system}' needs {', '.join(missing)} filled in: {path}")
    work = (os.environ.get(f"WORK_DIR_{system.upper()}") or usr.get("work_dir")
            or (os.path.join(usr["work_root"], CAMPAIGN) if usr.get("work_root") else ""))
    if not work:
        sys.exit(f"{path} needs 'work_dir' (an exact path) or 'work_root' (the directory "
                 f"campaigns sit under; '{CAMPAIGN}' is appended).")
    if os.environ.get("WORK_SUBDIR"):
        work = os.path.join(work, os.environ["WORK_SUBDIR"])
    usr["work_dir"] = work
    return sys_cfg, usr


SYSTEMS = {SYSTEM: {"cfg": _sys_cfg, "usr": _usr}}
for _over in (_cam.get("buckets") or {}).values():
    _s = (_over or {}).get("system")
    if _s and _s not in SYSTEMS:
        _c, _u = _access(_s)
        SYSTEMS[_s] = {"cfg": _c, "usr": _u}
for _sy in SYSTEMS.values():
    _sy["endpoint"] = _sy["usr"].get("endpoint", "")
    _sy["work_dir"] = _sy["usr"].get("work_dir", "")
MULTI_SYSTEM = len(SYSTEMS) > 1

# Globus Transfer is optional: configured per user, and simply absent otherwise. It is
# how the agent reads files on the compute system when the two do not share a filesystem.
# WORKSPACE_DIR rather than the default path under workspace/: a run pointed at another
# workspace fetches into that one, so its files sit with the rest of what it wrote.
_transfer.CFGS = {}
for _name, _sy in SYSTEMS.items():
    _c = _transfer.configure(_sy["usr"], WORKSPACE_DIR, _CAMPAIGN_DIR, _sy["cfg"])
    if _c is not None:
        _transfer.CFGS[_name] = _c
_transfer.MAIN = SYSTEM if SYSTEM in _transfer.CFGS else next(iter(_transfer.CFGS), None)
_transfer.CFG = _transfer.CFGS.get(_transfer.MAIN)
HAS_TRANSFER = bool(_transfer.CFGS)
# Named resource shapes on one system (e.g. a small quick queue and a large long one).
# A task routes a job to one with bucket_for(args); otherwise the default is used.
#
# `resources` in campaign.json is one shape, and is all a campaign needs where its jobs
# are all the same size. A campaign whose jobs differ instead gives `buckets`: a name to
# a shape, each filling in from `resources` for what it does not say, and the first named
# is the default. Each bucket gets its own executor and its own block pool on the endpoint.
# A bucket on another system starts from that system's defaults and your access to it;
# the campaign's `resources` describe its main system.
def _bucket_defaults(system):
    sy = SYSTEMS[system]
    d = dict(sy["cfg"].get("bucket_defaults", {}))
    if system == SYSTEM:
        d.update(_cam.get("resources", {}))         # campaign: queue, walltime, nodes
    d.update(sy["usr"].get("resources", {}))        # user: anything they must override
    d["account"] = sy["usr"].get("account", "")
    return d


_cam_buckets = _cam.get("buckets") or {"default": {}}
_SYS = {"buckets": {}}
for _name, _over in _cam_buckets.items():
    _over = dict(_over or {})
    _bsys = _over.pop("system", None) or SYSTEM
    _cfg = _bucket_defaults(_bsys)
    _cfg.update(_over)
    # A bucket's own cap on jobs in flight. Popped out: user_config goes to the endpoint
    # as the batch system's configuration and this is not one of its settings.
    _cap = _cfg.pop("max_concurrent", None)
    # A bucket's own budget of submits for this run, set in campaign.json. Popped out for
    # the same reason; a role's per-bucket budget (MAX_SUBMITS_BUCKETS) overrides it.
    _bsub = _cfg.pop("max_submits", None)
    # num_nodes is the old spelling; send only nodes_per_block, which MEPs expect.
    _npb = _cfg.pop("num_nodes", None)
    _npb = _cfg.setdefault("nodes_per_block", 1 if _npb is None else _npb)
    # The nodes one job gets. By default the whole block, which runs its jobs one after
    # another; at 1, every node of the block runs a job of its own, which needs a worker
    # on every node rather than only the first (MpiExecLauncher). Popped out: it is the
    # framework's, not the endpoint's.
    _npj = int(_cfg.pop("nodes_per_job", _npb))
    if _npj not in (1, int(_npb)):
        raise SystemExit(f"bucket {_name!r}: nodes_per_job must be 1 or nodes_per_block "
                         f"({_npb}), not {_npj}")
    if _npj < int(_npb):
        _cfg.setdefault("launcher_type", "MpiExecLauncher")
        _cfg.setdefault("max_workers_per_node", 1)
    # Each worker runs a job, so a block runs its job slots (one per node, or the one
    # block) times the workers in each; the endpoint templates default to one.
    _wpn = int(_cfg.get("max_workers_per_node", 1))
    _SYS["buckets"][_name] = {"num_nodes": _npb, "nodes_per_block": _npb,
                              "nodes_per_job": _npj, "workers_per_node": _wpn,
                              "jobs_per_block": int(_npb) // _npj * _wpn,
                              "system": _bsys, "user_config": _cfg,
                              "max_concurrent": int(_cap) if _cap else None,
                              "max_submits": int(_bsub) if _bsub is not None else None,
                              # The endpoint templates' default for max_idletime.
                              "idle_s": int(_cfg.get("max_idletime", 600))}
_default_bucket = next(iter(_SYS["buckets"]))

# The batch jobs (blocks) remote work runs in, for the submit tools' descriptions: a job
# sized past its block's walltime can never run.
def _block_shape(name):
    b = _SYS["buckets"][name]
    jobs = ""
    if b["jobs_per_block"] > 1:
        jobs = (f", {b['jobs_per_block']} jobs at once, {b['workers_per_node']} per node"
                if b["workers_per_node"] > 1 and b["nodes_per_job"] == 1 else
                f", {b['jobs_per_block']} jobs at once" if b["workers_per_node"] > 1 else
                f", {b['jobs_per_block']} jobs of {b['nodes_per_job']} node(s) at once")
    return (f"- {name}{' on ' + b['system'] if MULTI_SYSTEM else ''}: "
            f"{b['nodes_per_block']} node(s){jobs}, walltime "
            f"{b['user_config'].get('walltime') or 'unset'}, queue "
            f"{b['user_config'].get('queue') or 'unset'}")


_BLOCKS_DESC = "\n\nJobs run in batch jobs (blocks) of this shape:\n" + "\n".join(
    _block_shape(name) for name in _SYS["buckets"]) + (
    "\nA block runs one job after another, unless its shape says several at once, and "
    "may be shared with other agents, so a job can start in a block with less than its "
    "walltime left. A job never gets more than its block's walltime.")

# Total jobs in flight, across all buckets. Defaults to the sum of the bucket caps, a
# bucket without one counting the jobs one block runs at once.
_bucket_sum = sum(b["max_concurrent"] or b["jobs_per_block"] for b in _SYS["buckets"].values())
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT",
                                    _cam.get("max_concurrent",
                                             _sys_cfg.get("max_concurrent", _bucket_sum))))

# A target is handed to the task's remote function. Everything the remote side needs
# must be in it: the function is shipped source-only and cannot read this module. One per
# system: the system's own, then the campaign's `target`, then its `targets[system]`.
def _target(system):
    sy = SYSTEMS[system]
    t = dict(sy["cfg"].get("target", {}))
    cam_t = dict(_cam.get("target", {}))
    per = dict((_cam.get("targets") or {}).get(system, {}))
    # env merges key by key, so a campaign adds to the system's environment rather than
    # replacing it. Everything else the campaign sets wins outright.
    t["env"] = {**t.get("env", {}), **cam_t.pop("env", {}), **per.pop("env", {})}
    t.update(cam_t)
    t.update(per)
    t["work_dir"] = sy["work_dir"]
    t.setdefault("ppn", sy["cfg"].get("ppn", 1))
    return t


for _name in SYSTEMS:
    SYSTEMS[_name]["target"] = _target(_name)
TARGET = SYSTEMS[SYSTEM]["target"]
WORK_DIR = _usr["work_dir"]     # surfaced at preflight and recorded in meta.json
TARGET["nranks"] = _SYS["buckets"][_default_bucket]["nodes_per_job"] * TARGET["ppn"]

# Node-hours the whole launch -- the top run and its workers -- may use on each system,
# counted from the blocks its tasks ran in (node_hours.py). Experimental, and a soft cap:
# it stops new submits, but tasks already sent still run. MAX_NODE_HOURS is a number for
# the main system or a JSON object by system; otherwise campaign.json's `node_hours`.
_nh = os.environ.get("MAX_NODE_HOURS", "").strip()
NODE_HOURS = ({k: float(v) for k, v in json.loads(_nh).items()} if _nh.startswith("{")
              else {SYSTEM: float(_nh)} if _nh
              else {k: float(v) for k, v in (_cam.get("node_hours") or {}).items()})


def node_hours_used():
    """Node-hours this launch has used so far, by system."""
    return _node_hours.used(WORKSPACE_DIR,
                            _node_hours.launch_runs(WORKSPACE_DIR, os.environ.get("RUN_ID", "")))


REMOTE_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "43200"))   # 12h client-side wait
LOCAL_TIMEOUT = int(os.environ.get("LOCAL_JOB_TIMEOUT", "14400"))     # 4h
# The same, for jobs run on this machine. One by default: a local job is assumed to use
# the whole thing, and a task whose jobs are small enough to share it says so.
LOCAL_MAX_CONCURRENT = int(os.environ.get("LOCAL_MAX_CONCURRENT",
                                          _cam.get("local_max_concurrent",
                                                   _sys_cfg.get("local_max_concurrent", 1))))

# Whether a wait returns on the first job to finish or once they have all finished.
# Jobs that take similar times are read together, so waking on the first spends a turn
# on a fraction of the answer; jobs whose durations vary widely are read as they land.
BATCH_MODE = str(os.environ.get("BATCH_MODE",
                                _cam.get("batch_mode",
                                         _sys_cfg.get("batch_mode", False)))).lower() \
    in ("1", "true", "yes")
# Jobs a batch submit fires in one call. The capacity of whichever backend the run uses,
# by default: a batch that does not fill it leaves slots idle for as long as the batch
# takes, and one larger than it is refused.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE",
                                _cam.get("batch_size",
                                         _sys_cfg.get("batch_size",
                                                      MAX_CONCURRENT if HAS_REMOTE
                                                      else LOCAL_MAX_CONCURRENT))))

# One Executor per bucket, created lazily and reused. Each distinct user_endpoint_config
# gets its own block pool on the endpoint, so buckets can run concurrently.
_executors = {}
_sa_executor = None        # local backend, created lazily

_jobs = {}                 # remote: job_id -> {"future", "args", "key", "bucket"}
_job_counter = itertools.count(1)
_submit_count = 0
MAX_SUBMITS = int(os.environ.get("MAX_SUBMITS", "60"))   # backstop on total jobs per run
# Budgets per bucket, for work that differs in cost -- cheap CPU checks on one system,
# GPU scores on another. MAX_SUBMITS_BUCKETS (JSON, as spawn sets it from a role) wins
# over each bucket's own `max_submits`; a bucket with neither is limited only by
# MAX_SUBMITS. A submit a task declines gives its submit back, here as in the total.
BUCKET_SUBMITS = {b: v["max_submits"] for b, v in _SYS["buckets"].items()
                  if v.get("max_submits") is not None}
BUCKET_SUBMITS.update({b: int(n) for b, n in
                       json.loads(os.environ.get("MAX_SUBMITS_BUCKETS") or "{}").items()})
_bucket_submit_count = {b: 0 for b in _SYS["buckets"]}

_local_jobs = {}           # local: job_id -> {"future", "args"}
_local_counter = itertools.count(1)
_local_submit_count = 0

# Wind-down flag. When set, submitting NEW work is refused so outstanding jobs can
# drain and the run can end cleanly. Collecting finished work is unaffected.
_stop_requested = False

JOBS_LOG = os.path.join(WORKSPACE_DIR, "jobs.jsonl")          # durable record of every job fired

# --- Shared announcements board -------------------------------------------------
# One plain text file. Anyone (an operator, a Slack bridge) appends lines; every
# agent reads it and decides what applies to it. No routing, no per-agent state.
ANNOUNCEMENTS_FILE = os.path.join(WORKSPACE_DIR, "ANNOUNCEMENTS.md")
# Both sides of the conversation with whoever is watching, in the order they were said.
# The agent's own messages are written here whether or not a transport is configured, so
# a lab with no transport still has somewhere for notify to land.
MESSAGES_FILE = os.path.join(WORKSPACE_DIR, "MESSAGES.md")

NOTIFY_SCRIPT = os.path.join(SCRIPT_DIR, "notify.sh")
_last_success_time = None   # last non-error completion (real progress)
_problem_since = None       # when the agent flagged a blocking problem (None = none)


def record_message(who, msg):
    """Add one line to the run's conversation. Best-effort: a record nobody can write
    is not a reason to lose a message or fail a turn."""
    try:
        with open(MESSAGES_FILE, "a") as f:
            f.write(f"`{time.strftime('%H:%M')}` **{who}** {msg.strip()}\n\n")
    except Exception:
        pass


def _slack_post(msg):
    """Best-effort post to the lab. Never raises; a transport is optional."""
    if not os.path.isfile(NOTIFY_SCRIPT):
        return
    try:
        subprocess.run(["bash", NOTIFY_SCRIPT, msg], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            return None   # not populated yet (just submitted) -> not stuck
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


TIMINGS_KEPT = 50


def _timings(kind):
    """The `timings` of the latest finished jobs of a kind, by any agent in any run of
    the campaign, newest last."""
    out = []
    try:
        with open(JOBS_LOG) as f:
            for line in f:
                if '"timings"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("event") == "completed" and r.get("kind", "job") == kind:
                    out.append(r["timings"])
    except OSError:
        pass
    return out[-TIMINGS_KEPT:]


def _append_jobs_log(record):
    # The run that fired it: the log accumulates across every run of a campaign, so
    # without this there is no way to say what this run did.
    record = {"run": os.environ.get("RUN_ID", ""), "ts": round(time.time(), 1), **record}
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
                f.write(json.dumps({"key": key, "stage": stage, "agent": AGENT_ID,
                                    "ts": time.time(), "state": "claimed"}) + "\n")
            return True, None
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _release_claim(key):
    try:
        with open(CLAIMS_LOCK, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                with open(CLAIMS_FILE, "a") as f:
                    f.write(json.dumps({"key": key, "agent": AGENT_ID,
                                        "ts": time.time(), "state": "done"}) + "\n")
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except Exception:
        pass


# --- turn bookkeeping for agent.py's outer loop ---------------------------------
def request_stop():
    """Wind this run down: refuse NEW work so outstanding jobs can drain. Set by
    agent.py on a stop request, spent budget, or time limit. Collecting is unaffected."""
    global _stop_requested
    _stop_requested = True


def resume_submits():
    """Take back a wind-down: a run whose budget was spent, or whose time was up, has had
    it raised and goes on."""
    global _stop_requested
    _stop_requested = False


def stop_is_requested():
    return _stop_requested


def submit_count():
    """Jobs submitted this process that MAX_SUBMITS caps. Remote where the task has
    remote_fn, else the local jobs -- so a local-only run is bounded the same way."""
    return _submit_count if HAS_REMOTE else _local_submit_count


def bucket_submit_counts():
    """Remote jobs submitted this process, per bucket."""
    return dict(_bucket_submit_count)


# The limits in force now. A run's budgets file holds them (budgets.py) and may be raised
# while the run goes, so they are read there at each check; MAX_SUBMITS, BUCKET_SUBMITS
# and NODE_HOURS are what the run starts with, and what holds before the file exists.
def max_submits():
    """Jobs this run may submit, or None for no limit."""
    return budgets.limit("submits", fallback=MAX_SUBMITS)


def bucket_submits():
    """Jobs this run may submit to each bucket that has a limit."""
    out = {}
    for b in _SYS["buckets"]:
        n = budgets.limit("submits_buckets", b, fallback=BUCKET_SUBMITS.get(b))
        if n is not None:
            out[b] = n
    return out


def node_hours_cap(system):
    """Node-hours the launch may use on `system`, or None. The budget is the top run's,
    which its workers share."""
    return budgets.limit("node_hours", system, fallback=NODE_HOURS.get(system),
                         directory=budgets.top_run_dir())


def submits_start_workers():
    """Whether this run's submits budget counts workers it starts: its local jobs each
    run a worker agent (spawn.py) and it sends no remote work, which the budget would
    count instead."""
    return not HAS_REMOTE and "spawn" in sys.modules


def submits_label():
    """What the submits budget counts, as anything showing it names it."""
    return "workers started" if submits_start_workers() else "jobs submitted"


def _reached(count, cap):
    return cap is not None and count >= cap


def budget_spent():
    """Whether this run can submit nothing more: the total is spent, or every bucket has
    a budget and each is spent."""
    if _reached(submit_count(), max_submits()):
        return True
    caps = bucket_submits()
    return bool(HAS_REMOTE and caps and not HAS_LOCAL
                and all(b in caps for b in _SYS["buckets"])
                and all(_bucket_submit_count.get(b, 0) >= n for b, n in caps.items()))


def local_submit_count():
    """Local jobs submitted this process (observability only -- no cap)."""
    return _local_submit_count


def _remote_pending_count(bucket=None):
    return sum(1 for j in _jobs.values() if not j["future"].done()
               and (bucket is None or j["bucket"] == bucket))


def _bucket_of(args, kind="job"):
    """Which bucket this job would go to, before it is fired."""
    k = KINDS[kind]
    if k.get("bucket"):
        return k["bucket"]
    return k["bucket_for"](args) if k.get("bucket_for") else _default_bucket


def _key_of(args, kind="job"):
    k = KINDS[kind]
    if k.get("key"):
        return k["key"](args)
    return f"{kind}:" + json.dumps(args, sort_keys=True)


def _local_pending_count():
    return sum(1 for j in _local_jobs.values() if not j["future"].done())


def jobs_in_flight():
    """Jobs submitted but not yet collected (pending + done-but-uncollected)."""
    return len(_jobs) + len(_local_jobs)


def pending_count():
    """In-flight jobs whose future is not yet done."""
    return _remote_pending_count() + _local_pending_count()


def wait_for_any(timeout=1800):
    """Block until in-flight futures complete, or timeout; return how many finished.
    Lets agent.py wait between turns without spinning or holding the LLM open. Under
    BATCH_MODE the wait returns once they have all finished."""
    futs = [j["future"] for j in _jobs.values() if not j["future"].done()]
    futs += [j["future"] for j in _local_jobs.values() if not j["future"].done()]
    if not futs:
        return 0
    done, not_done = _futures_wait(futs, timeout=timeout,
                                   return_when=ALL_COMPLETED if BATCH_MODE
                                   else FIRST_COMPLETED)
    if BATCH_MODE and not_done:
        return 0                    # the timeout, not the batch: there is more to come
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
        endpoint = SYSTEMS[_SYS["buckets"][bucket]["system"]]["endpoint"]
        ex = Executor(endpoint_id=endpoint, user_endpoint_config=cfg)
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
            # the rest of shutdown from happening. cancel_futures drops the queued work
            # that has not started; a job already running is left to the exit below.
            _sa_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _sa_executor = None


# --- tools the agent calls ------------------------------------------------------
_WINDDOWN_REFUSAL = ("submit refused: this run is winding down. Collect and log the work "
                     "already in flight, but do not submit anything new.")


# The task states one job's parameters; a batch takes a list of them. Built here rather
# than asked of the task, so a task written for single submits needs no change.
_JSON_TYPES = {float: "number", int: "integer", str: "string", bool: "boolean"}


def _batch_schema(schema):
    props = {name: {"type": _JSON_TYPES.get(typ, "string")}
             for name, typ in (schema or {}).items()}
    return {"type": "object",
            "properties": {"jobs": {"type": "array", "minItems": 1,
                                    "maxItems": BATCH_SIZE,
                                    "items": {"type": "object", "properties": props}}},
            "required": ["jobs"]}


# A task may answer that it cannot run where it landed -- a batch job too close to its
# end, say -- with {"retry_after": seconds}. The job then stays in flight and is submitted
# again after that wait, without the agent's involvement. The task asks for a wait; the
# framework caps it at the bucket's idle time plus RETRY_MARGIN, since a block left idle
# that long is released and the next submission gets a fresh one. A block another job
# keeps busy refuses again, which costs seconds, so retries are unlimited unless
# RETRY_LIMIT is set.
RETRY_LIMIT = int(os.environ.get("RETRY_LIMIT", "0")) or None
RETRY_MARGIN = 60
# A submission the Globus Compute service turns away before the task reaches the
# endpoint: several agents submitting at once ("already in use: possibly due to
# concurrent requests -- please try again"), or a user endpoint still starting ("did not
# signal readiness"). The service says to try again, so the job is resubmitted after a
# short wait, a few times, before it counts as failed.
SUBMIT_RETRIES = 5
SUBMIT_RETRY_WAIT = 20


def _service_refused(exc):
    text = f"{getattr(exc, 'code', '') or ''} {exc}"
    return ("RESOURCE_CONFLICT" in text or "already in use" in text
            or "did not signal readiness" in text)


# A task lost with the node it ran on, as when its block is ended at walltime while the
# task is starting there (Parsl's ManagerLost). It is submitted again, to run elsewhere.
LOST_RETRIES = 2


def _manager_lost(exc):
    return "ManagerLost" in str(exc)


def _log_task_id(job_id, fut):
    """Record a submission's Globus task id in the jobs log, so the task can be looked up
    with the service. The executor sets it once its batch submit returns, after submit()
    has handed back the future, so it is waited for in the background."""
    def wait():
        for _ in range(600):
            tid = getattr(fut, "task_id", None)
            if tid:
                _append_jobs_log({"event": "task", "job_id": job_id, "task_id": tid})
                return
            if fut.done():
                return
            time.sleep(0.5)
    threading.Thread(target=wait, daemon=True).start()


class _RetryingFuture(Future):
    """One job across its submissions: done when a submission returns anything other
    than a retry_after answer, or when the retries are spent."""

    def __init__(self, submit, job_id=None, max_wait=3600):
        super().__init__()
        self._submit = submit
        self._max_wait = max_wait
        self._inner = None
        self.job_id = job_id
        self.retries = []
        self.refusals = 0
        self.lost = 0
        self._start()

    @property
    def task_id(self):
        return getattr(self._inner, "task_id", None)

    def _start(self):
        self._inner = self._submit()
        self._inner.add_done_callback(self._landed)
        _log_task_id(self.job_id, self._inner)

    def _landed(self, inner):
        if self.done():
            return
        try:
            res = inner.result()
        except BaseException as e:
            if _service_refused(e) and self.refusals < SUBMIT_RETRIES:
                self.refusals += 1
                _append_jobs_log({"event": "resubmit", "job_id": self.job_id,
                                  "after_s": SUBMIT_RETRY_WAIT,
                                  "reason": str(e).splitlines()[0][:200]})
                timer = threading.Timer(SUBMIT_RETRY_WAIT * self.refusals, self._again)
                timer.daemon = True
                timer.start()
                return
            if _manager_lost(e) and self.lost < LOST_RETRIES:
                self.lost += 1
                _append_jobs_log({"event": "resubmit", "job_id": self.job_id, "after_s": 0,
                                  "reason": "ManagerLost: the task's node was lost"})
                self._again()
                return
            self.set_exception(e)
            return
        wait = res.get("retry_after") if isinstance(res, dict) else None
        if RETRY_LIMIT is not None and len(self.retries) >= RETRY_LIMIT:
            wait = None
        if wait is not None:
            try:
                wait = max(0.0, min(float(wait), self._max_wait))
            except (TypeError, ValueError):
                wait = None
        if wait is not None:
            self.retries.append({"after_s": round(wait), "reason": res.get("error")})
            _append_jobs_log({"event": "retry", "job_id": self.job_id, "after_s": round(wait),
                              "reason": res.get("error"), "ran_on": res.get("ran_on")})
            timer = threading.Timer(wait, self._again)
            timer.daemon = True
            timer.start()
            return
        if isinstance(res, dict) and self.retries:
            res = {**res, "retries": self.retries}
        self.set_result(res)

    def _again(self):
        if self.done():
            return
        try:
            self._start()
        except Exception as e:
            self.set_exception(e)

    def cancel(self):
        cancelled = super().cancel()
        if self._inner is not None:
            self._inner.cancel()
        return cancelled


def _fire_remote(args, kind="job"):
    """Claim one piece of remote work and start it. Returns {"job_id", "key"}, or
    {"error"} for work already in flight, claimed elsewhere, or refused by the backend."""
    global _submit_count
    key = _key_of(args, kind)
    # A fresh-context agent cannot remember what it already fired, so refuse a repeat.
    for info in _jobs.values():
        if info["key"] == key and not info["future"].done():
            return {"error": f"submit refused: a job for {key} is already in flight. "
                             f"Collect it with get_completed_jobs before re-submitting."}
    ok, holder = _try_claim(key, stage=ROLE)
    if not ok:
        return {"error": f"submit skipped: {key} is already claimed by {holder}. "
                         f"Pick different work."}

    bucket = _bucket_of(args, kind)
    if bucket not in _SYS["buckets"]:
        return {"error": f"bucket {bucket!r} is not one of "
                         f"{sorted(_SYS['buckets'])} -- check the bucket in task.py."}
    _b = _SYS["buckets"][bucket]
    _nh_cap = node_hours_cap(_b["system"])
    if _nh_cap is not None:
        _nh_used = node_hours_used().get(_b["system"], 0)
        if _nh_used >= _nh_cap:
            _release_claim(key)
            return {"error": f"submit refused: {_b['system']} node-hours for this launch "
                             f"are used ({_nh_used:.1f} of {_nh_cap:g})."}
    target = dict(SYSTEMS[_b["system"]]["target"])
    target["nranks"] = _b.get("nodes_per_job", 1) * target["ppn"]
    # The shape the job actually got, so it can size itself to the allocation it is in.
    target["bucket"] = bucket
    target["num_nodes"] = _b.get("nodes_per_job", 1)
    target["walltime"] = _b.get("user_config", {}).get("walltime", "")
    if KINDS[kind].get("estimate_source"):
        target["estimate"] = {"source": KINDS[kind]["estimate_source"],
                              "name": KINDS[kind]["estimate_name"]}
        target["timings"] = _timings(kind)
    job_id = next(_job_counter)
    try:
        fut = _RetryingFuture(lambda: get_executor(bucket).submit(
                                  _run_remote, KINDS[kind]["source"], KINDS[kind]["name"],
                                  args, target),
                              job_id=job_id, max_wait=_b["idle_s"] + RETRY_MARGIN)
    except Exception as e:
        _release_claim(key)
        traceback.print_exc(file=sys.stderr)
        return {"error": f"submit failed: {e}"}

    _submit_count += 1
    _bucket_submit_count[bucket] = _bucket_submit_count.get(bucket, 0) + 1
    _jobs[job_id] = {"future": fut, "args": args, "key": key, "bucket": bucket, "kind": kind}
    _append_jobs_log({"event": "submit", "job_id": job_id, "key": key, "args": args,
                      "kind": kind, "bucket": bucket, "system": _b["system"],
                      "idle_s": _b["idle_s"]})
    return {"job_id": job_id, "key": key}


async def _submit_one(args, kind="job"):
    """Fire one remote job of a kind and return immediately with a job_id."""
    global _submit_count
    if _stop_requested:
        return {"content": [{"type": "text", "text": _WINDDOWN_REFUSAL}], "is_error": True}
    if _reached(_submit_count, max_submits()):
        return {"content": [{"type": "text", "text":
                f"submit refused: this run has used its {max_submits()} submits"}],
                "is_error": True}
    if _remote_pending_count() >= MAX_CONCURRENT:
        return {"content": [{"type": "text", "text":
                f"submit refused: at capacity ({_remote_pending_count()} running/queued, "
                f"max_concurrent={MAX_CONCURRENT}). Collect a finished job "
                f"with get_completed_jobs before submitting more."}], "is_error": True}
    _b = _bucket_of(args, kind)
    _lim = bucket_submits().get(_b)
    if _reached(_bucket_submit_count.get(_b, 0), _lim):
        return {"content": [{"type": "text", "text":
                f"submit refused: bucket {_b!r} has used its {_lim} submits for this run"}],
                "is_error": True}
    _cap = _SYS["buckets"].get(_b, {}).get("max_concurrent")
    if _cap and _remote_pending_count(_b) >= _cap:
        return {"content": [{"type": "text", "text":
                f"submit refused: bucket {_b!r} is at capacity "
                f"({_remote_pending_count(_b)} running/queued, max_concurrent={_cap}). "
                f"Collect one of its jobs, or submit work that routes elsewhere."}],
                "is_error": True}

    fired = _fire_remote(args, kind)
    if "error" in fired:
        return {"content": [{"type": "text", "text": fired["error"]}], "is_error": True}
    job_id, key = fired["job_id"], fired["key"]
    # The budget left, with the job that was just accepted counted. It changes as the
    # run goes, so it belongs in what a submit answers rather than in a prompt written
    # once at the start -- and it counts this run's submits, not the rows in a record
    # that outlives the run.
    out = {"job_id": job_id, "key": key,
           "submits_used": _submit_count, "submits_allowed": max_submits()}
    if bucket_submits():
        out["bucket_submits"] = {b: {"used": _bucket_submit_count.get(b, 0), "allowed": n}
                                 for b, n in bucket_submits().items()}
    return {"content": [{"type": "text", "text": json.dumps(out)}]}


@tool("submit_job", getattr(task, "JOB_DESC", "") + _BLOCKS_DESC, getattr(task, "JOB_SCHEMA", {}))
async def submit_job(args):
    """Fire one remote job and return immediately with a job_id."""
    return await _submit_one(args)


def _kind_tool(kind):
    """submit_<kind>, for a task that gives TASKS. Its description names the blocks its
    jobs run in: the kind's bucket, or every bucket where the kind chooses per job."""
    spec = KINDS[kind]
    named = [spec["bucket"]] if spec.get("bucket") else list(_SYS["buckets"])
    blocks = "\n\nJobs run in batch jobs (blocks) of this shape:\n" + "\n".join(
        _block_shape(n) for n in named if n in _SYS["buckets"]) + \
        _BLOCKS_DESC[_BLOCKS_DESC.index("\nA block"):]

    @tool(f"submit_{kind}", spec.get("desc", "") + blocks, spec.get("schema", {}))
    async def submit(args):
        return await _submit_one(args, kind)
    return submit


KIND_TOOLS = {kind: _kind_tool(kind) for kind in KINDS} if MULTI_KIND else {}


JOB_BATCH_DESC = (getattr(task, "JOB_DESC", "") + _BLOCKS_DESC +
                  f"\n\nSubmit {BATCH_SIZE} jobs in one call, as `jobs`: a list of "
                  f"{BATCH_SIZE} parameter sets, each of the form above. They run at "
                  f"the same time and are collected together with get_completed_jobs.")


@tool("submit_job_batch", JOB_BATCH_DESC,
      _batch_schema(getattr(task, "JOB_SCHEMA", {})))
async def submit_job_batch(args):
    """Fire a whole batch of remote jobs and return immediately with their job_ids."""
    if _stop_requested:
        return {"content": [{"type": "text", "text": _WINDDOWN_REFUSAL}], "is_error": True}
    jobs = args.get("jobs") or []
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        return {"content": [{"type": "text", "text":
                "submit refused: `jobs` must be a list of parameter sets"}], "is_error": True}
    room = MAX_CONCURRENT - _remote_pending_count()
    cap = max_submits()
    left = cap - _submit_count if cap is not None else BATCH_SIZE
    wanted = min(BATCH_SIZE, room, left)
    if wanted <= 0:
        return {"content": [{"type": "text", "text":
                f"submit refused: {_remote_pending_count()} job(s) running of "
                f"{MAX_CONCURRENT}, {max(left, 0)} of {cap} submits left. "
                f"Collect with get_completed_jobs."}], "is_error": True}
    if len(jobs) != wanted:
        return {"content": [{"type": "text", "text":
                f"submit refused: this batch takes {wanted} jobs, got {len(jobs)}"}],
                "is_error": True}
    fired = [dict(_fire_remote(j), args=j) for j in jobs]
    out = {"submitted": [f for f in fired if "error" not in f],
           "refused": [f for f in fired if "error" in f],
           "submits_used": _submit_count, "submits_allowed": max_submits()}
    return {"content": [{"type": "text", "text": json.dumps(out)}]}


GET_COMPLETED_DESC = (
    "Collect any finished jobs (non-blocking). Returns completed results and the list of "
    "still-pending job_ids. Call it once near the start of a turn; jobs still running come "
    "back on a later turn."
)


# A job's result may carry `scores`: named numbers the campaign defines, such as a check
# and the measure the campaign is judged by. The latest value of each is kept for the
# run and handed to SCORES_HOOK, which agent.py sets to record them in meta.json, so
# anything reading the run finds them in its own directory. campaign.json may name the
# one that counts (`primary_score`).
PRIMARY_SCORE = _cam.get("primary_score")
SCORES_HOOK = None
_scores = {}


def _record_scores(res):
    named = res.get("scores") if isinstance(res, dict) else None
    if not isinstance(named, dict):
        return
    fresh = {str(k): v for k, v in named.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not fresh:
        return
    _scores.update(fresh)
    if SCORES_HOOK is not None:
        try:
            SCORES_HOOK(dict(_scores))
        except Exception as e:
            print(f"[scores] could not record (ignored): {e}", flush=True)


@tool("get_completed_jobs", GET_COMPLETED_DESC, {})
async def get_completed_jobs(args):
    global _last_success_time, _problem_since, _submit_count
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
        _record_scores(res)
        res["job_id"] = job_id
        res.setdefault("key", info["key"])
        # A job the task declined to run, such as one that would not have fitted in the
        # time its batch job had left, did no work, so it does not count as a submit.
        if res.get("refused"):
            _submit_count -= 1
            _bucket_submit_count[info["bucket"]] -= 1
            res["submits_used"] = _submit_count
        if "error" not in res:
            _last_success_time = time.time()
            _problem_since = None        # real progress clears any flagged problem
        completed.append(res)
        # When the task started and how long it ran, where it reports them: with the
        # submit's ts, they give how long the job waited.
        # `timings` is kept for the kind's estimate of later jobs.
        _append_jobs_log({"event": "completed", "job_id": job_id, "key": info["key"],
                          "kind": info["kind"],
                          "error": "error" in res, "refused": bool(res.get("refused")),
                          **{k: res[k] for k in ("started_at", "wall_seconds", "ran_on",
                                                 "timings") if k in res}})
        _release_claim(info["key"])
        del _jobs[job_id]
    pending = [{"job_id": jid, "key": i["key"], "args": i["args"], "bucket": i["bucket"]}
               for jid, i in _jobs.items()]
    return {"content": [{"type": "text", "text":
            json.dumps({"completed": completed, "pending": pending}, indent=2, default=str)}]}


@tool("submit_local", getattr(task, "LOCAL_DESC", ""), getattr(task, "LOCAL_SCHEMA", {}))
async def submit_local(args):
    """Fire the local comparator and return immediately with a job_id."""
    global _local_submit_count
    if _stop_requested:
        return {"content": [{"type": "text", "text": _WINDDOWN_REFUSAL}], "is_error": True}
    if not HAS_REMOTE and _reached(_local_submit_count, max_submits()):
        what = "workers" if submits_start_workers() else "submits"
        return {"content": [{"type": "text", "text":
                f"submit refused: this run has used its {max_submits()} {what}"}],
                "is_error": True}
    if _local_pending_count() >= LOCAL_MAX_CONCURRENT:
        return {"content": [{"type": "text", "text":
                f"submit refused: a local job is already running (max {LOCAL_MAX_CONCURRENT} "
                f"at a time -- it uses the whole node). Collect it with get_local_completed "
                f"before submitting more."}], "is_error": True}
    job_id = _fire_local(args)
    # As for submit_job. Where the task has remote work too, MAX_SUBMITS caps that and
    # local jobs are uncapped, so there is a count to report and no allowance.
    out = {"job_id": job_id, "args": args, "local_submits_used": _local_submit_count}
    if not HAS_REMOTE:
        out["submits_allowed"] = max_submits()
    return {"content": [{"type": "text", "text": json.dumps(out)}]}


def _fire_local(args):
    """Start one local job and record it. Returns its job_id."""
    global _local_submit_count
    fut = get_local_executor().submit(task.local_fn, args)
    job_id = next(_local_counter)
    _local_submit_count += 1
    _local_jobs[job_id] = {"future": fut, "args": args}
    _append_jobs_log({"event": "local_submit", "job_id": job_id, "args": args})
    return job_id


BATCH_DESC = (getattr(task, "LOCAL_DESC", "") +
              f"\n\nSubmit {BATCH_SIZE} jobs in one call, as `jobs`: a list of "
              f"{BATCH_SIZE} parameter sets, each of the form above. They run at the "
              f"same time and are collected together with get_local_completed.")


@tool("submit_local_batch", BATCH_DESC,
      _batch_schema(getattr(task, "LOCAL_SCHEMA", {})))
async def submit_local_batch(args):
    """Fire a whole batch of local jobs and return immediately with their job_ids."""
    if _stop_requested:
        return {"content": [{"type": "text", "text": _WINDDOWN_REFUSAL}], "is_error": True}
    jobs = args.get("jobs") or []
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        return {"content": [{"type": "text", "text":
                "submit refused: `jobs` must be a list of parameter sets"}], "is_error": True}
    room = LOCAL_MAX_CONCURRENT - _local_pending_count()
    cap = max_submits() if not HAS_REMOTE else None
    left = cap - _local_submit_count if cap is not None else len(jobs)
    wanted = min(BATCH_SIZE, room, left)
    if wanted <= 0:
        return {"content": [{"type": "text", "text":
                f"submit refused: {_local_pending_count()} job(s) still running of "
                f"{LOCAL_MAX_CONCURRENT}, {max(left, 0)} of {cap} submits left. "
                f"Collect with get_local_completed."}], "is_error": True}
    if len(jobs) != wanted:
        return {"content": [{"type": "text", "text":
                f"submit refused: this batch takes {wanted} jobs, got {len(jobs)}"}],
                "is_error": True}
    fired = [{"job_id": _fire_local(j), "args": j} for j in jobs]
    out = {"submitted": fired, "local_submits_used": _local_submit_count}
    if not HAS_REMOTE:
        out["submits_allowed"] = max_submits()
    return {"content": [{"type": "text", "text": json.dumps(out)}]}


GET_LOCAL_DESC = (
    "Collect the finished local job, if any (non-blocking). Returns its results and any "
    "still-pending job_id. Call it like get_completed_jobs -- once near the start of a turn."
)


@tool("get_local_completed", GET_LOCAL_DESC, {})
async def get_local_completed(args):
    global _local_submit_count
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
        refused = isinstance(res, dict) and bool(res.get("refused"))
        if refused:
            _local_submit_count -= 1
        _record_scores(res)
        completed.append({"job_id": job_id, "results": res})
        _append_jobs_log({"event": "local_completed", "job_id": job_id, "refused": refused})
        del _local_jobs[job_id]
    pending = [{"job_id": jid, "args": i["args"]} for jid, i in _local_jobs.items()]
    return {"content": [{"type": "text", "text":
            json.dumps({"completed": completed, "pending": pending}, indent=2, default=str)}]}


RELEASE_CLAIM_DESC = (
    "Release a claim you are holding on a piece of work, so another agent can take it. "
    "Use it for an orphaned claim from a crashed session: one with no job running and no "
    "results. Pass the same key the job was claimed under."
)


@tool("release_claim", RELEASE_CLAIM_DESC, {"key": str})
async def release_claim(args):
    _release_claim(args["key"])
    return {"content": [{"type": "text", "text": f"released claim on {args['key']}"}]}


ABANDON_JOB_DESC = (
    "Stop waiting for a remote job that will not return: its batch job was cancelled, or "
    "you have been told it is cancelled. Pass its job_id and why. The run stops waiting "
    "for it, so a wind-down can finish, and jobs.jsonl records it as abandoned. Globus "
    "Compute cannot withdraw a submitted task: while the endpoint is running it may "
    "request a new batch job for it, which whoever stops the endpoint has to know."
)


@tool("abandon_job", ABANDON_JOB_DESC, {"job_id": int, "reason": str})
async def abandon_job(args):
    job_id = args.get("job_id")
    info = _jobs.pop(job_id, None)
    if info is None:
        return {"content": [{"type": "text", "text":
                             f"job {job_id} is not in flight; in flight: {sorted(_jobs)}"}],
                "is_error": True}
    info["future"].cancel()
    _append_jobs_log({"event": "abandoned", "job_id": job_id, "key": info["key"],
                      "reason": (args.get("reason") or "").strip()})
    _release_claim(info["key"])
    return {"content": [{"type": "text", "text":
                         f"job {job_id} abandoned; {len(_jobs)} remote job(s) still in flight"}]}


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
    record_message("agent", args["message"])
    note = " (blocking: shutdown grace started)" if blocking else ""
    return {"content": [{"type": "text", "text": "notified" + note}]}


CYCLE_DONE_DESC = (
    "Call this when you have finished writing up a cycle, after its record is written. "
    "Pass the cycle's conclusion in one or two lines -- what it established. It marks "
    "the boundary of the cycle in the record. Where this lab runs a reviewer, that "
    "boundary is where it reads: your write-up is checked against the results rows, and "
    "anything they do not support comes back to you next turn. One call per cycle."
)

_cycle_mark = None      # set by cycle_done, read and cleared by the runner


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
    return {"content": [{"type": "text", "text":
                         "cycle recorded; its write-up will be reviewed"}]}


GOAL_MET_DESC = (
    "Call this when the campaign's goal, as its own prompt states it, is met: the "
    "question is answered to the standard that prompt sets. A good result is not a met "
    "goal, and neither is a result you have not yet recorded -- write the evidence "
    "first, then call this. Pass what settles it, in one or two lines. The run stops "
    "taking new work, finishes what is in flight, and gives you a turn to write up. A "
    "job that will not return holds the run open: abandon it with abandon_job."
)

_goal_met = None        # what the agent said settles the goal, or None


def goal_is_met():
    """What the agent said settles the campaign's goal, or None."""
    return _goal_met


@tool("goal_met", GOAL_MET_DESC, {"reason": str})
async def goal_met(args):
    global _goal_met
    _goal_met = (args.get("reason") or "").strip() or "(no reason given)"
    request_stop()
    return {"content": [{"type": "text", "text":
                         "goal recorded; this run is winding down -- collect what is in "
                         "flight and write the cycle up"}]}


END_RUN_DESC = (
    "Call this when you have been asked to stop, rather than because the goal is met. "
    "Pass who asked and what they asked for, in a line. The run stops taking new work, "
    "finishes what is in flight, and gives you a turn to write up what it reached. A job "
    "that will not return holds the run open: abandon it with abandon_job."
)

_end_requested = None   # what the agent was asked to stop for, or None


def end_requested():
    """What the agent said it was asked to stop for, or None."""
    return _end_requested


@tool("end_run", END_RUN_DESC, {"reason": str})
async def end_run(args):
    global _end_requested
    _end_requested = (args.get("reason") or "").strip() or "(no reason given)"
    request_stop()
    return {"content": [{"type": "text", "text":
                         "recorded; this run is winding down -- collect what is in "
                         "flight and write the cycle up"}]}


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
        return {"content": [{"type": "text", "text": json.dumps({"error": f"client init: {e}"})}],
                "is_error": True}
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
        tasks.append({"job_id": jid, "key": j["key"], "task_id": tid,
                      "future_done": j["future"].done(), "task_status": st})
    info["in_flight_tasks"] = tasks
    return {"content": [{"type": "text", "text": json.dumps(info, indent=2, default=str)}]}


def create_server():
    """MCP server exposing the tools. Each pair is only offered when the task defines
    the matching function -- remote_fn for the remote tools, local_fn for the local."""
    tools = []
    if HAS_REMOTE:
        tools += (list(KIND_TOOLS.values()) if MULTI_KIND
                  else [submit_job_batch if BATCH_MODE else submit_job])
        tools += [get_completed_jobs, release_claim, abandon_job]
    if HAS_LOCAL:
        tools += [submit_local_batch if BATCH_MODE else submit_local,
                  get_local_completed]
    tools.append(notify)
    tools.append(cycle_done)
    tools.append(goal_met)
    tools.append(end_run)
    if HAS_REMOTE:
        tools.append(check_backend)
    if HAS_TRANSFER:
        tools.append(_transfer.transfer)
    return create_sdk_mcp_server(name="cas", version="1.0.0", tools=tools)


def tool_names():
    """Fully-qualified names for ClaudeAgentOptions(allowed_tools=...)."""
    names = []
    if HAS_REMOTE:
        names += ([f"submit_{k}" for k in KIND_TOOLS] if MULTI_KIND
                  else ["submit_job_batch" if BATCH_MODE else "submit_job"])
        names += ["get_completed_jobs", "release_claim", "abandon_job"]
    if HAS_LOCAL:
        names += ["submit_local_batch" if BATCH_MODE else "submit_local",
                  "get_local_completed"]
    names.append("notify")
    names.append("cycle_done")
    names.append("goal_met")
    names.append("end_run")
    if HAS_REMOTE:
        names.append("check_backend")
    if HAS_TRANSFER:
        names.append("transfer")
    return [f"mcp__cas__{n}" for n in names]
