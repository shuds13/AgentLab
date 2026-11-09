"""A campaign's runs: the newest, the past ones, the workers a run started and what each
has cost."""

import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from .workspace import AGENT_ALIVE_WITHIN, FRAMEWORK_DIR, beat_age, read_meta, run_time, workspace


def newest_run(campaign):
    """The run to show: one that is still beating, else the most recent.

    Not simply the newest meta.json -- a live run writes its meta once at startup and
    a finished one writes its own at exit, so a run that ended later looks newer than
    a run still going, and the view would stick to the finished one.

    Live means a RECENT heartbeat, not the file existing. A run killed outright leaves
    its heartbeat behind, and counting that as live pins the view to a dead run for
    good: every later run ends and removes its own, so the abandoned one is the only
    candidate left."""
    metas = glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json"))
    if not metas:
        return None
    # A worker another run started is reached from that run, so the view opens on the
    # runs nobody started.
    metas = [m for m in metas if not read_meta(m).get("parent_run")] or metas
    live = []
    for m in metas:
        age = beat_age(os.path.dirname(m))
        if age is not None and age < AGENT_ALIVE_WITHIN:
            live.append(m)
    return os.path.dirname(max(live or metas,
                               key=lambda m: run_time(os.path.dirname(m))))


# Workers commit each evaluation as `{description} | score = {value}`.
SCORE_RE = re.compile(r"\|\s*score\s*=\s*([-+0-9.eE]+)")
_scores = {}                # (cwd, span) -> (when read, score)


def _branch_score(cwd, branch, parent):
    """The newest score a worker committed on its branch, or None. Status is asked for
    every second or so, so a reading is kept a few seconds."""
    span = f"{parent}..{branch}" if parent else branch
    hit = _scores.get((cwd, span))
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    score = None
    try:
        log = subprocess.run(["git", "-C", cwd, "log", "--format=%s", span],
                             capture_output=True, text=True, timeout=5).stdout
        for subject in log.splitlines():
            found = SCORE_RE.search(subject)
            if found:
                score = float(found.group(1))
                break
    except Exception:
        pass
    _scores[(cwd, span)] = (time.time(), score)
    return score


def run_score(meta, cwd=None):
    """The score to show for a run: the one its campaign names as primary, from the
    scores the run recorded; its only score if it recorded one; else, for a run from
    before scores were recorded, the newest score committed on its branch."""
    scores = meta.get("scores") or {}
    primary = meta.get("primary_score")
    if primary:
        return scores.get(primary)
    if len(scores) == 1:
        return next(iter(scores.values()))
    if not scores and cwd and meta.get("branch"):
        return _branch_score(cwd, meta["branch"], meta.get("parent"))
    return None


def shows_cost(meta):
    """Whether the run shows its cost (SHOW_COST). It is recorded either way, for
    bin/cost.sh."""
    return meta.get("show_cost") is not False


def run_usd(meta):
    if not shows_cost(meta):
        return None
    usd = [c.get("usd") for c in meta.get("cost_models") or [] if c.get("usd") is not None]
    return sum(usd) if usd else None


def workers(campaign, run_id):
    """The runs `run_id` started, oldest first, with each one's score and cost."""
    out = []
    if not run_id:
        return out
    for m in glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json")):
        meta = read_meta(m)
        if meta.get("parent_run") != run_id:
            continue
        d = os.path.dirname(m)
        age = beat_age(d)
        state = meta.get("status")
        if state == "running" and (age is None or age >= AGENT_ALIVE_WITHIN):
            state = "not responding"
        cwd = run_cwd(campaign, meta)
        out.append({"run": os.path.basename(d), "kind": meta.get("kind"),
                    "method": meta.get("method"), "started_by": meta.get("started_by"),
                    "branch": meta.get("branch"), "parent": meta.get("parent"),
                    "parents": meta.get("parents") or [],
                    "status": state, "usd": run_usd(meta),
                    "score": run_score(meta, cwd),
                    "started_at": meta.get("started_at")})
    out.sort(key=lambda w: w["started_at"] or "")
    return out


def run_path(campaign, run=None):
    """The run a request is about: the one named, if the campaign has it, else the
    newest. The name becomes a path, so only a run directory that exists is accepted;
    any other name finds no run."""
    if not run:
        return newest_run(campaign)
    d = os.path.join(workspace(campaign), "runs", run)
    if os.path.basename(run) == run and os.path.isfile(os.path.join(d, "meta.json")):
        return d
    return None


def ended_with_session(run_dir):
    """A run whose agent has stopped and left a session that can be reopened."""
    age = beat_age(run_dir)
    return ((age is None or age >= AGENT_ALIVE_WITHIN)
            and bool(read_meta(os.path.join(run_dir, "meta.json")).get("session_id")))


def talk_to_run(run_dir, ws, text, stamp):
    """Put the message in the conversation and ask the run's agent about it. The reply
    takes as long as a turn does, so talk.py adds it to the conversation itself."""
    messages = os.path.join(ws, "MESSAGES.md")
    try:
        with open(messages, "a") as f:
            f.write(f"`{stamp}` **you** {text}\n\n")
        with open(os.path.join(run_dir, "talk.log"), "a") as log:
            subprocess.Popen([sys.executable, os.path.join(FRAMEWORK_DIR, "talk.py"), run_dir,
                              "-m", text, "--record", messages],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
    except OSError as e:
        return {"sent": False, "error": str(e)}
    return {"sent": True, "talk": True}


def runs(campaign):
    """Every run of the campaign, newest first: enough to choose one by."""
    out = []
    for m in glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json")):
        d = os.path.dirname(m)
        try:
            with open(m) as f:
                meta = json.load(f)
        except Exception:
            meta = {}
        usd = run_usd(meta)
        out.append({"run": os.path.basename(d), "started_at": meta.get("started_at"),
                    "status": meta.get("status"), "stop_reason": meta.get("stop_reason"),
                    "model": meta.get("model"), "turns": meta.get("turns"),
                    "usd": usd, "_t": run_time(d),
                    "parent_run": meta.get("parent_run"), "kind": meta.get("kind"),
                    "branch": meta.get("branch")})
    out.sort(key=lambda r: r.pop("_t"), reverse=True)
    return out


# What a run was handed: its prompts, its method, its task module, the campaign as it
# stood. Written at startup and belonging to that run, unlike the campaign's records,
# which every run of it appends to. What status already reports is left to status.
RUN_HIDDEN = ("meta.json",)
RUN_SUFFIXES = (".md", ".json", ".py")


def run_cwd(campaign, meta):
    """A run's working directory as this watcher can reach it. meta.json records it as
    an absolute path; where it lies inside the workspace the run wrote to (`shared_dir`),
    it is taken relative to the workspace read here, so a workspace that has been moved
    or copied still finds it."""
    cwd, shared = meta.get("cwd"), meta.get("shared_dir")
    if not cwd:
        return None
    if shared:
        rel = os.path.relpath(cwd, shared)
        if not rel.startswith(".."):
            return os.path.normpath(os.path.join(workspace(campaign), rel))
    return cwd


def run_files(campaign, run=None):
    """The snapshots a run (the newest, unless one is named) left in its own directory.

    This is also the set a request is checked against: a name not listed here is not
    served, so the name cannot be used to reach outside the run."""
    run = run_path(campaign, run)
    if not run:
        return []
    try:
        names = os.listdir(run)
    except OSError:
        return []
    return sorted(n for n in names
                  if n not in RUN_HIDDEN
                  and n.endswith(RUN_SUFFIXES)
                  and os.path.isfile(os.path.join(run, n)))


def run_phase(run_dir):
    """What the run said it was doing, and how long ago it said so."""
    try:
        with open(os.path.join(run_dir, "phase")) as f:
            stamp, phase = f.read().split("\n", 1)
            return phase.strip(), int(time.time() - float(stamp))
    except Exception:
        return None, None


def run_elapsed(meta, run_dir=None):
    """How long the run took, not how long ago it began: once it has ended, the clock
    stops where it stopped. A run that stopped without recording its end -- killed,
    crashed, or a copy of a run that was still going -- stops at its last heartbeat. A
    continued run's time between parts is left out."""
    started, ended = meta.get("started_at"), meta.get("ended_at")
    if not started:
        return None
    try:
        end = datetime.fromisoformat(ended).timestamp() if ended else time.time()
        age = beat_age(run_dir) if run_dir and not ended else None
        if age is not None and age >= AGENT_ALIVE_WITHIN:
            end = time.time() - age
        return int(end - datetime.fromisoformat(started).timestamp()
                   - (meta.get("paused_s") or 0))
    except Exception:
        return None


def newest_log(campaign):
    logs = glob.glob(os.path.join(workspace(campaign), "logs", "run_*.log"))
    return max(logs, key=os.path.getmtime) if logs else None
