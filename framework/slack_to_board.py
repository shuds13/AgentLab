#!/usr/bin/env python3
"""
Forward Slack messages addressed to the agents onto campaign announcement boards.

Flow:  someone @-mentions the bot in Slack
       -> this script copies that message into ANNOUNCEMENTS.md, tagged as from Slack
       -> a running agent reads the board (within ~2s) and replies with its notify tool
       -> the reply lands back in the same Slack channel

Only messages that mention the bot are forwarded. Messages posted BY bots are always
skipped, so the agents' own Slack posts can never come back round as new instructions.

State is the timestamp of the last Slack message handled, kept in
<WORKSPACE_DIR>/run/slack_last_ts. On the very first run it records "now" and forwards
nothing, so switching this on never dumps channel history onto the board.

Usage:
    python slack_to_board.py            # poll forever
    python slack_to_board.py --once     # one check, then exit (for cron)

Env:
    SLACK_CHANNEL          channel ID to read (required)
    SLACK_BOT_TOKEN_FILE   file holding the xoxb- bot token
    WORKSPACE_DIR             shared area holding ANNOUNCEMENTS.md
    SLACK_FETCH_POLL       seconds between checks (default 30)
"""

import json
import glob
import os
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# One bridge serves the whole lab. WORKSPACE_ROOT holds one directory per campaign;
# each campaign has its own ANNOUNCEMENTS.md that its agent reads between rounds.
WORKSPACE_ROOT = os.path.abspath(os.environ.get(
    "WORKSPACE_ROOT", os.path.join(SCRIPT_DIR, "..", "workspace")))
STATE = os.path.join(WORKSPACE_ROOT, "run", "slack_last_ts")
CHANNEL = os.environ.get("SLACK_CHANNEL", "")
TOKEN_FILE = os.environ.get("SLACK_BOT_TOKEN_FILE",
                            os.path.expanduser("~/.slack_bot_token"))
POLL = int(os.environ.get("SLACK_FETCH_POLL", "30"))
# Plain-text fallback for a mention typed without Slack autocomplete.
BOT_NAME = os.environ.get("SLACK_BOT_NAME", "@cas_agent")


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


def forward(messages, me):
    """Append agent-directed Slack messages to the board, oldest first."""
    lines = []
    for m in reversed(messages):          # Slack returns newest first
        if m.get("bot_id") or m.get("subtype"):
            continue                      # never echo bot posts back at the agents
        text = m.get("text", "").strip()
        if not text:
            continue
        if f"<@{me}>" not in text and BOT_NAME not in text:
            continue                      # not addressed to the agents
        text = text.replace(f"<@{me}>", "").strip()
        lines.append(f"[from Slack -- reply with the notify tool] {text}")
    if not lines:
        return 0
    campaigns = sorted(d for d in glob.glob(os.path.join(WORKSPACE_ROOT, "*"))
                       if os.path.isdir(d) and os.path.basename(d) != "run")
    if not campaigns:
        print(f"no campaigns under {WORKSPACE_ROOT}; dropping {len(lines)} message(s)",
              flush=True)
        return 0
    delivered = 0
    for line in lines:
        # A message naming a campaign goes to that one; otherwise to all of them.
        named = [d for d in campaigns if os.path.basename(d).lower() in line.lower()]
        for d in (named or campaigns):
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "ANNOUNCEMENTS.md"), "a") as f:
                f.write(line + "\n")
            delivered += 1
        print(f"forwarded to {len(named) or len(campaigns)} campaign(s):", line,
              flush=True)
    return delivered


def check(token, me):
    oldest = read_state()
    if not oldest:
        # First run: start from now so existing history is not forwarded.
        now = f"{time.time():.6f}"
        write_state(now)
        print(f"first run -- starting from now ({now}); nothing forwarded", flush=True)
        return
    resp = slack_get("conversations.history", token, channel=CHANNEL,
                     oldest=oldest, limit=50)
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
    print(f"Watching Slack channel {CHANNEL} as {who.get('user')} ({me}); "
          f"campaigns under {WORKSPACE_ROOT}", flush=True)

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
