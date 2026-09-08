# Slack

A campaign posts its status, milestones and alerts to a Slack channel, and messages sent
back reach the secretary, which answers them and steers running campaigns on their
behalf. Optional, and it can be added at any time.

## The pieces

**The Slack app** is a registration in a workspace: a name and a set of scopes. Nothing
runs on Slack's side for you. It exists so credentials can be issued against it. Most
workspaces need a Slack admin to approve one, requested through the app setup process.

**The incoming webhook** is a URL Slack issues, `https://hooks.slack.com/services/…`,
bound to one workspace and one channel. POST JSON to it and the message appears there.
There is no authentication header — the URL is the credential.

**The bot token**, `xoxb-…`, is presented as `Authorization: Bearer` on calls to Slack's
Web API. Reading a channel needs the scope for its kind: `channels:history` for a
public channel, `groups:history` for a private one. A bot reads only channels it is a
member of, so a private channel has to invite it.

Keep both outside the repository, in `~/.slack_webhook` and `~/.slack_bot_token`.

## Two paths, two credentials

Outbound and inbound use different endpoints and different credentials. Neither passes
through the other, and neither passes through the app — the app is an identity, not a hop.

**Outbound.** `framework/slack_notify.sh` POSTs to the webhook URL. The URL alone
authorises it, so any machine holding the file can post.

**Inbound.** `framework/slack_to_board.py` polls
`https://slack.com/api/conversations.history` with the bot token, every 5 seconds, and
delivers any message mentioning the bot. Where it delivers depends on the secretary's
heartbeat in `workspace/run/secretary_heartbeat`:

- **secretary up** — to `workspace/run/slack_inbox.md`, which only the secretary reads.
  One question, one answer.
- **secretary down** — to every campaign's `ANNOUNCEMENTS.md`, where the running agents
  pick it up. A message naming a campaign goes to that one. One answer per campaign.

The heartbeat is rewritten every poll, so a secretary that dies or wedges mid-answer
fails over to the boards rather than leaving questions in an inbox nobody reads.

Slack has no way to reach a compute node directly, which is why inbound is a poll rather
than Slack pushing to you.

## The announcements board

`ANNOUNCEMENTS.md` is the mechanism; Slack is one way to write to it. A running campaign
reads its board between turns and acts on what is there. Appending to the file by any
other means works identically.

## The secretary

`framework/secretary.py` watches the inbox and answers from `results.jsonl`,
`LOGBOOK.md` and `JOURNAL.md`, posting back through the webhook.

While it is running it owns the Slack questions, so one question gets one answer
however many campaigns and agents are running. It holds one continuing conversation,
so follow-ups work like a chat, and re-reads the files for every factual answer.

What it cannot know is a running agent's live reasoning. That it relays, by appending
to that campaign's board addressed to one agent:

```
[for research agent <run_id>] <question>   that agent answers
[for all research agents] <question>       every running agent in the campaign answers
```

An unaddressed line is answered by every agent reading that board, which is why a
relay always names its reader.

It can also start and stop runs, for campaigns named in `SLACK_CAMPAIGNS`:
`bin/start_run.sh <campaign>` and `bin/stop_run.sh <handle>`. Both refuse
anything not on that list; a start also refuses a campaign that already has a live
agent or was started within `START_COOLDOWN`, and a stop always drains. A request made
without mentioning the bot is confirmed in the channel before either runs.

Each running agent posts under a short handle — `local1`, `vllm2` — unique across the
lab, which is what a person types to address or stop it. The exact run it belongs to is
in `list_agents.sh`, alongside the Claude session id for reading a finished run back.

## Where things run

| | runs where | needs |
|---|---|---|
| campaign agent | any machine, one per campaign | `~/.slack_webhook` |
| bridge | one per lab | `~/.slack_bot_token`, the channel ID |
| secretary | one per lab | `~/.slack_webhook` |

Both are started by `bin/lab.sh start`, which runs whatever `lab.yaml` switches on.

Every post carries `*[$SLACK_PREFIX]*` — the campaign and agent for a research agent,
`secretary` for the secretary — applied in `slack_notify.sh` so one channel shared by
several campaigns stays readable.

The channel ID is in `lab.yaml` and the bot name and credential paths in
`notifiers/slack.env`, each copied from the template beside it and untracked. The
credentials themselves stay where they are, outside the repository.

Which is why joining a lab is a one-line setup: you need the channel and the webhook URL,
and nothing else. Creating the app, issuing the token and running the two processes
happens once, by whoever hosts the lab.

Neither credential is tied to the machine that created it. Moving a lab means copying the
two files and running `bin/lab.sh start` elsewhere.
