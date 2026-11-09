"""Where the lab is, a campaign's workspace in it, and what a run directory says about
itself."""

import json
import os
import time
from datetime import datetime


# The directory holding watch.py and this package: framework/ in a lab.
FRAMEWORK_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The lab is the directory holding workspace/. In a lab that is the parent of
# framework/. A workspace copied elsewhere is watched by putting the watcher beside it,
# and then it is that directory -- so whichever has a workspace under it wins, and
# LAB_DIR overrides both.
if os.path.isdir(os.path.join(FRAMEWORK_DIR, "workspace")):
    _default_lab = FRAMEWORK_DIR
else:
    _default_lab = os.path.join(FRAMEWORK_DIR, "..")
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", _default_lab))


def workspace(campaign):
    return os.path.join(LAB_DIR, "workspace", campaign)


def read_meta(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def file_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def run_time(run_dir):
    """When a run last mattered: when it ended, or when it started if it has not.

    Taken from meta.json's own timestamps, which belong to the run and outlive any
    copy of it. The file's mtime is the fallback for a meta that cannot be read or
    carries no timestamp."""
    meta_path = os.path.join(run_dir, "meta.json")
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        stamp = meta.get("ended_at") or meta.get("started_at")
        if stamp:
            return datetime.fromisoformat(stamp).timestamp()
    except (OSError, ValueError, TypeError):
        pass
    return file_mtime(meta_path)


# Liveness is a RECENT heartbeat, not the file existing: a run killed outright leaves
# its heartbeat behind, and a stale one must not read as alive. The convention, and the
# override, are the agent's that writes it.
AGENT_ALIVE_WITHIN = int(os.environ.get("AGENT_ALIVE_WITHIN", "600"))


def beat_age(run_dir):
    """Seconds since the run last said it was alive, or None if it never did."""
    try:
        with open(os.path.join(run_dir, "heartbeat")) as f:
            return time.time() - float(f.read().strip())
    except (OSError, ValueError):
        return None
