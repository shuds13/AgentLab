"""The blocks and tasks a run has used, and what the systems it runs on say about their
queues."""

import getpass
import glob
import json
import os
import threading
import time

import iri
import node_hours

from .workspace import LAB_DIR, read_meta, workspace
from .runs import run_path
from .budget_view import budget_limits
from .lab import group_siblings

# The batch jobs (blocks) a run's tasks ran in, read from the run's tasks log only. Each
# remote task reports where it ran (`ran_on`: its host and, under a batch scheduler, the
# batch job with its start, walltime and nodes), so a block is known from the tasks that
# ran in it, and from tasks it handed back. When a block ended is not reported; its time
# is counted up to its last task.


# What a system's IRI API says about its queues and your batch jobs (iri.py), for the
# systems that have IRI settings. Fetched in the background at most once a minute per
# system, so a page refresh never waits on the facility; the first look has nothing yet.
IRI_TTL = 60
_iri_seen = {}
_iri_lock = threading.Lock()


def iri_view(system, queues):
    def read(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    user = os.environ.get("USER_NAME") or getpass.getuser()
    cfg = iri.configure(read(os.path.join(LAB_DIR, "systems", f"{system}.json")),
                        read(os.path.join(LAB_DIR, "users", user, f"{system}.json")), user)
    if cfg is None:
        return None
    key = (system, tuple(sorted(q for q in queues if q)))
    with _iri_lock:
        seen = _iri_seen.setdefault(key, {"at": None, "data": {}, "busy": False})
        stale = seen["at"] is None or time.time() - seen["at"] >= IRI_TTL
        if stale and not seen["busy"]:
            seen["busy"] = True

            def fetch():
                try:
                    data = iri.status(cfg, list(key[1]))
                except Exception as e:
                    data = {"jobs_error": str(e)}
                with _iri_lock:
                    seen.update(at=time.time(), data=data, busy=False)
            threading.Thread(target=fetch, daemon=True).start()
        return {"checked_at": seen["at"], **seen["data"]}


def resources(campaign, run=None, light=False):
    """The run tree's remote work: per system, its buckets and who shares them, the
    blocks its tasks ran in, and the tasks each block ran or handed back.

    A workspace in a group shares its blocks with the group's other runs, so their
    tasks are read too, each labelled by its workspace, and this run's are the ones
    marked: a block is then seen whole, and its node-hours counted once."""
    top_dir = run_path(campaign, run)
    if not top_dir:
        return {"systems": []}
    top = read_meta(os.path.join(top_dir, "meta.json"))
    # A worker chosen in the agents panel: show its whole run tree, with it marked.
    selected = None
    if top.get("parent_run") and run_path(campaign, top["parent_run"]):
        selected = os.path.basename(top_dir)
        top_dir = run_path(campaign, top["parent_run"])
        top = read_meta(os.path.join(top_dir, "meta.json"))
    top_id = os.path.basename(top_dir)
    metas = {}
    for m in glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json")):
        d = read_meta(m)
        rid = os.path.basename(os.path.dirname(m))
        if rid == top_id or d.get("parent_run") == top_id:
            metas[rid] = d
    # A run's own tasks are labelled by its role: a coordinator when it has workers that
    # do the work (a resource agent does none), else a single researcher. In a group, by
    # its workspace's name as well.
    top_label = ("coordinator" if any(d.get("kind") != "resources" for rid, d in metas.items()
                                      if rid != top_id) else "researcher")
    siblings = group_siblings(campaign)
    if siblings and not selected:
        selected = top_id
    own = campaign.split("/", 1)[1] if siblings else None

    tasks, by_key = [], {}
    def own_label(rid):
        branch = metas[rid].get("branch")
        if own:
            return f"{own} {branch}" if branch else own
        return branch or top_label

    sources = [(campaign, metas, own_label)]
    for sib in siblings:
        sib_metas = {os.path.basename(os.path.dirname(m)): read_meta(m) for m in
                     glob.glob(os.path.join(workspace(sib), "runs", "*", "meta.json"))}
        name = sib.split("/", 1)[1]
        sources.append((sib, sib_metas, lambda rid, name=name, sm=sib_metas:
                        f"{name} {sm[rid]['branch']}" if sm[rid].get("branch") else name))
    for ws_name, ws_metas, label in sources:
        try:
            f = open(os.path.join(workspace(ws_name), "jobs.jsonl"))
        except FileNotFoundError:
            continue
        with f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("run") not in ws_metas:
                    continue
                k = (ws_name, r["run"], r.get("job_id"))
                ev = r.get("event")
                if ev == "submit":
                    mine = (r["run"] == selected if ws_name == campaign and selected != top_id
                            else ws_name == campaign)
                    t = {"run": r["run"], "agent": label(r["run"]),
                         "system": r.get("system") or ws_metas[r["run"]].get("system"),
                         "job_id": r.get("job_id"), "key": r.get("key"), "bucket": r.get("bucket"),
                         "kind": r.get("kind"), "idle_s": r.get("idle_s"),
                         "submitted": r.get("ts"), "started": None, "ran_s": None,
                         "state": "waiting or running", "retries": 0, "block": None,
                         "refused": [], "ran_on": None, "mine": mine}
                    tasks.append(t)
                    by_key[k] = t
                elif k in by_key and ev == "retry":
                    by_key[k]["retries"] += 1
                    by_key[k]["refused"].append({"at": r.get("ts"), "reason": r.get("reason"),
                                                 "ran_on": r.get("ran_on") or {}})
                elif k in by_key and ev in ("completed", "abandoned"):
                    t = by_key[k]
                    on = r.get("ran_on") or {}
                    t["ran_on"] = on or None
                    t["started"] = on.get("started_at") or r.get("started_at")
                    t["ran_s"] = (on["ended_at"] - on["started_at"]
                                  if on.get("ended_at") and on.get("started_at")
                                  else r.get("wall_seconds"))
                    t["state"] = ("abandoned" if ev == "abandoned"
                                  else "error" if r.get("error") else "done")

    # Every system the run tree sent work to, from what each run recorded: its systems
    # where it names several, else its one system and endpoint.
    systems = {}
    for d in metas.values():
        named = d.get("systems") or ({d["system"]: {"endpoint": d["endpoint"]}}
                                     if d.get("endpoint") and d.get("system") else {})
        for n, info in named.items():
            sy = systems.setdefault(n, {"system": n, "endpoint": info.get("endpoint"),
                                        "buckets": {}})
            for b, shape in (d.get("buckets") or {}).items():
                if (shape.get("system") or d.get("system")) == n:
                    sy["buckets"].setdefault(b, shape)
    out = []
    for name, sysd in systems.items():
        mine = [t for t in tasks if t["system"] == name]
        sharing = {}
        for t in mine:
            sharing.setdefault(t["bucket"], set()).add(t["agent"])
        sysd["sharing"] = {k: sorted(v) for k, v in sharing.items()}
        if light:
            out.append(sysd)
            continue
        blocks = {}

        def block(on, idle_s):
            bid = node_hours.block_key(on)
            if not bid:
                return None
            b = blocks.setdefault(bid, {"id": bid, "queue": None, "nodes": None,
                                        "queued_at": None, "start": None, "walltime_s": None,
                                        "last": None, "tasks": [], "refused": []})
            node_hours.add(b, on, idle_s)
            return b

        for t in mine:
            if t["ran_on"]:
                b = block(t["ran_on"], t["idle_s"])
                if b:
                    t["block"] = b["id"]
                    b["tasks"].append(t)
            for x in t["refused"]:
                b = block(x["ran_on"], t["idle_s"])
                if b:
                    b["refused"].append(dict(x, agent=t["agent"], job_id=t["job_id"],
                                             mine=t["mine"]))
        rows = sorted(blocks.values(), key=lambda b: b["start"] or b["last"] or 0)
        for b in rows:
            nh = node_hours.hours(b)
            b["node_hours"] = round(nh, 2) if nh else None
            b["end"] = node_hours.end(b)
            b["agents"] = sorted({t["agent"] for t in b["tasks"]})
        sysd.update(blocks=rows,
                    node_hours=round(sum(node_hours.hours(b) or 0 for b in rows), 1),
                    max_node_hours=(budget_limits(top_dir, top).get("node_hours")
                                    or {}).get(name),
                    iri=iri_view(name, [s.get("queue") for s in sysd["buckets"].values()]))
        out.append(sysd)
    return {"systems": out, "tasks": tasks, "selected": selected,
            "group": campaign.split("/", 1)[0] if siblings else None,
            "group_runs": len(siblings) + 1 if siblings else None}
