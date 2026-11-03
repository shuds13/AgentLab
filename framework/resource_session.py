"""A resource session: a node-hour budget for a group of runs, over a stretch of time.

Workspaces in one directory (workspace/<group>/<name>/) are a group. Runs of a group that
use the same bucket on a system share its blocks, which a run's own node-hour budget
cannot account for: each run would count a shared block in full. A resource session
gives the group one budget instead. It counts the blocks any of the group's runs ran in,
each block once, from the session's start until it is stopped or the next session
starts. On a system it budgets, it takes the place of each run's own node-hour budget:
the submit tool of every run in the group refuses new jobs there once the session's
node-hours are used.

The script that starts a group's runs can start a session as it does, and leave it open:
once the runs end their blocks are released, and the next session closes it. Runs started
at different times, or by different people, can open and close one by hand.

The session is <group>/resource_session.json; each start and stop is also appended to
<group>/resource_sessions.jsonl.

    python resource_session.py <group> start swing=4 [polaris=10 ...]
    python resource_session.py <group> stop
    python resource_session.py <group>          the session and its node-hours so far

<group> is the group's directory, or its name under workspace/.
"""

import json
import os
import sys
import time

import node_hours

FILE = "resource_session.json"
LOG = "resource_sessions.jsonl"


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write(group, session, event):
    path = os.path.join(group, FILE)
    with open(path + ".tmp", "w") as f:
        json.dump(session, f, indent=2)
    os.replace(path + ".tmp", path)
    with open(os.path.join(group, LOG), "a") as f:
        f.write(json.dumps({"event": event, "ts": time.time(), **session}) + "\n")


def current(group):
    """The group's session while it is open, else None."""
    s = _read(os.path.join(group, FILE))
    return s if s and not s.get("stopped") else None


def workspaces(group):
    """The group's workspaces: its directories that hold runs."""
    try:
        names = sorted(os.listdir(group))
    except OSError:
        return []
    return [os.path.join(group, n) for n in names
            if os.path.isdir(os.path.join(group, n, "runs"))]


def used(group, session):
    """Node-hours per system the group has used in the session."""
    return node_hours.workspaces_used(workspaces(group), session["started"],
                                      session.get("stopped"))


def budget(workspace, system):
    """(used, cap) of the node-hours on `system` when an open session of the workspace's
    group budgets it, else None. Where it does, it holds in place of a launch's own
    node-hour budget."""
    group = os.path.dirname(os.path.abspath(workspace))
    s = current(group)
    cap = (s or {}).get("node_hours", {}).get(system)
    if cap is None:
        return None
    return used(group, s).get(system, 0), cap


def start(group, caps):
    _write(group, {"started": time.time(), "stopped": None, "node_hours": caps}, "start")


def stop(group):
    s = current(group)
    if not s:
        return False
    s["stopped"] = time.time()
    _write(group, s, "stop")
    return True


def _group_dir(arg):
    if os.path.isdir(arg):
        return os.path.abspath(arg)
    lab = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(lab, "workspace", arg)


def _show(group):
    s = _read(os.path.join(group, FILE))
    if not s:
        print(f"{group}: no resource session")
        return
    when = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    state = f"stopped {when(s['stopped'])}" if s.get("stopped") else "open"
    print(f"{os.path.basename(group)}: session started {when(s['started'])}, {state}")
    u = used(group, s)
    for system in sorted(set(s.get("node_hours", {})) | set(u)):
        cap = s.get("node_hours", {}).get(system)
        print(f"  {system}: {u.get(system, 0):.2f} node-hours used"
              + (f" of {cap:g}" if cap is not None else ""))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    group = _group_dir(argv[0])
    if not os.path.isdir(group):
        print(f"no such group: {group}", file=sys.stderr)
        return 1
    if len(argv) == 1:
        _show(group)
        return 0
    if argv[1] == "start":
        try:
            caps = {k: float(v) for k, v in (a.split("=", 1) for a in argv[2:])}
        except ValueError:
            print("give each budget as <system>=<node-hours>, e.g. swing=4", file=sys.stderr)
            return 1
        if not caps:
            print("give at least one budget, e.g. swing=4", file=sys.stderr)
            return 1
        start(group, caps)
        _show(group)
        return 0
    if argv[1] == "stop":
        if not stop(group):
            print(f"{os.path.basename(group)}: no open session")
            return 1
        _show(group)
        return 0
    print(f"unknown command {argv[1]!r}: start or stop", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
