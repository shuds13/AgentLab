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
import subprocess

from claude_agent_sdk import tool

# Bounded so a huge log cannot flood the agent's context. The file is always saved in
# full; these limits apply only to what is returned inline.
_HEAD_BYTES = 4000
_TAIL_BYTES = 12000
_WAIT_SECONDS = 600


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
        "remote_write_root": str(
            g.get("remote_write_root") or user_cfg.get("work_dir", "")
        ).rstrip("/"),
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
        "collection_root": [
            str(r).rstrip("/")
            for r in _as_list(
                g.get("collection_root")
                if g.get("collection_root") is not None
                else sys_cfg.get("globus_collection_root", "")
            )
            if str(r).strip()
        ],
        "workspace_dir": workspace_dir,
        # The agent may send from, and fetch into, either the campaign's own directory
        # (task.py and the scripts a job runs) or its workspace (results and artefacts).
        # Scripts it revises between jobs live in the former, so bounding to the
        # workspace alone would rule out the main reason to have this tool.
        "local_roots": [d for d in (workspace_dir, campaign_dir) if d],
    }


CFG = None  # set by tools.py at import; None disables the tools


def _globus(*argv, timeout=_WAIT_SECONDS):
    """Run the globus CLI. It already holds the user's login, so this module never
    implements an auth flow of its own."""
    try:
        p = subprocess.run(
            ["globus", *argv], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, "", "the 'globus' CLI is not on PATH (pip install globus-cli)"
    except subprocess.TimeoutExpired:
        return -1, "", f"globus {' '.join(argv)} timed out after {timeout}s"
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _authenticated():
    """Checked at use rather than at startup: a network call per run start would slow
    every campaign, including those that never transfer anything."""
    rc, out, err = _globus("whoami", timeout=30)
    if rc != 0:
        return False, (err or out or "not logged in") + "\n  run: globus login"
    return True, out


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
    for root in CFG.get("collection_root") or []:
        if p == root:
            return "/"
        if p.startswith(root + "/"):
            return p[len(root) :]
    # Already collection-relative, or outside the collection: pass it through and let
    # Globus reject it, rather than silently rewriting into the wrong place.
    return posix_path


def _remote_is_dir(coll, path):
    """globus ls succeeds on a directory and fails on a file, which is the cheapest
    way to decide whether a get needs --recursive."""
    rc, _, _ = _globus("ls", f"{coll}:{path}", timeout=120)
    return rc == 0


def _under(path, root):
    """True when path is inside root. Both are collection paths, so plain prefix
    comparison after normalisation is the right test."""
    if not root:
        return False
    p = os.path.normpath(path)
    r = os.path.normpath(root)
    return p == r or p.startswith(r + "/")


def _wait(task_id):
    rc, out, err = _globus(
        "task",
        "wait",
        task_id,
        "--timeout",
        str(_WAIT_SECONDS),
        "--polling-interval",
        "2",
    )
    if rc != 0:
        return False, f"transfer {task_id} did not complete: {err or out}"
    return True, task_id


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

A directory is moved whole, in one task -- send a set of scripts by naming the directory
that holds them.

`get` on a file saves it whole and returns its path, with the inline text truncated head
and tail for anything large -- grep the saved copy rather than asking for more. `get` on
a directory reports how many files arrived.

Transfers are waited on, so a result means the bytes have landed. A file already on a
filesystem this machine can see needs no transfer -- read it directly.
"""

TRANSFER_SCHEMA = {"op": str, "path": str, "local_path": str}


@tool("transfer", TRANSFER_DESC, TRANSFER_SCHEMA)
async def transfer(args):
    if CFG is None:
        return _err("Globus Transfer is not configured for this user/system.")
    ok, who = _authenticated()
    if not ok:
        return _err(f"Globus Transfer is not usable: {who}")

    op = str(args.get("op", "")).strip()
    path = str(args.get("path", "")).strip()
    local_path = str(args.get("local_path", "")).strip()
    rc_coll = CFG["remote_collection"]
    lc_coll = CFG["local_collection"]

    if op == "ls":
        if not path:
            return _err("ls needs `path`.")
        rc, out, err = _globus("ls", f"{rc_coll}:{_cpath(path)}", timeout=120)
        if rc != 0:
            return _err(f"ls failed: {err or out}")
        return _ok(out or "(empty directory)")

    if op == "get":
        if not path:
            return _err("get needs `path`.")
        # Straight to where it belongs: GCP must be able to write the workspace anyway,
        # and a staging hop only adds a second place for permissions to be wrong.
        if CFG["remote_read_root"] and not _under(path, CFG["remote_read_root"]):
            return _err(f"refusing to read outside {CFG['remote_read_root']}: {path}")
        rel = local_path or os.path.join(
            "scratch", "transfers", os.path.basename(path.rstrip("/"))
        )
        dest = _local_dest(rel, CFG["local_roots"])
        if dest is None:
            return _err(
                "refusing to write outside the campaign and workspace "
                f"directories: {local_path}"
            )
        recursive = _remote_is_dir(rc_coll, _cpath(path))
        os.makedirs(dest if recursive else os.path.dirname(dest), exist_ok=True)
        cmd = [
            "transfer",
            f"{rc_coll}:{_cpath(path)}",
            f"{lc_coll}:{dest}",
            "--label",
            "agentlab-get",
            "--notify",
            "off",
            "--format",
            "json",
        ]
        if recursive:
            cmd.insert(1, "--recursive")
        rc, out, err = _globus(*cmd, timeout=120)
        if rc != 0:
            return _err(
                f"transfer submit failed: {err or out}\n"
                "If the local collection is not connected, start Globus Connect "
                "Personal. If the destination is refused, the workspace path is "
                "not writable in ~/.globusonline/lta/config-paths."
            )
        try:
            task_id = json.loads(out)["task_id"]
        except Exception:
            return _err(f"could not read task id from: {out}")
        done, msg = _wait(task_id)
        if not done:
            return _err(msg)
        if not os.path.exists(dest):
            return _err(f"transfer reported success but {dest} is not there.")
        if recursive:
            n = sum(len(f) for _, _, f in os.walk(dest))
            return _ok(f"{path}\n  -> {dest}  ({n} files)")

        size = os.path.getsize(dest)
        with open(dest, "rb") as f:
            head = f.read(_HEAD_BYTES)
            if size > _HEAD_BYTES + _TAIL_BYTES:
                f.seek(-_TAIL_BYTES, os.SEEK_END)
                tail = f.read()
                body = (
                    head.decode("utf-8", "replace")
                    + f"\n\n... [{size - _HEAD_BYTES - _TAIL_BYTES} bytes omitted;"
                    f" full file at {dest}] ...\n\n" + tail.decode("utf-8", "replace")
                )
            else:
                body = (head + f.read()).decode("utf-8", "replace")
        return _ok(f"{path}\n  -> {dest} ({size} bytes)\n\n{body}")

    if op == "put":
        if not path or not local_path:
            return _err("put needs both `local_path` and `path`.")
        if not _under(path, CFG["remote_write_root"]):
            return _err(
                f"refusing to write outside {CFG['remote_write_root']}: {path}\n"
                "Widen remote_write_root in your user file if that is intended."
            )
        src = _local_dest(local_path, CFG["local_roots"], must_exist=True)
        if src is None:
            return _err(
                "refusing to send from outside the campaign and workspace "
                f"directories: {local_path}"
            )
        if not os.path.exists(src):
            return _err(f"no such local path: {src}")
        recursive = os.path.isdir(src)
        cmd = [
            "transfer",
            f"{lc_coll}:{src}",
            f"{rc_coll}:{_cpath(path)}",
            "--label",
            "agentlab-put",
            "--notify",
            "off",
            "--format",
            "json",
        ]
        if recursive:
            cmd.insert(1, "--recursive")
        rc, out, err = _globus(*cmd, timeout=120)
        if rc != 0:
            return _err(f"transfer submit failed: {err or out}")
        try:
            task_id = json.loads(out)["task_id"]
        except Exception:
            return _err(f"could not read task id from: {out}")
        done, msg = _wait(task_id)
        if not done:
            return _err(msg)
        if recursive:
            n = sum(len(f) for _, _, f in os.walk(src))
            return _ok(f"{src}\n  -> {path}  ({n} files, task {task_id})")
        return _ok(f"{src}\n  -> {path} ({os.path.getsize(src)} bytes, task {task_id})")

    return _err(f"unknown op {op!r}; use ls, get or put.")
