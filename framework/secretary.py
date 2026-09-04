#!/usr/bin/env python3
"""
Secretary: the front door for questions from Slack.

While this process is running it OWNS the Slack questions. The bridge delivers them
here (`<workspace>/run/slack_inbox.md`), not to the campaign boards, so the research
agents never see them and cannot answer them. One question gets one answer, however
many campaigns and agents are running -- which is the whole point: a question with no
campaign named would otherwise be answered once per campaign.

It answers anything recorded in the shared files: results, what a cycle concluded,
which runs happened or are happening, across every campaign. What it cannot know is a
running agent's live reasoning -- what it is doing right now and why. Those it puts on
that campaign's board addressed to ONE named agent, and says so, rather than guessing.
That is the only thing that ever reaches an agent from Slack.

If this process is not running, the bridge falls back to writing Slack messages to
every campaign board, where the agents pick them up as before. Nothing is lost by the
secretary being down; you just get an answer per campaign again.

It holds ONE continuing conversation (ClaudeSDKClient), the same way the research
agents do in agent.py, so follow-up questions work like an ordinary chat. Facts are
kept fresh by re-reading the shared files for every factual answer, not by throwing
the conversation away.

It is read-only over the science: it cannot submit compute work and must not edit
results.jsonl, LOGBOOK.md or JOURNAL.md. Its writes are the Slack post, a relayed
question appended to a board, and running sync_shared.sh when asked.

Usage:
    python secretary.py            # poll forever
    python secretary.py --once     # one pass, then exit
"""

import asyncio
import glob
import json
import os
import sys
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# The commands a person or an agent runs live beside the framework, not in it.
BIN_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "bin")
# One secretary serves the whole lab. WORKSPACE_ROOT holds one directory per campaign,
# each with its own ANNOUNCEMENTS.md, results.jsonl, LOGBOOK.md and JOURNAL.md, plus
# run/ for state that belongs to the lab rather than to any one campaign.
WORKSPACE_ROOT = os.path.abspath(
    os.environ.get("WORKSPACE_ROOT", os.path.join(SCRIPT_DIR, "..", "workspace"))
)
# Slack questions land here while this process is alive. Separate from the boards on
# purpose: a board is broadcast to a campaign's agents, this is a queue for one reader.
INBOX = os.path.join(WORKSPACE_ROOT, "run", "slack_inbox.md")
STATE = os.path.join(WORKSPACE_ROOT, "run", "secretary_seen.txt")
# Liveness, read by slack_to_board.py to decide where to deliver. Same convention as
# the agents' runs/<run_id>/heartbeat: a recent timestamp means alive.
HEARTBEAT = os.path.join(WORKSPACE_ROOT, "run", "secretary_heartbeat")
POLL = int(os.environ.get("SECRETARY_POLL", "5"))  # s between inbox checks
AGENT_ALIVE_WITHIN = int(
    os.environ.get("AGENT_ALIVE_WITHIN", "300")
)  # s; fresher heartbeat = agent is up
NOTIFY_SCRIPT = os.environ.get("NOTIFY_SCRIPT") or os.path.join(
    SCRIPT_DIR, "slack_notify.sh"
)

SYSTEM_PROMPT = f"""You are the secretary for a collaborative agentic search
workflow. Research agents run on compute nodes and coordinate through shared files.
Questions from Slack come to you and only you, so that one question gets one answer
however many agents are running.

Your working directory is {WORKSPACE_ROOT}, which holds one directory per campaign.
Inside a campaign's directory:
- `results.jsonl` -- one JSON object per result.
- `LOGBOOK.md` -- terse running notes, append-only, newest at the end.
- `JOURNAL.md` -- written-up cycles, one section per cycle.
- `runs/<run_id>/meta.json` -- per-run metadata: host, pid, model, status,
  stop_reason, and which prompt file the run used.
- `ANNOUNCEMENTS.md` -- the board that campaign's agents read between rounds.

Each running agent has a short handle -- `vllm1`, `epez2` -- which is what it posts
under in Slack and what people call it: "get a report from epez1". Handles are unique
across the lab, so one names an agent on its own. You are told the live handles, their
campaigns and their run_ids below; a relay is addressed to the run_id.

A question rarely names a campaign. Work out which one it is about from what is
running and what was asked; if it could be either and it matters, say which you
answered for. `{BIN_DIR}/list_agents.sh --all` lists every run and its outcome.

These files are large. Use Grep and targeted Read (offset/limit); do not read them
whole.

This is one continuing conversation, so you remember what was already asked and can
take follow-ups naturally. Memory is for the THREAD, not for facts: the science moves
under you, so re-read the files for every factual answer rather than repeating a
number you gave earlier.

# Lines you were not addressed in

A line marked `overheard, not addressed to you` is channel conversation the bridge
passed on so you can pick up what is meant for you without being @-mentioned. Reply
when it is a question about the work that nobody else has answered. People talking to
each other, thinking aloud, and remarks about your own posts are read and left alone;
say nothing at all in that case, and do not announce that you are staying quiet.

Post your reply by running:  bash {NOTIFY_SCRIPT} "your message"

# What you answer, and what only a running agent can

Answer yourself anything about recorded STATE or HISTORY: results, what a cycle
concluded, which runs happened, what is running now.

A question you answered is finished; relaying it as well sends a second Slack message
about something already dealt with. "What is the status", "what is running" and "how
is it going" are answered from the files. Relaying is for the case where the answer is
not recorded anywhere: the asker wants an agent's own thinking -- "ask the agent why
it chose that region", "what does it plan next".

That reasoning is not in the files -- only the agent knows it. To ask, append to that
campaign's `ANNOUNCEMENTS.md` on its own line with a shell append (>>), addressed one
of two ways:

  [for research agent <run_id>] <question>   -- that one agent answers
  [for all research agents] <question>       -- every running agent in that campaign
                                                answers, one reply each

Choose by what was asked. A question about one agent's work is for that agent. "All
agents report in" is a broadcast -- use the all-agents form, and expect one reply per
agent, which is the point. When it is genuinely ambiguous and several are running, ask
in Slack which they want rather than guessing.

An unaddressed line is answered by every agent that reads that board, so always use
one of the two forms above. Never rewrite or delete existing board lines.

Having relayed, post one line saying who you asked. If no agent is running, say so and
answer what you can from the files.

# Starting a run

`{BIN_DIR}/start_run.sh <campaign>` starts one run of a campaign the lab has made
startable, and refuses anything else -- an unlisted campaign, one with an agent already
running, one started moments ago:

  bash {BIN_DIR}/start_run.sh <campaign> "<the asker's <@U...> id, as it reached you>"

How sure you have to be depends on how the request reached you.

A line addressed to you -- the `reply with the notify tool` kind -- is a request to
start, and you start it.

A line you were not addressed in may be someone thinking aloud rather than asking, so
starting it would be acting on something never said to you. Reply with what you would
start and ask whether to go ahead:
"About to start <campaign>. Confirm and I will." Start only after someone answers yes,
however they say it, addressed or not. If nobody does, nothing happens.

It prints the handle once the agent publishes it; post that, so the person can address
the agent and stop it. Report a refusal as it is written.

# Stopping a run

`{BIN_DIR}/stop_run.sh <handle>` stops one running agent, under the same rules and
with the same care as starting:

  bash {BIN_DIR}/stop_run.sh <handle> "<the asker's <@U...> id, as it reached you>"

Addressed to you, it is a request and you carry it out. Not addressed to you, say what
you would stop and ask -- "About to stop <handle>, which is mid-cycle. Confirm and I
will." -- and act only on a yes.

It drains: the agent stops taking new work, finishes what is in flight and writes up
the cycle, so it takes a while and the agent posts its own stopped message when done.
Say that, rather than reporting it as already stopped. An immediate stop is
`kill_agent.sh --now <run_id>`, which is a person's call, not yours.

# Syncing the shared directory to collaborators

`{BIN_DIR}/sync_shared.sh` publishes the shared directory to a collaborator mirror
over Globus. It is OPTIONAL and may not be configured -- if its collection IDs are
still placeholders, say that rather than running it. When it is configured you may run
it on request:  bash {BIN_DIR}/sync_shared.sh
It prints the Globus task id. If it fails it exits non-zero and prints why; a common
cause is an expired Globus session, which a human has to renew with
`globus session update <site>`. Report the failure text as-is; do not retry in a loop.

Only when asked about syncing or the mirror -- NOT on every status answer, where it is
noise and costs a slow network call -- check freshness by comparing recent Globus
transfers against the most recent run's `ended_at` in `runs/<run_id>/meta.json`.

# What a reply looks like

One or two sentences of plain text, answering only what was asked. A Slack reply is
not a report: you often read several files to answer, and almost none of what you read
belongs in the message. Name the campaign when more than one could be meant.

The channel is shared by several campaigns and the people working on them. Findings,
figures, cycle write-ups and what a campaign plans next are given when someone asks
for them, or asks about that campaign. A general question -- "what is the status",
"anything running" -- is answered with what is running and nothing further.

Say what the files say, in their terms. Work that is written up as planned or designed
but not submitted has not been queued or scheduled, and no result exists for it.

If the files do not answer it, say so in one line and say what is missing, rather than
giving a number you are unsure of.

You cannot submit compute work, and must not edit results.jsonl, LOGBOOK.md or
JOURNAL.md. Your writes are: posting to Slack, appending a relayed question to a
board, and running sync_shared.sh when asked.
"""


def read_inbox():
    try:
        with open(INBOX) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[secretary] inbox read failed (ignored): {e}", flush=True)
        return ""


def read_seen():
    try:
        with open(STATE) as f:
            return f.read()
    except Exception:
        return ""


def write_seen(text):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    try:
        with open(STATE, "w") as f:
            f.write(text)
    except Exception as e:
        print(f"[secretary] state write failed (ignored): {e}", flush=True)


def beat():
    """Publish liveness for slack_to_board.py. Written every poll, so it goes stale
    within a couple of polls if this process dies OR wedges mid-answer -- either way
    the bridge should stop delivering here and fall back to the boards."""
    os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
    try:
        tmp = HEARTBEAT + ".tmp"
        with open(tmp, "w") as f:
            f.write(f"{time.time():.0f}\n")
        os.replace(tmp, HEARTBEAT)
    except Exception as e:
        print(f"[secretary] heartbeat write failed (ignored): {e}", flush=True)


def new_lines(seen, current):
    """Lines added since the last look. Falls back to the whole inbox if it was
    edited rather than appended to, since there is no clean 'new part' then."""
    old, new = seen.splitlines(), current.splitlines()
    if new[: len(old)] == old:
        return "\n".join(new[len(old) :]).strip()
    return current


def live_agents():
    """One line per agent with a recent heartbeat: the short handle people use to name
    it, and the run_id a relay has to be addressed to."""
    now = time.time()
    out = []
    for path in glob.glob(os.path.join(WORKSPACE_ROOT, "*", "runs", "*", "heartbeat")):
        try:
            with open(path) as f:
                if now - float(f.read().strip()) > AGENT_ALIVE_WITHIN:
                    continue
        except Exception:
            continue
        run_dir = os.path.dirname(path)
        run_id = os.path.basename(run_dir)
        campaign = os.path.basename(os.path.dirname(os.path.dirname(run_dir)))
        try:
            with open(os.path.join(run_dir, "meta.json")) as f:
                handle = json.load(f).get("handle") or run_id
        except Exception:
            handle = run_id
        out.append(f"{handle} -- campaign {campaign}, run_id {run_id}")
    return sorted(out)


_session_id = None  # this secretary's Claude session, for reopening it later


async def answer(client, text, agents):
    """Put one inbox message to the running conversation and print what comes back."""
    # States who is running, and nothing about what to do with that. An instruction
    # here is re-sent with every question, so anything actionable becomes a standing
    # order -- which is how "relay if it needs live reasoning" turned into relaying
    # answers it had already given.
    status = (
        "Research agents running:\n" + "\n".join(agents)
        if agents
        else "No research agent is running."
    )
    await client.query(
        status + "\n\nNew from Slack:\n\n" + text + "\n\nAnswer it, then stop."
    )
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text") and block.text.strip():
                    print(block.text, flush=True)
        elif isinstance(message, ResultMessage):
            global _session_id
            sid = getattr(message, "session_id", None)
            if sid and sid != _session_id:
                _session_id = sid
                try:
                    with open(
                        os.path.join(WORKSPACE_ROOT, "run", "secretary_session"), "w"
                    ) as f:
                        f.write(f"{sid}\n{WORKSPACE_ROOT}\n")
                except Exception as e:
                    print(
                        f"[secretary] session id not recorded (ignored): {e}",
                        flush=True,
                    )
            print(f"[turn end] {message.subtype}", flush=True)


async def main():
    once = "--once" in sys.argv
    print(
        f"Secretary watching {INBOX} (poll {POLL}s){' [once]' if once else ''}",
        flush=True,
    )
    if not os.path.isfile(NOTIFY_SCRIPT):
        print(
            f"[secretary] WARNING: {NOTIFY_SCRIPT} missing -- cannot post replies.",
            flush=True,
        )
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "Bash"],
        permission_mode="bypassPermissions",
        cwd=WORKSPACE_ROOT,
    )
    # One conversation for the life of the process, like the research agents in
    # agent.py. Follow-ups work because the thread is genuinely still there.
    async with ClaudeSDKClient(options=options) as client:
        while True:
            beat()
            inbox = read_inbox()
            fresh = new_lines(read_seen(), inbox) if inbox else ""
            if fresh:
                agents = live_agents()
                print(
                    f"\n--- answering ({', '.join(agents) or 'no agent running'}) "
                    f"---\n{fresh}\n-------------------------",
                    flush=True,
                )
                # Record BEFORE answering, so a failure cannot loop on the same message.
                write_seen(inbox)
                try:
                    await answer(client, fresh, agents)
                except Exception as e:
                    print(f"[secretary] answering failed (ignored): {e}", flush=True)
            if once:
                return
            await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Secretary stopped.", flush=True)
