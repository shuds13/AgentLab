#!/bin/bash
# Launch the agent for this campaign, with its jobs running on a compute system through
# Globus Compute rather than on this machine. Run from this directory:
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials.
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# The system the jobs run on; its endpoint is in users/$USER_NAME/$SYSTEM.json.
export SYSTEM=gce

# The job taken there.
export TASK_MODULE=remote_task

export MAX_SUBMITS=24
export MAX_RUNTIME=900

export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME SYSTEM=$SYSTEM"
python -u ../../framework/agent.py "$@"
