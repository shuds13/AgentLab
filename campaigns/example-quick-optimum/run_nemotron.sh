#!/bin/bash
# Launch the agent for this campaign on Nemotron Ultra, served by the ALCF inference
# service. Same task as run.sh; only the model differs. Setup: docs/llm.md
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials.
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# A workspace of its own, and Claude Code settings naming the endpoint.
export WORKSPACE_DIR="$(cd ../.. && pwd)/workspace/${CAMPAIGN}-nemotron"
export CLAUDE_CONFIG_DIR="$WORKSPACE_DIR/claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
cp alcf.settings.json "$CLAUDE_CONFIG_DIR/settings.json"
export AGENT_MODEL=nemotron-3-ultra

# Run the agent in a sandbox (Linux, bubblewrap); SANDBOX=none to run without.
export SANDBOX="${SANDBOX:-bwrap}"

# ALCF access token, fetched per run.
export ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)"

export MAX_SUBMITS=24
export MAX_RUNTIME=900

export NOTIFY_START=true
export NOTIFY_DAILY=false
export NOTIFY_FINISH=true

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME MODEL=$AGENT_MODEL"
python -u ../../framework/agent.py "$@"
