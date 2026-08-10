# AgentLab

*A research lab run by agents.*

An experimental lab for persistent agent tasks that submit work to HPC systems.

Built on the same machinery as
[CAS framework](https://github.com/shuds13/cas-framework), which coordinates many
agents on a single search campaign. AgentLab allows a user or team to run any number of
investigations side by side, all visible and steerable from one Slack channel.

You give an agent a goal and a system. It submits the work, reads what comes back,
decides what to try next, and keeps going until it has an answer.

Each investigation is a **campaign**. Campaigns are independent and can run at the same
time, sharing the framework, the system definitions, and optionally one Slack app and
secretary, so nobody stands up new infrastructure per question.

## Structure

```
framework/     the agent loop, the tools it calls, and the mechanics prompt.
               Not edited per campaign.

systems/       one file per machine: module line, proxy, cache paths, queue
               defaults — whatever is true for everyone there.
               endpoints/ — Globus Compute endpoint templates.

users/<you>/   your endpoint UUID, account, and working directory, per system.
               Applies to every campaign you run there. Not tracked by git.

campaigns/<name>/
               prompt.md        the goal
               user_prompt.md   this run's kick-off
               task.py          what a job does
               campaign.json    which system, and what to run on it

workspace/<name>/
               everything the agent produces. Not tracked by git.
```

## Getting started

Ask your agent to get you running. `AGENTS.md` is the procedure it follows: standing
up a Globus Compute endpoint, describing your system and your access, verifying, and
building a campaign around your problem.

`docs/setup.md` covers the same ground for reading directly.

## Running

```
./run.sh <campaign> [user]
```

`run.sh` merges `systems/<system>.json`, `users/<you>/<system>.json` and the campaign's
`campaign.json` into the single config the framework reads, writes it into the
campaign's workspace, and starts the agent.

Start it inside tmux. A campaign runs for hours or days, and closing the terminal kills
it with jobs in flight.

```
framework/list_agents.sh --all          every run and its outcome
framework/kill_agent.sh --drain <run>   stop cleanly, finishing jobs in flight
```

## Writing a campaign

Copy `campaigns/example-vllm-inference-opt/` and replace four files:

| file | what it holds |
|---|---|
| `prompt.md` | the goal, what is fixed, what may vary, when to stop |
| `user_prompt.md` | what to do first |
| `task.py` | how one job runs, what it returns, and what the agent is told about it |
| `campaign.json` | which system, and any parameters for it |

`task.py` defines four names the framework imports — `JOB_DESC`, `JOB_SCHEMA`,
`job_key`, `remote_fn`. `remote_fn` is sent to the worker by source, so everything it
needs must be imported inside it or passed in through its arguments.

Nothing in `framework/` changes.

## The example campaign

`campaigns/example-vllm-inference-opt/` tunes single-node vLLM inference for
Llama-3-70B on Aurora: it measures per-token decode rate and searches for a
configuration that lowers it. It shows the shape of a campaign — a prompt stating the
goal, what is fixed, what may vary and the leads to work; and a task that runs one
benchmark configuration and returns metrics plus startup diagnostics.

Its own README describes what a campaign directory holds and what to add for your
problem.

## Current limits

- `task.py` in the example exposes a fixed set of vLLM flags. An `extra_args`
  passthrough would let a campaign reach any flag its version supports.
- Slack is present and unwired — `framework/secretary.py` and the bridge scripts.
- `run.sh` emits a single resource bucket per system. Several shapes per system would
  need the merge extended.
