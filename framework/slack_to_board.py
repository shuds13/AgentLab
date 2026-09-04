#!/usr/bin/env python3
"""
Forward Slack messages addressed to the agents to whoever should answer them.

Where a message goes depends on whether the secretary is running:

  secretary up    -> <workspace>/run/slack_inbox.md, read by the secretary alone. It
                     answers, and relays to a campaign's board when a running agent's
                     live reasoning is needed. ONE answer, however many agents run.
  secretary down  -> every campaign's ANNOUNCEMENTS.md, as before. Each running agent
                     reads its own board and replies, so N campaigns means N replies --
                     noisy, but better than a question nobody answers.

That split is why the boards are not the default: a board is a broadcast to whichever
agent reads it, so a question posted to all of them is answered once per campaign.

Only messages that mention the bot are forwarded, unless SLACK_READ_ALL is set: then
every human message in the channel goes to the secretary, which decides for itself
which ones are for it. Read-all needs a reader that can judge, so when the secretary is
down the mention filter applies as usual rather than putting chatter on every board.
Messages posted BY bots are always skipped, so the agents' own Slack posts can never
come back round as new instructions.

State is the timestamp of the last Slack message handled, kept in
<WORKSPACE_DIR>/run/slack_last_ts. On the very first run it records "now" and forwards
nothing, so switching this on never dumps channel history onto the board.

Usage:
    python slack_to_board.py            # poll forever
    python slack_to_board.py --once     # one check, then exit (for cron)

Env:
    SLACK_CHANNEL          channel ID to read (required)
    SLACK_BOT_TOKEN_FILE   file holding the xoxb- bot token
    WORKSPACE_ROOT         holds one directory per campaign, plus run/
    SLACK_FETCH_POLL       seconds between checks (default 5)
    SECRETARY_ALIVE_WITHIN heartbeat age that still counts as up (default 60)
    SLACK_READ_ALL         1/true to forward every message, not only mentions
    SLACK_INBOX            where to deliver, when this bridge serves a reader of its
                           own rather than the secretary
    SLACK_READER_HEARTBEAT that reader's liveness file
"""

import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# One bridge serves the whole lab. WORKSPACE_ROOT holds one directory per campaign;
# each campaign has its own ANNOUNCEMENTS.md that its agent reads between rounds.
WORKSPACE_ROOT = os.path.abspath(
    os.environ.get("WORKSPACE_ROOT", os.path.join(SCRIPT_DIR, "..", "workspace"))
)
STATE = os.environ.get("SLACK_STATE") or os.path.join(
    WORKSPACE_ROOT, "run", "slack_last_ts"
)
INBOX = os.environ.get("SLACK_INBOX") or os.path.join(
    WORKSPACE_ROOT, "run", "slack_inbox.md"
)
HEARTBEAT = os.environ.get("SLACK_READER_HEARTBEAT") or os.path.join(
    WORKSPACE_ROOT, "run", "secretary_heartbeat"
)
# s; a secretary heartbeat fresher than this means it is up and owns Slack questions.
# It rewrites the file every poll (default 5s), so this tolerates many missed beats.
SECRETARY_ALIVE_WITHIN = int(os.environ.get("SECRETARY_ALIVE_WITHIN", "60"))
CHANNEL = os.environ.get("SLACK_CHANNEL", "")
TOKEN_FILE = os.environ.get(
    "SLACK_BOT_TOKEN_FILE", os.path.expanduser("~/.slack_bot_token")
)
POLL = int(os.environ.get("SLACK_FETCH_POLL", "5"))
# Plain-text fallback for a mention typed without Slack autocomplete.
BOT_NAME = os.environ.get("SLACK_BOT_NAME", "@cas_agent")
READ_ALL = os.environ.get("SLACK_READ_ALL", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def slack_get(method, token, **params):
    url = "https://slack.com/api/" + method + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_state():
    try:
        with open(STATE) as f:
            return f.read().strip()
    except Exception:
        return ""


def write_state(ts):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        f.write(ts)


# A bridge told where to deliver serves one reader and nothing else: its messages are
# that reader's conversation, and a campaign board is not a place to put them.
DEDICATED = bool(os.environ.get("SLACK_INBOX"))

# Who may instruct the reader: Slack user ids, space separated. Empty means anyone in
# the channel, which is right when the channel is only the people who may drive it.
# Enforced here and not in the reader's prompt -- a rule the reader is merely told
# about is one a message can argue its way past.
ALLOW = set(os.environ.get("SLACK_ALLOW", "").split())


def secretary_up():
    """Whether the secretary is alive and should get the questions. Its heartbeat goes
    stale if the process dies OR wedges mid-answer; either way we fall back to the
    boards rather than dropping messages into an inbox nobody is reading."""
    try:
        with open(HEARTBEAT) as f:
            return time.time() - float(f.read().strip()) <= SECRETARY_ALIVE_WITHIN
    except FileNotFoundError:
        return False
    except Exception as e:
        print(
            f"[slack] heartbeat read failed, assuming down (ignored): {e}", flush=True
        )
        return False


def forward(messages, me):
    """Deliver agent-directed Slack messages, oldest first: to the secretary if it is
    up, to every campaign board if it is not."""
    lines = []
    read_all = READ_ALL and (DEDICATED or secretary_up())
    for m in reversed(messages):  # Slack returns newest first
        if m.get("bot_id") or m.get("subtype"):
            continue  # never echo bot posts back at the agents
        text = m.get("text", "").strip()
        if not text:
            continue
        if ALLOW and m.get("user") not in ALLOW:
            print(
                f"ignored, not on SLACK_ALLOW: <@{m.get('user')}>: {text}", flush=True
            )
            continue
        addressed = f"<@{me}>" in text or BOT_NAME in text
        if not addressed and not read_all:
            continue  # not addressed to the agents
        text = text.replace(f"<@{me}>", "").strip()
        # The author's Slack id travels with the message: it is what records who asked
        # for a run, and Slack renders it as their name when it is quoted back.
        who = f"<@{m['user']}>" if m.get("user") else "someone"
        tag = (
            "reply with the notify tool"
            if addressed
            else "overheard, not addressed to you"
        )
        # One reader, one channel: it is talking to whoever is there, so the author's
        # id is noise. The shared bridge needs it to say who asked for what.
        lines.append(
            f"[from Slack -- {tag}] " + (text if DEDICATED else f"{who}: {text}")
        )
    if not lines:
        return 0
    if DEDICATED or secretary_up():
        os.makedirs(os.path.dirname(INBOX), exist_ok=True)
        with open(INBOX, "a") as f:
            f.write("\n".join(lines) + "\n")
        where = "reader" if DEDICATED else "secretary"
        for line in lines:
            print(f"forwarded to {where}:", line, flush=True)
        return len(lines)
    campaigns = sorted(
        d
        for d in glob.glob(os.path.join(WORKSPACE_ROOT, "*"))
        if os.path.isdir(d) and os.path.basename(d) != "run"
    )
    if not campaigns:
        print(
            f"no campaigns under {WORKSPACE_ROOT}; dropping {len(lines)} message(s)",
            flush=True,
        )
        return 0
    delivered = 0
    for line in lines:
        # A message naming a campaign goes to that one; otherwise to all of them.
        named = [d for d in campaigns if os.path.basename(d).lower() in line.lower()]
        for d in named or campaigns:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "ANNOUNCEMENTS.md"), "a") as f:
                f.write(line + "\n")
            delivered += 1
        print(
            f"forwarded to {len(named) or len(campaigns)} campaign(s):",
            line,
            flush=True,
        )
    return delivered


def check(token, me):
    oldest = read_state()
    if not oldest:
        # First run: start from now so existing history is not forwarded.
        now = f"{time.time():.6f}"
        write_state(now)
        print(f"first run -- starting from now ({now}); nothing forwarded", flush=True)
        return
    resp = slack_get(
        "conversations.history", token, channel=CHANNEL, oldest=oldest, limit=50
    )
    if not resp.get("ok"):
        print(f"[slack] read failed (ignored): {resp.get('error')}", flush=True)
        return
    messages = resp.get("messages", [])
    if not messages:
        return
    forward(messages, me)
    # Advance past everything seen, whether or not it was forwarded.
    write_state(max(m["ts"] for m in messages))


def main():
    if not CHANNEL:
        sys.exit("SLACK_CHANNEL is not set (the channel ID to read).")
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
    except Exception as e:
        sys.exit(f"cannot read bot token from {TOKEN_FILE}: {e}")

    who = slack_get("auth.test", token)
    if not who.get("ok"):
        sys.exit(f"token rejected by Slack: {who.get('error')}")
    me = who["user_id"]
    print(
        f"Watching Slack channel {CHANNEL} as {who.get('user')} ({me}); "
        f"campaigns under {WORKSPACE_ROOT}",
        flush=True,
    )

    once = "--once" in sys.argv
    while True:
        try:
            check(token, me)
        except Exception as e:
            print(f"[slack] check failed (ignored): {e}", flush=True)
        if once:
            return
        time.sleep(POLL)


if __name__ == "__main__":
    main()
