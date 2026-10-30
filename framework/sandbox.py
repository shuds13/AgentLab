"""The box an agent's Claude Code runs in when SANDBOX=bwrap.

The box starts empty and holds only what is put in it: the system's own directories and
the run's software, read-only; the workspace and the agent's Claude config, writable; a
home and /tmp of its own that vanish with it. Nothing else exists inside, so there is no
list of things to hide. Inside, the agent sees only the processes it started. The
network is the machine's.

A campaign that needs more -- software installed elsewhere, data, another workspace --
names it in campaign.json:

    "sandbox": {"read": ["/path/to/julia", "~/data"], "write": ["/scratch/me/out"]}
"""
import os
import shlex
import shutil
import sys

# The system's own directories. Each is mounted as it is on the host: a symlink as the
# same symlink, a directory read-only.
SYSTEM_DIRS = ["/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc", "/sys"]


def claude_cli():
    """The Claude Code CLI the SDK would start: its bundled copy, else `claude` on PATH."""
    import claude_agent_sdk
    bundled = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude")
    return bundled if os.path.isfile(bundled) else shutil.which("claude")


def _expand(paths):
    return [os.path.abspath(os.path.expanduser(p)) for p in paths or []]


def missing(extra):
    """The paths a campaign's `sandbox` names that do not exist."""
    return [p for p in _expand(extra.get("read")) + _expand(extra.get("write"))
            if not os.path.exists(p)]


def _mount(args, flag, path):
    """Mount a path at its own name and, where that is a symlink, at its real name too,
    so either spelling works inside."""
    for p in dict.fromkeys([path, os.path.realpath(path)]):
        args += [flag, os.path.realpath(path), p]


def command(read, write, cwd):
    """The bwrap command that runs what follows it inside the box."""
    home = os.path.expanduser("~")
    args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid",
            "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", home]
    for d in SYSTEM_DIRS:
        if os.path.islink(d):
            args += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            args += ["--ro-bind", d, d]
    # Name resolution where /etc/resolv.conf points outside /etc, as with systemd.
    resolv = os.path.realpath("/etc/resolv.conf")
    if not resolv.startswith("/etc/") and os.path.exists(resolv):
        args += ["--ro-bind", resolv, resolv]
    for p in read:
        if os.path.exists(p):
            _mount(args, "--ro-bind", p)
    for p in write:
        if os.path.exists(p):
            _mount(args, "--bind", p)
    return args + ["--chdir", cwd]


def write_box(run_dir, read, write, cwd):
    """Write the box to the run directory: `sandbox.sh` runs any command in it, and
    `agent_cli.sh` runs the Claude Code CLI in it. Returns the latter, for the SDK's
    cli_path."""
    cli = claude_cli()
    if not cli:
        raise RuntimeError("SANDBOX=bwrap: no Claude Code CLI found")
    read = _expand(read) + [os.path.dirname(os.path.realpath(cli))]
    box = os.path.join(run_dir, "sandbox.sh")
    agent_cli = os.path.join(run_dir, "agent_cli.sh")
    os.makedirs(run_dir, exist_ok=True)
    for path, cmd in ((box, " ".join(shlex.quote(a) for a in command(read, _expand(write), cwd))),
                      (agent_cli, f"{shlex.quote(box)} {shlex.quote(cli)}")):
        with open(path, "w") as f:
            f.write(f'#!/bin/bash\nexec {cmd} "$@"\n')
        os.chmod(path, 0o755)
    return agent_cli


def python_dirs():
    """The Python this framework runs in, and the one it was made from if a venv."""
    return list(dict.fromkeys([sys.prefix, sys.base_prefix]))
