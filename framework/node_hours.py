"""Node-hours a launch has used on each system, from the blocks its tasks ran in.

Each remote task reports where it ran (`ran_on`): under a batch scheduler, the batch job
(block) with its start and nodes. A block's end is not reported, so its time is counted
from its start to its last task's end. That undercounts by the time a block is held idle
after its last task. Shared by the watch, which shows the figures, and tools.py, which
holds a launch to a node-hour budget."""
import glob
import json
import os


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


def add(block, on):
    """Fold one task's `ran_on` into its block's record."""
    for field, key in (("queue", "block_queue"), ("nodes", "block_nodes"),
                       ("start", "block_start"), ("queued_at", "block_queued_at"),
                       ("walltime_s", "block_walltime_s")):
        if block.get(field) is None and on.get(key) is not None:
            block[field] = on[key]
    end = on.get("ended_at")
    if end and (block.get("last") is None or end > block["last"]):
        block["last"] = end


def hours(block):
    """A block's node-hours, start to last task; None until a task in it has ended."""
    if not (block.get("start") and block.get("last")):
        return None
    return (block.get("nodes") or 1) * (block["last"] - block["start"]) / 3600


def used(workspace, runs):
    """Node-hours per system over the given runs ({run_id: system}), from jobs.jsonl."""
    systems, blocks = {}, {}
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
                elif ev in ("completed", "abandoned", "retry") and r.get("ran_on"):
                    bid = block_key(r["ran_on"])
                    if bid:
                        sy = systems.get(k) or runs[r["run"]]
                        add(blocks.setdefault((sy, bid), {}), r["ran_on"])
    except FileNotFoundError:
        pass
    out = {}
    for (sy, _), b in blocks.items():
        out[sy] = out.get(sy, 0) + (hours(b) or 0)
    return out
