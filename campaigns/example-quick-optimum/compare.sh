#!/bin/bash
# Compare models on this campaign: REPEATS runs per model (default 1), all at once, each in
# a workspace of its own under workspace/example-quick-optimum-compare[-<tag>]/<model>-<n>,
# which the watch shows as one group.
#
#   ./compare.sh                                   (opus, sonnet and haiku)
#   MODELS="sonnet muse mistral" REPEATS=2 TAG=1 ./compare.sh
set -euo pipefail
cd "$(dirname "$0")"
. ../../framework/settings.sh

# Name -> model. Claude models go the way run.sh's do; the others through the ALCF
# inference service (alcf.settings.json, docs/llm.md).
declare -A CLAUDE=([opus]=opus [sonnet]=sonnet [haiku]=haiku)
declare -A ALCF=([muse]=muse-spark-bf16 [mistral]=mistral-large-4-nvfp4 [nemotron]=nemotron-3-ultra)

export CAMPAIGN="$(basename "$PWD")" USER_NAME="${USER_NAME:-$USER}"
# The same box and limits for every model, so the comparison is of the models alone.
export SANDBOX="${SANDBOX:-bwrap}" MAX_SUBMITS="${MAX_SUBMITS:-24}" MAX_RUNTIME="${MAX_RUNTIME:-900}"
export NOTIFY_START=false NOTIFY_DAILY=false NOTIFY_FINISH=false
GROUP="$(cd ../.. && pwd)/workspace/$CAMPAIGN-compare${TAG:+-$TAG}"

for m in ${MODELS:-opus sonnet haiku}; do
  for n in $(seq "${REPEATS:-1}"); do
    ws="$GROUP/$m-$n"
    if [ -n "${CLAUDE[$m]:-}" ]; then
        run=(AGENT_MODEL="${CLAUDE[$m]}")
    elif [ -n "${ALCF[$m]:-}" ]; then
        mkdir -p "$ws/claude" && cp alcf.settings.json "$ws/claude/settings.json"
        run=(AGENT_MODEL="${ALCF[$m]}" CLAUDE_CONFIG_DIR="$ws/claude"
             ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)")
    else
        echo "$m: not one of ${!CLAUDE[*]} ${!ALCF[*]}" >&2
        continue 2
    fi
    mkdir -p "$ws"
    env WORKSPACE_DIR="$ws" "${run[@]}" python -u ../../framework/agent.py > "$ws/launch.log" 2>&1 &
    echo "$m-$n: $ws"
  done
done
wait
