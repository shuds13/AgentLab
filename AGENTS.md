# Working in this repository

AgentLab runs persistent agents on **campaigns** — one investigation each, submitting
work to a compute system through Globus Compute.

Someone arriving here has a problem to solve and access to a machine. Your job is to
get them from a clone to a running campaign.

## What you can do, and what you cannot

Authentication is theirs. You cannot supply a password, an MFA passcode, or complete a
browser login. These points need a human:

- **Logging in to the compute system.** Most facilities require a one-time passcode per
  login. Check whether you already have a usable session before assuming anything:

  ```
  ssh -o BatchMode=yes <host> true
  ```

  If that succeeds, they have keys or a multiplexed connection and you can drive the
  machine. If it fails, ask them to log in — with `ControlMaster`/`ControlPersist`
  configured, their session is then reusable and your later commands work without
  re-authenticating.
- **Globus login**, the first time the endpoint starts.
- **Globus Transfer endpoint activation**, if they sync a workspace across machines
  with `framework/sync_shared.sh`.
- **Slack app setup**, if they want it.

At each, stop and give them the exact command to run. Continue when they report back.

Given a working session you can do the rest: create environments, install packages,
write template and config files, start the endpoint, read back its UUID, run preflight,
and launch the agent.

Ask them for what only they know: which compute system, which project or allocation to
charge, which queue, and where they have writable space on that system.

## Onboarding sequence

### 1. Establish the target

Ask which system they will run on, and check whether `systems/<system>.json` exists.
If it does not, you are adding a new machine — read an existing one and the endpoint
templates in `systems/endpoints/` to see what is needed.

### 2. Stand up a Globus Compute endpoint

This runs on the compute system. Over ssh:

Check the Python version first. On many HPC systems the bare `python3` is the OS one
and may be years old, too old for the endpoint package. If their application comes from
a module, load it and build the venv on top with `--system-site-packages`, so the
endpoint's workers can see that application:

```
module load <their-module> && python3 -m venv --system-site-packages ~/venvs/agentlab && source ~/venvs/agentlab/bin/activate && pip install -U globus-compute-endpoint
```

Confirm the venv sees both the endpoint command and their application before going on.

Create the endpoint, then replace the generated
`~/.globus_compute/<name>/user_config_template.yaml.j2` with the closest template from
`systems/endpoints/`, editing account, filesystem declarations, and the `worker_init`
line that activates their environment.

Starting it may require a Globus login in a browser. Hand that step to them.

Read the UUID back with `globus-compute-endpoint list`.

Choose the launcher deliberately. `SimpleLauncher` gives one worker per allocation, and
the job's own launcher spans the nodes — correct when the application manages devices
itself. `MpiExecLauncher` with `available_accelerators` fans out one worker per device —
correct for many independent single-device tasks.

### 3. Record their access

Write `users/<their-username>/<system>.json` with the endpoint UUID, the account to
charge, and their `work_dir`. Create that directory on the compute system.

This file is not tracked by git.

### 4. Verify before running a campaign

Preflight checks the task contract, endpoint status and workspace writability, and
fails with a message rather than starting a run that cannot work. A single job that
returns is worth more than any amount of configuration review.

### 5. Build their campaign

Copy `campaigns/example-vllm-inference-opt/` and replace four files: `prompt.md`,
`user_prompt.md`, `task.py`, `campaign.json`. The README describes what each holds.

Write `prompt.md` from what they tell you about their problem: the current numbers,
what is fixed, what may vary, what counts as an answer, and when to stop. Keep it to
their investigation — the framework's own mechanics live in `framework/SYSTEM.md` and
are already given to the agent.

`task.py` defines `JOB_DESC`, `JOB_SCHEMA`, `job_key` and `remote_fn`. `remote_fn` is
shipped to the worker by source: every import must be inside its body, and paths reach
it through `args` and `target`.

Expose more parameters than you think are needed. A campaign can only explore what its
schema allows, and a lead nobody can reach is a lead nobody tests.

## Slack

Optional. Everything runs without it. Without a webhook file, `slack_notify.sh` exits
quietly and the agent is unaffected.

Ask first whether they are **setting up a lab** or **joining one**. The work is almost
entirely on the first.

### Joining a lab

Ask to be added to the lab's Slack channel, and for its webhook URL. Write that URL to
`~/.slack_webhook`, outside the repository. That is the whole procedure — no app, no
token, no processes to run.

### Setting up a lab

Done once by whoever hosts it. All the browser work lives here, which is why joining
needs none of it.

1. Create a Slack app in the workspace and get it approved.
2. Add an incoming webhook. Its URL goes in `~/.slack_webhook` and is what members
   receive when they join.
3. Create a bot token with `channels:history`, in `~/.slack_bot_token`. Inbound only —
   members never need it.
4. Run the bridge, which forwards channel messages onto the announcements board so
   people can steer running campaigns:

   ```
   framework/run_slack_bridge.sh      set SLACK_CHANNEL and SLACK_BOT_NAME first
   ```

5. Run the secretary, which answers questions from the recorded results so campaigns
   are not interrupted to reply:

   ```
   framework/run_secretary.sh
   ```

Both are long-running and belong to the lab, not to a campaign or a user. One of each
serves everyone.

## Running and controlling

**Start it inside tmux.** A campaign runs for hours or days, and closing the terminal
or dropping an ssh session kills the process — with jobs in flight and their results
uncollected. Confirm they are in a tmux session before launching, or start one.

```
tmux new -s agentlab
./run.sh <campaign> [user]
```

```
framework/list_agents.sh --all          every run and its outcome
framework/kill_agent.sh --drain <run>   stop cleanly, finishing jobs in flight
```

Campaign output goes to `workspace/<campaign>/`, untracked: `results.jsonl`,
`LOGBOOK.md`, `JOURNAL.md`, `SKILL.md`, run directories and logs.

`ANNOUNCEMENTS.md` in that directory reaches a running agent between rounds. Write to
it to correct a wrong conclusion or supply information the agent has no way to obtain.

## Failure signatures

**`ENDPOINT_NOT_ONLINE` on the first submission** — the endpoint manager forks a user
endpoint process on demand and the submit arrived first. Resubmit.

**`ManagerLost`** — the batch allocation reached its walltime mid-task. Blocks persist
between jobs, so a long-lived one eventually expires. Resubmit; the lost job says
nothing about what it was testing. Raise the walltime if it recurs.

**Submits succeed and nothing returns, with no error** — the endpoint is online and
accepting, but workers never start. Usually a failing `worker_init` or a queue request
that cannot be satisfied. Check the endpoint log on the compute system and whether a
batch job was ever queued.

A genuinely dead endpoint is detected rather than waited on: preflight refuses to start
against one, and during a run a health check distinguishes a dead backend from a slow
queue and gives the agent a turn to react. Both alert over Slack when it is configured.
