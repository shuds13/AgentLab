#!/bin/bash
# Launch the agent for this campaign on Nemotron Ultra, served by the ALCF inference
# service. Same task as run.sh; only the model differs.
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials. Anything below
# overrides it, since a campaign knows its own needs. Settings: docs/settings.md
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# A workspace of its own, so these results sit beside run.sh's rather than among them.
export WORKSPACE_DIR="$(cd ../.. && pwd)/workspace/${CAMPAIGN}-nemotron"

# The endpoint and model, from settings Claude Code reads in preference to the
# environment. Built in the workspace, since it fills with transcripts and caches.
export CLAUDE_CONFIG_DIR="$WORKSPACE_DIR/claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
cp run_nemotron.settings.json "$CLAUDE_CONFIG_DIR/settings.json"

# A bearer token, which is what ANTHROPIC_AUTH_TOKEN sends; anything else arrives as
# x-api-key and is refused. Lasts 48 hours, so fetched per run. docs/llm.md
export ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)"

# Jobs take about a second, so a run that proves the machinery works is minutes.
# Three sweeps of eight: bracket, narrow, then repeat readings where they are close.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Tokens in the viewer and Slack; no dollars, the price would not be this model's.
export SHOW_COST=true

# Notifications if set up
export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME MODEL=nemotron-3-ultra"
python -u ../../framework/agent.py "$@"
