#!/usr/bin/env bash
# Start an agent on a campaign.
#
#   ./run.sh <campaign> [user]
#   ./run.sh example-vllm-inference-opt
#
# Merges systems/<system>.json, users/<user>/<system>.json and the campaign's
# campaign.json into the single config the framework reads, then launches the agent.
#
# Stop:  framework/kill_agent.sh --drain <run_id>
# List:  framework/list_agents.sh --all
set -euo pipefail

LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMPAIGN="${1:?usage: run.sh <campaign> [user]}"
USER_NAME="${2:-$USER}"

CAMPAIGN_DIR="$LAB/campaigns/$CAMPAIGN"
[ -d "$CAMPAIGN_DIR" ] || { echo "no such campaign: $CAMPAIGN_DIR" >&2; exit 1; }

SYSTEM="$(jq -r '.system' "$CAMPAIGN_DIR/campaign.json")"
SYS_FILE="$LAB/systems/$SYSTEM.json"
USER_FILE="$LAB/users/$USER_NAME/$SYSTEM.json"
for f in "$SYS_FILE" "$USER_FILE"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

WORKSPACE_DIR="$LAB/workspace/$CAMPAIGN"
mkdir -p "$WORKSPACE_DIR"
CONFIG="$WORKSPACE_DIR/config.json"

jq -n --slurpfile sys "$SYS_FILE" --slurpfile usr "$USER_FILE" \
      --slurpfile cam "$CAMPAIGN_DIR/campaign.json" --arg system "$SYSTEM" '
  ($sys[0]) as $s | ($usr[0]) as $u | ($cam[0]) as $c |
  {systems: {($system): {
     endpoint:       $u.endpoint,
     ppn:            $s.ppn,
     max_concurrent: $s.max_concurrent,
     target:         (($s.target // {}) * ($c.target // {}) + {work_dir: $u.work_dir}),
     buckets: {default: {
       num_nodes:   ($s.bucket_defaults.num_nodes // 1),
       user_config: (($s.bucket_defaults // {}) + {account: $u.account})
     }}
  }}}' > "$CONFIG"

export SYSTEM CAMPAIGN_DIR WORKSPACE_DIR
export TASK_DIR="$CAMPAIGN_DIR"
export CONFIG_FILE="$CONFIG"

cd "$LAB/framework"
exec "${AGENTLAB_PYTHON:-python3}" agent.py "${@:3}"
