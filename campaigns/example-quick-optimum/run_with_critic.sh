#!/bin/bash
# Launch the agent for this campaign with a critic reviewing each cycle.
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# Three sweeps of eight: bracket, narrow, then repeat readings where they are close.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Which model reviews each cycle, and how much of it. Settings: docs/llm.md
export CRITIC_MODEL=auto
export CRITIC_LEVEL=light

# Notifications if set up
export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME CRITIC=$CRITIC_MODEL"
python -u ../../framework/agent.py "$@"
