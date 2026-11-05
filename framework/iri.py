"""Read-only access to a facility's IRI API: the state of a system and its queues.

IRI (the DOE Integrated Research Infrastructure facility API) is one interface across
facilities, so this is written to its specification and a system supplies only its own
values, in an `iri` block in systems/<system>.json:

    "iri": {
      "base_url": "https://api.alcf.anl.gov/api/v2",
      "compute_resource": "<the system's resource id, from GET /status/resources>",
      "token_command": ["alcf-tokens", "get-token", "iri"]
    }

The same block in users/<you>/<system>.json overrides any of these for one user, and may
add `username` (your login at the facility, when it is not the name this lab knows you
by). A campaign asks for the tool with `"iri": true` in campaign.json.

Only status calls are made. Submitting and cancelling stay with the framework, so that
budgets and resource buckets apply to every job.
"""
import json
import subprocess
from collections import Counter

import requests

TIMEOUT = 60
PAGE = 1000
OPEN_INCIDENTS = ("unresolved", "pending", "extended")


def configure(sys_cfg, usr_cfg, username):
    """The settings for one system, or None when it has no `iri` block."""
    c = dict((sys_cfg or {}).get("iri") or {})
    c.update((usr_cfg or {}).get("iri") or {})
    if not c.get("base_url") or not c.get("compute_resource"):
        return None
    c["base_url"] = c["base_url"].rstrip("/")
    c.setdefault("username", username)
    return c


def _token(c):
    cmd = c.get("token_command")
    if not cmd:
        raise RuntimeError("no token_command in the system's iri block")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        raise RuntimeError(f"`{cmd[0]}` is not installed in the environment the lab runs "
                           f"in (see docs/iri.md)")
    tok = r.stdout.strip()
    if r.returncode or not tok:
        raise RuntimeError(f"`{' '.join(cmd)}` failed: {(r.stderr or r.stdout).strip()[:400]}")
    return tok


def _call(c, method, path, token=None, **kw):
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = requests.request(method, f"{c['base_url']}/{path}", headers=h, timeout=TIMEOUT, **kw)
    if not r.ok:
        try:
            detail = r.json().get("detail") or r.text
        except ValueError:
            detail = r.text
        raise RuntimeError(f"{method} {path}: HTTP {r.status_code}: {str(detail)[:400]}")
    return r.json()


def _jobs(c, token, filters):
    """Every current (not historical) job matching `filters`, across pages."""
    out, offset = [], 0
    while True:
        page = _call(c, "POST", f"compute/status/{c['compute_resource']}", token,
                     params={"historical": "false", "limit": PAGE, "offset": offset},
                     json=filters)
        out += page
        if len(page) < PAGE:
            return out
        offset += PAGE


def _job(j):
    a = (j.get("job_spec") or {}).get("attributes") or {}
    st = j.get("status") or {}
    return {"id": str(j.get("id", "")).split(".")[0], "state": st.get("state"),
            "queue": a.get("queue_name"), "account": a.get("account"),
            **({"message": st["message"]} if st.get("message") else {})}


def check(c):
    """A problem with the settings or the token, or None. For the preflight."""
    try:
        _call(c, "GET", "account/projects", _token(c))
    except Exception as e:
        return str(e)
    return None


def status(c, queues=()):
    """The system's status and open incidents, the user's current jobs, and how many
    jobs are queued, held and running in each of `queues` and each queue those jobs
    are in."""
    rid = c["compute_resource"]
    out = {}
    try:
        out["status"] = _call(c, "GET", f"status/resources/{rid}").get("current_status")
        inc = _call(c, "GET", "status/incidents", params={"resource_id": rid, "limit": 200})
        out["incidents"] = [{k: i.get(k) for k in ("name", "description", "status", "type",
                                                  "start", "end", "resolution")}
                            for i in inc if i.get("resolution") in OPEN_INCIDENTS]
    except Exception as e:
        out["status_error"] = str(e)
    try:
        token = _token(c)
        mine = [_job(j) for j in _jobs(c, token, {"owner": c["username"]})]
        out["your_jobs"] = mine
        counts = {}
        for q in dict.fromkeys([q for q in queues if q] + [j["queue"] for j in mine
                                                           if j["queue"]]):
            n = Counter((j.get("status") or {}).get("state")
                        for j in _jobs(c, token, {"queue": q}))
            counts[q] = {s: n.get(s, 0) for s in ("queued", "held", "active")}
        out["queues"] = counts
    except Exception as e:
        out["jobs_error"] = str(e)
    return out
