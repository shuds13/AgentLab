"""One campaign's state at a glance: its phase, its workers, what it has submitted and
spent."""

import json
import os
import time

from .workspace import workspace
from .runs import run_elapsed, run_path, run_phase, run_usd, shows_cost, workers
from .budget_view import budget_rows, count_lines, submit_counts, submits_by_run


def status(campaign, run=None):
    """Where a run (the newest, unless one is named) has got to, in terms any campaign
    has: work done against the budgets it stops at, and whether it is still going."""
    run_dir = run_path(campaign, run)
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
    (submits_total, submits_run, done_run, submits_bucket, inflight_bucket,
     abandoned) = submit_counts(
        os.path.join(ws, "jobs.jsonl"), meta.get("run_id"))
    ran_here = sum(submits_run.values())
    phase, phase_age = run_phase(run_dir)
    started = meta.get("started_at")
    started_runs = workers(campaign, meta.get("run_id"))
    own_usd = run_usd(meta)
    tree_usd = (sum(w["usd"] or 0 for w in started_runs) + (own_usd or 0)
                if started_runs and shows_cost(meta) else None)
    elapsed = run_elapsed(meta, run_dir)
    budget = budget_rows(campaign, run_dir, meta,
                         submits_by_run(os.path.join(ws, "jobs.jsonl")), elapsed,
                         started_runs)
    return {
        "budgets": budget,
        "run": meta.get("run_id"), "handle": meta.get("handle"),
        "campaign": campaign, "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason"), "model": meta.get("model"),
        "effort": meta.get("effort"), "advisor": meta.get("advisor"),
        "critic": meta.get("critic"), "host": meta.get("host"),
        "system": meta.get("system"),
        "session_id": meta.get("session_id"), "session_cwd": meta.get("session_cwd"),
        "context_tokens": meta.get("context_tokens"),
        "context_window": meta.get("context_window"),
        "context_pct": meta.get("context_pct"),
        "cost_models": (meta.get("cost_models") or []) if shows_cost(meta) else [],
        "show_cost": shows_cost(meta),
        "turns": meta.get("turns"),
        "started_at": started, "ended_at": meta.get("ended_at"),
        "elapsed_s": elapsed, "heartbeat_age_s": age,
        "phase": phase, "phase_age_s": phase_age,
        "results": count_lines(os.path.join(ws, "results.jsonl")),
        "jobs": submits_total, "jobs_run": ran_here,
        "jobs_bucket": submits_bucket,
        "max_submits_buckets": meta.get("max_submits_buckets") or {},
        "inflight_bucket": inflight_bucket,
        "buckets": meta.get("buckets") or {},
        "systems": meta.get("systems") or {},
        "default_bucket": meta.get("default_bucket"),
        "jobs_done": sum(done_run.values()), "jobs_abandoned": abandoned,
        "jobs_remote": submits_run["remote"], "jobs_local": submits_run["local"],
        "done_remote": done_run["remote"], "done_local": done_run["local"],
        "endpoint": meta.get("endpoint"), "has_local": meta.get("has_local"),
        "reviews": count_lines(os.path.join(ws, "REVIEWS.md")) and
                   open(os.path.join(ws, "REVIEWS.md"), errors="replace").read().count("\n## "),
        "max_submits": meta.get("max_submits"),
        "max_runtime_s": meta.get("max_runtime_s"),
        "max_turns": meta.get("max_turns"),
        "parent_run": meta.get("parent_run"), "kind": meta.get("kind"),
        "method": meta.get("method"),
        "parent_run_known": bool(meta.get("parent_run")
                                 and run_path(campaign, meta["parent_run"])),
        "branch": meta.get("branch"), "parent": meta.get("parent"),
        "parents": meta.get("parents") or ([meta["parent"]] if meta.get("parent") else []),
        "workers": started_runs, "tree_usd": tree_usd,
        "records": meta.get("records") or [],
        "scores": meta.get("scores") or {}, "primary_score": meta.get("primary_score"),
        "routes": _routes_set(ws, os.path.basename(run_dir)) if meta.get("kind") == "resources"
                  else None,
    }


def _routes_set(ws, run_id):
    """The route changes a resource agent made, from the workspace's routing_log.jsonl."""
    out = []
    try:
        with open(os.path.join(ws, "routing_log.jsonl")) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("who") == run_id:
                    out.append({k: r.get(k) for k in ("ts", "from", "after", "reason")})
    except OSError:
        pass
    return out
