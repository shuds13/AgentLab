# Globus Transfer (optional)

Only needed if the machine running the agent and the compute system do **not** see the
same filesystem, or if you want to publish a workspace to collaborators. If everything is
on one filesystem, skip this — the agent never touches it.

Two things people confuse, so worth stating once:

- **Globus Compute** runs your function on a remote machine. That is how a campaign
  submits work, and it is required.
- **Globus Transfer** moves files between machines. That is this document, and it is
  optional.

## Make the agent's machine a Globus collection

Only needed for a machine that is not already a registered Globus endpoint — a
workstation or a small VM. HPC systems usually have one already; check the Globus web app
first and skip this if so.

```
cd ~                      # keep it out of the repo
wget https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz
tar xzf globusconnectpersonal-latest.tgz && cd globusconnectpersonal-*/
./globusconnectpersonal -setup --no-gui
```

Headless, it prints a URL: open it, log in, paste the code back, then give the collection
a display name. Setup prints the collection ID — that is what goes in `sync_shared.sh`.
You can find it again under Collections in the Globus web app.

Expose the directory you want to sync by adding it to `~/.globusonline/lta/config-paths`,
keeping the existing `$HOME,0,1` line:

```
/path/to/your/workspace/,0,1
```

The format is `<path>,<sharing 0|1>,<read-write 0|1>`.

Then start it, and leave it running — transfers need it up:

```
./globusconnectpersonal -start &
```

## Log in to the CLI

```
pip install globus-cli
globus login
globus endpoint search --filter-scope my-endpoints    # confirm your collection ID
```

## Configure the script

Edit `bin/sync_shared.sh` and fill in the collection IDs and paths; the values
shipped are placeholders. **Globus paths are relative to the collection root**, not POSIX
paths, which catches people out on HPC collections whose root is something like
`/lus/flare/projects`.

Then:

```
bin/sync_shared.sh          # publish the workspace to the mirror
bin/sync_shared.sh -n       # do not wait for the push; leave it running on Globus
```

It is a standalone script. Run it by hand or from cron; no campaign calls it or depends
on it.

Interrupting the script does not stop a transfer — use `globus task cancel <id>`, and
`globus task list --limit 5` to see recent ones.

A pull step is included but commented out, since what to copy back is specific to what
you are doing. Only pull paths no local agent writes: `--sync-level mtime` also
overwrites on a size mismatch, so a newer local file is not protected.

## What gets synced

The push is recursive over the whole workspace directory, so anything a run writes under
it — `results.jsonl`, the journal and logbook, and `runs/<run_id>/` with its metadata and
prompt snapshots — is mirrored. Keep new run artefacts inside the workspace and they need
no transfer step of their own.

## As an agent tool

Configured, the agent gets a `transfer` tool with three operations: `ls` a directory on
the compute system, `get` a file from it, and `put` a local file onto it.

It is how files other than the job function reach the compute system, and how they come
back. The pattern it exists for is iteration: the agent keeps scripts and inputs here,
revises them, pushes them, runs a job against them, reads what came out, revises again.
Anything the agent expects to change between jobs is better as a file it can push than
as something folded into the Globus Compute function. Reading job logs and staging
results elsewhere are the same operation.

Turn it on by adding a `globus` block to `users/<you>/<system>.json`:

```json
"globus": {
  "remote_collection": "<uuid of the compute system's collection>",
  "local_collection":  "<uuid of this machine's collection>",
  "remote_write_root": "/path/the/agent/may/write/under",
  "remote_read_root":  "/path/the/agent/may/read/under"
}
```

Only the two collection IDs are required. Without the block the tool is not offered, so
an installation that does not use Transfer sees no sign of it. Authentication is checked
when the tool is first used rather than at start-up, and an expired login comes back as
the `globus login` command to run.

`bin/setup_globus.sh` fills this in for you, and `--check` reports the current state
without changing anything.

### What the agent may touch

Three bounds, all of them plain path checks made before anything is submitted. Change
them by editing the `globus` block; they take effect on the next run.

| | set by | default | what it bounds |
|---|---|---|---|
| remote write | `remote_write_root` | your `work_dir` | where `put` may write on the compute system |
| remote read | `remote_read_root` | unset -- anywhere you can read | where `get` may read from |
| local | not configurable | the campaign workspace | `get` writes only inside it; `put` sends only from inside it |

Reads are unbounded by default because the common job is fetching a log from a path the
campaign did not choose. Set `remote_read_root` if you would rather confine it -- your
`work_dir` is the natural value, since that is where job logs are written.

These are guardrails against a wrong path, not a security boundary. The real boundary is
the filesystem: Globus acts as you, so you can reach exactly what your account on the
compute system can reach, and nothing else -- other people's projects are refused
whatever these settings say.

There is no delete operation.

### Collections are not always rooted at `/`

Paths everywhere in this tool are POSIX paths -- the same ones a job sees. Some
collections agree: NERSC's expose `/global/cfs/...` unchanged. ALCF's do not -- their
`/` is the filesystem's projects directory, so POSIX `/lus/eagle/projects/foo/bar` is
`/foo/bar` to the collection.

`globus_collection_root` in `systems/<system>.json` records that prefix, and the tool
strips it on the way out, so `work_dir` and a job's `log` path can be used as written.
Empty means the collection uses absolute POSIX paths.

| system | collection | root |
|---|---|---|
| perlmutter | NERSC DTN | *(none -- absolute POSIX)* |
| polaris | alcf#dtn_eagle | `/lus/eagle/projects` |
| aurora | ALCF Flare | `/lus/flare/projects` |

A user can override it with `collection_root` in their own `globus` block. Getting it
wrong is visible rather than silent: paths outside the declared root are passed through
untouched and Globus rejects them.

### Where job logs are

A job result reports a `log` path on the compute filesystem, under the campaign's
`work_dir` -- e.g. `/global/cfs/cdirs/<project>/<you>/<campaign>/<tag>.log`. That is what
to hand to `get`. Because `work_dir` is also the default `remote_write_root`, the default
setup can already read its own logs.

Two failure modes worth recognising:

- *"requires you to grant consent"* — the collection needs a one-off consent. The error
  quotes the exact `globus session consent ...` command; run it and retry.
- *"the local collection is not connected"* — Globus Connect Personal is not running on
  this machine. Start it: `globusconnectpersonal -start &`.

Transfers are waited on, so a successful result means the bytes have landed.

### What a transfer does not carry

Globus Transfer copies file contents and, with `--preserve-timestamp`, modification
times. It does not carry:

- **Execute permission.** A script or binary arrives without its execute bit, so running
  it fails with "Permission denied". Run scripts through their interpreter (`bash
  run.sh`, `python x.py`), or have the job set the bit before it runs them.
- **Symbolic links.** They are not copied as links. A software install that relies on
  them, such as a Julia release (`libjulia.so.1.12` is a link), is broken by a copy.
  Install such software on the compute system from its own download instead -- for
  example, a job that fetches and unpacks the release tarball there.

The CLI has no option for either.
