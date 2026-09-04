#!/usr/bin/env python3
"""
The engineer: this lab's own repository, worked on from Slack.

The secretary answers questions about campaigns from their records. This answers
questions about the framework, and changes it -- reading the code, editing it, running
things, committing. Same shape, different remit and different tools, and it works in
the repository rather than the workspace.

It is a person's session, not an autonomous one: it does what the channel asks and
stops. What keeps that safe is narrow: it commits only on a branch it made, and it
never pushes. Anything that leaves this machine stays a human decision.

One continuing conversation for the life of the process, so a channel reads as a
conversation rather than a series of strangers. Start it from an earlier one with
RESUME_SESSION to carry on where a previous day left off.

Env:
    SLACK_INBOX          where the bridge delivers (required)
    ENGINEER_HEARTBEAT   liveness the bridge reads, so it knows to deliver here
    ENGINEER_POLL        seconds between inbox checks (default 5)
    ENGINEER_BRANCH      a branch to keep its commits on, created if missing. Empty
                         (the default) leaves the repository where it is
    RESUME_SESSION       a session id to continue instead of starting fresh, or
                         `last` for the one this engineer used before, or `compact`
                         for that one summarised down before it carries on
    NOTIFY_SCRIPT        how it replies (default framework/slack_notify.sh)
"""

import asyncio
import os
import subprocess
import sys
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
INBOX = os.environ.get("SLACK_INBOX") or os.path.join(
    LAB_DIR, "workspace", "run", "engineer_inbox.md"
)
STATE = os.path.join(os.path.dirname(INBOX), "engineer_seen.txt")
HEARTBEAT = os.environ.get("ENGINEER_HEARTBEAT") or os.path.join(
    os.path.dirname(INBOX), "engineer_heartbeat"
)
SESSION_FILE = os.path.join(os.path.dirname(INBOX), "engineer_session")
POLL = int(os.environ.get("ENGINEER_POLL", "5"))
BRANCH = (os.environ.get("ENGINEER_BRANCH") or "").strip()
RESUME_SESSION = (os.environ.get("RESUME_SESSION") or "").strip()
COMPACT_FIRST = RESUME_SESSION.lower() == "compact"
NOTIFY_SCRIPT = os.environ.get("NOTIFY_SCRIPT") or os.path.join(
    SCRIPT_DIR, "slack_notify.sh"
)

SYSTEM_PROMPT = f"""You are the engineer for AgentLab, the framework in {LAB_DIR}, and
you work on it from a Slack channel. Someone types there; you answer, and change the
repository when that is what they asked for.

Read `{LAB_DIR}/AGENTS.md` before your first substantive answer. It says what this
framework is and how it is meant to be set up, and it is not loaded for you.

# What you are working on

The framework: `framework/`, `methods/`, `systems/`, `docs/`, and the tracked example
campaigns. A campaign that is not an example belongs to whoever is running it -- read
one to understand a problem, but it is not yours to change or to commit.

# Where the boundary is

You may edit, run and commit. You may not push, and you may not open a merge request:
what leaves this machine is decided by a person at a terminal. If asked to push, say
that and stop.

Commit on whatever branch the repository is on; the person you are talking to chose
it. Do not create branches unless asked.

Commit when the change is finished and the person asked for it, not as you go. A
commit message says what changed, in one line, and carries no attribution.

# How to answer

Slack, not a terminal: a few lines. Say what you did and what it means, not how you
found out. Paste a diff only when someone asks for one. Where a change deserves
discussion before it is made, describe it and wait -- a channel is a conversation, and
the person on the other end may be on a phone.

You cannot see what you have not read. Before answering about behaviour, read the code
that decides it; before saying something works, run it.

Reply by running:  bash {NOTIFY_SCRIPT} "your message"

Every message you are given is from a person in the channel. Answer each one.
"""


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return default


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[engineer] could not write {path} (ignored): {e}", flush=True)


def beat():
    """Liveness for the bridge, rewritten every poll: if this dies, the bridge should
    stop delivering into an inbox nobody reads."""
    _write(HEARTBEAT, f"{time.time():.0f}\n")


def new_lines(seen, current):
    old, new = seen.splitlines(), current.splitlines()
    if new[: len(old)] == old:
        return "\n".join(new[len(old) :]).strip()
    return current


def last_session():
    """The session this engineer used before, from the record it writes each turn.
    The record carries the checkout it belonged to, so a second lab on the same
    machine does not pick up this one's conversation."""
    lines = _read(SESSION_FILE).splitlines()
    if len(lines) >= 2 and os.path.abspath(lines[1].strip()) == os.path.abspath(
        LAB_DIR
    ):
        return lines[0].strip()
    return ""


def resume_session():
    """Which session to carry on, if any. `last` and `compact` both mean the recorded
    one; they differ in what happens once it is loaded, not in which it is."""
    if RESUME_SESSION.lower() in ("last", "compact"):
        return last_session()
    return RESUME_SESSION


async def compact(client):
    """Summarise the conversation before taking any of the day's questions, so a long
    history costs a summary rather than the whole transcript from here on. The CLI
    handles it and records it in the session; a session too short to be worth it says
    so and nothing is lost."""
    await client.query("/compact")
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text") and block.text.strip():
                    print(f"[compact] {block.text.strip()[:200]}", flush=True)
        elif isinstance(message, ResultMessage):
            # Compaction can leave the conversation on a new session id, and the record
            # has to name the one to come back to tomorrow.
            sid = getattr(message, "session_id", None)
            if sid:
                _write(SESSION_FILE, f"{sid}\n{LAB_DIR}\n")
            return


def on_branch():
    """Put the repository on a branch of its own, if the lab asked for one. Off by
    default: this is someone working on their own repository from a chat window, and
    switching the branch under a checkout they are also using causes more trouble than
    it prevents. Returns what happened, or None if git would not cooperate."""
    if not BRANCH:
        return "wherever the repository already is"
    try:
        current = subprocess.run(
            ["git", "-C", LAB_DIR, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.strip()
        if current == BRANCH:
            return f"on {BRANCH}"
        made = subprocess.run(
            ["git", "-C", LAB_DIR, "checkout", "-B", BRANCH],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if made.returncode != 0:
            return None
        return f"switched from {current} to {BRANCH}"
    except Exception:
        return None


async def answer(client, text):
    await client.query(text + "\n\nAnswer it, then stop.")
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text") and block.text.strip():
                    print(block.text, flush=True)
        elif isinstance(message, ResultMessage):
            sid = getattr(message, "session_id", None)
            if sid:
                _write(SESSION_FILE, f"{sid}\n{LAB_DIR}\n")
            print(f"[turn end] {message.subtype}", flush=True)


async def main():
    once = "--once" in sys.argv
    branch = on_branch()
    print(
        f"Engineer watching {INBOX} (poll {POLL}s){' [once]' if once else ''}",
        flush=True,
    )
    print(
        f"branch: {branch or 'not a git repository -- commits will fail'}", flush=True
    )
    resume = resume_session()
    if RESUME_SESSION and not resume:
        print(
            f"no earlier session recorded for {LAB_DIR} -- starting fresh", flush=True
        )
    elif resume:
        print(
            f"resuming session {resume}"
            + (", compacting first" if COMPACT_FIRST else ""),
            flush=True,
        )
    if not os.path.isfile(NOTIFY_SCRIPT):
        print(
            f"[engineer] WARNING: {NOTIFY_SCRIPT} missing -- cannot reply.", flush=True
        )

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write", "Edit"],
        permission_mode="bypassPermissions",
        cwd=LAB_DIR,
        # Resumed, not forked: this is one conversation picked up again, so it carries
        # on in the same transcript rather than starting a copy each time.
        **({"resume": resume} if resume else {}),
    )
    async with ClaudeSDKClient(options=options) as client:
        if resume and COMPACT_FIRST:
            await compact(client)
        while True:
            beat()
            inbox = _read(INBOX)
            fresh = new_lines(_read(STATE), inbox) if inbox else ""
            if fresh:
                print(f"\n--- answering ---\n{fresh}\n-----------------", flush=True)
                # Recorded before answering, so a failure cannot loop on one message.
                _write(STATE, inbox)
                try:
                    await answer(client, fresh)
                except Exception as e:
                    print(f"[engineer] answering failed (ignored): {e}", flush=True)
            if once:
                return
            await asyncio.sleep(POLL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Engineer stopped.", flush=True)
