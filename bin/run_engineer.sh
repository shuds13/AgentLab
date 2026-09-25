#!/usr/bin/env bash
# OPTIONAL. This lab's own repository, worked on from a Slack channel of its own.
# Settings come from notifiers/slack/slack.env; the channel and webhook here are its own, so
# it does not share either with the secretary.
# Run from this dir: ./run_engineer.sh
set -euo pipefail
cd "$(dirname "$0")"
umask 002
export PATH="$HOME/.local/bin:$PATH"
. ../framework/settings.sh
export LAB_DIR="$(cd .. && pwd)"

# Its own channel, webhook and queue -- sharing any of them with the secretary would
# put engineering questions in front of the wrong reader.
export SLACK_CHANNEL="${ENGINEER_SLACK_CHANNEL:-}"
export SLACK_WEBHOOK_FILE="${ENGINEER_WEBHOOK_FILE:-}"
export SLACK_INBOX="$LAB_DIR/workspace/run/engineer_inbox.md"
export SLACK_STATE="$LAB_DIR/workspace/run/engineer_last_ts"
export NOTIFY_PREFIX="${NOTIFY_PREFIX:-engineer}"
export SLACK_READ_ALL="${SLACK_READ_ALL:-true}"
export SLACK_ALLOW="${ENGINEER_ALLOW:-}"   # Slack user ids that may instruct it; empty: anyone
export ENGINEER_BRANCH="${ENGINEER_BRANCH:-}"
export ENGINEER_POLL="${ENGINEER_POLL:-5}"
# off: a fresh conversation. last: the one it had before. compact: that one, summarised
# down first. A session id works here too, to pick up a particular conversation.
export RESUME_SESSION="${RESUME_SESSION:-${ENGINEER_RESUME:-}}"
if [ "$RESUME_SESSION" = off ]; then RESUME_SESSION=""; fi

[ -n "$SLACK_CHANNEL" ] || { echo "set ENGINEER_SLACK_CHANNEL to the channel it reads" >&2; exit 2; }

# The bridge for that channel, and the engineer that reads what it delivers. One
# process would be simpler, but the bridge is the same one the secretary uses.
echo "[run] engineer <- Slack $SLACK_CHANNEL, repository $LAB_DIR"
python -u "../notifiers/${NOTIFIER:-slack}/reader.py" &
BRIDGE=$!
trap 'kill $BRIDGE 2>/dev/null' EXIT
python -u ../framework/engineer.py
