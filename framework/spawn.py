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

import budgets

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
# The same per bucket, as JSON: {"gpu": 16}, for a campaign whose buckets cost
# differently. A role's `bucket_submits` is a worker's share of each.
WORKER_BUCKET_BUDGET = json.loads(os.environ.get("WORKER_JOB_BUDGET_BUCKETS") or "{}")
_bucket_given = {}
# How long past its max_runtime a worker is given to collect what it has in flight and
# write up before it is stopped.
WIND_DOWN_SECONDS = int(os.environ.get("WORKER_WIND_DOWN", "3600"))


# The limits in force now: the run's budgets file (budgets.py), which may be raised while
# it goes, else what the run started with.
def _pool_limit():
    return budgets.limit("worker_jobs", fallback=WORKER_JOB_BUDGET)


def _pool_bucket_limits():
    out = dict(WORKER_BUCKET_BUDGET)
    out.update(budgets.limit("worker_jobs_buckets", fallback={}) or {})
    return {b: n for b, n in out.items() if n is not None}


def budget_seed(roles):
    """The workers' budgets a coordinating run starts with, for its budgets file."""
    return {"worker_jobs": WORKER_JOB_BUDGET,
            "worker_jobs_buckets": dict(WORKER_BUCKET_BUDGET),
            "workers": {kind: role.get("max_per_run") for kind, role in roles.items()}}


def count_earlier(workspace, run_id):
    """Take up the counts where an earlier part of the run left them: the workers it
    started, by kind, and the jobs they used out of the shared budget. A continued run's
    budgets are its totals, so its earlier workers count against them."""
    global _jobs_given
    with _started_lock:
        for meta in _metas(workspace):
            if meta.get("parent_run") != run_id:
                continue
            kind = meta.get("kind") or ""
            _started[kind] = _started.get(kind, 0) + 1
            used = (meta.get("remote_submitted") if meta.get("endpoint")
                    else meta.get("local_submitted")) or 0
            _jobs_given += int(used)
            for b, n in (meta.get("remote_submitted_buckets") or {}).items():
                _bucket_given[b] = _bucket_given.get(b, 0) + int(n)


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


def branch_parents(repo, branch):
    """The branches `branch` was made from, for one a coordinator set up itself: for each
    other branch, the newest commit the two share; those that no other branch's shared
    commit builds on, the one on its first-parent line first. Several for a merge of
    several parents; ["master"] or similar for one made from the base alone. A branch
    made from an earlier commit of another, such as its best result rather than its last,
    still counts. Branches made after it are not its parents; where several share the
    same commit, the oldest is taken, since the others were made from it."""
    def git(*a):
        return _git(repo, *a).stdout.strip()

    def is_anc(x, y):
        return _git(repo, "merge-base", "--is-ancestor", x, y).returncode == 0

    def created(name):
        log = _git(repo, "reflog", "show", "--date=unix", "--format=%gd",
                   f"refs/heads/{name}").stdout.split()
        m = re.search(r"@\{(\d+)\}", log[-1]) if log else None
        return int(m.group(1)) if m else 0

    head = git("rev-parse", branch)
    if not head:
        return []
    names = [n for n in git("for-each-ref", "--format=%(refname:short)",
                            "refs/heads/").split() if n != branch]
    born = created(branch)
    shared = {}
    for n in names:
        if born and created(n) > born:
            continue
        mb = git("merge-base", branch, n)
        if mb:
            shared.setdefault(mb, []).append(n)
    tips = [c for c in shared if not any(o != c and is_anc(c, o) for o in shared)]
    line = git("rev-list", "--first-parent", head).split()
    tips.sort(key=lambda c: line.index(c) if c in line else len(line))
    return [min(shared[c], key=created) for c in tips]


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


def _node_hours_left():
    """The launch's node-hours left by system, where it has a budget. tools is imported
    here, not at the top: it imports the task module, which imports this one."""
    import tools
    caps = {sy: tools.node_hours_cap(sy) for sy in tools.SYSTEMS}
    caps = {sy: cap for sy, cap in caps.items() if cap is not None}
    if not caps:
        return {}
    used = tools.node_hours_used()
    return {"node_hours_left": {sy: round(cap - used.get(sy, 0), 1)
                                for sy, cap in caps.items()}}


def run_worker(args, *, repo, roles, timeout=None):
    """Start one worker, wait for it to finish, and return what it produced.

    `roles` maps each kind the coordinator may ask for to its settings:
        method        the method file in the campaign directory
        task_module   the module the worker's job tools come from
        max_submits   the worker's job budget when the coordinator names none
        max_runtime   the worker's wall clock, in seconds
        fork          True when the kind starts from an earlier worker's session
        model         the worker's model; WORKER_MODEL, then the coordinator's, if unset
        effort        the worker's effort level; WORKER_EFFORT, then the coordinator's,
                      if unset
        max_per_run   how many of this kind one coordinating run may start; unlimited
                      if unset
        records       the files a worker of this kind keeps in its worktree, such as
                      its notes, for anything reading the run to find
        tools         the Claude Code tools a worker of this kind is given, as an
                      AGENT_TOOLS value; the coordinator's set if unset
        bucket_submits  the worker's budget per bucket, e.g. {"gpu": 4, "cpu": 100};
                      a bucket it does not name is limited only by max_submits

    `timeout` is how long the worker may run before it is stopped: unset, its
    max_runtime plus WIND_DOWN_SECONDS, since a worker at its time limit still collects
    the jobs it has in flight and writes up.
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
    limit = budgets.limit("workers", kind, fallback=role.get("max_per_run"))
    pool = _pool_limit()
    pool_buckets = _pool_bucket_limits()
    try:
        jobs = int(args.get("jobs") or role.get("max_submits", 10))
    except (TypeError, ValueError):
        return _error(f"jobs must be a whole number, got {args.get('jobs')!r}", args)
    with _started_lock:
        if limit is not None and _started.get(kind, 0) >= limit:
            return _error(f"this run has started {limit} {kind} worker(s), the most it "
                          f"may", args)
        if pool is not None:
            jobs = min(jobs, pool - _jobs_given)
        if jobs < 1:
            return _error("the workers' shared job budget is spent", args)
        # Per bucket: the role's share of each, cut to what the shared pool has left.
        bucket_jobs = {b: int(n) for b, n in (role.get("bucket_submits") or {}).items()}
        for b in bucket_jobs:
            if b in pool_buckets:
                bucket_jobs[b] = max(0, min(bucket_jobs[b], int(pool_buckets[b])
                                            - _bucket_given.get(b, 0)))
        if bucket_jobs and not any(bucket_jobs.values()):
            return _error("the workers' shared budget is spent in every bucket", args)
        for b, n in bucket_jobs.items():
            _bucket_given[b] = _bucket_given.get(b, 0) + n
        _started[kind] = _started.get(kind, 0) + 1
        _jobs_given += jobs
    worktree, problem = prepare_worktree(repo, branch, parent)
    if problem:
        with _started_lock:
            _started[kind] -= 1
            _jobs_given -= jobs
            for b, n in bucket_jobs.items():
                _bucket_given[b] -= n
        return _error(problem, args)
    # A branch the coordinator set up itself names no parent; read it from git, so the
    # record shows what the worker builds on.
    parents = [parent] if parent else branch_parents(repo, branch)
    parent = parent or (parents[0] if parents else "")

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
    for name in ("RESUME_SESSION", "CONTINUE_RUN", "WATCH", "RUN_ID"):
        env.pop(name, None)
    env.update({
        "ROLE": branch,
        "METHOD_FILE": role["method"],
        "TASK_MODULE": role["task_module"],
        "AGENT_CWD": worktree,
        "USER_PROMPT_FILE": user_prompt,
        "MAX_SUBMITS": str(jobs),
        "MAX_SUBMITS_BUCKETS": json.dumps(bucket_jobs) if bucket_jobs else "",
        "MAX_RUNTIME": str(role.get("max_runtime", 3600)),
        "LOCAL_MAX_CONCURRENT": "1",
        # A worker writes up before goal_met, so its run ends there. A run forked from
        # it then starts from that worker's own summary.
        "END_ON_GOAL_MET": "true",
        "NOTIFY_START": "false", "NOTIFY_DAILY": "false", "NOTIFY_FINISH": "false",
        "RUN_META": json.dumps({"parent_run": run_id, "kind": kind, "branch": branch,
                                "parent": parent, "parents": parents,
                                "parent_session": parent_session,
                                "records": role.get("records", [])}),
    })
    if work_dir:
        env["WORK_DIR"] = work_dir
    # The worker's directory on every other system the campaign uses, by the same name.
    env["WORK_SUBDIR"] = branch
    if role.get("tools"):
        env["AGENT_TOOLS"] = role["tools"]
    model = role.get("model") or os.environ.get("WORKER_MODEL")
    if model:
        env["AGENT_MODEL"] = model
    effort = role.get("effort") or os.environ.get("WORKER_EFFORT")
    if effort:
        env["AGENT_EFFORT"] = effort
    if role.get("fork"):
        env["RESUME_SESSION"] = parent_session

    if timeout is None:
        timeout = int(role.get("max_runtime", 3600)) + WIND_DOWN_SECONDS
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
    used_b = meta.get("remote_submitted_buckets") or {}
    with _started_lock:
        _jobs_given -= jobs - min(used, jobs)
        pool, pool_buckets = _pool_limit(), _pool_bucket_limits()
        left = (pool - _jobs_given) if pool is not None else None
        # What the worker did not use of each bucket goes back to the pool.
        for b, n in bucket_jobs.items():
            _bucket_given[b] -= n - min(int(used_b.get(b, 0)), n)
        left_b = {b: int(pool_buckets[b]) - _bucket_given.get(b, 0) for b in pool_buckets}
    if not meta:
        # It never ran -- a failed preflight, most often -- so it counts against nothing:
        # not this kind's limit, and, marked refused, not the coordinator's submits. Its
        # branch can be launched again.
        with _started_lock:
            _started[kind] -= 1
        try:
            with open(os.path.join(home, "console.log"), errors="replace") as f:
                said = [l.rstrip() for l in f if l.startswith(("PREFLIGHT", "  - "))][-4:]
        except OSError:
            said = []
        return {**_error(f"worker on {branch} exited {proc.returncode} without starting a "
                         f"run; see {home}/console.log", args),
                "refused": True, "reason": "\n".join(said)}
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
    # The scores the worker's jobs reported, and the one its campaign names as primary,
    # take precedence over a score read from a commit message.
    scores = meta.get("scores") or {}
    primary = meta.get("primary_score")
    if primary:
        score = scores.get(primary)
    elif len(scores) == 1:
        score = next(iter(scores.values()))
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
        "scores": scores,
        "cost_usd": round(sum(usd), 4) if usd else None,
        "jobs_given": jobs,
        "jobs_used": used,
        "shared_jobs_left": left,
        **_node_hours_left(),
        **({"bucket_jobs_given": bucket_jobs,
            "bucket_jobs_used": {b: used_b.get(b, 0) for b in bucket_jobs},
            "shared_bucket_jobs_left": left_b} if bucket_jobs else {}),
    }
