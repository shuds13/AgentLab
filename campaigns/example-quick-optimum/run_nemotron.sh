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

# Claude Code reads its endpoint from its own settings in preference to the
# environment, so the model is chosen by giving it a config directory rather than by
# setting ANTHROPIC_BASE_URL here. That directory is its whole config home and it
# fills it with transcripts, logs and caches, so it is staged in the workspace where
# run output already lives; the .settings.json beside this script is the part worth
# keeping. docs/llm.md covers the one-time authentication it needs.
export CLAUDE_CONFIG_DIR="$WORKSPACE_DIR/claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
cp run_nemotron.settings.json "$CLAUDE_CONFIG_DIR/settings.json"

# The service takes a Globus access token as a bearer credential, which is what
# ANTHROPIC_AUTH_TOKEN sends; a key given any other way arrives as x-api-key and is
# refused. Fetched per run rather than stored, since a token lasts 48 hours -- and so
# a run longer than that outlives its credential.
export ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)"

# Jobs take about a second, so a run that proves the machinery works is minutes.
# Three sweeps of eight: bracket, narrow, then repeat readings where they are close.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Tokens and, where the price is the model's own, dollars -- in the viewer and Slack.
export SHOW_COST=true

# Notifications if set up
export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME MODEL=nemotron-3-ultra"
python -u ../../framework/agent.py "$@"
