#!/bin/bash
# Launch the agent for this campaign, with its jobs running on a compute system through
# Globus Compute rather than on this machine. Run from this directory:
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# The system the jobs run on, overriding the campaign's own. Its endpoint and work_dir
# are in users/$USER_NAME/$SYSTEM.json.
export SYSTEM="${SYSTEM:-gce}"

# The goal for this run, and which tools it reaches for.
export USER_PROMPT_FILE="user_prompt_remote.md"

# Three sweeps of eight: bracket, narrow, then repeat readings where they are close.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Notifications if set up
export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME SYSTEM=$SYSTEM"
python -u ../../framework/agent.py "$@"
