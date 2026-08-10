#!/usr/bin/env python3
"""
Secretary: first responder for the shared announcements board.

It answers questions about the work so the research agents are not interrupted --
an agent answering costs a turn of its long-running context and breaks its wait for
compute jobs, and it may be minutes into a turn before it even looks at the board.
The secretary replies in tens of seconds and is free when idle.

It answers anything recorded in the shared files: results, what a cycle
concluded, which runs happened or are happening. What it cannot know is a running
agent's live reasoning -- what it is doing right now and why. Those it appends to
the board for the agent (prefixed RELAY_PREFIX) and says so, rather than guessing.
When no agent is running there is nothing to relay to, and it says that too.

Each answer runs in its own throwaway context (a one-shot query), so every fact is
re-read from the files and cannot go stale. Only a short window of recent exchanges
is carried across, enough for follow-up questions.

It is read-only over the science: it cannot submit compute work and must not edit
results.jsonl, LOGBOOK.md or JOURNAL.md. Its writes are the Slack post, a relayed
question appended to the board, and running sync_shared.sh when asked -- it also
reports how stale the collaborator mirror is alongside any status answer.

Usage:
    python secretary.py          # poll forever
    python secretary.py --once   # answer anything new, then exit (for cron)
"""

import asyncio
import glob
import os
import sys
import time

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# One secretary serves the whole lab. WORKSPACE_ROOT holds one directory per campaign,
# each with its own ANNOUNCEMENTS.md, results.jsonl, LOGBOOK.md and JOURNAL.md.
WORKSPACE_ROOT = os.path.abspath(os.environ.get(
    "WORKSPACE_ROOT", os.path.join(SCRIPT_DIR, "..", "workspace")))


def campaigns():
    """Campaign directories, newest board activity first."""
    ds = [d for d in glob.glob(os.path.join(WORKSPACE_ROOT, "*"))
          if os.path.isdir(d) and os.path.basename(d) != "run"]
    return sorted(ds, key=lambda d: os.path.basename(d))


def board_path(campaign_dir):
    return os.path.join(campaign_dir, "ANNOUNCEMENTS.md")


def state_path(campaign_dir):
    return os.path.join(campaign_dir, "run", "secretary_seen.txt")
POLL = int(os.environ.get("SECRETARY_POLL", "10"))          # s between board checks
AGENT_ALIVE_WITHIN = int(os.environ.get("AGENT_ALIVE_WITHIN", "300"))  # s; fresher heartbeat = agent is up
# Marks a question the secretary has handed to a running agent. The secretary skips
# these when reading the board (they are its own writing, and are for the agent).
RELAY_PREFIX = "[for the research agent"
# Noted on the board after the secretary replies, so a research agent reading the
# board can see the question is dealt with and does not answer it a second time.
ANSWERED_PREFIX = "[answered by secretary]"
NOTIFY_SCRIPT = os.path.join(SCRIPT_DIR, "slack_notify.sh")

SYSTEM_PROMPT = f"""You are the secretary for a collaborative agentic search
workflow. Research agents run on compute nodes and coordinate through shared files.
You are the first responder: you answer questions so the agents are not interrupted,
because answering costs them a turn of their own long-running context and breaks
their wait for compute jobs.

Answer from the files in your working directory, which is one campaign's workspace:
- `results.jsonl` -- one JSON object per result.
- `LOGBOOK.md` -- terse running notes, append-only, newest at the end.
- `JOURNAL.md` -- written-up cycles, one section per cycle.
- `runs/<run_id>/meta.json` -- per-run metadata: host, pid, model, status,
  stop_reason, and which prompt file the run used.

These files are large. Use Grep and targeted Read (offset/limit); do not read them
whole. For which agents ran or are running, use `{SCRIPT_DIR}/list_agents.sh`.
Always re-read the files for facts -- the science moves, so never answer a factual
question from something you were told earlier in this conversation.

Post your reply by running:  bash {NOTIFY_SCRIPT} "your message"

# What you answer, and what only a running agent can

Answer yourself anything about recorded STATE or HISTORY: results, what a cycle
concluded, which runs happened, what is running now.

A running agent's live REASONING is not in the files -- what it is working on right
now, why it chose a region, what it plans next. Only that agent knows. When a
question needs that AND an agent is running (you are told below whether one is):
1. Append the question to ANNOUNCEMENTS.md on its own line, prefixed exactly:
   [for the research agent -- reply with the notify tool]
   Do this with a shell append (>>). Never rewrite or delete existing board lines.
2. Post a short Slack note saying you have passed it to the agent and it will reply
   when it finishes its current step.
Do not ask permission first -- relay it and say you have.
If NO agent is running, say so and answer as much as you can from the files instead.

# Syncing the shared directory to collaborators

`{SCRIPT_DIR}/sync_shared.sh` publishes the shared directory to a collaborator mirror
over Globus. It is OPTIONAL and may not be configured for this workflow -- if its
collection IDs are still placeholders, say that rather than running it. When it is
configured you may run it on request:  bash {SCRIPT_DIR}/sync_shared.sh
It prints the Globus task id. If it fails it exits non-zero and prints why; a common
cause is an expired Globus session, which a human has to renew with
`globus session update <site>`. Report the failure text as-is; do not retry in a loop.

Report sync freshness whenever you give a status answer, so a stale mirror is noticed
without anyone having to ask. Compare:
- recent Globus transfers (newest first); the mirror push is the one labelled
  "push shared dir ...":
  globus task list --limit 10 --format json --jq "DATA[].{{label:label,req:request_time,status:status}}"
- against the most recent run's `ended_at` (or its heartbeat, if still running) in
  `runs/<run_id>/meta.json`.
Say plainly whether the mirror has been synced since the latest run's work, e.g.
"synced 20 min ago, after the run finished" or "not synced since the run ended 3h ago".

Keep replies to a few lines, plain text, answer first. If you cannot answer from the
files, say so and say what is missing -- never invent numbers.

You cannot submit compute work, and must not edit results.jsonl, LOGBOOK.md or
JOURNAL.md. Your writes are: posting to Slack, appending a relayed question to the
board, and running sync_shared.sh when asked.
"""


def read_board(board=None):
    try:
        with open(board) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[secretary] board read failed (ignored): {e}", flush=True)
        return ""


def read_seen(state):
    try:
        with open(state) as f:
            return f.read()
    except Exception:
        return ""


def write_seen(text, state):
    os.makedirs(os.path.dirname(state), exist_ok=True)
    try:
        with open(state, "w") as f:
            f.write(text)
    except Exception as e:
        print(f"[secretary] state write failed (ignored): {e}", flush=True)


def new_lines(seen, current):
    """Lines added since the last look. Falls back to the whole board if it was
    edited rather than appended to, since there is no clean 'new part' then."""
    old, new = seen.splitlines(), current.splitlines()
    if new[:len(old)] == old:
        return "\n".join(new[len(old):]).strip()
    return current


def mark_answered(handled, board):
    """Note on the board that these questions have been answered, so an agent reading
    the board does not answer them again.

    Appends rather than rewriting the handled lines in place: the Slack bridge appends
    to this same file, and rewriting it whole could silently drop a message that
    arrived mid-write."""
    lines = [l for l in handled.splitlines()
             if l.strip() and not l.startswith(ANSWERED_PREFIX)]
    if not lines:
        return
    try:
        with open(board, "a") as f:
            for l in lines:
                f.write(f"{ANSWERED_PREFIX} {l.strip()}\n")
    except Exception as e:
        print(f"[secretary] could not mark answered (ignored): {e}", flush=True)


def live_agent(campaign_dir):
    """Name of a research agent with a recent heartbeat in this campaign, or None. This is what keeps
    the secretary and the agents from both answering."""
    now = time.time()
    for path in glob.glob(os.path.join(campaign_dir, "runs", "*", "heartbeat")):
        try:
            with open(path) as f:
                beat = float(f.read().strip())
        except Exception:
            continue
        if now - beat <= AGENT_ALIVE_WITHIN:
            return os.path.basename(os.path.dirname(path))
    return None


RECENT = []          # last few (question, reply) pairs, for follow-ups
RECENT_KEEP = 4


def _recent_block():
    if not RECENT:
        return ""
    lines = ["\n# Recent exchanges (context for follow-ups only -- re-read the files "
             "for any fact)"]
    for q, a in RECENT:
        lines.append(f"Q: {q}\nYou: {a}")
    return "\n".join(lines) + "\n"


async def answer(text, agent_running, campaign_dir):
    """Answer one board message. Runs in its own throwaway context, so every factual
    answer re-reads current files; only a short window of recent exchanges is carried
    across, which is enough for follow-ups without going stale."""
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "Bash"],
        permission_mode="bypassPermissions",
        cwd=campaign_dir,
    )
    status = (f"A research agent IS running ({agent_running}); relay questions that "
              f"need its live reasoning." if agent_running else
              "NO research agent is running; nothing can be relayed.")
    prompt = (_recent_block() + "\n" + status +
              "\n\nNew on the announcements board:\n\n" + text +
              "\n\nAnswer it, then stop.")
    said = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text") and block.text.strip():
                    print(block.text, flush=True)
                    said.append(block.text.strip())
    RECENT.append((text, " ".join(said)[:600]))
    del RECENT[:-RECENT_KEEP]


async def main():
    once = "--once" in sys.argv
    print(f"Secretary watching campaigns under {WORKSPACE_ROOT} (poll {POLL}s)"
          f"{' [once]' if once else ''}", flush=True)
    if not os.path.isfile(NOTIFY_SCRIPT):
        print(f"[secretary] WARNING: {NOTIFY_SCRIPT} missing -- cannot post replies.",
              flush=True)
    while True:
        for campaign_dir in campaigns():
            name = os.path.basename(campaign_dir)
            board, state = board_path(campaign_dir), state_path(campaign_dir)
            text = read_board(board)
            fresh = new_lines(read_seen(state), text) if text else ""
            # Drop our own relays: appending one changes the board, so without this the
            # secretary would read it back as a new message and answer itself forever.
            fresh = "\n".join(l for l in fresh.splitlines()
                               if not l.startswith(RELAY_PREFIX)
                               and not l.startswith(ANSWERED_PREFIX)).strip()
            if not fresh:
                continue
            alive = live_agent(campaign_dir)
            print(f"\n--- {name}: answering ({alive or 'no agent running'}) ---\n"
                  f"{fresh}\n-------------------------", flush=True)
            # Record BEFORE answering, so a failure cannot loop on the same message.
            write_seen(text, state)
            try:
                await answer(fresh, alive, campaign_dir)
                mark_answered(fresh, board)
            except Exception as e:
                print(f"[secretary] {name}: answering failed (ignored): {e}", flush=True)
            # Answering may have appended a relay and a marker; mark those seen too.
            write_seen(read_board(board), state)
        if once:
            return
        await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Secretary stopped.", flush=True)
