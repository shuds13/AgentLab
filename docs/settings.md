# Settings

Every setting AgentLab reads, and where it comes from.

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
| `CAS_MAX_SUBMITS` | 60 | jobs submitted before winding down |
| `CAS_MAX_RUNTIME` | unset | seconds from start before winding down; unset means no limit |
| `CAS_STALL_LIMIT` | unset | seconds with nothing completing before giving up; unset means wait |
| `CAS_REMOTE_TIMEOUT` | 43200 | seconds to wait on one remote job |
| `CAS_LOCAL_TIMEOUT` | 14400 | seconds to wait on one local job, where a task defines one |

Slack and notification. Without `SLACK_WEBHOOK_FILE` these do nothing.

| | default | |
|---|---|---|
| `SLACK_WEBHOOK_FILE` | `~/.slack_webhook` | incoming webhook for outbound posts |
| `NOTIFY_START` | false | post when a launch starts |
| `NOTIFY_DAILY` | true | periodic status post |
| `NOTIFY_DAILY_INTERVAL` | 86400 | seconds between those posts |
| `NOTIFY_FINISH` | true | post when a launch ends |
| `NOTIFY_PROBLEM_GRACE` | 1800 | seconds after the agent flags a blocker before shutting down |

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
| `CLAIM_STALE_SECONDS` | 21600 | before an unfinished claim can be taken over |
| `ANNOUNCE_POLL` | 2 | seconds between announcement-board checks while waiting |

## Environment — Slack bridge and secretary

One of each per lab, not per campaign.

| | default | |
|---|---|---|
| `WORKSPACE_ROOT` | `workspace/` | scanned for campaigns |
| `SLACK_CHANNEL` | — | channel ID the bridge reads |
| `SLACK_BOT_TOKEN_FILE` | `~/.slack_bot_token` | bot token, needs `channels:history` |
| `SLACK_BOT_NAME` | `@cas_agent` | plain-text mention fallback |
| `SLACK_FETCH_POLL` | 30 | seconds between Slack checks |
| `SECRETARY_POLL` | 10 | seconds between board checks |
| `AGENT_ALIVE_WITHIN` | 300 | heartbeat age treated as a live agent |

## Files

Time limits that bound a job rather than a launch live here.

| | |
|---|---|
| `campaigns/<name>/campaign.json` | which system, the model, `target.timeout` — seconds a job's own command may run |
| `systems/<system>.json` | module line, proxy, cache paths, `ppn`, `max_concurrent`, and `bucket_defaults` including the batch allocation's `walltime` |
| `users/<you>/<system>.json` | endpoint UUID, account to charge, `work_dir` on the compute system |

`bucket_defaults.walltime` bounds the batch allocation, which persists between jobs. An
allocation expiring while a job runs reports `ManagerLost`.
