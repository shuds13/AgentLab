#!/bin/bash
# Compare models on this campaign: REPEATS swarms per model (default 1), all at once. Each
# swarm runs one model throughout -- coordinator, explorers and refiners -- in a workspace
# of its own under workspace/<campaign>-compare[-<tag>]/<model>-<n>, which the watch
# shows as one group. Every other setting is run.sh's (MODE, EVALUATIONS, WORKERS, ...).
#
#   ./compare.sh                                   (opus, sonnet and haiku)
#   MODELS="sonnet muse mistral" REPEATS=2 TAG=1 ./compare.sh
set -euo pipefail
cd "$(dirname "$0")"

# Name -> model. Claude models go the way run.sh's do; the others through the ALCF
# inference service (alcf.settings.json, docs/llm.md).
declare -A CLAUDE=([opus]=opus [sonnet]=sonnet [haiku]=haiku)
declare -A ALCF=([muse]=muse-spark-bf16 [mistral]=mistral-large-4-nvfp4 [nemotron]=nemotron-3-ultra)

MODELS="${MODELS:-opus sonnet haiku}"
REPEATS="${REPEATS:-1}"
GROUP="$(cd ../.. && pwd)/workspace/$(basename "$PWD")-compare${TAG:+-$TAG}"
for m in $MODELS; do
    [ -n "${CLAUDE[$m]:-}${ALCF[$m]:-}" ] || { echo "$m: not one of ${!CLAUDE[*]} ${!ALCF[*]}" >&2; exit 1; }
done

# Each swarm gets cores of its own, half for its agents and half for its evaluations, so
# one swarm's load does not slow another's evaluations.
set -- $MODELS
RUNS=$(( $# * REPEATS ))
PER=$(( $(nproc) / RUNS )); PER=$(( PER > 16 ? 16 : PER )); PER=$(( PER / 2 * 2 ))
[ "$PER" -ge 4 ] || { echo "$RUNS swarms is too many for $(nproc) cores" >&2; exit 1; }

# The evaluator is built once, before the swarms start, rather than by all of them at once.
WORKSPACE_DIR="$GROUP/$1-1" PREFLIGHT=true ./run.sh > /dev/null

i=0
for m in $MODELS; do
  for n in $(seq "$REPEATS"); do
    ws="$GROUP/$m-$n"; mkdir -p "$ws"
    lo=$(( i * PER )); mid=$(( lo + PER / 2 )); i=$(( i + 1 ))
    run=(WORKSPACE_DIR="$ws" WORKER_CPUS="$lo-$(( mid - 1 ))" EVAL_CPUS="$mid-$(( lo + PER - 1 ))")
    if [ -n "${CLAUDE[$m]:-}" ]; then
        id="${CLAUDE[$m]}"
    else
        id="${ALCF[$m]}"
        mkdir -p "$ws/claude" && cp alcf.settings.json "$ws/claude/settings.json"
        run+=(CLAUDE_CONFIG_DIR="$ws/claude"
              ANTHROPIC_AUTH_TOKEN="$(python3 ~/inference_auth_token.py get_access_token)")
    fi
    run+=(MODEL="$id" EXPLORER_MODEL="$id" REFINER_MODEL="$id")
    env "${run[@]}" ./run.sh > "$ws/launch.log" 2>&1 &
    echo "$m-$n: $ws (cores $lo-$(( lo + PER - 1 )))"
  done
done
wait
