#!/bin/bash
# OPTIONAL. Answers board questions so the agents are not interrupted.
# Run from this dir: ./run_secretary.sh
set -euo pipefail
cd "$(dirname "$0")"
# source "$HOME/miniconda3/etc/profile.d/conda.sh" && conda activate cas
umask 002
export PATH="$HOME/.local/bin:$PATH"
export WORKSPACE_ROOT="$(cd ../workspace && pwd)"   # all campaigns
# export SLACK_WEBHOOK_FILE="$HOME/.slack_webhook"
export SECRETARY_POLL=10
echo "[run] secretary -> campaign boards under $WORKSPACE_ROOT"
python -u secretary.py
