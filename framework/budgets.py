"""A run's budgets: every limit it stops at, as a total for the whole run.

They are kept in runs/<id>/budgets.json, so that everything enforcing a budget reads the
same value, a person sees them all in one place (the watch), and they can be raised while
the run goes or before it is continued (bin/budget.sh). Each change is logged in
runs/<id>/budget_log.jsonl, which the run's agent reads, so it is told of it.

  submits              jobs the run may submit: remote where it has a compute system,
                       else local
  submits_buckets      the same per bucket, {bucket: n}
  runtime_s            seconds the run may run, its parts' run time added up
  node_hours           node-hours per system, {system: h}, for the run and its workers
  worker_jobs          jobs the run's workers may submit between them
  worker_jobs_buckets  the same per bucket, {bucket: n}
  workers              workers of each kind the run may start, {kind: n}

A budget that is absent or null is no limit.

    python budgets.py <workspace> [--run RUN_ID]                  show the budgets
    python budgets.py <workspace> [--run RUN_ID] submits=+10 ...  change them

<workspace> is a campaign's workspace directory or its name under workspace/. The run is
the newest one that no other run started, unless named. A change is NAME[.KEY]=+N to add,
=-N to take away, or =N to set; runtime_s takes s, m or h, e.g. runtime_s=+2h.
"""

import fcntl
import glob
import json
import os
import sys
import time

FILE = "budgets.json"
LOG = "budget_log.jsonl"
# Budgets that hold one number per bucket, system or kind.
KEYED = ("submits_buckets", "node_hours", "worker_jobs_buckets", "workers")
NAMES = ("submits", "submits_buckets", "runtime_s", "node_hours", "worker_jobs",
         "worker_jobs_buckets", "workers")

_cache = {}


def run_dir():
    """This process's run directory, or None outside a run."""
    if not os.environ.get("WORKSPACE_DIR") or not os.environ.get("RUN_ID"):
        return None
    return os.path.join(os.environ["WORKSPACE_DIR"], "runs", os.environ["RUN_ID"])


def top_run_dir():
    """The directory of the run that started this one, or this run's own: launch-wide
    budgets such as node-hours belong to it."""
    parent = json.loads(os.environ.get("RUN_META") or "{}").get("parent_run")
    if parent and os.environ.get("WORKSPACE_DIR"):
        return os.path.join(os.environ["WORKSPACE_DIR"], "runs", parent)
    return run_dir()


def load(directory=None):
    """The budgets of a run (this process's, unless named), or {} before it has any.
    Read again only when the file has changed."""
    directory = directory or run_dir()
    if not directory:
        return {}
    path = os.path.join(directory, FILE)
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    held = _cache.get(path)
    if held and held[0] == stamp:
        return held[1]
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return held[1] if held else {}
    _cache[path] = (stamp, data)
    return data


def limit(name, key=None, fallback=None, directory=None):
    """A budget's current value, or `fallback` where the run has none recorded: before
    its budgets are written, or for a key it has no entry for."""
    budgets = load(directory)
    if name not in budgets:
        return fallback
    value = budgets[name]
    if key is not None:
        if not isinstance(value, dict) or key not in value:
            return fallback
        value = value[key]
    return value


def _write(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def seed(directory, values):
    """Record a run's budgets at its start. A run that already has them, a continued
    one, keeps them; only budgets it has no entry for are added."""
    path = os.path.join(directory, FILE)
    with open(os.path.join(directory, "budgets.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        current = load(directory) if os.path.exists(path) else {}
        merged = dict(current)
        for name, value in values.items():
            if name not in merged:
                merged[name] = value
            elif isinstance(value, dict) and isinstance(merged[name], dict):
                merged[name] = {**value, **merged[name]}
        if merged != current or not os.path.exists(path):
            _write(path, merged)
    return merged


def from_meta(directory):
    """The budgets a run from before budgets files recorded in its meta.json, so it can
    be raised and continued like any other."""
    try:
        with open(os.path.join(directory, "meta.json")) as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    found = {"submits": meta.get("max_submits"),
             "submits_buckets": meta.get("max_submits_buckets"),
             "runtime_s": meta.get("max_runtime_s"),
             "node_hours": meta.get("max_node_hours")}
    return {k: v for k, v in found.items() if v is not None}


def _seconds(text):
    text = text.strip()
    scale = {"s": 1, "m": 60, "h": 3600}.get(text[-1:].lower())
    return float(text[:-1]) * scale if scale else float(text)


def change(directory, changes, who=""):
    """Apply changes, each (name, key or None, "+" / "-" / "=", amount), and log them.
    Returns what changed, as {"name", "key", "before", "after"}."""
    path = os.path.join(directory, FILE)
    done = []
    with open(os.path.join(directory, "budgets.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                budgets = json.load(f)
        except (OSError, json.JSONDecodeError):
            budgets = from_meta(directory)
        for name, key, op, amount in changes:
            if name not in NAMES:
                raise ValueError(f"no budget {name!r}; budgets are {', '.join(NAMES)}")
            if name in KEYED and key is None:
                raise ValueError(f"{name} is one per key: give {name}.<key>")
            if name not in KEYED and key is not None:
                raise ValueError(f"{name} takes no key")
            holder = budgets.setdefault(name, {}) if key is not None else budgets
            field = key if key is not None else name
            before = holder.get(field)
            if op == "=":
                after = amount
            elif before is None:
                raise ValueError(f"{name}{'.' + key if key else ''} has no limit to add to; "
                                 f"give a number, with no sign, to set one")
            else:
                after = before + amount if op == "+" else before - amount
            if isinstance(after, float) and after.is_integer() and name != "node_hours":
                after = int(after)
            holder[field] = after
            done.append({"name": name, "key": key, "before": before, "after": after})
        _write(path, budgets)
        with open(os.path.join(directory, LOG), "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "who": who,
                                "changes": done}) + "\n")
    return done


def changes_since(directory, seen):
    """Budget changes logged after the first `seen`, and how many there are now."""
    try:
        with open(os.path.join(directory, LOG)) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, json.JSONDecodeError):
        return [], seen
    return [c for r in rows[seen:] for c in r.get("changes", [])], len(rows)


def describe(c):
    """One change as the agent is told it."""
    label = c["name"] + (f" ({c['key']})" if c.get("key") else "")
    before = "no limit" if c["before"] is None else f"{c['before']:g}"
    return f"{label}: {before} -> {c['after']:g}"


def _parse(arg):
    lhs, sep, rhs = arg.partition("=")
    if not sep or not rhs:
        raise ValueError(f"expected NAME[.KEY]=+N, =-N or =N, got {arg!r}")
    name, _, key = lhs.partition(".")
    op = rhs[0] if rhs[0] in "+-" else "="
    text = rhs[1:] if op != "=" else rhs
    amount = _seconds(text) if name == "runtime_s" else float(text)
    return name, key or None, op, amount


def _newest_top_run(workspace):
    found = []
    for path in glob.glob(os.path.join(workspace, "runs", "*", "meta.json")):
        try:
            with open(path) as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not meta.get("parent_run"):
            found.append((meta.get("part_started_at") or meta.get("started_at") or "",
                          os.path.basename(os.path.dirname(path))))
    return max(found)[1] if found else None


def main(argv):
    args = list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    workspace = args.pop(0)
    if not os.path.isdir(workspace):
        lab = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        workspace = os.path.join(lab, "workspace", workspace)
    run = None
    if "--run" in args:
        i = args.index("--run")
        run = args[i + 1]
        del args[i:i + 2]
    run = run or _newest_top_run(workspace)
    directory = os.path.join(workspace, "runs", run or "")
    if not run or not os.path.isdir(directory):
        print(f"no run {run!r} in {workspace}/runs", file=sys.stderr)
        return 1
    if args:
        try:
            done = change(directory, [_parse(a) for a in args], who=os.environ.get("USER", ""))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        for c in done:
            print(describe(c))
        return 0
    budgets = load(directory)
    print(f"{run}:")
    for name in NAMES:
        if name in budgets:
            print(f"  {name}: {json.dumps(budgets[name])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
