#!/bin/bash
# Launch the agent for this campaign. Run from this directory, inside tmux:
#   tmux new -s agentlab && ./run.sh
#
# Stop:  ../../bin/kill_agent.sh --drain <run_id>
# List:  ../../bin/list_agents.sh --all
set -euo pipefail
cd "$(dirname "$0")"

# Environment providing the claude CLI and the Python packages in requirements.txt.
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate agentlab
export PATH="$HOME/.local/bin:$PATH"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

export MAX_SUBMITS=20
export MAX_RUNTIME=21600

# export SLACK_WEBHOOK_FILE="$HOME/.slack_webhook_thislab"   # overrides notifiers/slack/slack.env
export NOTIFY_START=true
export NOTIFY_DAILY=true
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME"
python -u ../../framework/agent.py "$@"
