#!/usr/bin/env bash
# One agent looks for the lowest-drag shape, with its jobs on Polaris GPUs.
#
#   ./run.sh                  your Polaris endpoint (users/<you>/polaris.json)
#   PROFILE=mep ./run.sh      a profile in that file, e.g. ALCF's facility endpoint
set -euo pipefail
cd "$(dirname "$0")"
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"
export MAX_SUBMITS="${MAX_SUBMITS:-40}"
export MAX_RUNTIME="${MAX_RUNTIME:-7200}"
export NOTIFY_START=false
export NOTIFY_DAILY=false
export NOTIFY_FINISH=false
echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME"
python -u ../../framework/agent.py "$@"
