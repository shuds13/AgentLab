"""Run another agent as one local job: a worker on its own git branch and worktree.

A coordinating agent's task hands its local_fn to run_worker, so each local job it
submits starts a worker and the job's result is what that worker produced. A worker is
a full agent.py run in the same campaign: its own run directory, log, heartbeat and
cost, visible in the viewer like any other run, and with job tools of its own for the
work it evaluates.

What a worker is given:

  role          a kind the campaign defines (`roles` below): the method it follows,
                the task module its job tools come from, its submit budget, and
                whether it starts as a fork of an earlier worker's conversation.
  branch        the branch it works on, created from `parent` if it does not exist.
  worktree      <repo>/.worktrees/<branch>, its working directory.
  user prompt   its branch and worktree, and the coordinator's context if any.

Each worker's run records `parent_run`, `kind`, `branch`, `parent` and
`parent_session` in its meta.json, so its relation to the coordinator can be read back.
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading

FRAMEWORK_DIR = os.path.dirname(os.path.abspath(__file__))
# The CPUs workers may use, as a taskset list such as "0-15". Every worker is held to
# the same set, so it bounds what all of them use together on a shared machine, their
# own scripts and evaluations included. Unset leaves them unrestricted.
WORKER_CPUS = os.environ.get("WORKER_CPUS", "").strip()
SCORE_RE = re.compile(r"\|\s*score\s*=\s*([-+0-9.eE]+)")

# Workers this run has started, by kind, and the jobs given to workers out of the
# budget they share. A coordinating run is one process, and its jobs start workers from
# several threads at once.
_started = {}
_started_lock = threading.Lock()
# The jobs all of this run's workers may submit between them, or None for no shared
# limit. Each worker is given a share when it starts and returns what it did not use.
WORKER_JOB_BUDGET = (int(os.environ["WORKER_JOB_BUDGET"])
                     if os.environ.get("WORKER_JOB_BUDGET") else None)
_jobs_given = 0


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def _error(msg, args):
    return {"error": msg, "args": args}


def _metas(workspace):
    """Every run's meta.json in the campaign workspace."""
    for path in glob.glob(os.path.join(workspace, "runs", "*", "meta.json")):
        try:
            with open(path) as f:
                yield json.load(f)
        except (OSError, json.JSONDecodeError):
            continue


def drain_workers(reason=""):
    """Ask every worker this run started that is still running to wind down, as
    kill_agent.sh --drain does: each stops starting new work, finishes its turn and
    ends. Returns the runs asked."""
    workspace, run_id = os.environ.get("WORKSPACE_DIR"), os.environ.get("RUN_ID")
    if not workspace or not run_id:
        return []
    asked = []
    for meta in _metas(workspace):
        if meta.get("parent_run") == run_id and meta.get("status") == "running":
            run_dir = os.path.join(workspace, "runs", meta.get("run_id", ""))
            try:
                with open(os.path.join(run_dir, "stop"), "w") as f:
                    f.write(f"drained by {run_id}: {reason}\n")
                asked.append(meta.get("run_id"))
            except OSError:
                pass
    return asked


def _worked_by(workspace, branch):
    """The run of an earlier worker on `branch`, or None. A campaign keeps its search
    repo across runs, so a branch name a worker has had is taken."""
    for meta in _metas(workspace):
        if meta.get("branch") == branch and meta.get("parent_run"):
            return meta.get("run_id")
    return None


def _worker_run(workspace, run_id, branch):
    """The meta.json of the run this coordinator started for `branch`, or {}."""
    found = [m for m in _metas(workspace)
             if m.get("parent_run") == run_id and m.get("branch") == branch]
    return max(found, key=lambda m: m.get("started_at") or "") if found else {}


def _cpu_count(cpus):
    """How many CPUs a taskset list such as "0-7,16-23" names."""
    n = 0
    for part in cpus.split(","):
        lo, _, hi = part.partition("-")
        n += int(hi or lo) - int(lo) + 1
    return n


def prepare_worktree(repo, branch, parent):
    """Create `branch` from `parent` if it does not exist, and check it out in its own
    worktree. Returns (path, error)."""
    if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
        made = _git(repo, "branch", branch, parent or "HEAD")
        if made.returncode:
            return None, f"could not create {branch} from {parent!r}: {made.stderr.strip()}"
    path = os.path.join(repo, ".worktrees", branch)
    if not os.path.isdir(path):
        added = _git(repo, "worktree", "add", path, branch)
        if added.returncode:
            return None, f"could not check out {branch}: {added.stderr.strip()}"
    return path, None


def run_worker(args, *, repo, roles, timeout=14400):
    """Start one worker, wait for it to finish, and return what it produced.

    `roles` maps each kind the coordinator may ask for to its settings:
        method        the method file in the campaign directory
        task_module   the module the worker's job tools come from
        max_submits   the worker's job budget when the coordinator names none
        max_runtime   the worker's wall clock, in seconds
        fork          True when the kind starts from an earlier worker's session
        model         the worker's model; WORKER_MODEL, then the coordinator's, if unset
        max_per_run   how many of this kind one coordinating run may start; unlimited
                      if unset
        records       the files a worker of this kind keeps in its worktree, such as
                      its notes, for anything reading the run to find
        tools         the Claude Code tools a worker of this kind is given, as an
                      AGENT_TOOLS value; the coordinator's set if unset
    """
    kind = str(args.get("kind", "")).strip()
    branch = str(args.get("branch", "")).strip()
    parent = str(args.get("parent", "") or "").strip()
    parent_session = str(args.get("parent_session", "") or "").strip()
    context = str(args.get("context", "") or "").strip()

    role = roles.get(kind)
    if role is None:
        return _error(f"kind must be one of {sorted(roles)}, got {kind!r}", args)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", branch):
        return _error(f"branch must be a plain name, got {branch!r}", args)
    if role.get("fork") and not parent_session:
        return _error(f"a {kind} starts from an earlier worker's session: give "
                      f"parent_session", args)

    workspace = os.environ["WORKSPACE_DIR"]
    run_id = os.environ.get("RUN_ID", "")
    earlier = _worked_by(workspace, branch)
    if earlier:
        return _error(f"branch {branch} was worked on by {earlier}; give this worker a "
                      f"new branch name", args)
    global _jobs_given
    limit = role.get("max_per_run")
    try:
        jobs = int(args.get("jobs") or role.get("max_submits", 10))
    except (TypeError, ValueError):
        return _error(f"jobs must be a whole number, got {args.get('jobs')!r}", args)
    with _started_lock:
        if limit is not None and _started.get(kind, 0) >= limit:
            return _error(f"this run has started {limit} {kind} worker(s), the most it "
                          f"may", args)
        if WORKER_JOB_BUDGET is not None:
            jobs = min(jobs, WORKER_JOB_BUDGET - _jobs_given)
        if jobs < 1:
            return _error("the workers' shared job budget is spent", args)
        _started[kind] = _started.get(kind, 0) + 1
        _jobs_given += jobs
    worktree, problem = prepare_worktree(repo, branch, parent)
    if problem:
        with _started_lock:
            _started[kind] -= 1
            _jobs_given -= jobs
        return _error(problem, args)

    home = os.path.join(workspace, "agents", branch)
    os.makedirs(home, exist_ok=True)
    # Each worker's files on the compute system sit in a directory of their own under
    # the run's, so workers cannot overwrite one another's trials.
    work_dir = os.path.join(os.environ["WORK_DIR"], branch) if os.environ.get("WORK_DIR") else None
    user_prompt = os.path.join(home, "user_prompt.md")
    with open(user_prompt, "w") as f:
        f.write(f"Start. Your branch is `{branch}`, checked out in `{worktree}`, which is "
                f"your working directory.\n")
        if work_dir:
            f.write(f"\nYour work directory on the compute system is `{work_dir}`.\n")
        if WORKER_CPUS:
            f.write(f"\nYou and the other workers share {_cpu_count(WORKER_CPUS)} CPU cores; "
                    f"everything you run is held to them. `os.cpu_count()` reports the "
                    f"whole machine, so size parallel work by "
                    f"`len(os.sched_getaffinity(0))`.\n")
        if context:
            f.write(f"\n{context}\n")

    env = dict(os.environ)
    # What belongs to the coordinator's run and not to the worker's.
    for name in ("RESUME_SESSION", "WATCH", "RUN_ID"):
        env.pop(name, None)
    env.update({
        "ROLE": branch,
        "METHOD_FILE": role["method"],
        "TASK_MODULE": role["task_module"],
        "AGENT_CWD": worktree,
        "USER_PROMPT_FILE": user_prompt,
        "MAX_SUBMITS": str(jobs),
        "MAX_RUNTIME": str(role.get("max_runtime", 3600)),
        "LOCAL_MAX_CONCURRENT": "1",
        # A worker writes up before goal_met, so its run ends there. A run forked from
        # it then starts from that worker's own summary.
        "END_ON_GOAL_MET": "true",
        "NOTIFY_START": "false", "NOTIFY_DAILY": "false", "NOTIFY_FINISH": "false",
        "RUN_META": json.dumps({"parent_run": run_id, "kind": kind, "branch": branch,
                                "parent": parent, "parent_session": parent_session,
                                "records": role.get("records", [])}),
    })
    if work_dir:
        env["WORK_DIR"] = work_dir
    if role.get("tools"):
        env["AGENT_TOOLS"] = role["tools"]
    model = role.get("model") or os.environ.get("WORKER_MODEL")
    if model:
        env["AGENT_MODEL"] = model
    if role.get("fork"):
        env["RESUME_SESSION"] = parent_session

    with open(os.path.join(home, "console.log"), "w") as console:
        cmd = [sys.executable, "-u", os.path.join(FRAMEWORK_DIR, "agent.py")]
        if WORKER_CPUS:
            cmd = ["taskset", "-c", WORKER_CPUS] + cmd
        proc = subprocess.Popen(cmd,
                                env=env, cwd=FRAMEWORK_DIR, stdout=console,
                                stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=60)
            return _error(f"worker on {branch} ran past {timeout}s", args)

    # What the worker wrote after its last commit, such as closing notes, is kept: a
    # worker without a shell cannot commit it itself.
    _git(worktree, "add", "-A")
    if _git(worktree, "diff", "--cached", "--quiet").returncode:
        _git(worktree, "commit", "-q", "-m", "Worker's final state")

    meta = _worker_run(workspace, run_id, branch)
    # What the worker's MAX_SUBMITS counted: its remote jobs where it has an endpoint,
    # else its local ones, as tools.submit_count does.
    used = (meta.get("remote_submitted") if meta.get("endpoint")
            else meta.get("local_submitted")) or 0
    with _started_lock:
        _jobs_given -= jobs - min(used, jobs)
        left = (WORKER_JOB_BUDGET - _jobs_given) if WORKER_JOB_BUDGET is not None else None
    if not meta:
        return _error(f"worker on {branch} exited {proc.returncode} without starting a "
                      f"run; see {home}/console.log", args)
    # The worker's score is that of the newest scored commit it made: commits after it,
    # such as notes, carry none, and its parent's commits are not its own.
    commit, subject, score = None, "", None
    span = f"{parent}..{branch}" if parent else branch
    log = _git(repo, "log", "--format=%h%x00%s", span).stdout.splitlines()
    for line in log:
        h, _, s = line.partition("\x00")
        found = SCORE_RE.search(s)
        if found:
            commit, subject, score = h, s, float(found.group(1))
            break
    usd = [c.get("usd") for c in meta.get("cost_models") or [] if c.get("usd") is not None]
    return {
        "args": args,
        "run_id": meta.get("run_id"),
        "session_id": meta.get("session_id"),
        "summary": meta.get("goal_met") or meta.get("end_requested"),
        "stop_reason": meta.get("stop_reason"),
        "commit": commit,
        "commit_subject": subject,
        "score": score,
        "cost_usd": round(sum(usd), 4) if usd else None,
        "jobs_given": jobs,
        "jobs_used": used,
        "shared_jobs_left": left,
    }
