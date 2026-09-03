# Working in this repository

Someone arriving here has something they want to run and access to a machine. Get them
running. The aim is to be concise. Do not quibble over things unless they really matter.

## Setting someone up

Ask which they want, and say the first is the one for anyone new here:

1. **Set up the lab and run the example.** On the machine they are already on. No
   account, no endpoint, minutes to run.
2. **Set up a campaign of their own.**

Each is a sequence below, numbered from 1.

One step per message: a single question or a single action, take the answer, do the
thing, move on. Start each message with `Step N`. When their files already answer a
step, say so in a line and go to the next, so they can see nothing was skipped.

Where there is more than one way to do something, choose the one that works and say
which in a line.

## Setting up the lab and running the example

`campaigns/example-quick-optimum`: one dial, a noisy response, a lowest point to find.

### 1. Check the installation

Python 3.10+, and the `claude` CLI on PATH and authenticated:

```
python3 -V && claude --version && pip install -r requirements.txt
```

### 2. Give them the line

Show them the command. Offer to run it; otherwise it is theirs to start, and to say
when it has finished.

```
cd campaigns/example-quick-optimum && WATCH=true AGENT_MODEL=haiku ./run.sh
```

- `WATCH=true` serves the run to a browser at the address it prints.
- `AGENT_MODEL=haiku` runs the agent on haiku. Leave it out for their own setting.
- `PREFLIGHT=true ./run.sh` checks the task contract and the workspace and prints the tools
  and the model, without submitting anything.

### 3. Read what it produced

`workspace/example-quick-optimum/` — `results.jsonl`, `LOGBOOK.md`, and the run
directory with its logs. This is where they will read their own campaign.

`bin/list_agents.sh --all` lists the run and the Claude session it ran in, which
`claude -r <session>` reads back.

### 4. Slack (optional)

The lab posts status, milestones and alerts to a Slack channel, and messages back steer
a run. One app and one secretary serve every campaign, so it is set up once for the lab.

The `Slack` section below is the procedure. With it in place, run the example again so
they see the posts arrive.

### 5. Run it again with a critic (optional)

A critic is a second model, called every cycle, that checks the write-up against the
recorded results. Aim for a family other than the agent's own — that is what `auto`
picks where it can.

The SDK speaks only the Messages API, so reaching a non-Anthropic model needs something
that serves it in that format. Which of these they have decides the step:

- Their endpoint serves one already — name it, and check with
  `CRITIC_MODEL=<model> PREFLIGHT=true ./run.sh`.
- It has non-Anthropic models but does not serve Messages — LiteLLM is what the lab runs
  in front of it, and `docs/llm.md` is the procedure. Offer to set it up.
- Neither, or they would rather not now — a Claude model uses the access the agent
  already has, or skip the step.

### 6. What next

Their own campaign is the sequence below. `campaigns/example-local-compression/` is a
second local run, on real work.

## Setting up their own campaign

Open with what they will need:

- A prompt — what they want the agent to do.
- A machine they can run on, and an account to charge.
- The Claude Agent SDK, and access to an LLM service it can use.
- Any files they already have — a script, notes, previous results.

Then step 1, in the same message.

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
| `prompt.md` | their prompt: the campaign, standing across runs |
| `user_prompt.md` | the aim of one run, and when it stops; rewritten each run |
| `task.py` | how one job runs, what it returns, what the agent is told about it |
| `campaign.json` | which system, and any parameters for it |
| `run.sh` | settings and launch |
| `method.md` | optional; how the agent works, when the default does not suit |

`campaigns/example-vllm-inference-opt/` has all of these to copy the shape from, and its
README says more about each.

`methods/` holds the starting points for how the agent works. Copy one into the
campaign as `method.md` — the agent reads the campaign's copy, so every campaign owns
its own and can be changed without affecting any other:

| | |
|---|---|
| `methods/quick.md` | a few jobs, a few lines per cycle in `LOGBOOK.md` |
| `methods/standard.md` | runs jobs in rounds, keeps `results.jsonl` and `LOGBOOK.md` |
| `methods/research.md` | five-step cycles, and a `JOURNAL.md`/`JOURNAL.tex` write-up per cycle with figures |

Choose from what they described. Take `standard.md` unless they are investigating why
something behaves as it does and want the reasoning recorded — a typeset report, figures
per cycle, hypotheses written down — in which case take `research.md`. Take `quick.md`
for a short run whose record someone will read at a glance: trying the machinery, a
handful of jobs, an answer rather than an account of how it was reached. Say which you
copied and why, in a line. Then change anything in it that conflicts with what they have
already told you, and leave the rest.

`task.py` defines a job in one of two places, and may define both:

| | |
|---|---|
| remote | `JOB_DESC`, `JOB_SCHEMA`, `job_key`, `remote_fn` — runs on the system through Globus Compute |
| local | `LOCAL_DESC`, `LOCAL_SCHEMA`, `local_fn` — runs on the machine the agent is on |

Whichever set is present is what the agent gets tools for. `remote_fn` is shipped to the
worker by source, so every import goes inside its body and paths reach it through `args`
and `target`; `local_fn` takes `args` alone and is called in-process. Expose more
parameters than seem needed — a campaign can only explore what its schema allows.

A task with `local_fn` and no `remote_fn` needs no endpoint, no account and no
`users/<you>/<system>.json`, so the endpoint and access steps below do not apply to it.

If they gave you results from earlier work, ask whether those measure the same quantity,
the same way, as a job here. If they do, seed the workspace with them —
`workspace/<name>/results.jsonl`, and `LOGBOOK.md` for the conclusions — marking the
entries as coming from that earlier work. The agent then finds them where it already
looks before every submit, rather than only when a prompt names the file. If they measure
something else, they stay a reference document and `prompt.md` says what they are for.

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

Starting it may need a Globus login in a browser; walk them through it. Then read the UUID
back with `globus-compute-endpoint list`.

### 7. Record their access

Write `users/<their-username>/<system>.json`: endpoint UUID, account to charge, and a
writable directory on the compute system. Create that directory. This file is not
tracked by git.

### 8. Check before the first job

Run `PREFLIGHT=true ./run.sh`. It checks the task contract, endpoint status and
workspace writability, prints the tools and the model, and stops without submitting
anything. Most misconfigurations fail with a message here.

Ask before submitting anything — a single job can hold a node for a long time. With
their agreement, run one.

### 9. Slack (optional)

The campaign posts status, milestones and alerts as it goes, and they can message it
back to steer it mid-run. Where the lab already has Slack, there is nothing to set up;
`run.sh` holds which posts this campaign makes.

Otherwise the `Slack` section below is the procedure. It can be added at any time.

### 10. Hand over

The campaign is theirs to start. Show them the command, in tmux if needed:

```
tmux new -s agentlab
cd campaigns/<name> && ./run.sh
```

`PREFLIGHT=true ./run.sh` runs the checks, prints the tools and the model, and stops.
Worth doing before a long run. `../../bin/review_campaign.sh <campaign>` runs an
efficiency review of the campaign.

Tell them what the stopping conditions are set to, and how to watch and stop it —
`bin/list_agents.sh --all` and `bin/kill_agent.sh --drain <run_id>`.

### 11. Globus Transfer (optional)

Gives the agent a `transfer` tool -- `ls`, `get`, `put` -- for reading and
writing files on the compute system: scripts and inputs it revises between jobs, and
whatever the jobs produce. Not offered at all when unconfigured.
Reference: `docs/globus_transfer.md`.

Do it yourself; the only step that needs them is the browser login. `bin/setup_globus.sh`
covers the same ground as a script if they would rather run one.

1. **Is it needed?** If `work_dir` from their user file is readable from here, stop --
   there is nothing to set up.
2. **CLI and login.** `globus whoami`. If that fails, `pip install globus-cli` and ask
   them to run `globus login` -- browser, one time.
3. **This machine.** `globus endpoint search --filter-scope my-endpoints` lists what
   they own. If nothing there is this host, they need Globus Connect Personal: install
   it, run `globusconnectpersonal -setup --no-gui`, and start it with `setsid nohup`
   so it survives the shell. It serves files only while running, and only the paths in
   `~/.globusonline/lta/config-paths`.
4. **The compute system.** Read `globus_collection` from `systems/<system>.json`. Do
   **not** search by name: sites publish a guest collection per project and users
   publish their own, so a name search returns hundreds of look-alikes. If the field is
   missing, find the collection once in the Globus web app and add it to the system
   file, where it is stated once for everyone.
5. **Consent.** A Globus Connect Server v5 collection needs a one-off `data_access`
   consent; a Globus Connect Personal collection does not have that scope at all, and
   asking for one fails with `UNKNOWN_SCOPE_ERROR`. So request it for the compute
   system's collection only:

   ```
   globus session consent 'urn:globus:auth:scope:transfer.api.globus.org:all[*https://auth.globus.org/scopes/<remote>/data_access]'
   ```

6. **Get the path from the user file, not by browsing.** `work_dir` is already
   recorded there and is the path that matters. Verify that one path resolves:
   `globus ls <collection>:<work_dir>`. Never list a directory that holds every
   project on the machine.
7. **Write it** into `users/<them>/<system>.json` as a `globus` block:
   `remote_collection`, `local_collection`, `remote_write_root` -- the one subtree `put`
   may write to, their `work_dir` unless they say otherwise -- and optionally
   `remote_read_root`. Leave the read root unset unless they ask for it: the usual job is
   fetching a log from a path the campaign did not choose. Tell them the three bounds and
   where to change them; the local side is fixed to the campaign workspace.
8. **Prove it.** `globus ls <remote>:<work_dir>`, then fetch a real file with the
   `transfer` tool and show them it arrived.

## Steps that need them at the keyboard

A few steps need them at the keyboard — a password, an MFA passcode, a browser login.
Say exactly what to do, wait for them, and pick it up from there.

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
- **Globus Transfer endpoint activation**, if they sync a workspace across machines —
  see `docs/globus_transfer.md`.
- **Slack app setup**, if they want it.

At each of these, give them the exact command or the exact page, and carry on when
they say it is done.

Ask them for what only they know: which system, which project or allocation to charge,
which queue, and where they have writable space.

## Slack

A campaign posts its status, milestones and alerts to Slack. Messages sent back reach
the secretary, which answers them, relays what needs a running agent's own reasoning,
and starts or stops the campaigns the lab has allowed. It can be added at any time.

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
   approve it. They request that through the app setup process.
2. Add an incoming webhook. Its URL goes in `~/.slack_webhook` and is what members
   receive when they join.
3. Create a bot token in `~/.slack_bot_token`, scoped to the kind of channel the lab
   runs in: `channels:history` for a public one, `groups:history` for a private one.
   A private channel also needs the bot invited to it. Inbound only — members never
   need it.
4. Record what this lab runs and the channel it runs in:

   ```
   cp lab.yaml.template lab.yaml
   cp notifiers/slack.env.template notifiers/slack.env
   ```

   Neither copy is tracked by git. `lab.yaml` is the short one — which processes to
   start, the channel ID, which campaigns may be driven from Slack — and it is what
   someone reads to see what the lab does. The Slack file holds the bot name and the
   paths to the two credentials. `docs/settings.md` lists everything both can hold.

   Ask which campaigns, if any, may be started and stopped from Slack, and name them on
   the `startable-campaigns` line. A campaign not named there can be neither started
   nor stopped however the request is phrased.

   Ask which way they want to reach the agents. By default a message has to mention the
   bot. With `SLACK_READ_ALL=true` the secretary is given every message in the channel
   and decides which are for it, so a question can be asked in passing; it costs a
   secretary turn per message, and the mention rule comes back whenever the secretary
   is not running.

5. Start what they switched on:

   ```
   bin/lab.sh start
   ```

   The bridge delivers channel messages to the secretary, or to the campaign boards
   when the secretary is not running. The secretary answers questions from the recorded
   results so campaigns are not interrupted to reply, and starts and stops runs on
   request. `bin/lab.sh status` says what is up and where each one logs; `stop` ends
   them.

These are long-running and belong to the lab, not to a campaign or a user. One of each
serves everyone.

## Running and controlling

**Start it inside tmux if needed.** A campaign runs for hours or days, and losing the
terminal kills the process with jobs in flight and their results uncollected.

```
tmux new -s agentlab
cd campaigns/<name> && ./run.sh
```

```
bin/list_agents.sh --all          every run and its outcome
bin/list_agents.sh -n 5           the last five runs
bin/kill_agent.sh --drain <run>   stop cleanly, finishing jobs in flight
```

A running agent has a short handle — `local1` — which is what it posts under in Slack
and what a person calls it. A finished run keeps the Claude session it ran in, shown by
`list_agents.sh`, so `claude -r <session>` reads back what the agent was thinking.

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
