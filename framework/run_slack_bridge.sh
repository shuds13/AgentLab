#!/bin/bash
# OPTIONAL. Forwards Slack messages that @-mention the bot onto the announcements
# board. Needs a bot token with channels:history -- see AGENTS.md.
# Run from this dir: ./run_slack_bridge.sh
set -euo pipefail
cd "$(dirname "$0")"
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate cas
umask 002
export WORKSPACE_ROOT="$(cd ../workspace && pwd)"   # all campaigns
export SLACK_CHANNEL=C0000000000                  # channel ID to read
export SLACK_BOT_TOKEN_FILE="$HOME/.slack_bot_token"
export SLACK_BOT_NAME="@cas_agent"                # plain-text mention fallback
export SLACK_FETCH_POLL=30
echo "[run] slack bridge -> campaign boards under $WORKSPACE_ROOT"
python -u slack_to_board.py
