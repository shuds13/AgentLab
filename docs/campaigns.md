# Writing and maintaining a campaign

Guidance, not rules. It describes what has worked and what tends to go wrong; the person
whose campaign it is decides. If they want a specific result, a date or a version in
`prompt.md`, put it there — mention the cost once if it is worth mentioning, and follow
what they ask.

Two situations, and they differ:

- **Creating** a campaign — writing the files for the first time. `AGENTS.md` covers the
  steps; the sections below say what belongs in each file.
- **Editing** an existing one — setting up a later run, or changing something. Here the
  files already reflect decisions someone made. Change what the person asks for and leave
  the rest, even where it differs from what is below. A campaign that has been run is a
  record as much as a configuration.

## What a campaign is

A standing piece of work: a goal, an instrument, and a record that outlives any one run.
Some campaigns run once. Many are run again — to extend a result, to follow a lead the
last run left open, or because something the work depends on has changed.

Where a campaign will be run again, the aim is that a later run needs no edit to anything
but `user_prompt.md`. The test for anything you write: **will this still be true next
time it runs?** If not, it belongs in `user_prompt.md` or the workspace.

## Length

Campaign files are read on every run, so what is in them costs something each time. In
what you write, say a thing once, in as few words as carry it.

Text the person gave you — a prompt, a specification, a procedure — goes in as it stands.

`user_prompt.md` matters most, often being rewritten each run: keep it as small as expresses
what the run is for.

## What goes where

| file | changes | holds |
|---|---|---|
| `prompt.md` | rarely | the campaign's standing objective, and what holds across every run |
| `method.md` | rarely | how the agent works — cycles, records |
| `campaign.json` | when the system or resources change | which system, and the parameters a job needs |
| `task.py` | when the instrument changes | what one job runs and returns |
| `user_prompt.md` | every run | this run's aim toward that objective, and any note for this run |
| workspace files | written each run | the agent's records |

If a rerun needs an edit outside `user_prompt.md`, that is usually a sign something
durable was written in the wrong place.

## Resources

`campaign.json` gives either `resources` (one shape) or `buckets` (named shapes, the
first being the default). A task routes to one with `bucket_for(args)`.

The keys are passed to the endpoint as its configuration, so they must be names that
endpoint's template accepts. A facility multi-user endpoint validates them and rejects
anything it does not declare; `systems/endpoints/README.md` lists the common ones.

Five decide how much runs at once:

- `max_workers_per_node` — tasks sharing one node. Raise it when a task uses only part
  of a node; at 1 a second task waits for a second node. **It only spreads tasks within
  one node.** With `SimpleLauncher`, which the templates use, Parsl starts one worker
  pool on the block's first node, so with `nodes_per_block` above 1 the other nodes get
  no workers and raising this buys nothing — the task is expected to span the allocation
  itself, with mpiexec. More nodes per task means `nodes_per_block`; more tasks at once
  on one node means this; more tasks at once on separate nodes means `max_blocks`.
- `nodes_per_job` — the nodes one job gets: `nodes_per_block` by default, or 1 for a job
  on every node of the block at once. At 1 the framework sends
  `launcher_type: MpiExecLauncher` and `max_workers_per_node: 1`, so the endpoint's
  template must accept `launcher_type` (ALCF's Polaris endpoint and
  `systems/endpoints/polaris_pbs` do). The framework's own key; not sent to the endpoint.
- `max_blocks` — batch jobs this bucket may have outstanding.
- `parallelism` — 1 by default, as on facility endpoints: a multi-node bucket runs its
  tasks one after another in the same block, which keeps the block busy. Set it to
  `nodes_per_block` for a block per task, up to `max_blocks`, when long tasks should run
  at once, each with a full walltime. `systems/endpoints/README.md` explains why.
- `max_concurrent` — tasks the agent may have in flight for this bucket. Agent-side,
  unlike the other three.

`node_hours` (experimental), beside `buckets`, gives node-hours per system for a whole
launch, the top run and its workers together, e.g. `{"polaris": 48}`; `MAX_NODE_HOURS`
overrides it for one launch. Each block counts its nodes from its start to its last
task's end, plus the time the endpoint holds it idle afterwards (`max_idletime`), or to
its walltime if that comes first. Once a system's budget is used, submits to it are
refused; tasks already sent still run.

Runs in a workspace group that use the same bucket share its blocks, so a launch's own
count would charge a shared block in full to each of them. A resource session gives the
group one budget instead:

```
bin/resource_session.sh <group> start swing=4      # node-hours per system
bin/resource_session.sh <group>                    # what the group has used
bin/resource_session.sh <group> stop
```

It counts every block any run in the group used, each block once, from the session's
start until it is stopped or the next session starts. On a system it budgets it replaces
each launch's `node_hours`: the group's runs are refused new jobs there once it is used,
and the agents and the watch show the group's figure. A script that starts a group's runs
can start a session as it does and leave it open, since the next session closes it. It is
kept in `<group>/resource_session.json`. A block that a run outside the group also used
is counted in full to the group.

A bucket's tasks can be redirected to another bucket while the runs go, for every run
in the workspace from its next submit, when both give a job the same number of nodes:

```
bin/route.sh <workspace> debug=debug_scaling      # send debug's tasks to debug_scaling
bin/route.sh <workspace> debug=                   # stop redirecting debug
```

A submit that was redirected says so in its result. The redirects are kept in
`<workspace>/routing.json`, and each change in `routing_log.jsonl`.

A resource agent makes the same change with its `set_route` tool. With
`RESOURCE_AGENT=true` in `run.sh`, the framework starts one as the run's first worker:
it shows in the agents panel as `resources` and stops with the run. It places the work
of the other agents rather than doing any, so it gets `system_status` and `set_route`
and no submit tools. It follows `method_resources.md` in the campaign if there is one,
else `methods/resources.md`, and keeps `RESOURCES.md`, an entry per check with what it
saw and decided. It checks every `RESOURCE_INTERVAL` seconds (300 by default), or sooner
when a task is submitted or collected or it is sent a message. `RESOURCE_MODEL` sets its
model; otherwise it has the run's own.

Machine-specific keys belong in `systems/<system>.json` rather than here, or the
campaign will not run anywhere else.

## prompt.md

Standing, and reread every run as part of the system prompt.

It holds the campaign's objective — what the campaign exists to find out, unchanged from
one run to the next — along with anything else true of every run: what is held fixed so
results stay comparable, and how the agent should work.

Where the person gave you a prompt, it goes in as they wrote it. What the campaign needs
beyond it is added alongside, so which is which stays clear.

What you established yourself — how a job runs, what a measurement reproduced, where the
code and the prior results are — goes in a file of its own that this one names in a line.

A campaign has the sections its own goal needs.

`user_prompt.md` holds this run's aim: which part of that objective this run goes after,
and when it stops. The objective outlives the run; the aim does not.

Things that tend to cause trouble here:

- **Results.** Any value the campaign measures is superseded by the next run, so the file
  needs editing to stay right. Saying what is measured, and where the numbers are kept,
  does not.
- **Dates and version numbers.** A reference the campaign is measured against is usually
  replaced once a run beats it.
- **This run's aim**, which belongs in `user_prompt.md`, and **findings**, which belong in
  the logbook.

Grepping the file for a four-digit year or a version string is a quick way to spot these.

When editing, this is the file to leave alone unless the change is genuinely standing.

## user_prompt.md

Often updated before a run; it is what starts the agent. Keep it short — this is the file the
person editing the campaign touches every time.

- What this run is for, and what would answer it.
- Anything that changed since the last run.
- When to stop — for each way the run could go, not just the expected one.

This is the file a later run is expected to change, so it is where an edit goes by
default.

## method.md

**Creating:** copy one from `methods/` into the campaign, so each campaign owns its own
and can be changed without affecting another. `AGENTS.md` says which to pick.

**Editing:** the campaign's copy may have been changed on purpose. Do not overwrite it
from `methods/`. Compare the two only when something suggests the copy has gone stale —
it names a tool that no longer exists, or describes records the framework no longer keeps
— and then change the parts that are wrong rather than replacing the file.

## Sandbox

With `SANDBOX=bwrap` (Linux only) the agent works in a box built with bubblewrap. The box starts empty
and holds only what is put in it:

- read-only: the system's own directories (`/usr`, `/etc` and the like), the Python the
  framework runs in, the Claude Code CLI, the framework, and the campaign directory
- writable: the workspace, the agent's working directory, and its Claude config
  (`CLAUDE_CONFIG_DIR`, else `~/.claude`)
- its own: an empty home and `/tmp`, gone when the agent stops

Nothing else exists inside, so the rest of the home directory and its credentials are
not there to read. The agent sees only the processes it started. The network is the
machine's.

A campaign whose agent needs more names it in `campaign.json`:

```json
"sandbox": {"read": ["/opt/julia-1.12", "~/data/inputs"], "write": ["/scratch/me/out"]}
```

A path missing from the box fails as "No such file or directory". Preflight checks that
each path named exists. Remote jobs are not affected; they run on their own system.

## System status

A resource agent, or with `"iri": true` in `campaign.json` any agent, gets a
`system_status` tool: whether a system is up, its open incidents, your batch jobs there,
and how busy the queues your buckets use are. It reads the facility's IRI API. See [iri.md](iri.md).

## The workspace

Written by the agent, not by you. What is in it is whatever the campaign asks for —
usually the records named in `method.md`, or in `prompt.md` where the campaign has no
`method.md`.

A logbook is dated entries under a heading per run, appended.

A separate state or handover file tends to go wrong: it duplicates the logbook and the
two drift apart. Current state is the newest logbook entry; the next run's aim is
`user_prompt.md`. Where one is genuinely wanted, reference it from `user_prompt.md`, and
give it a name that will not mislead a later run — anything like "next-run" describes a
moment, and is wrong from the following run onward.

Where a campaign produces one comparable set of numbers per run, a small table with a row
appended each run is easier to read than the logbook or the raw records.

## Setting up a later run

1. Read the last logbook entry — what the previous run concluded and left open.
2. Update `user_prompt.md` for this run, as needed.
3. Change `campaign.json` if the system or resources differ.
