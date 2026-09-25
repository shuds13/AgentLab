#!/bin/bash
# OPTIONAL. Forwards Slack messages that @-mention the bot onto the announcements
# board. Needs a bot token with channels:history for a public channel, or
# groups:history for a private one -- see AGENTS.md.
# Settings come from lab.yaml and notifiers/slack/slack.env -- see docs/settings.md.
# Run from this dir: ./run_slack_bridge.sh
set -euo pipefail
cd "$(dirname "$0")"
umask 002
. ../framework/settings.sh
export WORKSPACE_ROOT="$(cd ../workspace && pwd)"   # all campaigns
export SLACK_CHANNEL="${SLACK_CHANNEL:-}"                             # channel ID to read
export SLACK_BOT_TOKEN_FILE="${SLACK_BOT_TOKEN_FILE:-}"               # notifiers/slack/slack.env
export SLACK_BOT_NAME="${SLACK_BOT_NAME:-@cas_agent}"                 # plain-text mention fallback
export SLACK_READ_ALL="${SLACK_READ_ALL:-false}"                      # forward every message, not only mentions
# 5s, not 30: this poll dominates end-to-end latency, and conversations.history is
# Slack Tier 3 (50+ req/min), so 12/min leaves plenty of headroom.
export SLACK_FETCH_POLL="${SLACK_FETCH_POLL:-5}"
echo "[run] slack bridge -> campaign boards under $WORKSPACE_ROOT"
python -u "../notifiers/${NOTIFIER:-slack}/reader.py"
