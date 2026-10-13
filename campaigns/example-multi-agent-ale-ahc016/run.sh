#!/bin/bash
# A coordinated run: a coordinator launching explorer and refiner workers, which share
# one evaluation budget.
#
#   MODE=directed TRIAL=1 ./run.sh
#
# MODEL is the coordinator's model, EXPLORER_MODEL and REFINER_MODEL the workers'.
#
# MODE: directed (the coordinator assigns each explorer an approach) or undirected
# (explorers choose). Each mode, model and trial has a workspace of its own.
set -euo pipefail
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"
MODE="${MODE:-directed}"
TRIAL="${TRIAL:-1}"
case "$MODE" in
    directed)   export METHOD_FILE=director.md ;;
    undirected) export METHOD_FILE=shepherd.md ;;
    *) echo "MODE must be directed or undirected, got $MODE" >&2; exit 2 ;;
esac
# The coordinator's model names the workspace.
export AGENT_MODEL="${MODEL:-sonnet}"
export EXPLORER_MODEL="${EXPLORER_MODEL:-opus}"
export REFINER_MODEL="${REFINER_MODEL:-sonnet}"
export WORKSPACE_DIR="$(cd ../.. && pwd)/workspace/$CAMPAIGN-$MODE-$AGENT_MODEL-$TRIAL"
export EVAL_DIR="$(cd ../.. && pwd)/workspace/.$CAMPAIGN-eval"
# Evaluations run one at a time on cores of their own, one test case per core.
export EVAL_CPUS="${EVAL_CPUS:-8-15}"
# The coordinator works in its workspace: its records, and its workers' branches.
export AGENT_CWD="$WORKSPACE_DIR"

# The coordinator reads and writes its records; it has no shell.
export AGENT_TOOLS="Read Write Edit Glob Grep"
# File tools confined to the agent's working directory, so no agent reads the evaluator,
# another condition's workspace or another worker's branch.
export FENCE_FILES=true

# The cores all workers share, as a taskset list. This node is shared.
export WORKER_CPUS="${WORKER_CPUS:-0-7}"

# The evaluations all workers share, and the most workers the coordinator may launch.
export WORKER_JOB_BUDGET="${EVALUATIONS:-80}"
export MAX_SUBMITS="${WORKERS:-12}"
export MAX_RUNTIME="${MAX_RUNTIME:-7200}"

export NOTIFY_START=false
export NOTIFY_DAILY=false
export NOTIFY_FINISH=false

./setup.sh

echo "[run] CAMPAIGN=$CAMPAIGN MODE=$MODE TRIAL=$TRIAL WORKSPACE=$WORKSPACE_DIR"
python -u ../../framework/agent.py "$@"
