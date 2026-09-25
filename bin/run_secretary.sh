#!/bin/bash
# OPTIONAL. Answers board questions so the agents are not interrupted.
# Settings come from lab.yaml and notifiers/slack/slack.env -- see docs/settings.md.
# Run from this dir: ./run_secretary.sh
set -euo pipefail
cd "$(dirname "$0")"
umask 002
export PATH="$HOME/.local/bin:$PATH"
. ../framework/settings.sh
export WORKSPACE_ROOT="$(cd ../workspace && pwd)"   # all campaigns
export SLACK_WEBHOOK_FILE="${SLACK_WEBHOOK_FILE:-}"   # notifiers/slack/slack.env; empty: no Slack
export NOTIFY_SCRIPT="${NOTIFY_SCRIPT:-}"   # a transport other than notifiers/$NOTIFIER
export NOTIFY_PREFIX="${NOTIFY_PREFIX:-secretary}"   # who is speaking, in the transcript and onward
export SLACK_CAMPAIGNS="${SLACK_CAMPAIGNS:-}"   # campaigns the secretary may start and stop; empty means none
export SECRETARY_POLL="${SECRETARY_POLL:-5}"       # also how often the heartbeat the bridge reads is rewritten
echo "[run] secretary -> campaign boards under $WORKSPACE_ROOT"
python -u ../framework/secretary.py
