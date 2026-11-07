"""A run's budgets as the page shows them: each limit, what has been used of it, and
what was submitted."""

import json
import os

import budgets
import node_hours
import resource_session

from .workspace import read_meta, workspace
from .runs import run_elapsed, run_path, workers


def count_lines(path):
    try:
        with open(path, errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def budget_limits(run_dir, meta):
    """A run's budgets (budgets.py), or for a run from before it had a budgets file, the
    limits its meta recorded."""
    found = budgets.load(run_dir)
    if found:
        return found
    return {"submits": meta.get("max_submits"),
            "submits_buckets": meta.get("max_submits_buckets") or {},
            "runtime_s": meta.get("max_runtime_s"),
            "node_hours": meta.get("max_node_hours") or {}}


def submits_by_run(path):
    """Jobs each run has submitted, {run: {"remote": n, "local": n, "buckets": {b: n}}},
    as tools counts them: a job that came back refused gives its submit back."""
    out, bucket_of = {}, {}
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = r.get("event")
                if ev not in ("submit", "local_submit", "completed", "local_completed"):
                    continue
                where = "local" if ev.startswith("local_") else "remote"
                run = r.get("run")
                c = out.setdefault(run, {"remote": 0, "local": 0, "buckets": {}})
                if ev.endswith("submit"):
                    c[where] += 1
                    if r.get("bucket"):
                        bucket_of[(run, r.get("job_id"))] = r["bucket"]
                        c["buckets"][r["bucket"]] = c["buckets"].get(r["bucket"], 0) + 1
                elif r.get("refused"):
                    c[where] -= 1
                    b = bucket_of.get((run, r.get("job_id"))) if where == "remote" else None
                    if b:
                        c["buckets"][b] -= 1
    except OSError:
        pass
    return out


def budget_rows(campaign, run_dir, meta, counts, elapsed, started):
    """Every budget of a run with what it has used: {name, key, label, used, limit,
    unit}. A limit of None is no limit. `counts` is submits_by_run's, `started` the
    runs it started."""
    ws = workspace(campaign)
    lim = budget_limits(run_dir, meta)
    mine = counts.get(meta.get("run_id")) or {"remote": 0, "local": 0, "buckets": {}}
    rows = []

    def row(name, key, label, used, limit, unit=""):
        rows.append({"name": name, "key": key, "label": label, "used": used,
                     "limit": limit, "unit": unit})

    # A coordinator's submits are the workers it starts. meta.json says which; a run
    # from before it did is a coordinator if it started workers and sent no remote work.
    row("submits", None, meta.get("submits_label")
        or ("workers started" if started and not meta.get("endpoint") else "jobs submitted"),
        mine["remote" if meta.get("endpoint") else "local"], lim.get("submits"))
    for b, n in (lim.get("submits_buckets") or {}).items():
        row("submits_buckets", b, f"jobs submitted, {b}", mine["buckets"].get(b, 0), n)
    row("runtime_s", None, "run time", elapsed, lim.get("runtime_s"), "s")
    # Node-hours are the launch's, held by the run that started it. In a group with an
    # open resource session, the session's budget is the one that holds on its systems:
    # the group's runs share their blocks, so a launch's own count means nothing there.
    if not meta.get("parent_run"):
        ses = group_session(campaign)
        shared = ses["node_hours"] if ses and not ses.get("stopped") else {}
        for sy, cap in shared.items():
            row("group_node_hours", sy, f"node-hours, {sy}, the group's {ses['runs']} runs",
                ses["used"].get(sy, 0), cap, "h")
        caps = {sy: c for sy, c in (lim.get("node_hours") or {}).items() if sy not in shared}
        if caps:
            used = node_hours.used(ws, node_hours.launch_runs(ws, meta.get("run_id")))
            for sy, cap in caps.items():
                row("node_hours", sy, f"node-hours, {sy}", round(used.get(sy, 0), 1),
                    cap, "h")
    # A run that starts workers: the jobs they have used of their shared budget, and how
    # many of each kind it has started.
    if "worker_jobs" in lim or lim.get("workers"):
        used = 0
        for w in started:
            wm = read_meta(os.path.join(ws, "runs", w["run"], "meta.json"))
            used += (counts.get(w["run"]) or {}).get(
                "remote" if wm.get("endpoint") else "local", 0)
        row("worker_jobs", None, "jobs given to workers", used, lim.get("worker_jobs"))
        for b, n in (lim.get("worker_jobs_buckets") or {}).items():
            row("worker_jobs_buckets", b, f"jobs given to workers, {b}",
                sum((counts.get(w["run"]) or {}).get("buckets", {}).get(b, 0)
                    for w in started), n)
        for kind, n in (lim.get("workers") or {}).items():
            row("workers", kind, f"{kind} workers started",
                sum(1 for w in started if w["kind"] == kind), n)
    return rows


def owns(run_dir):
    """Whether a run belongs to the user this watcher runs as, by its directory."""
    try:
        return os.stat(run_dir).st_uid == os.getuid()
    except (OSError, TypeError):
        return False


def group_session(campaign):
    """The resource session of the group this workspace is in, open or the last one
    stopped, with the node-hours the group has used in it; None outside a group or
    without one."""
    if "/" not in campaign:
        return None
    group = os.path.dirname(workspace(campaign))
    s = read_meta(os.path.join(group, resource_session.FILE))
    if not s or not s.get("started"):
        return None
    used = resource_session.used(group, s)
    return {"group": os.path.basename(group), "started": s["started"],
            "stopped": s.get("stopped"), "node_hours": s.get("node_hours") or {},
            "used": {k: round(v, 2) for k, v in used.items()},
            "runs": len(resource_session.workspaces(group))}


def budgets_view(campaign, run=None):
    """The budgets tab: every budget of a run tree in one place. The run's own, the
    resources it draws on, each worker's, and the changes made to them."""
    run_dir = run_path(campaign, run)
    if not run_dir:
        return {"run": None}
    meta = read_meta(os.path.join(run_dir, "meta.json"))
    selected = None
    if meta.get("parent_run") and run_path(campaign, meta["parent_run"]):
        selected = meta.get("run_id")
        run_dir = run_path(campaign, meta["parent_run"])
        meta = read_meta(os.path.join(run_dir, "meta.json"))
    ws = workspace(campaign)
    counts = submits_by_run(os.path.join(ws, "jobs.jsonl"))
    started = workers(campaign, meta.get("run_id"))
    out = {"run": meta.get("run_id"), "selected": selected, "status": meta.get("status"),
           "part": meta.get("part") or 1, "workspace": os.path.basename(ws),
           "rows": budget_rows(campaign, run_dir, meta, counts, run_elapsed(meta, run_dir), started),
           "buckets": meta.get("buckets") or {}, "workers": [], "changes": []}
    for w in started:
        wdir = os.path.join(ws, "runs", w["run"])
        wm = read_meta(os.path.join(wdir, "meta.json"))
        out["workers"].append({"run": w["run"], "branch": w["branch"], "kind": w["kind"],
                               "status": w["status"],
                               "rows": budget_rows(campaign, wdir, wm, counts,
                                                   run_elapsed(wm, wdir), [])})
    try:
        with open(os.path.join(run_dir, budgets.LOG)) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out["changes"] += [dict(c, ts=r.get("ts"), who=r.get("who"))
                                   for c in r.get("changes", [])]
    except OSError:
        pass
    return out


def submit_counts(path, run_id):
    """Jobs fired, in total and by this run, and how many of this run's have come back.
    This run's counts are split by where the work ran. The log spans every run of the
    campaign, so a budget only means something against the run's own count."""
    total = 0
    this_run = {"remote": 0, "local": 0}
    done = {"remote": 0, "local": 0}
    abandoned = 0
    # Per bucket, for a campaign whose jobs come in more than one resource shape.
    buckets = {}
    # Still out: submitted and not yet returned. A completed row names the job but not
    # its bucket, so the bucket is taken from the submit that opened it. Job ids are
    # numbered per run, so a job is known by its run and id together.
    job_bucket = {}
    inflight = {}
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
                # An abandoned job is out of flight without having returned.
                if event.endswith("completed") or event == "abandoned":
                    if event == "abandoned":
                        abandoned += mine
                    else:
                        done[where] += mine
                    b = job_bucket.pop((row.get("run"), row.get("job_id")), None)
                    if b:
                        inflight[b] = inflight.get(b, 0) - 1
                    continue
                # A resubmit resends a job the service turned away; it is not a new one.
                if event not in ("submit", "local_submit"):
                    continue
                total += 1
                this_run[where] += mine
                b = row.get("bucket")
                if mine and where == "remote" and b:
                    buckets[b] = buckets.get(b, 0) + 1
                    job_bucket[(row.get("run"), row.get("job_id"))] = b
                    inflight[b] = inflight.get(b, 0) + 1
    except OSError:
        pass
    return (total, this_run, done, buckets,
            {k: v for k, v in inflight.items() if v > 0}, abandoned)
