"""Globus Transfer as an agent tool: read and write files on the compute system.

Optional. A campaign whose agent and compute system share a filesystem never needs it.
Where they do not, this is how anything reaches the compute side other than the job
function itself -- scripts, inputs, configuration -- and how anything comes back.

The shape it is built for: the agent keeps a set of files here, edits them, sends them
over, runs a job against them, reads the results, edits again. Only what the job needs
at submit time belongs inside the Globus Compute function; everything the agent expects
to revise between jobs is better as files it can push. Staging results elsewhere is the
same operation in the other direction.

Not to be confused with Globus Compute, which runs the job. This moves bytes.

Configuration is per user, in `users/<you>/<system>.json`, because collection IDs are an
access fact rather than a property of the machine:

    "globus": {
      "remote_collection": "<uuid>",     required -- the compute system's collection
      "local_collection":  "<uuid>",     required -- this machine's collection
      "remote_write_root": "<path>",     optional -- defaults to work_dir
      "remote_read_root":  "<path>",     optional -- unset means read anywhere you can
      "collection_root":   "<path>"      optional -- overrides the system file
    }

Paths are written as POSIX paths throughout -- the same ones the job sees. Where a
collection is not rooted at the filesystem root, `globus_collection_root` in
`systems/<system>.json` says what its "/" corresponds to, and paths are translated on
the way out.

Absent that block the tools are not offered at all, so an installation that does not use
Globus Transfer sees no sign of it.

Two things about the local side that have to be right:

- Globus Connect Personal only serves paths listed in `~/.globusonline/lta/config-paths`,
  and being listed there is not enough on its own -- the entry has to grant write. The
  workspace path must therefore appear in that file, e.g. `/path/to/AgentLab/,0,1`,
  followed by a GCP restart. Transfers go straight to their destination in the workspace:
  an earlier design staged them under $HOME first, on the assumption that the default
  `~/` entry allows writes, and it does not -- every transfer failed with
  PERMISSION_DENIED on the directory create.
- Transfer is asynchronous. Every operation here waits for the task to finish and reports
  the outcome, because an agent that is told "submitted" cannot act on it.
"""

import json
import os
import sqlite3
import sys
import time

from claude_agent_sdk import tool

# Bounded so a huge log cannot flood the agent's context. The file is always saved in
# full; these limits apply only to what is returned inline.
_HEAD_BYTES = 4000
_TAIL_BYTES = 12000
_WAIT_SECONDS = 600
_POLL_SECONDS = 2


def _as_list(v):
    return v if isinstance(v, (list, tuple)) else [v]


def configure(user_cfg, workspace_dir, campaign_dir=None, sys_cfg=None):
    """Read the optional `globus` block. Returns None when Transfer is not configured,
    which is what gates the tools out of the server."""
    g = dict(user_cfg.get("globus") or {})
    sys_cfg = dict(sys_cfg or {})
    remote = str(g.get("remote_collection", "")).strip()
    local = str(g.get("local_collection", "")).strip()
    if not remote or not local or remote.startswith("<") or local.startswith("<"):
        return None
    return {
        "remote_collection": remote,
        "local_collection": local,
        # Writes to the compute system are confined to one subtree. Defaulting it to
        # work_dir means the tool cannot scribble outside the campaign's own directory
        # unless someone widens it deliberately.
        "remote_write_root": str(g.get("remote_write_root")
                                 or user_cfg.get("work_dir", "")).rstrip("/"),
        # Reads are unbounded by default: the usual job is fetching a log from a path
        # the campaign did not choose. Set it to confine reads to one subtree.
        "remote_read_root": str(g.get("remote_read_root") or "").rstrip("/"),
        # A collection's "/" is not always the filesystem's "/". NERSC's exposes
        # absolute POSIX paths; ALCF's are rooted at the filesystem's projects
        # directory, so /lus/eagle/projects/foo/bar is /foo/bar to the collection.
        # Everything else here is written in POSIX paths and translated on the way out.
        # One or several prefixes: the same filesystem is often reachable by a short
        # mount path and a long one (/flare and /lus/flare/projects), and a work_dir may
        # be written either way. The first that matches is stripped.
        "collection_root": [str(r).rstrip("/") for r in _as_list(
            g.get("collection_root") if g.get("collection_root") is not None
            else sys_cfg.get("globus_collection_root", "")) if str(r).strip()],
        "workspace_dir": workspace_dir,
        # The agent may send from, and fetch into, either the campaign's own directory
        # (task.py and the scripts a job runs) or its workspace (results and artefacts).
        # Scripts it revises between jobs live in the former, so bounding to the
        # workspace alone would rule out the main reason to have this tool.
        "local_roots": [d for d in (workspace_dir, campaign_dir) if d],
    }


CFG = None          # set by tools.py at import; None disables the tools


# --- the Transfer client --------------------------------------------------------------
# One client for the life of the process, built from the tokens `globus login` already
# stored. The CLI remains the setup step; this reads its token store instead of running
# it, so an operation costs one HTTPS request rather than a Python process start. The
# tokens are read, never written: a refresh is held in memory for this process only, so
# nothing here contends with the CLI or another agent for the same file.

_CLIENT = None
_CLIENT_ERROR = None


def _cli_storage_path():
    """Where the Globus CLI keeps its tokens, per globus_cli.login_manager.storage."""
    if sys.platform == "win32":
        datadir = (os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
                   or os.path.join(os.path.expanduser("~"), "AppData", "Local"))
        return os.path.join(datadir, "globus", "cli", "storage.db")
    return os.path.expanduser("~/.globus/cli/storage.db")


def _cli_namespace():
    """The CLI's token namespace: userprofile/<environment>[/<profile>]."""
    env = os.environ.get("GLOBUS_SDK_ENVIRONMENT") or "production"
    profile = os.environ.get("GLOBUS_PROFILE")
    return "userprofile/" + env + (f"/{profile}" if profile else "")


def _build_client():
    from globus_sdk import (ConfidentialAppAuthClient, RefreshTokenAuthorizer,
                            TransferClient)
    from globus_sdk.token_storage import SQLiteTokenStorage

    db = _cli_storage_path()
    login = "\n  run: globus login"
    if not os.path.isfile(db):
        raise RuntimeError(f"no Globus login found at {db}{login}")
    ns = _cli_namespace()
    store = SQLiteTokenStorage(filepath=db, namespace=ns)
    try:
        tokens = store.get_token_data_by_resource_server()
    finally:
        store.close()
    td = tokens.get("transfer.api.globus.org")
    if td is None:
        raise RuntimeError(f"the Globus login in {db} holds no Transfer token{login}")
    if not td.refresh_token:
        raise RuntimeError(f"the stored Transfer token cannot be refreshed{login}")

    # The CLI logs in as a templated confidential client, so refreshing its tokens needs
    # that client's own credentials. They sit beside the tokens in the same file.
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT config_data_json FROM config_storage "
                           "WHERE namespace = ? AND config_name = 'auth_client_data'",
                           (ns,)).fetchone()
    if row is None:
        raise RuntimeError(f"no client credentials in {db} for {ns}{login}")
    data = json.loads(row[0])
    auth = ConfidentialAppAuthClient(data["client_id"], data["client_secret"],
                                     app_name="AgentLab")
    return TransferClient(app_name="AgentLab", authorizer=RefreshTokenAuthorizer(
        td.refresh_token, auth, access_token=td.access_token,
        expires_at=td.expires_at_seconds))


def _client():
    """The shared client, or None with the reason in _CLIENT_ERROR.

    Built at first use rather than at startup: a network call per run start would slow
    every campaign, including those that never transfer anything.
    """
    global _CLIENT, _CLIENT_ERROR
    if _CLIENT is None and _CLIENT_ERROR is None:
        try:
            _CLIENT = _build_client()
        except Exception as exc:
            _CLIENT_ERROR = (str(exc) if isinstance(exc, RuntimeError)
                             else f"{type(exc).__name__}: {exc}")
    return _CLIENT


def _fault(exc):
    """A Globus exception as something the agent can act on.

    The structured fields say what the service wants, which the message text alone does
    not: a missing consent names the scope to grant, and a lapsed session names the
    identity domain to refresh.
    """
    info = getattr(exc, "info", None)
    consent = getattr(info, "consent_required", None)
    if consent:
        scopes = " ".join(getattr(consent, "required_scopes", None) or [])
        return ("this collection needs a one-off consent.\n"
                f"  run: globus session consent '{scopes}'")
    params = getattr(info, "authorization_parameters", None)
    if params:
        domains = getattr(params, "session_required_single_domain", None) or []
        if domains:
            return ("the Globus session has lapsed for this collection.\n"
                    f"  run: globus session update {domains[0]}")
        identities = getattr(params, "session_required_identities", None) or []
        if identities:
            return ("the Globus session has lapsed for this collection.\n"
                    f"  run: globus session update {identities[0]}")
        message = getattr(params, "session_message", None)
        if message:
            return f"{message}\n  run: globus session update"
    code = getattr(exc, "code", "") or ""
    detail = getattr(exc, "message", None) or str(exc)
    return f"{code}: {detail}" if code else detail


def preflight():
    """Check the configured collections answer, before a run that depends on them.

    Only meaningful when Transfer is configured -- CFG is None otherwise and the tool is
    not offered, so there is nothing to check. Returns a list of problems, empty when
    the path is usable.

    A local collection is most often a Globus Connect Personal that is simply not
    running, which `ls` reports rather than the client build: building a client from
    stored tokens succeeds while the collection is unreachable, and the first transfer
    of the run is then what discovers it.

    The remote side is checked at the write root, not at "/": a compute system's
    collection is rooted where every project on the machine is visible, and listing
    that is both slow and more than the check needs.
    """
    if CFG is None:
        return []
    tc = _client()
    if tc is None:
        return [f"Globus Transfer is configured but not usable: {_CLIENT_ERROR}"]
    problems = []
    for role, probe in (("local_collection", "/"),
                        ("remote_collection", _cpath(CFG["remote_write_root"] or "/"))):
        coll = CFG[role]
        try:
            tc.operation_ls(coll, path=probe)
        except Exception as exc:
            hint = ("\n    If this is Globus Connect Personal, start it: "
                    "globusconnectpersonal -start &"
                    if role == "local_collection" else
                    "\n    Check the collection id and that any required consent is granted.")
            problems.append(f"Globus {role.split('_')[0]} collection {coll} is not "
                            f"reachable at {probe}: {_fault(exc)}{hint}")
    return problems


def _err(msg):
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _ok(msg):
    return {"content": [{"type": "text", "text": msg}]}


def _local_dest(rel, roots, must_exist=False):
    """Resolve a requested path against the allowed local roots.

    An absolute path is accepted only if it already sits inside one of them -- checked
    because os.path.join discards its first argument when the second is absolute, so an
    absolute path would otherwise escape silently.

    A relative path is ambiguous when there is more than one root, so for a source
    (must_exist) the roots are tried in turn and the first that resolves to something
    on disk wins; for a destination the first root is used.
    """
    if os.path.isabs(rel):
        dest = os.path.realpath(rel)
        for root in roots:
            r = os.path.realpath(root)
            if dest == r or dest.startswith(r + os.sep):
                return dest
        return None
    for root in roots:
        cand = os.path.realpath(os.path.join(root, rel))
        r = os.path.realpath(root)
        if not (cand == r or cand.startswith(r + os.sep)):
            continue
        if not must_exist or os.path.exists(cand):
            return cand
    return None


def _cpath(posix_path):
    """POSIX path -> the path this collection understands."""
    p = os.path.normpath(posix_path)
    for root in (CFG.get("collection_root") or []):
        if p == root:
            return "/"
        if p.startswith(root + "/"):
            return p[len(root):]
    # Already collection-relative, or outside the collection: pass it through and let
    # Globus reject it, rather than silently rewriting into the wrong place.
    return posix_path


def _stat(tc, coll, path):
    """(type, size) for a collection path, or (None, None) when it is not there.

    One request answers both questions a get has: whether the path is there at all, and
    whether it is a directory. A missing path is answered here rather than becoming a
    transfer, because Globus retries FILE_NOT_FOUND as though it were transient and the
    get would otherwise hang for the full wait instead of saying so at once.
    """
    from globus_sdk import TransferAPIError
    try:
        r = tc.operation_stat(coll, path)
    except TransferAPIError as exc:
        if exc.http_status == 404:
            return None, None
        raise
    return r.get("type"), r.get("size")


def _under(path, root):
    """True when path is inside root. Both are collection paths, so plain prefix
    comparison after normalisation is the right test."""
    if not root:
        return False
    p = os.path.normpath(path)
    r = os.path.normpath(root)
    return p == r or p.startswith(r + "/")


# Faults Globus keeps retrying that will never clear on their own. Waiting out the full
# timeout on one of these turns a wrong path into a ten-minute stall.
_FATAL = {"FILE_NOT_FOUND", "PERMISSION_DENIED", "ENDPOINT_NOT_FOUND", "NO_CREDENTIALS",
          "AUTHENTICATION_FAILED", "SUBJECT_MISMATCH", "QUOTA_EXCEEDED"}


def _submit(tc, src_coll, dst_coll, items, label):
    """One task carrying every (src, dst, recursive) in `items`. Globus bills a round
    trip per task, so batching n paths into one is n times cheaper in latency."""
    from globus_sdk import TransferData
    data = TransferData(source_endpoint=src_coll, destination_endpoint=dst_coll,
                        label=label, notify_on_succeeded=False, notify_on_failed=False,
                        notify_on_inactive=False)
    for src, dst, recursive in items:
        data.add_item(src, dst, recursive=recursive)
    return tc.submit_transfer(data)["task_id"]


def _requested(args):
    """[(path, local_path)] from either the single pair or the `items` list."""
    items = args.get("items")
    if isinstance(items, list) and items:
        return [(str(i.get("path", "")).strip(), str(i.get("local_path", "")).strip())
                for i in items if isinstance(i, dict)]
    return [(str(args.get("path", "")).strip(), str(args.get("local_path", "")).strip())]


def _wait(tc, task_id):
    """Poll until the task settles, giving up early on a fault retrying cannot fix."""
    deadline = time.time() + _WAIT_SECONDS
    while True:
        task = tc.get_task(task_id)
        status = task.get("status") or ""
        if status == "SUCCEEDED":
            return True, task_id
        nice = task.get("nice_status") or ""
        if status == "FAILED":
            detail = nice or task.get("fatal_error") or "no detail"
            return False, f"transfer {task_id} failed: {detail}"
        if nice in _FATAL:
            try:
                tc.cancel_task(task_id)
            except Exception:
                pass
            return False, (f"transfer {task_id} cancelled after {nice}: retrying cannot "
                           "fix this. Check the path exists and is readable.")
        if time.time() >= deadline:
            return False, (f"transfer {task_id} did not complete within {_WAIT_SECONDS}s"
                           + (f" (last status {nice})" if nice else ""))
        time.sleep(_POLL_SECONDS)


TRANSFER_DESC = """
Move files between this machine and the compute system with Globus Transfer, and read
files on the compute system that are otherwise unreachable from here -- job logs above
all.

Operations (`op`):

  ls     list a directory on the compute system.
           path        directory to list

  get    copy a file or directory from the compute system and return what it contains.
           path        file or directory on the compute system
           local_path  where to put it, relative to the workspace (optional; defaults
                       to scratch/transfers/<basename>)

  put    copy a local file or directory to the compute system. Confined to the
         configured remote_write_root, which defaults to the campaign's work_dir.
           local_path  path on this machine, relative to the campaign directory, or
                       absolute and inside the campaign or workspace directory
           path        destination on the compute system

Several paths go in one task through `items`, a list of {local_path, path} used in place
of the single pair. One task costs the same whether it carries one path or twenty, so
staging four trial directories as one call takes about as long as staging one:

    transfer(op="put", items=[{"local_path": "libe_scripts", "path": "<work_dir>/t30"},
                              {"local_path": "libe_scripts", "path": "<work_dir>/t31"}])

`get` takes the same list. Stage everything a cycle needs in a single call.

A directory is moved whole, in one task -- send a set of scripts by naming the directory
that holds them. One task for the directory costs far less than one task per file, so
prefer naming the directory over transferring its files one at a time.

`get` on a file saves it whole and returns its path, with the inline text truncated head
and tail for anything large -- grep the saved copy rather than asking for more. `get` on
a directory reports how many files arrived.

Transfers are waited on, so a result means the bytes have landed. A file already on a
filesystem this machine can see needs no transfer -- read it directly.
"""

# JSON Schema, so `op` alone is required and `items` can be a list.
TRANSFER_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["ls", "get", "put"]},
        "path": {"type": "string"},
        "local_path": {"type": "string"},
        "items": {"type": "array", "items": {
            "type": "object",
            "properties": {"path": {"type": "string"},
                           "local_path": {"type": "string"}},
            "required": ["path"]}},
    },
    "required": ["op"],
}


@tool("transfer", TRANSFER_DESC, TRANSFER_SCHEMA)
async def transfer(args):
    if CFG is None:
        return _err("Globus Transfer is not configured for this user/system.")
    tc = _client()
    if tc is None:
        return _err(f"Globus Transfer is not usable: {_CLIENT_ERROR}")

    op = str(args.get("op", "")).strip()
    path = str(args.get("path", "")).strip()
    local_path = str(args.get("local_path", "")).strip()
    rc_coll = CFG["remote_collection"]
    lc_coll = CFG["local_collection"]

    if op == "ls":
        if not path:
            return _err("ls needs `path`.")
        try:
            listing = tc.operation_ls(rc_coll, path=_cpath(path))
        except Exception as exc:
            return _err(f"ls failed: {_fault(exc)}")
        names = [f"{e['name']}/" if e["type"] == "dir" else e["name"] for e in listing]
        return _ok("\n".join(names) if names else "(empty directory)")

    if op == "get":
        want = [(pa, lp) for pa, lp in _requested(args) if pa]
        if not want:
            return _err("get needs `path`, or `items` with a path in each entry.")
        fetch, dests = [], []
        for path, local_path in want:
            if CFG["remote_read_root"] and not _under(path, CFG["remote_read_root"]):
                return _err(f"refusing to read outside {CFG['remote_read_root']}: {path}")
            rel = local_path or os.path.join("scratch", "transfers",
                                             os.path.basename(path.rstrip("/")))
            dest = _local_dest(rel, CFG["local_roots"])
            if dest is None:
                return _err("refusing to write outside the campaign and workspace "
                            f"directories: {local_path}")
            try:
                kind, _size = _stat(tc, rc_coll, _cpath(path))
            except Exception as exc:
                return _err(f"could not check {path}: {_fault(exc)}")
            if kind is None:
                return _err(f"no such path on the compute system: {path}")
            recursive = kind == "dir"
            os.makedirs(dest if recursive else os.path.dirname(dest), exist_ok=True)
            fetch.append((_cpath(path), dest, recursive))
            dests.append((path, dest, recursive))
        try:
            task_id = _submit(tc, rc_coll, lc_coll, fetch, "agentlab-get")
        except Exception as exc:
            return _err(f"transfer submit failed: {_fault(exc)}\n"
                        "If the local collection is not connected, start Globus Connect "
                        "Personal. If the destination is refused, the workspace path is "
                        "not writable in ~/.globusonline/lta/config-paths.")
        done, msg = _wait(tc, task_id)
        if not done:
            return _err(msg)
        missing = [d for _, d, _ in dests if not os.path.exists(d)]
        if missing:
            return _err("transfer reported success but these are not there: "
                        + ", ".join(missing))

        if len(dests) > 1:
            lines = [f"{len(dests)} paths in one task ({task_id})"]
            for path, dest, recursive in dests:
                n = (sum(len(f) for _, _, f in os.walk(dest)) if recursive else 1)
                lines.append(f"  {path}\n    -> {dest}  ({n} file{'s' if n != 1 else ''})")
            return _ok("\n".join(lines))

        path, dest, recursive = dests[0]
        if recursive:
            n = sum(len(f) for _, _, f in os.walk(dest))
            return _ok(f"{path}\n  -> {dest}  ({n} files)")

        size = os.path.getsize(dest)
        with open(dest, "rb") as f:
            head = f.read(_HEAD_BYTES)
            if size > _HEAD_BYTES + _TAIL_BYTES:
                f.seek(-_TAIL_BYTES, os.SEEK_END)
                tail = f.read()
                body = (head.decode("utf-8", "replace")
                        + f"\n\n... [{size - _HEAD_BYTES - _TAIL_BYTES} bytes omitted;"
                          f" full file at {dest}] ...\n\n"
                        + tail.decode("utf-8", "replace"))
            else:
                body = (head + f.read()).decode("utf-8", "replace")
        return _ok(f"{path}\n  -> {dest} ({size} bytes)\n\n{body}")

    if op == "put":
        want = [(pa, lp) for pa, lp in _requested(args) if pa or lp]
        if not want or any(not pa or not lp for pa, lp in want):
            return _err("put needs both `local_path` and `path` in every entry.")
        send, sent = [], []
        for path, local_path in want:
            if not _under(path, CFG["remote_write_root"]):
                return _err(f"refusing to write outside {CFG['remote_write_root']}: "
                            f"{path}\nWiden remote_write_root in your user file if that "
                            "is intended.")
            src = _local_dest(local_path, CFG["local_roots"], must_exist=True)
            if src is None:
                return _err("refusing to send from outside the campaign and workspace "
                            f"directories: {local_path}")
            if not os.path.exists(src):
                return _err(f"no such local path: {src}")
            recursive = os.path.isdir(src)
            send.append((src, _cpath(path), recursive))
            sent.append((src, path, recursive))
        try:
            task_id = _submit(tc, lc_coll, rc_coll, send, "agentlab-put")
        except Exception as exc:
            return _err(f"transfer submit failed: {_fault(exc)}")
        done, msg = _wait(tc, task_id)
        if not done:
            return _err(msg)

        def _count(src, recursive):
            return sum(len(f) for _, _, f in os.walk(src)) if recursive else 1

        if len(sent) > 1:
            lines = [f"{len(sent)} paths in one task ({task_id})"]
            for src, path, recursive in sent:
                n = _count(src, recursive)
                lines.append(f"  {src}\n    -> {path}  ({n} file{'s' if n != 1 else ''})")
            return _ok("\n".join(lines))
        src, path, recursive = sent[0]
        if recursive:
            return _ok(f"{src}\n  -> {path}  ({_count(src, True)} files, task {task_id})")
        return _ok(f"{src}\n  -> {path} ({os.path.getsize(src)} bytes, task {task_id})")

    return _err(f"unknown op {op!r}; use ls, get or put.")
