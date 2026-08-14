# Working in this repository

Someone arriving here has something they want to run and access to a machine. Get them
running. The aim is to be concise. Do not quibble over things unless they really matter.

## Setting someone up

Open with what they will need:

- A prompt — what they want the agent to do.
- A machine they can run on, and an account to charge.
- The Claude Agent SDK, or the `openai` package plus an OpenAI-compatible LLM service for campaigns configured with `agent.provider: openai`.
- Any files they already have — a script, notes, previous results.

Then step 1, in the same message.

One step per message after that: a single question or a single action, take the answer,
do the thing, move on. Start each message with `Step N`. When their files already
answer a step, say so in a line and go to the next, so they can see nothing was skipped.

Where there is more than one way to do something, choose the one that works and say
which in a line.

### 1. Ask for a name

Ask for a short name for what they are doing. When they answer, create `campaigns/<name>/`.

### 2. Get their files

Ask them to copy what they have into that directory, or to point you at the files and
you will copy them. If they have nothing on disk, move on.

### 3. Find out what one job is

You are building the machinery around their work, so what you need is mechanical: the
command or script that runs one piece of work, what changes between one job and the
next, and where its result comes from — a number it prints, a file it writes.

Read their files first, then ask only for what you still need. Their goal is theirs;
take it as given and build to it.

### 4. Fill in the campaign

Write whichever of these the campaign does not have yet, and use what they gave you as
it stands:

| | |
|---|---|
| `prompt.md` | their prompt |
| `user_prompt.md` | what to do first |
| `task.py` | how one job runs, what it returns, what the agent is told about it |
| `campaign.json` | which system, optional agent provider/model settings, and any parameters for it |
| `run.sh` | settings and launch |

`campaigns/example-vllm-inference-opt/` has all five to copy the shape from, and its
README says more about each.

`task.py` defines `JOB_DESC`, `JOB_SCHEMA`, `job_key` and `remote_fn`. `remote_fn` is
shipped to the worker by source, so every import goes inside its body and paths reach it
through `args` and `target`. Expose more parameters than seem needed — a campaign can
only explore what its schema allows.

Then the stopping conditions, which go in `run.sh`: There are safeguards in place in
case to stop the agent if it has not met the users goal such as total tasks or campaign
wallclock. `docs/settings.md` has every setting. If their files already indicate these,
use that and tell them what you set. Otherwise ask, and offer the defaults.

### 5. The machine

Ask which system. If `systems/<system>.json` exists you have its module line, proxy,
cache paths and queue defaults already. If not, you are adding a machine — read an
existing one and the templates in `systems/endpoints/`.

### 6. The Globus Compute endpoint

The user needs to have their endpoint on the remote system. Check with them they do not
already have an endpoint and environment set up. If they do, get the UUID from them with
`globus-compute-endpoint list` and go to step 7. If not, the user will need to have an ssh
connection available. Once they have that you can help put the endpoint in place.

Check the Python version first. On many HPC systems the bare `python3` is the OS one and
too old for the endpoint package. If their application comes from a module, load it and
build the venv on top with `--system-site-packages`, so the endpoint's workers can see
that application:

```
module load <their-module> && python3 -m venv --system-site-packages ~/venvs/agentlab && source ~/venvs/agentlab/bin/activate && pip install -U globus-compute-endpoint
```

Confirm the venv sees both the endpoint command and their application before going on.

Create the endpoint, then replace the generated
`~/.globus_compute/<name>/user_config_template.yaml.j2` with the closest template from
`systems/endpoints/`, editing account, filesystem declarations, and the `worker_init`
line that activates their environment.

Choose the launcher deliberately. `SimpleLauncher` gives one worker per allocation and
the job's own launcher spans the nodes — correct when the application manages devices
itself. `MpiExecLauncher` with `available_accelerators` fans out one worker per device —
correct for many independent single-device tasks. `SimpleLauncher` should be
considered the default. If you think they might want `MpiExecLauncher` you should check
with them.

Starting it may need a Globus login in a browser; hand that to them. Then read the UUID
back with `globus-compute-endpoint list`.

### 7. Record their access

Write `users/<their-username>/<system>.json`: endpoint UUID, account to charge, and a
writable directory on the compute system. Create that directory. This file is not
tracked by git.

### 8. One job

Ask before submitting anything — a single job can hold a node for a long time. With
their agreement, run one. Preflight checks the task contract, endpoint status and
workspace writability, so most misconfigurations fail with a message before anything is
submitted.

### 9. Slack, if they want it

The campaign posts status, milestones and alerts as it goes, and they can message it
back to steer it mid-run.

Ask whether they are joining an existing lab or setting one up — joining needs only the
channel and its webhook URL. Setting one up means creating a Slack app, which in most
workspaces a Slack admin has to approve; requesting that approval is part of the setup,
so they do not need it arranged beforehand. The `Slack` section below has both.

It can also be added later, if they would rather set it up another time.

### 10. Hand over

The campaign is theirs to start. Show them the command, and that it goes in tmux:

```
tmux new -s agentlab
cd campaigns/<name> && ./run.sh
```

Tell them what the stopping conditions are set to, and how to watch and stop it —
`framework/list_agents.sh --all` and `framework/kill_agent.sh --drain <run_id>`.

## Steps that need them at the keyboard

A few things you cannot do for them — a password, an MFA passcode, a browser login. Walk
them through those: say exactly what to do, wait, and pick it up from there.

- **Logging in to the compute system.** Most facilities require a one-time passcode per
  login. Check whether you already have a usable session:

  ```
  ssh -o BatchMode=yes <host> true
  ```

  If that succeeds, they have keys or a multiplexed connection and you can drive the
  machine. If it fails, ask them to log in — with `ControlMaster`/`ControlPersist`
  configured their session is reusable, and your later commands work without
  re-authenticating.
- **Globus login**, the first time the endpoint starts.
- **Globus Transfer endpoint activation**, if they sync a workspace across machines with
  `framework/sync_shared.sh`.
- **Slack app setup**, if they want it.

At each of these, give them the exact command or the exact page, and carry on when
they say it is done.

Ask them for what only they know: which system, which project or allocation to charge,
which queue, and where they have writable space.

## Slack

A campaign posts its status, milestones and alerts to Slack, and messages sent back
reach a running agent through the announcements board. It can be added at any time.

Ask first whether they are **setting up a lab** or **joining one**. The work is almost
entirely on the first.

### Joining a lab

Ask to be added to the lab's Slack channel, and for its webhook URL. Write that URL to
`~/.slack_webhook`, outside the repository. That is the whole procedure — no app, no
token, no processes to run.

### Setting up a lab

Done once by whoever hosts it. Walk them through each of these — say what to click,
wait, carry on:

1. Create a Slack app in the workspace. Most workspaces require a Slack admin to
   approve it — request that as part of this step rather than expecting it in advance,
   and expect to wait.
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
cd campaigns/<name> && ./run.sh
```

```
framework/list_agents.sh --all          every run and its outcome
framework/kill_agent.sh --drain <run>   stop cleanly, finishing jobs in flight
```

Stopping conditions, Slack and the rest live in the campaign's `run.sh`. Every setting,
with its default, is in `docs/settings.md`.

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
