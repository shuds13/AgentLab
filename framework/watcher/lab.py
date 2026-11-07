"""The lab as a whole: its campaigns, which are running, its services, its users and the
secretary."""

import glob
import json
import os
import subprocess
import time

from .workspace import AGENT_ALIVE_WITHIN, LAB_DIR, beat_age, run_time, workspace
from .files import READABLE
from .runs import newest_run, run_elapsed, run_phase, run_usd


def campaigns():
    """Every campaign this lab has a workspace for, for the page to choose between.

    A campaign workspace is one holding a campaign's own records or its runs. The lab
    keeps its own directories under workspace/ too -- run/, logs/, belonging to the
    services rather than to any campaign -- and those are not offered. A directory that
    is not a workspace itself may group several, one level down, as a campaign whose
    runs each have a workspace of their own does; each is offered as `group/name`.

    This is also the set a request is checked against: the name it asks for becomes a
    path, so only a name from here is allowed to."""
    root = os.path.join(LAB_DIR, "workspace")

    def is_workspace(ws):
        return os.path.isdir(os.path.join(ws, "runs")) or any(
            os.path.isfile(os.path.join(ws, f)) for f in READABLE)

    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return found
    for name in names:
        ws = os.path.join(root, name)
        if not os.path.isdir(ws):
            continue
        if is_workspace(ws):
            found.append(name)
            continue
        try:
            subs = os.listdir(ws)
        except OSError:
            continue
        found += [f"{name}/{sub}" for sub in subs
                  if os.path.isdir(os.path.join(ws, sub)) and is_workspace(os.path.join(ws, sub))]
    return sorted(found)


def running_campaigns():
    """The campaigns with a run whose heartbeat is recent: those with an agent at work."""
    live = []
    for name in campaigns():
        beats = glob.glob(os.path.join(workspace(name), "runs", "*", "heartbeat"))
        if any((beat_age(os.path.dirname(b)) or AGENT_ALIVE_WITHIN) < AGENT_ALIVE_WITHIN
               for b in beats):
            live.append(name)
    return live


# How long a finished campaign stays near the top of the list, marked as recently ended.
RECENT_WITHIN = float(os.environ.get("WATCH_RECENT_HOURS", "24")) * 3600


def recent_campaigns():
    """The campaigns not running whose latest run ended within RECENT_WITHIN, newest
    first, each with when that was."""
    live, now, out = set(running_campaigns()), time.time(), []
    for name in campaigns():
        if name in live:
            continue
        ends = [run_time(os.path.dirname(m)) for m in
                glob.glob(os.path.join(workspace(name), "runs", "*", "meta.json"))]
        ended = max(ends, default=None)
        if ended and now - ended < RECENT_WITHIN:
            out.append({"campaign": name, "ended": ended})
    return sorted(out, key=lambda r: -r["ended"])


def group_siblings(campaign):
    """The other workspaces of the group a workspace belongs to (`group/name`), whose
    runs may share its blocks; none for a workspace of its own."""
    if "/" not in campaign:
        return []
    group = campaign.split("/", 1)[0] + "/"
    return [c for c in campaigns() if c.startswith(group) and c != campaign]


# The lab's own run directory: state belonging to the lab rather than to any one
# campaign. The secretary's inbox and the lab transcript live here.
def lab_run_dir():
    return os.path.join(LAB_DIR, "workspace", "run")


def lab_messages():
    """The lab conversation: what was asked here and what the secretary answered.

    `framework/notify.sh` appends every reply, whether or not Slack is configured, so
    this is the transcript even in a lab with no Slack at all."""
    try:
        with open(os.path.join(lab_run_dir(), "MESSAGES.md")) as fh:
            return fh.read()
    except OSError:
        return ""


def slack_attached():
    """Is there a Slack channel on the other end of this conversation?

    The page says so before you type: a line sent here is answered by the secretary
    through `framework/notify.sh`, which carries it on to Slack when a webhook is
    configured.
    Someone writing in the browser would otherwise have no way to know the reply --
    quoting their question -- appears in a channel.

    The webhook is the one this lab names in `notifiers/slack/slack.env`, which `watch.sh`
    reads through `settings.sh`. A lab that names none is its own conversation, so
    several labs on one machine stay separate until they are pointed at a channel."""
    path = os.environ.get("SLACK_WEBHOOK_FILE") or ""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


# A secretary beats every poll (5s by default), so it is known dead in seconds -- not
# on the agent's window, which is sized for a research agent that can spend minutes in
# one model turn. This is the figure the Slack reader uses to decide the same thing,
# and the two must agree or the page and the bridge disagree about who is up.
SECRETARY_ALIVE_WITHIN = int(os.environ.get("SECRETARY_ALIVE_WITHIN", "60"))


def secretary_live():
    """Is a secretary reading the inbox? It writes a heartbeat every poll, so a stale
    one means questions asked here will sit unanswered until it is started."""
    try:
        with open(os.path.join(lab_run_dir(), "secretary_heartbeat")) as fh:
            return (time.time() - float(fh.read().strip())) <= SECRETARY_ALIVE_WITHIN
    except Exception:
        return False


# What `lab.sh status` last said, and when. The page polls, and each ask is a bash and
# a python3; a few seconds stale is not worth spawning those every second.
services_state = {"at": 0.0, "rows": [], "none": False}
SERVICES_TTL = 3


def lab_services(force=False):
    """The lab's own processes and whether they are up.

    Asked of `lab.sh` rather than worked out here: which services a lab has is
    lab.yaml's business and what counts as running is that script's, and a second
    opinion in this file would be one to keep in step."""
    now = time.time()
    if not force and now - services_state["at"] < SERVICES_TTL:
        return services_state["rows"]
    rows = []
    try:
        out = subprocess.run(["./lab.sh", "status"], cwd=os.path.join(LAB_DIR, "bin"),
                             capture_output=True, text=True, timeout=60).stdout
    except Exception:
        out = ""
        services_state.update(at=now, rows=[], none=True)
        return []
    for line in out.splitlines():
        name, _, rest = line.strip().partition(":")
        rest = rest.strip()
        if name and rest:
            rows.append({"service": name, "running": rest.startswith("running"),
                         "detail": rest})
    services_state.update(at=now, rows=rows, none=False)
    return rows


def lab_control(action):
    """Start or stop those processes, by running what a person would run. `lab.sh` owns
    what that means -- which services, in which order, and leaving alone anything it did
    not start itself. Starting waits for each service to come up, so this is slow, and
    the page is told what the script printed rather than a bare success."""
    try:
        done = subprocess.run(["./lab.sh", action], cwd=os.path.join(LAB_DIR, "bin"),
                              capture_output=True, text=True, timeout=300)
        text = (done.stdout + done.stderr).strip() or f"{action}: nothing to do"
    except Exception as e:
        text = f"{action} failed: {e}"
    services_state["at"] = 0.0          # whatever it did, the cached answer is now stale
    return text


def lab_users():
    """Who is set up in this lab, grouped by person.

    A user is a directory of `users/<name>/<system>.json`, so one person holds one entry
    per system they have access to. Read fresh each time and never cached: a user file
    appears when someone is added, and the page is how you see that happened.

    Credentials are not returned. The endpoint id is an address, not a secret, and it is
    what someone comes to this page for; nothing else in the file is sent.

    Two things are reported rather than hidden, because a lab is shared and a silent
    omission reads as "nobody is there": a file still carrying the template's
    placeholders is a stub, and a directory this process cannot read is named with
    `unreadable` set. Someone half set up, or set up behind permissions you do not have,
    is still someone in the lab."""
    people = []
    root = os.path.join(LAB_DIR, "users")
    try:
        names = sorted(os.listdir(root))
    except OSError as exc:
        # The whole directory, not one person: say which it is, so an empty section
        # cannot be mistaken for a lab with nobody in it.
        return [{"user": os.path.basename(root), "systems": [], "unreadable": True,
                 "error": exc.strerror or str(exc)}] if os.path.exists(root) else []
    for user in names:
        udir = os.path.join(root, user)
        if not os.path.isdir(udir):
            continue
        try:
            fnames = sorted(os.listdir(udir))
        except OSError as exc:
            people.append({"user": user, "systems": [], "unreadable": True,
                           "error": exc.strerror or str(exc)})
            continue
        systems = []
        for fname in fnames:
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(udir, fname)) as fh:
                    cfg = json.load(fh)
                unreadable = False
            except OSError:
                cfg, unreadable = {}, True
            except Exception:
                cfg, unreadable = {}, False
            endpoint = str(cfg.get("endpoint", "") or "")
            account = str(cfg.get("account", "") or "")
            # The templates ship values in angle brackets, and an unedited one must not
            # read as a working endpoint.
            stub = endpoint.startswith("<") or not endpoint
            systems.append({"system": fname[: -len(".json")],
                            "endpoint": endpoint, "account": account,
                            "work_dir": str(cfg.get("work_dir", "") or ""),
                            "transfer": bool(cfg.get("globus")) and not stub,
                            "stub": stub, "unreadable": unreadable})
        if systems:
            people.append({"user": user, "systems": systems, "unreadable": False})
    return people


def lab_summary(campaign):
    """One row of the lab page: enough to tell whether to go in.

    Nothing here grows with the campaign's history. A lab accumulates campaigns and
    each of those accumulates runs, so this reads the newest run's own small files and
    leaves the counts -- which mean reading every job and result ever recorded -- to
    the campaign's own page, where there is one campaign to pay for."""
    run_dir = newest_run(campaign)
    if not run_dir:
        return {"campaign": campaign, "run": None}
    try:
        with open(os.path.join(run_dir, "meta.json")) as f:
            meta = json.load(f)
    except Exception:
        return {"campaign": campaign, "run": None}
    age = beat_age(run_dir)
    phase, phase_age = run_phase(run_dir)
    return {
        "campaign": campaign, "run": meta.get("run_id"),
        "handle": meta.get("handle"), "status": meta.get("status"),
        "stop_reason": meta.get("stop_reason"), "model": meta.get("model"),
        "effort": meta.get("effort"), "advisor": meta.get("advisor"),
        "system": meta.get("system"), "endpoint": meta.get("endpoint"),
        "started_at": meta.get("started_at"), "ended_at": meta.get("ended_at"),
        "elapsed_s": run_elapsed(meta, run_dir),
        "heartbeat_age_s": None if age is None else int(age),
        # Liveness is the heartbeat being recent, not the run saying it is running:
        # a run killed outright never gets to write down that it stopped.
        "live": age is not None and age <= AGENT_ALIVE_WITHIN,
        "phase": phase, "phase_age_s": phase_age,
        "usd": run_usd(meta),
        "score": (meta.get("scores") or {}).get(meta.get("primary_score")),
    }


def latest_campaign():
    """The campaign to open on when none was named: the one most recently active.

    A campaign whose run is still beating comes first, whenever that run began, since
    that is the one there is something to watch. The rest are ranked by when their last
    run ended. A campaign that has never run sorts last; there is nothing of it to
    show."""
    def rank(campaign):
        run_dir = newest_run(campaign)
        if not run_dir:
            return (0, 0.0)
        age = beat_age(run_dir)
        if age is not None and age <= AGENT_ALIVE_WITHIN:
            return (2, -age)            # beating: the freshest of them first
        return (1, run_time(run_dir))

    names = campaigns()
    return max(names, key=rank) if names else None
