#!/bin/bash
# Launch the agent for this campaign. Run from this directory, inside tmux:
#   tmux new -s agentlab && ./run.sh
#
# Stop:  ../../bin/kill_agent.sh --drain <run_id>
# List:  ../../bin/list_agents.sh --all
set -euo pipefail
cd "$(dirname "$0")"

# The environment comes from lab.yaml's `activate`, by way of settings.sh below.
export PATH="$HOME/.local/bin:$PATH"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

export MAX_SUBMITS=12
export MAX_RUNTIME=1800

export NOTIFY_START=false
export NOTIFY_DAILY=false
export NOTIFY_FINISH=false

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME"
python -u ../../framework/agent.py "$@"
