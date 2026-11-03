#!/bin/bash
# Launch the agent for this campaign on an open model served by the ALCF inference
# service (alcf.settings.json). Same task as run.sh; only the model differs.
# Setup: docs/llm.md
#
#   MODEL=<muse|mistral|nemotron> ./run_open_model.sh      (default muse)
set -euo pipefail

# To run from anywhere
cd "$(dirname "$0")"

# What this installation has available -- model gateways, credentials.
. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"
MODEL="${MODEL:-muse}"
case "$MODEL" in
    muse)     export AGENT_MODEL=muse-spark-bf16 ;;
    mistral)  export AGENT_MODEL=mistral-large-4-nvfp4 ;;
    nemotron) export AGENT_MODEL=nemotron-3-ultra ;;
    *) echo "MODEL must be muse, mistral or nemotron" >&2; exit 1 ;;
esac

# A workspace of its own, named for the model (workspace/<campaign>-muse, ...), and
# Claude Code settings naming the endpoint.
export WORKSPACE_DIR="${WORKSPACE_DIR:-$(cd ../.. && pwd)/workspace/${CAMPAIGN}-${MODEL}}"
export CLAUDE_CONFIG_DIR="$WORKSPACE_DIR/claude"
mkdir -p "$CLAUDE_CONFIG_DIR"
cp alcf.settings.json "$CLAUDE_CONFIG_DIR/settings.json"

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
