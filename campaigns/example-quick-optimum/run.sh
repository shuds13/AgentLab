#!/bin/bash
# Launch the agent for this campaign. Run from this directory:
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# Jobs take about a second, so a run that proves the machinery works is minutes.
# Three sweeps of eight: bracket, narrow, then repeat readings where they are close.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Notifications if set up
export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME"
python -u ../../framework/agent.py "$@"
