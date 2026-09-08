#!/bin/bash
# Launch the agent for this campaign. Run from this directory, inside tmux:
#   tmux new -s agentlab && ./run.sh
#
# Stop:  ../../bin/kill_agent.sh --drain <run_id>
# List:  ../../bin/list_agents.sh --all
set -euo pipefail
cd "$(dirname "$0")"

# Environment providing the claude CLI and the Python packages in requirements.txt.
export PATH="$HOME/.local/bin:$PATH"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# Jobs take about a second, so a run that proves the machinery works is minutes.
export MAX_SUBMITS=16
export MAX_RUNTIME=900

# No critic on a first run: it finishes in a few minutes and shows the machinery.
# Run it again with one when you want to see a cycle reviewed -- it takes longer,
# because the reviewing model reads the rows and thinks before it answers:
#
#     CRITIC_MODEL=auto CRITIC_BASE_URL=<your gateway> ./run.sh
#
# docs/llm.md covers what serves the second model.
export CRITIC_MODEL="${CRITIC_MODEL:-}"

# Critic level. Only applies when there is a critic
export CRITIC_LEVEL="${CRITIC_LEVEL:-light}"

# Turns here are seconds long, so context figures older than half a minute are not
# worth showing: the status pane says "no data" instead.
export CONTEXT_TIMEOUT=30

export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME"
python -u ../../framework/agent.py "$@"
