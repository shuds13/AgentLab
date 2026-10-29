"""Node-hours a launch has used on each system, from the blocks its tasks ran in.

Each remote task reports where it ran (`ran_on`): under a batch scheduler, the batch job
(block) with its start and nodes. A block's end is not reported, so it is taken as its
last task's end plus the time the endpoint holds an idle block (`max_idletime`, recorded
with each submit), or the block's walltime end if that is sooner. Blocks that ran no task
are not counted. Shared by the watch, which shows the figures, and tools.py, which holds a
launch to a node-hour budget."""
import glob
import json
import os
import time


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def launch_runs(workspace, run_id):
    """The runs of the launch `run_id` belongs to: its top run and that run's workers,
    each with its own system."""
    meta = _read_json(os.path.join(workspace, "runs", run_id, "meta.json"))
    top = meta.get("parent_run") or run_id
    runs = {}
    for m in glob.glob(os.path.join(workspace, "runs", "*", "meta.json")):
        rid = os.path.basename(os.path.dirname(m))
        d = _read_json(m)
        if rid == top or d.get("parent_run") == top:
            runs[rid] = d.get("system")
    runs.setdefault(top, meta.get("system"))
    return runs


def block_key(on):
    """A block's id: its batch job where there is one, else the host it ran on."""
    return on.get("batch_job") or (on.get("host") and "host " + on["host"])


def add(block, on, idle_s=None):
    """Fold one task's `ran_on` into its block's record; `idle_s` is how long the endpoint
    holds the block idle after its last task."""
    if idle_s is not None:
        block["idle_s"] = max(block.get("idle_s") or 0, idle_s)
    for field, key in (("queue", "block_queue"), ("nodes", "block_nodes"),
                       ("start", "block_start"), ("queued_at", "block_queued_at"),
                       ("walltime_s", "block_walltime_s")):
        if block.get(field) is None and on.get(key) is not None:
            block[field] = on[key]
    end = on.get("ended_at")
    if end and (block.get("last") is None or end > block["last"]):
        block["last"] = end


def end(block):
    """A block's estimated end, no later than now; None until a task in it has ended."""
    if not (block.get("start") and block.get("last")):
        return None
    e = block["last"] + (block.get("idle_s") or 0)
    if block.get("walltime_s"):
        e = min(e, block["start"] + block["walltime_s"])
    return max(block["last"], min(e, time.time()))


def hours(block):
    """A block's node-hours, start to its estimated end; None until a task in it has
    ended."""
    e = end(block)
    if e is None:
        return None
    return (block.get("nodes") or 1) * (e - block["start"]) / 3600


def used(workspace, runs):
    """Node-hours per system over the given runs ({run_id: system}), from jobs.jsonl."""
    systems, idle, blocks = {}, {}, {}
    try:
        with open(os.path.join(workspace, "jobs.jsonl")) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("run") not in runs:
                    continue
                k = (r["run"], r.get("job_id"))
                ev = r.get("event")
                if ev == "submit":
                    systems[k] = r.get("system") or runs[r["run"]]
                    idle[k] = r.get("idle_s")
                elif ev in ("completed", "abandoned", "retry") and r.get("ran_on"):
                    bid = block_key(r["ran_on"])
                    if bid:
                        sy = systems.get(k) or runs[r["run"]]
                        add(blocks.setdefault((sy, bid), {}), r["ran_on"], idle.get(k))
    except FileNotFoundError:
        pass
    out = {}
    for (sy, _), b in blocks.items():
        out[sy] = out.get(sy, 0) + (hours(b) or 0)
    return out
