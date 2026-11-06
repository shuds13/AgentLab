#!/usr/bin/env bash
# What a workspace's runs consumed: one line per agent and model (tokens in and out, and
# dollars where known), then totals per model.
#
# Usage:
#   ./cost.sh <workspace>            # a campaign's workspace, by name or path
#   ./cost.sh <group>                # every workspace in a group
#   ./cost.sh <workspace> --json
#
# Recorded whether or not the run shows its cost (SHOW_COST).
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")/.." && pwd)/framework/cost.py" "$@"
