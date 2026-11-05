"""Where a workspace's tasks go: redirects from one resource bucket to another, changed
while its runs go.

A task picks its bucket (campaign.json's buckets, and the task's bucket_for). A redirect
here sends every task bound for one bucket to another instead, for all runs in the
workspace, from their next submit. It is how a resource agent (ROLE=resources), or a
person, moves work off a busy queue. Only buckets with the same nodes per job may be
redirected to each other, so a task always gets the shape it was written for.

The redirects are kept in <workspace>/routing.json as {"redirect": {from: to}}, and each
change is logged in routing_log.jsonl.

    python routing.py <workspace>                   show the redirects
    python routing.py <workspace> FROM=TO ...       send FROM's tasks to TO
    python routing.py <workspace> FROM=             stop redirecting FROM

<workspace> is a workspace directory or its name under workspace/.
"""

import fcntl
import glob
import json
import os
import sys
import time

FILE = "routing.json"
LOG = "routing_log.jsonl"

_cache = {}


def load(workspace):
    """The redirects in force, {from: to}. Read again only when the file has changed."""
    path = os.path.join(workspace, FILE)
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    held = _cache.get(path)
    if held and held[0] == stamp:
        return held[1]
    try:
        with open(path) as f:
            data = dict(json.load(f).get("redirect") or {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return held[1] if held else {}
    _cache[path] = (stamp, data)
    return data


def change(workspace, frm, to, who="", reason=""):
    """Redirect `frm` to `to`, or stop redirecting it when `to` is empty. Returns the
    redirect before and after. Checking that the buckets exist and fit is the caller's."""
    path = os.path.join(workspace, FILE)
    with open(os.path.join(workspace, "routing.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
        redirect = dict(data.get("redirect") or {})
        before = redirect.get(frm)
        if to and to != frm:
            redirect[frm] = to
        else:
            redirect.pop(frm, None)
        data["redirect"] = redirect
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        after = redirect.get(frm)
        with open(os.path.join(workspace, LOG), "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "who": who, "from": frm,
                                "before": before, "after": after,
                                **({"reason": reason} if reason else {})}) + "\n")
    return {"from": frm, "before": before, "after": after}


def demand(workspace, exclude_run=None, alive_s=120):
    """What the runs in a workspace are asking of its buckets now: tasks in flight
    (submitted and not yet collected or abandoned) per bucket, and the runs still alive
    (a heartbeat in the last `alive_s` seconds), other than `exclude_run`."""
    open_jobs = {}
    try:
        with open(os.path.join(workspace, "jobs.jsonl")) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                k = (r.get("run"), r.get("job_id"))
                if r.get("event") == "submit":
                    open_jobs[k] = r.get("bucket")
                elif r.get("event") in ("completed", "abandoned"):
                    open_jobs.pop(k, None)
    except OSError:
        pass
    alive = []
    now = time.time()
    for hb in sorted(glob.glob(os.path.join(workspace, "runs", "*", "heartbeat"))):
        run = os.path.basename(os.path.dirname(hb))
        try:
            with open(hb) as f:
                beat = float(f.read().strip() or 0)
        except (OSError, ValueError):
            continue
        if run != exclude_run and now - beat < alive_s:
            alive.append(run)
    in_flight = {}
    for (run, _), bucket in open_jobs.items():
        if run in alive:
            in_flight[bucket] = in_flight.get(bucket, 0) + 1
    return {"in_flight": in_flight, "live_runs": alive}


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    workspace = args.pop(0)
    if not os.path.isdir(workspace):
        lab = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        workspace = os.path.join(lab, "workspace", workspace)
    if not os.path.isdir(workspace):
        print(f"no workspace {workspace}", file=sys.stderr)
        return 1
    for a in args:
        frm, sep, to = a.partition("=")
        if not sep or not frm:
            print(f"expected FROM=TO or FROM=, got {a!r}", file=sys.stderr)
            return 1
        c = change(workspace, frm, to, who=os.environ.get("USER", ""))
        print(f"{frm}: {c['before'] or 'not redirected'} -> {c['after'] or 'not redirected'}")
    if not args:
        redirect = load(workspace)
        print("\n".join(f"{k} -> {v}" for k, v in redirect.items()) or "no redirects")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
