# Settings

Every setting AgentLab reads, and where it comes from.

Three places, in the order they are read:

| | |
|---|---|
| `lab.yaml` | what this lab runs, and where its own things are. Copy `lab.yaml.template` |
| `notifiers/<transport>.env` | credentials and reader behaviour for the transport, default `notifiers/slack.env`; `NOTIFIER` picks another |
| `campaigns/<name>/run.sh` | what one campaign wants, which is the last word |

A value already in the environment wins over all three, so a setting given on the
command line holds for that run only.

## `lab.yaml`

The lab, at a glance: `bin/lab.sh start | stop | status` runs what is switched on here.
Untracked, because it is this installation's, not the framework's.

| | | |
|---|---|---|
| `bridge` | off | forward Slack messages to the secretary and the boards |
| `secretary` | off | answer from the records, start and stop runs |
| `engineer` | off | this repository, worked on from a channel of its own |
| `litellm` | off | a LiteLLM proxy, for using non-Anthropic models. `when-needed` leaves it to a run's preflight, which starts it only if one of them was asked for and nothing is answering |
| `litellm-bin` | — | the `litellm` executable, from the venv `docs/llm.md` builds |
| `litellm-config` | `litellm/config.yaml` | which models it reaches |
| `litellm-url` | — | where it answers: `CRITIC_BASE_URL` |
| `litellm-key-file` | — | its credential, if not the agent's: `CRITIC_API_KEY_FILE` |
| `slack-channel` | — | the channel the bridge reads: `SLACK_CHANNEL` |
| `engineer-slack-channel` | — | the engineer's own channel: `ENGINEER_SLACK_CHANNEL` |
| `engineer-webhook-file` | — | the webhook bound to it: `ENGINEER_WEBHOOK_FILE` |
| `engineer-resume` | off | `off` starts a fresh conversation, `last` carries on the one before, `compact` carries it on with the history summarised down first: `ENGINEER_RESUME` |
| `engineer-allow` | — | Slack user ids that may instruct the engineer, space separated; empty means anyone in the channel: `ENGINEER_ALLOW` |
| `startable-campaigns` | — | campaigns the secretary may start and stop, space separated: `SLACK_CAMPAIGNS` |

Each line is `name: value`, and the right-hand column is the environment variable it
sets, which is how everything below it is reachable without this file at all. The
exception is the proxy's start command, `CRITIC_GATEWAY_START`, which is built from the
bin, the config and the port in the URL rather than written out — LiteLLM takes the
rest of its settings from its own config, so there is nothing else for it to hold.

A relative path on a line naming a file is taken from the lab directory, so
`litellm/config.yaml` means the same thing wherever the command was run from.

## Environment — set in the campaign's `run.sh`

Required.

| | |
|---|---|
| `CAMPAIGN` | directory name under `campaigns/`. `run.sh` sets it from its own location. |
| `USER_NAME` | picks `users/<name>/`. Defaults to `$USER`. |

Stopping conditions for one launch. A campaign can span several: the logbook carries across, and a
new launch continues from it. On reaching either limit the agent submits nothing further,
collects what is in flight, writes up, and exits.

| | default | |
|---|---|---|
| `PREFLIGHT` | unset | run the checks, print the tools and the model, and stop without starting the run |
| `RESUME_SESSION` | — | a Claude session id to start from, so a run continues a conversation you had with an agent. Forked, so that transcript is untouched; its whole context comes with it |
| `MAX_SUBMITS` | 60 | jobs submitted before winding down |
| `MAX_RUNTIME` | unset | seconds from start before winding down; unset means no limit |
| `STALL_LIMIT` | unset | seconds with nothing completing before giving up; unset means wait |
| `JOB_TIMEOUT` | 43200 | seconds to wait on one remote job |
| `LOCAL_JOB_TIMEOUT` | 14400 | seconds to wait on one local job, where a task defines one |
| `MAX_CONCURRENT` | `max_concurrent` in campaign.json, else the system file, else 1 | remote jobs in flight at once |
| `LOCAL_MAX_CONCURRENT` | `local_max_concurrent` in campaign.json, else the system file, else 1 | local jobs at once; one by default, since a local job is assumed to use the whole machine |

Both follow the same order — environment, campaign, system, default. The system file
carries a site default, conservative because queue policy and an allocation bound it as
much as the hardware does; a campaign overrides it, because how many jobs are sensible
depends on what one job does.

Slack and notification. Without `SLACK_WEBHOOK_FILE` these do nothing.

| | default | |
|---|---|---|
| `WATCH` | false | serve this run for a browser at `http://127.0.0.1:<WATCH_PORT>/` — its log as it is written, and the files it writes |
| `WATCH_PORT` | 8765 | port the viewer listens on, so two runs can be watched at once |
| `WATCH_IDLE` | 600 | seconds without anyone looking before the viewer stops itself; it outlives the run, since that is when its records are worth reading |
| `SLACK_WEBHOOK_FILE` | `~/.slack_webhook` | incoming webhook for outbound posts |
| `SLACK_PREFIX` | the agent's handle | prepended to every post, so one channel carrying several campaigns stays readable |
| `NOTIFY_SCRIPT` | `framework/slack_notify.sh` | the script that posts a message; another transport's script goes here |
| `NOTIFY_START` | false | post when a launch starts |
| `NOTIFY_DAILY` | true | periodic status post |
| `NOTIFY_DAILY_INTERVAL` | 86400 | seconds between those posts |
| `NOTIFY_FINISH` | true | post when a launch ends |
| `NOTIFY_PROBLEM_GRACE` | 1800 | seconds after the agent flags a blocker before shutting down |

The critic, a second model that checks each cycle's write-up against the recorded
results. Resolved once at preflight and named in the startup post; `docs/llm.md` covers
what a gateway makes available.

| | default | |
|---|---|---|
| `CRITIC_MODEL` | — | model name, `auto` to pick one of a different family from what the gateway serves, or unset for no critic |
| `CRITIC_REQUIRED` | false | refuse to start when no critic can be resolved |
| `CRITIC_LEVEL` | full | `full` judges every claim; `light` only those resting on a recorded number, which suits short runs |
| `CRITIC_PROMPT_FILE` | `framework/critic_prompt_<level>.md` | a prompt of your own, instead of either level |
| `CRITIC_BASE_URL` | the agent's `ANTHROPIC_BASE_URL` | gateway serving the critic, when it is not where the agent goes |
| `CRITIC_API_KEY` | the agent's `ANTHROPIC_API_KEY`, else what Claude Code is configured with (`settings.json`, including `apiKeyHelper`) | credential for that gateway |
| `CRITIC_API_KEY_FILE` | — | a file holding that credential instead, as `SLACK_WEBHOOK_FILE` does |
| `CRITIC_GATEWAY_START` | — | command that starts the lab's model gateway, run at preflight when something needs it and nothing is already serving. Something needs it when a critic was asked for, or when `ANTHROPIC_BASE_URL` is the gateway's own URL — the agent is on a model behind it |
| `CRITIC_GATEWAY_WAIT` | 60 | seconds to wait for that gateway to answer before carrying on without it |
| `CRITIC_MAX_TOKENS` | 8000 | cap on one review; a reasoning model spends much of it thinking |

What the agent is given to work with. By default: the campaign's own job tools, and
`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`, `Skill`, `WebFetch`, `Agent`. Left out
are the CLI's messaging and scheduling tools, and the ones that fork work out from
under the framework's bookkeeping. `WebSearch` is left out as well: it runs on the API
server rather than on this machine, so a gateway that does not carry server tools
refuses it. Add it where the lab's gateway does.

| | default | |
|---|---|---|
| `AGENT_TOOLS` | — | change that set: `+WebSearch -Write` adjusts it, a plain list (`Read Grep Bash`) replaces it. One form or the other, not both. The job tools are always given — they come from what the task defines. |
| `AGENT_MODEL` | — | which model the agent runs as. Unset leaves it to Claude Code. An alias (`sonnet`, `opus`) or a full model name. |

`PREFLIGHT=true ./run.sh` runs the checks, prints the tools and the model, and stops.

Less often changed.

| | default | |
|---|---|---|
| `ROLE` | both | free-form label, when two agents share a campaign |
| `USER_PROMPT_FILE` | `user_prompt.md` | which kick-off file to read from the campaign |
| `LAB_DIR` | parent of `framework/` | the lab root |
| `WORKSPACE_DIR` | `workspace/<campaign>` | where output goes |
| `CAMPAIGN_DIR` | `campaigns/<campaign>` | where the campaign's files are |
| `TASK_DIR` | the campaign directory | where `task.py` is found |
| `TASK_MODULE` | task | module name within `TASK_DIR` |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | directory holding the Claude Code `settings.json` that decides which LLM the agent uses. `docs/llm.md` |
| `AGENT_MODEL` | unset | explicit model name passed to the Agent SDK; normally set to a proxy alias from `litellm/config.yaml`, otherwise Claude Code's configured model applies |
| `CLAIM_STALE_SECONDS` | 21600 | before an unfinished claim can be taken over |
| `ANNOUNCE_POLL` | 2 | seconds between announcement-board checks while waiting |

## Environment — Slack bridge and secretary

One of each per lab, not per campaign. Channels and startable campaigns come from
`lab.yaml`; the rest from `notifiers/<transport>.env`, default `notifiers/slack.env`.

| | default | |
|---|---|---|
| `WORKSPACE_ROOT` | `workspace/` | scanned for campaigns |
| `SLACK_CHANNEL` | — | channel ID the bridge reads |
| `SLACK_BOT_TOKEN_FILE` | `~/.slack_bot_token` | bot token, needs `channels:history` for a public channel or `groups:history` for a private one |
| `SLACK_BOT_NAME` | `@cas_agent` | plain-text mention fallback |
| `SLACK_FETCH_POLL` | 5 | seconds between Slack checks; dominates end-to-end latency |
| `RESUME_SESSION` | — | a session id, or `last` / `compact` for the engineer's own previous conversation. Set from `engineer-resume` |
| `SLACK_ALLOW` | — | Slack user ids whose messages this bridge forwards, space separated; empty forwards everyone's. Set from `engineer-allow` for the engineer's bridge, so people can watch its channel without driving it |
| `SLACK_READ_ALL` | false | forward every channel message to the secretary, which decides which are for it; mentions only when the secretary is down |
| `SLACK_CAMPAIGNS` | — | campaigns the secretary may start and stop on request; empty means none |
| `START_COOLDOWN` | 300 | seconds before the same campaign can be started again |
| `SECRETARY_POLL` | 5 | seconds between inbox checks, and between heartbeat writes |
| `SECRETARY_ALIVE_WITHIN` | 60 | secretary heartbeat age the bridge still treats as up |
| `AGENT_ALIVE_WITHIN` | 300 | heartbeat age treated as a live agent |

## Files

Time limits that bound a job rather than a launch live here.

| | |
|---|---|
| `campaigns/<name>/campaign.json` | which system, the model, `target.timeout` — seconds a job's own command may run |
| `lab.yaml` | what this lab runs and where its own things are, as above |
| `framework/framework_prompt.md` | how a run works whatever its method: the tools, the records the runner reads, and the two ways a run ends. Given to every run alongside its `method.md` |
| `litellm/config.yaml` | the non-Anthropic models this lab can reach, and the keys for them. Copy `litellm/config.yaml.template`; `docs/llm.md` explains the settings that matter |
| `bin/review_campaign.sh <campaign>` | reads a campaign before it runs and reports what would waste a machine or a budget, appending to `campaigns/<name>/EFFICIENCY-REVIEW.md`; `framework/review_campaign_prompt.md` is what it asks |
| `systems/<system>.json` | module line, proxy, cache paths, `ppn`, `max_concurrent`, and `bucket_defaults` including the batch allocation's `walltime` |
| `users/<you>/<system>.json` | endpoint UUID, account to charge, `work_dir` on the compute system |

`bucket_defaults.walltime` bounds the batch allocation, which persists between jobs. An
allocation expiring while a job runs reports `ManagerLost`.
